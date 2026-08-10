from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from market_checker_app.models import YahooSnapshot
from market_checker_app.services.yahoo_enrichment_service import YahooEnrichmentService
from market_checker_app.storage.yahoo_cache_store import YahooCacheStore


class _FakeYahooClient:
    def __init__(self, responses: dict[str, tuple[YahooSnapshot, str | None]]) -> None:
        self.responses = responses
        self.calls: list[str] = []
        self.rate_limited = False

    def fetch_metadata(self, ticker: str):
        self.calls.append(ticker)
        response = self.responses[ticker]
        if response[1] and "[rate_limit]" in response[1]:
            self.rate_limited = True
        return response

    def is_rate_limited(self) -> bool:
        return self.rate_limited

    @staticmethod
    def normalize_yahoo_symbol(ticker: str) -> str:
        return ticker


class YahooEnrichmentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.cache = YahooCacheStore(Path(self.temp_dir.name) / "cache.db")

    def test_refresh_persists_complete_and_partial_metadata(self) -> None:
        client = _FakeYahooClient(
            {
                "AAPL": (YahooSnapshot("AAPL", {"marketCap": 1, "forwardPE": 2}, "ok"), None),
                "MSFT": (
                    YahooSnapshot("MSFT", {"marketCap": 2, "forwardPE": 3}, "partial"),
                    "Yahoo metadata [partial] pro MSFT",
                ),
            }
        )
        updates = []
        result = YahooEnrichmentService(self.cache, client, sleep_fn=lambda _: None).refresh(
            ["AAPL", "MSFT"],
            delay_seconds=0,
            progress_callback=lambda *args: updates.append(args),
        )

        self.assertEqual(2, result.succeeded)
        self.assertEqual(1, result.partial)
        self.assertEqual(2, result.coverage.fresh)
        self.assertEqual("partial", self.cache.get("MSFT").record.data["_market_checker_yahoo_quality"])
        self.assertEqual(2, len(updates))

    def test_rate_limit_stops_batch_and_leaves_remaining_for_resume(self) -> None:
        client = _FakeYahooClient(
            {
                "AAPL": (
                    YahooSnapshot("AAPL", {}, "fallback"),
                    "Yahoo metadata [rate_limit] pro AAPL",
                ),
                "MSFT": (YahooSnapshot("MSFT", {"marketCap": 2}, "ok"), None),
            }
        )
        result = YahooEnrichmentService(self.cache, client, sleep_fn=lambda _: None).refresh(
            ["AAPL", "MSFT"],
            delay_seconds=0,
        )

        self.assertTrue(result.rate_limited)
        self.assertEqual(["AAPL"], client.calls)
        self.assertEqual(1, result.failed)
        self.assertEqual("missing", self.cache.get("MSFT").state)
        self.assertEqual(2, result.remaining)

    def test_max_items_makes_refresh_resumable(self) -> None:
        responses = {
            ticker: (YahooSnapshot(ticker, {"marketCap": index, "forwardPE": 20}, "ok"), None)
            for index, ticker in enumerate(["A", "B", "C"], start=1)
        }
        client = _FakeYahooClient(responses)
        service = YahooEnrichmentService(self.cache, client, sleep_fn=lambda _: None)

        first = service.refresh(["A", "B", "C"], max_items=2, delay_seconds=0)
        second = service.refresh(["A", "B", "C"], max_items=2, delay_seconds=0)

        self.assertEqual(["A", "B", "C"], client.calls)
        self.assertEqual(2, first.coverage.fresh)
        self.assertEqual(3, second.coverage.fresh)
        self.assertEqual(0, second.remaining)


if __name__ == "__main__":
    unittest.main()
