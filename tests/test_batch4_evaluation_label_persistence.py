from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from market_checker_app.models import RunMetadata
from market_checker_app.prediction_contract import (
    PRIMARY_PREDICTION_TARGET, build_point_in_time_snapshot, resolve_excess_return_label,
)
from market_checker_app.services.prediction_label_service import PredictionLabelService
from market_checker_app.services.specialist_evaluation_service import build_specialist_evaluation_report, read_archived_prediction_snapshots
from market_checker_app.services.specialist_label_availability_service import (
    load_label_observation, persist_label_observation,
)
from market_checker_app.services.us_equity_calendar import target_us_equity_window
from market_checker_app.storage.sqlite_store import SQLiteStore


SNAPSHOT_AT = datetime(2026, 1, 2, 22, tzinfo=timezone.utc)
LOGICAL_EVALUATION = datetime(2026, 1, 12, 22, tzinfo=timezone.utc)
# Deterministic test clock: these are artificial input frames, not live prices.
PHYSICAL_CAPTURE = datetime(2026, 10, 13, 13, tzinfo=timezone.utc)


def _store(directory: Path, *, snapshot_at: datetime = SNAPSHOT_AT, ticker: str = "AAPL") -> SQLiteStore:
    store = SQLiteStore(directory / "history.db")
    store.ensure_schema()
    run_id = store.insert_run(RunMetadata(started_at=snapshot_at, finished_at=snapshot_at,
        watchlist_size=1, processed_symbols=1, warnings_count=0, errors_count=0))
    store.save_prediction_snapshots([build_point_in_time_snapshot(run_id=run_id, ticker=ticker,
        observed_at=snapshot_at, feature_payload={"current_price": 100.0}, baseline_output={"action": "BUY"},
        provenance={"test_fixture": True}, benchmark_ticker="SPY")])
    return store


def _histories(*, snapshot_at: datetime = SNAPSHOT_AT, metadata: bool = False) -> dict[str, pd.DataFrame]:
    base, future = target_us_equity_window(snapshot_at, 5)
    sessions = [base, *future]
    histories = {
        "AAPL": pd.DataFrame({"Close": [100, 101, 102, 103, 104, 110]}, index=pd.to_datetime([session.session_date for session in sessions])),
        "SPY": pd.DataFrame({"Close": [100, 100, 101, 102, 103, 105]}, index=pd.to_datetime([session.session_date for session in sessions])),
    }
    if metadata:
        for history in histories.values():
            history.attrs["point_in_time_price_provenance"] = {
                "provider": "synthetic-unit-test", "price_basis": PRIMARY_PREDICTION_TARGET.price_basis,
                "price_method_version": PRIMARY_PREDICTION_TARGET.price_method_version, "dividends_included": False,
                "revision_status": "CAPTURED_CURRENT_VERSION", "corporate_action_status": "RECORDED_COMPLETE_WINDOW",
                "corporate_actions_checked_from": base.session_date.isoformat(),
                "corporate_actions_checked_through": future[-1].session_date.isoformat(),
                "corporate_actions_known_at": PHYSICAL_CAPTURE.isoformat(), "corporate_actions": [],
            }
    return histories


def _resolve(store: SQLiteStore, histories: dict, *, clock: datetime = PHYSICAL_CAPTURE, **options) -> dict:
    return PredictionLabelService(observation_clock=lambda: clock, **options).resolve_pending_snapshots(
        store=store, price_loader=lambda ticker: histories[ticker], as_of=LOGICAL_EVALUATION)


def _archives(directory: Path) -> list[Path]:
    return sorted((directory / "prediction_label_observations").glob("prediction_label_*.json"))


def _report(store: SQLiteStore, clock: datetime = PHYSICAL_CAPTURE) -> dict:
    return build_specialist_evaluation_report(read_archived_prediction_snapshots(store.db_path),
        as_of=clock, label_archive_directory=store.db_path.parent / "prediction_label_observations")


