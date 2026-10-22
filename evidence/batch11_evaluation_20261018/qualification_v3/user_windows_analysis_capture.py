"""Explicit same-process captured-input analysis observations, never inventory acceptance.

Native execution requires Windows outside hosted CI. Injected execution controls
are fixture-only and require an explicit test pipeline factory. A loaded receipt
is content to review; loading JSON never re-establishes process/platform evidence.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import closing, contextmanager, ExitStack
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib
from itertools import islice
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
import tempfile
import time
from unittest.mock import patch
from uuid import uuid4

import pandas as pd

from market_checker_app.config import AppConfig
from market_checker_app.prediction_contract import canonical_hash
from market_checker_app.release_manifest import config_hash
from market_checker_app.services.captured_market_data_provider import CapturedMarketDataProvider
from market_checker_app.services.user_windows_readiness_capture import (
    _database_file_state, _file_pin, _guard, _market_binding, _runtime_facts, _utc, _validate_runtime,
)
from market_checker_app.services.yahoo_price_provenance_service import _access_value
from market_checker_app.storage.sqlite_store import SQLiteStore
from market_checker_app.utils.ticker_universe import load_canonical_tickers
from market_checker_app.utils.dates import to_iso


SCHEMA_VERSION = "user_windows_analysis_capture_v1"
SCOPE = "SAME_PROCESS_SELECTED_ANALYSIS_RUN"
SOURCE_ROOT = Path(__file__).resolve().parents[2]
PRODUCER_PATH = "market_checker_app/services/user_windows_analysis_capture.py"
MAX_SUBJECTS = 687
MAX_NEW_RUNS = 4
MAX_RECEIPT_BYTES = 8_000_000
MAX_SQL_OPERATIONS = 500_000
MAX_SOURCE_FILES = 512
MAX_SOURCE_TOTAL_BYTES = 32_000_000
RUN_COLUMNS = ("run_id", "started_at", "finished_at", "watchlist_size", "processed_symbols", "warnings_count", "errors_count", "excel_path")
SIGNAL_COLUMNS = tuple(value.strip() for value in re.search(
    r"INSERT INTO signal_history\((.*?)\)\s*VALUES", SQLiteStore.SIGNAL_HISTORY_INSERT, re.S).group(1).split(","))
_OPERATIONAL_CONFIG = {"output_dir", "sqlite_path", "captured_market_manifest", "captured_market_archive_root",
    "prospective_history_enabled", "prospective_audit_archive_root"}


def physical_clock():
    return datetime.now(timezone.utc)


def _encode(value):
    if _access_value(value):
        raise ValueError("WITHHELD_UNSAFE_WHOLE_ANALYSIS_CAPTURE")
    try:
        raw = (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise ValueError("INVALID_ANALYSIS_CAPTURE_BODY") from None
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError("ANALYSIS_CAPTURE_BYTE_LIMIT")
    return raw


def _single_link(path):
    path = Path(path); _guard(path)
    if path.is_file() and path.stat().st_nlink != 1:
        raise ValueError("ANALYSIS_CAPTURE_HARDLINK_REFUSED")


def _code_pins():
    """Pin bounded tracked production bytes, plus this initially new producer."""
    revision = None; entries = {}
    try:
        revision = subprocess.run(["git", "-C", str(SOURCE_ROOT), "rev-parse", "HEAD"],
            check=True, capture_output=True, timeout=3).stdout.decode().strip()
        if re.fullmatch(r"[a-f0-9]{40}", revision) is None:
            raise ValueError("UNKNOWN_SOURCE_REVISION")
        raw = subprocess.run(["git", "-C", str(SOURCE_ROOT), "ls-tree", "-r", "-z", "HEAD", "--", "market_checker_app"],
            check=True, capture_output=True, timeout=3).stdout
        for item in raw.split(b"\0"):
            if item:
                header, name = item.split(b"\t", 1)
                mode, kind, blob = header.decode().split()
                relative = name.decode()
                if kind != "blob" or mode not in {"100644", "100755"} or ".." in PurePosixPath(relative).parts:
                    raise ValueError("UNSUPPORTED_PRODUCTION_SOURCE_ENTRY")
                entries[relative] = blob
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        # A packaged copy can still produce an unqualified observation with
        # explicit critical source hashes. It cannot invent a commit identity.
        revision = None
        entries = {path: None for path in ("market_checker_app/services/pipeline_service.py",
            "market_checker_app/storage/sqlite_store.py", "market_checker_app/config.py",
            "market_checker_app/services/captured_market_data_provider.py",
            "market_checker_app/services/captured_market_input_acquisition.py",
            "market_checker_app/data/market_checker_687_tickers.csv")}
    entries.setdefault(PRODUCER_PATH, None)
    if len(entries) > MAX_SOURCE_FILES:
        raise ValueError("PRODUCTION_SOURCE_COUNT_LIMIT")
    files = {}; total = 0; matches = revision is not None
    for name, blob in sorted(entries.items()):
        path = SOURCE_ROOT / name; _single_link(path)
        pin = _file_pin(path, maximum=8_000_000)
        if not pin["exists"]:
            raise ValueError("PRODUCTION_SOURCE_UNAVAILABLE")
        total += pin["bytes"]
        if total > MAX_SOURCE_TOTAL_BYTES:
            raise ValueError("PRODUCTION_SOURCE_BYTE_LIMIT")
        body = path.read_bytes()
        matches = matches and hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest() == blob
        files[name] = pin
    return {"git_head": revision, "source_files": files, "source_manifest_sha256": canonical_hash(files),
        "exact_pinned_sources_match_HEAD": matches, "code_identity_from_environment": False}


def _destination(db, output, archive):
    for path in (db, output):
        _single_link(path)
    if db.suffix != ".db" or output.suffix != ".json":
        raise ValueError("EXPLICIT_ANALYSIS_DB_AND_RECEIPT_PATH_REQUIRED")
    if output.exists():
        raise FileExistsError("IMMUTABLE_ANALYSIS_RECEIPT_ALREADY_EXISTS")
    db, output, archive = db.resolve(), output.resolve(), archive.resolve()
    if db == output or output.is_relative_to(db) or db.is_relative_to(archive) or output.is_relative_to(archive):
        raise ValueError("ANALYSIS_DESTINATION_COLLIDES_WITH_INPUT")
    if any(db.is_relative_to(SOURCE_ROOT / name) for name in ("market_checker_app", "tests", "evidence", ".git")):
        raise ValueError("ANALYSIS_DATABASE_CANNOT_OVERWRITE_VERSIONED_INPUTS")
    return db, output


def _input_pins(provider, readiness):
    root = provider.archive_root; _single_link(root)
    pins = {"manifest": _file_pin(provider.manifest_path, maximum=2_000_000), "selected_files": {}}
    _single_link(provider.manifest_path)
    for row in readiness.get("rows", []):
        source = row.get("source") or {}
        candidates = [(source.get("path"), source.get("file_bytes"), source.get("file_sha256"))]
        parent = (row.get("provenance") or {}).get("original_chart_parent", {}).get("raw_http_parent")
        if parent:
            candidates.append((parent["raw_path"], parent["body_bytes"], parent["body_sha256"]))
        for relative, size, digest in candidates:
            if relative is None:
                continue
            if (not isinstance(relative, str) or re.fullmatch(r"[A-Za-z0-9_./-]{1,512}", relative) is None
                    or PurePosixPath(relative).is_absolute() or any(part in {"", ".", ".."} for part in relative.split("/"))):
                raise ValueError("INVALID_SELECTED_ANALYSIS_SOURCE_PATH")
            path = root.joinpath(*PurePosixPath(relative).parts); _single_link(path)
            if not path.resolve().is_relative_to(root):
                raise ValueError("ANALYSIS_SOURCE_OUTSIDE_ARCHIVE_ROOT")
            pin = _file_pin(path, maximum=16_000_000)
            if pin != {"exists": True, "bytes": size, "sha256": digest}:
                raise ValueError("ANALYSIS_SOURCE_PIN_MISMATCH")
            pins["selected_files"][relative] = pin
    return pins


@contextmanager
def _offline_only(counter):
    def refused(*args, **kwargs):
        counter["blocked_network_attempts"] += 1
        raise RuntimeError("OFFLINE_ANALYSIS_NETWORK_REFUSED")
    with ExitStack() as stack:
        for name in ("socket.socket.connect", "socket.socket.connect_ex", "socket.create_connection",
                "socket.getaddrinfo", "urllib.request.urlopen", "requests.sessions.Session.request"):
            stack.enter_context(patch(name, side_effect=refused))
        try:
            curl = importlib.import_module("curl_cffi.requests")
        except ImportError:
            curl = None
        if curl is not None:
            stack.enter_context(patch.object(curl.Session, "request", side_effect=refused))
        yield


class _ForbiddenClient:
    def __getattr__(self, name):
        raise RuntimeError("OFFLINE_ANALYSIS_RSS_OR_MT5_REFUSED")


def _database_state(path):
    _single_link(path)
    for suffix in ("-journal", "-wal", "-shm"):
        _single_link(Path(str(path) + suffix))
    return _database_file_state(path)


def _read_runs(path, *, after_id=0, expected_ids=(), subjects=(), before_only=False):
    """Bounded closed mode=ro reads; never opens a journal-bearing database."""
    state = _database_state(path)
    report = {"status": "ABSENT", "SQL_opened": False, "file_state": state,
        "new_run_ids": [], "new_run_count": 0, "run_count_complete": True,
        "signal_count": 0, "signal_count_complete": True, "runs": [], "signals": [], "rows_withheld": False,
        "latest_run_id": 0, "saved_run_runtime_is_not_inferred_from_reader": True}
    if any(value["exists"] for value in state["journals"].values()):
        report.update(status="WAIT_ACTIVE_DATABASE_JOURNAL", new_run_count=None, signal_count=None)
        return report
    if not state["file"]["exists"]:
        return report
    steps = 0
    def progress():
        nonlocal steps
        steps += 1000
        return int(steps > MAX_SQL_OPERATIONS)
    def authorizer(action, first, second, database, trigger):
        if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION}:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and first in {"sqlite_master", "runs", "signal_history"}:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)) as connection:
            report["SQL_opened"] = True
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
            connection.set_progress_handler(progress, 1000)
            connection.set_authorizer(authorizer)
            present = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'").fetchone()
            if present:
                report["latest_run_id"] = int(connection.execute("SELECT COALESCE(MAX(run_id),0) FROM runs").fetchone()[0])
            if before_only or not present:
                report["status"] = "READ_ONLY_OBSERVED"
            else:
                total = int(connection.execute("SELECT COUNT(*) FROM runs WHERE run_id>?", (after_id,)).fetchone()[0])
                ids = [int(row[0]) for row in connection.execute("SELECT run_id FROM runs WHERE run_id>? ORDER BY run_id LIMIT ?", (after_id, MAX_NEW_RUNS + 1))]
                report.update(new_run_count=total, run_count_complete=total <= MAX_NEW_RUNS,
                    new_run_ids=ids[:MAX_NEW_RUNS])
                ids = sorted(set(ids[:MAX_NEW_RUNS]) | {value for value in expected_ids if type(value) is int and value > after_id})[:MAX_NEW_RUNS]
                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    raw_runs = list(connection.execute("SELECT " + ",".join(RUN_COLUMNS) + " FROM runs WHERE run_id IN (" + placeholders + ") ORDER BY run_id", ids))
                    count = int(connection.execute("SELECT COUNT(*) FROM signal_history WHERE run_id IN (" + placeholders + ")", ids).fetchone()[0])
                    report["signal_count"] = count
                    limit = MAX_SUBJECTS * MAX_NEW_RUNS
                    report["signal_count_complete"] = count <= limit
                    rows = connection.execute("SELECT id," + ",".join(SIGNAL_COLUMNS) + " FROM signal_history WHERE run_id IN (" + placeholders + ") ORDER BY id LIMIT ?", [*ids, limit])
                    result = {"runs": [dict(zip(RUN_COLUMNS, row)) for row in raw_runs], "signals": []}
                    for row in rows:
                        item = dict(zip(("id", *SIGNAL_COLUMNS), row))
                        if _access_value(item) or len(_encode(item)) > 32_000:
                            raise ValueError("WITHHELD_WHOLE_ANALYSIS_RESULT_ROWS")
                        result["signals"].append(item)
                    _encode(result)
                    report.update(result)
                report["status"] = "READ_ONLY_OBSERVED"
    except sqlite3.Error:
        report.update(status="WAIT_DATABASE_READ", new_run_count=None, signal_count=None, runs=[], signals=[])
    except (ValueError, TypeError, OverflowError):
        report.update(status="WITHHELD_RESULT_ROWS", runs=[], signals=[], rows_withheld=True)
    report["file_state_after"] = _database_state(path)
    if report["file_state_after"] != state:
        report["status"] = "WAIT_DATABASE_CHANGED"
    return report


def _safe_read_runs(path, **kwargs):
    try:
        return _read_runs(path, **kwargs)
    except (ValueError, OSError, TypeError):
        return {"status": "WAIT_UNSAFE_OR_CHANGED_DATABASE", "SQL_opened": False, "file_state": None,
            "new_run_ids": [], "new_run_count": None, "run_count_complete": False,
            "signal_count": None, "signal_count_complete": False, "runs": [], "signals": [],
            "rows_withheld": True, "latest_run_id": None}


def _seal(record):
    record = dict(record)
    record["capture_id"] = canonical_hash({key: value for key, value in record.items() if key != "capture_id"})
    return record


def load_user_windows_analysis_receipt(path):
    """Content-integrity validation only. Stored claims are not re-execution."""
    path = Path(path); _single_link(path)
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("DUPLICATE_ANALYSIS_RECEIPT_KEY")
            out[key] = value
        return out
    def finite(_):
        raise ValueError("NONFINITE_ANALYSIS_RECEIPT")
    with path.open("rb") as handle:
        raw = handle.read(MAX_RECEIPT_BYTES + 1)
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError("ANALYSIS_CAPTURE_BYTE_LIMIT")
    try:
        record = json.loads(raw, object_pairs_hook=unique, parse_constant=finite)
        if (not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION or record.get("scope") != SCOPE
                or record != _seal(record) or any(record.get(key) is not False for key in
                    ("full_specialist_acceptance", "strict_pit_eligible", "historical_OOS_verified", "user_windows_end_to_end_verified"))):
            raise ValueError("INVALID_ANALYSIS_RECEIPT")
        _encode(record)
    except (TypeError, ValueError, RecursionError, UnicodeError):
        raise ValueError("INVALID_ANALYSIS_RECEIPT") from None
    return {"record": record, "content_hash_verified": True, "loaded_json_is_not_execution_proof": True,
        "native_windows_selected_analysis_verified": False, "runtime_execution_reverified": False}


class UserWindowsAnalysisCapture:
    def __init__(self, *, enabled=False, observation_clock: Callable | None = None,
                 monotonic_clock: Callable | None = None, runtime_probe: Callable | None = None,
                 pipeline_factory: Callable | None = None):
        if type(enabled) is not bool:
            raise ValueError("EXPLICIT_ANALYSIS_CAPTURE_ENABLE_REQUIRED")
        self.enabled = enabled
        self.fixture_only = any(value is not None for value in (observation_clock, monotonic_clock, runtime_probe, pipeline_factory))
        self.wall = observation_clock or physical_clock
        self.monotonic = monotonic_clock or time.monotonic_ns
        self.runtime = runtime_probe or _runtime_facts
        self.pipeline_factory = pipeline_factory
        self.high_wall = None; self.high_ns = None
        self.clock_failed = False; self.latest_clock = None

    def _clock(self):
        try:
            wall = _utc(self.wall()); monotonic = self.monotonic()
        except (ValueError, TypeError):
            self.clock_failed = True
            raise
        if type(monotonic) is not int or monotonic < 0:
            self.clock_failed = True
            raise ValueError("INVALID_ANALYSIS_MONOTONIC_CLOCK")
        self.latest_clock = {"observed_at": wall.isoformat(), "monotonic_ns": monotonic}
        if (self.high_wall is not None and wall < self.high_wall) or (self.high_ns is not None and monotonic < self.high_ns):
            self.clock_failed = True
            raise ValueError("ANALYSIS_PHYSICAL_CLOCK_ROLLBACK")
        self.high_wall = wall; self.high_ns = monotonic
        return dict(self.latest_clock)

    def _provider_clock(self):
        self._clock()
        return self.high_wall

    def capture(self, *, output_path, tickers: Iterable[str], db_path,
                market_manifest_path, market_archive_root, progress_callback=None):
        if not self.enabled:
            return {"status": "DISABLED", "scope": SCOPE, "receipt_created": False,
                "native_windows_selected_analysis_verified": False, "pipeline_invoked": False}
        subjects = list(islice(iter(tickers), MAX_SUBJECTS + 1))
        canonical = set(load_canonical_tickers())
        if (not 1 <= len(subjects) <= MAX_SUBJECTS or any(not isinstance(t, str) for t in subjects)
                or len(set(subjects)) != len(subjects) or any(t not in canonical for t in subjects)):
            raise ValueError("INVALID_EXPLICIT_CANONICAL_ANALYSIS_SUBJECTS")
        output, db, manifest, archive = map(Path, (output_path, db_path, market_manifest_path, market_archive_root))
        _single_link(output)
        if output.suffix != ".json" or output.exists():
            raise FileExistsError("EXPLICIT_NEW_IMMUTABLE_ANALYSIS_JSON_REQUIRED")
        # Platform refusal never reads, hashes or opens the selected DB/source.
        if output.absolute() in {db.absolute(), manifest.absolute()} or output.absolute().is_relative_to(archive.absolute()):
            raise ValueError("ANALYSIS_RECEIPT_COLLIDES_WITH_INPUT")
        started = self._clock(); runtime = _validate_runtime(self.runtime()); code = _code_pins()
        record = {"schema_version": SCHEMA_VERSION, "scope": SCOPE, "session_id": uuid4().hex,
            "status": "WAIT", "fixture_only": self.fixture_only,
            "evidence_origin": "EXPLICIT_SYNTHETIC_TEST_EXECUTION" if self.fixture_only else "SAME_PROCESS_NATIVE_OBSERVATION",
            "runtime": runtime, "subjects": subjects, "clocks": {"started": started}, "code": code,
            "pipeline_invoked": False, "pipeline_kind": "EXPLICIT_TEST_DOUBLE" if self.pipeline_factory else "PRODUCTION_PIPELINE_SERVICE",
            "native_windows_selected_analysis_verified": False, "user_windows_end_to_end_verified": False,
            "full_specialist_acceptance": False, "strict_pit_eligible": False, "historical_OOS_verified": False,
            "publisher_revision_timing_status": "UNKNOWN", "forecasts_created": 0, "registrations_created": 0,
            "labels_created": 0, "fitter_or_trading_invoked": False, "qualification_reasons": [],
            "market_preflight": None, "input_pins": None, "database_before": None, "database": None,
            "saved_run_ids_returned_by_store": [], "blocked_network_attempts": 0, "source_HTTP_requests": 0,
            "same_process_observation_is_not_full_inventory_acceptance": True,
            "publication_revalidation_phase": "AFTER_TEMPORARY_FSYNC_BEFORE_EXCLUSIVE_LINK"}
        reasons = record["qualification_reasons"]; provider = None; initial_inputs = None
        max_before = 0; committed = []; expected_payloads = {}; result = None
        windows = runtime["os_system"] == "Windows" and runtime["os_name"] == "nt" and runtime["sys_platform"] == "win32"
        eligible_runtime = windows and not runtime["ci_detected"]
        if not windows:
            record["status"] = "UNSUPPORTED_PLATFORM"; reasons.append("NATIVE_WINDOWS_REQUIRED")
        elif runtime["ci_detected"]:
            record["status"] = "HOSTED_CI_REFUSED"; reasons.append("HOSTED_CI_IS_NOT_USER_WINDOWS")
        elif self.fixture_only and self.pipeline_factory is None:
            record["status"] = "FIXTURE_CONTROLS_REFUSED"; reasons.append("INJECTED_CONTROLS_REQUIRE_EXPLICIT_TEST_PIPELINE")
        else:
            try:
                db, output = _destination(db, output, archive)
                _single_link(manifest); _guard(archive)
                provider = CapturedMarketDataProvider(manifest_path=manifest, archive_root=archive,
                    observation_clock=self._provider_clock if self.fixture_only else None)
                preflight = provider.readiness(subjects, include_benchmarks=True); record["market_preflight"] = preflight
                if preflight["status"] != "READY" or (preflight.get("fixture_only") is True and not self.fixture_only):
                    record["status"] = "WAIT_DATA"; reasons.append("COMPLETE_GENUINE_CAPTURED_MARKET_INPUTS_REQUIRED")
                else:
                    initial_inputs = _input_pins(provider, preflight); record["input_pins"] = initial_inputs
                    before = _read_runs(db, before_only=True); record["database_before"] = before
                    if before["status"] not in {"ABSENT", "READ_ONLY_OBSERVED"}:
                        record["status"] = "WAIT_DATABASE"; reasons.append(before["status"])
                    else:
                        max_before = before["latest_run_id"]
                        config = AppConfig(output_dir=db.parent, sqlite_path=db,
                            captured_market_manifest=manifest, captured_market_archive_root=archive)
                        default = asdict(AppConfig()); effective = asdict(config)
                        model_defaults_match = {k: v for k, v in effective.items() if k not in _OPERATIONAL_CONFIG} == {k: v for k, v in default.items() if k not in _OPERATIONAL_CONFIG}
                        record["configuration"] = {"default_config_hash": config_hash(AppConfig()), "effective_config_hash": config_hash(config),
                            "model_settings_match_defaults": model_defaults_match,
                            "runtime_flags": {"rss_sources": [], "rss_enabled": False, "mt5_enabled": False,
                                "yahoo_metadata_enabled": False, "yahoo_only_mode": False, "save_history": True,
                                "agent_stage1_enabled": config.agent_stage1_enabled, "agent_shadow_mode": config.agent_shadow_mode,
                                "prospective_history_enabled": False, "prospective_audit_archive_root": None,
                                "persistent_market_cache_allowed": False}}
                        owner = self
                        def validate_inputs():
                            owner._clock()
                            if _code_pins()["source_files"] != code["source_files"]:
                                raise ValueError("ANALYSIS_PRODUCER_SOURCE_CHANGED")
                            fresh = provider.revalidate_for_persistence(subjects, expected_readiness=preflight)
                            if fresh["status"] != "READY" or _market_binding(fresh) != _market_binding(preflight) or _input_pins(provider, fresh) != initial_inputs:
                                raise ValueError("ANALYSIS_CAPTURED_INPUT_CHANGED")
                            _single_link(db)
                            if any(value["exists"] for value in _database_state(db)["journals"].values()):
                                raise ValueError("ANALYSIS_DATABASE_JOURNAL_REFUSED")
                        class ObservedStore(SQLiteStore):
                            def save_run(inner, metadata, signals, updated_at, **kwargs):
                                validate_inputs()
                                selected = signals["ticker"].tolist()
                                if len(selected) > len(subjects) or len(set(selected)) != len(selected) or any(t not in subjects for t in selected):
                                    raise ValueError("ANALYSIS_SCOPE_CHANGED_BEFORE_PERSISTENCE")
                                copied = signals.copy(deep=True)
                                expected = SQLiteStore._build_signal_payload(0, copied, updated_at)
                                _encode(expected)
                                value = super().save_run(metadata, signals, updated_at, **kwargs)
                                committed.append(value)
                                expected_payloads[value] = [list((value, *row[1:])) for row in expected]
                                return value
                        store = ObservedStore(db)
                        validate_inputs()
                        with _offline_only(record):
                            if self.pipeline_factory is None:
                                from market_checker_app.services.pipeline_service import PipelineService
                                pipeline = PipelineService(config)
                            else:
                                pipeline = self.pipeline_factory(config)
                            if getattr(pipeline, "captured_market_mode", False) is not True:
                                raise ValueError("CAPTURED_PRODUCTION_PIPELINE_REQUIRED")
                            pipeline.yahoo_client = provider
                            pipeline.mt5_client = pipeline.rss_client = _ForbiddenClient()
                            if getattr(pipeline, "yahoo_cache", None) is not None or getattr(pipeline, "yahoo_ohlc_cache", None) is not None:
                                raise ValueError("PERSISTENT_MARKET_CACHE_REFUSED")
                            validate_inputs(); record["clocks"]["run_started"] = self._clock()
                            record["pipeline_invoked"] = True
                            result = pipeline.run(watchlist=list(subjects), rss_sources=[], store=store,
                                progress_callback=progress_callback, rss_enabled=False, mt5_enabled=False, yahoo_metadata_enabled=False)
                        record["clocks"]["run_returned"] = self._clock()
                        validate_inputs()
            except Exception as error:
                reason = str(error) if isinstance(error, (ValueError, RuntimeError)) and re.fullmatch(r"[A-Z][A-Z0-9_:]{1,150}", str(error)) else "ANALYSIS_CAPTURE_EXECUTION_FAILED"
                reasons.append(reason); record["status"] = "PARTIAL" if record["pipeline_invoked"] else "WAIT"
        record["saved_run_ids_returned_by_store"] = list(committed)
        record["saved_run_count_returned_by_store"] = len(committed)
        if record["pipeline_invoked"]:
            observed = _safe_read_runs(db, after_id=max_before, expected_ids=committed, subjects=subjects)
            record["database"] = observed
            if observed["status"] != "READ_ONLY_OBSERVED" or observed["new_run_count"] != 1 or observed["rows_withheld"]:
                reasons.append("EXACT_NEW_PERSISTED_RUN_NOT_VERIFIED")
            rows = observed["signals"]
            if (len(rows) != len(subjects) or {row["ticker"] for row in rows} != set(subjects)
                    or len(observed["runs"]) != 1 or observed["runs"][0]["processed_symbols"] != len(subjects)
                    or observed["runs"][0]["watchlist_size"] != len(subjects) or observed["runs"][0]["errors_count"] != 0):
                reasons.append("INCOMPLETE_OR_FAILED_SELECTED_RUN")
            if len(committed) != 1 or committed != observed["new_run_ids"]:
                reasons.append("SAME_PROCESS_STORE_BINDING_NOT_VERIFIED")
            elif [[row[column] for column in SIGNAL_COLUMNS] for row in rows] != expected_payloads.get(committed[0]):
                reasons.append("PERSISTED_VALUES_DIFFER_FROM_EXACT_PIPELINE_SAVE_PAYLOAD")
            if (not isinstance(result, dict) or type(result.get("run_id")) is not int
                    or result.get("run_id") not in committed or result.get("errors")):
                reasons.append("PRODUCTION_PIPELINE_RESULT_NOT_COMPLETE")
            elif len(committed) == 1:
                try:
                    updated_at = expected_payloads[committed[0]][0][2]
                    returned = [list(row) for row in SQLiteStore._build_signal_payload(committed[0], result["signals"], updated_at)]
                    if returned != expected_payloads[committed[0]]:
                        raise ValueError("RETURNED_SIGNAL_VALUES_CHANGED")
                    for identity_column in ("run_id", "id"):
                        if identity_column in result["signals"].columns:
                            identities = [SQLiteStore._sqlite_scalar(value) for value in result["signals"][identity_column].tolist()]
                            expected_ids = [row[identity_column] for row in rows]
                            if any(type(value) is not int for value in identities) or identities != expected_ids:
                                raise ValueError("RETURNED_SIGNAL_ROW_IDENTITY_CHANGED")
                    metadata = result["metadata"]
                    persisted = observed["runs"][0]
                    expected_meta = {"started_at": to_iso(metadata.started_at), "finished_at": to_iso(metadata.finished_at),
                        **{key: getattr(metadata, key) for key in RUN_COLUMNS if key not in {"run_id", "started_at", "finished_at"}}}
                    if expected_meta != {key: persisted[key] for key in expected_meta}:
                        raise ValueError("RETURNED_RUN_METADATA_CHANGED")
                    run_started = _utc(datetime.fromisoformat(record["clocks"]["run_started"]["observed_at"]))
                    run_returned = _utc(datetime.fromisoformat(record["clocks"]["run_returned"]["observed_at"]))
                    if not run_started <= _utc(datetime.fromisoformat(persisted["started_at"])) <= _utc(datetime.fromisoformat(persisted["finished_at"])) <= run_returned:
                        raise ValueError("SAVED_RUN_NOT_WITHIN_THIS_PHYSICAL_EXECUTION")
                except (KeyError, IndexError, TypeError, ValueError, AttributeError):
                    reasons.append("RETURNED_RESULT_OR_PHYSICAL_RUN_CLOCK_BINDING_NOT_VERIFIED")
            if not code["exact_pinned_sources_match_HEAD"]:
                reasons.append("EXACT_COMMITTED_PRODUCER_IDENTITY_UNAVAILABLE")
            if record["blocked_network_attempts"]:
                reasons.append("NETWORK_ATTEMPT_BLOCKED")
        try:
            record["clocks"]["capture_completed"] = self._clock()
        except ValueError:
            reasons.append("ANALYSIS_PHYSICAL_CLOCK_ROLLBACK")
        record["clocks"]["integrity_verified"] = not self.clock_failed
        record["clocks"]["latest_observed"] = self.latest_clock
        record["clocks"]["elapsed_ns"] = self.high_ns - started["monotonic_ns"]
        if record["pipeline_invoked"] and record["clocks"]["elapsed_ns"] <= 0:
            reasons.append("POSITIVE_PHYSICAL_EXECUTION_DURATION_NOT_VERIFIED")
        if self.fixture_only:
            reasons.append("SYNTHETIC_EXECUTION_IS_NOT_NATIVE_PROOF")
            record["status"] = "FIXTURE_ONLY" if record["pipeline_invoked"] else record["status"]
        verified = record["pipeline_invoked"] and eligible_runtime and not self.fixture_only and not reasons
        record["native_windows_selected_analysis_verified"] = bool(verified)
        if verified:
            record["status"] = "NATIVE_SELECTED_ANALYSIS_OBSERVED"
        elif record["pipeline_invoked"] and not self.fixture_only:
            record["status"] = "PARTIAL"
        record["qualification_reasons"] = list(dict.fromkeys(reasons))
        def final_validate():
            self._clock()
            if _validate_runtime(self.runtime()) != runtime or _code_pins()["source_files"] != code["source_files"]:
                raise ValueError("ANALYSIS_RUNTIME_OR_PRODUCER_CHANGED_BEFORE_PUBLICATION")
            if provider is not None and initial_inputs is not None:
                fresh = provider.revalidate_for_persistence(subjects, expected_readiness=record["market_preflight"])
                if fresh["status"] != "READY" or _input_pins(provider, fresh) != initial_inputs:
                    raise ValueError("ANALYSIS_INPUT_CHANGED_BEFORE_PUBLICATION")
            if record["database"] is not None and _database_state(db) != record["database"].get("file_state_after", record["database"]["file_state"]):
                raise ValueError("ANALYSIS_DATABASE_CHANGED_BEFORE_PUBLICATION")
            self._clock()
        output = output.resolve(); _single_link(output)
        output.parent.mkdir(parents=True, exist_ok=True); _guard(output)
        parent_identity = (output.parent.stat().st_dev, output.parent.stat().st_ino)
        publication_response = None
        def unpublished():
            record["native_windows_selected_analysis_verified"] = False
            record["status"] = "RECEIPT_NOT_PUBLISHED"
            record["qualification_reasons"].append("IMMUTABLE_ANALYSIS_PUBLICATION_REFUSED")
            if record["pipeline_invoked"]:
                record["database"] = _safe_read_runs(db, after_id=max_before, expected_ids=committed, subjects=subjects)
            failed_record = _seal(record); _encode(failed_record)
            return {"status": failed_record["status"], "scope": SCOPE, "receipt_created": False,
                "receipt_path": None, "receipt_sha256": None, "requested_output_path": str(output),
                "receipt": failed_record, "native_windows_selected_analysis_verified": False,
                "pipeline_invoked": failed_record["pipeline_invoked"]}
        for attempt in range(2):
            record = _seal(record); raw = _encode(record); temporary = None
            try:
                _guard(output)
                if parent_identity != (output.parent.stat().st_dev, output.parent.stat().st_ino):
                    raise ValueError("ANALYSIS_OUTPUT_PARENT_CHANGED")
                with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".user_windows_analysis_", suffix=".tmp", delete=False) as handle:
                    temporary = Path(handle.name); handle.write(raw); handle.flush(); os.fsync(handle.fileno())
                if attempt == 0:
                    try:
                        final_validate()
                    except Exception as error:
                        record["native_windows_selected_analysis_verified"] = False
                        record["status"] = ("FIXTURE_ONLY" if self.fixture_only else "PARTIAL") if record["pipeline_invoked"] else record["status"]
                        record["qualification_reasons"].append("POST_FSYNC_PUBLICATION_REVALIDATION_FAILED")
                        if isinstance(error, ValueError) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,150}", str(error)):
                            record["qualification_reasons"].append(str(error))
                        record["publication_qualification"] = "FAILED_UNQUALIFIED_OBSERVATION_ONLY"
                        record["clocks"]["integrity_verified"] = not self.clock_failed
                        record["clocks"]["latest_observed"] = self.latest_clock
                        if record["pipeline_invoked"]:
                            record["database"] = _safe_read_runs(db, after_id=max_before, expected_ids=committed, subjects=subjects)
                        continue
                _single_link(output); _guard(temporary)
                if parent_identity != (output.parent.stat().st_dev, output.parent.stat().st_ino):
                    raise ValueError("ANALYSIS_OUTPUT_PARENT_CHANGED")
                os.link(temporary, output)
                publication_response = {"status": record["status"], "scope": SCOPE, "receipt_created": True,
                    "receipt_path": str(output), "receipt_sha256": hashlib.sha256(raw).hexdigest(),
                    "receipt": record, "native_windows_selected_analysis_verified": record["native_windows_selected_analysis_verified"],
                    "pipeline_invoked": record["pipeline_invoked"]}
                return publication_response
            except (ValueError, OSError):
                publication_response = unpublished()
                return publication_response
            finally:
                if temporary is not None:
                    try:
                        _guard(temporary)
                        if parent_identity != (output.parent.stat().st_dev, output.parent.stat().st_ino):
                            raise ValueError("ANALYSIS_OUTPUT_PARENT_CHANGED")
                        temporary.unlink(missing_ok=True)
                    except (ValueError, OSError):
                        # Never chase a replaced parent to unlink another file.
                        # Preserve the immutable bytes and the saved-run facts.
                        if publication_response is not None:
                            publication_response["temporary_cleanup_incomplete"] = True
                            publication_response["native_windows_selected_analysis_verified"] = False
        raise RuntimeError("ANALYSIS_RECEIPT_NOT_PUBLISHED")
