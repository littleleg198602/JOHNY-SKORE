from __future__ import annotations

from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.contracts import (
    AgentContext,
    AgentEvidence,
    AgentResult,
    AgentStatus,
    RegulatoryContractEvent,
    RegulatoryContractEventType,
    RegulatoryEventStatus,
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
from market_checker_app.config import RegulatoryContractConfig
from market_checker_app.utils.text import normalize_ticker


class RegulatoryContractAgent(BaseAgent):
    """Normalize explicit public contract and regulatory events without scoring."""

    name = "regulatory_contract"
    version = "1.0"
    required = False
    dependencies = ("entity_registry",)

    def __init__(
        self,
        config: RegulatoryContractConfig | None = None,
        *,
        client: ShortReportClient | None = None,
    ) -> None:
        self.config = config or RegulatoryContractConfig()
        self.client = client
        if self.config.source_verification.enabled and self.client is None:
            self.client = build_source_client(self.config.source_verification)

    def run(self, context: AgentContext) -> AgentResult:
        if not self.config.enabled:
            return AgentResult(
                status=AgentStatus.UNAVAILABLE,
                warnings=["RegulatoryContractAgent je vypnutý v konfiguraci."],
            )
        if not self.config.sources:
            return AgentResult(
                status=AgentStatus.UNAVAILABLE,
                warnings=[
                    "RegulatoryContractAgent nemá nakonfigurované žádné události."
                ],
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
                state_updates={"regulatory_contract_events_by_ticker": {}},
            )

        observed_at = utc_now()
        documents = {}
        events: list[RegulatoryContractEvent] = []
        evidence: list[AgentEvidence] = []
        warnings: list[str] = []
        rejected_sources = 0
        seen_records: set[str] = set()

        for source in matching_sources:
            ticker = normalize_ticker(source.ticker)
            try:
                event_type = RegulatoryContractEventType(
                    str(source.event_type).strip().upper()
                )
                status = RegulatoryEventStatus(str(source.status).strip().upper())
                title = str(source.title or "").strip()
                authority = str(source.authority_or_counterparty or "").strip()
                publisher = str(source.publisher or "").strip()
                if not title or not authority or not publisher:
                    raise ValueError(
                        "chybí název události, protistrana/úřad nebo vydavatel"
                    )
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
                    source_type="regulatory_contract_reference",
                    stage_record_type="regulatory_contract_event",
                    fetched=fetched,
                    content_verification_required=(
                        self.config.source_verification.enabled
                    ),
                    support_terms=(title, authority),
                    discovery_method=source.discovery_method,
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
                event_id = "regulatory-event:" + stable_id(
                    ticker,
                    event_type.value,
                    title.casefold(),
                    authority.casefold(),
                    document.document_id,
                )
                event = RegulatoryContractEvent(
                    event_id=event_id,
                    ticker=ticker,
                    event_type=event_type,
                    status=status,
                    title=title,
                    authority_or_counterparty=authority,
                    observed_at=observed_at,
                    published_at=source.published_at,
                    document_id=document.document_id,
                    source_url=document.url or source.url,
                    event_value=source.event_value,
                    currency=source.currency,
                    confidence=effective_confidence,
                    source_agent_name=self.name,
                    metadata={
                        "publisher": publisher,
                        "stage": 3,
                        "event_truth_assessed": False,
                        "causal_impact_assessed": False,
                        "source_content_support_detected": support_detected,
                        "scoring_applied": False,
                    },
                )
            except (AttributeError, TypeError, ValueError) as exc:
                rejected_sources += 1
                warnings.append(f"RegulatoryContractAgent {ticker}: {exc}.")
                continue

            if event_id in seen_records:
                continue
            seen_records.add(event_id)
            documents[document.document_id] = document
            events.append(event)
            evidence.append(
                AgentEvidence(
                    evidence_id=stable_id(
                        context.orchestration_id,
                        self.name,
                        event_id,
                    ),
                    ticker=ticker,
                    agent_name=self.name,
                    event_type="REGULATORY_CONTRACT_EVENT_RECORDED",
                    observed_at=observed_at,
                    summary=(
                        f"Zaznamenána událost {event_type.value} ({status.value}) "
                        f"pro {ticker}: {title}."
                    ),
                    direction=0.0,
                    risk_score=0.0,
                    confidence=event.confidence,
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
                    source_urls=[event.source_url],
                    metadata={
                        "regulatory_event_id": event_id,
                        "event_value": event.event_value,
                        "currency": event.currency,
                        "stage": 3,
                        "causal_impact_assessed": False,
                        "source_content_support_detected": support_detected,
                        "scoring_applied": False,
                        "shadow_mode": context.shadow_mode,
                    },
                )
            )

        by_ticker: dict[str, list[RegulatoryContractEvent]] = {}
        for event in events:
            by_ticker.setdefault(event.ticker, []).append(event)
        if not events:
            status = AgentStatus.UNAVAILABLE
        elif rejected_sources:
            status = AgentStatus.PARTIAL
        else:
            status = AgentStatus.SUCCESS
        return AgentResult(
            status=status,
            documents=list(documents.values()),
            regulatory_contract_events=events,
            evidence=evidence,
            warnings=list(dict.fromkeys(warnings)),
            metadata={
                "configured_sources": len(self.config.sources),
                "matching_sources": len(matching_sources),
                "events": len(events),
                "rejected_sources": rejected_sources,
                "scoring_applied": False,
            },
            state_updates={"regulatory_contract_events_by_ticker": by_ticker},
        )
