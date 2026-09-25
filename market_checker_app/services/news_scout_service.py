from __future__ import annotations

from datetime import datetime, timezone
import hashlib

from market_checker_app.models import NewsItem
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.utils.source_validation import PublicSourceError, public_https_reference
from market_checker_app.utils.text import normalize_ticker


class NewsScoutService:
    """Keep RSS search hits as unverified leads for a later primary-source check."""

    def __init__(self, store: ScoutStore) -> None:
        self.store = store

    def ingest(
        self, articles: list[NewsItem], *, allowed_tickers: list[str],
        max_per_ticker: int = 2,
    ) -> int:
        if max_per_ticker < 1:
            return 0
        allowed = set(allowed_tickers)
        grouped: dict[str, list[NewsItem]] = {}
        for article in articles:
            ticker = normalize_ticker(article.ticker)
            if ticker in allowed:
                grouped.setdefault(ticker, []).append(article)
        created = 0
        for ticker in sorted(grouped):
            candidates = sorted(
                grouped[ticker], key=lambda item: item.published_at, reverse=True,
            )
            accepted = 0
            seen_urls: set[str] = set()
            for article in candidates:
                if accepted >= max_per_ticker:
                    break
                observed = article.observed_at or datetime.now(timezone.utc)
                if observed.tzinfo is None or observed.utcoffset() is None:
                    continue
                published = article.published_at
                if (published.tzinfo is None or published.utcoffset() is None
                        or published > observed):
                    continue
                try:
                    url = public_https_reference(article.original_url or article.url)
                except (PublicSourceError, ValueError):
                    continue
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                accepted += 1
                digest = hashlib.sha256(
                    f"{article.title}|{article.summary}|{url}".encode("utf-8")
                ).hexdigest()
                finding_id, new = self.store.record_finding(
                    source="rss", subject_id=ticker,
                    source_object_id=url, content_hash=digest,
                    title=article.title[:350], source_url=url, locator="rss:title",
                    published_at=published, available_at=observed, observed_at=observed,
                    verification_status="UNVERIFIED",
                    details={"stage": "search_candidate", "publisher": article.publisher,
                             "feed_url": article.feed_url, "event_id": article.event_id,
                             "identity_status": "TICKER_HINT_ONLY"},
                )
                if new:
                    created += 1
                    self.store.add_lead(
                        subject_id=ticker, finding_id=finding_id,
                        question="Ověřit vazbu tohoto článku na emitenta a dohledat primární zdroj.",
                        as_of=observed,
                    )
        return created
