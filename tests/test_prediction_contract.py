from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from market_checker_app.prediction_contract import (
    BASELINE_MODEL_ID,
    BASELINE_MODEL_VERSION,
    PRIMARY_PREDICTION_TARGET,
    benchmark_for_sector,
    build_point_in_time_snapshot,
    compute_excess_return_target,
    compute_forward_return,
    make_snapshot_id,
    resolve_excess_return_label,
)


class PredictionContractTests(unittest.TestCase):
    def test_primary_target_is_five_day_excess_return(self) -> None:
        self.assertEqual("5d_excess_return_vs_benchmark", PRIMARY_PREDICTION_TARGET.name)
        self.assertEqual("excess_return_5d_v1", PRIMARY_PREDICTION_TARGET.version)
        self.assertEqual(5, PRIMARY_PREDICTION_TARGET.horizon_trading_days)
        self.assertEqual("decimal", PRIMARY_PREDICTION_TARGET.return_unit)

    def test_forward_return_requires_complete_future_horizon(self) -> None:
        self.assertIsNone(compute_forward_return([100, 101, 102, 103, 104]))
        self.assertAlmostEqual(0.10, compute_forward_return([100, 101, 102, 103, 104, 110]))

    def test_excess_return_is_asset_minus_benchmark(self) -> None:
        value = compute_excess_return_target(
            [100, 100, 100, 100, 100, 110],
            [100, 100, 100, 100, 100, 105],
        )
        self.assertAlmostEqual(0.05, value)

    def test_benchmark_selection_is_explicit_and_falls_back_without_sector(self) -> None:
        self.assertEqual(("XLK", "sector_etf"), benchmark_for_sector("Technology"))
        self.assertEqual(("SPY", "default_fallback"), benchmark_for_sector(None))

    def test_snapshot_is_pending_and_contains_no_future_label(self) -> None:
        observed_at = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        snapshot = build_point_in_time_snapshot(
            run_id=7,
            ticker="aapl",
            observed_at=observed_at,
            feature_payload={"current_price": 100.0},
            baseline_output={"action": "NO_TRADE", "final_total_score": 52.0},
            provenance={"price_source": "test"},
            benchmark_ticker="SPY",
        )
        self.assertEqual(make_snapshot_id(7, "AAPL", "excess_return_5d_v1"), snapshot["snapshot_id"])
        self.assertEqual("AAPL", snapshot["ticker"])
        self.assertEqual("PENDING", snapshot["label_status"])
        self.assertIsNone(snapshot["target_value"])
        self.assertEqual(BASELINE_MODEL_ID, snapshot["baseline_model_id"])
        self.assertEqual(BASELINE_MODEL_VERSION, snapshot["baseline_model_version"])
        self.assertNotIn("future_prices", snapshot["feature_payload"])
        self.assertTrue(snapshot["snapshot_hash"])

        labeled = resolve_excess_return_label(
            snapshot,
            [100, 100, 100, 100, 100, 110],
            [100, 100, 100, 100, 100, 105],
            target_observed_at=observed_at,
        )
        self.assertEqual("RESOLVED", labeled["label_status"])
        self.assertAlmostEqual(0.05, labeled["target_value"])
        self.assertNotEqual(snapshot["snapshot_hash"], labeled["snapshot_hash"])


if __name__ == "__main__":
    unittest.main()
