from __future__ import annotations

from datetime import datetime, timezone
import sys
import unittest
from unittest.mock import patch

import pandas as pd

from market_checker_app.collectors.mt5_client import MT5Client
from market_checker_app.collectors.rss_client import RSSClient


class LargeUniverseCollectorTests(unittest.TestCase):
    def test_rss_yahoo_ticker_hint_assigns_company_name_article(self) -> None:
        payload = b"""<?xml version="1.0"?>
        <rss version="2.0"><channel><title>Yahoo</title><item>
          <title>Apple reports record revenue growth</title>
          <description>Quarterly results beat expectations.</description>
          <pubDate>Wed, 01 Jul 2026 08:00:00 GMT</pubDate>
          <link>https://example.test/apple-results</link>
        </item></channel></rss>"""
        client = RSSClient(max_workers=2)
        client._download = lambda source: payload  # type: ignore[method-assign]

        items, warnings = client.collect(
            ["https://finance.yahoo.com/rss/headline?s=AAPL"],
            ["AAPL"],
        )

        self.assertEqual([], warnings)
        self.assertEqual(1, len(items))
        self.assertEqual("AAPL", items[0].ticker)

    def test_google_news_query_hint_assigns_ticker_without_text_match(self) -> None:
        payload = b"""<?xml version="1.0"?>
        <rss version="2.0"><channel><title>Google News</title><item>
          <title>Apple reports record revenue growth</title>
          <description>Quarterly results beat expectations.</description>
          <pubDate>Wed, 01 Jul 2026 08:00:00 GMT</pubDate>
          <link>https://example.test/apple-results</link>
        </item></channel></rss>"""
        client = RSSClient(max_workers=2)
        client._download = lambda source: payload  # type: ignore[method-assign]

        items, warnings = client.collect(
            ["https://news.google.com/rss/search?q=AAPL%20stock&hl=en-US&gl=US&ceid=US:en"],
            ["AAPL"],
        )

        self.assertEqual([], warnings)
        self.assertEqual(["AAPL"], [item.ticker for item in items])

    def test_undated_news_is_not_misclassified_as_fresh(self) -> None:
        payload = b"""<?xml version="1.0"?>
        <rss version="2.0"><channel><item>
          <title>AAPL reports earnings</title>
          <link>https://example.test/no-date</link>
        </item></channel></rss>"""
        client = RSSClient()
        client._download = lambda source: payload  # type: ignore[method-assign]

        items, warnings = client.collect(["https://example.test/feed"], ["AAPL"])

        self.assertEqual([], items)
        self.assertTrue(any("bez data publikace" in warning for warning in warnings))

    def test_rss_timeout_isolated_and_progress_reported(self) -> None:
        client = RSSClient(max_workers=2)

        def fail_download(source: str) -> bytes:
            raise TimeoutError("test timeout")

        client._download = fail_download  # type: ignore[method-assign]
        progress: list[tuple[int, int, str]] = []
        items, warnings = client.collect(
            ["https://example.test/a", "https://example.test/b"],
            ["AAPL"],
            progress_callback=lambda completed, total, source: progress.append(
                (completed, total, source)
            ),
        )

        self.assertEqual([], items)
        self.assertEqual(2, len(warnings))
        self.assertEqual((2, 2), progress[-1][:2])

    def test_mt5_batch_uses_single_terminal_session(self) -> None:
        calls = {"initialize": 0, "shutdown": 0, "copy": 0}

        class FakeMT5:
            TIMEFRAME_D1 = 1440

            def initialize(self) -> bool:
                calls["initialize"] += 1
                return True

            def shutdown(self) -> None:
                calls["shutdown"] += 1

            def copy_rates_from_pos(self, ticker: str, timeframe: int, start: int, bars: int):
                calls["copy"] += 1
                return [
                    {
                        "time": int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp()),
                        "open": 99.0,
                        "high": 102.0,
                        "low": 98.0,
                        "close": 101.0,
                        "tick_volume": 1000,
                    }
                ]

        fake_mt5 = FakeMT5()
        progress: list[tuple[int, int, str]] = []
        with patch.dict(sys.modules, {"MetaTrader5": fake_mt5}):
            frames, errors = MT5Client().fetch_ohlcv_batch(
                ["AAPL", "MSFT", "NVDA"],
                progress_callback=lambda completed, total, ticker: progress.append(
                    (completed, total, ticker)
                ),
            )

        self.assertEqual({}, errors)
        self.assertEqual({"AAPL", "MSFT", "NVDA"}, set(frames))
        self.assertTrue(all(isinstance(frame, pd.DataFrame) for frame in frames.values()))
        self.assertEqual({"initialize": 1, "shutdown": 1, "copy": 3}, calls)
        self.assertEqual((3, 3, "NVDA"), progress[-1])


if __name__ == "__main__":
    unittest.main()
