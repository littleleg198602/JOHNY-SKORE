from __future__ import annotations

import unittest
from unittest.mock import patch

from market_checker_app.collectors.yahoo_client import YahooClient


class _FakeTicker:
    def __init__(self, info=None, error: Exception | None = None) -> None:
        self._info = info
        self._error = error
        self.history_calls = 0

    @property
    def info(self):
        if self._error is not None:
            raise self._error
        return self._info

    def history(self, **kwargs):
        self.history_calls += 1
        raise AssertionError("fetch_metadata nesmí volat cenovou historii")


class YahooMetadataClientTests(unittest.TestCase):
    def setUp(self) -> None:
        YahooClient._cache.clear()
        YahooClient._rate_limited_until = 0.0

    def test_complete_metadata_is_ok_and_history_is_never_called(self):
        fake = _FakeTicker(
            {
                "currentPrice": 100.0,
                "targetMeanPrice": 120.0,
                "targetMedianPrice": 118.0,
                "recommendationMean": 2.0,
                "numberOfAnalystOpinions": 20,
                "forwardPE": 22.0,
            }
        )

        with patch("market_checker_app.collectors.yahoo_client.yf.Ticker", return_value=fake):
            snapshot, warning = YahooClient(retry_attempts=1).fetch_metadata("aapl")

        self.assertEqual("AAPL", snapshot.ticker)
        self.assertEqual("ok", snapshot.status)
        self.assertIsNone(warning)
        self.assertEqual(0, fake.history_calls)

    def test_usable_but_incomplete_metadata_is_partial(self):
        fake = _FakeTicker({"currentPrice": 100.0, "forwardPE": 22.0, "symbol": "AAPL"})

        with patch("market_checker_app.collectors.yahoo_client.yf.Ticker", return_value=fake):
            snapshot, warning = YahooClient(retry_attempts=1).fetch_metadata("AAPL")

        self.assertEqual("partial", snapshot.status)
        self.assertEqual(100.0, snapshot.data["currentPrice"])
        self.assertIn("[partial]", warning or "")

    def test_empty_or_irrelevant_metadata_falls_back(self):
        for payload in ({}, {"symbol": "AAPL"}, {"currentPrice": 100.0}):
            with self.subTest(payload=payload):
                fake = _FakeTicker(payload)
                with patch(
                    "market_checker_app.collectors.yahoo_client.yf.Ticker", return_value=fake
                ):
                    snapshot, warning = YahooClient(retry_attempts=1).fetch_metadata("AAPL")

                self.assertEqual("fallback", snapshot.status)
                self.assertEqual({}, snapshot.data)
                self.assertIn("[unusable]", warning or "")

    def test_rate_limit_is_machine_detectable(self):
        fake = _FakeTicker(error=RuntimeError("HTTP Error 429: Too Many Requests"))

        with patch("market_checker_app.collectors.yahoo_client.yf.Ticker", return_value=fake):
            snapshot, warning = YahooClient(retry_attempts=1).fetch_metadata("AAPL")

        self.assertEqual("fallback", snapshot.status)
        self.assertIn("[rate_limit]", warning or "")
        self.assertTrue(YahooClient.is_rate_limited())
        self.assertGreater(YahooClient.rate_limit_remaining_seconds(), 0)

    def test_maps_only_known_class_share_symbols(self):
        self.assertEqual("BRK-B", YahooClient.normalize_yahoo_symbol("brk.b"))
        self.assertEqual("BF-B", YahooClient.normalize_yahoo_symbol("BF.B"))
        self.assertEqual("VOD.L", YahooClient.normalize_yahoo_symbol("VOD.L"))
        self.assertEqual("AAPL.US", YahooClient.normalize_yahoo_symbol("AAPL.US"))


if __name__ == "__main__":
    unittest.main()
