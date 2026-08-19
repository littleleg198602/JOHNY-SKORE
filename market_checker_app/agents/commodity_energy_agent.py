from __future__ import annotations

from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.contracts import (
    AgentContext,
    AgentEvidence,
    AgentResult,
    AgentStatus,
    ResourceExposure,
    ResourceExposureType,
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
from market_checker_app.config import CommodityEnergyConfig
from market_checker_app.utils.text import normalize_ticker


class CommodityEnergyAgent(BaseAgent):
    """Normalize explicit commodity and energy exposures without price scoring."""

    name = "commodity_energy"
    version = "1.0"
    required = False
    dependencies = ("entity_registry",)

    def __init__(
        self,
        config: CommodityEnergyConfig | None = None,
        *,
        client: ShortReportClient | None = None,
    ) -> None:
        self.config = config or CommodityEnergyConfig()
        self.client = client
        if self.config.source_verification.enabled and self.client is None:
            self.client = build_source_client(self.config.source_verification)

    def run(self, context: AgentContext) -> AgentResult:
        if not self.config.enabled:
            return AgentResult(
                status=AgentStatus.UNAVAILABLE,
                warnings=["CommodityEnergyAgent je vypnutý v konfiguraci."],
            )
        if not self.config.sources:
            return AgentResult(
                status=AgentStatus.UNAVAILABLE,
                warnings=["CommodityEnergyAgent nemá nakonfigurované žádné expozice."],
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
                state_updates={"resource_exposures_by_ticker": {}},
            )

        observed_at = utc_now()
        documents = {}
        exposures: list[ResourceExposure] = []
        evidence: list[AgentEvidence] = []
        warnings: list[str] = []
        rejected_sources = 0
        seen_records: set[str] = set()

        for source in matching_sources:
            ticker = normalize_ticker(source.ticker)
            try:
                exposure_type = ResourceExposureType(
                    str(source.exposure_type).strip().upper()
                )
                resource_name = str(source.resource_name or "").strip()
                publisher = str(source.publisher or "").strip()
                if not resource_name or not publisher:
                    raise ValueError("chybí zdroj/komodita nebo vydavatel")
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
                    source_type="commodity_energy_reference",
                    stage_record_type="resource_exposure",
                    fetched=fetched,
                    content_verification_required=(
                        self.config.source_verification.enabled
                    ),
                    support_terms=(resource_name,),
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
                exposure_id = "exposure:" + stable_id(
                    ticker,
                    resource_name.casefold(),
                    exposure_type.value,
                    document.document_id,
                )
                exposure = ResourceExposure(
                    exposure_id=exposure_id,
                    ticker=ticker,
                    resource_name=resource_name,
                    exposure_type=exposure_type,
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
                        "price_series_attached": False,
                        "causal_impact_assessed": False,
                        "source_content_support_detected": support_detected,
                        "scoring_applied": False,
                    },
                )
            except (AttributeError, TypeError, ValueError) as exc:
                rejected_sources += 1
                warnings.append(f"CommodityEnergyAgent {ticker}: {exc}.")
                continue

            if exposure_id in seen_records:
                continue
            seen_records.add(exposure_id)
            documents[document.document_id] = document
            exposures.append(exposure)
            evidence.append(
                AgentEvidence(
                    evidence_id=stable_id(
                        context.orchestration_id,
                        self.name,
                        exposure_id,
                    ),
                    ticker=ticker,
                    agent_name=self.name,
                    event_type="RESOURCE_EXPOSURE_RECORDED",
                    observed_at=observed_at,
                    summary=(
                        f"Zaznamenána expozice {exposure_type.value}: "
                        f"{resource_name} pro {ticker}."
                    ),
                    direction=0.0,
                    risk_score=0.0,
                    confidence=exposure.confidence,
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
                    source_urls=[exposure.source_url],
                    metadata={
                        "exposure_id": exposure_id,
                        "dependency_pct": exposure.dependency_pct,
                        "stage": 3,
                        "price_series_attached": False,
                        "causal_impact_assessed": False,
                        "source_content_support_detected": support_detected,
                        "scoring_applied": False,
                        "shadow_mode": context.shadow_mode,
                    },
                )
            )

        by_ticker: dict[str, list[ResourceExposure]] = {}
        for exposure in exposures:
            by_ticker.setdefault(exposure.ticker, []).append(exposure)
        if not exposures:
            status = AgentStatus.UNAVAILABLE
        elif rejected_sources:
            status = AgentStatus.PARTIAL
        else:
            status = AgentStatus.SUCCESS
        return AgentResult(
            status=status,
            documents=list(documents.values()),
            resource_exposures=exposures,
            evidence=evidence,
            warnings=list(dict.fromkeys(warnings)),
            metadata={
                "configured_sources": len(self.config.sources),
                "matching_sources": len(matching_sources),
                "exposures": len(exposures),
                "rejected_sources": rejected_sources,
                "price_series_attached": False,
                "scoring_applied": False,
            },
            state_updates={"resource_exposures_by_ticker": by_ticker},
        )
