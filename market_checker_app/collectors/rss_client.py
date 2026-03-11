from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

import feedparser

from market_checker_app.models import NewsItem


class RSSClient:
    def __init__(self, max_items_per_source: int = 30) -> None:
        self.max_items_per_source = max_items_per_source

    @staticmethod
    def _sentiment_score(text: str) -> float:
        positive = {
            "beat", "beats", "growth", "upgrade", "upgraded", "surge", "strong", "record", "profit", "profits", "buyback"
        }
        negative = {
            "miss", "misses", "downgrade", "downgraded", "lawsuit", "probe", "drop", "falls", "fall", "weak", "loss", "losses"
        }
        words = {w.strip(".,:;!?()[]{}\"'").lower() for w in text.split()}
        raw = sum(1 for w in words if w in positive) - sum(1 for w in words if w in negative)
        raw = max(-4, min(4, raw))
        return raw / 4.0

    @staticmethod
    def _recency_weight(published_at: datetime, now: datetime) -> float:
        age_days = max(0.0, (now - published_at).total_seconds() / 86400.0)
        # half-life ~14 days keeps fresh news dominant, but preserves impact for up to 90 days.
        decay = math.exp(-math.log(2.0) * age_days / 14.0)
        return max(0.05, decay)

    def collect(self, rss_sources: list[str], tickers: list[str]) -> tuple[list[NewsItem], list[str]]:
        warnings: list[str] = []
        ticker_set = set(tickers)
        now = datetime.now(timezone.utc)
        cutoff_3m = now - timedelta(days=90)
        items: list[NewsItem] = []

        for source in rss_sources:
            try:
                parsed = feedparser.parse(source)
            except Exception as exc:
                warnings.append(f"RSS načtení selhalo ({source}). Zdroj byl přeskočen. Detail: {exc}")
                continue

            if getattr(parsed, "bozo", False):
                bozo_exc = getattr(parsed, "bozo_exception", "neznámá chyba parseru")
                warnings.append(f"RSS parser hlásí problém pro {source}. Pokračuji s dostupnými položkami. Detail: {bozo_exc}")

            entries = list(getattr(parsed, "entries", []))
            if not entries:
                warnings.append(f"RSS zdroj {source} nevrátil žádné položky.")
                continue

            for entry in entries[: self.max_items_per_source]:
                title = str(getattr(entry, "title", ""))
                summary = str(getattr(entry, "summary", ""))
                published_parsed = getattr(entry, "published_parsed", None)
                if published_parsed is None:
                    published_at = now
                else:
                    published_at = datetime(*published_parsed[:6], tzinfo=timezone.utc)
                if published_at < cutoff_3m:
                    continue

                text = f"{title} {summary}"
                text_upper = text.upper()
                sentiment = self._sentiment_score(text)
                recency = self._recency_weight(published_at, now)
                sentiment_weight = round(recency * sentiment, 4)

                for ticker in ticker_set:
                    if ticker in text_upper:
                        items.append(
                            NewsItem(
                                ticker=ticker,
                                source=source,
                                title=title,
                                summary=summary,
                                published_at=published_at,
                                sentiment_weight=sentiment_weight,
                                url=str(getattr(entry, "link", "")),
                            )
                        )
        return items, warnings
