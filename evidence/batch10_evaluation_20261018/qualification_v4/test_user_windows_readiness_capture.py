"""Offline readiness boundaries; all populated inputs are explicit test fixtures.

No fixture proves a native user-Windows run, forecast authenticity or PIT/OOS.
Temporary roots are canonicalized before any fixture or guard target is made.
"""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import itertools
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from market_checker_app.prediction_contract import canonical_hash
from market_checker_app.services import user_windows_readiness_capture as module
from market_checker_app.services.captured_market_data_provider import (
    BENCHMARK_TICKERS, MANIFEST_SCHEMA_VERSION, build_captured_market_data_entry,
)
from market_checker_app.services.captured_market_input_acquisition import CapturedMarketInputAcquisition
from market_checker_app.services.user_windows_readiness_capture import (
    UserWindowsReadinessCapture, load_user_windows_readiness_receipt,
)
from market_checker_app.services.us_equity_calendar_service import _nyse_calendar, last_completed_session, sessions_between
from market_checker_app.services.yahoo_price_provenance_service import capture_provider_frame, persist_observation


NOW = datetime(2026, 10, 17, 22, tzinfo=timezone.utc)
NATIVE_CONNECT = sqlite3.connect


def runtime_fixture(*, windows=True, hosted=False):
    flags = {name: hosted and name == "GITHUB_ACTIONS" for name in module._CI_NAMES}
    return {"os_system": "Windows" if windows else "Linux", "os_release": "fixture-release",
        "os_version": "fixture-version", "machine": "fixture-machine", "os_name": "nt" if windows else "posix",
        "sys_platform": "win32" if windows else "linux", "python_implementation": "CPython",
        "python_version": "3.12.fixture", "hosted_ci_indicators": flags, "ci_detected": hosted}


class TrackedConnection(sqlite3.Connection):
    closed = False

    def close(self):
        self.closed = True
        super().close()


class UserWindowsReadinessCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.output = self.root / "receipts" / "readiness.json"
        self.db = self.root / "history.db"
        self.archive = self.root / "market-inputs"
        self.manifest = self.archive / "market_manifest.json"
        self.addCleanup(patch.stopall)
        patch("socket.socket.connect", side_effect=AssertionError("No readiness HTTP")).start()
        patch("urllib.request.urlopen", side_effect=AssertionError("No readiness HTTP")).start()

    def recorder(self, **overrides):
        settings = {"enabled": True, "observation_clock": lambda: NOW,
            "monotonic_clock": itertools.count(1_000_000, 10).__next__, "runtime_probe": runtime_fixture}
        settings.update(overrides)
        return UserWindowsReadinessCapture(**settings)

    def capture(self, *, recorder=None, **overrides):
        settings = {"output_path": self.output, "tickers": ["AAPL"]}
        settings.update(overrides)
        return (recorder or self.recorder()).capture(**settings)

    def database_fixture(self, *, rows=1, selected_value=None, private_payload=False):
        with closing(NATIVE_CONNECT(self.db)) as connection:
            connection.executescript("""
                CREATE TABLE runs(run_id,started_at,finished_at,watchlist_size,processed_symbols,warnings_count,errors_count);
                CREATE TABLE prediction_snapshots(snapshot_id,run_id,ticker,as_of,observed_at,label_status,snapshot_hash,created_at,provenance_json);
                CREATE TABLE signal_history(run_id,ticker,updated_at);
            """)
            for index in range(rows):
                run_id = selected_value if selected_value is not None else index + 1
                connection.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?)", (run_id, NOW.isoformat(), NOW.isoformat(), 1, 1, 0, 0))
                connection.execute("INSERT INTO prediction_snapshots VALUES (?,?,?,?,?,?,?,?,?)", (
                    f"fixture-{index}", index + 1, "AAPL", NOW.isoformat(), NOW.isoformat(), "PENDING",
                    "a" * 64, NOW.isoformat(), "fixture-private-payload-never-selected" if private_payload else "{}"))
                connection.execute("INSERT INTO signal_history VALUES (?,?,?)", (index + 1, "AAPL", NOW.isoformat()))
            connection.commit()

    def complete_market_fixture(self, *, original_parent=False):
        self.archive.mkdir()
        latest = last_completed_session(NOW)
        sessions = sessions_between(latest - pd.Timedelta(days=600), latest)[-66:]
        values = [100.0 + index / 100 for index in range(66)]
        frame = pd.DataFrame({"Open": [v - .2 for v in values], "High": [v + .3 for v in values],
            "Low": [v - .4 for v in values], "Close": values, "Volume": [1_000_000 + i for i in range(66)]},
            index=pd.DatetimeIndex(sessions))
        if original_parent:
            schedule = _nyse_calendar().schedule(start_date=sessions[0], end_date=sessions[-1])
            def fixture_transport(url, limit):
                ticker = url.split("/chart/")[1].split("?")[0]
                quote = {key: frame[column].tolist() for key, column in (("open", "Open"), ("high", "High"), ("low", "Low"), ("close", "Close"), ("volume", "Volume"))}
                body = json.dumps({"chart": {"result": [{"meta": {"symbol": ticker, "currency": "USD",
                    "exchangeTimezoneName": "America/New_York", "exchangeName": "NMS" if ticker == "AAPL" else "PCX",
                    "instrumentType": "EQUITY" if ticker == "AAPL" else "ETF", "dataGranularity": "1d"},
                    "timestamp": [int(stamp.timestamp()) for stamp in schedule.market_open], "indicators": {"quote": [quote]}}],
                    "error": None}}, separators=(",", ":"), allow_nan=False).encode()
                return {"status": 200, "response_url": url, "body": body, "content_encoding": "identity",
                    "content_type": "application/json", "body_complete_within_byte_budget": True}
            service = CapturedMarketInputAcquisition(archive_root=self.archive, enabled=True,
                observation_clock=lambda: NOW, transport=fixture_transport)
            report = service.acquire(service.plan(("AAPL", *BENCHMARK_TICKERS)), execute=True)
            self.assertEqual(("READY", 13, True), (report["status"], report["usable_count"], report["fixture_only"]))
            self.manifest = self.archive / report["manifest_path"]
            return report
        entries = []
        for ticker in ("AAPL", *BENCHMARK_TICKERS):
            record = capture_provider_frame(ticker, frame, started_at=NOW - timedelta(minutes=2), observed_at=NOW - timedelta(minutes=1))
            record["evidence_origin"] = "DETERMINISTIC_TEST_FIXTURE"
            record["capture_id"] = canonical_hash({key: value for key, value in record.items() if key != "capture_id"})
            path = persist_observation(record, self.archive / "selected")
            entries.append(build_captured_market_data_entry(archive_root=self.archive, source_archive_path=path.relative_to(self.archive).as_posix()))
        self.manifest.write_text(json.dumps({"schema_version": MANIFEST_SCHEMA_VERSION, "instruments": entries}) + "\n", encoding="utf-8")

    def assert_late_market_refused(self, callback, **settings):
        fsync = module.os.fsync
        hits = []
        def mutate_after_fsync(descriptor):
            fsync(descriptor)
            if not hits:
                hits.append(True)
                callback()
        with patch.object(module.os, "fsync", side_effect=mutate_after_fsync), self.assertRaises(ValueError):
            self.capture(market_manifest_path=self.manifest, market_archive_root=self.archive, **settings)
        self.assertEqual([True], hits)
        self.assertFalse(self.output.exists())
        self.assertEqual([], list(self.output.parent.iterdir()))

    def assert_no_acceptance(self, record):
        self.assertEqual("READINESS_CAPTURE", record["scope"])
        self.assertEqual("ABSENT", record["same_process_production_run_receipt"])
        for key in ("user_windows_end_to_end_verified", "strict_pit_eligible", "historical_OOS_verified", "full_specialist_acceptance", "activation_allowed"):
            self.assertIs(False, record[key])
        for key in ("network_requests", "DB_writes", "forecasts_created", "registrations_created", "labels_created"):
            self.assertEqual(0, record[key])

    def test_default_disabled_touches_no_clocks_probe_provider_database_or_output(self):
        fail = lambda: self.fail("disabled recorder must not inspect anything")
        with patch.object(module, "_code_observation", side_effect=fail), patch.object(module, "CapturedMarketDataProvider", side_effect=fail), \
             patch.object(module.sqlite3, "connect", side_effect=fail):
            result = self.capture(recorder=UserWindowsReadinessCapture(observation_clock=fail, runtime_probe=fail))
        self.assertEqual(("DISABLED", False), (result["status"], result["receipt_created"]))
        self.assertFalse(self.output.parent.exists())
        self.assertFalse(self.db.exists())

    def test_enable_and_row_bound_require_explicit_real_boolean_and_bounded_integer(self):
        for value in (1, "true", None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "EXPLICIT_READINESS_ENABLE"):
                UserWindowsReadinessCapture(enabled=value)
        for value in (True, 0, 65, "1"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "ROW_BOUND"):
                self.capture(maximum_rows=value)
        self.assertFalse(self.output.exists())

    def test_explicit_subjects_refuse_empty_duplicates_foreign_and_non_string_values(self):
        for subjects in ([], ["AAPL", "AAPL"], ["SPY"], ["aapl"], [None], ["FAKE"]):
            with self.subTest(subjects=subjects), self.assertRaisesRegex(ValueError, "CANONICAL_READINESS_SUBJECTS"):
                self.capture(tickers=subjects)
        self.assertFalse(self.output.exists())

    def test_unbounded_subject_generator_is_consumed_only_to_hard_limit(self):
        seen = []
        def infinite():
            while True:
                seen.append(True)
                yield "AAPL"
        with self.assertRaisesRegex(ValueError, "CANONICAL_READINESS_SUBJECTS"):
            self.capture(tickers=infinite())
        self.assertEqual(688, len(seen))

    def test_missing_database_and_no_manifest_are_observed_absent_without_initializing(self):
        with patch.object(module.sqlite3, "connect", side_effect=AssertionError("Do not initialize absent DB")):
            result = self.capture(db_path=self.db)
        record = load_user_windows_readiness_receipt(self.output)
        self.assertEqual(result["receipt"], record)
        self.assertEqual(("ABSENT", False), (record["database"]["status"], record["database"]["SQL_opened"]))
        self.assertEqual("NO_EXPLICIT_CAPTURED_MARKET_MANIFEST", record["market_readiness"]["reason"])
        self.assertEqual("WAIT", record["readiness_status"])
        self.assertFalse(self.db.exists())
        self.assert_no_acceptance(record)

    def test_missing_explicit_captured_manifest_runs_real_provider_and_all_benchmark_checks_offline(self):
        with patch.object(module.sqlite3, "connect", side_effect=AssertionError("No DB")), \
             patch.object(Path, "glob", side_effect=AssertionError("No discovery")), \
             patch.object(Path, "rglob", side_effect=AssertionError("No discovery")):
            record = self.capture(market_manifest_path=self.manifest, market_archive_root=self.archive)["receipt"]
        self.assertEqual(("WAIT_DATA", 13, 0), tuple(record["market_readiness"][key] for key in ("status", "requested_count", "usable_count")))
        self.assertFalse(record["market_manifest"]["exists"])
        self.assertEqual("WAIT_DATA", record["market_readiness_final"]["status"])
        self.assertFalse(self.archive.exists())

    def test_explicit_manifest_and_archive_are_required_as_a_pair(self):
        for settings in ({"market_manifest_path": self.manifest}, {"market_archive_root": self.archive}):
            with self.subTest(settings=settings), self.assertRaisesRegex(ValueError, "REQUIRED_TOGETHER"):
                self.capture(**settings)
        self.assertFalse(self.output.parent.exists())

    def test_read_only_database_connection_is_deterministically_closed_and_bytes_unchanged(self):
        self.database_fixture(private_payload=True)
        original = self.db.read_bytes()
        opened, calls = [], []
        def tracked(*args, **kwargs):
            calls.append((args, kwargs))
            connection = NATIVE_CONNECT(*args, **kwargs, factory=TrackedConnection)
            opened.append(connection)
            return connection
        with patch.object(module.sqlite3, "connect", side_effect=tracked):
            record = self.capture(db_path=self.db)["receipt"]
        self.assertEqual(1, len(opened))
        self.assertTrue(opened[0].closed)
        self.assertTrue(calls[0][0][0].endswith("?mode=ro"))
        self.assertIs(True, calls[0][1]["uri"])
        self.assertEqual(original, self.db.read_bytes())
        self.assertEqual("READ_ONLY_OBSERVED", record["database"]["status"])
        self.assertTrue(record["database"]["raw_file_unchanged_during_observation"])
        self.assertNotIn("fixture-private-payload-never-selected", self.output.read_text())
        for suffix in ("-wal", "-shm", "-journal"):
            self.assertFalse(Path(str(self.db) + suffix).exists())

    def test_reader_mocked_windows_and_saved_rows_do_not_establish_prior_run_platform_or_acceptance(self):
        self.database_fixture()
        record = self.capture(db_path=self.db)["receipt"]
        self.assertEqual(("Windows", "FIXTURE_ONLY"), (record["runtime"]["os_system"], record["status"]))
        self.assertEqual("UNKNOWN", record["database"]["saved_run_runtime_platform"])
        self.assertIs(True, record["database"]["reader_platform_does_not_establish_saved_run_platform"])
        self.assertIs(False, record["database"]["stored_row_authenticity_independently_verified"])
        self.assertEqual(1, record["database"]["tables"]["prediction_snapshots"]["sampled_rows"])
        self.assert_no_acceptance(record)

    def test_database_metadata_sample_is_bounded_subject_filtered_and_not_coverage(self):
        self.database_fixture(rows=4)
        with closing(NATIVE_CONNECT(self.db)) as connection:
            connection.execute("UPDATE prediction_snapshots SET ticker='NVDA' WHERE snapshot_id='fixture-0'")
            connection.execute("UPDATE signal_history SET ticker='NVDA' WHERE run_id=1")
            connection.commit()
        record = self.capture(db_path=self.db, maximum_rows=2)["receipt"]
        tables = record["database"]["tables"]
        self.assertEqual((2, True), (tables["runs"]["sampled_rows"], tables["runs"]["limit_reached"]))
        self.assertEqual("BOUNDED_GLOBAL_RUN_METADATA_NO_PLATFORM_PROOF", tables["runs"]["scope"])
        for table in ("prediction_snapshots", "signal_history"):
            self.assertEqual(2, tables[table]["sampled_rows"])
            self.assertTrue(tables[table]["limit_reached"])
            self.assertEqual({"AAPL"}, {row["ticker"] for row in tables[table]["rows"]})

    def test_corrupt_database_is_wait_and_preserves_exact_original_bytes(self):
        self.db.write_bytes(b"Not SQLite: deterministic corrupt fixture\x00")
        before = self.db.read_bytes()
        record = self.capture(db_path=self.db)["receipt"]
        self.assertEqual("WAIT_DATABASE_READ", record["database"]["status"])
        self.assertEqual(before, self.db.read_bytes())
        self.assert_no_acceptance(record)

    def test_existing_journal_refuses_sql_open_instead_of_initializing_or_ignoring_wal(self):
        self.database_fixture()
        original = self.db.read_bytes()
        for index, suffix in enumerate(("-wal", "-shm", "-journal")):
            with self.subTest(suffix=suffix):
                sidecar = Path(str(self.db) + suffix)
                sidecar.write_bytes(b"synthetic active journal marker")
                with patch.object(module.sqlite3, "connect", side_effect=AssertionError("Refuse active DB")):
                    record = self.capture(db_path=self.db, output_path=self.root / f"journal-{index}.json")["receipt"]
                self.assertEqual(("WAIT_ACTIVE_DATABASE_JOURNAL", False), (record["database"]["status"], record["database"]["SQL_opened"]))
                for table in record["database"]["tables"].values():
                    self.assertIsNone(table["present"])
                    self.assertIsNone(table["sampled_rows"])
                    self.assertEqual(("UNOBSERVED", []), (table["observation_status"], table["rows"]))
                sidecar.unlink()
        self.assertEqual(original, self.db.read_bytes())

    def test_selected_access_bearing_metadata_is_wholly_withheld_without_value_export(self):
        sentinel = "api_key=fixture-not-real-sensitive-marker"
        self.database_fixture(selected_value=sentinel)
        record = self.capture(db_path=self.db)["receipt"]
        self.assertEqual("WITHHELD_DATABASE_METADATA", record["database"]["status"])
        self.assertTrue(all(table["rows"] == [] and table["sampled_rows"] is None for table in record["database"]["tables"].values()))
        self.assertNotIn(sentinel, self.output.read_text())

    def test_incompatible_database_schema_does_not_add_tables_or_columns(self):
        with closing(NATIVE_CONNECT(self.db)) as connection:
            connection.execute("CREATE TABLE runs(unrelated TEXT)")
            connection.commit()
        original = self.db.read_bytes()
        record = self.capture(db_path=self.db)["receipt"]
        self.assertEqual("WAIT_DATABASE_READ", record["database"]["status"])
        self.assertEqual(original, self.db.read_bytes())

    def test_sql_authorizer_rejects_unapproved_table_and_all_writes(self):
        self.database_fixture()
        seen = []
        class InspectConnection(TrackedConnection):
            def set_authorizer(inner, callback):
                seen.extend([callback(sqlite3.SQLITE_INSERT, "runs", None, "main", None),
                    callback(sqlite3.SQLITE_READ, "secret_other_table", "value", "main", None),
                    callback(sqlite3.SQLITE_READ, "runs", "run_id", "main", None)])
                return super().set_authorizer(callback)
        def inspect(*args, **kwargs):
            return NATIVE_CONNECT(*args, **kwargs, factory=InspectConnection)
        with patch.object(module.sqlite3, "connect", side_effect=inspect):
            record = self.capture(db_path=self.db)["receipt"]
        self.assertEqual([sqlite3.SQLITE_DENY, sqlite3.SQLITE_DENY, sqlite3.SQLITE_OK], seen)
        self.assertEqual("READ_ONLY_OBSERVED", record["database"]["status"])

    def test_sql_progress_bound_interrupts_large_unmatched_subject_scan_and_closes(self):
        self.database_fixture(rows=1)
        with closing(NATIVE_CONNECT(self.db)) as connection:
            connection.executemany("INSERT INTO signal_history VALUES (?, 'NVDA', ?)", ((index, NOW.isoformat()) for index in range(500)))
            connection.commit()
        opened = []
        def tracked(*args, **kwargs):
            connection = NATIVE_CONNECT(*args, **kwargs, factory=TrackedConnection)
            opened.append(connection)
            return connection
        with patch.object(module, "MAX_SQL_OPERATIONS", 0), patch.object(module.sqlite3, "connect", side_effect=tracked):
            record = self.capture(db_path=self.db)["receipt"]
        self.assertEqual("WAIT_DATABASE_READ", record["database"]["status"])
        self.assertTrue(record["database"]["query_bound_reached"])
        self.assertTrue(opened[0].closed)

    def test_runtime_environment_exports_only_fixed_boolean_indicators_never_values(self):
        sentinel = "not-a-real-private-environment-value"
        with patch.dict(os.environ, {"CI": sentinel, "GITHUB_ACTIONS": "true", "OTHER_PRIVATE_TEST_KEY": sentinel}):
            record = self.capture(recorder=UserWindowsReadinessCapture(enabled=True, observation_clock=lambda: NOW))["receipt"]
        self.assertEqual(set(module._CI_NAMES), set(record["runtime"]["hosted_ci_indicators"]))
        self.assertTrue(record["runtime"]["hosted_ci_indicators"]["GITHUB_ACTIONS"])
        self.assertFalse(record["runtime"]["hosted_ci_indicators"]["CI"])
        self.assertNotIn(sentinel, self.output.read_text())
        self.assertNotIn("OTHER_PRIVATE_TEST_KEY", self.output.read_text())
        self.assertTrue(all(type(value) is bool for value in record["runtime"]["hosted_ci_indicators"].values()))

    def test_all_injected_clock_or_runtime_hooks_are_fixture_only_and_cannot_supply_acceptance(self):
        cases = ({"observation_clock": lambda: NOW}, {"monotonic_clock": lambda: 5}, {"runtime_probe": runtime_fixture})
        for index, settings in enumerate(cases):
            with self.subTest(settings=list(settings)):
                record = self.capture(recorder=UserWindowsReadinessCapture(enabled=True, **settings), output_path=self.root / f"injected-{index}.json")["receipt"]
                self.assertEqual(("FIXTURE_ONLY", "DETERMINISTIC_TEST_FIXTURE", True), (record["status"], record["evidence_origin"], record["fixture_only"]))
                self.assert_no_acceptance(record)
        with self.assertRaises(TypeError):
            UserWindowsReadinessCapture(enabled=True, user_windows_end_to_end_verified=True)

    def test_malformed_or_access_bearing_runtime_probe_fails_without_receipt(self):
        for variant in ("extra", "boolean", "access"):
            record = runtime_fixture()
            if variant == "extra": record["caller_acceptance"] = True
            elif variant == "boolean": record["hosted_ci_indicators"]["CI"] = "true"
            else: record["os_release"] = "api_key=fixture-only"
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                self.capture(recorder=self.recorder(runtime_probe=lambda: record))
        self.assertFalse(self.output.exists())

    def test_complete_66_session_asset_and_12_benchmarks_are_real_provider_fixture_readiness_only(self):
        self.complete_market_fixture()
        self.database_fixture()
        record = self.capture(db_path=self.db, market_manifest_path=self.manifest, market_archive_root=self.archive)["receipt"]
        self.assertEqual(("READY", 13), (record["market_readiness"]["status"], record["market_readiness"]["usable_count"]))
        self.assertEqual("READY", record["market_readiness_final"]["status"])
        self.assertEqual("FIXTURE_ONLY", record["status"])
        self.assertTrue(record["fixture_only"])
        self.assertEqual([row["source"] for row in record["market_readiness"]["rows"]], [row["source"] for row in record["market_readiness_final"]["rows"]])
        self.assertEqual("UNKNOWN", record["publisher_revision_timing_status"])
        self.assert_no_acceptance(record)

    def test_default_clock_refuses_marked_synthetic_market_inputs_and_preserves_fixture_visibility(self):
        self.complete_market_fixture()
        record = self.capture(recorder=UserWindowsReadinessCapture(enabled=True), market_manifest_path=self.manifest, market_archive_root=self.archive)["receipt"]
        self.assertEqual(("WAIT_DATA", 0), (record["market_readiness"]["status"], record["market_readiness"]["usable_count"]))
        self.assertEqual({"SYNTHETIC_CAPTURE_NOT_PRODUCTION_INPUT"}, {row["reason"] for row in record["market_readiness"]["rows"]})
        self.assertTrue(record["fixture_only"])
        self.assert_no_acceptance(record)

    def test_physical_utc_and_monotonic_clocks_are_aware_ordered_and_never_rebased(self):
        ticks = iter(range(100, 200, 3))
        record = self.capture(recorder=self.recorder(monotonic_clock=ticks.__next__))["receipt"]
        clocks = record["clocks"]
        self.assertEqual(NOW.isoformat(), clocks["started_at"])
        self.assertEqual(NOW.isoformat(), clocks["observed_at"])
        self.assertGreater(clocks["monotonic_ended_ns"], clocks["monotonic_started_ns"])
        self.assertEqual(clocks["elapsed_ns"], clocks["monotonic_ended_ns"] - clocks["monotonic_started_ns"])

    def test_wall_rollback_naive_or_invalid_monotonic_clock_fails_without_creating_receipt(self):
        cases = ({"observation_clock": iter([NOW, NOW - timedelta(seconds=1)]).__next__},
            {"observation_clock": lambda: NOW.replace(tzinfo=None)},
            {"monotonic_clock": iter([10, 9]).__next__}, {"monotonic_clock": lambda: True})
        for settings in cases:
            with self.subTest(settings=list(settings)), self.assertRaises(ValueError):
                self.capture(recorder=self.recorder(**settings))
        self.assertFalse(self.output.parent.exists())

    def test_reserved_output_names_and_non_json_locations_are_refused(self):
        for name in ("market_manifest.json", "prospective_source_manifest.json", "yahoo_price_fake.json", "forecast_fake.json", "prediction_label_fake.json", "capture.txt"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "SEPARATE_READINESS_JSON"):
                self.capture(output_path=self.root / name)

    def test_output_cannot_overwrite_or_be_below_any_input_even_if_missing(self):
        for path, settings in ((self.db, {"db_path": self.db}), (self.manifest, {"market_manifest_path": self.manifest, "market_archive_root": self.archive})):
            output = path / "receipt.json"
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "COLLIDES_WITH_INPUT"):
                self.capture(output_path=output, **settings)
        with self.assertRaisesRegex(ValueError, "SEPARATE_FROM_MARKET_INPUT_ROOT"):
            self.capture(output_path=self.archive / "receipt.json", market_manifest_path=self.manifest, market_archive_root=self.archive)
        self.assertFalse(self.db.exists())
        self.assertFalse(self.archive.exists())

    def test_existing_receipt_collision_is_exclusive_and_original_bytes_remain_exact(self):
        first = self.capture()
        original = self.output.read_bytes()
        with patch.object(module, "_code_observation", side_effect=AssertionError("Reject collision first")), self.assertRaises(FileExistsError):
            self.capture()
        self.assertEqual(original, self.output.read_bytes())
        self.assertEqual(first["receipt_sha256"], hashlib.sha256(original).hexdigest())

    def test_output_and_input_parent_symlink_guards_are_exercised_portably(self):
        original = Path.is_symlink
        for target, settings in ((self.output.parent, {}), (self.db.parent, {"db_path": self.db})):
            hits = []
            def guarded(path):
                if path == target:
                    hits.append(path)
                    return True
                return original(path)
            with self.subTest(target=target), patch.object(Path, "is_symlink", guarded), self.assertRaisesRegex(ValueError, "SYMLINK_OR_JUNCTION"):
                self.capture(**settings)
            self.assertGreater(len(hits), 0)
        self.assertFalse(self.output.exists())

    def test_canonical_temp_alias_fixture_hits_exact_guard_target_before_any_capture(self):
        (self.root / "alias-child").mkdir()
        lexical_alias = self.root / "alias-child" / ".."
        self.assertNotEqual(lexical_alias, lexical_alias.resolve())
        canonical_fixture_root = lexical_alias.resolve()
        target = canonical_fixture_root / "receipts"
        output = target / "alias-readiness.json"
        hits = []
        def guarded(path):
            if path == target:
                hits.append(path)
                return True
            return False
        with patch.object(Path, "is_symlink", guarded), self.assertRaisesRegex(ValueError, "SYMLINK_OR_JUNCTION"):
            self.capture(output_path=output)
        self.assertEqual(canonical_fixture_root, self.root)
        self.assertGreater(len(hits), 0)
        self.assertFalse(output.exists())

    def test_junction_guard_is_exercised_without_windows_privilege_or_platform_skip(self):
        hits = []
        target = self.output.parent
        def junction(path):
            if path == target:
                hits.append(path)
                return True
            return False
        with patch.object(Path, "is_junction", junction, create=True), self.assertRaisesRegex(ValueError, "SYMLINK_OR_JUNCTION"):
            self.capture()
        self.assertGreater(len(hits), 0)
        self.assertFalse(self.output.exists())

    def test_atomic_publish_competing_output_preserves_winner_and_removes_temporary(self):
        winner = b"immutable competing receipt bytes\r\n"
        def competing_link(source, target):
            Path(target).write_bytes(winner)
            raise FileExistsError("synthetic collision")
        with patch.object(module.os, "link", side_effect=competing_link), self.assertRaises(FileExistsError):
            self.capture()
        self.assertEqual(winner, self.output.read_bytes())
        self.assertEqual([self.output], list(self.output.parent.iterdir()))

    def test_atomic_publish_failure_does_not_change_a_prior_receipt_or_leave_partial_new_output(self):
        self.capture()
        original = self.output.read_bytes()
        other = self.output.parent / "second.json"
        with patch.object(module.os, "link", side_effect=OSError("synthetic unsupported link")), self.assertRaises(OSError):
            self.capture(output_path=other)
        self.assertEqual(original, self.output.read_bytes())
        self.assertFalse(other.exists())
        self.assertEqual([self.output], list(self.output.parent.iterdir()))

    def test_code_manifest_and_database_mutations_abort_without_receipt_and_retain_prior_bytes(self):
        self.capture()
        original = self.output.read_bytes()
        other = self.output.parent / "changed.json"
        actual = module._code_observation
        calls = 0
        def changed():
            nonlocal calls
            calls += 1
            record = actual()
            if calls == 2:
                record["source_files"][module.SOURCE_FILES[0]]["sha256"] = "f" * 64
            return record
        with patch.object(module, "_code_observation", side_effect=changed), self.assertRaisesRegex(ValueError, "PRODUCER_CODE_CHANGED"):
            self.capture(output_path=other)
        self.assertFalse(other.exists())
        self.assertEqual(original, self.output.read_bytes())
        self.archive.mkdir()
        self.manifest.write_text("{}", encoding="utf-8")
        actual_database = module._database_observation
        def mutate_manifest(*args, **kwargs):
            result = actual_database(*args, **kwargs)
            self.manifest.write_text('{"changed":true}', encoding="utf-8")
            return result
        with patch.object(module, "_database_observation", side_effect=mutate_manifest), self.assertRaisesRegex(ValueError, "MARKET_MANIFEST_CHANGED"):
            self.capture(output_path=other, market_manifest_path=self.manifest, market_archive_root=self.archive)
        self.assertFalse(other.exists())
        self.assertEqual(original, self.output.read_bytes())

    def test_database_change_after_read_is_rejected_before_receipt(self):
        self.database_fixture()
        actual = module._database_observation
        def mutate(*args, **kwargs):
            observed = actual(*args, **kwargs)
            with self.db.open("ab") as handle:
                handle.write(b"fixture-external-change")
            return observed
        with patch.object(module, "_database_observation", side_effect=mutate), self.assertRaisesRegex(ValueError, "DATABASE_CHANGED_BEFORE"):
            self.capture(db_path=self.db)
        self.assertFalse(self.output.exists())

    def test_late_manifest_mutation_after_temporary_fsync_cannot_publish_old_readiness(self):
        self.complete_market_fixture()
        self.assert_late_market_refused(lambda: self.manifest.write_bytes(self.manifest.read_bytes() + b" "))

    def test_late_original_frame_tamper_after_temporary_fsync_is_revalidated(self):
        self.complete_market_fixture()
        manifest = json.loads(self.manifest.read_text())
        source = self.archive / manifest["instruments"][0]["source"]["path"]
        self.assert_late_market_refused(lambda: source.write_bytes(source.read_bytes() + b" "))

    def test_late_raw_http_parent_tamper_after_temporary_fsync_is_replayed_and_refused(self):
        report = self.complete_market_fixture(original_parent=True)
        parent = self.archive / report["rows"][0]["raw_path"]
        original = parent.read_bytes()
        self.assert_late_market_refused(lambda: parent.write_bytes(original[:-1] + b" "))
        self.assertNotEqual(original, parent.read_bytes())

    def test_late_missing_raw_http_parent_after_temporary_fsync_is_not_reconstructed(self):
        report = self.complete_market_fixture(original_parent=True)
        parent = self.archive / report["rows"][0]["raw_path"]
        self.assert_late_market_refused(parent.unlink)
        self.assertFalse(parent.exists())

    def test_late_raw_http_parent_symlink_guard_hits_canonical_path_and_refuses(self):
        report = self.complete_market_fixture(original_parent=True)
        parent = self.archive / report["rows"][0]["raw_path"]
        original = Path.is_symlink
        active = [False]
        hits = []
        def guarded(path):
            if active[0] and path == parent:
                hits.append(path)
                return True
            return original(path)
        with patch.object(Path, "is_symlink", guarded):
            self.assert_late_market_refused(lambda: active.__setitem__(0, True))
        self.assertGreater(len(hits), 0)

    def test_crossing_new_completed_close_during_receipt_fsync_cannot_publish_stale_ready_inputs(self):
        self.complete_market_fixture()
        clock = [NOW]
        recorder = self.recorder(observation_clock=lambda: clock[0])
        next_completed_close = datetime(2026, 10, 19, 20, tzinfo=timezone.utc)
        self.assert_late_market_refused(lambda: clock.__setitem__(0, next_completed_close), recorder=recorder)

    def test_receipt_loader_refuses_tampering_resealed_acceptance_duplicate_keys_and_nonfinite(self):
        first = self.capture()["receipt"]
        for index, field in enumerate(("user_windows_end_to_end_verified", "strict_pit_eligible", "historical_OOS_verified", "full_specialist_acceptance")):
            record = deepcopy(first)
            record[field] = True
            record["capture_id"] = canonical_hash({key: value for key, value in record.items() if key != "capture_id"})
            path = self.root / f"forged-{index}.json"
            path.write_text(json.dumps(record), encoding="utf-8")
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "CANNOT_ASSERT_ACCEPTANCE"):
                load_user_windows_readiness_receipt(path)
        for index, body in enumerate(('{"scope":"READINESS_CAPTURE","scope":"READINESS_CAPTURE"}', '{"clock":NaN}', '{"bad":')):
            path = self.root / f"invalid-{index}.json"
            path.write_text(body, encoding="utf-8")
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, "INVALID_READINESS_RECEIPT_BODY"):
                load_user_windows_readiness_receipt(path)
        tampered = self.root / "tampered.json"
        record = deepcopy(first)
        record["status"] = "caller changed without reseal"
        tampered.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "CANNOT_ASSERT_ACCEPTANCE"):
            load_user_windows_readiness_receipt(tampered)

    def test_code_and_manifest_pins_are_exact_bytes_and_environment_cannot_supply_git_identity(self):
        with patch.dict(os.environ, {"GITHUB_SHA": "1" * 40, "CODE_SHA": "2" * 40}):
            record = self.capture()["receipt"]
        self.assertNotEqual("1" * 40, record["code"]["git_head"])
        for name, pin in record["code"]["source_files"].items():
            raw = (module.SOURCE_ROOT / name).read_bytes()
            self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), (pin["bytes"], pin["sha256"]))
        self.assertEqual(canonical_hash(record["code"]["source_files"]), record["code"]["source_manifest_sha256"])


if __name__ == "__main__":
    unittest.main()
