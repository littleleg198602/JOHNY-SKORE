from __future__ import annotations

import hashlib

from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.contracts import (
    AgentContext,
    AgentResult,
    AgentStatus,
    DocumentRecord,
    DocumentSourceResolution,
    utc_now,
)
from market_checker_app.agents.source_policy import resolve_document_conflicts


def _stable_id(*parts: object) -> str:
    return hashlib.sha256(
        "|".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()


class SourceResolutionAgent(BaseAgent):
    """Select and audit one preferred source for every canonical event."""

    name = "source_resolution"
    version = "1.0"
    required = True

    def __init__(self, *, dependencies: tuple[str, ...]) -> None:
        self.dependencies = dependencies

    def run(self, context: AgentContext) -> AgentResult:
        raw_results = context.state.get("agent_results")
        if not isinstance(raw_results, dict):
            raw_results = {}
        documents_by_id: dict[str, DocumentRecord] = {}
        for result in raw_results.values():
            if not isinstance(result, AgentResult):
                continue
            for document in result.documents:
                existing = documents_by_id.get(document.document_id)
                if existing is not None and existing != document:
                    raise ValueError(
                        f"Document ID {document.document_id} má konfliktní obsah napříč agenty."
                    )
                documents_by_id[document.document_id] = document

        documents = list(documents_by_id.values())
        _, raw_resolutions = resolve_document_conflicts(
            documents,
            include_singletons=True,
        )
        observed_at = utc_now()
        resolutions: list[DocumentSourceResolution] = []
        for resolution in raw_resolutions:
            retained = [
                documents_by_id[document_id]
                for document_id in resolution.retained_document_ids
            ]
            tickers = {item.ticker for item in retained}
            legal_entities = {
                item.legal_entity_id for item in retained if item.legal_entity_id
            }
            if len(tickers) != 1 or len(legal_entities) > 1:
                raise ValueError(
                    f"Canonical event {resolution.conflict_key} spojuje rozdílné emitenty."
                )
            ticker = next(iter(tickers))
            legal_entity_id = (
                next(iter(legal_entities)) if legal_entities else None
            )
            resolutions.append(
                DocumentSourceResolution(
                    resolution_id="source-resolution:"
                    + _stable_id(resolution.conflict_key),
                    canonical_event_key=resolution.conflict_key,
                    ticker=ticker,
                    legal_entity_id=legal_entity_id,
                    preferred_document_id=resolution.preferred_document_id,
                    retained_document_ids=resolution.retained_document_ids,
                    observed_at=observed_at,
                    metadata={
                        "candidate_count": len(resolution.retained_document_ids),
                        "conflict_resolved": len(resolution.retained_document_ids) > 1,
                        "all_evidence_retained": True,
                    },
                )
            )
        preferred = {
            item.canonical_event_key: item.preferred_document_id
            for item in resolutions
        }
        return AgentResult(
            status=AgentStatus.SUCCESS,
            document_source_resolutions=resolutions,
            metadata={
                "documents_seen": len(documents),
                "canonical_events": len(resolutions),
                "conflicts_resolved": sum(
                    bool(item.metadata.get("conflict_resolved"))
                    for item in resolutions
                ),
            },
            state_updates={
                "document_source_resolutions": resolutions,
                "preferred_documents_by_canonical_event_key": preferred,
            },
        )
