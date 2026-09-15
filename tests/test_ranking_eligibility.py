from __future__ import annotations

import unittest

import pandas as pd

from market_checker_app.services.ranking_service import RankingService


class RankingEligibilityTests(unittest.TestCase):
    def test_invalid_price_cannot_rank_ahead_of_usable_signal(self) -> None:
        signals = pd.DataFrame(
            [
                {
                    "ticker": "BAD",
                    "final_total_score": 99.0,
                    "current_price": None,
                    "current_price_source": "bulk_price_source_unavailable",
                    "ohlc_history_usable": False,
                },
                {
                    "ticker": "GOOD",
                    "final_total_score": 60.0,
                    "current_price": 100.0,
                    "current_price_source": "yahoo_ohlc_close",
                    "ohlc_history_usable": True,
                },
            ]
        )

        ranked = RankingService.apply_ranking(signals).set_index("ticker")

        self.assertEqual(1, ranked.loc["GOOD", "rank_in_watchlist"])
        self.assertTrue(bool(ranked.loc["GOOD", "ranking_eligible"]))
        self.assertFalse(bool(ranked.loc["BAD", "ranking_eligible"]))
        self.assertTrue(pd.isna(ranked.loc["BAD", "rank_in_watchlist"]))
        self.assertIn("NO_DATED_PRICE", ranked.loc["BAD", "ranking_reason"])

    def test_undated_metadata_quote_is_not_ranking_eligible(self) -> None:
        signals = pd.DataFrame(
            [
                {
                    "ticker": "UNDATED",
                    "final_total_score": 70.0,
                    "current_price": 100.0,
                    "current_price_source": "yahoo_metadata_quote_undated",
                    "ohlc_history_usable": True,
                }
            ]
        )

        ranked = RankingService.apply_ranking(signals)

        self.assertFalse(bool(ranked.iloc[0]["ranking_eligible"]))
        self.assertIn("UNDATED_PRICE", ranked.iloc[0]["ranking_reason"])
        self.assertTrue(RankingService.top_bottom_tables(ranked)["top"].empty)


if __name__ == "__main__":
    unittest.main()
