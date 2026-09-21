from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from market_checker_app.models import NewsItem
from market_checker_app.services.source_discovery_service import (
    SourceDiscoveryService,
)


def _item(
    *,
    title: str,
    url: str,
    published_at: datetime,
    ticker: str = "AAPL",
) -> NewsItem:
    return NewsItem(
        ticker=ticker,
        source="fixture",
        title=title,
        summary="",
        published_at=published_at,
        sentiment_weight=0.0,
        url=url,
    )


class SourceDiscoveryServiceTests(unittest.TestCase):
    def test_only_direct_known_publisher_report_is_auto_ingested(self) -> None:
        now = datetime.now(timezone.utc)
        discovered = SourceDiscoveryService().discover(
            [
                _item(
                    title="Hindenburg Research publishes short report",
                    url="https://hindenburgresearch.com/example-report/",
                    published_at=now - timedelta(hours=1),
                ),
                _item(
                    title="News outlet discusses Hindenburg short report",
                    url="https://news.example.com/story",
                    published_at=now - timedelta(hours=1),
                ),
                _item(
                    title="Unknown Research publishes short report",
                    url="https://unknown.example/report",
                    published_at=now - timedelta(hours=1),
                ),
            ],
            as_of=now,
            discover_short_reports=True,
            discover_regulatory_events=False,
        )

        self.assertEqual(1, len(discovered.short_reports))
        self.assertEqual(
            "Hindenburg Research",
            discovered.short_reports[0].publisher,
        )
        self.assertEqual("rss", discovered.short_reports[0].discovery_method)

    def test_regulatory_discovery_is_low_confidence_and_non_authoritative(self) -> None:
        now = datetime.now(timezone.utc)
        discovered = SourceDiscoveryService().discover(
            [
                _item(
                    title="FDA approves new therapy",
                    url="https://agency.example/announcement",
                    published_at=now - timedelta(hours=1),
                )
            ],
            as_of=now,
            discover_short_reports=False,
            discover_regulatory_events=True,
        )

        self.assertEqual(1, len(discovered.regulatory_events))
        event = discovered.regulatory_events[0]
        self.assertEqual("REGULATORY_APPROVAL", event.event_type)
        self.assertEqual(0.45, event.confidence)
        self.assertIn("Neověřeno", event.authority_or_counterparty)
        self.assertEqual("rss", event.discovery_method)

    def test_direct_official_domain_canary_is_detected_without_publisher_in_title(self) -> None:
        now = datetime.now(timezone.utc)
        discovered = SourceDiscoveryService().discover(
            [
                _item(
                    ticker="TAL",
                    title="MW is Short TAL",
                    url="https://muddywatersresearch.com/research/tal/mw-is-short-tal/",
                    published_at=now - timedelta(hours=1),
                ),
                _item(
                    ticker="TAL",
                    title="MW is Short TAL",
                    url="https://news.example.com/mw-is-short-tal",
                    published_at=now - timedelta(hours=1),
                ),
            ],
            as_of=now,
            discover_short_reports=True,
            discover_regulatory_events=False,
        )

        self.assertEqual(1, len(discovered.short_reports))
        self.assertEqual("Muddy Waters Research", discovered.short_reports[0].publisher)

    def test_future_and_private_sources_are_ignored(self) -> None:
        now = datetime.now(timezone.utc)
        discovered = SourceDiscoveryService().discover(
            [
                _item(
                    title="Hindenburg Research publishes short report",
                    url="https://127.0.0.1/report",
                    published_at=now - timedelta(hours=1),
                ),
                _item(
                    title="FDA approves new therapy",
                    url="https://agency.example/future",
                    published_at=now + timedelta(hours=1),
                ),
            ],
            as_of=now,
            discover_short_reports=True,
            discover_regulatory_events=True,
        )

        self.assertEqual((), discovered.short_reports)
        self.assertEqual((), discovered.regulatory_events)

    def test_prefers_original_article_url_and_extracts_pdf_corporate_event(self) -> None:
        now = datetime.now(timezone.utc)
        item = _item(
            title="AAPL raises guidance and announces share buyback",
            url="https://news.google.com/rss/articles/wrapper",
            published_at=now - timedelta(hours=1),
        )
        item.original_url = "https://investor.example.com/aapl-guidance"
        item.publisher = "Example Investor Relations"

        discovered = SourceDiscoveryService().discover(
            [item],
            as_of=now,
            discover_short_reports=False,
            discover_regulatory_events=True,
        )

        self.assertEqual(1, len(discovered.regulatory_events))
        event = discovered.regulatory_events[0]
        self.assertEqual("GUIDANCE_RAISE", event.event_type)
        self.assertEqual(item.original_url, event.url)
        self.assertEqual("Example Investor Relations", event.publisher)

    def test_universe_limit_is_allocated_round_robin_across_tickers(self) -> None:
        now = datetime.now(timezone.utc)
        items = [
            _item(
                ticker=ticker,
                title=f"{ticker} wins contract",
                url=f"https://example.com/{ticker}/{index}",
                published_at=now - timedelta(hours=index + 1),
            )
            for ticker in ("AAA", "BBB", "CCC")
            for index in range(3)
        ]
        discovered = SourceDiscoveryService().discover(
            items,
            as_of=now,
            discover_short_reports=False,
            discover_regulatory_events=True,
            max_regulatory_events=3,
        )

        self.assertEqual(
            {"AAA", "BBB", "CCC"},
            {event.ticker for event in discovered.regulatory_events},
        )


if __name__ == "__main__":
    unittest.main()
