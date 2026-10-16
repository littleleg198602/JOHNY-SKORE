from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from market_checker_app.models import RunMetadata
from market_checker_app.prediction_contract import (
    build_point_in_time_snapshot, canonical_hash, resolve_excess_return_label,
)
from market_checker_app.services.agent_feature_service import AGENT_FEATURE_VERSION
from market_checker_app.services.candidate_model_service import (
    CORE_MARKET_FEATURE_PATHS, build_candidate_model_report,
)
from market_checker_app.services.prediction_label_service import PredictionLabelService
from market_checker_app.services.sec_fundamental_feature_service import SEC_FUNDAMENTAL_FEATURE_VERSION
from market_checker_app.services.specialist_evaluation_service import (
    build_specialist_evaluation_report, read_archived_prediction_snapshots,
)
from market_checker_app.services.specialist_label_availability_service import LABEL_OBSERVATION_SCHEMA_VERSION
from market_checker_app.prediction_contract import PRIMARY_PREDICTION_TARGET
from market_checker_app.services.us_equity_calendar import target_us_equity_window
from market_checker_app.specialist_evaluation import main
from market_checker_app.storage.sqlite_store import SQLiteStore


START = datetime(2026, 3, 2, 22, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 5, 1, tzinfo=timezone.utc)


def _payload(seed: float, clock: datetime) -> dict:
    stamp = clock.isoformat()
    return {
        "market_factors": {"version": "market_factors_v3", "as_of": stamp,
            "asset_returns": {"5d": seed / 100, "20d": seed / 80, "60d": seed / 60},
            "relative_returns": {"5d": seed / 120, "20d": seed / 100, "60d": seed / 90},
            "realized_volatility": {"20d_annualized": 0.2 + abs(seed) / 1000},
            "drawdown": {"252d": -0.15 + seed / 1000},
            "feature_contract": {"available_at": stamp, "observed_at": stamp}},
        "sec_fundamentals": {"feature_version": SEC_FUNDAMENTAL_FEATURE_VERSION,
            "as_of": stamp, "availability_at": stamp,
            "values": {"revenue_yoy_pct": seed * 10, "operating_margin_pct": seed + 10,
                       "debt_to_assets_ratio": 0.5 + seed / 100}},
        "agent_features": {"version": AGENT_FEATURE_VERSION, "as_of": stamp, "available_at": stamp,
            "regulatory": {"signed_event_score": seed, "verified_event_count": abs(seed)},
            "governance": {"event_count": abs(seed) + 1, "risk_event_count": abs(seed)},
            "forensics": {"risk_score": abs(seed) * 10, "hard_veto_count": float(seed < 0)},
            "claims": {"corroborated_count": max(seed, 0), "contradicted_count": max(-seed, 0),
                       "unresolved_count": abs(seed)},
            "missingness": {"regulatory_events": False, "governance_events": False,
                            "forensic_evidence": False, "research_claims": False},
            "provenance": {"point_in_time": True, "orchestration_id": "synthetic-unit-test"}},
    }


def _snapshot(index: int = 0, week: int = 0, *, offset: timedelta = timedelta()) -> dict:
    clock = START + timedelta(weeks=week) + offset
    seed = float(index - 3)
    snapshot = build_point_in_time_snapshot(run_id=week * 100 + index + 1 + int(offset.total_seconds()),
        ticker=f"TEST{index}", observed_at=clock,
        feature_payload=_payload(seed, clock), baseline_output={"final_total_score": 100},
        provenance={"source": "synthetic-unit-test-not-live-data"})
    _, horizon = target_us_equity_window(clock, 5)
    return resolve_excess_return_label(snapshot,
        [100] * 5 + [103 if seed > 0 else 97], [100] * 6,
        target_observed_at=horizon[-1].close_at)


def _rehash(row: dict) -> dict:
    row["snapshot_hash"] = canonical_hash({key: value for key, value in row.items() if key != "snapshot_hash"})
    return row


