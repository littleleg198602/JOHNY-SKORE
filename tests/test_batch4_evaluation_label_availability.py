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

from market_checker_app.prediction_contract import canonical_hash
from market_checker_app.services.specialist_evaluation_service import build_specialist_evaluation_report
from market_checker_app.services.specialist_incremental_archive_service import load_specialist_source_run_archive
from market_checker_app.services.specialist_label_availability_service import (
    MAX_LABEL_OBSERVATION_BYTES, audit_label_observation, audit_snapshot_label,
    label_observation_filename, load_label_observation, load_label_observations,
    prepare_label_observations,
)
from tests.test_specialist_evaluation_service import (
    CUTOFF, START, _history, _label_observation, _observations, _snapshot,
)


def _seal(record: dict) -> dict:
    for capture in record.get("price_captures", {}).values():
        capture["capture_hash"] = canonical_hash({key: value for key, value in capture.items() if key != "capture_hash"})
    record["observation_hash"] = canonical_hash({key: value for key, value in record.items() if key != "observation_hash"})
    return record


def _at_capture(record: dict, clock: datetime) -> dict:
    """Move all actual capture clocks together in a deterministic unit fixture."""
    stamp = clock.isoformat()
    for field in ("observed_at", "available_at", "captured_at"):
        record[field] = stamp
    for capture in record["price_captures"].values():
        for field in ("observed_at", "available_at", "corporate_actions_known_at"):
            capture[field] = stamp
        for price in capture["session_prices"]:
            price["observed_at"] = stamp
            price["available_at"] = stamp
    return _seal(record)


def _report(history: pd.DataFrame, observations=None, **options) -> dict:
    return build_specialist_evaluation_report(history, as_of=CUTOFF,
        minimum_training_samples=8, minimum_evaluation_samples=8,
        minimum_weeks=2, iterations=30, label_observations=observations, **options)


