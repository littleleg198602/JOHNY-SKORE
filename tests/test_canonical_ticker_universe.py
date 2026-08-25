from __future__ import annotations

import unittest

from market_checker_app.utils.ticker_universe import (
    CANONICAL_SOURCE_FILE,
    CANONICAL_TICKER_COUNT,
    DEFAULT_TICKER_UNIVERSE_PATH,
    load_canonical_ticker_records,
    load_canonical_tickers,
)


class CanonicalTickerUniverseTests(unittest.TestCase):
    def test_market_checker_export_is_the_687_ticker_source(self) -> None:
        records = load_canonical_ticker_records()

        self.assertTrue(DEFAULT_TICKER_UNIVERSE_PATH.exists())
        self.assertEqual(CANONICAL_TICKER_COUNT, len(records))
        self.assertEqual(
            CANONICAL_TICKER_COUNT,
            len({record["ticker"] for record in records}),
        )
        self.assertTrue(all(record["yahoo_ticker"] for record in records))
        self.assertEqual("NVDA", records[0]["ticker"])
        self.assertEqual("OLN", records[-1]["ticker"])
        self.assertEqual(
            "market_checker_20260818_213623.xlsx",
            CANONICAL_SOURCE_FILE,
        )

    def test_ticker_loader_preserves_excel_order(self) -> None:
        tickers = load_canonical_tickers()
        self.assertEqual(CANONICAL_TICKER_COUNT, len(tickers))
        self.assertEqual("NVDA", tickers[0])
        self.assertEqual("AAPL", tickers[1])
        self.assertEqual("OLN", tickers[-1])


if __name__ == "__main__":
    unittest.main()