def _history(weeks: int = 5) -> pd.DataFrame:
    return pd.DataFrame([_snapshot(index, week) for week in range(weeks) for index in range(8)])


def _label_observation(snapshot: dict) -> dict:
    """Explicit synthetic prices for walk-forward mechanics, never live proof."""
    snapshot_at = datetime.fromisoformat(snapshot["as_of"])
    base, horizon = target_us_equity_window(snapshot_at, 5)
    sessions = [base, *horizon]
    stamp = horizon[-1].close_at.isoformat()
    captures = {}
    for role, ticker in (("asset", snapshot["ticker"]), ("benchmark", snapshot["benchmark_ticker"])):
        target = float(snapshot["target_value"]) if role == "asset" else 0.0
        values = [100.0] * 5 + [100.0 * (1.0 + target)]
        capture = {"ticker": ticker, "provider": "synthetic-unit-test",
            "price_basis": PRIMARY_PREDICTION_TARGET.price_basis,
            "price_method_version": PRIMARY_PREDICTION_TARGET.price_method_version,
            "dividends_included": False, "observed_at": stamp, "available_at": stamp,
            "revision_status": "CAPTURED_CURRENT_VERSION",
            "corporate_action_status": "RECORDED_COMPLETE_WINDOW",
            "corporate_actions_checked_from": base.session_date.isoformat(),
            "corporate_actions_checked_through": horizon[-1].session_date.isoformat(),
            "corporate_actions_known_at": stamp, "corporate_actions": [],
            "session_prices": [{"session_date": session.session_date.isoformat(), "close": value,
                "observed_at": stamp, "available_at": stamp} for session, value in zip(sessions, values)]}
        capture["capture_hash"] = canonical_hash(capture)
        captures[role] = capture
    observation = {"schema_version": LABEL_OBSERVATION_SCHEMA_VERSION,
        "snapshot_id": snapshot["snapshot_id"], "ticker": snapshot["ticker"],
        "benchmark_ticker": snapshot["benchmark_ticker"], "target_version": snapshot["target_version"],
        "horizon_trading_days": snapshot["horizon_trading_days"], "snapshot_as_of": snapshot["as_of"],
        "target_value": snapshot["target_value"], "observed_at": stamp,
        "available_at": stamp, "captured_at": stamp,
        "evidence_origin": "DETERMINISTIC_TEST_FIXTURE", "price_captures": captures}
    observation["observation_hash"] = canonical_hash(observation)
    return observation


def _observations(frame: pd.DataFrame) -> list[dict]:
    return [_label_observation(row) for row in frame.to_dict("records")
            if row.get("label_status") == "RESOLVED"]


def _report(frame: pd.DataFrame, **options) -> dict:
    return build_specialist_evaluation_report(frame, as_of=CUTOFF,
        minimum_training_samples=8, minimum_evaluation_samples=8,
        minimum_weeks=2, iterations=30, label_observations=_observations(frame), **options)


