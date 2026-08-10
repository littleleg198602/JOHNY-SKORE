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

    def test_refresh_all_automatically_continues_across_batches(self) -> None:
        tickers = ["A", "B", "C", "D", "E"]
        responses = {
            ticker: (
                YahooSnapshot(
                    ticker,
                    {"marketCap": index, "forwardPE": 20},
                    "ok",
                ),
                None,
            )
            for index, ticker in enumerate(tickers, start=1)
        }
        client = _FakeYahooClient(responses)
        sleeps: list[float] = []
        progress: list[tuple] = []
        service = YahooEnrichmentService(self.cache, client, sleep_fn=sleeps.append)

        result = service.refresh_all(
            tickers,
            batch_size=2,
            delay_seconds=0.25,
            progress_callback=lambda *args: progress.append(args),
        )

        self.assertEqual(tickers, client.calls)
        self.assertEqual(3, result.batches)
        self.assertEqual(5, result.candidates)
        self.assertEqual(5, result.attempted)
        self.assertEqual(5, result.succeeded)
        self.assertEqual(0, result.remaining)
        self.assertEqual([1, 2, 3, 4, 5], [int(row[0]) for row in progress])
        self.assertEqual({5}, {int(row[1]) for row in progress})
        # Four request boundaries: two inside batches and two between batches.
        self.assertEqual([0.25, 0.25, 0.25, 0.25], sleeps)

    def test_refresh_all_rejects_invalid_batch_size(self) -> None:
        with self.assertRaisesRegex(ValueError, "batch_size"):
            YahooEnrichmentService(self.cache).refresh_all([], batch_size=0)

    def test_refresh_all_stops_immediately_after_rate_limit(self) -> None:
        client = _FakeYahooClient(
            {
                "A": (
                    YahooSnapshot("A", {}, "fallback"),
                    "Yahoo metadata [rate_limit] pro A",
                ),
                "B": (YahooSnapshot("B", {"marketCap": 2}, "ok"), None),
            }
        )

        result = YahooEnrichmentService(
            self.cache,
            client,
            sleep_fn=lambda _: None,
        ).refresh_all(["A", "B"], batch_size=1, delay_seconds=0)

        self.assertEqual(["A"], client.calls)
        self.assertTrue(result.rate_limited)
        self.assertEqual(1, result.batches)
        self.assertEqual(1, result.attempted)
        self.assertEqual(2, result.remaining)


if __name__ == "__main__":
    unittest.main()
