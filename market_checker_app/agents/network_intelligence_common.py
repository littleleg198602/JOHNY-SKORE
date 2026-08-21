from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import re

from market_checker_app.agents.contracts import DocumentRecord
from market_checker_app.collectors.short_report_client import (
    FetchedShortReport,
    ShortReportClient,
)
from market_checker_app.config import (
    ShortReportSourceConfig,
    Stage3SourceVerificationConfig,
)
from market_checker_app.utils.source_validation import (
    PublicSourceError,
    public_https_reference,
)


def stable_id(*parts: object) -> str:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return sha256(payload).hexdigest()


NetworkSourceError = PublicSourceError


def build_source_client(
    config: Stage3SourceVerificationConfig,
) -> ShortReportClient:
    return ShortReportClient(
        user_agent=config.user_agent,
        timeout_seconds=config.request_timeout_seconds,
        max_download_bytes=config.max_download_bytes,
        max_text_characters=config.max_text_characters,
    )


def fetch_source_document(
    client: ShortReportClient,
    *,
    ticker: str,
    publisher: str,
    published_at: datetime,
    url: str,
) -> FetchedShortReport:
    return client.fetch(
        ShortReportSourceConfig(
            ticker=ticker,
            publisher=publisher,
            published_at=published_at,
            url=url,
        )
    )


def reference_document(
    *,
    ticker: str,
    publisher: str,
    published_at: datetime,
    observed_at: datetime,
    url: str,
    source_type: str,
    stage_record_type: str,
    fetched: FetchedShortReport | None = None,
    content_verification_required: bool = False,
    support_terms: tuple[str, ...] = (),
    discovery_method: str = "manual",
    source_authority: str | None = None,
    legal_entity_id: str | None = None,
    issuer_id: str | None = None,
    instrument_id: str | None = None,
    canonical_event_key: str | None = None,
) -> DocumentRecord:
    normalized_url = public_https_reference(url)
    final_url = public_https_reference(fetched.final_url) if fetched else normalized_url
    searchable_text = (
        re.sub(
            r"[^\w]+",
            " ",
            f"{fetched.title} {fetched.text}".casefold(),
        ).strip()
        if fetched
        else ""
    )
    normalized_terms = tuple(
        re.sub(r"[^\w]+", " ", str(term or "").casefold()).strip()
        for term in support_terms
        if len(str(term or "").strip()) >= 3
    )
    support_detected = bool(
        fetched
        and normalized_terms
        and any(term in searchable_text for term in normalized_terms)
    )
    document_id = f"stage3:{source_type}:" + stable_id(
        ticker,
        publisher.strip().lower(),
        published_at.isoformat(),
        normalized_url,
    )
    return DocumentRecord(
        document_id=document_id,
        ticker=ticker,
        source=publisher.strip(),
        source_type=source_type,
        source_authority=source_authority,
        legal_entity_id=legal_entity_id,
        issuer_id=issuer_id,
        instrument_id=instrument_id,
        canonical_event_key=canonical_event_key,
        observed_at=observed_at,
        url=final_url,
        published_at=published_at,
        content_hash=fetched.content_hash if fetched else None,
        mime_type=fetched.mime_type if fetched else None,
        metadata={
            "stage": 3,
            "stage_record_type": stage_record_type,
            "explicitly_configured_source": discovery_method == "manual",
            "discovery_method": discovery_method,
            "original_url": normalized_url,
            "content_verification_required": content_verification_required,
            "content_fetched": fetched is not None,
            "source_content_support_detected": support_detected,
            "support_terms_checked": list(normalized_terms),
            "fetched_title": fetched.title[:500] if fetched else "",
            "download_size_bytes": fetched.size_bytes if fetched else 0,
            "extractor": fetched.extractor if fetched else "",
            "truth_assessed": False,
            "canonical_event_key": canonical_event_key,
            "scoring_applied": False,
        },
    )
