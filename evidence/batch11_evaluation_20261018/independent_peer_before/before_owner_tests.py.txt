"""Synthetic same-process boundaries only; never native Windows run acceptance.

Real production provider and SQLite persistence are exercised with an explicitly
injected test pipeline. No production DB, source request, model or forecast runs.
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

from market_checker_app.models import RunMetadata
from market_checker_app.prediction_contract import canonical_hash
from market_checker_app.services import user_windows_analysis_capture as module
from market_checker_app.services.captured_market_data_provider import (
    BENCHMARK_TICKERS, MANIFEST_SCHEMA_VERSION, build_captured_market_data_entry,
)
from market_checker_app.services.captured_market_input_acquisition import CapturedMarketInputAcquisition
from market_checker_app.services.user_windows_analysis_capture import UserWindowsAnalysisCapture, load_user_windows_analysis_receipt
from market_checker_app.services.user_windows_readiness_capture import _runtime_facts
from market_checker_app.services.us_equity_calendar_service import _nyse_calendar, last_completed_session, sessions_between
from market_checker_app.services.yahoo_price_provenance_service import capture_provider_frame, persist_observation


NOW = datetime(2026, 10, 18, 14, tzinfo=timezone.utc)
NATIVE_CONNECT = sqlite3.connect


def fixture_runtime(*, system="Windows", hosted=False):
    flags = {name: hosted and name == "GITHUB_ACTIONS" for name in module._runtime_facts()["hosted_ci_indicators"]}
    return {"os_system": system, "os_release": "SYNTHETIC", "os_version": "SYNTHETIC",
        "machine": "SYNTHETIC", "os_name": "nt" if system == "Windows" else "posix",
        "sys_platform": "win32" if system == "Windows" else "linux", "python_implementation": "CPython",
        "python_version": "3.12.synthetic", "hosted_ci_indicators": flags, "ci_detected": hosted}


def fixture_signals(tickers):
    string_fields = {"ticker", "current_price_source", "scoring_version", "legacy_signal", "tech_source_used",
        "blocked_reasons", "module_breakdown", "decision_signal", "forecast", "action", "action_reasons",
        "signal", "signal_strength", "regime", "reasons", "warnings", "risk_flags", "key_drivers", "overall_summary"}
    rows = []
    for index, ticker in enumerate(tickers):
        row = {name: "SYNTHETIC" if name in string_fields else 0.0 for name in module.SIGNAL_COLUMNS if name not in {"run_id", "updated_at"}}
        row.update(ticker=ticker, current_price=100.0, current_price_source="captured_market_data_close",
            scoring_version="SYNTHETIC_NOT_A_MODEL_OUTPUT", tech_source_used="captured_market_data",
            rank_in_watchlist=index + 1, signal="NO_TRADE", forecast="FLAT", warnings="SYNTHETIC_FIXTURE")
        rows.append(row)
    return pd.DataFrame(rows)


class WindowsAnalysisCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.archive = self.root / "inputs"
        self.manifest = self.archive / "market_manifest.json"
        self.db = self.root / "analysis" / "analysis.db"
        self.output = self.root / "receipts" / "analysis.json"
        self.clock = [NOW]
        self.calls = []
        self.before_save = None
        self.after_save = None
        self.return_transform = None
        self.selected_rows = None
        self.raise_after_save = False
        self.addCleanup(patch.stopall)
        patch("socket.socket.connect", side_effect=AssertionError("No actual test HTTP")).start()

    def pipeline_factory(self, config):
        owner = self
        class FixturePipeline:
            captured_market_mode = True
            yahoo_cache = yahoo_ohlc_cache = None
            def run(inner, **kwargs):
                owner.calls.append({"config": config, "kwargs": kwargs})
                if kwargs.get("progress_callback"):
                    kwargs["progress_callback"]("SYNTHETIC_PRE_SAVE_CALLBACK")
                if owner.before_save:
                    owner.before_save(inner, kwargs)
                selected = owner.selected_rows if owner.selected_rows is not None else kwargs["watchlist"]
                signals = fixture_signals(selected)
                metadata = RunMetadata(NOW, NOW, len(kwargs["watchlist"]), len(signals), 0, 0, "")
                run_id = kwargs["store"].save_run(metadata, signals, NOW.isoformat())
                if owner.after_save:
                    owner.after_save(inner, kwargs, run_id)
                if owner.raise_after_save:
                    raise RuntimeError("SYNTHETIC_FAILURE_AFTER_COMMIT")
                result = {"run_id": run_id, "metadata": metadata, "signals": signals, "warnings": [], "errors": []}
                return owner.return_transform(result) if owner.return_transform else result
        return FixturePipeline()

    def recorder(self, **overrides):
        settings = {"enabled": True, "observation_clock": lambda: self.clock[0],
            "monotonic_clock": itertools.count(1_000_000, 10).__next__, "runtime_probe": fixture_runtime,
            "pipeline_factory": self.pipeline_factory}
        settings.update(overrides)
        return UserWindowsAnalysisCapture(**settings)

    def capture(self, *, recorder=None, **overrides):
        settings = {"output_path": self.output, "tickers": ["AAPL"], "db_path": self.db,
            "market_manifest_path": self.manifest, "market_archive_root": self.archive}
        settings.update(overrides)
        return (recorder or self.recorder()).capture(**settings)

    def market_fixture(self, *, tickers=("AAPL",), raw_parent=False):
        self.archive.mkdir()
        latest = last_completed_session(NOW)
        sessions = sessions_between(latest - pd.Timedelta(days=600), latest)[-66:]
        values = [100.0 + index / 100 for index in range(66)]
        frame = pd.DataFrame({"Open": [v - .2 for v in values], "High": [v + .3 for v in values],
            "Low": [v - .4 for v in values], "Close": values, "Volume": [1_000_000 + index for index in range(66)]}, index=pd.DatetimeIndex(sessions))
        if raw_parent:
            schedule = _nyse_calendar().schedule(start_date=sessions[0], end_date=sessions[-1])
            def source(url, limit):
                ticker = url.split("/chart/")[1].split("?")[0]
                quote = {lower: frame[title].tolist() for lower, title in (("open", "Open"), ("high", "High"), ("low", "Low"), ("close", "Close"), ("volume", "Volume"))}
                body = json.dumps({"chart": {"result": [{"meta": {"symbol": ticker, "currency": "USD",
                    "exchangeTimezoneName": "America/New_York", "exchangeName": "NMS" if ticker == "AAPL" else "PCX",
                    "instrumentType": "EQUITY" if ticker == "AAPL" else "ETF", "dataGranularity": "1d"},
                    "timestamp": [int(stamp.timestamp()) for stamp in schedule.market_open], "indicators": {"quote": [quote]}}], "error": None}}, separators=(",", ":")).encode()
                return {"body": body, "status": 200, "response_url": url, "content_encoding": "identity",
                    "content_type": "application/json", "body_complete_within_byte_budget": True}
            acquisition = CapturedMarketInputAcquisition(archive_root=self.archive, enabled=True,
                observation_clock=lambda: NOW, transport=source)
            report = acquisition.acquire(acquisition.plan(("AAPL", *BENCHMARK_TICKERS)), execute=True)
            self.assertEqual(("READY", True), (report["status"], report["fixture_only"]))
            self.manifest = self.archive / report["manifest_path"]
            return report
        entries = []
        for ticker in dict.fromkeys((*tickers, *BENCHMARK_TICKERS)):
            record = capture_provider_frame(ticker, frame, started_at=NOW - timedelta(minutes=2), observed_at=NOW - timedelta(minutes=1))
            record["evidence_origin"] = "DETERMINISTIC_TEST_FIXTURE"
            record["capture_id"] = canonical_hash({key: value for key, value in record.items() if key != "capture_id"})
            path = persist_observation(record, self.archive / "selected")
            entries.append(build_captured_market_data_entry(archive_root=self.archive, source_archive_path=path.relative_to(self.archive).as_posix()))
        self.manifest.write_text(json.dumps({"schema_version": MANIFEST_SCHEMA_VERSION, "instruments": entries}) + "\n", encoding="utf-8")

    def assert_nonacceptance(self, record):
        self.assertIs(False, record["native_windows_selected_analysis_verified"])
        for key in ("user_windows_end_to_end_verified", "full_specialist_acceptance", "strict_pit_eligible", "historical_OOS_verified", "fitter_or_trading_invoked"):
            self.assertIs(False, record[key])
        for key in ("source_HTTP_requests", "forecasts_created", "registrations_created", "labels_created"):
            self.assertEqual(0, record[key])

    def late(self, callback):
        fsync, hits = module.os.fsync, []
        def mutate(descriptor):
            fsync(descriptor)
            if not hits:
                hits.append(True)
                callback()
        with patch.object(module.os, "fsync", side_effect=mutate):
            result = self.capture()
        self.assertEqual([True], hits)
        self.assertIn("POST_FSYNC_PUBLICATION_REVALIDATION_FAILED", result["receipt"]["qualification_reasons"])
        self.assertIs(False, result["native_windows_selected_analysis_verified"])
        self.assertEqual([], list(self.output.parent.glob(".user_windows_analysis_*.tmp")))
        return result["receipt"]

    def test_default_disabled_reads_and_writes_nothing(self):
        def fail(*args, **kwargs):
            self.fail("Disabled recorder inspected execution state")
        with patch.object(module, "_code_pins", side_effect=fail), patch.object(module, "CapturedMarketDataProvider", side_effect=fail), patch.object(module.sqlite3, "connect", side_effect=fail):
            result = self.capture(recorder=UserWindowsAnalysisCapture(runtime_probe=fail, observation_clock=fail))
        self.assertEqual(("DISABLED", False, False), (result["status"], result["receipt_created"], result["pipeline_invoked"]))
        self.assertFalse(self.root.joinpath("receipts").exists())
        self.assertFalse(self.db.exists())

    def test_enable_scope_and_unbounded_iterator_are_strict_before_inputs(self):
        for value in (1, "true", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                UserWindowsAnalysisCapture(enabled=value)
        for scope in ([], ["SPY"], ["AAPL", "AAPL"], [None], ["aapl"]):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                self.capture(tickers=scope)
        hits = []
        def endless():
            while True:
                hits.append(1)
                yield "AAPL"
        with self.assertRaises(ValueError):
            self.capture(tickers=endless())
        self.assertEqual(688, len(hits))
        self.assertFalse(self.output.exists())

    def test_native_missing_inputs_are_os_neutral_and_never_initialize_pipeline_or_database(self):
        facts = _runtime_facts()
        with patch.object(module.sqlite3, "connect", side_effect=AssertionError("No DB on refusal or missing inputs")):
            result = self.capture(recorder=UserWindowsAnalysisCapture(enabled=True))
        expected = "UNSUPPORTED_PLATFORM" if facts["os_system"] != "Windows" else "HOSTED_CI_REFUSED" if facts["ci_detected"] else "WAIT_DATA"
        self.assertEqual(expected, result["status"])
        self.assertFalse(result["pipeline_invoked"])
        self.assertIsNone(result["receipt"]["database_before"])
        self.assertFalse(self.db.exists())
        self.assert_nonacceptance(result["receipt"])

    def test_explicit_hosted_windows_and_linux_gates_refuse_before_source_or_sql_reads(self):
        for index, facts in enumerate((fixture_runtime(hosted=True), fixture_runtime(system="Linux"))):
            with self.subTest(facts=facts["os_system"]), patch.object(module, "CapturedMarketDataProvider", side_effect=AssertionError("Gate precedes source inspection")), patch.object(module, "_read_runs", side_effect=AssertionError("Gate precedes DB inspection")):
                result = self.capture(recorder=self.recorder(runtime_probe=lambda: facts), output_path=self.root / f"refusal-{index}.json")
            self.assertFalse(result["pipeline_invoked"])
            self.assertIsNone(result["receipt"]["market_preflight"])
            self.assert_nonacceptance(result["receipt"])
        self.assertFalse(self.calls)

    def test_injected_runtime_or_clock_needs_explicit_fixture_pipeline_and_never_claims_native(self):
        result = self.capture(recorder=self.recorder(pipeline_factory=None))
        self.assertEqual("FIXTURE_CONTROLS_REFUSED", result["status"])
        self.assertTrue(result["receipt"]["fixture_only"])
        self.assertFalse(result["pipeline_invoked"])

    def test_missing_any_benchmark_stops_before_pipeline_and_db_initialization(self):
        self.market_fixture()
        manifest = json.loads(self.manifest.read_text())
        manifest["instruments"] = [row for row in manifest["instruments"] if row["ticker"] != "XLY"]
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        with patch.object(module.sqlite3, "connect", side_effect=AssertionError("Preflight must precede SQL")):
            result = self.capture()
        self.assertEqual(("WAIT_DATA", False), (result["status"], result["pipeline_invoked"]))
        self.assertEqual((13, 12), (result["receipt"]["market_preflight"]["requested_count"], result["receipt"]["market_preflight"]["usable_count"]))
        self.assertFalse(self.calls)
        self.assertFalse(self.db.exists())

    def test_fixture_execution_uses_real_provider_and_exact_sqlite_persistence_with_unchanged_model_defaults(self):
        self.market_fixture()
        result = self.capture()
        record = result["receipt"]
        self.assertEqual(("FIXTURE_ONLY", True, [1]), (result["status"], result["pipeline_invoked"], record["saved_run_ids_returned_by_store"]))
        self.assertEqual((1, 1), (record["database"]["new_run_count"], record["database"]["signal_count"]))
        self.assertEqual("AAPL", record["database"]["signals"][0]["ticker"])
        self.assertEqual(100.0, record["database"]["signals"][0]["current_price"])
        self.assertEqual("EXPLICIT_SYNTHETIC_TEST_EXECUTION", record["evidence_origin"])
        self.assertTrue(record["configuration"]["model_settings_match_defaults"])
        flags = record["configuration"]["runtime_flags"]
        self.assertFalse(any(flags[key] for key in ("rss_enabled", "mt5_enabled", "yahoo_metadata_enabled", "prospective_history_enabled", "persistent_market_cache_allowed")))
        self.assertTrue(flags["agent_shadow_mode"])
        self.assertIsNone(flags["prospective_audit_archive_root"])
        self.assertEqual([], self.calls[0]["kwargs"]["rss_sources"])
        self.assert_nonacceptance(record)

    def test_reader_connections_are_ro_closed_and_cannot_mutate_persisted_bytes(self):
        self.market_fixture()
        opened = []
        class ReadConnection(sqlite3.Connection):
            closed = False
            def close(inner):
                inner.closed = True
                return super().close()
        def connect(*args, **kwargs):
            if kwargs.get("uri"):
                self.assertTrue(args[0].endswith("?mode=ro"))
                kwargs["factory"] = ReadConnection
            value = NATIVE_CONNECT(*args, **kwargs)
            if kwargs.get("uri"):
                opened.append(value)
            return value
        with patch.object(module.sqlite3, "connect", side_effect=connect):
            result = self.capture()
        self.assertTrue(opened)
        self.assertTrue(all(connection.closed for connection in opened))
        state = result["receipt"]["database"]
        self.assertEqual(state["file_state"], state["file_state_after"])
        self.assertFalse(any(Path(str(self.db) + suffix).exists() for suffix in ("-journal", "-wal", "-shm")))

    def test_existing_journal_is_pinned_and_blocks_sql_open_without_replay_or_removal(self):
        self.market_fixture()
        self.db.parent.mkdir()
        self.db.write_bytes(b"SYNTHETIC_NONOPENED_EXISTING_DB")
        journal = Path(str(self.db) + "-journal")
        journal.write_bytes(b"SYNTHETIC_EXISTING_JOURNAL")
        before = self.db.read_bytes(), journal.read_bytes()
        with patch.object(module.sqlite3, "connect", side_effect=AssertionError("Never replay journal")):
            result = self.capture()
        self.assertEqual(("WAIT_DATABASE", False), (result["status"], result["pipeline_invoked"]))
        self.assertEqual("WAIT_ACTIVE_DATABASE_JOURNAL", result["receipt"]["database_before"]["status"])
        self.assertEqual(before, (self.db.read_bytes(), journal.read_bytes()))

    def test_progress_callback_source_change_is_refused_before_main_save(self):
        self.market_fixture()
        def change(_):
            self.manifest.write_bytes(self.manifest.read_bytes() + b" ")
        record = self.capture(progress_callback=change)["receipt"]
        self.assertTrue(record["pipeline_invoked"])
        self.assertEqual([], record["saved_run_ids_returned_by_store"])
        self.assertFalse(self.db.exists())
        self.assertIn("ANALYSIS_CAPTURED_INPUT_CHANGED", record["qualification_reasons"])
        self.assert_nonacceptance(record)

    def test_scope_change_before_main_save_does_not_persist_a_foreign_ticker(self):
        self.market_fixture()
        self.selected_rows = ["NVDA"]
        record = self.capture()["receipt"]
        self.assertIn("ANALYSIS_SCOPE_CHANGED_BEFORE_PERSISTENCE", record["qualification_reasons"])
        self.assertEqual(0, record["saved_run_count_returned_by_store"])
        self.assertFalse(self.db.exists())

    def test_partial_selected_run_retains_exact_saved_subset_and_fails_completion(self):
        self.market_fixture(tickers=("AAPL", "NVDA"))
        self.selected_rows = ["AAPL"]
        record = self.capture(tickers=["AAPL", "NVDA"])["receipt"]
        self.assertEqual((1, 1), (record["database"]["new_run_count"], record["database"]["signal_count"]))
        self.assertEqual(["AAPL"], [row["ticker"] for row in record["database"]["signals"]])
        self.assertIn("INCOMPLETE_OR_FAILED_SELECTED_RUN", record["qualification_reasons"])
        self.assert_nonacceptance(record)

    def test_failure_after_actual_temp_commit_retains_run_id_values_and_count(self):
        self.market_fixture()
        self.raise_after_save = True
        record = self.capture()["receipt"]
        self.assertEqual([1], record["saved_run_ids_returned_by_store"])
        self.assertEqual((1, 1), (record["database"]["new_run_count"], record["database"]["signal_count"]))
        self.assertIn("SYNTHETIC_FAILURE_AFTER_COMMIT", record["qualification_reasons"])
        self.assertIn("PRODUCTION_PIPELINE_RESULT_NOT_COMPLETE", record["qualification_reasons"])

    def test_restarting_append_run_never_adopts_old_run_as_the_new_same_process_run(self):
        self.market_fixture()
        first = self.capture()
        original = self.output.read_bytes()
        second = self.capture(output_path=self.output.parent / "second.json")["receipt"]
        self.assertEqual([2], second["database"]["new_run_ids"])
        self.assertEqual([2], second["saved_run_ids_returned_by_store"])
        self.assertEqual(1, second["database_before"]["latest_run_id"])
        self.assertNotEqual(first["receipt"]["session_id"], second["session_id"])
        self.assertEqual(original, self.output.read_bytes())

    def test_returned_result_values_modified_after_save_are_not_qualified(self):
        self.market_fixture()
        def changed(result):
            result["signals"].loc[0, "current_price"] = 999.0
            return result
        self.return_transform = changed
        record = self.capture()["receipt"]
        self.assertEqual(100.0, record["database"]["signals"][0]["current_price"])
        self.assertIn("RETURNED_RESULT_OR_PHYSICAL_RUN_CLOCK_BINDING_NOT_VERIFIED", record["qualification_reasons"])

    def test_changed_saved_values_are_reported_exactly_and_fail_original_save_payload_binding(self):
        self.market_fixture()
        def change(_, kwargs, run_id):
            with closing(NATIVE_CONNECT(self.db)) as connection:
                connection.execute("UPDATE signal_history SET current_price=321 WHERE run_id=?", (run_id,))
                connection.commit()
        self.after_save = change
        record = self.capture()["receipt"]
        self.assertEqual(321.0, record["database"]["signals"][0]["current_price"])
        self.assertIn("PERSISTED_VALUES_DIFFER_FROM_EXACT_PIPELINE_SAVE_PAYLOAD", record["qualification_reasons"])

    def test_saved_run_times_cannot_be_backdated_even_when_values_match(self):
        self.market_fixture()
        def changed(result):
            yesterday = NOW - timedelta(days=1)
            result["metadata"].started_at = result["metadata"].finished_at = yesterday
            with closing(NATIVE_CONNECT(self.db)) as connection:
                connection.execute("UPDATE runs SET started_at=?,finished_at=?", (yesterday.isoformat(), yesterday.isoformat()))
                connection.commit()
            return result
        self.return_transform = changed
        record = self.capture()["receipt"]
        self.assertIn("RETURNED_RESULT_OR_PHYSICAL_RUN_CLOCK_BINDING_NOT_VERIFIED", record["qualification_reasons"])
        self.assertEqual((NOW - timedelta(days=1)).isoformat(), record["database"]["runs"][0]["started_at"])

    def test_constructor_and_run_network_fallback_attempts_are_blocked_without_exporting_values(self):
        self.market_fixture()
        def attempted(config):
            import requests
            requests.get("https://invalid.test/no-network")
            return self.pipeline_factory(config)
        record = self.capture(recorder=self.recorder(pipeline_factory=attempted))["receipt"]
        self.assertEqual(1, record["blocked_network_attempts"])
        self.assertFalse(record["pipeline_invoked"])
        self.assertFalse(self.db.exists())
        self.assert_nonacceptance(record)

    def test_rss_and_mt5_clients_are_unusable_even_if_pipeline_ignores_false_flags(self):
        self.market_fixture()
        def attempted(pipeline, kwargs):
            pipeline.mt5_client.load_watchlist()
        self.before_save = attempted
        record = self.capture()["receipt"]
        self.assertIn("OFFLINE_ANALYSIS_RSS_OR_MT5_REFUSED", record["qualification_reasons"])
        self.assertEqual(0, record["saved_run_count_returned_by_store"])

    def test_secret_bearing_saved_result_is_wholly_withheld_without_sanitized_subset_export(self):
        self.market_fixture()
        sentinel = "api_key=synthetic-not-real-private-value"
        def change(_, kwargs, run_id):
            with closing(NATIVE_CONNECT(self.db)) as connection:
                connection.execute("UPDATE signal_history SET reasons=? WHERE run_id=?", (sentinel, run_id))
                connection.commit()
        self.after_save = change
        record = self.capture()["receipt"]
        self.assertEqual("WITHHELD_RESULT_ROWS", record["database"]["status"])
        self.assertEqual((1, 1), (record["database"]["new_run_count"], record["database"]["signal_count"]))
        self.assertEqual(([], []), (record["database"]["runs"], record["database"]["signals"]))
        self.assertNotIn(sentinel, self.output.read_text())

    def test_existing_output_or_input_collision_is_refused_without_any_overwrite(self):
        self.output.parent.mkdir()
        original = b"immutable previous receipt\r\n"
        self.output.write_bytes(original)
        with self.assertRaises(FileExistsError):
            self.capture()
        self.assertEqual(original, self.output.read_bytes())
        with self.assertRaises(ValueError):
            self.capture(output_path=self.archive / "receipt.json")
        self.assertFalse(self.db.exists())

    def test_manifest_and_database_hardlinks_refuse_before_any_pipeline_execution(self):
        self.market_fixture()
        alias = self.archive / "manifest-alias.json"
        os.link(self.manifest, alias)
        first = self.capture()["receipt"]
        self.assertFalse(first["pipeline_invoked"])
        self.assertIn("ANALYSIS_CAPTURE_HARDLINK_REFUSED", first["qualification_reasons"])
        alias.unlink()
        self.db.parent.mkdir()
        self.db.write_bytes(b"SYNTHETIC_HARDLINK_DB")
        other = self.root / "db-alias.db"
        os.link(self.db, other)
        original = self.db.read_bytes()
        second = self.capture(output_path=self.output.parent / "hardlink-db.json")["receipt"]
        self.assertFalse(second["pipeline_invoked"])
        self.assertEqual(original, self.db.read_bytes())

    def test_canonical_temp_alias_and_symlink_guard_require_a_real_exact_target_hit(self):
        (self.root / "alias-child").mkdir()
        lexical = self.root / "alias-child" / ".."
        self.assertNotEqual(lexical, lexical.resolve())
        target = lexical.resolve() / "receipts"
        hits = []
        original = Path.is_symlink
        def guarded(path):
            if path == target:
                hits.append(path)
                return True
            return original(path)
        with patch.object(Path, "is_symlink", guarded), self.assertRaises(ValueError):
            self.capture(output_path=target / "alias.json")
        self.assertGreater(len(hits), 0)
        self.assertFalse(self.db.exists())

    def test_physical_rollback_before_save_blocks_writes_and_marks_clock_failure(self):
        self.market_fixture()
        self.before_save = lambda _, kwargs: self.clock.__setitem__(0, NOW - timedelta(seconds=1))
        record = self.capture()["receipt"]
        self.assertIn("ANALYSIS_PHYSICAL_CLOCK_ROLLBACK", record["qualification_reasons"])
        self.assertFalse(record["clocks"]["integrity_verified"])
        self.assertEqual(0, record["saved_run_count_returned_by_store"])
        self.assertFalse(self.db.exists())

    def test_late_manifest_tamper_after_receipt_fsync_preserves_saved_subset_unqualified(self):
        self.market_fixture()
        record = self.late(lambda: self.manifest.write_bytes(self.manifest.read_bytes() + b" "))
        self.assertEqual((1, 1), (record["saved_run_count_returned_by_store"], record["database"]["signal_count"]))

    def test_late_whole_raw_parent_tamper_after_fsync_is_replayed_before_publication(self):
        report = self.market_fixture(raw_parent=True)
        parent = self.archive / report["rows"][0]["raw_path"]
        original = parent.read_bytes()
        record = self.late(lambda: parent.write_bytes(original[:-1] + b" "))
        self.assertEqual(1, record["saved_run_count_returned_by_store"])
        self.assertNotEqual(original, parent.read_bytes())

    def test_late_database_change_is_reobserved_with_true_committed_count_and_values(self):
        self.market_fixture()
        def change():
            with closing(NATIVE_CONNECT(self.db)) as connection:
                connection.execute("UPDATE signal_history SET current_price=432")
                connection.commit()
        record = self.late(change)
        self.assertEqual((1, 1), (record["saved_run_count_returned_by_store"], record["database"]["signal_count"]))
        self.assertEqual(432.0, record["database"]["signals"][0]["current_price"])

    def test_late_journal_never_replays_and_retains_confirmed_store_id_with_unknown_current_counts(self):
        self.market_fixture()
        journal = Path(str(self.db) + "-journal")
        record = self.late(lambda: journal.write_bytes(b"SYNTHETIC_LATE_JOURNAL"))
        self.assertEqual([1], record["saved_run_ids_returned_by_store"])
        self.assertEqual("WAIT_ACTIVE_DATABASE_JOURNAL", record["database"]["status"])
        self.assertIsNone(record["database"]["signal_count"])
        self.assertEqual(b"SYNTHETIC_LATE_JOURNAL", journal.read_bytes())

    def test_late_wall_and_monotonic_rollback_cannot_publish_verified_run(self):
        self.market_fixture()
        record = self.late(lambda: self.clock.__setitem__(0, NOW - timedelta(seconds=1)))
        self.assertFalse(record["clocks"]["integrity_verified"])
        self.assertEqual(1, record["saved_run_count_returned_by_store"])

    def test_completion_after_new_nyse_close_refuses_stale_inputs_without_moving_capture_clocks(self):
        self.market_fixture()
        record = self.late(lambda: self.clock.__setitem__(0, datetime(2026, 10, 19, 20, tzinfo=timezone.utc)))
        self.assertEqual(1, record["database"]["signal_count"])
        self.assertEqual(NOW.isoformat(), record["input_pins"] and record["market_preflight"]["as_of"])

    def test_loader_checks_hash_but_never_promotes_resealed_stored_claims_to_execution_proof(self):
        self.market_fixture()
        record = self.capture()["receipt"]
        record = deepcopy(record)
        record["native_windows_selected_analysis_verified"] = True
        record["fixture_only"] = False
        record["capture_id"] = canonical_hash({key: value for key, value in record.items() if key != "capture_id"})
        path = self.output.parent / "stored-claim.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        loaded = load_user_windows_analysis_receipt(path)
        self.assertTrue(loaded["record"]["native_windows_selected_analysis_verified"])
        self.assertFalse(loaded["native_windows_selected_analysis_verified"])
        self.assertFalse(loaded["runtime_execution_reverified"])
        self.assertTrue(loaded["loaded_json_is_not_execution_proof"])

    def test_loader_refuses_tampering_duplicate_keys_and_inventory_acceptance_claims(self):
        record = self.capture()["receipt"]
        for index, key in enumerate(("full_specialist_acceptance", "strict_pit_eligible", "historical_OOS_verified", "user_windows_end_to_end_verified")):
            changed = deepcopy(record)
            changed[key] = True
            changed["capture_id"] = canonical_hash({name: value for name, value in changed.items() if name != "capture_id"})
            path = self.root / f"forged-{index}.json"
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.subTest(key=key), self.assertRaises(ValueError):
                load_user_windows_analysis_receipt(path)
        duplicate = self.root / "duplicate.json"
        duplicate.write_text('{"scope":"a","scope":"a"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            load_user_windows_analysis_receipt(duplicate)

    def test_runtime_export_contains_fixed_ci_booleans_only_and_no_environment_values(self):
        sentinel = "synthetic-private-value-never-export"
        with patch.dict(os.environ, {"CI": sentinel, "GITHUB_ACTIONS": "true", "CUSTOM_PRIVATE_TEST_VALUE": sentinel}):
            result = self.capture(recorder=UserWindowsAnalysisCapture(enabled=True))
        self.assertNotIn(sentinel, self.output.read_text())
        self.assertNotIn("CUSTOM_PRIVATE_TEST_VALUE", self.output.read_text())
        self.assertTrue(all(type(value) is bool for value in result["receipt"]["runtime"]["hosted_ci_indicators"].values()))


if __name__ == "__main__":
    unittest.main()
