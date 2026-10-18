"""Explicit immutable local readiness observations, never Windows acceptance.

The reader's platform does not establish where an old saved run executed.
This recorder performs no production run, registration, label resolution,
network access, cache initialization or activation. It has no acceptance flag
that a caller can set. A future same-process production-run recorder is separate.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import closing
from datetime import datetime, timezone
import hashlib
from itertools import islice
import json
import os
from pathlib import Path
import platform
import re
import sqlite3
import subprocess
import sys
import tempfile
import time

from market_checker_app.prediction_contract import canonical_hash
from market_checker_app.services.captured_market_data_provider import CapturedMarketDataProvider
from market_checker_app.services.yahoo_price_provenance_service import _access_value
from market_checker_app.utils.ticker_universe import load_canonical_tickers


SCHEMA_VERSION = "user_windows_readiness_capture_v1"
SCOPE = "READINESS_CAPTURE"
MAX_SUBJECTS = 687
MAX_SAMPLE_ROWS = 64
MAX_RECEIPT_BYTES = 8_000_000
MAX_DATABASE_BYTES = 256_000_000
MAX_SOURCE_BYTES = 8_000_000
MAX_SQL_OPERATIONS = 200_000
SOURCE_ROOT = Path(__file__).resolve().parents[2]
SOURCE_FILES = (
    "market_checker_app/services/user_windows_readiness_capture.py",
    "market_checker_app/services/captured_market_data_provider.py",
    "market_checker_app/services/captured_market_input_acquisition.py",
    "market_checker_app/services/prospective_specialist_history.py",
    "market_checker_app/services/ohlc_quality.py",
    "market_checker_app/services/us_equity_calendar_service.py",
    "market_checker_app/services/price_methodology.py",
    "market_checker_app/prediction_contract.py",
    "market_checker_app/release_manifest.py",
    "market_checker_app/config.py",
    "market_checker_app/data/market_checker_687_tickers.csv",
)
_TABLE_COLUMNS = {
    "runs": ("run_id", "started_at", "finished_at", "watchlist_size", "processed_symbols", "warnings_count", "errors_count"),
    "prediction_snapshots": ("snapshot_id", "run_id", "ticker", "as_of", "observed_at", "label_status", "snapshot_hash", "created_at"),
    "signal_history": ("run_id", "ticker", "updated_at"),
}
_CI_NAMES = ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "TF_BUILD", "CIRCLECI", "BUILDKITE")


def physical_clock():
    return datetime.now(timezone.utc)


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("AWARE_PHYSICAL_READINESS_CLOCK_REQUIRED")
    return value.astimezone(timezone.utc)


def _guard(path):
    path = Path(path)
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise ValueError("READINESS_SYMLINK_OR_JUNCTION_REFUSED")


def _encoded(record):
    if _access_value(record):
        raise ValueError("WITHHELD_UNSAFE_WHOLE_READINESS_RECEIPT")
    try:
        raw = (json.dumps(record, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise ValueError("INVALID_READINESS_RECEIPT_BODY") from None
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError("READINESS_RECEIPT_BYTE_LIMIT")
    return raw


def _file_pin(path, *, maximum):
    path = Path(path); _guard(path)
    if not path.exists():
        return {"exists": False, "bytes": None, "sha256": None}
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError("READINESS_INPUT_FILE_BOUND_OR_TYPE")
    digest = hashlib.sha256(); count = 0
    with path.open("rb") as handle:
        while block := handle.read(1_000_000):
            count += len(block)
            if count > maximum:
                raise ValueError("READINESS_INPUT_FILE_BOUND_OR_TYPE")
            digest.update(block)
    return {"exists": True, "bytes": count, "sha256": digest.hexdigest()}


def _runtime_facts():
    # Export indicators only. Never archive environment values, username,
    # hostname, interpreter path or a complete environment/platform dump.
    flags = {name: str(os.environ.get(name, "")).strip().casefold() in {"true", "1"} for name in _CI_NAMES}
    return {"os_system": platform.system(), "os_release": platform.release(), "os_version": platform.version(),
        "machine": platform.machine(), "os_name": os.name, "sys_platform": sys.platform,
        "python_implementation": platform.python_implementation(), "python_version": platform.python_version(),
        "hosted_ci_indicators": flags, "ci_detected": any(flags.values())}


def _validate_runtime(record):
    expected = {"os_system", "os_release", "os_version", "machine", "os_name", "sys_platform",
        "python_implementation", "python_version", "hosted_ci_indicators", "ci_detected"}
    if not isinstance(record, dict) or set(record) != expected:
        raise ValueError("INVALID_READINESS_RUNTIME_PROBE")
    if any(not isinstance(record[key], str) or len(record[key]) > 512 for key in expected - {"hosted_ci_indicators", "ci_detected"}):
        raise ValueError("INVALID_READINESS_RUNTIME_PROBE")
    flags = record["hosted_ci_indicators"]
    if (not isinstance(flags, dict) or set(flags) != set(_CI_NAMES) or any(type(value) is not bool for value in flags.values())
            or type(record["ci_detected"]) is not bool or record["ci_detected"] != any(flags.values())):
        raise ValueError("INVALID_READINESS_RUNTIME_PROBE")
    _encoded(record)
    return record


def _code_observation():
    files = {name: _file_pin(SOURCE_ROOT / name, maximum=MAX_SOURCE_BYTES) for name in SOURCE_FILES}
    if not all(row["exists"] for row in files.values()):
        raise ValueError("READINESS_PRODUCER_SOURCE_UNAVAILABLE")
    result = {"source_files": files, "source_manifest_sha256": canonical_hash(files),
        "git_head": None, "tracked_worktree_clean": None, "pinned_sources_match_HEAD": False}
    try:
        revision = subprocess.run(["git", "-C", str(SOURCE_ROOT), "rev-parse", "HEAD"],
            capture_output=True, timeout=3, check=True).stdout.decode().strip()
        if not re.fullmatch(r"[a-f0-9]{40,64}", revision):
            raise ValueError("INVALID_GIT_IDENTITY")
        dirty = subprocess.run(["git", "-C", str(SOURCE_ROOT), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, timeout=3, check=True).stdout
        tree = subprocess.run(["git", "-C", str(SOURCE_ROOT), "ls-tree", "-r", "-z", "HEAD", "--", *SOURCE_FILES],
            capture_output=True, timeout=3, check=True).stdout
        expected = {}
        for entry in tree.split(b"\0"):
            if entry:
                header, name = entry.split(b"\t", 1)
                expected[name.decode()] = header.decode().split()[2]
        matches = len(expected) == len(SOURCE_FILES)
        for name in SOURCE_FILES:
            body = (SOURCE_ROOT / name).read_bytes()
            matches = matches and hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest() == expected.get(name)
        result.update(git_head=revision, tracked_worktree_clean=not dirty, pinned_sources_match_HEAD=matches)
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        result["git_identity_status"] = "UNAVAILABLE"
    result["git_head_is_exact_pinned_source_identity"] = result["tracked_worktree_clean"] is True and result["pinned_sources_match_HEAD"]
    return result


def _database_file_state(path):
    """Pin absence as well as bytes; no SQLite connection or journal replay."""
    return {"file": _file_pin(path, maximum=MAX_DATABASE_BYTES),
        "journals": {suffix: _file_pin(Path(str(path) + suffix), maximum=MAX_DATABASE_BYTES)
            for suffix in ("-wal", "-shm", "-journal")}}


def _market_binding(report):
    # Physical read timestamps can advance. The selected manifest, current
    # completed-session requirement, sources and their usable/missing facts
    # must remain the ones represented in the immutable receipt.
    return {key: report.get(key) for key in ("status", "reason", "manifest_sha256", "latest_completed_session",
        "requested_count", "usable_count", "missing_count", "fixture_only")} | {
        "rows": [{key: row.get(key) for key in ("ticker", "status", "reason", "source", "provenance")}
            for row in report.get("rows", [])]}


def _database_observation(path, subjects, *, maximum):
    result = {"status": "ABSENT", "file": {"exists": False, "bytes": None, "sha256": None},
        "journals": {suffix: {"exists": False, "bytes": None, "sha256": None} for suffix in ("-wal", "-shm", "-journal")},
        "mode": "ro", "SQL_opened": False, "query_bound_reached": False,
        "reader_platform_does_not_establish_saved_run_platform": True,
        "saved_run_runtime_platform": "UNKNOWN", "stored_row_authenticity_independently_verified": False,
        "tables": {name: {"present": False, "sampled_rows": 0, "limit_reached": False, "rows": []} for name in _TABLE_COLUMNS}}
    if path is None:
        return result
    path = Path(path); before_state = _database_file_state(path)
    before = before_state["file"]; result.update(before_state)
    if any(pin["exists"] for pin in before_state["journals"].values()):
        result.update(status="WAIT_ACTIVE_DATABASE_JOURNAL", reason="READ_ONLY_CAPTURE_REQUIRES_QUIESCENT_DATABASE")
        return result
    if not before["exists"]:
        return result
    steps = 0
    def progress():
        nonlocal steps
        steps += 1
        return int(steps * 1000 > MAX_SQL_OPERATIONS)
    def authorize(action, first, second, database, trigger):
        if action == sqlite3.SQLITE_SELECT or action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and first in {*_TABLE_COLUMNS, "sqlite_master"}:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)) as connection:
            result["SQL_opened"] = True
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 2_000_000)
            connection.set_progress_handler(progress, 1000)
            connection.set_authorizer(authorize)
            for table, columns in _TABLE_COLUMNS.items():
                observed = result["tables"][table]
                observed["present"] = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None
                if not observed["present"]:
                    continue
                where = "" if table == "runs" else " WHERE ticker IN (" + ",".join("?" for _ in subjects) + ")"
                params = [] if table == "runs" else list(subjects)
                # Fixed table/column names; no source payload or private JSON is
                # selected. Global run metadata alone proves no subject coverage.
                cursor = connection.execute("SELECT " + ",".join(columns) + " FROM " + table + where + " LIMIT ?", [*params, maximum + 1])
                for index, row in enumerate(cursor):
                    if index == maximum:
                        observed["limit_reached"] = True
                        break
                    item = dict(zip(columns, row))
                    if _access_value(item):
                        raise ValueError("WITHHELD_UNSAFE_SELECTED_DATABASE_METADATA")
                    if len(_encoded(item)) > 8192:
                        raise ValueError("SELECTED_DATABASE_METADATA_BYTE_LIMIT")
                    observed["rows"].append(item)
                observed["sampled_rows"] = len(observed["rows"])
                observed["scope"] = "BOUNDED_GLOBAL_RUN_METADATA_NO_PLATFORM_PROOF" if table == "runs" else "EXPLICIT_SUBJECT_METADATA_ONLY"
        result["status"] = "READ_ONLY_OBSERVED"
    except sqlite3.Error:
        result.update(status="WAIT_DATABASE_READ", reason="BOUNDED_READ_ONLY_DATABASE_QUERY_FAILED", query_bound_reached=steps * 1000 > MAX_SQL_OPERATIONS)
    except (ValueError, TypeError):
        result.update(status="WITHHELD_DATABASE_METADATA", reason="UNSAFE_OR_UNBOUNDED_SELECTED_DATABASE_METADATA")
        for table in result["tables"].values():
            table.update(rows=[], sampled_rows=None)
    after_state = _database_file_state(path); after = after_state["file"]
    result["file_after"] = after
    result["journals_after"] = after_state["journals"]
    if before_state != after_state:
        result.update(status="WAIT_DATABASE_CHANGED", reason="DATABASE_CHANGED_DURING_READINESS_CAPTURE")
    result["raw_file_unchanged_during_observation"] = before == after
    return result


def load_user_windows_readiness_receipt(path):
    """Validate a bounded immutable readiness receipt; never promote its scope."""
    path = Path(path); _guard(path)
    with path.open("rb") as handle:
        raw = handle.read(MAX_RECEIPT_BYTES + 1)
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError("READINESS_RECEIPT_BYTE_LIMIT")
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("DUPLICATE_READINESS_JSON_KEY")
            result[key] = value
        return result
    def finite_constant(_):
        raise ValueError("NONFINITE_READINESS_JSON_VALUE")
    try:
        record = json.loads(raw, object_pairs_hook=unique_object, parse_constant=finite_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("INVALID_READINESS_RECEIPT_BODY") from None
    if (not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION or record.get("scope") != SCOPE
            or record.get("capture_id") != canonical_hash({key: value for key, value in record.items() if key != "capture_id"})
            or record.get("user_windows_end_to_end_verified") is not False
            or record.get("same_process_production_run_receipt") != "ABSENT"
            or any(record.get(key) is not False for key in ("strict_pit_eligible", "historical_OOS_verified", "full_specialist_acceptance"))):
        raise ValueError("READINESS_RECEIPT_CANNOT_ASSERT_ACCEPTANCE")
    _encoded(record)
    return record


class UserWindowsReadinessCapture:
    """Default-off native readiness recorder. Injected probes are fixture-only."""
    def __init__(self, *, enabled=False, observation_clock: Callable | None = None,
                 monotonic_clock: Callable | None = None, runtime_probe: Callable | None = None):
        if type(enabled) is not bool:
            raise ValueError("EXPLICIT_READINESS_ENABLE_REQUIRED")
        self.enabled = enabled
        self.fixture_only = any(value is not None for value in (observation_clock, monotonic_clock, runtime_probe))
        self.wall = observation_clock or physical_clock
        self.monotonic = monotonic_clock or time.monotonic_ns
        self.runtime = runtime_probe or _runtime_facts
        self._high_water = None; self._monotonic_high_water = None

    def _clock(self):
        wall = _utc(self.wall()); monotonic = self.monotonic()
        if type(monotonic) is not int or monotonic < 0:
            raise ValueError("INVALID_READINESS_MONOTONIC_CLOCK")
        if ((self._high_water is not None and wall < self._high_water)
                or (self._monotonic_high_water is not None and monotonic < self._monotonic_high_water)):
            raise ValueError("READINESS_PHYSICAL_CLOCK_ROLLBACK")
        self._high_water = wall; self._monotonic_high_water = monotonic
        return wall, monotonic

    def capture(self, *, output_path, tickers: Iterable[str], db_path=None,
                market_manifest_path=None, market_archive_root=None, maximum_rows=MAX_SAMPLE_ROWS):
        if not self.enabled:
            return {"status": "DISABLED", "scope": SCOPE, "receipt_created": False,
                "user_windows_end_to_end_verified": False, "network_requests": 0, "DB_writes": 0}
        subjects = list(islice(iter(tickers), MAX_SUBJECTS + 1))
        canonical_subjects = set(load_canonical_tickers())
        if (not 1 <= len(subjects) <= MAX_SUBJECTS or any(not isinstance(ticker, str) for ticker in subjects)
                or len(set(subjects)) != len(subjects) or any(ticker not in canonical_subjects for ticker in subjects)):
            raise ValueError("INVALID_EXPLICIT_CANONICAL_READINESS_SUBJECTS")
        if type(maximum_rows) is not int or not 1 <= maximum_rows <= MAX_SAMPLE_ROWS:
            raise ValueError("INVALID_READINESS_DATABASE_ROW_BOUND")
        if (market_manifest_path is None) != (market_archive_root is None):
            raise ValueError("EXPLICIT_MARKET_MANIFEST_AND_ROOT_REQUIRED_TOGETHER")
        output = Path(output_path); _guard(output)
        if (output.suffix != ".json" or output.name in {"market_manifest.json", "prospective_source_manifest.json"}
                or output.name.startswith(("yahoo_price_", "forecast_", "label_", "prediction_label_"))):
            raise ValueError("EXPLICIT_SEPARATE_READINESS_JSON_OUTPUT_REQUIRED")
        if output.exists():
            raise FileExistsError("IMMUTABLE_READINESS_RECEIPT_ALREADY_EXISTS")
        output = output.resolve()
        for value in (db_path, market_manifest_path):
            if value is not None:
                _guard(value)
                if output.is_relative_to(Path(value).resolve()):
                    raise ValueError("READINESS_OUTPUT_COLLIDES_WITH_INPUT")
        if market_archive_root is not None and output.is_relative_to(Path(market_archive_root).resolve()):
            raise ValueError("READINESS_OUTPUT_MUST_BE_SEPARATE_FROM_MARKET_INPUT_ROOT")
        started, start_ns = self._clock(); runtime = _validate_runtime(self.runtime())
        code = _code_observation(); self._clock()
        manifest_before = None
        if market_manifest_path is None:
            market = {"status": "WAIT_DATA", "reason": "NO_EXPLICIT_CAPTURED_MARKET_MANIFEST", "usable_count": 0,
                "requested_subjects": subjects, "source_http_requests": 0, "strict_pit_eligible": False}
            provider = None
        else:
            _guard(market_archive_root)
            manifest_before = _file_pin(market_manifest_path, maximum=MAX_SOURCE_BYTES)
            provider = CapturedMarketDataProvider(manifest_path=Path(market_manifest_path), archive_root=Path(market_archive_root),
                observation_clock=(lambda: self._clock()[0]) if self.fixture_only else None)
            market = provider.readiness(subjects, include_benchmarks=True)
        self._clock()
        database = _database_observation(db_path, subjects, maximum=maximum_rows); self._clock()
        if provider is not None:
            final_market = provider.revalidate_for_persistence(subjects, expected_readiness=market)
            if _file_pin(market_manifest_path, maximum=MAX_SOURCE_BYTES) != manifest_before:
                raise ValueError("MARKET_MANIFEST_CHANGED_DURING_READINESS_CAPTURE")
        else:
            final_market = market
        code_after = _code_observation()
        if code_after["source_files"] != code["source_files"]:
            raise ValueError("PRODUCER_CODE_CHANGED_DURING_READINESS_CAPTURE")
        database_state = {"file": database.get("file_after", database["file"]),
            "journals": database.get("journals_after", database["journals"])}
        if db_path is not None and _database_file_state(db_path) != database_state:
            raise ValueError("DATABASE_CHANGED_BEFORE_READINESS_RECEIPT")
        ended, end_ns = self._clock()
        marked_fixture = self.fixture_only or final_market.get("fixture_only") is True or any(
            row.get("reason") == "SYNTHETIC_CAPTURE_NOT_PRODUCTION_INPUT" for row in final_market.get("rows", []))
        reasons = []
        if final_market["status"] != "READY": reasons.append("CAPTURED_MARKET_INPUTS_WAIT_DATA")
        if database["status"] != "READ_ONLY_OBSERVED": reasons.append("SAVED_DATABASE_UNAVAILABLE_OR_NOT_QUIESCENT")
        for table in ("runs", "prediction_snapshots"):
            if not database["tables"][table]["sampled_rows"]: reasons.append("NO_OBSERVED_" + table.upper())
        if not code["git_head_is_exact_pinned_source_identity"]: reasons.append("EXACT_COMMITTED_CODE_IDENTITY_UNAVAILABLE")
        status = ("FIXTURE_ONLY" if marked_fixture else "UNSUPPORTED_PLATFORM" if runtime["os_system"] != "Windows"
            else "HOSTED_CI_NOT_USER_WINDOWS" if runtime["ci_detected"] else "WAIT" if reasons else "READINESS_CAPTURED")
        record = {"schema_version": SCHEMA_VERSION, "scope": SCOPE, "status": status,
            "readiness_status": "WAIT" if reasons else "READY_INPUTS_ONLY", "readiness_reasons": reasons,
            "evidence_origin": "DETERMINISTIC_TEST_FIXTURE" if marked_fixture else "PHYSICAL_PROCESS_READINESS_CAPTURE",
            "fixture_only": marked_fixture, "clocks": {"started_at": started.isoformat(), "observed_at": ended.isoformat(),
                "monotonic_started_ns": start_ns, "monotonic_ended_ns": end_ns, "elapsed_ns": end_ns - start_ns,
                "injected_clock_or_runtime_probe": self.fixture_only}, "runtime": runtime, "subjects": subjects,
            "code": code, "market_manifest": manifest_before, "market_readiness": market, "market_readiness_final": final_market,
            "database": database, "same_process_production_run_receipt": "ABSENT", "user_windows_end_to_end_verified": False,
            "strict_pit_eligible": False, "historical_OOS_verified": False, "full_specialist_acceptance": False,
            "publisher_revision_timing_status": "UNKNOWN", "network_requests": 0, "DB_writes": 0,
            "forecasts_created": 0, "registrations_created": 0, "labels_created": 0, "activation_allowed": False,
            "scores_changed": False, "fitter_changed": False}
        record["publication_revalidation"] = "AFTER_TEMPORARY_FSYNC_BEFORE_EXCLUSIVE_LINK"
        record["capture_id"] = canonical_hash(record); raw = _encoded(record)
        _guard(output); output.parent.mkdir(parents=True, exist_ok=True); _guard(output)
        parent_identity = (output.parent.stat().st_dev, output.parent.stat().st_ino)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".user_windows_readiness_", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name); handle.write(raw); handle.flush(); os.fsync(handle.fileno())
            # Encoding, temporary writes and fsync are observable callback/IO
            # boundaries. Recheck AFTER them, immediately before publication.
            # No new observation replaces the sources or clocks in the body.
            if provider is not None:
                publication_market = provider.revalidate_for_persistence(subjects, expected_readiness=market)
                if (_file_pin(market_manifest_path, maximum=MAX_SOURCE_BYTES) != manifest_before
                        or _market_binding(publication_market) != _market_binding(final_market)):
                    raise ValueError("CAPTURED_MARKET_INPUT_CHANGED_BEFORE_READINESS_PUBLICATION")
            if _code_observation()["source_files"] != code["source_files"]:
                raise ValueError("PRODUCER_CODE_CHANGED_BEFORE_READINESS_PUBLICATION")
            if db_path is not None and _database_file_state(db_path) != database_state:
                raise ValueError("DATABASE_CHANGED_BEFORE_READINESS_PUBLICATION")
            _guard(output)
            if parent_identity != (output.parent.stat().st_dev, output.parent.stat().st_ino):
                raise ValueError("READINESS_OUTPUT_PARENT_CHANGED")
            self._clock()
            try: os.link(temporary, output)
            except FileExistsError: raise FileExistsError("IMMUTABLE_READINESS_RECEIPT_ALREADY_EXISTS") from None
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)
        return {"status": status, "scope": SCOPE, "receipt_created": True, "receipt_path": str(output),
            "receipt_sha256": hashlib.sha256(raw).hexdigest(), "receipt": record}
