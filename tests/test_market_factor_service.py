from __future__ import annotations

from datetime import datetime, timezone
import unittest

import pandas as pd

from market_checker_app.services.market_factor_service import (
    MARKET_FACTOR_VERSION,
    build_market_factor_snapshot,
)


def _history(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"Close": values},
        index=pd.date_range("2025-01-01", periods=len(values), freq="B", tz="UTC"),
    )


class MarketFactorServiceTests(unittest.TestCase):
    def test_records_returns_relative_strength_and_explicit_provenance(self) -> None:
        factor = build_market_factor_snapshot(
            asset_history=_history([100.0 + index for index in range(270)]),
            benchmark_history=_history([100.0 + index * 0.5 for index in range(270)]),
            as_of=datetime(2026, 2, 1, tzinfo=timezone.utc),
            asset_source="yahoo_ohlc_close",
            benchmark_source="yahoo_ohlc_cache",
        )

        self.assertEqual(MARKET_FACTOR_VERSION, factor["version"])
        self.assertAlmostEqual(
            factor["asset_returns"]["5d"] - factor["benchmark_returns"]["5d"],
            factor["relative_returns"]["5d"],
        )
        self.assertIsNotNone(factor["realized_volatility"]["20d_annualized"])
        self.assertFalse(factor["missingness"]["relative_returns"])
        self.assertTrue(factor["provenance"]["point_in_time"])

    def test_missing_benchmark_is_not_replaced_by_zero(self) -> None:
        factor = build_market_factor_snapshot(
            asset_history=_history([100.0 + index for index in range(30)]),
            benchmark_history=None,
            as_of=datetime(2026, 2, 1, tzinfo=timezone.utc),
            asset_source="yahoo_ohlc_close",
            benchmark_source=None,
        )

        self.assertIsNone(factor["relative_returns"]["5d"])
        self.assertTrue(factor["missingness"]["benchmark_history"])
        self.assertTrue(factor["missingness"]["relative_returns"])


if __name__ == "__main__":
    unittest.main()
