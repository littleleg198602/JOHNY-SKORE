from __future__ import annotations

import unittest

from market_checker_app.services.watchlist_service import (
    WatchlistError,
    normalize_watchlist,
    select_watchlist_pilot,
)


class WatchlistServiceTests(unittest.TestCase):
    def test_comments_are_ignored_and_declared_order_is_preserved(self) -> None:
        self.assertEqual(
            ["NVDA", "AAPL", "BRKB"],
            normalize_watchlist(
                ["# exported universe", "nvda", " AAPL ", "BRKB"]
            ),
        )

    def test_duplicates_and_invalid_symbols_fail_closed(self) -> None:
        with self.assertRaisesRegex(WatchlistError, "duplicitní"):
            normalize_watchlist(["AAPL", "aapl"])
        with self.assertRaisesRegex(WatchlistError, "neplatné"):
            normalize_watchlist(["AAPL", "BAD TICKER"])

    def test_required_source_ticker_replaces_tail_of_limited_pilot(self) -> None:
        pilot = select_watchlist_pilot(
            ["A", "B", "C", "D", "MSTR"],
            3,
            required_tickers=["MSTR"],
        )

        self.assertEqual(["A", "B", "MSTR"], pilot)

    def test_required_ticker_outside_universe_fails_closed(self) -> None:
        with self.assertRaisesRegex(WatchlistError, "nejsou v produkčním universe"):
            select_watchlist_pilot(
                ["AAPL", "MSFT"],
                2,
                required_tickers=["MSTR"],
            )


if __name__ == "__main__":
    unittest.main()
