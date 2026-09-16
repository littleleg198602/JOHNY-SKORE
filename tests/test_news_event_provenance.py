from __future__ import annotations

from datetime import datetime, timezone
import unittest

from market_checker_app.analysis.news_analysis import analyze_news
from market_checker_app.models import NewsItem
from market_checker_app.utils.news_events import canonical_news_event_id


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

    def test_rewritten_cross_publisher_copies_are_not_independent_confirmation(self) -> None:
        now = datetime.now(timezone.utc)
        original = NewsItem(
            ticker="ACME",
            source="https://transport.example/rss",
            title="ACME beats earnings estimates and raises outlook",
            summary="ACME earnings estimates outlook.",
            published_at=now,
            sentiment_weight=0.5,
            url="https://transport.example/one",
            publisher="Reuters",
            publisher_domain="reuters.com",
            event_id=canonical_news_event_id(
                "ACME beats earnings estimates and raises outlook"
            ),
        )
        copies = [original]
        for index in range(9):
            copies.append(
                NewsItem(
                    ticker="ACME",
                    source=f"https://feed-{index}.example/rss",
                    title="ACME raises outlook after earnings beat",
                    summary="ACME earnings estimates outlook.",
                    published_at=now,
                    sentiment_weight=0.5,
                    url=f"https://publisher-{index}.example/article",
                    publisher=f"Republisher {index}",
                    publisher_domain=f"publisher-{index}.example",
                    event_id=canonical_news_event_id(
                        "ACME raises outlook after earnings beat"
                    ),
                )
            )

        one = analyze_news("ACME", [original])
        result = analyze_news("ACME", copies)

        self.assertEqual(10, result.unique_sources_count)
        self.assertEqual(0.9, result.duplicate_ratio)
        self.assertLessEqual(result.news_confidence, one.news_confidence)
        self.assertIn("syndicated or rewritten copies collapsed", result.warnings)
        self.assertEqual(1, len({item.canonical_event_id for item in result.article_features}))

    def test_trust_uses_original_publisher_not_feed_transport(self) -> None:
        result = analyze_news(
            "TEST",
            [
                NewsItem(
                    ticker="TEST",
                    source="https://news.google.com/rss/search?q=TEST",
                    title="TEST earnings beat",
                    summary="TEST earnings beat expectations.",
                    published_at=datetime.now(timezone.utc),
                    sentiment_weight=0.5,
                    url="https://news.google.com/link",
                    publisher="Reuters",
                    publisher_domain="reuters.com",
                )
            ],
        )
        self.assertEqual(1.0, result.avg_source_trust)

    def test_short_ticker_must_match_a_token_not_a_substring(self) -> None:
        result = analyze_news(
            "A",
            [
                NewsItem(
                    ticker="A",
                    source="https://feed.example/rss",
                    title="Apple posts routine update",
                    summary="No ticker symbol is stated.",
                    published_at=datetime.now(timezone.utc),
                    sentiment_weight=0.0,
                    url="https://feed.example/apple",
                )
            ],
        )
        self.assertEqual(0.3, result.article_features[0].ticker_relevance)

    def test_no_news_is_neutral_missingness(self) -> None:
        result = analyze_news("TEST", [])
        self.assertEqual(50.0, result.news_score)
        self.assertEqual(0.0, result.news_confidence)
        self.assertIn("missingness is not directional evidence", result.reasons[0])


if __name__ == "__main__":
    unittest.main()
