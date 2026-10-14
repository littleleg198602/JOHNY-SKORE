"""Append-only forecasts and exact later labels, with physical knowledge clocks.

This recorder reads already persisted forecasts; it never makes a forecast or
backfills a historical prediction. A forecast must be recorded before its first
forward NYSE session opens. Later labels retain the exact originally pinned
sessions, source content and attachment time. Hashes prove consistency, not
publisher authenticity, historical coverage or out-of-sample acceptance.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
from itertools import islice
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from market_checker_app.prediction_contract import (
    PRIMARY_PREDICTION_TARGET, SNAPSHOT_SCHEMA_VERSION, canonical_hash,
    make_snapshot_id,
)
from market_checker_app.services.specialist_incremental_archive_service import (
    MAX_RUN_ARCHIVE_BYTES, load_specialist_source_run_archive,
)
from market_checker_app.services.specialist_label_availability_service import (
    audit_label_observation, load_label_observation,
)
from market_checker_app.services.us_equity_calendar import target_us_equity_window
from market_checker_app.services.us_equity_calendar_service import _nyse_calendar
from market_checker_app.services.yahoo_price_provenance_service import (
    MAX_CAPTURE_BYTES, load_observation,
)
from market_checker_app.utils.ticker_universe import load_canonical_ticker_records


FORECAST_SCHEMA_VERSION = "prospective_specialist_forecast_v1"
LABEL_SCHEMA_VERSION = "prospective_specialist_label_attachment_v1"
REPORT_VERSION = "prospective_specialist_history_v1"
MAX_RECORD_BYTES = 2_000_000
MAX_HISTORY_RECORDS = 10_000
MAX_REPORT_BYTES = 64_000_000
MAX_SOURCE_REFERENCES = 24
MAX_SOURCE_BYTES = 64_000_000
_HASH = re.compile(r"[a-f0-9]{64}")
_FORECAST_FILE = re.compile(r"forecast_([a-f0-9]{64})\.json")
_LABEL_FILE = re.compile(r"label_([a-f0-9]{64})\.json")
_ACCESS = re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|credential|bearer\s|cookie|crumb|[?&](?:token|key)=)")
_COLUMNS = (
    "snapshot_schema_version", "snapshot_id", "run_id", "ticker", "as_of",
    "observed_at", "target_name", "target_version", "horizon_trading_days",
    "benchmark_ticker", "benchmark_selection", "label_status", "target_value",
    "target_observed_at", "baseline_model_id", "baseline_model_version",
    "feature_payload_json", "baseline_output_json", "provenance_json",
    "snapshot_hash", "created_at",
)
_MUTABLE_LABEL_FIELDS = {"label_status", "target_value", "target_observed_at", "snapshot_hash"}
_TRAINING_CLOCKS = {
    "trained_at", "training_completed_at", "latest_training_label_at",
    "latest_training_label_available_at", "training_cutoff", "training_as_of",
}
_KNOWLEDGE_CLOCKS = {
    "as_of", "observed_at", "available_at", "availability_at", "captured_at",
    "known_at", "cache_read_at", "provider_observed_at", "revision_known_at",
}


def physical_clock() -> datetime:
    return datetime.now(timezone.utc)


def _time(value: object) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("INVALID_HISTORY_KNOWLEDGE_CLOCK") from None


def _hash(value: object) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise ValueError("INVALID_HISTORY_CONTENT_ID")
    return value


def _bounded(value: int, ceiling: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= ceiling:
        raise ValueError("INVALID_HISTORY_BOUND")


def _encoded(record: Mapping) -> bytes:
    try:
        encoded = (json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2,
                              allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ValueError("UNSUPPORTED_HISTORY_BODY") from None
    if len(encoded) > MAX_RECORD_BYTES:
        raise ValueError("HISTORY_RECORD_BYTE_LIMIT")
    # Refuse the whole input. Never strip/encode an access-bearing field and
    # then publish a supposedly safe substitute body.
    if _ACCESS.search(encoded.decode("utf-8")):
        raise ValueError("WITHHELD_HISTORY_ACCESS_VALUE")
    return encoded


def _no_symlink(path: Path) -> None:
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("HISTORY_SYMLINK_REFUSED")


def _read_json(path: Path, limit: int = MAX_RECORD_BYTES) -> tuple[dict, bytes]:
    _no_symlink(path)
    with path.open("rb") as handle:
        encoded = handle.read(limit + 1)
    if len(encoded) > limit:
        raise ValueError("HISTORY_RECORD_BYTE_LIMIT")
    try:
        text = encoded.decode("utf-8")
        if _ACCESS.search(text):
            raise ValueError("WITHHELD_HISTORY_ACCESS_VALUE")
        def unsupported_constant(_: str):
            raise ValueError("UNSUPPORTED_HISTORY_BODY")

        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("DUPLICATE_HISTORY_JSON_KEY")
                result[key] = value
            return result

        record = json.loads(text, parse_constant=unsupported_constant, object_pairs_hook=unique_object)
        if not isinstance(record, dict):
            raise ValueError("UNSUPPORTED_HISTORY_BODY")
        # Escaped JSON keys/values are inspected in their decoded form too.
        if _ACCESS.search(json.dumps(record, ensure_ascii=False)):
            raise ValueError("WITHHELD_HISTORY_ACCESS_VALUE")
        return record, encoded
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("UNSUPPORTED_HISTORY_BODY") from None


def _seal(record: dict, field: str) -> dict:
    record[field] = canonical_hash(record)
    _encoded(record)
    return record


def _immutable_inputs(snapshot: Mapping) -> dict:
    return {key: value for key, value in snapshot.items() if key not in _MUTABLE_LABEL_FIELDS}


def _check_snapshot(snapshot: Mapping) -> None:
    _encoded(snapshot)
    identifier = _hash(snapshot.get("snapshot_id"))
    run_id = snapshot.get("run_id")
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 1:
        raise ValueError("INVALID_STORED_FORECAST_IDENTITY")
    if (snapshot.get("snapshot_schema_version") != SNAPSHOT_SCHEMA_VERSION
            or identifier != make_snapshot_id(run_id, snapshot.get("ticker"), snapshot.get("target_version"))):
        raise ValueError("INVALID_STORED_FORECAST_IDENTITY")
    canonical_tickers = {item["ticker"] for item in load_canonical_ticker_records()}
    if snapshot.get("ticker") not in canonical_tickers:
        raise ValueError("FORECAST_OUTSIDE_CANONICAL_UNIVERSE")
    if (snapshot.get("target_name") != PRIMARY_PREDICTION_TARGET.name
            or snapshot.get("target_version") != PRIMARY_PREDICTION_TARGET.version
            or isinstance(snapshot.get("horizon_trading_days"), bool)
            or snapshot.get("horizon_trading_days") != PRIMARY_PREDICTION_TARGET.horizon_trading_days):
        raise ValueError("UNSUPPORTED_PROSPECTIVE_TARGET")
    if not isinstance(snapshot.get("benchmark_ticker"), str) or not re.fullmatch(r"[A-Z0-9.-]{1,20}", snapshot["benchmark_ticker"]):
        raise ValueError("INVALID_FORECAST_BENCHMARK_IDENTITY")
    clock = _time(snapshot.get("as_of"))
    if _time(snapshot.get("observed_at")) != clock:
        raise ValueError("FORECAST_ASOF_OBSERVATION_MISMATCH")
    _time(snapshot.get("created_at"))
    declared = _hash(snapshot.get("snapshot_hash"))
    body = {key: value for key, value in snapshot.items() if key != "snapshot_hash"}
    # SQLite adds created_at to the original body. The label resolver includes
    # it in later hashes. Both documented storage forms retain the same inputs.
    if declared not in {canonical_hash(body), canonical_hash({key: value for key, value in body.items() if key != "created_at"})}:
        raise ValueError("STORED_FORECAST_HASH_MISMATCH")
    provenance = snapshot.get("provenance")
    if (not isinstance(provenance, dict)
            or provenance.get("target_price_basis") != PRIMARY_PREDICTION_TARGET.price_basis
            or provenance.get("target_price_method_version") != PRIMARY_PREDICTION_TARGET.price_method_version
            or provenance.get("target_dividends_included") is not False):
        raise ValueError("FORECAST_TARGET_PRICE_METHOD_MISMATCH")
    if not isinstance(snapshot.get("feature_payload"), dict) or not isinstance(snapshot.get("baseline_output"), dict) or not snapshot["baseline_output"]:
        raise ValueError("MISSING_STORED_FORECAST_OUTPUT")


def read_stored_prediction_snapshot(db_path: Path, snapshot_id: str) -> dict:
    """Read one bounded existing row with mode=ro, without schema creation."""
    identifier = _hash(snapshot_id)
    path = Path(db_path)
    _no_symlink(path)
    if not path.is_file():
        raise ValueError("FORECAST_DATABASE_UNAVAILABLE")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0)) as connection:
        connection.row_factory = sqlite3.Row
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='prediction_snapshots'").fetchone():
            raise ValueError("FORECAST_TABLE_UNAVAILABLE")
        sizes = connection.execute("SELECT length(feature_payload_json), length(baseline_output_json), length(provenance_json) FROM prediction_snapshots WHERE snapshot_id = ?", (identifier,)).fetchone()
        if sizes is None:
            raise ValueError("STORED_FORECAST_NOT_FOUND")
        if any(size is None or size > MAX_RECORD_BYTES for size in sizes) or sum(sizes) > MAX_RECORD_BYTES:
            raise ValueError("HISTORY_RECORD_BYTE_LIMIT")
        row = connection.execute("SELECT " + ", ".join(_COLUMNS) + " FROM prediction_snapshots WHERE snapshot_id = ?", (identifier,)).fetchone()
    result = dict(row)
    # Gate the entire stored row before projecting/parsing its JSON fields.
    _encoded(result)
    for field in ("feature_payload", "baseline_output", "provenance"):
        try:
            result[field] = json.loads(result.pop(field + "_json"))
        except (ValueError, TypeError, RecursionError):
            raise ValueError("UNSUPPORTED_STORED_FORECAST_JSON") from None
    _check_snapshot(result)
    return result


def _check_input_clocks(value: object, prediction_at: datetime, depth: int = 0) -> None:
    if depth > 16:
        raise ValueError("FORECAST_INPUT_NESTING_LIMIT")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if item is not None and key in _TRAINING_CLOCKS:
                if _time(item) >= prediction_at:
                    raise ValueError("TRAINING_NOT_STRICTLY_BEFORE_PREDICTION")
            elif item is not None and (key in _KNOWLEDGE_CLOCKS or str(key).endswith("_known_at")):
                if _time(item) > prediction_at:
                    raise ValueError("FORECAST_INPUT_KNOWLEDGE_AFTER_PREDICTION")
            _check_input_clocks(item, prediction_at, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check_input_clocks(item, prediction_at, depth + 1)


def _check_feature_outcomes(value: object) -> None:
    if isinstance(value, Mapping):
        if any(key in value for key in ("target_value", "target_observed_at", "forward_target_value")):
            raise ValueError("FORECAST_FEATURES_CONTAIN_OUTCOME")
        for item in value.values():
            _check_feature_outcomes(item)
    elif isinstance(value, list):
        for item in value:
            _check_feature_outcomes(item)


@lru_cache(maxsize=512)
def _session_open(day) -> str:
    schedule = _nyse_calendar().schedule(start_date=day, end_date=day)
    if len(schedule) != 1:
        raise ValueError("EXACT_FORWARD_SESSION_UNAVAILABLE")
    return schedule.iloc[0].market_open.to_pydatetime().isoformat()


def _window(snapshot: Mapping) -> dict:
    prediction_at = _time(snapshot["as_of"])
    if not 1970 <= prediction_at.year <= 2099:
        raise ValueError("UNSUPPORTED_FORECAST_CALENDAR_DATE")
    base, future = target_us_equity_window(prediction_at, snapshot["horizon_trading_days"])
    return {"calendar": "NYSE", "horizon_trading_days": snapshot["horizon_trading_days"],
        "base_session_date": base.session_date.isoformat(), "base_close_at": base.close_at.isoformat(),
        "first_forward_session_open_at": _session_open(future[0].session_date),
        "required_session_dates": [session.session_date.isoformat() for session in (base, *future)],
        "required_session_close_times": [session.close_at.isoformat() for session in (base, *future)],
        "target_session_date": future[-1].session_date.isoformat(), "horizon_end": future[-1].close_at.isoformat()}


def _source_references(paths: Sequence[Path], *, prediction_at: datetime) -> list[dict]:
    paths = list(islice(iter(paths), MAX_SOURCE_REFERENCES + 1))
    if len(paths) > MAX_SOURCE_REFERENCES:
        raise ValueError("FORECAST_SOURCE_REFERENCE_LIMIT")
    result, seen, total = [], set(), 0
    for item in paths:
        path = Path(item)
        limit = MAX_CAPTURE_BYTES if path.name.startswith("yahoo_price_") else MAX_RUN_ARCHIVE_BYTES
        record, encoded = _read_json(path, limit)
        total += len(encoded)
        if total > MAX_SOURCE_BYTES:
            raise ValueError("FORECAST_SOURCE_TOTAL_BYTE_LIMIT")
        if path.name.startswith("specialist_source_run_"):
            checked = load_specialist_source_run_archive(path)
            source_id, content_hash, kind = checked["source_run_id"], checked["snapshot_hash"], "SPECIALIST_SOURCE_RUN"
            observed, available = checked["observed_at"], checked["available_at"]
            identity = {"source": checked["source"]}
        elif path.name.startswith("yahoo_price_"):
            checked = load_observation(path)
            source_id, content_hash, kind = checked["capture_id"], checked["capture_id"], "YAHOO_PRICE_OBSERVATION"
            observed, available = checked["observed_at"], checked["captured_at"]
            identity = {"ticker": checked["ticker"], "frame_sha256": checked["frame_sha256"],
                        "publisher_revision_timing_status": checked["publisher_revision_timing_status"]}
        else:
            raise ValueError("UNSUPPORTED_FORECAST_SOURCE_ARCHIVE")
        if record != checked:
            raise ValueError("FORECAST_SOURCE_CHANGED_DURING_READ")
        if not _time(observed) <= _time(available) <= prediction_at:
            raise ValueError("FORECAST_SOURCE_AVAILABLE_AFTER_PREDICTION")
        pair = (_hash(source_id), _hash(content_hash))
        if pair in seen:
            continue
        seen.add(pair)
        result.append({"kind": kind, "source_id": source_id, "content_hash": content_hash,
            "archive_filename": path.name,
            "file_sha256": hashlib.sha256(encoded).hexdigest(), "file_bytes": len(encoded),
            "observed_at": observed, "available_at": available,
            "evidence_origin": checked.get("evidence_origin", checked.get("source_evidence_origin")), **identity})
    return sorted(result, key=lambda row: (row["kind"], row["source_id"], row["content_hash"]))


def _load_forecast(path: Path) -> tuple[dict, int]:
    record, encoded = _read_json(path)
    match = _FORECAST_FILE.fullmatch(path.name)
    if (match is None or record.get("schema_version") != FORECAST_SCHEMA_VERSION
            or match.group(1) != record.get("snapshot_id")
            or record.get("registration_hash") != canonical_hash({key: value for key, value in record.items() if key != "registration_hash"})):
        raise ValueError("INVALID_FORECAST_REGISTRATION_HASH")
    snapshot = record.get("forecast")
    if not isinstance(snapshot, dict):
        raise ValueError("INVALID_FORECAST_REGISTRATION_BODY")
    _check_snapshot(snapshot)
    if (snapshot["snapshot_id"] != record["snapshot_id"] or snapshot["snapshot_hash"] != record.get("forecast_snapshot_hash")
            or snapshot["label_status"] != "PENDING" or snapshot["target_value"] is not None or snapshot["target_observed_at"] is not None
            or record.get("target_window") != _window(snapshot)
            or record.get("forecast_inputs_hash") != canonical_hash(_immutable_inputs(snapshot))):
        raise ValueError("INVALID_FORECAST_REGISTRATION_BODY")
    _check_input_clocks(_immutable_inputs(snapshot), _time(snapshot["as_of"]))
    _check_feature_outcomes(snapshot["feature_payload"])
    recorded_at = _time(record.get("registered_at"))
    if not _time(snapshot["as_of"]) <= recorded_at < _time(record["target_window"]["first_forward_session_open_at"]):
        raise ValueError("FORECAST_NOT_PROSPECTIVE_FOR_EXACT_WINDOW")
    if record.get("available_at") != record["registered_at"] or record.get("registration_id") != canonical_hash({"schema_version": FORECAST_SCHEMA_VERSION, "snapshot_id": record["snapshot_id"], "forecast_snapshot_hash": record["forecast_snapshot_hash"]}):
        raise ValueError("INVALID_FORECAST_REGISTRATION_IDENTITY")
    if record.get("source_record_id") != snapshot["snapshot_id"] or record.get("source_kind") != "STORED_PREDICTION_SNAPSHOT":
        raise ValueError("INVALID_FORECAST_SOURCE_RECORD_IDENTITY")
    if (record.get("analysis_only") is not True or any(record.get(flag) is not False for flag in (
            "stored_created_at_is_physical_registration", "forecast_authenticity_independently_verified",
            "feature_source_coverage_verified", "historical_acceptance_verified",
            "training_inputs_independently_verified", "scores_changed", "activation_allowed"))):
        raise ValueError("FORECAST_HISTORY_CANNOT_ASSERT_ACCEPTANCE")
    refs = record.get("source_references")
    if not isinstance(refs, list) or len(refs) > MAX_SOURCE_REFERENCES:
        raise ValueError("INVALID_FORECAST_SOURCE_REFERENCES")
    for ref in refs:
        if not isinstance(ref, dict):
            raise ValueError("INVALID_FORECAST_SOURCE_REFERENCES")
        for field in ("source_id", "content_hash", "file_sha256"):
            _hash(ref.get(field))
        expected_filename = (f"yahoo_price_{ref['source_id']}.json" if ref.get("kind") == "YAHOO_PRICE_OBSERVATION"
            else f"specialist_source_run_{ref['source_id']}_{ref['content_hash']}.json" if ref.get("kind") == "SPECIALIST_SOURCE_RUN" else None)
        if (expected_filename is None or ref.get("archive_filename") != expected_filename
                or isinstance(ref.get("file_bytes"), bool) or not isinstance(ref.get("file_bytes"), int)
                or not 0 < ref["file_bytes"] <= MAX_RUN_ARCHIVE_BYTES):
            raise ValueError("INVALID_FORECAST_SOURCE_ARCHIVE_IDENTITY")
        if not _time(ref.get("observed_at")) <= _time(ref.get("available_at")) <= _time(snapshot["as_of"]):
            raise ValueError("FORECAST_SOURCE_AVAILABLE_AFTER_PREDICTION")
    return record, len(encoded)


def _strict_revision_reason(observation: Mapping) -> str | None:
    for role in ("asset", "benchmark"):
        capture = observation.get("price_captures", {}).get(role, {})
        # A local content revision, market date or current-version claim cannot
        # substitute for an explicitly dated publisher version.
        if (capture.get("publisher_revision_timing_status") != "DATED_PUBLISHER_VERSION"
                or capture.get("revision_status") != "KNOWN_REVISION"):
            return "MISSING_DATED_PUBLISHER_PRICE_REVISION"
        if not any(isinstance(capture.get(field), str) and _HASH.fullmatch(capture[field])
                   for field in ("source_capture_id", "provider_payload_sha256")):
            return "MISSING_PUBLISHER_VERSION_SOURCE_ID"
    return None


def _load_label(path: Path, forecast: Mapping) -> tuple[dict, int]:
    record, encoded = _read_json(path)
    match = _LABEL_FILE.fullmatch(path.name)
    if (match is None or record.get("schema_version") != LABEL_SCHEMA_VERSION
            or match.group(1) != forecast["snapshot_id"] or record.get("snapshot_id") != forecast["snapshot_id"]
            or record.get("registration_id") != forecast["registration_id"]
            or record.get("registration_hash") != forecast["registration_hash"]
            or record.get("attachment_hash") != canonical_hash({key: value for key, value in record.items() if key != "attachment_hash"})):
        raise ValueError("INVALID_LABEL_ATTACHMENT_HASH")
    labeled = record.get("labeled_snapshot")
    observation = record.get("label_observation")
    if not isinstance(labeled, dict) or not isinstance(observation, dict):
        raise ValueError("INVALID_LABEL_ATTACHMENT_BODY")
    _check_snapshot(labeled)
    if _immutable_inputs(labeled) != _immutable_inputs(forecast["forecast"]):
        raise ValueError("ATTACHED_LABEL_FORECAST_INPUTS_CHANGED")
    if labeled.get("label_status") != "RESOLVED" or record.get("target_window") != forecast["target_window"]:
        raise ValueError("ATTACHED_LABEL_TARGET_CHANGED")
    if (record.get("analysis_only") is not True or any(record.get(flag) is not False for flag in (
            "publisher_authenticity_verified", "historical_acceptance_verified", "scores_changed", "activation_allowed"))):
        raise ValueError("LABEL_HISTORY_CANNOT_ASSERT_ACCEPTANCE")
    attached_at = _time(record.get("attached_at"))
    if attached_at < _time(forecast["registered_at"]) or record.get("available_at") != record["attached_at"]:
        raise ValueError("INVALID_LABEL_ATTACHMENT_CLOCK")
    audit = audit_label_observation(labeled, observation, as_of=attached_at)
    if audit["status"] != "ELIGIBLE_RECORDED_LABEL":
        raise ValueError(audit["reason"])
    issue = _strict_revision_reason(observation)
    if issue:
        raise ValueError(issue)
    if (audit["required_session_dates"] != forecast["target_window"]["required_session_dates"]
            or record.get("price_capture_hashes") != audit["price_capture_hashes"]
            or record.get("usable_at") != max(attached_at, _time(audit["usable_at"])).isoformat()
            or record.get("observation_hash") != observation["observation_hash"]
            or record.get("label_id") != canonical_hash({"schema_version": LABEL_SCHEMA_VERSION,
                "registration_id": forecast["registration_id"], "observation_hash": observation["observation_hash"]})):
        raise ValueError("INVALID_LABEL_ATTACHMENT_IDENTITY")
    return record, len(encoded)


def _publish(record: dict, path: Path, *, replay_matches: Callable[[dict], bool], loader: Callable[[Path], tuple[dict, int]]) -> dict:
    _no_symlink(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".prospective_", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(_encoded(record))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            saved, _ = loader(path)
            if not replay_matches(saved):
                raise ValueError("PROSPECTIVE_HISTORY_REWRITE_REFUSED") from None
            return saved
        saved, _ = loader(path)
        return saved
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class ProspectiveSpecialistHistory:
    """Bounded recorder for one database's immutable run/ticker snapshot IDs.

    ``observation_clock`` is for deterministic unit fixtures only. Production
    uses the physical UTC clock. Passing an old snapshot's as_of as that clock
    is not an authorized historical backfill. Source references pin supplied
    already existing files; their count never proves complete feature coverage.
    """

    def __init__(self, directory: Path, *, observation_clock: Callable[[], datetime] | None = None):
        self.directory = Path(directory)
        _no_symlink(self.directory)
        self._clock = observation_clock or physical_clock

    def _forecast_path(self, snapshot_id: str) -> Path:
        return self.directory / "forecasts" / f"forecast_{_hash(snapshot_id)}.json"

    def _label_path(self, snapshot_id: str) -> Path:
        return self.directory / "labels" / f"label_{_hash(snapshot_id)}.json"

    def register_stored_snapshot(self, db_path: Path, snapshot_id: str, *, source_archive_paths: Sequence[Path] = ()) -> dict:
        snapshot = read_stored_prediction_snapshot(db_path, snapshot_id)
        prediction_at = _time(snapshot["as_of"])
        _check_input_clocks(_immutable_inputs(snapshot), prediction_at)
        _check_feature_outcomes(snapshot["feature_payload"])
        references = _source_references(source_archive_paths, prediction_at=prediction_at)
        path = self._forecast_path(snapshot_id)
        if path.exists():
            saved, _ = _load_forecast(path)
            if (saved["forecast_inputs_hash"] != canonical_hash(_immutable_inputs(snapshot))
                    or saved["source_references"] != references):
                raise ValueError("PROSPECTIVE_HISTORY_REWRITE_REFUSED")
            # Label resolution must not change a preserved forecast or its clock.
            return saved
        if snapshot["label_status"] != "PENDING" or snapshot["target_value"] is not None or snapshot["target_observed_at"] is not None:
            raise ValueError("FIRST_REGISTRATION_REQUIRES_PENDING_FORECAST")
        recorded_at = _time(self._clock())
        window = _window(snapshot)
        if recorded_at < prediction_at:
            raise ValueError("FORECAST_OBSERVATION_AFTER_PHYSICAL_REGISTRATION")
        if recorded_at >= _time(window["first_forward_session_open_at"]):
            raise ValueError("FORECAST_NOT_PROSPECTIVE_FOR_EXACT_WINDOW")
        record = {"schema_version": FORECAST_SCHEMA_VERSION,
            "snapshot_id": snapshot_id, "forecast_snapshot_hash": snapshot["snapshot_hash"],
            "forecast_inputs_hash": canonical_hash(_immutable_inputs(snapshot)),
            "registration_id": canonical_hash({"schema_version": FORECAST_SCHEMA_VERSION,
                "snapshot_id": snapshot_id, "forecast_snapshot_hash": snapshot["snapshot_hash"]}),
            "registered_at": recorded_at.isoformat(), "available_at": recorded_at.isoformat(),
            "forecast": snapshot, "target_window": window, "source_references": references,
            "source_record_id": snapshot_id, "source_kind": "STORED_PREDICTION_SNAPSHOT",
            "stored_created_at_is_physical_registration": False,
            "forecast_authenticity_independently_verified": False,
            "feature_source_coverage_verified": False, "historical_acceptance_verified": False,
            "training_inputs_independently_verified": False,
            "analysis_only": True, "scores_changed": False, "activation_allowed": False}
        _seal(record, "registration_hash")
        return _publish(record, path, replay_matches=lambda saved:
            saved["forecast_inputs_hash"] == record["forecast_inputs_hash"] and saved["source_references"] == references,
            loader=_load_forecast)

    def attach_label(self, db_path: Path, snapshot_id: str, *, label_observation_path: Path) -> dict:
        forecast, _ = _load_forecast(self._forecast_path(snapshot_id))
        labeled = read_stored_prediction_snapshot(db_path, snapshot_id)
        if _immutable_inputs(labeled) != _immutable_inputs(forecast["forecast"]):
            raise ValueError("ATTACHED_LABEL_FORECAST_INPUTS_CHANGED")
        if labeled["label_status"] != "RESOLVED":
            return {"status": "WAIT_LABEL", "reason": "EXACT_TARGET_LABEL_NOT_RESOLVED", "snapshot_id": snapshot_id,
                    "required_session_dates": forecast["target_window"]["required_session_dates"]}
        path = self._label_path(snapshot_id)
        _no_symlink(Path(label_observation_path))
        if not Path(label_observation_path).is_file():
            return {"status": "WAIT_LABEL", "reason": "MISSING_LABEL_OBSERVATION", "snapshot_id": snapshot_id}
        raw, _ = _read_json(Path(label_observation_path))
        observation = load_label_observation(Path(label_observation_path))
        if observation != raw:
            raise ValueError("LABEL_OBSERVATION_CHANGED_DURING_READ")
        _encoded(observation)
        if path.exists():
            saved, _ = _load_label(path, forecast)
            if saved["labeled_snapshot"] != labeled or saved["label_observation"] != observation:
                raise ValueError("PROSPECTIVE_HISTORY_REWRITE_REFUSED")
            return {"status": "LABEL_ATTACHED", "record": saved, "replayed": True}
        attached_at = _time(self._clock())
        if attached_at < _time(forecast["registered_at"]):
            raise ValueError("LABEL_ATTACHMENT_BEFORE_FORECAST_REGISTRATION")
        audit = audit_label_observation(labeled, observation, as_of=attached_at)
        reason = audit["reason"] if audit["status"] != "ELIGIBLE_RECORDED_LABEL" else _strict_revision_reason(observation)
        if reason:
            return {"status": "WAIT_LABEL", "reason": reason, "snapshot_id": snapshot_id,
                    "required_session_dates": forecast["target_window"]["required_session_dates"]}
        if audit["required_session_dates"] != forecast["target_window"]["required_session_dates"]:
            raise ValueError("ATTACHED_LABEL_TARGET_SESSION_CHANGED")
        record = {"schema_version": LABEL_SCHEMA_VERSION, "snapshot_id": snapshot_id,
            "registration_id": forecast["registration_id"], "registration_hash": forecast["registration_hash"],
            "label_id": canonical_hash({"schema_version": LABEL_SCHEMA_VERSION,
                "registration_id": forecast["registration_id"], "observation_hash": observation["observation_hash"]}),
            "observation_hash": observation["observation_hash"], "attached_at": attached_at.isoformat(),
            "available_at": attached_at.isoformat(), "usable_at": max(attached_at, _time(audit["usable_at"])).isoformat(),
            "target_window": forecast["target_window"], "labeled_snapshot": labeled,
            "label_observation": observation, "price_capture_hashes": audit["price_capture_hashes"],
            "publisher_authenticity_verified": False, "historical_acceptance_verified": False,
            "analysis_only": True, "scores_changed": False, "activation_allowed": False}
        _seal(record, "attachment_hash")
        saved = _publish(record, path,
            replay_matches=lambda existing: existing["labeled_snapshot"] == labeled and existing["label_observation"] == observation,
            loader=lambda location: _load_label(location, forecast))
        return {"status": "LABEL_ATTACHED", "record": saved, "replayed": saved["attachment_hash"] != record["attachment_hash"]}

    def read_report(self, *, as_of: datetime, training_before: datetime | None = None,
                    maximum: int = 1_000) -> dict:
        """Read bounded immutable history; unavailable rows never become zeros.

        Eligible rows require registration and label knowledge at the report
        cutoff. ``training_before`` additionally requires both clocks strictly
        before that prediction, even when evaluation is requested later.
        Fixture evidence is always separately counted and never returned as
        real eligible snapshots. Missing source pins cannot be added later to
        rewrite what a forecast knew at prediction time.
        """
        cutoff = _time(as_of)
        before = _time(training_before) if training_before is not None else None
        if before is not None and before > cutoff:
            raise ValueError("TRAINING_PREDICTION_AFTER_REPORT_CUTOFF")
        _bounded(maximum, MAX_HISTORY_RECORDS)
        _no_symlink(self.directory)
        rows, eligible, failures, byte_count = [], [], Counter(), 0
        bounds = {"maximum_forecasts": maximum, "maximum_total_record_bytes": MAX_REPORT_BYTES,
                  "directory_scan_limit_reached": False, "forecast_limit_reached": False,
                  "label_directory_scan_limit_reached": False, "label_limit_reached": False,
                  "total_byte_limit_reached": False, "loaded_record_bytes": 0}
        directory = self.directory / "forecasts"
        _no_symlink(directory)
        paths = list(islice(directory.iterdir(), maximum * 3 + 1)) if directory.exists() else []
        bounds["directory_scan_limit_reached"] = len(paths) > maximum * 3
        candidates = sorted((path for path in paths[:maximum * 3] if _FORECAST_FILE.fullmatch(path.name)), key=lambda item: item.name)
        bounds["forecast_limit_reached"] = len(candidates) > maximum
        label_directory = self.directory / "labels"
        _no_symlink(label_directory)
        label_paths = list(islice(label_directory.iterdir(), maximum * 3 + 1)) if label_directory.exists() else []
        bounds["label_directory_scan_limit_reached"] = len(label_paths) > maximum * 3
        label_candidates = [path for path in label_paths[:maximum * 3] if _LABEL_FILE.fullmatch(path.name)]
        bounds["label_limit_reached"] = len(label_candidates) > maximum
        for label_path in label_candidates[:maximum]:
            identifier = _LABEL_FILE.fullmatch(label_path.name).group(1)
            if not self._forecast_path(identifier).is_file():
                failures["ORPHAN_LABEL_WITHOUT_FORECAST_REGISTRATION"] += 1
        for path in candidates[:maximum]:
            try:
                charged_size = min(path.stat().st_size, MAX_RECORD_BYTES + 1)
                if byte_count + charged_size > MAX_REPORT_BYTES:
                    bounds["total_byte_limit_reached"] = True
                    break
                byte_count += charged_size
                forecast, size = _load_forecast(path)
                byte_count += max(0, size - charged_size)
                if byte_count > MAX_REPORT_BYTES:
                    bounds["total_byte_limit_reached"] = True
                    break
                row = {"snapshot_id": forecast["snapshot_id"], "registration_id": forecast["registration_id"],
                    "forecast_snapshot_hash": forecast["forecast_snapshot_hash"],
                    "registered_at": forecast["registered_at"], "prediction_at": forecast["forecast"]["as_of"],
                    "target_session_date": forecast["target_window"]["target_session_date"],
                    "required_session_dates": forecast["target_window"]["required_session_dates"],
                    "source_reference_count": len(forecast["source_references"]),
                    "source_ids": [ref["source_id"] for ref in forecast["source_references"]],
                    "status": "WAIT_LABEL", "reason": "MISSING_LABEL_ATTACHMENT", "label_usable_at": None,
                    "eligible_for_requested_clock": False, "fixture_only": False}
                rows.append(row)
                registered = _time(forecast["registered_at"])
                if registered > cutoff:
                    row.update(status="EXCLUDED", reason="FORECAST_REGISTRATION_AFTER_REPORT_CUTOFF")
                    continue
                if before is not None and (registered >= before or _time(row["prediction_at"]) >= before):
                    row.update(status="EXCLUDED", reason="FORECAST_NOT_STRICTLY_BEFORE_TRAINING_PREDICTION")
                    continue
                label_path = self._label_path(forecast["snapshot_id"])
                _no_symlink(label_path)
                if not label_path.exists():
                    continue
                charged_size = min(label_path.stat().st_size, MAX_RECORD_BYTES + 1)
                if byte_count + charged_size > MAX_REPORT_BYTES:
                    bounds["total_byte_limit_reached"] = True
                    break
                byte_count += charged_size
                label, label_size = _load_label(label_path, forecast)
                byte_count += max(0, label_size - charged_size)
                if byte_count > MAX_REPORT_BYTES:
                    bounds["total_byte_limit_reached"] = True
                    break
                usable = _time(label["usable_at"])
                row.update(label_usable_at=label["usable_at"], observation_hash=label["observation_hash"],
                           price_capture_hashes=label["price_capture_hashes"])
                if usable > cutoff:
                    row.update(status="EXCLUDED", reason="LABEL_ATTACHMENT_AFTER_REPORT_CUTOFF")
                    continue
                if before is not None and usable >= before:
                    row.update(status="EXCLUDED", reason="LABEL_NOT_STRICTLY_BEFORE_TRAINING_PREDICTION")
                    continue
                if not forecast["source_references"]:
                    row.update(status="WAIT_SOURCE_PROVENANCE", reason="MISSING_FORECAST_SOURCE_ARCHIVE_PINS")
                    continue
                fixture = (label["label_observation"]["evidence_origin"] == "DETERMINISTIC_TEST_FIXTURE"
                    or forecast["forecast"]["provenance"].get("evidence_origin") == "DETERMINISTIC_TEST_FIXTURE"
                    or any(ref.get("evidence_origin") == "DETERMINISTIC_TEST_FIXTURE" for ref in forecast["source_references"]))
                row.update(eligible_for_requested_clock=True, fixture_only=fixture,
                    status="FIXTURE_ONLY_LABEL" if fixture else "ELIGIBLE_RECORDED_LABEL", reason="")
                if not fixture:
                    eligible.append(label["labeled_snapshot"])
            except (ValueError, OSError, sqlite3.Error) as error:
                failures[str(error) if isinstance(error, ValueError) else "HISTORY_READ_FAILED"] += 1
                if rows and rows[-1].get("snapshot_id") == _FORECAST_FILE.fullmatch(path.name).group(1):
                    rows[-1].update(status="EXCLUDED", reason="INVALID_IMMUTABLE_HISTORY_RECORD",
                                    eligible_for_requested_clock=False)
        bounds["loaded_record_bytes"] = byte_count
        incomplete = bool(failures or any(bounds[field] for field in (
            "directory_scan_limit_reached", "forecast_limit_reached", "label_directory_scan_limit_reached",
            "label_limit_reached", "total_byte_limit_reached")))
        if incomplete:
            eligible = []
            for row in rows:
                if row["eligible_for_requested_clock"]:
                    row.update(status="EXCLUDED", reason="INCOMPLETE_BOUNDED_HISTORY",
                               eligible_for_requested_clock=False)
        return {"report_version": REPORT_VERSION, "as_of": cutoff.isoformat(),
            "training_before": before.isoformat() if before else None,
            "status": "PARTIAL_HISTORY" if incomplete else "RECORDED_LABELS_AVAILABLE" if eligible else "WAIT_PROSPECTIVE_HISTORY",
            "history_directory_present": self.directory.is_dir(),
            "registered_forecast_count": len(rows), "eligible_real_label_count": len(eligible),
            "registrations_known_at_cutoff": sum(_time(row["registered_at"]) <= cutoff for row in rows),
            "label_attachment_count": sum("observation_hash" in row for row in rows),
            "eligible_fixture_label_count": sum(row["fixture_only"] and row["eligible_for_requested_clock"] for row in rows),
            "reason_counts": dict(sorted(Counter(row["reason"] or row["status"] for row in rows).items())),
            "rejected_record_reasons": dict(sorted(failures.items())), "bounds": bounds,
            "rows": rows, "eligible_snapshots": eligible,
            "analysis_only": True, "scores_changed": False, "activation_allowed": False,
            "publisher_authenticity_verified": False, "feature_source_coverage_verified": False,
            "historical_acceptance_verified": False, "out_of_sample_evaluation_verified": False,
            "limitations": [
                "Only already stored forecasts recorded before the first forward session opens are prospective for the exact full window.",
                "Physical registration and attachment clocks never substitute an earlier event/session/storage clock.",
                "A source ID and content hash pin supplied evidence; they do not prove complete feature coverage or authentic publisher revision metadata.",
                "Missing exact sessions, corporate-action histories or dated publisher revisions exclude labels. A later session cannot replace the missing target.",
                "Current canonical 687 tickers are not a historical survivor-free universe. Fixture rows never become real acceptance.",
                "This is read-only analysis history, without model fitting, performance claims, score activation or trading."]}


__all__ = ["ProspectiveSpecialistHistory", "read_stored_prediction_snapshot", "REPORT_VERSION",
           "FORECAST_SCHEMA_VERSION", "LABEL_SCHEMA_VERSION"]
