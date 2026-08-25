from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Callable, Iterable

from market_checker_app.agents.contracts import (
    DocumentRecord,
    DocumentSourcePriority,
)


SOURCE_TYPE_BY_PRIORITY = {
    DocumentSourcePriority.REGULATORY_FILING: "regulatory_filing",
    DocumentSourcePriority.AUDITED_FINANCIAL_STATEMENT: (
        "audited_financial_statement"
    ),
    DocumentSourcePriority.EXCHANGE_ANNOUNCEMENT: "exchange_announcement",
    DocumentSourcePriority.INVESTOR_RELATIONS: "investor_relations",
    DocumentSourcePriority.MANAGEMENT_PRESENTATION: "management_presentation",
    DocumentSourcePriority.MEDIA_ARTICLE: "media_article",
}


@dataclass(frozen=True, slots=True)
class DocumentConflictResolution:
    conflict_key: str
    preferred_document_id: str
    retained_document_ids: tuple[str, ...]


def source_priority_for(
    source_type: str,
    *,
    audited: bool = False,
) -> int:
    normalized = str(source_type or "").strip().lower()
    if audited:
        return int(DocumentSourcePriority.AUDITED_FINANCIAL_STATEMENT)
    for priority, candidate in SOURCE_TYPE_BY_PRIORITY.items():
        if normalized == candidate:
            return int(priority)
    return int(DocumentSourcePriority.UNKNOWN)


def _canonical_family(document: DocumentRecord) -> str | None:
    form = str(document.metadata.get("form") or "").strip().upper().removesuffix("/A")
    if form in {"10-K", "20-F", "40-F"}:
        return "ANNUAL_REPORT"
    if form == "10-Q":
        return "QUARTERLY_REPORT"
    if form in {"8-K", "6-K"}:
        return "CURRENT_REPORT"
    if form in {"S-1", "424B1", "424B2", "424B3", "424B4", "424B5", "424B7", "424B8"}:
        return "PROSPECTUS"
    if form == "4":
        return "INSIDER_TRANSACTION"
    if form in {"SC 13D", "SC 13G"}:
        return "OWNERSHIP_DISCLOSURE"

    document_type = str(
        document.metadata.get("document_type") or document.source_type or ""
    ).strip().casefold()
    if any(token in document_type for token in ("annual", "year", "výroční", "fy")):
        return "ANNUAL_REPORT"
    if any(token in document_type for token in ("quarter", "interim", "half", "pololet")):
        return "INTERIM_REPORT"
    if "prospect" in document_type:
        return "PROSPECTUS"
    if "presentation" in document_type:
        return "MANAGEMENT_PRESENTATION"
    return None


def canonical_event_key_for(document: DocumentRecord) -> str | None:
    """Return a conservative cross-agent event key without name matching."""

    explicit = str(
        document.canonical_event_key
        or document.metadata.get("canonical_event_key")
        or ""
    ).strip()
    if explicit:
        return explicit
    family = _canonical_family(document)
    if family is None:
        return None
    period = document.reporting_period_end
    if period is None:
        raw_period = document.metadata.get("report_date")
        if raw_period:
            try:
                period = datetime.fromisoformat(str(raw_period).replace("Z", "+00:00"))
            except ValueError:
                period = None
    if period is None:
        period = document.published_at
    if period is None:
        return None
    issuer_identity = str(
        document.issuer_id or document.legal_entity_id or document.ticker
    ).strip()
    instrument_identity = str(
        document.instrument_id or f"ticker:{document.ticker}"
    ).strip()
    if not issuer_identity or not instrument_identity:
        return None
    # SourceResolutionAgent deliberately resolves one ticker/instrument at a
    # time.  Include the instrument scope so two listed share classes of the
    # same issuer never collapse into one contradictory canonical event.
    normalized_identity = re.sub(
        r"\s+",
        "",
        f"{issuer_identity}|{instrument_identity}",
    ).upper()
    return f"{normalized_identity}|{family}|{_timestamp(period).date().isoformat()}"


def assign_canonical_event_key(document: DocumentRecord) -> str | None:
    key = canonical_event_key_for(document)
    if key:
        document.canonical_event_key = key
        document.metadata["canonical_event_key"] = key
    return key


def is_primary_confirmation(document: DocumentRecord | None) -> bool:
    """Only authority/exchange evidence can independently confirm a claim."""

    return bool(
        document is not None
        and int(document.source_priority or 0)
        >= int(DocumentSourcePriority.EXCHANGE_ANNOUNCEMENT)
    )


def _timestamp(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def document_precedence_key(document: DocumentRecord) -> tuple[object, ...]:
    return (
        int(document.source_priority or 0),
        bool(document.is_audited),
        _timestamp(document.published_at),
        _timestamp(document.observed_at),
        document.document_id,
    )


def resolve_document_conflicts(
    documents: Iterable[DocumentRecord],
    *,
    conflict_key: Callable[[DocumentRecord], str | None] | None = None,
    include_singletons: bool = False,
) -> tuple[list[DocumentRecord], list[DocumentConflictResolution]]:
    """Retain every document while selecting one deterministic preferred source."""

    retained = list(documents)
    key_function = conflict_key or (
        lambda item: assign_canonical_event_key(item)
    )
    groups: dict[str, list[DocumentRecord]] = defaultdict(list)
    for document in retained:
        key = key_function(document)
        if key:
            groups[key].append(document)

    resolutions: list[DocumentConflictResolution] = []
    for key, group in sorted(groups.items()):
        if len(group) < 2 and not include_singletons:
            continue
        preferred = max(group, key=document_precedence_key)
        resolutions.append(
            DocumentConflictResolution(
                conflict_key=key,
                preferred_document_id=preferred.document_id,
                retained_document_ids=tuple(
                    item.document_id
                    for item in sorted(group, key=document_precedence_key, reverse=True)
                ),
            )
        )
    return retained, resolutions
