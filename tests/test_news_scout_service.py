from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from market_checker_app.models import NewsItem
from market_checker_app.services.news_scout_service import NewsScoutService
from market_checker_app.storage.scout_store import ScoutStore


class NewsScoutTests(unittest.TestCase):
    def test_rss_title_is_only_a_dated_unverified_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            now = datetime.now(timezone.utc)
            article = NewsItem(
                ticker="AAPL", source="RSS", title="Apple supplier announcement",
                summary="Unconfirmed search match", published_at=now - timedelta(days=1),
                sentiment_weight=0.0, url="https://example.org/story",
                original_url="https://example.org/story", publisher="Example",
                observed_at=now,
            )
            service = NewsScoutService(store)
            self.assertEqual(1, service.ingest([article], allowed_tickers=["AAPL"]))
            self.assertEqual(0, service.ingest([article], allowed_tickers=["AAPL"]))
            self.assertFalse(store.has_findings(["AAPL"], as_of=now))
            self.assertEqual([], store.latest_findings(
                ["AAPL"], source="rss", as_of=now - timedelta(minutes=1),
            ))
            rows = store.latest_findings(["AAPL"], source="rss", as_of=now)
            self.assertEqual("UNVERIFIED", rows[0]["verification_status"])
            self.assertEqual("search_candidate", rows[0]["stage"])
            self.assertEqual(1, len(store.open_leads(["AAPL"], as_of=now)))

    def test_rejects_foreign_future_and_unsafe_hits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            now = datetime.now(timezone.utc)

            def item(ticker: str, url: str, published: datetime) -> NewsItem:
                return NewsItem(ticker=ticker, source="RSS", title="Result",
                                summary="Search match", published_at=published,
                                sentiment_weight=0, url=url, observed_at=now)

            service = NewsScoutService(store)
            self.assertEqual(0, service.ingest([
                item("MSFT", "https://example.org/x", now - timedelta(days=1)),
                item("AAPL", "http://localhost/x", now - timedelta(days=1)),
                item("AAPL", "https://example.org/future", now + timedelta(days=1)),
            ], allowed_tickers=["AAPL"], max_per_ticker=3))
            self.assertEqual([], store.latest_findings(["AAPL"], as_of=now))


if __name__ == "__main__":
    unittest.main()
