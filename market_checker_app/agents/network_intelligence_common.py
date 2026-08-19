from __future__ import annotations

from datetime import datetime
from hashlib import sha256

from market_checker_app.agents.contracts import DocumentRecord
from market_checker_app.utils.source_validation import (
    PublicSourceError,
    public_https_reference,
)


def stable_id(*parts: object) -> str:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return sha256(payload).hexdigest()


NetworkSourceError = PublicSourceError


def reference_document(
    *,
    ticker: str,
    publisher: str,
    published_at: datetime,
    observed_at: datetime,
    url: str,
    source_type: str,
    stage_record_type: str,
) -> DocumentRecord:
    normalized_url = public_https_reference(url)
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
        observed_at=observed_at,
        url=normalized_url,
        published_at=published_at,
        metadata={
            "stage": 3,
            "stage_record_type": stage_record_type,
            "explicitly_configured_source": True,
            "content_fetched": False,
            "truth_assessed": False,
            "scoring_applied": False,
        },
    )