class Batch4EvaluationLabelAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = _snapshot(0)
        self.observation = _label_observation(self.snapshot)

    def assertReason(self, reason: str, observation: dict):
        result = audit_label_observation(self.snapshot, observation, as_of=CUTOFF)
        self.assertEqual("INSUFFICIENT_DATA", result["status"])
        self.assertEqual(reason, result["reason"])
        self.assertIsNone(result["usable_at"])

    def test_legacy_close_timestamp_is_visible_but_cannot_train_without_prices(self):
        history = _history()
        before = history.copy(deep=True)
        report = _report(history)
        self.assertEqual(40, report["input"]["resolved_label_timestamps_at_cutoff"])
        self.assertEqual(0, report["input"]["resolved_labels_known_at_cutoff"])
        self.assertEqual({"MISSING_LABEL_OBSERVATION": 40}, report["label_availability"]["label_reason_counts"])
        self.assertTrue(all(row["matched_metrics"] is None for row in report["comparisons"].values()))
        self.assertEqual(40, report["feature_group_coverage"]["sec_fundamentals"]["observed_feature_snapshot_count"])
        self.assertFalse(report["activation_allowed"])
        self.assertFalse(report["acceptance"]["out_of_sample_evaluation_verified"])
        pd.testing.assert_frame_equal(before, history)

    def test_recorded_prices_recompute_the_label_and_keep_captures_independent(self):
        before = deepcopy(self.observation)
        result = audit_label_observation(self.snapshot, self.observation, as_of=CUTOFF)
        self.assertEqual("ELIGIBLE_RECORDED_LABEL", result["status"])
        self.assertEqual(6, result["complete_forward_session_count"])
        self.assertTrue(result["target_value_recomputed"])
        self.assertFalse(result["publisher_authenticity_verified"])
        self.assertEqual(before, self.observation)
        self.assertEqual(self.observation["captured_at"], result["usable_at"])

    def test_current_capture_cannot_backdate_to_exchange_close_or_old_source_clock(self):
        later = START + timedelta(weeks=2)
        observation = _at_capture(deepcopy(self.observation), later)
        result = audit_label_observation(self.snapshot, observation, as_of=CUTOFF)
        self.assertEqual(later.isoformat(), result["usable_at"])
        self.assertTrue(result["target_timestamp_precedes_capture"])
        early = audit_label_observation(self.snapshot, observation, as_of=later - timedelta(seconds=1))
        self.assertEqual("LABEL_ARCHIVE_AFTER_REPORT_CUTOFF", early["reason"])

    def test_walk_forward_uses_physical_capture_not_retrospective_endpoint_time(self):
        history = _history(4)
        observations = _observations(history)
        delayed_capture = START + timedelta(weeks=2)
        observations[:8] = [_at_capture(observation, delayed_capture) for observation in observations[:8]]
        report = _report(history, observations)
        folds = report["comparisons"]["sec_fundamentals"]["folds"]
        self.assertEqual((START + timedelta(weeks=2)).isoformat(), folds[0]["as_of"])
        self.assertEqual(8, folds[0]["training_sample_count"])
        # The label captured exactly at that prediction clock may not train
        # it. At the following clock the same immutable observation may train.
        self.assertEqual(24, folds[1]["training_sample_count"])
        self.assertLess(datetime.fromisoformat(folds[0]["latest_training_label_at"]), delayed_capture)

    def test_missing_middle_session_cannot_shift_the_five_session_window(self):
        self.observation["price_captures"]["asset"]["session_prices"].pop(2)
        self.assertReason("INCOMPLETE_FORWARD_SESSION_WINDOW", _seal(self.observation))

    def test_duplicate_or_substitute_session_is_not_a_complete_forward_window(self):
        for kind in ("duplicate", "outside"):
            with self.subTest(kind=kind):
                observation = deepcopy(self.observation)
                prices = observation["price_captures"]["benchmark"]["session_prices"]
                prices[2]["session_date"] = prices[1]["session_date"] if kind == "duplicate" else "2026-04-01"
                self.assertReason("INCOMPLETE_FORWARD_SESSION_WINDOW", _seal(observation))

    def test_missing_benchmark_and_incompatible_total_return_prices_are_excluded(self):
        missing = deepcopy(self.observation)
        del missing["price_captures"]["benchmark"]
        self.assertReason("MISSING_ASSET_OR_BENCHMARK_CAPTURE", _seal(missing))
        wrong_basis = deepcopy(self.observation)
        wrong_basis["price_captures"]["benchmark"]["dividends_included"] = True
        self.assertReason("INCOMPATIBLE_LABEL_PRICE_METHOD", _seal(wrong_basis))

    def test_wrong_instrument_or_target_version_does_not_reuse_another_label(self):
        wrong = deepcopy(self.observation)
        wrong["price_captures"]["asset"]["ticker"] = "OTHER"
        self.assertReason("INVALID_PRICE_SOURCE_IDENTITY", _seal(wrong))
        wrong = deepcopy(self.observation)
        wrong["target_version"] = "old-price-return-target"
        self.assertReason("LABEL_OBSERVATION_SNAPSHOT_MISMATCH", _seal(wrong))

    def test_label_value_must_match_both_exact_windows_not_a_claimed_scalar(self):
        wrong = deepcopy(self.observation)
        wrong["price_captures"]["asset"]["session_prices"][-1]["close"] = 200.0
        self.assertReason("LABEL_VALUE_DIFFERS_FROM_CAPTURE", _seal(wrong))
        wrong = deepcopy(self.observation)
        wrong["target_value"] = 1.0
        self.assertReason("LABEL_VALUE_DIFFERS_FROM_CAPTURE", _seal(wrong))

    def test_individual_prices_need_actual_availability_after_their_session_close(self):
        early = deepcopy(self.observation)
        early["price_captures"]["asset"]["session_prices"][-1]["observed_at"] = START.isoformat()
        self.assertReason("INVALID_SESSION_PRICE_AVAILABILITY", _seal(early))
        late = deepcopy(self.observation)
        late["price_captures"]["asset"]["session_prices"][-1]["available_at"] = CUTOFF.isoformat()
        self.assertReason("INVALID_SESSION_PRICE_AVAILABILITY", _seal(late))

    def test_naive_or_future_source_observation_cannot_hide_behind_archive_time(self):
        for timestamp in ("2026-03-10T22:00:00", CUTOFF.isoformat()):
            with self.subTest(timestamp=timestamp):
                observation = deepcopy(self.observation)
                observation["price_captures"]["asset"]["observed_at"] = timestamp
                self.assertReason("INVALID_PRICE_CAPTURE_AVAILABILITY", _seal(observation))

    def test_unknown_revision_and_partial_corporate_actions_are_missing_data(self):
        for field, value, reason in (
            ("revision_status", "UNKNOWN", "MISSING_PRICE_REVISION_PROVENANCE"),
            ("corporate_action_status", "PARTIAL", "INCOMPLETE_CORPORATE_ACTION_PROVENANCE"),
            ("corporate_actions_checked_through", "2026-03-03", "INCOMPLETE_CORPORATE_ACTION_WINDOW"),
        ):
            with self.subTest(field=field):
                observation = deepcopy(self.observation)
                observation["price_captures"]["asset"][field] = value
                self.assertReason(reason, _seal(observation))

    def test_known_revision_is_usable_only_after_actual_retrieval_and_capture(self):
        later = START + timedelta(weeks=2)
        observation = _at_capture(deepcopy(self.observation), later)
        capture = observation["price_captures"]["asset"]
        capture.update(revision_status="KNOWN_REVISION", revision_known_at=(later - timedelta(hours=1)).isoformat())
        result = audit_label_observation(self.snapshot, _seal(observation), as_of=CUTOFF)
        self.assertEqual(later.isoformat(), result["usable_at"])
        capture["revision_known_at"] = (later + timedelta(hours=1)).isoformat()
        self.assertReason("INVALID_PRICE_REVISION_KNOWLEDGE", _seal(observation))

    def test_future_split_knowledge_cannot_be_backdated_into_prior_capture(self):
        capture = self.observation["price_captures"]["asset"]
        capture["corporate_actions"] = [{"kind": "SPLIT", "effective_session": "2026-03-05", "ratio": 2.0,
            "known_at": (START + timedelta(weeks=2)).isoformat()}]
        self.assertReason("INVALID_CORPORATE_ACTION_KNOWLEDGE", _seal(self.observation))

    def test_dated_split_and_dividend_records_preserve_the_price_only_target(self):
        capture = self.observation["price_captures"]["asset"]
        capture["corporate_actions"] = [
            {"kind": "SPLIT", "effective_session": "2026-03-05", "ratio": 2.0,
             "known_at": (START + timedelta(days=1)).isoformat()},
            {"kind": "DIVIDEND", "effective_session": "2026-03-06",
             "known_at": (START + timedelta(days=2)).isoformat()},
        ]
        result = audit_label_observation(self.snapshot, _seal(self.observation), as_of=CUTOFF)
        self.assertEqual("ELIGIBLE_RECORDED_LABEL", result["status"])
        self.assertTrue(result["target_value_recomputed"])
        self.assertEqual(6, len(result["required_session_dates"]))
        self.assertEqual("CAPTURED_CURRENT_VERSION", result["source_price_windows"]["asset"]["revision_status"])
        self.assertIsNone(result["source_price_windows"]["asset"]["revision_known_at"])

    def test_conflicting_recorded_revision_remains_visible_instead_of_using_old_value(self):
        changed = deepcopy(self.observation)
        changed["price_captures"]["asset"]["session_prices"][-1]["close"] = 120.0
        _seal(changed)
        result = audit_snapshot_label(self.snapshot, [self.observation, changed], as_of=CUTOFF)
        self.assertEqual("CONFLICTING_RECORDED_LABEL_VERSIONS", result["reason"])
        # A future correction cannot alter an earlier cutoff.
        changed = _at_capture(changed, CUTOFF + timedelta(days=1))
        self.assertEqual("ELIGIBLE_RECORDED_LABEL", audit_snapshot_label(self.snapshot,
            [self.observation, changed], as_of=CUTOFF)["status"])

    def test_hash_tampering_is_rejected_before_prices_enter_evaluation(self):
        self.observation["price_captures"]["asset"]["session_prices"][0]["close"] = 80.0
        self.assertReason("INVALID_LABEL_OBSERVATION_HASH", self.observation)
        self.observation["observation_hash"] = canonical_hash({key: value for key, value in self.observation.items() if key != "observation_hash"})
        self.assertReason("INVALID_PRICE_CAPTURE_HASH", self.observation)

    def test_nonfinite_boolean_and_zero_prices_are_never_zero_return_labels(self):
        for value in (float("nan"), True, 0.0, -1.0):
            with self.subTest(value=value):
                observation = deepcopy(self.observation)
                observation["price_captures"]["asset"]["session_prices"][0]["close"] = value
                _seal(observation)
                result = audit_label_observation(self.snapshot, observation, as_of=CUTOFF)
                self.assertEqual("INSUFFICIENT_DATA", result["status"])
                self.assertIsNone(result["usable_at"])

    def test_actual_file_contents_and_filename_must_match_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / label_observation_filename(self.observation)
            path.write_text(json.dumps(self.observation), encoding="utf-8")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            report = _report(pd.DataFrame([self.snapshot]), label_archive_directory=Path(temp))
            self.assertEqual(1, report["input"]["resolved_labels_known_at_cutoff"])
            self.assertEqual(1, report["label_availability"]["loaded_files"])
            self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(self.observation, load_label_observation(path))
            path.unlink()
            forged = Path(temp) / ("prediction_label_" + self.snapshot["snapshot_id"] + "_" + "0" * 64 + ".json")
            forged.write_text(json.dumps(self.observation), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "FILENAME_HASH_MISMATCH"):
                load_label_observation(forged)
            records, metadata = load_label_observations(Path(temp))
            self.assertEqual([], records)
            self.assertEqual(1, metadata["rejected_files"])

    def test_symlink_oversize_and_absent_archive_cannot_be_treated_as_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            missing = directory / "absent"
            self.assertEqual([], load_label_observations(missing)[0])
            self.assertFalse(missing.exists())
            path = directory / label_observation_filename(self.observation)
            path.write_bytes(b" " * (MAX_LABEL_OBSERVATION_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "BYTE_LIMIT"):
                load_label_observation(path)
            if hasattr(Path, "symlink_to"):
                try:
                    link = directory / ("prediction_label_" + self.snapshot["snapshot_id"] + "_" + "1" * 64 + ".json")
                    link.symlink_to(path)
                except OSError:
                    pass  # Windows without symlink permission still checks bounded bytes.
                else:
                    with self.assertRaisesRegex(ValueError, "INVALID_LABEL_OBSERVATION_PATH"):
                        load_label_observation(link)

    def test_bounded_observation_and_directory_scan_report_uninspected_data(self):
        more = _label_observation(_snapshot(1))
        indexed, metadata = prepare_label_observations([self.observation, more], maximum=1)
        self.assertEqual(1, len(indexed))
        self.assertTrue(metadata["explicit_observation_limit_reached"])
        with tempfile.TemporaryDirectory() as temp:
            for index in range(4):
                (Path(temp) / f"unrelated{index}.txt").write_text("unrelated", encoding="utf-8")
            records, metadata = load_label_observations(Path(temp), maximum=1)
            self.assertEqual([], records)
            self.assertTrue(metadata["directory_scan_limit_reached"])
        for maximum in (0, True, 10001):
            with self.subTest(maximum=maximum), self.assertRaises(ValueError):
                prepare_label_observations([], maximum=maximum)

    def test_total_byte_budget_limits_explicit_and_file_capture_population(self):
        more = _label_observation(_snapshot(1))
        record_bytes = len(json.dumps(self.observation, sort_keys=True).encode("utf-8"))
        with patch("market_checker_app.services.specialist_label_availability_service.MAX_TOTAL_LABEL_OBSERVATION_BYTES", record_bytes + 1):
            indexed, metadata = prepare_label_observations([self.observation, more])
            self.assertEqual(1, len(indexed))
            self.assertTrue(metadata["total_byte_limit_reached"])
            with tempfile.TemporaryDirectory() as temp:
                for observation in (self.observation, more):
                    (Path(temp) / label_observation_filename(observation)).write_text(json.dumps(observation), encoding="utf-8")
                records, metadata = load_label_observations(Path(temp))
                self.assertEqual(1, len(records))
                self.assertTrue(metadata["total_byte_limit_reached"])

    def test_direct_audit_requires_an_explicit_timezone(self):
        with self.assertRaises(ValueError):
            audit_label_observation(self.snapshot, self.observation, as_of=datetime(2026, 5, 1))

    def test_rejected_observation_does_not_turn_missing_label_into_a_loss(self):
        self.observation["observation_hash"] = "0" * 64
        report = _report(pd.DataFrame([self.snapshot]), [self.observation])
        self.assertEqual(0, report["input"]["resolved_labels_known_at_cutoff"])
        self.assertEqual(1, report["input"]["resolved_label_timestamps_at_cutoff"])
        self.assertEqual({"INVALID_LABEL_OBSERVATION_HASH": 1}, report["label_availability"]["rejected_observation_reasons"])
        self.assertEqual({"MISSING_LABEL_OBSERVATION": 1}, report["label_availability"]["label_reason_counts"])
        self.assertTrue(all(item["matched_metrics"] is None for item in report["comparisons"].values()))

    def test_real_fdic_source_archive_is_source_only_not_forward_price_evidence(self):
        directory = Path(__file__).resolve().parents[1] / "evidence/batch3_evaluation_saved_payload_archives_20261012/fdic"
        paths = sorted(directory.glob("specialist_source_run_*.json"))
        self.assertEqual(3, len(paths))
        archive = load_specialist_source_run_archive(paths[0])
        self.assertEqual("fdic", archive["source"])
        self.assertEqual("CAPTURED_PUBLISHER_PAYLOAD_REPLAY", archive["evidence_origin"])
        self.assertGreater(datetime.fromisoformat(archive["available_at"]), datetime(2026, 10, 1, tzinfo=timezone.utc))
        report = _report(pd.DataFrame([self.snapshot]), [archive])
        self.assertEqual({"UNSUPPORTED_LABEL_OBSERVATION": 1}, report["label_availability"]["rejected_observation_reasons"])
        self.assertEqual(0, report["input"]["resolved_labels_known_at_cutoff"])
        self.assertFalse(report["completion_verified"])


if __name__ == "__main__":
    unittest.main()
