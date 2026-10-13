"""Audit recorded forward-price observations without inventing PIT labels.

The exchange close is an event time. It is not the time a price frame, a
correction or its immutable archive became available to this application.
Self-contained price-window captures keep those clocks separate. Hashes prove
consistency of the recorded content, never publisher authenticity or OOS lift.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from itertools import islice
import json
import math
import os
from pathlib import Path
import re
import tempfile

from market_checker_app.prediction_contract import (
    PRIMARY_PREDICTION_TARGET, canonical_hash, compute_excess_return_target,
)
from market_checker_app.services.us_equity_calendar import target_us_equity_window


LABEL_OBSERVATION_SCHEMA_VERSION = "specialist_label_observation_v1"
LABEL_AVAILABILITY_REPORT_VERSION = "specialist_label_availability_v1"
MAX_LABEL_OBSERVATION_BYTES = 262_144
MAX_LABEL_OBSERVATIONS = 10_000
MAX_TOTAL_LABEL_OBSERVATION_BYTES = 64_000_000
_SHA256 = re.compile(r"[a-f0-9]{64}")
_FILE_NAME = re.compile(r"prediction_label_([a-f0-9]{64})_([a-f0-9]{64})\.json")
_ORIGINS = {"RECORDED_PRICE_FRAME", "DETERMINISTIC_TEST_FIXTURE"}


def _time(value: object) -> datetime | None:
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        return None
    return stamp.astimezone(timezone.utc)


def _number(value: object, *, positive: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and (not positive or number > 0) else None


def _date(value: object) -> date | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _bounded(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_LABEL_OBSERVATIONS:
        raise ValueError("maximum_label_observations must be between 1 and 10000")


def _integrity(record: object) -> str | None:
    if not isinstance(record, Mapping) or record.get("schema_version") != LABEL_OBSERVATION_SCHEMA_VERSION:
        return "UNSUPPORTED_LABEL_OBSERVATION"
    try:
        encoded = json.dumps(record, allow_nan=False, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError):
        return "INVALID_LABEL_OBSERVATION_CONTENT"
    if len(encoded) > MAX_LABEL_OBSERVATION_BYTES:
        return "LABEL_OBSERVATION_BYTE_LIMIT"
    if record.get("observation_hash") != canonical_hash({key: value for key, value in record.items() if key != "observation_hash"}):
        return "INVALID_LABEL_OBSERVATION_HASH"
    if record.get("evidence_origin") not in _ORIGINS:
        return "UNSUPPORTED_LABEL_EVIDENCE_ORIGIN"
    return None


def label_observation_filename(record: Mapping) -> str:
    """Content address a validated record; a name is never an acceptance proof."""
    issue = _integrity(record)
    if issue or not _SHA256.fullmatch(str(record.get("snapshot_id") or "")):
        raise ValueError(issue or "INVALID_LABEL_SNAPSHOT_ID")
    return f"prediction_label_{record['snapshot_id']}_{record['observation_hash']}.json"


def _identifier(value: object) -> str:
    return value if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", value) else "UNKNOWN"


def _recorded_capture(*, ticker: str, sessions: Sequence, prices: Sequence[float],
                      observed_at: datetime, provenance: Mapping | None) -> dict:
    """Project supplied metadata only; a plain Close frame proves no actions."""
    supplied = isinstance(provenance, Mapping)
    metadata = provenance if supplied else {}
    stamp = observed_at.isoformat()
    actions = []
    raw_actions = metadata.get("corporate_actions")
    actions_incomplete = not isinstance(raw_actions, list) or len(raw_actions) > 100
    if isinstance(raw_actions, list):
        for action in raw_actions[:100]:
            if not isinstance(action, Mapping):
                actions_incomplete = True
                continue
            kind = action.get("kind")
            effective = _date(action.get("effective_session"))
            known = _time(action.get("known_at"))
            if kind not in {"SPLIT", "DIVIDEND", "SYMBOL_CHANGE", "MERGER"} or effective is None or known is None:
                actions_incomplete = True
                continue
            recorded = {"kind": kind, "effective_session": effective.isoformat(), "known_at": known.isoformat()}
            if kind == "SPLIT":
                recorded["ratio"] = _number(action.get("ratio"), positive=True)
                actions_incomplete |= recorded["ratio"] is None
            actions.append(recorded)
    capture = {"ticker": ticker, "provider": _identifier(metadata.get("provider")),
        "price_basis": _identifier(metadata.get("price_basis")),
        "price_method_version": _identifier(metadata.get("price_method_version")),
        "dividends_included": metadata.get("dividends_included") if isinstance(metadata.get("dividends_included"), bool) else None,
        "observed_at": stamp, "available_at": stamp, "provenance_supplied": supplied,
        "revision_status": metadata.get("revision_status") if metadata.get("revision_status") in {"CAPTURED_CURRENT_VERSION", "KNOWN_REVISION"} else "UNKNOWN",
        "revision_known_at": known.isoformat() if (known := _time(metadata.get("revision_known_at"))) is not None else None,
        "corporate_action_status": "RECORDED_COMPLETE_WINDOW" if metadata.get("corporate_action_status") == "RECORDED_COMPLETE_WINDOW" and not actions_incomplete else "UNKNOWN",
        "corporate_actions_checked_from": day.isoformat() if (day := _date(metadata.get("corporate_actions_checked_from"))) is not None else None,
        "corporate_actions_checked_through": day.isoformat() if (day := _date(metadata.get("corporate_actions_checked_through"))) is not None else None,
        "corporate_actions_known_at": known.isoformat() if (known := _time(metadata.get("corporate_actions_known_at"))) is not None else None,
        "corporate_actions": actions,
        "corporate_action_record_projection_incomplete": actions_incomplete,
        "session_prices": [{"session_date": session.session_date.isoformat(), "close": float(close),
            "observed_at": stamp, "available_at": stamp} for session, close in zip(sessions, prices)]}
    if (provider_observed := _time(metadata.get("provider_observed_at"))) is not None:
        capture["provider_observed_at"] = provider_observed.isoformat()
    payload_hash = metadata.get("provider_payload_sha256")
    if isinstance(payload_hash, str) and _SHA256.fullmatch(payload_hash):
        capture["provider_payload_sha256"] = payload_hash
    capture["capture_hash"] = canonical_hash(capture)
    return capture


def build_recorded_label_observation(snapshot: Mapping, *, asset_prices: Sequence[float],
        benchmark_prices: Sequence[float], asset_observed_at: datetime, benchmark_observed_at: datetime,
        captured_at: datetime, asset_provenance: Mapping | None = None,
        benchmark_provenance: Mapping | None = None) -> dict:
    """Capture exact consumed prices at actual call clocks, even if metadata lacks.

    The resolver supplies physical clocks after returning data. Its logical
    evaluation cutoff, exchange endpoint and older cache clocks are not capture
    times. This does not grant unknown metadata a verified price methodology.
    """
    snapshot_at = _time(snapshot.get("as_of"))
    asset_at, benchmark_at, captured = map(_time, (asset_observed_at, benchmark_observed_at, captured_at))
    if snapshot_at is None or any(stamp is None for stamp in (asset_at, benchmark_at, captured)):
        raise ValueError("Label capture needs explicit physical timezone clocks")
    horizon = snapshot.get("horizon_trading_days")
    if isinstance(horizon, bool) or not isinstance(horizon, int) or not 1 <= horizon <= 252:
        raise ValueError("Label capture needs a bounded target horizon")
    base, future = target_us_equity_window(snapshot_at, horizon)
    sessions = [base, *future]
    if not future[-1].close_at <= min(asset_at, benchmark_at) <= max(asset_at, benchmark_at) <= captured:
        raise ValueError("Price observation cannot precede future closes or follow capture")
    for prices in (asset_prices, benchmark_prices):
        if len(prices) != len(sessions) or any(_number(value, positive=True) is None for value in prices):
            raise ValueError("Label capture needs the exact complete positive-price session window")
    target = compute_excess_return_target(asset_prices, benchmark_prices, horizon)
    stored = _number(snapshot.get("target_value"))
    if target is None or stored is None or not math.isclose(target, stored, abs_tol=1e-12, rel_tol=1e-10):
        raise ValueError("Label capture prices disagree with the resolved target")
    record = {"schema_version": LABEL_OBSERVATION_SCHEMA_VERSION, "snapshot_id": snapshot["snapshot_id"],
        "ticker": snapshot["ticker"], "benchmark_ticker": snapshot["benchmark_ticker"],
        "target_version": snapshot["target_version"], "horizon_trading_days": horizon,
        "snapshot_as_of": snapshot_at.isoformat(), "target_value": stored,
        "observed_at": max(asset_at, benchmark_at).isoformat(), "available_at": captured.isoformat(),
        "captured_at": captured.isoformat(), "evidence_origin": "RECORDED_PRICE_FRAME",
        "price_captures": {
            "asset": _recorded_capture(ticker=snapshot["ticker"], sessions=sessions, prices=asset_prices,
                observed_at=asset_at, provenance=asset_provenance),
            "benchmark": _recorded_capture(ticker=snapshot["benchmark_ticker"], sessions=sessions, prices=benchmark_prices,
                observed_at=benchmark_at, provenance=benchmark_provenance)}}
    record["observation_hash"] = canonical_hash(record)
    issue = _integrity(record)
    if issue:
        raise ValueError(issue)
    return record


def persist_label_observation(record: Mapping, directory: Path) -> Path:
    """Atomically preserve one immutable content-addressed capture before labeling."""
    name = label_observation_filename(record)
    encoded = (json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(encoded) > MAX_LABEL_OBSERVATION_BYTES:
        raise ValueError("LABEL_OBSERVATION_BYTE_LIMIT")
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError("Label archive directory cannot be a symlink")
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / name
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".prediction_label_", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if load_label_observation(destination) != dict(record):
                raise ValueError("Existing label capture differs; refusing rewrite") from None
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_label_observation(path: Path) -> dict:
    """Read actual bounded bytes and verify both embedded and filename hashes."""
    path = Path(path)
    match = _FILE_NAME.fullmatch(path.name)
    if match is None or path.is_symlink() or not path.is_file():
        raise ValueError("INVALID_LABEL_OBSERVATION_PATH")
    with path.open("rb") as handle:
        raw = handle.read(MAX_LABEL_OBSERVATION_BYTES + 1)
    if len(raw) > MAX_LABEL_OBSERVATION_BYTES:
        raise ValueError("LABEL_OBSERVATION_BYTE_LIMIT")
    try:
        record = json.loads(raw)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ValueError("INVALID_LABEL_OBSERVATION_CONTENT") from None
    issue = _integrity(record)
    if issue:
        raise ValueError(issue)
    if (record.get("snapshot_id"), record.get("observation_hash")) != match.groups():
        raise ValueError("LABEL_OBSERVATION_FILENAME_HASH_MISMATCH")
    return dict(record)


def load_label_observations(directory: Path, *, maximum: int = MAX_LABEL_OBSERVATIONS) -> tuple[list[dict], dict]:
    """Bound file discovery and content reads; absence is reported, not created."""
    _bounded(maximum)
    directory = Path(directory)
    metadata = {"archive_directory_present": directory.is_dir(), "loaded_files": 0,
                "rejected_files": 0, "rejected_file_reasons": {},
                "directory_scan_limit_reached": False, "observation_limit_reached": False,
                "total_byte_limit_reached": False, "loaded_file_bytes": 0}
    if not directory.exists():
        return [], metadata
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Label observation directory must be an existing regular directory")
    # Unrelated files also cost work. Never scan an unbounded directory to
    # locate a small number of label records, or claim complete coverage then.
    paths = list(islice(directory.iterdir(), maximum * 3 + 1))
    metadata["directory_scan_limit_reached"] = len(paths) > maximum * 3
    candidates = sorted((path for path in paths[:maximum * 3] if _FILE_NAME.fullmatch(path.name)), key=lambda path: path.name)
    metadata["observation_limit_reached"] = len(candidates) > maximum
    records = []
    failures: Counter = Counter()
    for path in candidates[:maximum]:
        try:
            file_bytes = path.stat().st_size
            if metadata["loaded_file_bytes"] + min(file_bytes, MAX_LABEL_OBSERVATION_BYTES + 1) > MAX_TOTAL_LABEL_OBSERVATION_BYTES:
                metadata["total_byte_limit_reached"] = True
                break
            metadata["loaded_file_bytes"] += min(file_bytes, MAX_LABEL_OBSERVATION_BYTES + 1)
            records.append(load_label_observation(path))
        except (OSError, ValueError) as error:
            reason = str(error) if isinstance(error, ValueError) else "LABEL_OBSERVATION_READ_FAILED"
            failures[reason] += 1
    metadata.update(loaded_files=len(records), rejected_files=sum(failures.values()),
                    rejected_file_reasons=dict(sorted(failures.items())))
    return records, metadata


def _audit_capture(capture: object, *, ticker: str, sessions: Sequence,
                   archive_observed_at: datetime) -> tuple[list[float] | None, datetime | None, str | None]:
    if not isinstance(capture, Mapping):
        return None, None, "MISSING_ASSET_OR_BENCHMARK_CAPTURE"
    if capture.get("capture_hash") != canonical_hash({key: value for key, value in capture.items() if key != "capture_hash"}):
        return None, None, "INVALID_PRICE_CAPTURE_HASH"
    if capture.get("provenance_supplied") is False:
        return None, None, "MISSING_PRICE_SOURCE_PROVENANCE"
    if capture.get("ticker") != ticker or not isinstance(capture.get("provider"), str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", capture["provider"]):
        return None, None, "INVALID_PRICE_SOURCE_IDENTITY"
    if (capture.get("price_basis") != PRIMARY_PREDICTION_TARGET.price_basis
            or capture.get("price_method_version") != PRIMARY_PREDICTION_TARGET.price_method_version
            or capture.get("dividends_included") is not False):
        return None, None, "INCOMPATIBLE_LABEL_PRICE_METHOD"
    observed = _time(capture.get("observed_at"))
    available = _time(capture.get("available_at"))
    if observed is None or available is None or not sessions[-1].close_at <= observed <= available <= archive_observed_at:
        return None, None, "INVALID_PRICE_CAPTURE_AVAILABILITY"
    if "provider_observed_at" in capture:
        provider_observed = _time(capture["provider_observed_at"])
        if provider_observed is None or provider_observed > observed:
            return None, None, "INVALID_PROVIDER_PRICE_OBSERVATION"
    revision = capture.get("revision_status")
    revision_at = observed
    if revision == "KNOWN_REVISION":
        revision_at = _time(capture.get("revision_known_at"))
        if revision_at is None or revision_at > observed:
            return None, None, "INVALID_PRICE_REVISION_KNOWLEDGE"
    elif revision != "CAPTURED_CURRENT_VERSION":
        return None, None, "MISSING_PRICE_REVISION_PROVENANCE"
    if capture.get("corporate_action_status") != "RECORDED_COMPLETE_WINDOW":
        return None, None, "INCOMPLETE_CORPORATE_ACTION_PROVENANCE"
    action_start = _date(capture.get("corporate_actions_checked_from"))
    action_end = _date(capture.get("corporate_actions_checked_through"))
    action_at = _time(capture.get("corporate_actions_known_at"))
    if (action_start is None or action_end is None or action_start > sessions[0].session_date
            or action_end < sessions[-1].session_date or action_at is None
            or not sessions[-1].close_at <= action_at <= observed):
        return None, None, "INCOMPLETE_CORPORATE_ACTION_WINDOW"
    actions = capture.get("corporate_actions")
    if not isinstance(actions, list) or len(actions) > 100:
        return None, None, "INVALID_CORPORATE_ACTION_RECORDS"
    action_times = [action_at]
    for action in actions:
        if not isinstance(action, Mapping) or action.get("kind") not in {"SPLIT", "DIVIDEND", "SYMBOL_CHANGE", "MERGER"}:
            return None, None, "INVALID_CORPORATE_ACTION_RECORDS"
        effective = _date(action.get("effective_session"))
        known_at = _time(action.get("known_at"))
        if effective is None or known_at is None or known_at > observed:
            return None, None, "INVALID_CORPORATE_ACTION_KNOWLEDGE"
        if action.get("kind") == "SPLIT" and _number(action.get("ratio"), positive=True) is None:
            return None, None, "INVALID_CORPORATE_ACTION_RECORDS"
        action_times.append(known_at)
    prices = capture.get("session_prices")
    if not isinstance(prices, list) or len(prices) != len(sessions):
        return None, None, "INCOMPLETE_FORWARD_SESSION_WINDOW"
    lookup = {}
    price_times = []
    expected = {session.session_date: session for session in sessions}
    for price in prices:
        if not isinstance(price, Mapping):
            return None, None, "INVALID_SESSION_PRICE"
        session_date = _date(price.get("session_date"))
        close = _number(price.get("close"), positive=True)
        if session_date not in expected or session_date in lookup or close is None:
            return None, None, "INCOMPLETE_FORWARD_SESSION_WINDOW"
        price_observed = _time(price.get("observed_at"))
        price_available = _time(price.get("available_at"))
        if price_observed is None or price_available is None or not expected[session_date].close_at <= price_observed <= price_available <= available:
            return None, None, "INVALID_SESSION_PRICE_AVAILABILITY"
        lookup[session_date] = close
        price_times.extend((price_observed, price_available))
    return [lookup[session.session_date] for session in sessions], max(observed, available, revision_at, *action_times, *price_times), None


def audit_label_observation(snapshot: Mapping, observation: Mapping, *, as_of: datetime) -> dict:
    """Validate one self-contained observation against the immutable snapshot."""
    as_of = _time(as_of)
    if as_of is None:
        raise ValueError("Label audit as_of must include timezone")
    if not isinstance(observation, Mapping):
        return {"status": "INSUFFICIENT_DATA", "reason": "UNSUPPORTED_LABEL_OBSERVATION", "usable_at": None,
                "horizon_end": None, "observation_hash": None, "publisher_authenticity_verified": False}
    result = {"status": "INSUFFICIENT_DATA", "reason": "", "usable_at": None,
              "horizon_end": None, "observation_hash": observation.get("observation_hash"),
              "evidence_origin": observation.get("evidence_origin"),
              "price_capture_hashes": {}, "publisher_authenticity_verified": False}

    def reject(reason: str) -> dict:
        result["reason"] = reason
        return result

    issue = _integrity(observation)
    if issue:
        return reject(issue)
    snapshot_at = _time(snapshot.get("as_of"))
    observed = _time(observation.get("observed_at"))
    available = _time(observation.get("available_at"))
    captured = _time(observation.get("captured_at"))
    if snapshot_at is None or any(stamp is None for stamp in (observed, available, captured)):
        return reject("INVALID_LABEL_OBSERVATION_TIME")
    if not observed <= available <= captured:
        return reject("INVALID_LABEL_OBSERVATION_TIME")
    result.update(label_observed_at=observed.isoformat(), archive_available_at=available.isoformat(),
                  archive_captured_at=captured.isoformat())
    if captured > as_of:
        return reject("LABEL_ARCHIVE_AFTER_REPORT_CUTOFF")
    for field in ("snapshot_id", "ticker", "benchmark_ticker", "target_version", "horizon_trading_days"):
        if observation.get(field) != snapshot.get(field):
            return reject("LABEL_OBSERVATION_SNAPSHOT_MISMATCH")
    if _time(observation.get("snapshot_as_of")) != snapshot_at:
        return reject("LABEL_OBSERVATION_SNAPSHOT_MISMATCH")
    try:
        horizon = snapshot["horizon_trading_days"]
        if isinstance(horizon, bool) or not isinstance(horizon, int) or not 1 <= horizon <= 252:
            return reject("INVALID_LABEL_HORIZON")
        base, future = target_us_equity_window(snapshot_at, horizon)
    except (ValueError, TypeError, IndexError):
        return reject("INVALID_LABEL_HORIZON")
    sessions = [base, *future]
    result["horizon_end"] = sessions[-1].close_at.isoformat()
    result["required_session_dates"] = [session.session_date.isoformat() for session in sessions]
    if observed < sessions[-1].close_at:
        return reject("PREMATURE_LABEL_OBSERVATION")
    captures = observation.get("price_captures")
    if not isinstance(captures, Mapping):
        return reject("MISSING_ASSET_OR_BENCHMARK_CAPTURE")
    values = {}
    source_times = []
    result["source_price_windows"] = {}
    for role, field in (("asset", "ticker"), ("benchmark", "benchmark_ticker")):
        capture = captures.get(role)
        prices, usable_at, issue = _audit_capture(capture, ticker=snapshot[field],
            sessions=sessions, archive_observed_at=observed)
        if issue:
            return reject(issue)
        values[role] = prices
        source_times.append(usable_at)
        result["price_capture_hashes"][role] = capture["capture_hash"]
        result["source_price_windows"][role] = {"ticker": snapshot[field], "provider": capture["provider"],
            "observed_at": _time(capture["observed_at"]).isoformat(),
            "available_at": _time(capture["available_at"]).isoformat(),
            "revision_status": capture["revision_status"],
            "revision_known_at": _time(capture["revision_known_at"]).isoformat() if capture["revision_status"] == "KNOWN_REVISION" else None,
            "corporate_action_status": capture["corporate_action_status"],
            "corporate_actions_checked_from": capture["corporate_actions_checked_from"],
            "corporate_actions_checked_through": capture["corporate_actions_checked_through"],
            "corporate_actions_known_at": _time(capture["corporate_actions_known_at"]).isoformat(),
            "session_count": len(capture["session_prices"])}
    target = compute_excess_return_target(values["asset"], values["benchmark"], horizon)
    stored = _number(snapshot.get("target_value"))
    recorded = _number(observation.get("target_value"))
    if any(value is None for value in (target, stored, recorded)) or not math.isclose(target, stored, abs_tol=1e-12, rel_tol=1e-10) or not math.isclose(target, recorded, abs_tol=1e-12, rel_tol=1e-10):
        return reject("LABEL_VALUE_DIFFERS_FROM_CAPTURE")
    label_at = _time(snapshot.get("target_observed_at"))
    if label_at is None or label_at < sessions[-1].close_at:
        return reject("INVALID_OR_PREMATURE_RESOLVED_LABEL")
    usable = max(label_at, observed, available, captured, *source_times)
    if usable > as_of:
        return reject("LABEL_AFTER_REPORT_CUTOFF")
    result.update(status="ELIGIBLE_RECORDED_LABEL", reason="", usable_at=usable.isoformat(),
                  target_timestamp_precedes_capture=label_at < captured,
                  complete_forward_session_count=len(sessions),
                  target_value_recomputed=True)
    return result


def prepare_label_observations(observations: Sequence[Mapping] | None, *,
        directory: Path | None = None, maximum: int = MAX_LABEL_OBSERVATIONS) -> tuple[dict[str, list[dict]], dict]:
    """Index bounded, validated captures. No source query or database write."""
    _bounded(maximum)
    records = list(islice(observations or (), maximum + 1))
    metadata = {"report_version": LABEL_AVAILABILITY_REPORT_VERSION,
                "maximum_observations": maximum, "explicit_observation_limit_reached": len(records) > maximum,
                "rejected_observation_reasons": {}, "archive_directory_present": False,
                "loaded_files": 0, "rejected_files": 0, "rejected_file_reasons": {},
                "directory_scan_limit_reached": False, "observation_limit_reached": False,
                "total_byte_limit_reached": False, "loaded_file_bytes": 0,
                "maximum_total_observation_bytes": MAX_TOTAL_LABEL_OBSERVATION_BYTES}
    records = records[:maximum]
    if directory is not None:
        loaded, archive_metadata = load_label_observations(directory, maximum=maximum)
        metadata.update(archive_metadata)
        metadata["observation_limit_reached"] |= len(records) + len(loaded) > maximum
        records.extend(loaded[:max(0, maximum - len(records))])
    by_id = defaultdict(list)
    failures: Counter = Counter()
    seen = set()
    total_bytes = 0
    for record in records:
        issue = _integrity(record)
        if issue:
            failures[issue] += 1
            continue
        identifier = record.get("snapshot_id")
        if not isinstance(identifier, str) or _SHA256.fullmatch(identifier) is None:
            failures["INVALID_LABEL_SNAPSHOT_ID"] += 1
            continue
        if record["observation_hash"] not in seen:
            record_bytes = len(json.dumps(record, allow_nan=False, sort_keys=True).encode("utf-8"))
            if total_bytes + record_bytes > MAX_TOTAL_LABEL_OBSERVATION_BYTES:
                metadata["total_byte_limit_reached"] = True
                break
            total_bytes += record_bytes
            by_id[identifier].append(dict(record))
            seen.add(record["observation_hash"])
    for records_for_id in by_id.values():
        records_for_id.sort(key=lambda record: (str(record.get("captured_at")), record["observation_hash"]))
    metadata.update(validated_observation_count=sum(len(records_for_id) for records_for_id in by_id.values()),
                    validated_observation_bytes=total_bytes,
                    rejected_observation_reasons=dict(sorted(failures.items())))
    return dict(by_id), metadata


def audit_snapshot_label(snapshot: Mapping, observations: Sequence[Mapping], *, as_of: datetime) -> dict:
    """Choose only an actual version matching the stored label, at its real clock."""
    if not observations:
        return {"status": "INSUFFICIENT_DATA", "reason": "MISSING_LABEL_OBSERVATION", "usable_at": None,
                "horizon_end": None, "observation_hash": None,
                "publisher_authenticity_verified": False}
    audited = [audit_label_observation(snapshot, observation, as_of=as_of) for observation in observations]
    versions = {"observation_version_count": len(audited),
        "observation_version_reason_counts": dict(sorted(Counter(record["reason"] or record["status"] for record in audited).items()))}
    eligible = [record for record in audited if record["status"] == "ELIGIBLE_RECORDED_LABEL"]
    # A conflicting captured revision is visible rather than replaced by an
    # older convenient version. Observations beyond cutoff cannot affect it.
    conflicts = any(record["reason"] == "LABEL_VALUE_DIFFERS_FROM_CAPTURE" for record in audited)
    if eligible and conflicts:
        return {"status": "INSUFFICIENT_DATA", "reason": "CONFLICTING_RECORDED_LABEL_VERSIONS", "usable_at": None,
                "horizon_end": eligible[0]["horizon_end"], "observation_hash": None,
                "publisher_authenticity_verified": False, **versions}
    if eligible:
        return {**min(eligible, key=lambda record: (record["usable_at"], record["observation_hash"])), **versions}
    return {**audited[0], **versions}
