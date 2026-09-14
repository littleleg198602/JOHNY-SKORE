from __future__ import annotations

from datetime import datetime, timezone
import unittest

from market_checker_app.analysis.news_analysis import analyze_news
from market_checker_app.models import NewsItem


def _article(index: int) -> NewsItem:
    return NewsItem(
        ticker="TEST",
        source=f"https://news.example.com/feed/{index}",
        title=f"TEST publishes routine update {index}",
        summary="No earnings, guidance, upgrade, downgrade, profit or loss signal.",
        published_at=datetime.now(timezone.utc),
        sentiment_weight=0.0,
        url=f"https://news.example.com/article/{index}",
    )


class NeutralNewsDirectionTests(unittest.TestCase):
    def test_volume_of_neutral_headlines_does_not_change_directional_score(self) -> None:
        one = analyze_news("TEST", [_article(1)])
        many = analyze_news("TEST", [_article(index) for index in range(30)])

        self.assertEqual(50.0, one.news_score)
        self.assertEqual(one.news_score, many.news_score)
        self.assertGreater(many.news_confidence, one.news_confidence)


if __name__ == "__main__":
    unittest.main()
