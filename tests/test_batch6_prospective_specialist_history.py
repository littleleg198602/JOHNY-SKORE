"""Deterministic persistence fixtures only; no live forecasts or OOS proof."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from market_checker_app.models import RunMetadata
from market_checker_app.prediction_contract import (
    PRIMARY_PREDICTION_TARGET, build_point_in_time_snapshot, canonical_hash,
    resolve_excess_return_label,
)
from market_checker_app.services.prospective_specialist_history import (
    MAX_HISTORY_RECORDS, MAX_RECORD_BYTES, MAX_SOURCE_REFERENCES,
    ProspectiveSpecialistHistory, read_stored_prediction_snapshot,
)
from market_checker_app.services.specialist_label_availability_service import (
    build_recorded_label_observation, persist_label_observation,
)
from market_checker_app.services.us_equity_calendar import target_us_equity_window
from market_checker_app.services.yahoo_price_provenance_service import (
    capture_provider_frame, persist_observation,
)
from market_checker_app.storage.sqlite_store import SQLiteStore


PREDICTION_AT = datetime(2026, 10, 12, 21, tzinfo=timezone.utc)
REGISTERED_AT = PREDICTION_AT + timedelta(seconds=3)
_MODULE = "market_checker_app.services.prospective_specialist_history"


def _fixture(directory, *, prediction_at=PREDICTION_AT, source=True, fixture_origin=True):
    directory = Path(directory)
    store = SQLiteStore(directory / "forecast_fixture.db")
    store.ensure_schema()
    run_id = store.insert_run(RunMetadata(started_at=prediction_at, finished_at=prediction_at,
        watchlist_size=1, processed_symbols=1, warnings_count=0, errors_count=0))
    provenance = {"source": "deterministic-unit-fixture"}
    if fixture_origin:
        provenance["evidence_origin"] = "DETERMINISTIC_TEST_FIXTURE"
    snapshot = build_point_in_time_snapshot(run_id=run_id, ticker="AAPL", observed_at=prediction_at,
        feature_payload={"current_price": 100.0, "feature_contract": {
            "observed_at": prediction_at.isoformat(), "available_at": prediction_at.isoformat()}},
        baseline_output={"action": "NO_TRADE", "final_total_score": 52.0,
            "training_completed_at": (prediction_at - timedelta(seconds=1)).isoformat()},
        provenance=provenance)
    store.save_prediction_snapshots([snapshot])
    paths = []
    if source:
        base, _ = target_us_equity_window(prediction_at, 5)
        frame = pd.DataFrame({"Close": [100.0], "Stock Splits": [0.0], "Dividends": [0.0]},
            index=pd.to_datetime([base.session_date.isoformat()], utc=True))
        observed = prediction_at - timedelta(seconds=10)
        record = capture_provider_frame("AAPL", frame, started_at=observed - timedelta(seconds=1),
            observed_at=observed)
        paths.append(persist_observation(record, directory / "fixture_sources"))
    return store, snapshot, paths


def _rewrite_snapshot(store, identifier, mutate):
    snapshot = read_stored_prediction_snapshot(store.db_path, identifier)
    mutate(snapshot)
    snapshot["snapshot_hash"] = canonical_hash({key: value for key, value in snapshot.items() if key != "snapshot_hash"})
    columns = ("snapshot_schema_version", "run_id", "ticker", "as_of", "observed_at",
        "target_name", "target_version", "horizon_trading_days", "benchmark_ticker",
        "benchmark_selection", "label_status", "target_value", "target_observed_at",
        "baseline_model_id", "baseline_model_version", "snapshot_hash", "created_at")
    fields = {key: snapshot[key] for key in columns}
    fields.update({key + "_json": json.dumps(snapshot[key]) for key in ("feature_payload", "baseline_output", "provenance")})
    with closing(sqlite3.connect(store.db_path)) as connection:
        with connection:
            connection.execute("UPDATE prediction_snapshots SET " + ", ".join(key + "=?" for key in fields) + " WHERE snapshot_id=?",
                               (*fields.values(), identifier))
    return snapshot


def _seal_observation(observation):
    for capture in observation["price_captures"].values():
        capture["capture_hash"] = canonical_hash({key: value for key, value in capture.items() if key != "capture_hash"})
    observation["observation_hash"] = canonical_hash({key: value for key, value in observation.items() if key != "observation_hash"})
    return observation


def _resolved_fixture(store, identifier, directory, *, label_origin="DETERMINISTIC_TEST_FIXTURE"):
    original = read_stored_prediction_snapshot(store.db_path, identifier)
    base, future = target_us_equity_window(datetime.fromisoformat(original["as_of"]), 5)
    clock = future[-1].close_at + timedelta(seconds=30)
    asset, benchmark = [100.0] * 5 + [103.0], [100.0] * 6
    labeled = resolve_excess_return_label(original, asset, benchmark, target_observed_at=clock)
    store.update_prediction_snapshot_labels([labeled])
    metadata = {"provider": "fixture-provider", "price_basis": PRIMARY_PREDICTION_TARGET.price_basis,
        "price_method_version": PRIMARY_PREDICTION_TARGET.price_method_version, "dividends_included": False,
        "revision_status": "KNOWN_REVISION", "revision_known_at": (clock - timedelta(seconds=1)).isoformat(),
        "corporate_action_status": "RECORDED_COMPLETE_WINDOW", "corporate_actions": [],
        "corporate_actions_checked_from": base.session_date.isoformat(),
        "corporate_actions_checked_through": future[-1].session_date.isoformat(),
        "corporate_actions_known_at": clock.isoformat(),
        "provider_payload_sha256": canonical_hash({"fixture_only": True, "sessions": [base.session_date.isoformat(), *[item.session_date.isoformat() for item in future]]})}
    observation = build_recorded_label_observation(labeled, asset_prices=asset, benchmark_prices=benchmark,
        asset_observed_at=clock, benchmark_observed_at=clock, captured_at=clock + timedelta(seconds=1),
        asset_provenance=metadata, benchmark_provenance=metadata)
    observation["evidence_origin"] = label_origin
    for capture in observation["price_captures"].values():
        # Explicit synthetic publisher-version metadata exercises the strict
        # positive contract. It is never populated into the real handoff.
        capture["publisher_revision_timing_status"] = "DATED_PUBLISHER_VERSION"
    _seal_observation(observation)
    path = persist_label_observation(observation, Path(directory) / "fixture_labels")
    return labeled, observation, path, clock + timedelta(minutes=2)


def _files(directory):
    return sorted(str(path.relative_to(directory)) for path in Path(directory).rglob("*.json")) if Path(directory).exists() else []


class Batch6ProspectiveSpecialistHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.store, self.snapshot, self.sources = _fixture(self.directory)
        self.identifier = self.snapshot["snapshot_id"]
        self.history = ProspectiveSpecialistHistory(self.directory / "history", observation_clock=lambda: REGISTERED_AT)

    def register(self):
        return self.history.register_stored_snapshot(self.store.db_path, self.identifier, source_archive_paths=self.sources)

    def attach(self, *, label_origin="DETERMINISTIC_TEST_FIXTURE"):
        self.registration = self.register()
        self.labeled, self.observation, self.label_path, self.attached_at = _resolved_fixture(
            self.store, self.identifier, self.directory, label_origin=label_origin)
        self.history._clock = lambda: self.attached_at
        return self.history.attach_label(self.store.db_path, self.identifier, label_observation_path=self.label_path)

    def replace_observation(self, mutate):
        observation = deepcopy(self.observation)
        mutate(observation)
        path = persist_label_observation(_seal_observation(observation), self.directory / "fixture_labels")
        return observation, path

    def test_registration_pins_authentic_stored_body_without_changing_database(self):
        before = read_stored_prediction_snapshot(self.store.db_path, self.identifier)
        saved = self.register()
        self.assertEqual(before, saved["forecast"])
        self.assertEqual(self.snapshot["snapshot_hash"], saved["forecast_snapshot_hash"])
        self.assertEqual(REGISTERED_AT.isoformat(), saved["registered_at"])
        self.assertEqual(REGISTERED_AT.isoformat(), saved["available_at"])
        self.assertEqual(6, len(saved["target_window"]["required_session_dates"]))
        self.assertEqual("2026-10-19", saved["target_window"]["target_session_date"])
        self.assertEqual(1, len(saved["source_references"]))
        self.assertFalse(saved["stored_created_at_is_physical_registration"])
        self.assertFalse(saved["forecast_authenticity_independently_verified"])
        self.assertEqual(before, read_stored_prediction_snapshot(self.store.db_path, self.identifier))

    def test_missing_database_is_not_created_and_empty_database_is_not_initialized(self):
        missing = self.directory / "missing.db"
        with self.assertRaisesRegex(ValueError, "FORECAST_DATABASE_UNAVAILABLE"):
            self.history.register_stored_snapshot(missing, self.identifier)
        self.assertFalse(missing.exists())
        empty = self.directory / "empty.db"
        with closing(sqlite3.connect(empty)) as connection:
            before = connection.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
        with self.assertRaisesRegex(ValueError, "FORECAST_TABLE_UNAVAILABLE"):
            self.history.register_stored_snapshot(empty, self.identifier)
        with closing(sqlite3.connect(empty)) as connection:
            self.assertEqual(before, connection.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0])
        self.assertFalse(self.history.directory.exists())

    def test_unknown_id_is_rejected_before_any_history_write(self):
        with self.assertRaisesRegex(ValueError, "STORED_FORECAST_NOT_FOUND"):
            self.history.register_stored_snapshot(self.store.db_path, "0" * 64)
        self.assertFalse(self.history.directory.exists())

    def test_old_pending_forecast_cannot_be_registered_as_prospective(self):
        self.history._clock = lambda: datetime(2026, 10, 14, 13, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "FORECAST_NOT_PROSPECTIVE_FOR_EXACT_WINDOW"):
            self.register()
        self.assertFalse(self.history.directory.exists())

    def test_registration_at_first_forward_open_is_already_too_late(self):
        self.history._clock = lambda: datetime(2026, 10, 13, 13, 30, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "FORECAST_NOT_PROSPECTIVE_FOR_EXACT_WINDOW"):
            self.register()
        self.history._clock = lambda: datetime(2026, 10, 13, 13, 29, 59, tzinfo=timezone.utc)
        self.assertEqual("2026-10-19", self.register()["target_window"]["target_session_date"])

    def test_intraday_forecast_cannot_claim_a_whole_already_started_forward_window(self):
        store, snapshot, sources = _fixture(self.directory / "intraday",
            prediction_at=datetime(2026, 10, 13, 15, tzinfo=timezone.utc))
        history = ProspectiveSpecialistHistory(self.directory / "intraday_history",
            observation_clock=lambda: datetime(2026, 10, 13, 15, 1, tzinfo=timezone.utc))
        with self.assertRaisesRegex(ValueError, "FORECAST_NOT_PROSPECTIVE_FOR_EXACT_WINDOW"):
            history.register_stored_snapshot(store.db_path, snapshot["snapshot_id"], source_archive_paths=sources)

    def test_physically_future_or_naive_registration_clocks_are_rejected(self):
        for clock, reason in ((PREDICTION_AT - timedelta(seconds=1), "FORECAST_OBSERVATION_AFTER_PHYSICAL_REGISTRATION"),
                              (datetime(2026, 10, 12, 22), "INVALID_HISTORY_KNOWLEDGE_CLOCK")):
            with self.subTest(clock=str(clock)):
                self.history._clock = lambda: clock
                with self.assertRaisesRegex(ValueError, reason):
                    self.register()
        self.assertFalse(self.history.directory.exists())

    def test_holidays_and_early_closes_pin_official_calendar_without_sliding(self):
        forecast_at = datetime(2026, 11, 25, 22, tzinfo=timezone.utc)
        store, snapshot, sources = _fixture(self.directory / "holiday", prediction_at=forecast_at)
        history = ProspectiveSpecialistHistory(self.directory / "holiday_history", observation_clock=lambda: forecast_at)
        saved = history.register_stored_snapshot(store.db_path, snapshot["snapshot_id"], source_archive_paths=sources)
        self.assertEqual(["2026-11-25", "2026-11-27", "2026-11-30", "2026-12-01", "2026-12-02", "2026-12-03"],
                         saved["target_window"]["required_session_dates"])
        self.assertEqual("2026-11-27T18:00:00+00:00", saved["target_window"]["required_session_close_times"][1])

    def test_same_source_replay_is_idempotent_even_after_the_window_and_label_resolution(self):
        saved = self.register()
        original_bytes = self.history._forecast_path(self.identifier).read_bytes()
        _resolved_fixture(self.store, self.identifier, self.directory)
        self.history._clock = lambda: datetime(2027, 1, 1, tzinfo=timezone.utc)
        replay = self.history.register_stored_snapshot(self.store.db_path, self.identifier,
            source_archive_paths=self.sources + self.sources)
        self.assertEqual(saved, replay)
        self.assertEqual(original_bytes, self.history._forecast_path(self.identifier).read_bytes())
        self.assertEqual(1, len(_files(self.history.directory)))

    def test_racing_identical_registrations_preserve_the_first_physical_record(self):
        # Warm the calendar, then exercise the atomic file collision path.
        target_us_equity_window(PREDICTION_AT, 5)
        other = ProspectiveSpecialistHistory(self.history.directory,
            observation_clock=lambda: REGISTERED_AT + timedelta(seconds=1))
        with ThreadPoolExecutor(max_workers=2) as pool:
            outputs = list(pool.map(lambda history: history.register_stored_snapshot(
                self.store.db_path, self.identifier, source_archive_paths=self.sources), [self.history, other]))
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(1, len(_files(self.history.directory)))

    def test_changed_baseline_cannot_rewrite_a_registration_even_with_a_valid_new_hash(self):
        self.register()
        before = self.history._forecast_path(self.identifier).read_bytes()
        _rewrite_snapshot(self.store, self.identifier, lambda row: row["baseline_output"].update(final_total_score=90.0))
        with self.assertRaisesRegex(ValueError, "PROSPECTIVE_HISTORY_REWRITE_REFUSED"):
            self.register()
        self.assertEqual(before, self.history._forecast_path(self.identifier).read_bytes())

    def test_replay_cannot_add_or_remove_forecast_source_pins(self):
        self.register()
        with self.assertRaisesRegex(ValueError, "PROSPECTIVE_HISTORY_REWRITE_REFUSED"):
            self.history.register_stored_snapshot(self.store.db_path, self.identifier)

    def test_later_source_capture_never_inherits_its_earlier_market_date(self):
        record = json.loads(self.sources[0].read_text())
        record.update(observed_at=(PREDICTION_AT + timedelta(seconds=1)).isoformat(),
                      captured_at=(PREDICTION_AT + timedelta(seconds=1)).isoformat())
        record["capture_id"] = canonical_hash({key: value for key, value in record.items() if key != "capture_id"})
        source = persist_observation(record, self.directory / "fixture_sources")
        with self.assertRaisesRegex(ValueError, "FORECAST_SOURCE_AVAILABLE_AFTER_PREDICTION"):
            self.history.register_stored_snapshot(self.store.db_path, self.identifier, source_archive_paths=[source])
        self.assertFalse(self.history.directory.exists())

    def test_explicit_future_feature_knowledge_is_rejected(self):
        _rewrite_snapshot(self.store, self.identifier, lambda row: row["feature_payload"]["feature_contract"].update(
            available_at=(PREDICTION_AT + timedelta(seconds=1)).isoformat()))
        with self.assertRaisesRegex(ValueError, "FORECAST_INPUT_KNOWLEDGE_AFTER_PREDICTION"):
            self.register()

    def test_training_clock_must_be_strictly_before_the_original_prediction(self):
        _rewrite_snapshot(self.store, self.identifier, lambda row: row["baseline_output"].update(
            training_completed_at=PREDICTION_AT.isoformat()))
        with self.assertRaisesRegex(ValueError, "TRAINING_NOT_STRICTLY_BEFORE_PREDICTION"):
            self.register()

    def test_scalar_outcome_in_features_cannot_be_registered_as_a_forecast(self):
        _rewrite_snapshot(self.store, self.identifier, lambda row: row["feature_payload"].update(target_value=0.03))
        with self.assertRaisesRegex(ValueError, "FORECAST_FEATURES_CONTAIN_OUTCOME"):
            self.register()

    def test_unhashed_database_changes_are_rejected(self):
        with closing(sqlite3.connect(self.store.db_path)) as connection:
            with connection:
                connection.execute("UPDATE prediction_snapshots SET benchmark_ticker='QQQ' WHERE snapshot_id=?", (self.identifier,))
        with self.assertRaisesRegex(ValueError, "STORED_FORECAST_HASH_MISMATCH"):
            self.register()

    def test_unsupported_horizon_cannot_change_the_definition_of_the_target(self):
        _rewrite_snapshot(self.store, self.identifier, lambda row: row.update(horizon_trading_days=6))
        with self.assertRaisesRegex(ValueError, "UNSUPPORTED_PROSPECTIVE_TARGET"):
            self.register()

    def test_first_registration_requires_an_unlabeled_forecast(self):
        _resolved_fixture(self.store, self.identifier, self.directory)
        with self.assertRaisesRegex(ValueError, "FIRST_REGISTRATION_REQUIRES_PENDING_FORECAST"):
            self.register()
        self.assertFalse(self.history.directory.exists())

    def test_pending_and_unavailable_labels_remain_waits_without_zero_returns(self):
        self.register()
        missing = self.directory / "missing_label.json"
        result = self.history.attach_label(self.store.db_path, self.identifier, label_observation_path=missing)
        self.assertEqual("EXACT_TARGET_LABEL_NOT_RESOLVED", result["reason"])
        _rewrite_snapshot(self.store, self.identifier, lambda row: row.update(label_status="UNAVAILABLE"))
        result = self.history.attach_label(self.store.db_path, self.identifier, label_observation_path=missing)
        self.assertEqual("WAIT_LABEL", result["status"])
        self.assertFalse((self.history.directory / "labels").exists())

    def test_missing_sidecar_never_becomes_a_usable_scalar_label(self):
        self.register()
        _resolved_fixture(self.store, self.identifier, self.directory)
        result = self.history.attach_label(self.store.db_path, self.identifier,
            label_observation_path=self.directory / "missing_label.json")
        self.assertEqual("MISSING_LABEL_OBSERVATION", result["reason"])
        self.assertEqual(0, self.history.read_report(as_of=datetime(2027, 1, 1, tzinfo=timezone.utc))["eligible_real_label_count"])

    def test_exact_later_label_is_appended_at_its_true_attachment_clock(self):
        attached = self.attach()
        record = attached["record"]
        self.assertEqual("LABEL_ATTACHED", attached["status"])
        self.assertEqual(self.attached_at.isoformat(), record["usable_at"])
        self.assertEqual(self.registration["target_window"], record["target_window"])
        self.assertEqual(self.observation, record["label_observation"])
        self.assertEqual(self.labeled, record["labeled_snapshot"])
        before = self.history.read_report(as_of=self.attached_at - timedelta(microseconds=1))
        self.assertEqual("LABEL_ATTACHMENT_AFTER_REPORT_CUTOFF", before["rows"][0]["reason"])
        after = self.history.read_report(as_of=self.attached_at)
        self.assertEqual(1, after["eligible_fixture_label_count"])
        self.assertEqual(0, after["eligible_real_label_count"])
        self.assertEqual([], after["eligible_snapshots"])
        self.assertFalse(after["out_of_sample_evaluation_verified"])

    def test_training_uses_strict_attachment_knowledge_before_each_prediction(self):
        self.attach()
        equal = self.history.read_report(as_of=self.attached_at + timedelta(days=1), training_before=self.attached_at)
        self.assertEqual("LABEL_NOT_STRICTLY_BEFORE_TRAINING_PREDICTION", equal["rows"][0]["reason"])
        later = self.history.read_report(as_of=self.attached_at + timedelta(days=1),
            training_before=self.attached_at + timedelta(microseconds=1))
        self.assertTrue(later["rows"][0]["eligible_for_requested_clock"])

    def test_registration_clock_itself_must_precede_a_training_prediction(self):
        self.attach()
        result = self.history.read_report(as_of=self.attached_at, training_before=REGISTERED_AT)
        self.assertEqual("FORECAST_NOT_STRICTLY_BEFORE_TRAINING_PREDICTION", result["rows"][0]["reason"])
        early = self.history.read_report(as_of=REGISTERED_AT - timedelta(microseconds=1))
        self.assertEqual("FORECAST_REGISTRATION_AFTER_REPORT_CUTOFF", early["rows"][0]["reason"])

    def test_fixture_with_production_schema_exercises_eligible_rows_without_live_claim(self):
        # Everything here is still deterministic unit-test data. This single
        # case exercises the production-format branch, not a real corpus.
        store, snapshot, sources = _fixture(self.directory / "production_schema_fixture", fixture_origin=False)
        history = ProspectiveSpecialistHistory(self.directory / "production_schema_history", observation_clock=lambda: REGISTERED_AT)
        history.register_stored_snapshot(store.db_path, snapshot["snapshot_id"], source_archive_paths=sources)
        labeled, _, label_path, clock = _resolved_fixture(store, snapshot["snapshot_id"],
            self.directory / "production_schema_fixture", label_origin="RECORDED_PRICE_FRAME")
        history._clock = lambda: clock
        history.attach_label(store.db_path, snapshot["snapshot_id"], label_observation_path=label_path)
        report = history.read_report(as_of=clock, training_before=clock - timedelta(seconds=1))
        self.assertEqual([], report["eligible_snapshots"])
        report = history.read_report(as_of=clock)
        self.assertEqual([labeled], report["eligible_snapshots"])
        self.assertEqual(1, report["eligible_real_label_count"])
        self.assertFalse(report["publisher_authenticity_verified"])
        self.assertFalse(report["feature_source_coverage_verified"])

    def test_captured_price_revision_claim_without_dated_publisher_version_is_excluded(self):
        self.register()
        _, self.observation, self.label_path, clock = _resolved_fixture(self.store, self.identifier, self.directory)
        self.history._clock = lambda: clock
        for kind in ("unknown", "absent", "local_current_version"):
            with self.subTest(kind=kind):
                def mutate(record):
                    for capture in record["price_captures"].values():
                        if kind == "absent":
                            capture.pop("publisher_revision_timing_status")
                        else:
                            capture["publisher_revision_timing_status"] = "UNKNOWN"
                        if kind == "local_current_version":
                            capture["revision_status"] = "CAPTURED_CURRENT_VERSION"
                _, path = self.replace_observation(mutate)
                result = self.history.attach_label(self.store.db_path, self.identifier, label_observation_path=path)
                self.assertEqual("MISSING_DATED_PUBLISHER_PRICE_REVISION", result["reason"])
                self.assertFalse((self.history.directory / "labels").exists())

    def test_dated_revision_without_a_source_capture_id_is_excluded(self):
        self.register()
        _, self.observation, self.label_path, clock = _resolved_fixture(self.store, self.identifier, self.directory)
        self.history._clock = lambda: clock
        _, path = self.replace_observation(lambda record: record["price_captures"]["asset"].pop("provider_payload_sha256"))
        self.assertEqual("MISSING_PUBLISHER_VERSION_SOURCE_ID", self.history.attach_label(
            self.store.db_path, self.identifier, label_observation_path=path)["reason"])

    def test_missing_middle_endpoint_duplicate_or_shifted_sessions_cannot_slide(self):
        self.register()
        _, self.observation, self.label_path, clock = _resolved_fixture(self.store, self.identifier, self.directory)
        self.history._clock = lambda: clock
        for kind in ("middle", "endpoint", "duplicate", "shifted"):
            with self.subTest(kind=kind):
                def mutate(record):
                    prices = record["price_captures"]["asset"]["session_prices"]
                    if kind in {"middle", "endpoint"}:
                        prices.pop(2 if kind == "middle" else -1)
                    elif kind == "duplicate":
                        prices[-1]["session_date"] = prices[-2]["session_date"]
                    else:
                        prices[-1]["session_date"] = "2026-10-20"
                _, path = self.replace_observation(mutate)
                self.assertEqual("INCOMPLETE_FORWARD_SESSION_WINDOW", self.history.attach_label(
                    self.store.db_path, self.identifier, label_observation_path=path)["reason"])
        self.assertFalse((self.history.directory / "labels").exists())

    def test_wrong_asset_benchmark_or_price_method_never_reuses_a_scalar(self):
        self.register()
        _, self.observation, self.label_path, clock = _resolved_fixture(self.store, self.identifier, self.directory)
        self.history._clock = lambda: clock
        for role, field, value, reason in (("asset", "ticker", "MSFT", "INVALID_PRICE_SOURCE_IDENTITY"),
                ("benchmark", "ticker", "QQQ", "INVALID_PRICE_SOURCE_IDENTITY"),
                ("asset", "dividends_included", True, "INCOMPATIBLE_LABEL_PRICE_METHOD")):
            with self.subTest(role=role, field=field):
                _, path = self.replace_observation(lambda record: record["price_captures"][role].update({field: value}))
                self.assertEqual(reason, self.history.attach_label(self.store.db_path, self.identifier, label_observation_path=path)["reason"])

    def test_changed_price_window_must_recompute_to_the_exact_stored_label(self):
        self.register()
        _, self.observation, self.label_path, clock = _resolved_fixture(self.store, self.identifier, self.directory)
        self.history._clock = lambda: clock
        _, path = self.replace_observation(lambda record: record["price_captures"]["asset"]["session_prices"][-1].update(close=110.0))
        self.assertEqual("LABEL_VALUE_DIFFERS_FROM_CAPTURE", self.history.attach_label(
            self.store.db_path, self.identifier, label_observation_path=path)["reason"])

    def test_unknown_actions_incomplete_action_scope_and_future_revision_knowledge_stay_excluded(self):
        self.register()
        _, self.observation, self.label_path, clock = _resolved_fixture(self.store, self.identifier, self.directory)
        self.history._clock = lambda: clock
        for field, value, reason in (("corporate_action_status", "UNKNOWN", "INCOMPLETE_CORPORATE_ACTION_PROVENANCE"),
                ("corporate_actions_checked_through", "2026-10-16", "INCOMPLETE_CORPORATE_ACTION_WINDOW"),
                ("revision_known_at", (clock + timedelta(hours=1)).isoformat(), "INVALID_PRICE_REVISION_KNOWLEDGE")):
            with self.subTest(field=field):
                _, path = self.replace_observation(lambda record: record["price_captures"]["asset"].update({field: value}))
                self.assertEqual(reason, self.history.attach_label(self.store.db_path, self.identifier, label_observation_path=path)["reason"])

    def test_retrospective_attachment_cannot_inherit_the_archive_or_exchange_close_clock(self):
        self.register()
        _, self.observation, self.label_path, clock = _resolved_fixture(self.store, self.identifier, self.directory)
        physical_later = clock + timedelta(days=7)
        self.history._clock = lambda: physical_later
        result = self.history.attach_label(self.store.db_path, self.identifier, label_observation_path=self.label_path)
        self.assertEqual(physical_later.isoformat(), result["record"]["usable_at"])
        self.assertEqual("LABEL_ATTACHMENT_AFTER_REPORT_CUTOFF", self.history.read_report(as_of=clock)["rows"][0]["reason"])

    def test_future_label_archive_is_not_attached_at_an_earlier_physical_clock(self):
        self.register()
        _, self.observation, self.label_path, clock = _resolved_fixture(self.store, self.identifier, self.directory)
        self.history._clock = lambda: datetime.fromisoformat(self.observation["captured_at"]) - timedelta(microseconds=1)
        result = self.history.attach_label(self.store.db_path, self.identifier, label_observation_path=self.label_path)
        self.assertEqual("LABEL_ARCHIVE_AFTER_REPORT_CUTOFF", result["reason"])
        self.assertFalse((self.history.directory / "labels").exists())

    def test_changed_forecast_features_cannot_attach_even_with_the_same_identity(self):
        self.register()
        _, _, path, clock = _resolved_fixture(self.store, self.identifier, self.directory)
        _rewrite_snapshot(self.store, self.identifier, lambda row: row["feature_payload"].update(current_price=200.0))
        self.history._clock = lambda: clock
        with self.assertRaisesRegex(ValueError, "ATTACHED_LABEL_FORECAST_INPUTS_CHANGED"):
            self.history.attach_label(self.store.db_path, self.identifier, label_observation_path=path)

    def test_identical_attachment_replay_preserves_old_bytes_and_knowledge(self):
        saved = self.attach()["record"]
        original_bytes = self.history._label_path(self.identifier).read_bytes()
        self.history._clock = lambda: self.attached_at + timedelta(days=10)
        result = self.history.attach_label(self.store.db_path, self.identifier, label_observation_path=self.label_path)
        self.assertTrue(result["replayed"])
        self.assertEqual(saved, result["record"])
        self.assertEqual(original_bytes, self.history._label_path(self.identifier).read_bytes())

    def test_new_valid_capture_cannot_rewrite_a_final_label_attachment(self):
        self.attach()
        before = self.history._label_path(self.identifier).read_bytes()
        _, path = self.replace_observation(lambda record: record["price_captures"]["asset"].update(
            provider_payload_sha256="a" * 64))
        with self.assertRaisesRegex(ValueError, "PROSPECTIVE_HISTORY_REWRITE_REFUSED"):
            self.history.attach_label(self.store.db_path, self.identifier, label_observation_path=path)
        self.assertEqual(before, self.history._label_path(self.identifier).read_bytes())

    def test_attachment_preserves_the_full_exact_observation_after_external_sidecar_disappears(self):
        self.attach()
        self.label_path.unlink()
        report = self.history.read_report(as_of=self.attached_at)
        self.assertEqual(1, report["eligible_fixture_label_count"])
        self.assertEqual(self.observation, json.loads(self.history._label_path(self.identifier).read_text())["label_observation"])

    def test_missing_source_pins_are_visible_and_cannot_be_backfilled_later(self):
        self.sources = []
        self.attach()
        report = self.history.read_report(as_of=self.attached_at)
        self.assertEqual("MISSING_FORECAST_SOURCE_ARCHIVE_PINS", report["rows"][0]["reason"])
        self.assertEqual(0, report["eligible_fixture_label_count"])
        self.assertFalse(report["feature_source_coverage_verified"])

    def test_corrupt_final_history_fails_closed_without_overwriting_it(self):
        self.attach()
        path = self.history._label_path(self.identifier)
        before = path.read_bytes()
        path.write_bytes(before.replace(b'"target_value": 0.030000000000000027', b'"target_value": 0.5'))
        # Use an unequivocal byte change regardless of numeric formatting.
        record = json.loads(path.read_text())
        record["attached_at"] = (self.attached_at + timedelta(days=1)).isoformat()
        path.write_text(json.dumps(record), encoding="utf-8")
        corrupt = path.read_bytes()
        report = self.history.read_report(as_of=self.attached_at + timedelta(days=2))
        self.assertEqual("PARTIAL_HISTORY", report["status"])
        self.assertEqual([], report["eligible_snapshots"])
        self.assertEqual({"INVALID_LABEL_ATTACHMENT_HASH": 1}, report["rejected_record_reasons"])
        with self.assertRaisesRegex(ValueError, "INVALID_LABEL_ATTACHMENT_HASH"):
            self.history.attach_label(self.store.db_path, self.identifier, label_observation_path=self.label_path)
        self.assertEqual(corrupt, path.read_bytes())

    def test_rehashed_acceptance_claims_cannot_be_smuggled_into_history(self):
        self.register()
        path = self.history._forecast_path(self.identifier)
        record = json.loads(path.read_text())
        record["activation_allowed"] = True
        record["registration_hash"] = canonical_hash({key: value for key, value in record.items() if key != "registration_hash"})
        path.write_text(json.dumps(record), encoding="utf-8")
        report = self.history.read_report(as_of=REGISTERED_AT)
        self.assertEqual("PARTIAL_HISTORY", report["status"])
        self.assertEqual({"FORECAST_HISTORY_CANNOT_ASSERT_ACCEPTANCE": 1}, report["rejected_record_reasons"])
        self.assertFalse(report["activation_allowed"])

    def test_rehashed_source_record_identity_cannot_change_the_forecast_source(self):
        self.register()
        path = self.history._forecast_path(self.identifier)
        record = json.loads(path.read_text())
        record["source_record_id"] = "a" * 64
        record["registration_hash"] = canonical_hash({key: value for key, value in record.items() if key != "registration_hash"})
        path.write_text(json.dumps(record), encoding="utf-8")
        report = self.history.read_report(as_of=REGISTERED_AT)
        self.assertEqual({"INVALID_FORECAST_SOURCE_RECORD_IDENTITY": 1}, report["rejected_record_reasons"])
        self.assertEqual([], report["eligible_snapshots"])

    def test_rehashed_source_reference_cannot_claim_another_archive_filename(self):
        self.register()
        path = self.history._forecast_path(self.identifier)
        record = json.loads(path.read_text())
        record["source_references"][0]["archive_filename"] = "yahoo_price_" + "a" * 64 + ".json"
        record["registration_hash"] = canonical_hash({key: value for key, value in record.items() if key != "registration_hash"})
        path.write_text(json.dumps(record), encoding="utf-8")
        report = self.history.read_report(as_of=REGISTERED_AT)
        self.assertEqual({"INVALID_FORECAST_SOURCE_ARCHIVE_IDENTITY": 1}, report["rejected_record_reasons"])

    def test_rehashed_attachment_cannot_claim_unrelated_price_capture_hashes(self):
        self.attach()
        path = self.history._label_path(self.identifier)
        record = json.loads(path.read_text())
        record["price_capture_hashes"]["asset"] = "a" * 64
        record["attachment_hash"] = canonical_hash({key: value for key, value in record.items() if key != "attachment_hash"})
        path.write_text(json.dumps(record), encoding="utf-8")
        report = self.history.read_report(as_of=self.attached_at)
        self.assertEqual({"INVALID_LABEL_ATTACHMENT_IDENTITY": 1}, report["rejected_record_reasons"])
        self.assertEqual(0, report["eligible_fixture_label_count"])

    def test_rejected_record_bytes_still_count_toward_the_read_budget(self):
        self.register()
        path = self.history._forecast_path(self.identifier)
        path.write_bytes(b'{"invalid":')
        report = self.history.read_report(as_of=REGISTERED_AT)
        self.assertEqual(len(path.read_bytes()), report["bounds"]["loaded_record_bytes"])
        self.assertEqual({"UNSUPPORTED_HISTORY_BODY": 1}, report["rejected_record_reasons"])

    def test_total_read_byte_limit_excludes_even_an_otherwise_complete_history(self):
        self.attach()
        with patch(_MODULE + ".MAX_REPORT_BYTES", 1):
            report = self.history.read_report(as_of=self.attached_at)
        self.assertTrue(report["bounds"]["total_byte_limit_reached"])
        self.assertEqual("PARTIAL_HISTORY", report["status"])
        self.assertEqual(0, report["eligible_fixture_label_count"])
        self.assertEqual([], report["eligible_snapshots"])

    def test_future_recording_can_never_use_an_unbounded_calendar_date(self):
        _rewrite_snapshot(self.store, self.identifier, lambda row: row.update(
            as_of="9999-01-01T00:00:00+00:00", observed_at="9999-01-01T00:00:00+00:00"))
        self.history._clock = lambda: datetime(9999, 1, 1, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "UNSUPPORTED_FORECAST_CALENDAR_DATE"):
            self.register()

    def test_bounded_partial_history_never_reports_a_convenient_eligible_subset(self):
        self.attach()
        other_store, other, sources = _fixture(self.directory / "other",
            prediction_at=PREDICTION_AT - timedelta(seconds=1))
        # The fixture stores reuse run 1, so use a different run to make a
        # second authentic stored snapshot instead of rewriting its ID.
        extra_run = other_store.insert_run(RunMetadata(started_at=PREDICTION_AT, finished_at=PREDICTION_AT,
            watchlist_size=1, processed_symbols=1, warnings_count=0, errors_count=0))
        extra = build_point_in_time_snapshot(run_id=extra_run, ticker="MSFT", observed_at=PREDICTION_AT,
            feature_payload={"current_price": 100.0}, baseline_output={"action": "NO_TRADE"},
            provenance={"evidence_origin": "DETERMINISTIC_TEST_FIXTURE"})
        other_store.save_prediction_snapshots([extra])
        self.history._clock = lambda: REGISTERED_AT
        self.history.register_stored_snapshot(other_store.db_path, extra["snapshot_id"], source_archive_paths=sources)
        report = self.history.read_report(as_of=self.attached_at, maximum=1)
        self.assertTrue(report["bounds"]["forecast_limit_reached"])
        self.assertEqual("PARTIAL_HISTORY", report["status"])
        self.assertEqual(0, report["eligible_fixture_label_count"])
        self.assertEqual([], report["eligible_snapshots"])

    def test_orphan_label_never_hides_a_deleted_forecast(self):
        self.attach()
        self.history._forecast_path(self.identifier).unlink()
        report = self.history.read_report(as_of=self.attached_at)
        self.assertEqual("PARTIAL_HISTORY", report["status"])
        self.assertEqual({"ORPHAN_LABEL_WITHOUT_FORECAST_REGISTRATION": 1}, report["rejected_record_reasons"])

    def test_source_and_record_bounds_fail_before_history_publication(self):
        with self.assertRaisesRegex(ValueError, "FORECAST_SOURCE_REFERENCE_LIMIT"):
            self.history.register_stored_snapshot(self.store.db_path, self.identifier,
                source_archive_paths=self.sources * (MAX_SOURCE_REFERENCES + 1))
        with closing(sqlite3.connect(self.store.db_path)) as connection:
            with connection:
                connection.execute("UPDATE prediction_snapshots SET feature_payload_json=? WHERE snapshot_id=?",
                    ("x" * (MAX_RECORD_BYTES + 1), self.identifier))
        with self.assertRaisesRegex(ValueError, "HISTORY_RECORD_BYTE_LIMIT"):
            self.register()
        self.assertFalse(self.history.directory.exists())

    def test_access_bearing_whole_forecast_is_withheld_before_projection_or_publication(self):
        # The fixture value is deliberately fake. It still exercises the whole
        # body gate and is never written into an evidence handoff.
        _rewrite_snapshot(self.store, self.identifier, lambda row: row["provenance"].update(
            authorization="Bearer deterministic-fixture-secret"))
        with self.assertRaisesRegex(ValueError, "WITHHELD_HISTORY_ACCESS_VALUE"):
            self.register()
        self.assertFalse(self.history.directory.exists())

    def test_escaped_access_fields_in_a_source_body_are_withheld_in_full(self):
        source = self.directory / "yahoo_price_escaped.json"
        source.write_text('{"access\\u005ftoken":"fixture-only"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "WITHHELD_HISTORY_ACCESS_VALUE"):
            self.history.register_stored_snapshot(self.store.db_path, self.identifier, source_archive_paths=[source])
        self.assertFalse(self.history.directory.exists())

    def test_duplicate_json_keys_and_nonfinite_archive_constants_are_rejected(self):
        for raw, reason in (('{"x":1,"x":2}', "DUPLICATE_HISTORY_JSON_KEY"),
                            ('{"x":NaN}', "UNSUPPORTED_HISTORY_BODY")):
            source = self.directory / "yahoo_price_unsafe.json"
            source.write_text(raw, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, reason):
                self.history.register_stored_snapshot(self.store.db_path, self.identifier, source_archive_paths=[source])

    def test_symlink_gate_is_exercised_without_requiring_windows_symlink_privileges(self):
        native = Path.is_symlink
        with patch.object(Path, "is_symlink", lambda path: path == self.store.db_path or native(path)):
            with self.assertRaisesRegex(ValueError, "HISTORY_SYMLINK_REFUSED"):
                self.register()
        native = Path.is_symlink
        with patch.object(Path, "is_symlink", lambda path: path.name == "forecasts" or native(path)):
            with self.assertRaisesRegex(ValueError, "HISTORY_SYMLINK_REFUSED"):
                self.history.read_report(as_of=REGISTERED_AT)

    def test_all_native_database_handles_close_on_success_and_on_errors(self):
        native, opened = sqlite3.connect, []
        def connect(*args, **kwargs):
            connection = native(*args, **kwargs)
            opened.append(connection)
            return connection
        with patch(_MODULE + ".sqlite3.connect", side_effect=connect):
            read_stored_prediction_snapshot(self.store.db_path, self.identifier)
            with self.assertRaisesRegex(ValueError, "STORED_FORECAST_NOT_FOUND"):
                read_stored_prediction_snapshot(self.store.db_path, "0" * 64)
            self.register()
        self.assertEqual(3, len(opened))
        for connection in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_native_transaction_context_negative_control_really_leaves_a_handle_open(self):
        # This assertion fails if the lifetime regression's tracker merely
        # checks transactions rather than explicit connection release.
        connection = sqlite3.connect(":memory:")
        try:
            with connection:
                connection.execute("SELECT 1")
            self.assertEqual((1,), connection.execute("SELECT 1").fetchone())
        finally:
            connection.close()

    def test_failed_atomic_publish_leaves_no_partial_json_or_temp_file(self):
        with patch(_MODULE + ".os.link", side_effect=OSError("fixture link failure")):
            with self.assertRaisesRegex(OSError, "fixture link failure"):
                self.register()
        self.assertEqual([], _files(self.history.directory))
        self.assertEqual([], list((self.history.directory / "forecasts").iterdir()))
        self.assertEqual("PENDING", read_stored_prediction_snapshot(self.store.db_path, self.identifier)["label_status"])

    def test_empty_history_is_read_only_wait_and_report_bounds_are_validated(self):
        report = self.history.read_report(as_of=REGISTERED_AT)
        self.assertEqual("WAIT_PROSPECTIVE_HISTORY", report["status"])
        self.assertEqual(0, report["registered_forecast_count"])
        self.assertEqual(0, report["eligible_real_label_count"])
        self.assertFalse(self.history.directory.exists())
        for bound in (True, 0, -1, MAX_HISTORY_RECORDS + 1):
            with self.assertRaisesRegex(ValueError, "INVALID_HISTORY_BOUND"):
                self.history.read_report(as_of=REGISTERED_AT, maximum=bound)
        with self.assertRaisesRegex(ValueError, "TRAINING_PREDICTION_AFTER_REPORT_CUTOFF"):
            self.history.read_report(as_of=REGISTERED_AT, training_before=REGISTERED_AT + timedelta(seconds=1))


if __name__ == "__main__":
    unittest.main()
