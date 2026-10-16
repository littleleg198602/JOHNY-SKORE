"""Synthetic offline fixtures; none of these forecasts are live acceptance."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from market_checker_app.config import AppConfig
from market_checker_app.prediction_contract import build_point_in_time_snapshot, canonical_hash
from market_checker_app.services.pipeline_service import PipelineService
from market_checker_app.services.prospective_forecast_registration_adapter import (
    MANIFEST_FILENAME, MANIFEST_SCHEMA_VERSION, ProspectiveForecastRegistrationAdapter,
)
from market_checker_app.services.prospective_specialist_history import read_stored_prediction_snapshot
from market_checker_app.services.specialist_label_availability_service import persist_label_observation
from market_checker_app.services.us_equity_calendar_service import last_completed_session, sessions_between
from market_checker_app.services.yahoo_price_provenance_service import capture_provider_frame, persist_observation
from market_checker_app.storage.sqlite_store import SQLiteStore
from market_checker_app.weekly_shadow_runner import _parser, finalize_analysis_run
from tests.test_batch6_prospective_specialist_history import (
    PREDICTION_AT, REGISTERED_AT, _fixture, _resolved_fixture, _seal_observation,
)


MODULE = "market_checker_app.services.prospective_forecast_registration_adapter"
NORMAL_CLOCK = datetime(2026, 10, 15, 22, tzinfo=timezone.utc)


def _manifest(root, *, sources, labels=()):
    record = {"schema_version": MANIFEST_SCHEMA_VERSION, "forecast_sources": list(sources),
              "label_observations": list(labels)}
    (Path(root) / MANIFEST_FILENAME).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def _entry(root, ticker, paths, benchmark="SPY"):
    return {"ticker": ticker, "benchmark_ticker": benchmark,
            "source_archive_paths": [path.relative_to(root).as_posix() for path in paths]}


class Batch7ForecastRegistrationAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store, self.snapshot, self.sources = _fixture(self.root)
        self.identifier = self.snapshot["snapshot_id"]
        self.entry = _entry(self.root, "AAPL", self.sources)
        _manifest(self.root, sources=[self.entry])

    def adapter(self, clock=REGISTERED_AT, **kwargs):
        return ProspectiveForecastRegistrationAdapter(db_path=self.store.db_path,
            audit_archive_root=self.root, observation_clock=lambda: clock, **kwargs)

    def test_only_explicit_paths_are_used_and_no_glob_or_network_is_attempted(self):
        with patch.object(Path, "glob", side_effect=AssertionError("Implicit source glob")), \
             patch.object(Path, "rglob", side_effect=AssertionError("Implicit source glob")), \
             patch("socket.socket.connect", side_effect=AssertionError("Unexpected HTTP")):
            report = self.adapter().register_after_persistence([self.identifier])
        self.assertEqual(("RECORDED", 1), (report["status"], report["registered_count"]))
        self.assertTrue(report["rows"][0]["fixture_only"])
        self.assertEqual(REGISTERED_AT.isoformat(), report["rows"][0]["registered_at"])
        self.assertFalse(report["source_discovery_performed"])
        self.assertEqual(0, report["forecasts_created"])
        self.assertEqual(1, len(self.store.read_prediction_snapshots()))

    def test_settings_and_exact_manifest_bytes_are_pinned_outside_the_forecast(self):
        before = read_stored_prediction_snapshot(self.store.db_path, self.identifier)
        report = self.adapter().register_after_persistence([self.identifier])
        path = self.root / "prospective_specialist_history/adapter_audits" / f"settings_{report['settings_audit_id']}.json"
        record = json.loads(path.read_text())
        raw = (self.root / MANIFEST_FILENAME).read_bytes()
        self.assertEqual(raw, record["manifest_raw_utf8"].encode())
        self.assertEqual(hashlib.sha256(raw).hexdigest(), report["manifest_sha256"])
        self.assertEqual(REGISTERED_AT.isoformat(), record["observed_at"])
        self.assertEqual(str(self.root.resolve()), record["settings"]["prospective_audit_archive_root"])
        self.assertEqual(before, read_stored_prediction_snapshot(self.store.db_path, self.identifier))

    def test_restart_is_idempotent_and_preserves_original_physical_registration(self):
        first = self.adapter().register_after_persistence([self.identifier])
        later = self.adapter(REGISTERED_AT + timedelta(days=7)).register_after_persistence([self.identifier])
        self.assertEqual(1, later["registration_replay_count"])
        self.assertEqual(first["rows"][0]["registration_hash"], later["rows"][0]["registration_hash"])
        self.assertEqual(first["rows"][0]["binding_hash"], later["rows"][0]["binding_hash"])
        self.assertEqual(first["settings_audit_id"], later["settings_audit_id"])
        self.assertEqual(REGISTERED_AT.isoformat(), later["rows"][0]["registered_at"])

    def test_changed_safe_manifest_has_a_separate_audit_without_rewriting_the_forecast(self):
        first = self.adapter().register_after_persistence([self.identifier])
        path = self.root / MANIFEST_FILENAME
        path.write_bytes(path.read_bytes() + b"  \n")
        second = self.adapter(REGISTERED_AT + timedelta(seconds=1)).register_after_persistence([self.identifier])
        self.assertNotEqual(first["settings_audit_id"], second["settings_audit_id"])
        self.assertEqual(first["rows"][0]["registration_hash"], second["rows"][0]["registration_hash"])

    def test_missing_root_manifest_or_source_is_a_wait_without_an_implicit_collection(self):
        missing_root = ProspectiveForecastRegistrationAdapter(db_path=self.store.db_path, audit_archive_root=None)
        self.assertEqual("PROSPECTIVE_AUDIT_ROOT_NOT_CONFIGURED", missing_root.register_after_persistence([self.identifier])["reason"])
        (self.root / MANIFEST_FILENAME).unlink()
        self.assertEqual("MISSING_EXPLICIT_SOURCE_MANIFEST", self.adapter().register_after_persistence([self.identifier])["reason"])
        _manifest(self.root, sources=[self.entry])
        self.sources[0].unlink()
        report = self.adapter().register_after_persistence([self.identifier])
        self.assertEqual("EXPLICIT_FORECAST_SOURCE_ARCHIVE_UNAVAILABLE", report["rows"][0]["reason"])
        self.assertFalse((self.root / "prospective_specialist_history/forecasts").exists())

    def test_forecast_and_source_clocks_are_not_repaired_by_using_as_of(self):
        for clock, reason in ((PREDICTION_AT - timedelta(seconds=1), "FORECAST_OBSERVATION_AFTER_PHYSICAL_REGISTRATION"),
                (REGISTERED_AT + timedelta(days=2), "FORECAST_NOT_PROSPECTIVE_FOR_EXACT_WINDOW")):
            with self.subTest(clock=clock):
                report = self.adapter(clock).register_after_persistence([self.identifier])
                self.assertEqual(reason, report["rows"][0]["reason"])
        self.assertFalse((self.root / "prospective_specialist_history/forecasts").exists())

    def test_naive_clock_is_a_truthful_wait(self):
        report = self.adapter(datetime(2026, 10, 12, 22)).register_after_persistence([self.identifier])
        self.assertEqual("INVALID_HISTORY_KNOWLEDGE_CLOCK", report["reason"])
        self.assertEqual(0, report["registered_count"])

    def test_physical_clock_rollback_cannot_make_a_late_manifest_prospective(self):
        opened = datetime(2026, 10, 13, 13, 30, tzinfo=timezone.utc)
        clocks = iter([opened + timedelta(seconds=1), opened + timedelta(seconds=1),
            opened - timedelta(seconds=1), opened + timedelta(seconds=2)])
        adapter = ProspectiveForecastRegistrationAdapter(db_path=self.store.db_path,
            audit_archive_root=self.root, observation_clock=lambda: next(clocks))
        report = adapter.register_after_persistence([self.identifier])
        self.assertEqual(("WAIT", 0), (report["status"], report["registered_count"]))
        self.assertEqual("PROSPECTIVE_PHYSICAL_CLOCK_ROLLBACK", report["rows"][0]["reason"])
        self.assertFalse((self.root / "prospective_specialist_history/forecasts").exists())

    def test_restart_cannot_replace_saved_settings_observation_with_an_earlier_clock(self):
        first = self.adapter().register_after_persistence([self.identifier])
        forecast_path = self.root / "prospective_specialist_history/forecasts" / f"forecast_{self.identifier}.json"
        before = forecast_path.read_bytes()
        replay = self.adapter(REGISTERED_AT - timedelta(seconds=1)).register_after_persistence([self.identifier])
        self.assertEqual("AUDIT_SETTINGS_OBSERVATION_AFTER_PHYSICAL_READ", replay["reason"])
        self.assertEqual(0, replay["registered_count"])
        self.assertEqual(before, forecast_path.read_bytes())
        self.assertEqual(REGISTERED_AT.isoformat(), first["rows"][0]["registered_at"])

    def test_malformed_source_clock_preserves_an_earlier_successful_registration(self):
        for clock_field, value in (("observed_at", None), ("captured_at", 123)):
            with self.subTest(field=clock_field):
                root = self.root / clock_field
                store, first, sources = _fixture(root)
                second = build_point_in_time_snapshot(run_id=first["run_id"], ticker="MSFT", observed_at=PREDICTION_AT,
                    feature_payload=first["feature_payload"], baseline_output=first["baseline_output"],
                    provenance=first["provenance"])
                store.save_prediction_snapshots([second])
                malformed = json.loads(sources[0].read_text())
                malformed["ticker"] = "MSFT"
                if value is None:
                    malformed.pop(clock_field)
                else:
                    malformed[clock_field] = value
                malformed["capture_id"] = canonical_hash({key: item for key, item in malformed.items() if key != "capture_id"})
                bad_path = sources[0].parent / f"yahoo_price_{malformed['capture_id']}.json"
                bad_path.write_text(json.dumps(malformed), encoding="utf-8")
                _manifest(root, sources=[_entry(root, "AAPL", sources), _entry(root, "MSFT", [bad_path])])
                def adapter():
                    return ProspectiveForecastRegistrationAdapter(db_path=store.db_path,
                        audit_archive_root=root, observation_clock=lambda: REGISTERED_AT)
                report = adapter().register_after_persistence([first["snapshot_id"], second["snapshot_id"]])
                self.assertEqual(("PARTIAL", 1), (report["status"], report["registered_count"]))
                self.assertEqual("MALFORMED_PROSPECTIVE_ARCHIVE_RECORD", report["rows"][1]["reason"])
                forecast_path = root / "prospective_specialist_history/forecasts" / f"forecast_{first['snapshot_id']}.json"
                saved = forecast_path.read_bytes()
                replay = adapter().register_after_persistence([first["snapshot_id"], second["snapshot_id"]])
                self.assertEqual(("PARTIAL", 1), (replay["status"], replay["registration_replay_count"]))
                self.assertEqual(saved, forecast_path.read_bytes())
                self.assertFalse((forecast_path.parent / f"forecast_{second['snapshot_id']}.json").exists())

    def test_missing_stored_prediction_is_never_created(self):
        before = self.store.read_prediction_snapshots().copy(deep=True)
        report = self.adapter().register_after_persistence(["a" * 64])
        self.assertEqual("STORED_FORECAST_NOT_FOUND", report["rows"][0]["reason"])
        pd.testing.assert_frame_equal(before, self.store.read_prediction_snapshots())

    def test_bounded_batch_is_partial_with_a_lower_bound_count(self):
        report = self.adapter(maximum_forecasts=1).register_after_persistence([self.identifier, "a" * 64, "b" * 64])
        self.assertEqual("PARTIAL", report["status"])
        self.assertEqual((1, 1), (report["registered_count"], report["deferred_at_least"]))
        self.assertTrue(report["request_count_is_lower_bound"])
        self.assertEqual(1, len(report["rows"]))

    def test_unsafe_whole_manifest_is_withheld_before_any_archive_is_published(self):
        record = _manifest(self.root, sources=[self.entry])
        record["access_token"] = "synthetic-fixture-only"
        (self.root / MANIFEST_FILENAME).write_text(json.dumps(record), encoding="utf-8")
        report = self.adapter().register_after_persistence([self.identifier])
        self.assertEqual("WITHHELD_HISTORY_ACCESS_VALUE", report["reason"])
        self.assertFalse((self.root / "prospective_specialist_history").exists())

    def test_manifest_cannot_escape_the_configured_root(self):
        self.entry["source_archive_paths"] = ["../yahoo_price_" + "a" * 64 + ".json"]
        _manifest(self.root, sources=[self.entry])
        report = self.adapter().register_after_persistence([self.identifier])
        self.assertEqual("EXPLICIT_ARCHIVE_PATH_OUTSIDE_ROOT", report["reason"])
        self.assertFalse((self.root / "prospective_specialist_history").exists())

    def test_foreign_price_archive_cannot_be_assigned_to_another_forecast(self):
        frame = pd.DataFrame({"Close": [100.0]}, index=pd.to_datetime(["2026-10-12"], utc=True))
        foreign = persist_observation(capture_provider_frame("MSFT", frame,
            started_at=PREDICTION_AT - timedelta(minutes=1), observed_at=PREDICTION_AT - timedelta(seconds=1)), self.root / "fixture_sources")
        _manifest(self.root, sources=[_entry(self.root, "AAPL", [foreign])])
        report = self.adapter().register_after_persistence([self.identifier])
        self.assertEqual("FOREIGN_PRICE_SOURCE_ARCHIVE", report["rows"][0]["reason"])
        self.assertFalse((self.root / "prospective_specialist_history/forecasts").exists())

    def test_later_capture_is_not_backdated_to_its_older_market_session(self):
        frame = pd.DataFrame({"Close": [100.0]}, index=pd.to_datetime(["2026-10-12"], utc=True))
        later = persist_observation(capture_provider_frame("AAPL", frame,
            started_at=PREDICTION_AT, observed_at=PREDICTION_AT + timedelta(seconds=1)), self.root / "fixture_sources")
        _manifest(self.root, sources=[_entry(self.root, "AAPL", [later])])
        report = self.adapter().register_after_persistence([self.identifier])
        self.assertEqual("FORECAST_SOURCE_AVAILABLE_AFTER_PREDICTION", report["rows"][0]["reason"])
        self.assertFalse((self.root / "prospective_specialist_history/forecasts").exists())

    def test_binding_failure_preserves_the_registered_forecast_and_restart_finishes_it(self):
        from market_checker_app.services import prospective_forecast_registration_adapter as adapter_module
        original_publish = adapter_module._publish
        def publish(record, path, **kwargs):
            if path.name.startswith("binding_"):
                raise OSError("synthetic binding failure")
            return original_publish(record, path, **kwargs)
        with patch(MODULE + "._publish", side_effect=publish):
            first = self.adapter().register_after_persistence([self.identifier])
        self.assertEqual(("PARTIAL", 1), (first["status"], first["registered_count"]))
        self.assertEqual("REGISTRATION_SAVED_BINDING_PARTIAL", first["rows"][0]["status"])
        later = self.adapter(REGISTERED_AT + timedelta(seconds=1)).register_after_persistence([self.identifier])
        self.assertEqual(("RECORDED", 1), (later["status"], later["registration_replay_count"]))
        self.assertEqual(REGISTERED_AT.isoformat(), later["rows"][0]["registered_at"])

    def test_later_exact_label_attachment_is_explicit_and_uses_the_later_clock(self):
        self.adapter().register_after_persistence([self.identifier])
        _, observation, path, clock = _resolved_fixture(self.store, self.identifier, self.root)
        _manifest(self.root, sources=[self.entry], labels=[{"snapshot_id": self.identifier,
            "path": path.relative_to(self.root).as_posix()}])
        adapter = self.adapter(clock + timedelta(days=2))
        outcome = adapter.attach_explicit_labels()
        self.assertEqual(1, outcome["attached_count"])
        self.assertEqual((clock + timedelta(days=2)).isoformat(), outcome["rows"][0]["usable_at"])
        replay = adapter.attach_explicit_labels()
        self.assertEqual(1, replay["attachment_replay_count"])
        self.assertEqual(0, adapter.read_history_report()["eligible_real_label_count"])
        self.assertEqual(1, adapter.read_history_report()["eligible_fixture_label_count"])

    def test_unknown_publisher_revision_is_not_upgraded_by_the_adapter(self):
        self.adapter().register_after_persistence([self.identifier])
        _, observation, _, clock = _resolved_fixture(self.store, self.identifier, self.root)
        for capture in observation["price_captures"].values():
            capture["publisher_revision_timing_status"] = "UNKNOWN"
        path = persist_label_observation(_seal_observation(observation), self.root / "fixture_labels")
        _manifest(self.root, sources=[self.entry], labels=[{"snapshot_id": self.identifier,
            "path": path.relative_to(self.root).as_posix()}])
        report = self.adapter(clock).attach_explicit_labels()
        self.assertEqual("MISSING_DATED_PUBLISHER_PRICE_REVISION", report["rows"][0]["reason"])
        self.assertFalse((self.root / "prospective_specialist_history/labels").exists())

    def test_a_future_sidecar_cannot_be_attached_at_an_earlier_label_clock(self):
        self.adapter().register_after_persistence([self.identifier])
        _, observation, path, _ = _resolved_fixture(self.store, self.identifier, self.root)
        _manifest(self.root, sources=[self.entry], labels=[{"snapshot_id": self.identifier,
            "path": path.relative_to(self.root).as_posix()}])
        report = self.adapter(datetime.fromisoformat(observation["captured_at"]) - timedelta(microseconds=1)).attach_explicit_labels()
        self.assertEqual("LABEL_ARCHIVE_AFTER_REPORT_CUTOFF", report["rows"][0]["reason"])

    def test_label_attachment_cannot_roll_back_its_manifest_observation_clock(self):
        self.adapter().register_after_persistence([self.identifier])
        _, _, path, clock = _resolved_fixture(self.store, self.identifier, self.root)
        _manifest(self.root, sources=[self.entry], labels=[{"snapshot_id": self.identifier,
            "path": path.relative_to(self.root).as_posix()}])
        clocks = iter([clock + timedelta(days=2), clock + timedelta(days=2), clock + timedelta(days=1)])
        adapter = ProspectiveForecastRegistrationAdapter(db_path=self.store.db_path,
            audit_archive_root=self.root, observation_clock=lambda: next(clocks))
        report = adapter.attach_explicit_labels()
        self.assertEqual(("WAIT", 0), (report["status"], report["attached_count"]))
        self.assertEqual("PROSPECTIVE_PHYSICAL_CLOCK_ROLLBACK", report["rows"][0]["reason"])
        self.assertFalse((self.root / "prospective_specialist_history/labels").exists())

    def test_multiple_label_versions_are_not_silently_selected(self):
        _manifest(self.root, sources=[self.entry], labels=[{"snapshot_id": self.identifier,
            "path": f"prediction_label_observations/prediction_label_{self.identifier}_{suffix * 64}.json"} for suffix in ("a", "b")])
        report = self.adapter().register_after_persistence([self.identifier])
        self.assertEqual("AMBIGUOUS_EXPLICIT_LABEL_OBSERVATIONS", report["reason"])
        self.assertFalse((self.root / "prospective_specialist_history").exists())

    def test_cli_audit_is_explicitly_opt_in(self):
        defaults = _parser().parse_args([])
        self.assertIsNone(defaults.register_prospective_history)
        self.assertIsNone(defaults.prospective_audit_archive_root)
        enabled = _parser().parse_args(["--register-prospective-history", "--prospective-audit-archive-root", "fixture-root"])
        self.assertTrue(enabled.register_prospective_history)
        self.assertEqual(Path("fixture-root"), enabled.prospective_audit_archive_root)


class _FrozenClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NORMAL_CLOCK.astimezone(tz) if tz is not None else NORMAL_CLOCK.replace(tzinfo=None)


class _OfflineNormalYahoo:
    def __init__(self):
        self.ohlc_calls = []
        latest = last_completed_session(NORMAL_CLOCK)
        index = pd.DatetimeIndex(sessions_between(latest - pd.Timedelta(days=520), latest)[-260:])
        self.frame = pd.DataFrame({"Open": 99.8, "High": 100.3, "Low": 99.5, "Close": 100.0,
            "Volume": 1_000_000}, index=index)

    def fetch_ohlc_batch(self, tickers, **kwargs):
        return {ticker: self.frame.copy() for ticker in tickers}, {}

    def fetch_ohlc_only(self, *args, **kwargs):
        # The normal small-universe pipeline legitimately fetches its current
        # forecast prices before persistence. Post-save audit must add no call.
        self.ohlc_calls.append(args[0] if args else None)
        return self.frame.copy(), None


class Batch7NormalForecastPersistenceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = AppConfig(output_dir=self.root, sqlite_path=self.root / "normal_forecasts.db",
            export_excel=False, agent_stage1_enabled=False)
        self.store = SQLiteStore(self.config.sqlite_path)
        self.pipeline = PipelineService(self.config)
        self.client = _OfflineNormalYahoo()
        self.pipeline.yahoo_client = self.client

    def normal_run(self, tickers=("AAPL",)):
        with patch("socket.socket.connect", side_effect=AssertionError("Synthetic normal path attempted HTTP")), \
             patch("market_checker_app.services.pipeline_service.datetime", _FrozenClock), \
             patch("market_checker_app.services.pipeline_service.utc_now", return_value=NORMAL_CLOCK):
            result = self.pipeline.run(list(tickers), [], self.store, rss_enabled=False,
                mt5_enabled=False, yahoo_metadata_enabled=False)
        for item in result["point_in_time_inputs"]:
            item["provenance"]["evidence_origin"] = "DETERMINISTIC_TEST_FIXTURE"
        return result

    def source_manifest(self, tickers=("AAPL",)):
        entries = []
        for ticker in tickers:
            source = capture_provider_frame(ticker, self.client.frame,
                started_at=NORMAL_CLOCK - timedelta(minutes=2), observed_at=NORMAL_CLOCK - timedelta(minutes=1))
            path = persist_observation(source, self.root / "fixture_sources")
            entries.append(_entry(self.root, ticker, [path]))
        _manifest(self.root, sources=entries)

    def finalize(self, result, tickers=("AAPL",), **kwargs):
        calls_before = list(self.client.ohlc_calls)
        with patch("socket.socket.connect", side_effect=AssertionError("Post-save adapter attempted HTTP")), \
             patch(MODULE + ".physical_clock", return_value=NORMAL_CLOCK + timedelta(seconds=2)):
            summary = finalize_analysis_run(result=result, config=self.config, tickers=list(tickers),
                store=self.store, pipeline=self.pipeline, **kwargs)
        self.assertEqual(calls_before, self.client.ohlc_calls)
        return summary

    def test_default_normal_output_has_no_audit_io_or_added_summary_field(self):
        result = self.normal_run()
        with patch(MODULE + ".ProspectiveForecastRegistrationAdapter", side_effect=AssertionError("Default audit I/O")):
            summary = self.finalize(result)
        self.assertEqual(1, summary["point_in_time_snapshot_count"])
        self.assertNotIn("prospective_forecast_history", summary)
        self.assertFalse((self.root / "prospective_specialist_history").exists())

    def test_normal_save_precedes_registration_and_keeps_actual_model_output_immutable(self):
        result = self.normal_run()
        self.source_manifest()
        before_signals = result["signals"].copy(deep=True)
        events = []
        native_save = self.store.save_prediction_snapshots
        native_register = ProspectiveForecastRegistrationAdapter.register_after_persistence
        def save(rows):
            saved = native_save(rows)
            self.assertEqual(1, len(self.store.read_prediction_snapshots()))
            events.append("SAVED")
            return saved
        def register(adapter, ids):
            self.assertEqual(["SAVED"], events)
            events.append("REGISTER")
            return native_register(adapter, ids)
        with patch.object(self.store, "save_prediction_snapshots", side_effect=save), \
             patch.object(ProspectiveForecastRegistrationAdapter, "register_after_persistence", new=register):
            first = self.finalize(result, prospective_history_enabled=True, prospective_audit_archive_root=self.root)
        audit = first["prospective_forecast_history"]
        self.assertEqual(["SAVED", "REGISTER"], events)
        self.assertEqual(1, audit["registration"]["registered_count"])
        self.assertTrue(audit["registration"]["rows"][0]["fixture_only"])
        stored_before = self.store.read_prediction_snapshots().copy(deep=True)
        again = self.finalize(result, prospective_history_enabled=True, prospective_audit_archive_root=self.root)
        self.assertEqual(1, again["prospective_forecast_history"]["registration"]["registration_replay_count"])
        pd.testing.assert_frame_equal(stored_before, self.store.read_prediction_snapshots())
        pd.testing.assert_frame_equal(before_signals, result["signals"])
        self.assertFalse(audit["history"]["activation_allowed"])
        self.assertEqual(0, audit["history"]["eligible_real_label_count"])

    def test_partial_native_persistence_preserves_the_completed_forecast_and_restart_completes(self):
        tickers = ("AAPL", "MSFT")
        result = self.normal_run(tickers)
        self.source_manifest(tickers)
        native_save = self.store.save_prediction_snapshots
        def partial(rows):
            native_save(rows[:1])
            raise OSError("synthetic partial commit interruption")
        with patch.object(self.store, "save_prediction_snapshots", side_effect=partial):
            first = self.finalize(result, tickers, prospective_history_enabled=True, prospective_audit_archive_root=self.root)
        self.assertEqual("FAILED", first["point_in_time_snapshot_status"])
        registration = first["prospective_forecast_history"]["registration"]
        self.assertEqual(("PARTIAL", 1), (registration["status"], registration["registered_count"]))
        self.assertIn("STORED_FORECAST_NOT_FOUND", registration["reason_counts"])
        restarted = self.finalize(result, tickers, prospective_history_enabled=True, prospective_audit_archive_root=self.root)
        self.assertEqual(2, restarted["point_in_time_snapshot_count"])
        self.assertEqual((1, 1), (restarted["prospective_forecast_history"]["registration"]["registered_count"],
            restarted["prospective_forecast_history"]["registration"]["registration_replay_count"]))

    def test_missing_audit_configuration_is_reported_without_failing_normal_persistence(self):
        result = self.normal_run()
        summary = self.finalize(result, prospective_history_enabled=True)
        self.assertEqual("SUCCESS", summary["point_in_time_snapshot_status"])
        self.assertEqual("PROSPECTIVE_AUDIT_ROOT_NOT_CONFIGURED", summary["prospective_forecast_history"]["registration"]["reason"])
        self.assertEqual(1, len(self.store.read_prediction_snapshots()))

    def test_normal_report_preserves_first_saved_registration_before_a_malformed_second_source(self):
        tickers = ("AAPL", "MSFT")
        result = self.normal_run(tickers)
        self.source_manifest(tickers)
        manifest = json.loads((self.root / MANIFEST_FILENAME).read_text())
        second_entry = manifest["forecast_sources"][1]
        source_path = self.root / second_entry["source_archive_paths"][0]
        malformed = json.loads(source_path.read_text())
        malformed.pop("observed_at")
        malformed["capture_id"] = canonical_hash({key: item for key, item in malformed.items() if key != "capture_id"})
        bad_path = source_path.parent / f"yahoo_price_{malformed['capture_id']}.json"
        bad_path.write_text(json.dumps(malformed), encoding="utf-8")
        second_entry["source_archive_paths"] = [bad_path.relative_to(self.root).as_posix()]
        (self.root / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")
        summary = self.finalize(result, tickers, prospective_history_enabled=True, prospective_audit_archive_root=self.root)
        self.assertEqual("SUCCESS", summary["point_in_time_snapshot_status"])
        registration = summary["prospective_forecast_history"]["registration"]
        self.assertEqual(("PARTIAL", 1), (registration["status"], registration["registered_count"]))
        self.assertEqual(2, len(self.store.read_prediction_snapshots()))
        self.assertEqual("MALFORMED_PROSPECTIVE_ARCHIVE_RECORD", registration["rows"][1]["reason"])


if __name__ == "__main__":
    unittest.main()
