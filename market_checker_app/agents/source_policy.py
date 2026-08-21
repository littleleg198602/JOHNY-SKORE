from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
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
) -> tuple[list[DocumentRecord], list[DocumentConflictResolution]]:
    """Retain every document while selecting one deterministic preferred source."""

    retained = list(documents)
    key_function = conflict_key or (
        lambda item: str(item.metadata.get("canonical_event_key") or "").strip()
        or None
    )
    groups: dict[str, list[DocumentRecord]] = defaultdict(list)
    for document in retained:
        key = key_function(document)
        if key:
            groups[key].append(document)

    resolutions: list[DocumentConflictResolution] = []
    for key, group in sorted(groups.items()):
        if len(group) < 2:
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
