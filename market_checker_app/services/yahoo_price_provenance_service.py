"""Recorded Yahoo frame/cache versions, never dated publisher revisions.

Yahoo session labels, HTTP dates and a historical download do not establish
when a publisher corrected an earlier price. This adapter records observed
numeric content and physical clocks; publisher revision knowledge stays UNKNOWN.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import date
from collections.abc import Sequence

import pandas as pd

from market_checker_app.prediction_contract import canonical_hash
from market_checker_app.services.price_methodology import PRICE_BASIS, PRICE_METHOD_VERSION
from market_checker_app.utils.symbols import normalize_yahoo_symbol


SCHEMA_VERSION = "yahoo_price_observation_v1"
MAX_FRAME_ROWS = 20_000
MAX_CAPTURE_BYTES = 16_000_000
PROVIDER_ATTR = "yahoo_provider_observation"
PROVENANCE_ATTR = "point_in_time_price_provenance"
_NAME = re.compile(r"yahoo_price_([a-f0-9]{64})\.json")
_ACCESS = re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|credential|bearer\s|cookie|crumb|[?&](?:token|key)=)")
_COLUMNS = {"Open", "High", "Low", "Close", "Adj Close", "Volume", "Stock Splits", "Dividends", "Capital Gains"}


def utc_clock(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Price observation clock must include timezone")
    return value.astimezone(timezone.utc)


def physical_clock() -> datetime:
    return datetime.now(timezone.utc)


def digest(encoded: str | bytes) -> str:
    return hashlib.sha256(encoded.encode("utf-8") if isinstance(encoded, str) else encoded).hexdigest()


def _access_value(value: object, depth: int = 0) -> bool:
    if depth > 12:
        return True
    if isinstance(value, str):
        return bool(_ACCESS.search(value))
    if isinstance(value, Mapping):
        return any(_access_value(key, depth + 1) or _access_value(item, depth + 1) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return len(value) > 100_000 or any(_access_value(item, depth + 1) for item in value)
    return False


def frame_json(frame: pd.DataFrame, *, precision: int = 15) -> str:
    """Whole accepted public numeric frame; refuse access-bearing input in full."""
    if not isinstance(frame, pd.DataFrame) or frame.empty or len(frame) > MAX_FRAME_ROWS:
        raise ValueError("Price capture requires a bounded nonempty frame")
    if _access_value(frame.attrs) or _access_value(list(frame.columns)) or _access_value(list(frame.index)):
        raise ValueError("WITHHELD_PRICE_CAPTURE_ACCESS_VALUE")
    # Unknown/string columns are not stripped to get around a capture gate.
    if any(column not in _COLUMNS for column in frame.columns) or frame.columns.duplicated().any():
        raise ValueError("WITHHELD_PRICE_CAPTURE_UNSUPPORTED_COLUMNS")
    for column in frame.columns:
        if not pd.api.types.is_numeric_dtype(frame[column]):
            raise ValueError("WITHHELD_PRICE_CAPTURE_NONNUMERIC_BODY")
    encoded = frame.to_json(orient="split", date_format="iso", double_precision=precision)
    if len(encoded.encode("utf-8")) > MAX_CAPTURE_BYTES // 2:
        raise ValueError("Price frame byte limit exceeded")
    return encoded


def _seal(record: dict) -> dict:
    record["capture_id"] = canonical_hash(record)
    return record


def validate_observation(record: object) -> dict:
    if not isinstance(record, Mapping) or record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported price observation")
    if _access_value(record):
        raise ValueError("WITHHELD_PRICE_CAPTURE_ACCESS_VALUE")
    if record.get("capture_id") != canonical_hash({key: value for key, value in record.items() if key != "capture_id"}):
        raise ValueError("Price observation content hash mismatch")
    encoded = record.get("frame_json")
    if not isinstance(encoded, str) or record.get("frame_sha256") != digest(encoded):
        raise ValueError("Price frame content hash mismatch")
    if record.get("publisher_revision_timing_status") != "UNKNOWN" or record.get("publisher_revision_at") is not None:
        raise ValueError("Yahoo observation cannot verify publisher revision timing")
    times = {field: utc_clock(datetime.fromisoformat(record[field])) for field in ("observed_at", "captured_at")}
    if times["observed_at"] > times["captured_at"]:
        raise ValueError("Price observation cannot follow physical capture")
    return dict(record)


def capture_provider_frame(ticker: str, frame: pd.DataFrame, *, started_at: datetime,
        observed_at: datetime, interval: str = "1d", upstream_response_sha256: str | None = None,
        evidence_origin: str = "YFINANCE_RETURNED_FRAME") -> dict:
    started, observed = map(utc_clock, (started_at, observed_at))
    if started > observed:
        raise ValueError("Provider call clocks are reversed")
    if evidence_origin not in {"YFINANCE_RETURNED_FRAME", "CAPTURED_YAHOO_CHART_REPLAY"}:
        raise ValueError("Unsupported price observation origin")
    if upstream_response_sha256 is not None and (not isinstance(upstream_response_sha256, str) or re.fullmatch(r"[a-f0-9]{64}", upstream_response_sha256) is None):
        raise ValueError("Invalid upstream price response hash")
    encoded = frame_json(frame)
    return _seal({"schema_version": SCHEMA_VERSION, "kind": "PROVIDER_RETURNED_FRAME",
        "ticker": ticker.upper(), "provider": "yfinance", "provider_symbol": normalize_yahoo_symbol(ticker),
        "interval": interval, "actions_requested": True, "auto_adjust_requested": False,
        "started_at": started.isoformat(), "observed_at": observed.isoformat(), "captured_at": observed.isoformat(),
        "frame_json": encoded, "frame_sha256": digest(encoded), "row_count": len(frame),
        "synthesized_columns": list(frame.attrs.get("yahoo_synthesized_columns", ())),
        "evidence_origin": evidence_origin, "upstream_response_sha256": upstream_response_sha256,
        "publisher_revision_timing_status": "UNKNOWN", "publisher_revision_at": None,
        "publisher_authenticity_independently_verified": False,
        "methodology": "PROVIDER_VALUES_AS_RETURNED"})


def provider_observation(frame: pd.DataFrame, ticker: str) -> dict | None:
    record = frame.attrs.get(PROVIDER_ATTR)
    if record is None:
        return None
    record = validate_observation(record)
    if (record.get("kind") != "PROVIDER_RETURNED_FRAME" or record.get("ticker") != ticker.upper()
            or record.get("provider_symbol") != normalize_yahoo_symbol(ticker)
            or record["frame_sha256"] != digest(frame_json(frame))):
        raise ValueError("Provider observation does not match this exact instrument/frame")
    return record


def _change(previous_json: str | None, current_json: str) -> dict:
    if previous_json is None:
        return {"status": "FIRST_CAPTURE", "changed_overlap_rows": 0, "changed_row_sample": [], "sample_complete": True}
    before, after = json.loads(previous_json), json.loads(current_json)
    old = {index: dict(zip(before["columns"], row)) for index, row in zip(before["index"], before["data"])}
    new = {index: dict(zip(after["columns"], row)) for index, row in zip(after["index"], after["data"])}
    changes = [index for index in sorted(old.keys() & new.keys()) if old[index] != new[index]]
    status = "CONTENT_CHANGED" if changes else "EXTENDED_ONLY" if old != new else "UNCHANGED_RETRIEVAL"
    return {"status": status, "changed_overlap_rows": len(changes), "changed_row_sample": changes[:100],
            "sample_complete": len(changes) <= 100,
            "publisher_correction_inferred": False}


def capture_cache_frame(ticker: str, frame: pd.DataFrame, *, encoded: str, provider: str,
        interval: str, adjustment: str, methodology_version: str, captured_at: datetime,
        logical_fetched_at: datetime, prior_json: str | None, source: Mapping | None,
        prior_capture_id: str | None = None) -> dict:
    # Inspect the whole frame before any serialized version is published.
    frame_json(frame, precision=10)
    actual = utc_clock(captured_at)
    source_at = datetime.fromisoformat(source["observed_at"]) if source else actual
    if source_at > actual or encoded != frame.to_json(orient="split", date_format="iso"):
        raise ValueError("Cache payload or physical clock does not match capture")
    return _seal({"schema_version": SCHEMA_VERSION, "kind": "CACHE_MERGED_FRAME",
        "ticker": ticker.upper(), "provider": provider, "provider_symbol": normalize_yahoo_symbol(ticker),
        "interval": interval, "adjustment": adjustment, "methodology_version": methodology_version,
        "observed_at": source_at.isoformat(), "captured_at": actual.isoformat(),
        "logical_fetched_at": utc_clock(logical_fetched_at).isoformat(),
        "logical_fetched_at_is_physical_observation": False,
        "frame_json": encoded, "frame_sha256": digest(encoded), "row_count": len(frame),
        "source_capture_id": source["capture_id"] if source else None,
        "source_frame_sha256": source["frame_sha256"] if source else None,
        "source_observation_status": "RECORDED_PROVIDER_FRAME" if source else "CACHE_INPUT_ONLY",
        "source_evidence_origin": source.get("evidence_origin") if source else None,
        "prior_capture_id": prior_capture_id, "prior_frame_sha256": digest(prior_json) if prior_json else None,
        "observed_content_revision": _change(prior_json, encoded),
        "publisher_revision_timing_status": "UNKNOWN", "publisher_revision_at": None,
        "publisher_authenticity_independently_verified": False})


def persist_observation(record: Mapping, directory: Path) -> Path:
    record = validate_observation(record)
    encoded = (json.dumps(record, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if len(encoded) > MAX_CAPTURE_BYTES:
        raise ValueError("Price capture byte limit exceeded")
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError("Price observation directory cannot be a symlink")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"yahoo_price_{record['capture_id']}.json"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".yahoo_price_", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if load_observation(path) != record:
                raise ValueError("Refusing price observation rewrite") from None
        return path
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_observation(path: Path) -> dict:
    path = Path(path)
    match = _NAME.fullmatch(path.name)
    if match is None or path.is_symlink():
        raise ValueError("Invalid price observation path")
    with path.open("rb") as handle:
        encoded = handle.read(MAX_CAPTURE_BYTES + 1)
    if len(encoded) > MAX_CAPTURE_BYTES:
        raise ValueError("Price capture byte limit exceeded")
    record = validate_observation(json.loads(encoded))
    if record["capture_id"] != match.group(1):
        raise ValueError("Price observation filename hash mismatch")
    return record


def label_provenance(record: Mapping, *, cache_read_at: datetime, required_sessions: Sequence[date] | None = None) -> dict:
    record = validate_observation(record)
    actual = utc_clock(cache_read_at)
    captured = datetime.fromisoformat(record["captured_at"])
    if actual < captured:
        raise ValueError("Cache cannot be physically read before capture")
    canonical = (record.get("provider") == "yfinance" and record.get("interval") == "1d"
        and record.get("adjustment") == "split_only" and record.get("methodology_version") == PRICE_METHOD_VERSION)
    frame = json.loads(record["frame_json"])
    action_columns = [column for column in frame["columns"] if column in {"Stock Splits", "Dividends", "Capital Gains"}]
    slots = [frame["columns"].index(column) for column in action_columns]
    action_frame = {"index": frame["index"], "columns": action_columns,
        "data": [[row[slot] for slot in slots] for row in frame["data"]]}
    required = set(required_sessions or ())
    action_inputs = []
    for index, row in zip(frame["index"], action_frame["data"]):
        if not isinstance(index, str):
            continue
        try:
            session = pd.Timestamp(index).date()
        except (ValueError, TypeError):
            continue
        if session in required:
            action_inputs.append({"session_date": session.isoformat(), "values": dict(zip(action_columns, row))})
    return {"provider": record["provider"], "price_basis": PRICE_BASIS if canonical else "UNKNOWN",
        "price_method_version": PRICE_METHOD_VERSION if canonical else "UNKNOWN", "dividends_included": False if canonical else None,
        "provider_observed_at": record["observed_at"], "cache_read_at": actual.isoformat(),
        "provider_payload_sha256": record.get("source_frame_sha256"), "cache_frame_sha256": record["frame_sha256"],
        "price_observation_id": record["capture_id"], "source_capture_id": record.get("source_capture_id"),
        "source_observation_status": record.get("source_observation_status"),
        "observed_content_revision_status": record.get("observed_content_revision", {}).get("status"),
        "observed_content_revision_at": record["captured_at"],
        "publisher_revision_timing_status": "UNKNOWN", "revision_status": "UNKNOWN", "revision_known_at": None,
        "corporate_action_status": "OBSERVED_PROVIDER_INPUT_WINDOW", "corporate_actions": [],
        "corporate_actions_checked_from": None, "corporate_actions_checked_through": None, "corporate_actions_known_at": None,
        "corporate_actions_input_sha256": digest(json.dumps(action_frame, sort_keys=True)),
        "corporate_action_session_inputs": action_inputs,
        "corporate_action_input_sessions_requested": len(required),
        "corporate_action_input_sessions_recorded": len(action_inputs),
        "publisher_revision_from_session_or_http_date_inferred": False}
