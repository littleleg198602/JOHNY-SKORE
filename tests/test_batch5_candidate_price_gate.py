"""Deterministic gate mechanics, never genuine forecast/price-history acceptance."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from market_checker_app.services import candidate_model_evaluation_service as candidate
from market_checker_app.services.specialist_label_availability_service import persist_label_observation
from tests import test_specialist_evaluation_service as history_helpers
from tests import test_batch4_evaluation_label_availability as label_helpers


class CandidateRecordedPriceGateTests(unittest.TestCase):
    def setUp(self):
        self.frame = history_helpers._history()
        self.frame["feature_payload_json"] = self.frame["feature_payload"].map(json.dumps)
        self.observations = history_helpers._observations(self.frame)

    def report(self, observations=None, **options):
        arguments = {"as_of": history_helpers.CUTOFF, "minimum_training_samples": 8,
                     "minimum_evaluation_samples": 1, "minimum_weeks": 1, "iterations": 20}
        arguments.update(options)
        return candidate.evaluate_candidate_walk_forward_strict(
            self.frame, label_observations=observations, **arguments)

    def test_legacy_label_only_report_stays_compatible_and_explicitly_unverified(self):
        legacy = candidate.evaluate_candidate_walk_forward(self.frame, minimum_training_samples=8,
            minimum_evaluation_samples=1, minimum_weeks=1, iterations=20)
        self.assertEqual("EVALUATED", legacy["status"])
        self.assertEqual("legacy_label_timestamp_diagnostic", legacy["report_type"])
        self.assertFalse(legacy["label_price_gate_applied"])
        self.assertFalse(legacy["label_price_provenance_verified"])
        self.assertFalse(legacy["out_of_sample_evaluation_verified"])
        strict = self.report()
        self.assertEqual("INSUFFICIENT_DATA", strict["status"])
        self.assertEqual([], strict["samples"])
        self.assertEqual({"MISSING_LABEL_OBSERVATION": 40}, strict["label_availability"]["heldout_label_reason_counts"])
        self.assertNotIn("metrics", strict)

    def test_fully_audited_fixture_path_matches_unchanged_formula_without_claiming_verified_oos(self):
        before = self.frame.copy(deep=True)
        observations_before = deepcopy(self.observations)
        strict = self.report(self.observations)
        legacy = candidate.evaluate_candidate_walk_forward(self.frame, minimum_training_samples=8,
            minimum_evaluation_samples=1, minimum_weeks=1, iterations=20)
        self.assertEqual("EVALUATED_EXPLORATORY", strict["status"])
        self.assertEqual(legacy["metrics"], strict["metrics"])
        self.assertEqual(32, len(strict["samples"]))
        self.assertTrue(strict["label_price_gate_applied"])
        self.assertFalse(strict["out_of_sample_evaluation_verified"])
        self.assertFalse(strict["publisher_authenticity_verified"])
        self.assertFalse(strict["activation_allowed"])
        self.assertFalse(strict["ranking_modified"])
        self.assertEqual({"DETERMINISTIC_TEST_FIXTURE": 40}, strict["label_availability"]["eligible_evidence_origin_counts"])
        for sample in strict["samples"]:
            self.assertEqual(64, len(sample["label_observation_hash"]))
            self.assertEqual({"asset", "benchmark"}, set(sample["label_price_capture_hashes"]))
        for fold in strict["folds"]:
            if fold["latest_training_label_available_at"]:
                self.assertLess(datetime.fromisoformat(fold["latest_training_label_available_at"]), datetime.fromisoformat(fold["as_of"]))
            self.assertTrue(set(fold["training_snapshot_ids"]).isdisjoint(fold["prediction_snapshot_ids"]))
        pd.testing.assert_frame_equal(before, self.frame)
        self.assertEqual(observations_before, self.observations)

    def test_archive_capture_at_prediction_boundary_cannot_train_that_prediction(self):
        delayed = history_helpers.START + timedelta(weeks=2)
        observations = deepcopy(self.observations)
        observations[:8] = [label_helpers._at_capture(item, delayed) for item in observations[:8]]
        report = self.report(observations)
        fold = next(item for item in report["folds"] if item["as_of"] == delayed.isoformat())
        self.assertEqual(8, fold["training_sample_count"])
        self.assertEqual(8, fold["training_rejection_reasons"]["LABEL_NOT_STRICTLY_BEFORE_PREDICTION"])
        later = next(item for item in report["folds"] if item["as_of"] == (delayed + timedelta(weeks=1)).isoformat())
        self.assertEqual(24, later["training_sample_count"])

    def test_later_conflicting_price_version_cannot_rewrite_an_earlier_training_fit(self):
        conflicts = deepcopy(self.observations[:8])
        for observation in conflicts:
            label_helpers._at_capture(observation, history_helpers.START + timedelta(weeks=3))
            observation["price_captures"]["asset"]["session_prices"][-1]["close"] = 200.0
            label_helpers._seal(observation)
        report = self.report(self.observations + conflicts)
        self.assertEqual(8, report["label_availability"]["heldout_label_reason_counts"]["CONFLICTING_RECORDED_LABEL_VERSIONS"])
        first_trained = next(fold for fold in report["folds"] if fold["as_of"] == (history_helpers.START + timedelta(weeks=1)).isoformat())
        self.assertEqual(8, first_trained["training_sample_count"])
        self.assertEqual(set(self.frame.iloc[:8]["snapshot_id"]), set(first_trained["training_snapshot_ids"]))

    def test_prediction_rows_pass_features_and_no_held_out_label_to_model_fitter(self):
        original = candidate.build_candidate_model_report
        frames = []

        def inspected(frame, **kwargs):
            frames.append((frame.copy(deep=True), kwargs))
            return original(frame, **kwargs)

        with patch.object(candidate, "build_candidate_model_report", side_effect=inspected):
            self.report(self.observations)
        self.assertTrue(frames)
        for frame, kwargs in frames:
            predictions = frame[frame["snapshot_id"].isin(kwargs["prediction_snapshot_ids"])]
            self.assertTrue((predictions["label_status"] == "PENDING").all())
            self.assertTrue(predictions["target_value"].isna().all())
            self.assertTrue(predictions["target_observed_at"].isna().all())

    def test_evaluation_cutoff_excludes_future_archived_labels_instead_of_scoring_them(self):
        cutoff = history_helpers.START + timedelta(weeks=4)
        report = self.report(self.observations, as_of=cutoff)
        self.assertEqual(8, report["label_availability"]["heldout_label_reason_counts"]["LABEL_ARCHIVE_AFTER_REPORT_CUTOFF"])
        for sample in report["samples"]:
            self.assertLessEqual(datetime.fromisoformat(sample["label_usable_at"]), cutoff)
        self.assertTrue(set(self.frame.iloc[-8:]["snapshot_id"]).isdisjoint({sample["snapshot_id"] for sample in report["samples"]}))

    def test_unknown_provider_revision_never_becomes_eligible_recorded_price_history(self):
        observations = deepcopy(self.observations)
        for observation in observations:
            observation["evidence_origin"] = "RECORDED_PRICE_FRAME"
            observation["price_captures"]["asset"]["revision_status"] = "UNKNOWN"
            label_helpers._seal(observation)
        report = self.report(observations)
        self.assertEqual("INSUFFICIENT_DATA", report["status"])
        self.assertEqual({"MISSING_PRICE_REVISION_PROVENANCE": 40}, report["label_availability"]["heldout_label_reason_counts"])
        self.assertEqual([], report["samples"])
        self.assertFalse(report["label_price_provenance_verified"])

    def test_missing_benchmark_and_tampered_price_hashes_are_visible_exclusions(self):
        observations = deepcopy(self.observations)
        for observation in observations[:8]:
            del observation["price_captures"]["benchmark"]
            label_helpers._seal(observation)
        for observation in observations[8:16]:
            observation["price_captures"]["asset"]["session_prices"][-1]["close"] += 1
        report = self.report(observations)
        self.assertEqual(8, report["label_availability"]["heldout_label_reason_counts"]["MISSING_ASSET_OR_BENCHMARK_CAPTURE"])
        self.assertEqual(8, report["label_availability"]["archive"]["rejected_observation_reasons"]["INVALID_LABEL_OBSERVATION_HASH"])
        self.assertEqual(8, report["label_availability"]["heldout_label_reason_counts"]["MISSING_LABEL_OBSERVATION"])

    def test_bounded_archive_prefix_cannot_supply_uninspected_labels_or_oos_acceptance(self):
        report = self.report(self.observations, maximum_label_observations=8)
        self.assertTrue(report["label_availability"]["archive"]["explicit_observation_limit_reached"])
        self.assertEqual(8, report["label_availability"]["eligible_heldout_label_count"])
        self.assertEqual(32, report["label_availability"]["heldout_label_reason_counts"]["MISSING_LABEL_OBSERVATION"])
        self.assertEqual("INSUFFICIENT_DATA", report["status"])
        self.assertFalse(report["out_of_sample_evaluation_verified"])

    def test_exact_sidecar_files_are_loaded_read_only_by_explicit_strict_path(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for observation in self.observations:
                persist_label_observation(observation, directory)
            before = {path.name: path.read_bytes() for path in directory.iterdir()}
            report = self.report(label_archive_directory=directory)
            self.assertEqual("EVALUATED_EXPLORATORY", report["status"])
            self.assertEqual(40, report["label_availability"]["archive"]["loaded_files"])
            self.assertEqual(before, {path.name: path.read_bytes() for path in directory.iterdir()})

    def test_duplicate_snapshot_ids_and_naive_times_never_enter_strict_folds(self):
        duplicated_id = self.frame.iloc[0]["snapshot_id"]
        self.frame = pd.concat([self.frame, self.frame.iloc[:1]], ignore_index=True)
        report = self.report(self.observations)
        self.assertEqual(2, report["label_availability"]["rejected_snapshot_reasons"]["DUPLICATE_SNAPSHOT_ID"])
        self.assertTrue(all(duplicated_id not in fold["training_snapshot_ids"] for fold in report["folds"]))
        self.frame = history_helpers._history()
        self.frame["feature_payload_json"] = self.frame["feature_payload"].map(json.dumps)
        self.frame["as_of"] = [datetime.fromisoformat(stamp).replace(tzinfo=None).isoformat() for stamp in self.frame["as_of"]]
        report = self.report(self.observations)
        self.assertEqual({"INVALID_SNAPSHOT_OR_LABEL_TIME": 40}, report["label_availability"]["rejected_snapshot_reasons"])
        self.assertEqual([], report["samples"])

    def test_explicit_strict_cutoff_and_numeric_budgets_fail_closed(self):
        with self.assertRaises(ValueError):
            self.report(self.observations, as_of=history_helpers.CUTOFF.replace(tzinfo=None))
        for option in ({"maximum_label_observations": 0}, {"minimum_training_samples": True},
                       {"iterations": 0}, {"positive_label_hurdle": float("nan")}, {"top_fraction": True}):
            with self.subTest(option=option), self.assertRaises(ValueError):
                self.report(self.observations, **option)

    def test_historical_cutoff_does_not_backdate_report_generation(self):
        generated_before = datetime.now(timezone.utc)
        report = self.report(self.observations)
        self.assertEqual(history_helpers.CUTOFF.isoformat(), report["as_of"])
        self.assertGreaterEqual(datetime.fromisoformat(report["generated_at"]), generated_before)
        self.assertNotEqual(report["generated_at"], report["as_of"])


if __name__ == "__main__":
    unittest.main()
