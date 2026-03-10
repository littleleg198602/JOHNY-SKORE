from __future__ import annotations

from datetime import datetime, timedelta, timezone

import feedparser

from market_checker_app.models import NewsItem


class RSSClient:
    def __init__(self, max_items_per_source: int = 30) -> None:
        self.max_items_per_source = max_items_per_source

    def collect(self, rss_sources: list[str], tickers: list[str]) -> tuple[list[NewsItem], list[str]]:
        warnings: list[str] = []
        ticker_set = set(tickers)
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=48)
        items: list[NewsItem] = []

        for source in rss_sources:
            try:
                parsed = feedparser.parse(source)
            except Exception as exc:
                warnings.append(f"RSS source timeout/chyba ({source}): {exc}")
                continue

            for entry in parsed.entries[: self.max_items_per_source]:
                title = str(getattr(entry, "title", ""))
                summary = str(getattr(entry, "summary", ""))
                published_parsed = getattr(entry, "published_parsed", None)
                if published_parsed is None:
                    published_at = now
                else:
                    published_at = datetime(*published_parsed[:6], tzinfo=timezone.utc)
                if published_at < cutoff:
                    continue

                text = f"{title} {summary}".upper()
                for ticker in ticker_set:
                    if ticker in text:
                        items.append(
                            NewsItem(
                                ticker=ticker,
                                source=source,
                                title=title,
                                published_at=published_at,
                                sentiment_weight=1.0,
                                url=str(getattr(entry, "link", "")),
                            )
                        )
        return items, warnings
