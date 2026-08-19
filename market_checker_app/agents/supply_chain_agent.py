from __future__ import annotations

from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.contracts import (
    AgentContext,
    AgentEvidence,
    AgentResult,
    AgentStatus,
    CompanyRelationship,
    RelationshipType,
    utc_now,
)
from market_checker_app.agents.network_intelligence_common import (
    build_source_client,
    fetch_source_document,
    reference_document,
    stable_id,
)
from market_checker_app.collectors.short_report_client import (
    ShortReportClient,
)
from market_checker_app.config import SupplyChainConfig
from market_checker_app.utils.text import normalize_ticker


class SupplyChainAgent(BaseAgent):
    """Normalize explicit supplier/customer relationships without scoring them."""

    name = "supply_chain"
    version = "1.0"
    required = False
    dependencies = ("entity_registry",)

    def __init__(
        self,
        config: SupplyChainConfig | None = None,
        *,
        client: ShortReportClient | None = None,
    ) -> None:
        self.config = config or SupplyChainConfig()
        self.client = client
        if self.config.source_verification.enabled and self.client is None:
            self.client = build_source_client(self.config.source_verification)

    def run(self, context: AgentContext) -> AgentResult:
        if not self.config.enabled:
            return AgentResult(
                status=AgentStatus.UNAVAILABLE,
                warnings=["SupplyChainAgent je vypnutý v konfiguraci."],
            )
        if not self.config.sources:
            return AgentResult(
                status=AgentStatus.UNAVAILABLE,
                warnings=["SupplyChainAgent nemá nakonfigurované žádné vztahy."],
            )

        watchlist = {
            normalize_ticker(ticker)
            for ticker in context.watchlist
            if normalize_ticker(ticker)
        }
        matching_sources = [
            source
            for source in self.config.sources
            if normalize_ticker(source.ticker) in watchlist
        ]
        if not matching_sources:
            return AgentResult(
                metadata={
                    "configured_sources": len(self.config.sources),
                    "matching_sources": 0,
                    "scoring_applied": False,
                },
                state_updates={"supply_chain_relationships_by_ticker": {}},
            )

        observed_at = utc_now()
        documents = {}
        relationships: list[CompanyRelationship] = []
        evidence: list[AgentEvidence] = []
        warnings: list[str] = []
        rejected_sources = 0
        seen_records: set[str] = set()

        for source in matching_sources:
            ticker = normalize_ticker(source.ticker)
            try:
                relationship_type = RelationshipType(
                    str(source.relationship_type).strip().upper()
                )
                counterparty = str(source.counterparty or "").strip()
                publisher = str(source.publisher or "").strip()
                if not counterparty or not publisher:
                    raise ValueError("chybí protistrana nebo vydavatel zdroje")
                if (
                    source.published_at.tzinfo is None
                    or source.published_at.utcoffset() is None
                ):
                    raise ValueError("datum zveřejnění nemá časové pásmo")
                if source.published_at > context.started_at:
                    raise ValueError("zdroj má budoucí datum zveřejnění")
                fetched = None
                if self.config.source_verification.enabled:
                    if self.client is None:
                        raise ValueError("chybí klient pro ověření obsahu zdroje")
                    try:
                        fetched = fetch_source_document(
                            self.client,
                            ticker=ticker,
                            publisher=publisher,
                            published_at=source.published_at,
                            url=source.url,
                        )
                    except Exception as exc:
                        raise ValueError(
                            f"obsah veřejného zdroje nelze ověřit: {exc}"
                        ) from exc
                document = reference_document(
                    ticker=ticker,
                    publisher=publisher,
                    published_at=source.published_at,
                    observed_at=observed_at,
                    url=source.url,
                    source_type="supply_chain_reference",
                    stage_record_type="company_relationship",
                    fetched=fetched,
                    content_verification_required=(
                        self.config.source_verification.enabled
                    ),
                    support_terms=(counterparty,),
                )
                support_detected = bool(
                    document.metadata.get("source_content_support_detected")
                )
                effective_confidence = (
                    min(float(source.confidence), 0.45)
                    if self.config.source_verification.enabled
                    and not support_detected
                    else float(source.confidence)
                )
                relationship_id = "relationship:" + stable_id(
                    ticker,
                    counterparty.casefold(),
                    relationship_type.value,
                    document.document_id,
                )
                relationship = CompanyRelationship(
                    relationship_id=relationship_id,
                    ticker=ticker,
                    counterparty=counterparty,
                    relationship_type=relationship_type,
                    observed_at=observed_at,
                    published_at=source.published_at,
                    document_id=document.document_id,
                    source_url=document.url or source.url,
                    dependency_pct=source.dependency_pct,
                    confidence=effective_confidence,
                    source_agent_name=self.name,
                    metadata={
                        "publisher": publisher,
                        "stage": 3,
                        "relationship_truth_assessed": False,
                        "causal_impact_assessed": False,
                        "source_content_support_detected": support_detected,
                        "scoring_applied": False,
                    },
                )
            except (AttributeError, TypeError, ValueError) as exc:
                rejected_sources += 1
                warnings.append(f"SupplyChainAgent {ticker}: {exc}.")
                continue

            if relationship_id in seen_records:
                continue
            seen_records.add(relationship_id)
            documents[document.document_id] = document
            relationships.append(relationship)
            evidence.append(
                AgentEvidence(
                    evidence_id=stable_id(
                        context.orchestration_id,
                        self.name,
                        relationship_id,
                    ),
                    ticker=ticker,
                    agent_name=self.name,
                    event_type="SUPPLY_CHAIN_RELATIONSHIP_RECORDED",
                    observed_at=observed_at,
                    summary=(
                        f"Zaznamenán vztah {relationship_type.value} mezi "
                        f"{ticker} a {counterparty}."
                    ),
                    direction=0.0,
                    risk_score=0.0,
                    confidence=relationship.confidence,
                    hard_veto=False,
                    reasons=[
                        "explicit_public_source_reference",
                        (
                            "source_content_not_requested"
                            if not self.config.source_verification.enabled
                            else (
                                "source_content_support_detected"
                                if support_detected
                                else "source_content_support_not_detected"
                            )
                        ),
                    ],
                    document_ids=[document.document_id],
                    source_urls=[relationship.source_url],
                    metadata={
                        "relationship_id": relationship_id,
                        "dependency_pct": relationship.dependency_pct,
                        "stage": 3,
                        "causal_impact_assessed": False,
                        "source_content_support_detected": support_detected,
                        "scoring_applied": False,
                        "shadow_mode": context.shadow_mode,
                    },
                )
            )

        by_ticker: dict[str, list[CompanyRelationship]] = {}
        for relationship in relationships:
            by_ticker.setdefault(relationship.ticker, []).append(relationship)
        if not relationships:
            status = AgentStatus.UNAVAILABLE
        elif rejected_sources:
            status = AgentStatus.PARTIAL
        else:
            status = AgentStatus.SUCCESS
        return AgentResult(
            status=status,
            documents=list(documents.values()),
            company_relationships=relationships,
            evidence=evidence,
            warnings=list(dict.fromkeys(warnings)),
            metadata={
                "configured_sources": len(self.config.sources),
                "matching_sources": len(matching_sources),
                "relationships": len(relationships),
                "rejected_sources": rejected_sources,
                "scoring_applied": False,
            },
            state_updates={"supply_chain_relationships_by_ticker": by_ticker},
        )