class Batch4EvaluationLabelPersistenceTests(unittest.TestCase):
    def test_new_label_uses_completion_clock_and_preserves_original_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            store = _store(directory)
            original = store.read_prediction_snapshots().to_dict("records")[0]
            result = _resolve(store, _histories())
            resolved = store.read_prediction_snapshots().to_dict("records")[0]
            self.assertEqual(1, result["resolved"])
            self.assertEqual(1, result["archive_written"])
            self.assertEqual(0, result["archive_failures"])
            self.assertAlmostEqual(0.05, resolved["target_value"])
            self.assertEqual(PHYSICAL_CAPTURE.isoformat(), resolved["target_observed_at"])
            for field in ("feature_payload_json", "provenance_json", "baseline_output_json", "snapshot_id", "as_of", "observed_at"):
                self.assertEqual(original[field], resolved[field])
            expected = resolve_excess_return_label(PredictionLabelService._snapshot_mapping(original),
                [100, 101, 102, 103, 104, 110], [100, 100, 101, 102, 103, 105], target_observed_at=PHYSICAL_CAPTURE)
            self.assertEqual(expected["snapshot_hash"], resolved["snapshot_hash"])
            archive = load_label_observation(_archives(directory)[0])
            self.assertEqual(PHYSICAL_CAPTURE.isoformat(), archive["observed_at"])
            self.assertEqual(PHYSICAL_CAPTURE.isoformat(), archive["captured_at"])
            self.assertEqual([100.0, 101.0, 102.0, 103.0, 104.0, 110.0],
                [price["close"] for price in archive["price_captures"]["asset"]["session_prices"]])
            self.assertEqual("RECORDED_PRICE_FRAME", archive["evidence_origin"])

    def test_missing_provider_metadata_is_recorded_as_unknown_not_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            store = _store(directory)
            _resolve(store, _histories())
            archive = load_label_observation(_archives(directory)[0])
            for capture in archive["price_captures"].values():
                self.assertFalse(capture["provenance_supplied"])
                self.assertEqual("UNKNOWN", capture["provider"])
                self.assertEqual("UNKNOWN", capture["revision_status"])
                self.assertEqual("UNKNOWN", capture["corporate_action_status"])
            report = _report(store)
            self.assertEqual(0, report["input"]["resolved_labels_known_at_cutoff"])
            self.assertEqual({"MISSING_PRICE_SOURCE_PROVENANCE": 1}, report["label_availability"]["label_reason_counts"])
            self.assertFalse(report["completion_verified"])

    def test_explicit_recorded_provenance_enables_label_audit_with_actual_clock(self):
        with tempfile.TemporaryDirectory() as temp:
            store = _store(Path(temp))
            _resolve(store, _histories(metadata=True))
            report = _report(store)
            self.assertEqual(1, report["input"]["resolved_labels_known_at_cutoff"])
            self.assertEqual(PHYSICAL_CAPTURE.isoformat(), report["label_availability"]["audited_labels"][0]["usable_at"])
            self.assertFalse(report["acceptance"]["out_of_sample_evaluation_verified"])
            earlier = _report(store, LOGICAL_EVALUATION)
            self.assertEqual(0, earlier["input"]["resolved_labels_known_at_cutoff"])
            self.assertEqual({"LABEL_AFTER_REPORT_CUTOFF": 1}, earlier["input"]["unusable_resolved_label_reasons"])

    def test_replay_is_idempotent_and_never_rewrites_final_legacy_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            store = _store(directory)
            original = PredictionLabelService._snapshot_mapping(store.read_prediction_snapshots().to_dict("records")[0])
            _, future = target_us_equity_window(SNAPSHOT_AT, 5)
            legacy = resolve_excess_return_label(original, [100, 101, 102, 103, 104, 110],
                [100, 100, 101, 102, 103, 105], target_observed_at=future[-1].close_at)
            store.update_prediction_snapshot_labels([legacy])
            before = store.read_prediction_snapshots().copy(deep=True)
            result = PredictionLabelService(observation_clock=lambda: PHYSICAL_CAPTURE).resolve_pending_snapshots(
                store=store, price_loader=lambda _: self.fail("A finalized legacy label must not be reloaded"), as_of=LOGICAL_EVALUATION)
            self.assertEqual(0, result["pending_before"])
            self.assertEqual(0, result["archive_written"])
            self.assertEqual([], _archives(directory))
            pd.testing.assert_frame_equal(before, store.read_prediction_snapshots())

    def test_new_label_replay_preserves_sidecar_bytes_and_loader_frames(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            store = _store(directory)
            histories = _histories(metadata=True)
            before = {ticker: history.copy(deep=True) for ticker, history in histories.items()}
            _resolve(store, histories)
            paths = _archives(directory)
            digest = hashlib.sha256(paths[0].read_bytes()).hexdigest()
            result = _resolve(store, histories, clock=PHYSICAL_CAPTURE + timedelta(days=1))
            self.assertEqual(0, result["resolved"])
            self.assertEqual(paths, _archives(directory))
            self.assertEqual(digest, hashlib.sha256(paths[0].read_bytes()).hexdigest())
            for ticker in histories:
                pd.testing.assert_frame_equal(before[ticker], histories[ticker])
                self.assertEqual(before[ticker].attrs, histories[ticker].attrs)

    def test_each_loader_and_completion_clock_remains_separate_from_session_event(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            store = _store(directory)
            histories = _histories(metadata=True)
            clocks = iter(PHYSICAL_CAPTURE + timedelta(seconds=offset) for offset in range(4))
            result = PredictionLabelService(observation_clock=lambda: next(clocks)).resolve_pending_snapshots(
                store=store, price_loader=lambda ticker: histories[ticker], as_of=LOGICAL_EVALUATION)
            self.assertEqual(1, result["resolved"])
            record = load_label_observation(_archives(directory)[0])
            self.assertEqual(PHYSICAL_CAPTURE.isoformat(), record["price_captures"]["asset"]["observed_at"])
            self.assertEqual((PHYSICAL_CAPTURE + timedelta(seconds=1)).isoformat(), record["price_captures"]["benchmark"]["observed_at"])
            self.assertEqual((PHYSICAL_CAPTURE + timedelta(seconds=2)).isoformat(), record["captured_at"])
            completed = PHYSICAL_CAPTURE + timedelta(seconds=3)
            self.assertEqual(completed.isoformat(), store.read_prediction_snapshots().iloc[0]["target_observed_at"])
            self.assertEqual(1, _report(store, completed)["input"]["resolved_labels_known_at_cutoff"])
            self.assertEqual(0, _report(store, completed - timedelta(microseconds=1))["input"]["resolved_labels_known_at_cutoff"])

    def test_later_loader_mutation_cannot_change_already_observed_asset_prices(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            store = _store(directory)
            histories = _histories()
            def loader(ticker):
                if ticker == "SPY":
                    histories["AAPL"].iloc[-1, 0] = 200
                return histories[ticker]
            result = PredictionLabelService(observation_clock=lambda: PHYSICAL_CAPTURE).resolve_pending_snapshots(
                store=store, price_loader=loader, as_of=LOGICAL_EVALUATION)
            self.assertEqual(1, result["resolved"])
            self.assertAlmostEqual(0.05, store.read_prediction_snapshots().iloc[0]["target_value"])
            self.assertEqual(110.0, load_label_observation(_archives(directory)[0])["price_captures"]["asset"]["session_prices"][-1]["close"])

    def test_archive_write_failure_preserves_pending_label_for_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            store = _store(Path(temp))
            before = store.read_prediction_snapshots().copy(deep=True)
            with patch("market_checker_app.services.prediction_label_service.persist_label_observation", side_effect=OSError("disk unavailable")):
                result = _resolve(store, _histories())
            self.assertEqual(0, result["resolved"])
            self.assertEqual(1, result["archive_failures"])
            self.assertEqual(1, result["deferred"])
            self.assertEqual(0, result["source_failures"])
            pd.testing.assert_frame_equal(before, store.read_prediction_snapshots())

    def test_capture_survives_a_process_failure_before_database_label_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            store = _store(directory)
            with patch.object(store, "update_prediction_snapshot_labels", side_effect=RuntimeError("interrupted before label commit")):
                with self.assertRaises(RuntimeError):
                    _resolve(store, _histories(metadata=True))
            first = _archives(directory)[0]
            digest = hashlib.sha256(first.read_bytes()).hexdigest()
            self.assertEqual("PENDING", store.read_prediction_snapshots().iloc[0]["label_status"])
            later = PHYSICAL_CAPTURE + timedelta(hours=1)
            result = _resolve(store, _histories(metadata=True), clock=later)
            self.assertEqual(1, result["resolved"])
            self.assertEqual(2, len(_archives(directory)))
            self.assertEqual(digest, hashlib.sha256(first.read_bytes()).hexdigest())
            self.assertEqual(later.isoformat(), _report(store, later)["label_availability"]["audited_labels"][0]["usable_at"])

    def test_immutable_capture_persistence_is_atomic_idempotent_and_tamper_strict(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            store = _store(directory)
            _resolve(store, _histories())
            path = _archives(directory)[0]
            record = load_label_observation(path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(path, persist_label_observation(record, path.parent))
            self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                persist_label_observation(record, path.parent)
            self.assertEqual("{}", path.read_text(encoding="utf-8"))
            self.assertFalse(list(path.parent.glob("*.tmp")))

    def test_explicit_archive_directory_is_used_without_changing_target_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            store = _store(directory)
            override = directory / "chosen-captures"
            _resolve(store, _histories(), label_archive_directory=override)
            self.assertEqual(1, len(list(override.glob("prediction_label_*.json"))))
            self.assertFalse((directory / "prediction_label_observations").exists())

    def test_secrets_and_unrecognized_metadata_are_not_exported_or_inferred(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            store = _store(directory)
            histories = _histories(metadata=True)
            secret = "sensitive-sentinel-DO-NOT-EXPORT"
            metadata = histories["AAPL"].attrs["point_in_time_price_provenance"]
            metadata.update(password=secret, request_url="https://example.com/?token=" + secret,
                provider="https://example.com/?token=" + secret,
                corporate_actions=[{"kind": "SPLIT", "effective_session": "2026-01-06", "known_at": secret, "ratio": 2}])
            histories["AAPL"].attrs["other_data"] = {"credential": secret}
            _resolve(store, histories)
            text = _archives(directory)[0].read_text(encoding="utf-8")
            self.assertNotIn(secret, text)
            self.assertNotIn("request_url", text)
            capture = json.loads(text)["price_captures"]["asset"]
            self.assertEqual("UNKNOWN", capture["provider"])
            self.assertEqual("UNKNOWN", capture["corporate_action_status"])
            self.assertTrue(capture["corporate_action_record_projection_incomplete"])

    def test_partial_action_projection_never_upgrades_to_a_complete_window(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            store = _store(directory)
            histories = _histories(metadata=True)
            histories["AAPL"].attrs["point_in_time_price_provenance"]["corporate_actions"] = [
                {"kind": "SPLIT", "effective_session": "2026-01-06", "known_at": PHYSICAL_CAPTURE.isoformat(), "ratio": 2.0}] * 101
            _resolve(store, histories)
            capture = load_label_observation(_archives(directory)[0])["price_captures"]["asset"]
            self.assertEqual(100, len(capture["corporate_actions"]))
            self.assertEqual("UNKNOWN", capture["corporate_action_status"])
            self.assertEqual(0, _report(store)["input"]["resolved_labels_known_at_cutoff"])

    def test_provider_cache_clock_is_provenance_only_and_cannot_backdate_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            store = _store(Path(temp))
            histories = _histories(metadata=True)
            histories["AAPL"].attrs["point_in_time_price_provenance"]["provider_observed_at"] = LOGICAL_EVALUATION.isoformat()
            _resolve(store, histories)
            self.assertEqual(PHYSICAL_CAPTURE.isoformat(), _report(store)["label_availability"]["audited_labels"][0]["usable_at"])

    def test_future_provider_clock_is_preserved_as_a_conflict_not_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            store = _store(Path(temp))
            histories = _histories(metadata=True)
            histories["AAPL"].attrs["point_in_time_price_provenance"]["provider_observed_at"] = (PHYSICAL_CAPTURE + timedelta(days=1)).isoformat()
            _resolve(store, histories)
            self.assertEqual({"INVALID_PROVIDER_PRICE_OBSERVATION": 1}, _report(store)["label_availability"]["label_reason_counts"])

    def test_naive_physical_clock_is_rejected_without_finalizing_a_label(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            store = _store(directory)
            with self.assertRaises(ValueError):
                _resolve(store, _histories(), clock=datetime(2026, 10, 13))
            self.assertEqual("PENDING", store.read_prediction_snapshots().iloc[0]["label_status"])
            self.assertFalse((directory / "prediction_label_observations").exists())

    def test_loader_future_fixture_cannot_resolve_before_physical_horizon_close(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            future_snapshot = datetime(2027, 1, 4, 22, tzinfo=timezone.utc)
            store = _store(directory, snapshot_at=future_snapshot)
            result = PredictionLabelService(observation_clock=lambda: PHYSICAL_CAPTURE).resolve_pending_snapshots(
                store=store, price_loader=lambda ticker: _histories(snapshot_at=future_snapshot)[ticker],
                as_of=datetime(2027, 2, 1, tzinfo=timezone.utc))
            self.assertEqual(0, result["resolved"])
            self.assertEqual(1, result["deferred"])
            self.assertFalse((directory / "prediction_label_observations").exists())


if __name__ == "__main__":
    unittest.main()