class SpecialistEvaluationServiceTests(unittest.TestCase):
    def test_empty_archive_returns_all_specialists_and_honest_insufficiency(self):
        report = build_specialist_evaluation_report(pd.DataFrame(), as_of=CUTOFF)
        self.assertEqual("INSUFFICIENT_DATA", report["status"])
        self.assertEqual(21, len(report["specialist_coverage"]))
        self.assertEqual(0, report["input"]["snapshot_rows"])
        self.assertFalse(report["completion_verified"])
        self.assertFalse(report["acceptance"]["out_of_sample_evaluation_verified"])
        self.assertTrue(all(item["sample_count"] == 0 for item in report["comparisons"].values()))
        self.assertTrue(all(item["observed_feature_snapshot_count"] == 0 for item in report["specialist_coverage"]))
        self.assertTrue(all(item["eligible_labeled_snapshot_count"] == 0 for item in report["specialist_coverage"]))

    def test_matched_walk_forward_is_reproducible_and_cannot_activate_scores(self):
        history = _history()
        before = history.copy(deep=True)
        report = _report(history)
        self.assertEqual(report, _report(history.sample(frac=1, random_state=19)))
        pd.testing.assert_frame_equal(before, history)
        sec = report["comparisons"]["sec_fundamentals"]
        self.assertEqual("EVALUATED_EXPLORATORY", sec["status"])
        self.assertEqual(32, sec["sample_count"])
        self.assertEqual("matched_market_logistic", sec["baseline"])
        for fold in sec["folds"]:
            self.assertLess(datetime.fromisoformat(fold["latest_training_label_at"]), datetime.fromisoformat(fold["as_of"]))
            self.assertTrue(set(fold["training_snapshot_ids"]).isdisjoint(fold["prediction_snapshot_ids"]))
        self.assertEqual("paired_weekly_bootstrap", sec["matched_metrics"]["paired_candidate_minus_baseline_intervals"]["brier_score"]["method"])
        self.assertTrue(report["analysis_only"])
        self.assertFalse(report["ranking_modified"])
        self.assertFalse(report["activation_allowed"])

    def test_vectorized_diagnostic_matches_existing_shadow_logistic_equations(self):
        history = _history(3)
        report = _report(history)
        source_rows = history.to_dict("records")
        for row in source_rows:
            row["feature_payload_json"] = json.dumps(row["feature_payload"])
        first_fold = report["comparisons"]["sec_fundamentals"]["folds"][0]
        existing = build_candidate_model_report(pd.DataFrame(source_rows),
            prediction_snapshot_ids=first_fold["prediction_snapshot_ids"],
            as_of=datetime.fromisoformat(first_fold["as_of"]), minimum_training_samples=8,
            iterations=30, feature_paths=CORE_MARKET_FEATURE_PATHS)
        augmented = build_candidate_model_report(pd.DataFrame(source_rows),
            prediction_snapshot_ids=first_fold["prediction_snapshot_ids"],
            as_of=datetime.fromisoformat(first_fold["as_of"]), minimum_training_samples=8,
            iterations=30, feature_paths=CORE_MARKET_FEATURE_PATHS + tuple(report["comparisons"]["sec_fundamentals"]["feature_paths"]))
        expected = {row["snapshot_id"]: row["candidate_probability_up"] for row in existing["predictions"]}
        expected_augmented = {row["snapshot_id"]: row["candidate_probability_up"] for row in augmented["predictions"]}
        for sample in report["comparisons"]["sec_fundamentals"]["samples"][:8]:
            self.assertAlmostEqual(expected[sample["snapshot_id"]], sample["momentum_probability_up"], places=12)
            self.assertAlmostEqual(expected_augmented[sample["snapshot_id"]], sample["candidate_probability_up"], places=12)

    def test_per_specialist_counts_never_credit_aggregate_events_to_collectors(self):
        report = _report(pd.DataFrame([_snapshot(0)]))
        by_id = {item["specialist_id"]: item for item in report["specialist_coverage"]}
        for specialist in ("sec", "regulatory", "verification", "prices"):
            self.assertEqual(1, by_id[specialist]["observed_feature_snapshot_count"])
            self.assertEqual(1, by_id[specialist]["matched_market_feature_snapshot_count"])
            self.assertEqual(1, by_id[specialist]["eligible_labeled_snapshot_count"])
            self.assertTrue(by_id[specialist]["archived_feature_paths"])
        for specialist in ("fda", "bank_fdic", "institutions", "contracts", "insider"):
            self.assertEqual(0, by_id[specialist]["observed_feature_snapshot_count"])
            self.assertEqual([], by_id[specialist]["archived_feature_groups"])
            self.assertFalse(by_id[specialist]["source_specific_attribution_proven"])
        self.assertEqual(1, by_id["sec"]["feature_group_coverage"]["sec_fundamentals"]["observed_feature_snapshot_count"])

    def test_future_collector_observation_cannot_hide_behind_an_earlier_availability(self):
        future_sec, future_agent, future_market = [_snapshot(index) for index in range(3)]
        later = (START + timedelta(days=1)).isoformat()
        future_sec["feature_payload"]["sec_fundamentals"]["observed_at"] = later
        future_agent["feature_payload"]["agent_features"]["observed_at"] = later
        future_market["feature_payload"]["market_factors"]["observed_at"] = later
        report = _report(pd.DataFrame([_rehash(row) for row in (future_sec, future_agent, future_market)]))
        self.assertEqual(1, report["feature_group_coverage"]["sec_fundamentals"]["missing_or_rejected_reasons"]["FEATURE_AVAILABLE_AFTER_SNAPSHOT"])
        self.assertEqual(1, report["feature_group_coverage"]["regulatory_events"]["missing_or_rejected_reasons"]["FEATURE_AVAILABLE_AFTER_SNAPSHOT"])
        self.assertEqual(1, report["feature_group_coverage"]["sec_fundamentals"]["matched_market_feature_snapshot_count"])

    def test_late_label_resolution_does_not_extend_economic_overlap_horizon(self):
        rows = _history(4).to_dict("records")
        for row in rows[:8]:
            row["target_observed_at"] = (START + timedelta(days=12)).isoformat()
            _rehash(row)
        report = _report(pd.DataFrame(rows))
        comparison = report["comparisons"]["sec_fundamentals"]
        self.assertEqual(0, comparison["overlap_excluded_count"])
        self.assertEqual((START + timedelta(weeks=2)).isoformat(), comparison["folds"][0]["as_of"])
        self.assertEqual(16, comparison["folds"][0]["training_sample_count"])

    def test_finite_but_unscalable_numeric_range_is_insufficient_not_nan_output(self):
        rows = _history(3).to_dict("records")
        rows[8]["feature_payload"]["market_factors"]["asset_returns"]["5d"] = 1.7e308
        _rehash(rows[8])
        report = _report(pd.DataFrame(rows))
        self.assertEqual("INSUFFICIENT_DATA", report["status"])
        self.assertEqual(0, report["comparisons"]["sec_fundamentals"]["sample_count"])
        json.dumps(report, allow_nan=False)

    def test_created_at_hash_variant_must_match_its_actual_stored_body(self):
        row = _snapshot(0)
        row["created_at"] = START.isoformat()
        _rehash(row)
        self.assertEqual(1, _report(pd.DataFrame([row]))["input"]["integrity_verified_rows"])
        row["created_at"] = (START + timedelta(seconds=1)).isoformat()
        report = _report(pd.DataFrame([row]))
        self.assertEqual({"INVALID_SNAPSHOT_HASH_OR_JSON": 1}, report["input"]["rejected_reasons"])

    def test_label_observed_exactly_at_prediction_time_cannot_train_that_fold(self):
        rows = _history().to_dict("records")
        for row in rows[:8]:
            row["target_observed_at"] = (START + timedelta(weeks=1)).isoformat()
            _rehash(row)
        report = _report(pd.DataFrame(rows))
        folds = report["comparisons"]["sec_fundamentals"]["folds"]
        self.assertEqual((START + timedelta(weeks=2)).isoformat(), folds[0]["as_of"])
        self.assertEqual(16, folds[0]["training_sample_count"])

    def test_future_and_premature_labels_are_excluded_without_becoming_losses(self):
        future = _snapshot(0)
        future["target_observed_at"] = (CUTOFF + timedelta(days=1)).isoformat()
        premature = _snapshot(1)
        premature["target_observed_at"] = (START + timedelta(days=1)).isoformat()
        report = _report(pd.DataFrame([_rehash(future), _rehash(premature)]))
        self.assertEqual(0, report["input"]["resolved_labels_known_at_cutoff"])
        self.assertEqual(1, report["input"]["unusable_resolved_label_reasons"]["LABEL_AFTER_REPORT_CUTOFF"])
        self.assertEqual(1, report["input"]["unusable_resolved_label_reasons"]["INVALID_OR_PREMATURE_RESOLVED_LABEL"])
        self.assertEqual(0, report["comparisons"]["sec_fundamentals"]["sample_count"])

    def test_hash_tamper_and_duplicate_id_are_rejected(self):
        tampered = _snapshot(0)
        tampered["feature_payload"]["sec_fundamentals"]["values"]["revenue_yoy_pct"] = 99999
        duplicate = _snapshot(1)
        report = _report(pd.DataFrame([tampered, duplicate, deepcopy(duplicate)]))
        self.assertEqual(0, report["input"]["integrity_verified_rows"])
        self.assertEqual({"DUPLICATE_SNAPSHOT_ID": 2, "INVALID_SNAPSHOT_HASH_OR_JSON": 1}, report["input"]["rejected_reasons"])

    def test_blank_agent_zero_features_do_not_count_as_observed_evidence(self):
        row = _snapshot(0)
        agent = row["feature_payload"]["agent_features"]
        agent["regulatory"] = {"signed_event_score": 0, "verified_event_count": 0}
        agent["missingness"]["regulatory_events"] = True
        report = _report(pd.DataFrame([_rehash(row)]))
        coverage = report["feature_group_coverage"]["regulatory_events"]
        self.assertEqual(0, coverage["observed_feature_snapshot_count"])
        self.assertEqual({"NO_AGENT_EVIDENCE_OBSERVED": 1}, coverage["missing_or_rejected_reasons"])
        by_id = {item["specialist_id"]: item for item in report["specialist_coverage"]}
        for specialist in ("fda", "bank_fdic", "institutions", "contracts"):
            self.assertEqual("NO_ATTRIBUTABLE_SPECIALIST_FEATURES", by_id[specialist]["status"])
            self.assertFalse(by_id[specialist]["acceptance_proven"])

    def test_future_specialist_and_market_availability_are_not_backfilled(self):
        future_sec = _snapshot(0)
        future_sec["feature_payload"]["sec_fundamentals"]["availability_at"] = (START + timedelta(days=1)).isoformat()
        future_market = _snapshot(1)
        future_market["feature_payload"]["market_factors"]["feature_contract"]["available_at"] = (START + timedelta(days=1)).isoformat()
        report = _report(pd.DataFrame([_rehash(future_sec), _rehash(future_market)]))
        coverage = report["feature_group_coverage"]["sec_fundamentals"]
        self.assertEqual(1, coverage["missing_or_rejected_reasons"]["FEATURE_AVAILABLE_AFTER_SNAPSHOT"])
        self.assertEqual(0, coverage["matched_market_feature_snapshot_count"])
        self.assertEqual(0, report["comparisons"]["sec_fundamentals"]["eligible_labeled_snapshot_count"])

    def test_missing_availability_and_missingness_are_explicit(self):
        row = _snapshot(0)
        del row["feature_payload"]["sec_fundamentals"]["availability_at"]
        del row["feature_payload"]["agent_features"]["missingness"]
        report = _report(pd.DataFrame([_rehash(row)]))
        self.assertEqual({"MISSING_FEATURE_AVAILABILITY": 1}, report["feature_group_coverage"]["sec_fundamentals"]["missing_or_rejected_reasons"])
        self.assertEqual({"UNPROVEN_AGENT_EVIDENCE_PRESENCE": 1}, report["feature_group_coverage"]["claim_verification"]["missing_or_rejected_reasons"])

    def test_overlapping_snapshots_are_counted_and_excluded(self):
        history = _history()
        overlapping = _snapshot(0, week=1, offset=timedelta(days=1))
        report = _report(pd.concat([history, pd.DataFrame([overlapping])], ignore_index=True))
        comparison = report["comparisons"]["sec_fundamentals"]
        self.assertEqual(1, comparison["overlap_excluded_count"])
        sample_ids = [item["snapshot_id"] for item in comparison["samples"]]
        self.assertEqual(32, len(sample_ids))
        self.assertNotIn(overlapping["snapshot_id"], sample_ids)

    def test_market_holiday_extends_label_horizon_and_prevents_double_counting(self):
        with patch(__name__ + ".START", datetime(2026, 1, 5, 22, tzinfo=timezone.utc)):
            history = pd.DataFrame([_snapshot(0, week) for week in range(3)])
        # Jan 19 is MLK Day: the Jan 12 forecast ends Jan 20, overlapping
        # another forecast created Jan 19. Calendar days must not stand in
        # for trading days or permit two independent observations.
        report = _report(history)
        self.assertEqual(1, report["comparisons"]["sec_fundamentals"]["overlap_excluded_count"])

    def test_legacy_baseline_versions_and_market_v2_hashes_remain_unchanged(self):
        rows = []
        for index, version in enumerate(("v2", "v2.1")):
            row = _snapshot(index)
            row["baseline_model_id"] = "legacy_baseline"
            row["baseline_model_version"] = version
            row["baseline_output"]["scoring_version"] = version
            row["feature_payload"]["market_factors"]["version"] = "market_factors_v2"
            del row["feature_payload"]["market_factors"]["feature_contract"]
            rows.append(_rehash(row))
        frame = pd.DataFrame(rows)
        before = frame.copy(deep=True)
        report = _report(frame)
        self.assertEqual(2, report["input"]["integrity_verified_rows"])
        self.assertEqual(2, report["feature_group_coverage"]["sec_fundamentals"]["matched_market_feature_snapshot_count"])
        self.assertEqual({}, report["input"]["rejected_reasons"])
        pd.testing.assert_frame_equal(before, frame)

    def test_non_win_loss_states_are_kept_separate(self):
        rows = []
        for index, status in enumerate(("MARKET_CLOSED", "DATA_GAP", "PENDING", "UNAVAILABLE")):
            row = _snapshot(index)
            row.update(label_status=status, target_value=None, target_observed_at=None)
            rows.append(_rehash(row))
        report = _report(pd.DataFrame(rows))
        self.assertEqual({"MARKET_CLOSED": 1, "DATA_GAP": 1, "PENDING": 1, "UNAVAILABLE": 1}, report["input"]["non_win_loss_status_counts"])
        self.assertEqual(0, report["input"]["resolved_labels_known_at_cutoff"])
        self.assertTrue(all(item["matched_metrics"] is None for item in report["comparisons"].values()))

    def test_small_histories_do_not_show_persuasive_intervals_or_acceptance(self):
        report = _report(_history(2))
        comparison = report["comparisons"]["sec_fundamentals"]
        self.assertEqual("INSUFFICIENT_DATA", comparison["status"])
        self.assertEqual(1, comparison["distinct_weeks"])
        for interval in comparison["matched_metrics"]["paired_candidate_minus_baseline_intervals"].values():
            self.assertIsNone(interval["lower"])
            self.assertIsNone(interval["upper"])
        self.assertFalse(comparison["acceptance_proven"])

    def test_enough_weeks_but_too_few_samples_still_suppresses_intervals(self):
        history = _history()
        report = build_specialist_evaluation_report(history, as_of=CUTOFF,
            minimum_training_samples=8, minimum_evaluation_samples=200,
            minimum_weeks=2, iterations=30, label_observations=_observations(history))
        comparison = report["comparisons"]["sec_fundamentals"]
        self.assertEqual(4, comparison["distinct_weeks"])
        self.assertEqual("INSUFFICIENT_DATA", comparison["status"])
        intervals = list(comparison["matched_metrics"]["paired_candidate_minus_baseline_intervals"].values())
        intervals.extend(comparison["matched_metrics"]["baseline"]["weekly_intervals"].values())
        intervals.extend(comparison["matched_metrics"]["candidate"]["weekly_intervals"].values())
        for interval in intervals:
            self.assertIsNone(interval["lower"])
            self.assertIsNone(interval["upper"])

    def test_snapshot_and_target_contract_changes_fail_closed(self):
        wrong_target = _snapshot(0)
        wrong_target["target_version"] = "previous_target"
        wrong_id = _snapshot(1)
        wrong_id["snapshot_id"] = "forged-identity"
        future = _snapshot(2)
        future.update(as_of=(CUTOFF + timedelta(days=1)).isoformat(), observed_at=(CUTOFF + timedelta(days=1)).isoformat())
        report = _report(pd.DataFrame([_rehash(wrong_target), _rehash(wrong_id), _rehash(future)]))
        self.assertEqual(0, report["input"]["integrity_verified_rows"])
        self.assertEqual(1, report["input"]["rejected_reasons"]["UNSUPPORTED_SNAPSHOT_OR_TARGET_VERSION"])
        self.assertEqual(1, report["input"]["rejected_reasons"]["INCOMPLETE_SNAPSHOT_IDENTITY"])
        self.assertEqual(1, report["input"]["rejected_reasons"]["SNAPSHOT_AFTER_REPORT_CUTOFF"])

    def test_real_sqlite_contract_and_existing_resolver_hash_are_supported_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "history.db"
            store = SQLiteStore(db_path)
            store.ensure_schema()
            run_id = store.insert_run(RunMetadata(started_at=START, finished_at=START,
                watchlist_size=1, processed_symbols=1, warnings_count=0, errors_count=0))
            pending = build_point_in_time_snapshot(run_id=run_id, ticker="TEST0", observed_at=START,
                feature_payload=_payload(-3, START), baseline_output={}, provenance={"test": True})
            store.save_prediction_snapshots([pending])
            pending_report = _report(read_archived_prediction_snapshots(db_path))
            self.assertEqual(1, pending_report["input"]["integrity_verified_rows"])
            raw = store.read_prediction_snapshots().to_dict("records")[0]
            mapping = PredictionLabelService._snapshot_mapping(raw)
            _, horizon = target_us_equity_window(START, 5)
            resolved = resolve_excess_return_label(mapping, [100] * 5 + [103], [100] * 6,
                target_observed_at=horizon[-1].close_at)
            store.update_prediction_snapshot_labels([resolved])
            db_digest = hashlib.sha256(db_path.read_bytes()).hexdigest()
            frame = read_archived_prediction_snapshots(db_path)
            report = _report(frame)
            self.assertEqual(1, report["input"]["resolved_labels_known_at_cutoff"])
            self.assertEqual({}, report["input"]["rejected_reasons"])
            output = Path(tmp) / "report.json"
            with patch("builtins.print"):
                main(["--db-path", str(db_path), "--output-path", str(output), "--as-of", CUTOFF.isoformat(), "--iterations", "20"])
            first = output.read_bytes()
            with patch("builtins.print"):
                main(["--db-path", str(db_path), "--output-path", str(output), "--as-of", CUTOFF.isoformat(), "--iterations", "20"])
            self.assertEqual(first, output.read_bytes())
            self.assertEqual(db_digest, hashlib.sha256(db_path.read_bytes()).hexdigest())
            self.assertFalse(list(Path(tmp).glob("*.tmp")))
            # Reader closes its file handles: relevant Windows cleanup contract.
            db_path.unlink()

    def test_absent_database_is_not_created_and_empty_existing_archive_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.db"
            with self.assertRaises(FileNotFoundError):
                read_archived_prediction_snapshots(path)
            self.assertFalse(path.exists())
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE unrelated (value TEXT)")
            connection.close()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            frame = read_archived_prediction_snapshots(path)
            self.assertTrue(frame.empty)
            self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_cli_cannot_overwrite_its_source_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.db"
            path.write_bytes(b"source-must-remain-unchanged")
            with self.assertRaises(SystemExit):
                main(["--db-path", str(path), "--output-path", str(path)])
            self.assertEqual(b"source-must-remain-unchanged", path.read_bytes())

    def test_invalid_policy_or_naive_replay_time_is_rejected(self):
        for options in ({"as_of": datetime(2026, 1, 1)}, {"minimum_weeks": 0},
                        {"iterations": True}, {"positive_label_hurdle": float("nan")},
                        {"positive_label_hurdle": True}, {"positive_label_hurdle": "bad"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                build_specialist_evaluation_report(pd.DataFrame(), **options)


if __name__ == "__main__":
    unittest.main()
