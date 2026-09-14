from __future__ import annotations

from datetime import datetime, timezone
import unittest

from market_checker_app.analysis.news_analysis import analyze_news
from market_checker_app.models import NewsItem


class NewsEventProvenanceTests(unittest.TestCase):
    def test_syndicated_copies_are_one_event_and_one_publisher(self) -> None:
        items = [
            NewsItem(
                ticker="TEST",
                source="https://feed-one.example/rss",
                title="TEST reports earnings beat",
                summary="Earnings beat expectations.",
                published_at=datetime.now(timezone.utc),
                sentiment_weight=0.5,
                url="https://feed-one.example/link",
                feed_url="https://feed-one.example/rss",
                publisher="Reuters",
                publisher_domain="reuters.com",
                original_url="https://www.reuters.com/example",
                event_id="reuters.com|test-reports-earnings-beat",
            ),
            NewsItem(
                ticker="TEST",
                source="https://feed-two.example/rss",
                title="TEST reports earnings beat",
                summary="Syndicated copy of the same event.",
                published_at=datetime.now(timezone.utc),
                sentiment_weight=0.5,
                url="https://feed-two.example/link",
                feed_url="https://feed-two.example/rss",
                publisher="Reuters",
                publisher_domain="reuters.com",
                original_url="https://www.reuters.com/example",
                event_id="reuters.com|test-reports-earnings-beat",
            ),
        ]

        result = analyze_news("TEST", items)

        self.assertEqual(1, result.unique_sources_count)
        self.assertEqual(0.5, result.duplicate_ratio)
        self.assertTrue(all(feature.is_duplicate for feature in result.article_features))


if __name__ == "__main__":
    unittest.main()
