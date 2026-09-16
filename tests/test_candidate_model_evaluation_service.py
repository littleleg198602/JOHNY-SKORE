from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from market_checker_app.services.candidate_model_evaluation_service import (
    evaluate_candidate_walk_forward,
    summarize_candidate_samples,
)
from market_checker_app.storage.sqlite_store import SQLiteStore


def _at(day: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)


def _payload(seed: float) -> dict[str, object]:
    return {
        "market_factors": {
            "asset_returns": {"5d": seed / 100, "20d": seed / 80, "60d": seed / 60},
            "relative_returns": {"5d": seed / 120, "20d": seed / 100, "60d": seed / 90},
            "realized_volatility": {"20d_annualized": 0.2 + abs(seed) / 1000},
            "drawdown": {"252d": -0.15 + seed / 1000},
        }
    }


def _snapshots(*, delayed_first_week: bool = False) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for week in range(5):
        for ticker_index in range(8):
            seed = float(ticker_index - 4)
            as_of_day = week * 7
            observed_day = as_of_day + 5
            if delayed_first_week and week == 0:
                observed_day = 40
            rows.append(
                {
                    "snapshot_id": f"w{week}-t{ticker_index}",
                    "ticker": f"T{ticker_index:03d}",
                    "sector": "Technology" if ticker_index < 4 else "Energy",
                    "as_of": _at(as_of_day).isoformat(),
                    "target_version": "excess_return_5d_nyse_split_price_v3",
                    "label_status": "RESOLVED",
                    "target_value": 0.02 if seed > 0 else -0.02,
                    "target_observed_at": _at(observed_day).isoformat(),
                    "feature_payload_json": json.dumps(_payload(seed)),
                }
            )
    return pd.DataFrame(rows)


class CandidateModelEvaluationServiceTests(unittest.TestCase):
    def test_known_samples_report_baseline_candidate_and_week_intervals(self) -> None:
        samples = []
        for week in ("2026-01-05", "2026-01-12"):
            samples.extend(
                [
                    {
                        "week": week,
                        "ticker": "GOOD",
                        "target_value": 0.10,
                        "outcome_up": 1.0,
                        "momentum_probability_up": 0.10,
                        "candidate_probability_up": 0.90,
                    },
                    {
                        "week": week,
                        "ticker": "BAD",
                        "target_value": -0.10,
                        "outcome_up": 0.0,
                        "momentum_probability_up": 0.90,
                        "candidate_probability_up": 0.10,
                    },
                ]
            )
        report = summarize_candidate_samples(samples, top_fraction=0.5)
        self.assertEqual(4, report["sample_count"])
        self.assertAlmostEqual(0.81, report["baseline"]["brier_score"])
        self.assertAlmostEqual(0.01, report["candidate"]["brier_score"])
        self.assertAlmostEqual(-1.0, report["baseline"]["ranking_ic"])
        self.assertAlmostEqual(1.0, report["candidate"]["ranking_ic"])
        self.assertAlmostEqual(-0.10, report["baseline"]["top_decile_excess_return"])
        self.assertAlmostEqual(0.10, report["candidate"]["top_decile_excess_return"])
        self.assertEqual("weekly_bootstrap", report["candidate"]["weekly_intervals"]["ranking_ic"]["method"])

    def test_walk_forward_uses_only_labels_known_at_each_week(self) -> None:
        report = evaluate_candidate_walk_forward(
            _snapshots(delayed_first_week=True),
            minimum_training_samples=8,
            minimum_evaluation_samples=1,
            minimum_weeks=1,
            iterations=100,
        )
        self.assertEqual("EVALUATED", report["status"])
        samples = report["samples"]
        self.assertTrue(samples)
        # Week 0 labels are withheld until day 40, so the first trainable
        # period can only use the eight week-1 labels known by week 2.
        self.assertEqual(8, samples[0]["training_sample_count"])
        self.assertTrue(report["analysis_only"])
        self.assertFalse(report["activation_allowed"])

    def test_overlapping_label_horizon_is_excluded_and_insufficiency_is_explicit(self) -> None:
        snapshots = _snapshots().iloc[:16].copy()
        duplicate = dict(snapshots.iloc[0])
        duplicate["snapshot_id"] = "overlap"
        duplicate["as_of"] = _at(0).isoformat()
        duplicate["target_observed_at"] = _at(5).isoformat()
        snapshots = pd.concat([snapshots, pd.DataFrame([duplicate])], ignore_index=True)
        report = evaluate_candidate_walk_forward(
            snapshots,
            minimum_training_samples=100,
            minimum_evaluation_samples=200,
            minimum_weeks=12,
        )
        self.assertEqual("INSUFFICIENT_DATA", report["status"])
        self.assertEqual("NO_WALK_FORWARD_PERIOD_WITH_TRAINED_CANDIDATE", report["reason"])
        self.assertGreaterEqual(report["overlap_excluded_count"], 1)

    def test_persists_evaluation_even_when_history_is_insufficient(self) -> None:
        report = evaluate_candidate_walk_forward(
            _snapshots().iloc[:8],
            minimum_training_samples=100,
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "evaluation.db")
            self.assertTrue(store.save_candidate_model_evaluation(report))
            self.assertEqual(1, len(store.read_candidate_model_evaluations()))


if __name__ == "__main__":
    unittest.main()
