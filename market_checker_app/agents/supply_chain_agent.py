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
from market_checker_app.services.filing_exposure_discovery_service import (
    FilingExposureDiscoveryService,
)
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
        dependencies: tuple[str, ...] | None = None,
    ) -> None:
        self.config = config or SupplyChainConfig()
        self.client = client
        if dependencies is not None:
            self.dependencies = dependencies
        if self.config.source_verification.enabled and self.client is None:
            self.client = build_source_client(self.config.source_verification)

    def run(self, context: AgentContext) -> AgentResult:
        if not self.config.enabled:
            return AgentResult(
                status=AgentStatus.UNAVAILABLE,
                warnings=["SupplyChainAgent je vypnutý v konfiguraci."],
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
        candidates: list[tuple[object, object | None, tuple[str, ...], str, str]] = [
            (
                source,
                None,
                (str(source.counterparty),),
                "manual",
                "explicit_public_source_reference",
            )
            for source in matching_sources
        ]
        auto_discovered_count = 0
        scanned_sec_filings = 0
        if self.config.auto_discover_from_sec_filings:
            filing_texts = context.state.get("sec_filing_texts_by_ticker")
            if isinstance(filing_texts, dict):
                discovery = FilingExposureDiscoveryService()
                for ticker in sorted(watchlist):
                    fetched_items = filing_texts.get(ticker, [])
                    if not isinstance(fetched_items, list):
                        continue
                    for fetched in fetched_items:
                        scanned_sec_filings += 1
                        findings = discovery.discover(
                            fetched,
                            max_supply_chain=(
                                self.config.max_auto_discovered_relationships_per_filing
                            ),
                            max_commodity_energy=0,
                        )
                        for finding in findings.supply_chain:
                            candidates.append(
                                (
                                    finding.source,
                                    fetched,
                                    (finding.support_term,),
                                    "sec_filing",
                                    finding.reason,
                                )
                            )
                            auto_discovered_count += 1
        if not candidates:
            if scanned_sec_filings:
                return AgentResult(
                    status=AgentStatus.SUCCESS,
                    metadata={
                        "configured_sources": len(self.config.sources),
                        "matching_sources": 0,
                        "scanned_sec_filings": scanned_sec_filings,
                        "auto_discovered_relationships": 0,
                        "relationships": 0,
                        "scoring_applied": False,
                    },
                    state_updates={"supply_chain_relationships_by_ticker": {}},
                )
            return AgentResult(
                status=AgentStatus.UNAVAILABLE,
                warnings=[
                    "SupplyChainAgent nenašel ruční zdroj ani explicitní "
                    "dodavatelskou koncentraci v načteném SEC 10-K."
                ],
                metadata={
                    "configured_sources": len(self.config.sources),
                    "matching_sources": 0,
                    "scanned_sec_filings": 0,
                    "auto_discovered_relationships": 0,
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

        for (
            source,
            prefetched,
            support_terms,
            discovery_method,
            discovery_reason,
        ) in candidates:
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
                fetched = prefetched
                if self.config.source_verification.enabled and fetched is None:
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
                    support_terms=support_terms,
                    discovery_method=discovery_method,
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
                        "discovery_method": discovery_method,
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
                        discovery_reason,
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
                        "discovery_method": discovery_method,
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
                "scanned_sec_filings": scanned_sec_filings,
                "auto_discovered_relationships": auto_discovered_count,
                "relationships": len(relationships),
                "rejected_sources": rejected_sources,
                "scoring_applied": False,
            },
            state_updates={"supply_chain_relationships_by_ticker": by_ticker},
        )
