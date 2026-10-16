from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

from market_checker_app.models import NewsItem
from market_checker_app.services.ir_primary_verification_service import (
    CapturedIRDocument, verify_primary_ir_lead,
)
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.utils.source_validation import PublicSourceError, public_https_reference
from market_checker_app.utils.text import normalize_ticker


def _local_ingestion_clock() -> datetime:
    # Report cutoffs and archive/source timestamps cannot backdate this clock.
    return datetime.now(timezone.utc)


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

    def verify_primary_candidate(
        self, finding_id: str, *, ticker: str, feed_capture: CapturedIRDocument,
        primary_capture: CapturedIRDocument, authority_capture: CapturedIRDocument,
        publisher_hosts: tuple[str, ...], as_of: datetime, claim_quote: str | None = None,
    ) -> dict[str, object]:
        """Import a primary-source audit from captured bytes, without HTTP/scoring.

        The RSS database row remains UNVERIFIED: source-only proof cannot enter
        an issuer-scoped verified slot. Existing leads are never overwritten.
        """
        ticker = normalize_ticker(ticker)
        candidate = self.store.finding_by_id(finding_id, subject_id=ticker)
        if candidate is None or candidate["source"] != "rss":
            raise ValueError("IR review requires that ticker's existing RSS candidate")
        details = json.loads(str(candidate["details_json"]))
        if details.get("stage") != "search_candidate":
            raise ValueError("IR review cannot use another primary review as an RSS lead")
        if datetime.fromisoformat(str(candidate["available_at"])) > as_of:
            raise ValueError("IR review cannot import a future RSS candidate")
        article = NewsItem(
            ticker=ticker, source=str(details.get("feed_url") or "rss"),
            title=str(candidate["title"]), summary="", sentiment_weight=0.0,
            published_at=datetime.fromisoformat(str(candidate["published_at"])),
            observed_at=datetime.fromisoformat(str(candidate["first_observed_at"])),
            url=str(candidate["source_url"]), original_url=str(candidate["source_url"]),
            feed_url=str(details.get("feed_url") or ""),
        )
        report = verify_primary_ir_lead(
            article, feed_capture=feed_capture, primary_capture=primary_capture,
            authority_capture=authority_capture, publisher_hosts=publisher_hosts,
            knowledge_at=as_of, claim_quote=claim_quote,
        )
        stable_report = {key: value for key, value in report.items() if key != "reviewed_at"}
        digest = hashlib.sha256(json.dumps(stable_report, sort_keys=True).encode()).hexdigest()
        completed_at = _local_ingestion_clock()
        inputs_available = max(datetime.fromisoformat(str(candidate["available_at"])),
                               datetime.fromisoformat(str(report["source_available_at"])))
        if inputs_available > completed_at:
            raise ValueError("IR capture inputs cannot be locally imported before their observation clocks")
        available_at = max(inputs_available, completed_at)
        report["local_import_completed_at"] = completed_at.isoformat()
        report["local_verification_available_at"] = available_at.isoformat()
        report["requested_cutoff_is_not_local_availability"] = True
        review_id, created = self.store.record_finding(
            source="rss", subject_id=ticker, source_object_id=f"{candidate['source_object_id']}:primary-review",
            content_hash=digest, title=f"Primární zdroj: {candidate['title']}"[:350],
            source_url=primary_capture.resolved_url, locator="ir:primary_source_review",
            published_at=article.published_at, available_at=available_at, observed_at=available_at,
            verification_status="UNVERIFIED",
            details={"stage": "ir_primary_source_review", "rss_candidate_id": finding_id,
                     "identity_status": "TICKER_HINT_ONLY", "issuer_attribution_allowed": False,
                     "published_at_semantics": "RSS_LEAD_DATE_NOT_INDEPENDENT_PRIMARY_DISSEMINATION",
                     "primary_source_review": report, "scoring_applied": False},
        )
        stored = self.store.finding_by_id(review_id, subject_id=ticker)
        return {"finding_id": review_id, "created": created, "primary_source_review": report,
                "persisted_available_at": stored["available_at"],
                "verification_status": "UNVERIFIED", "scoring_applied": False}
