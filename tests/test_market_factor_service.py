from __future__ import annotations

from datetime import datetime, timezone
import unittest

import pandas as pd

from market_checker_app.services.market_factor_service import (
    MARKET_FACTOR_VERSION,
    build_market_factor_snapshot,
)
from market_checker_app.services.us_equity_calendar_service import sessions_between


def _history(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"Close": values},
        index=pd.DatetimeIndex(sessions_between(pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2026-01-30", tz="UTC"))[-len(values):]),
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


    def test_misaligned_asset_and_benchmark_endpoints_do_not_create_relative_return(self) -> None:
        asset = _history([100.0 + index for index in range(40)])
        benchmark = _history([100.0 + index * 0.5 for index in range(40)]).iloc[:-1]
        factor = build_market_factor_snapshot(
            asset_history=asset,
            benchmark_history=benchmark,
            as_of=datetime(2026, 2, 1, tzinfo=timezone.utc),
            asset_source="fixture",
            benchmark_source="fixture",
        )

        self.assertIsNone(factor["relative_returns"]["1d"])
        self.assertTrue(factor["missingness"]["relative_returns"])

    def test_short_history_does_not_claim_full_year_drawdown(self) -> None:
        factor = build_market_factor_snapshot(
            asset_history=_history([100.0 + index for index in range(30)]),
            benchmark_history=_history([100.0 + index for index in range(30)]),
            as_of=datetime(2026, 2, 1, tzinfo=timezone.utc),
            asset_source="fixture",
            benchmark_source="fixture",
        )

        self.assertIsNone(factor["drawdown"]["252d"])
        self.assertTrue(factor["missingness"]["drawdown_252d"])


if __name__ == "__main__":
    unittest.main()
