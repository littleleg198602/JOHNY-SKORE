from __future__ import annotations

from datetime import datetime, timezone

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
from market_checker_app.services.filing_exposure_discovery_service import (
    FilingExposureDiscoveryService,
)
from market_checker_app.services.resource_margin_scenario_service import (
    build_resource_margin_scenario,
)
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
        dependencies: tuple[str, ...] | None = None,
    ) -> None:
        self.config = config or CommodityEnergyConfig()
        self.client = client
        if dependencies is not None:
            self.dependencies = dependencies
        if self.config.source_verification.enabled and self.client is None:
            self.client = build_source_client(self.config.source_verification)

    def run(self, context: AgentContext) -> AgentResult:
        if not self.config.enabled:
            return AgentResult(
                status=AgentStatus.UNAVAILABLE,
                warnings=["CommodityEnergyAgent je vypnutý v konfiguraci."],
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
        candidates: list[tuple[object, object | None, tuple[str, ...], str, str, dict | None]] = [
            (
                source,
                None,
                (str(source.resource_name),),
                "manual",
                "explicit_public_source_reference",
                None,
            )
            for source in matching_sources
        ]
        auto_discovered_count = 0
        scanned_sec_filings = 0
        rejected_filings = 0
        discovery_warnings: list[str] = []
        filing_documents = {}
        ambiguous_documents = set()
        prior_results = context.state.get("agent_results")
        if isinstance(prior_results, dict):
            for prior in prior_results.values():
                if not isinstance(prior, AgentResult):
                    continue
                for document in prior.documents:
                    if document.source_type != "regulatory_filing":
                        continue
                    key = (document.ticker, document.url, document.content_hash)
                    if key in filing_documents and filing_documents[key] != document:
                        ambiguous_documents.add(key)
                    filing_documents[key] = document
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
                        try:
                            if normalize_ticker(fetched.source.ticker) != ticker:
                                raise ValueError("SEC podání má jiný ticker než výzkumný kontext")
                            key = (fetched.source.ticker, fetched.final_url, fetched.content_hash)
                            if key in ambiguous_documents:
                                raise ValueError("konfliktní metadata stejného SEC podání")
                            findings = discovery.discover(
                                fetched,
                                max_supply_chain=0,
                                max_commodity_energy=(
                                    self.config.max_auto_discovered_exposures_per_filing
                                ),
                                as_of=context.started_at,
                                filing_document=filing_documents.get(key),
                                require_commodity_issuer_path=True,
                            )
                        except (AttributeError, TypeError, ValueError) as exc:
                            rejected_filings += 1
                            discovery_warnings.append(f"CommodityEnergyAgent {ticker}: {exc}.")
                            continue
                        for finding in findings.commodity_energy:
                            candidates.append(
                                (
                                    finding.source,
                                    fetched,
                                    (finding.support_term, finding.evidence_quote),
                                    "sec_filing",
                                    finding.reason,
                                    finding.provenance,
                                )
                            )
                            auto_discovered_count += 1
        if not candidates:
            if scanned_sec_filings:
                return AgentResult(
                    status=AgentStatus.UNAVAILABLE if rejected_filings else AgentStatus.SUCCESS,
                    warnings=discovery_warnings,
                    metadata={
                        "configured_sources": len(self.config.sources),
                        "matching_sources": 0,
                        "scanned_sec_filings": scanned_sec_filings,
                        "rejected_filings": rejected_filings,
                        "auto_discovered_exposures": 0,
                        "exposures": 0,
                        "price_series_attached": False,
                        "scoring_applied": False,
                    },
                    state_updates={"resource_exposures_by_ticker": {}},
                )
            return AgentResult(
                status=AgentStatus.UNAVAILABLE,
                warnings=[
                    "CommodityEnergyAgent nenašel ruční zdroj ani explicitní "
                    "materiálovou či energetickou expozici v načteném SEC 10-K."
                ],
                metadata={
                    "configured_sources": len(self.config.sources),
                    "matching_sources": 0,
                    "scanned_sec_filings": 0,
                    "auto_discovered_exposures": 0,
                    "scoring_applied": False,
                },
                state_updates={"resource_exposures_by_ticker": {}},
            )

        observed_at = utc_now()
        documents = {}
        exposures: list[ResourceExposure] = []
        evidence: list[AgentEvidence] = []
        warnings: list[str] = discovery_warnings
        rejected_sources = 0
        seen_records: set[str] = set()

        for (
            source,
            prefetched,
            support_terms,
            discovery_method,
            discovery_reason,
            filing_provenance,
        ) in candidates:
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
                    source_type="commodity_energy_reference",
                    stage_record_type="resource_exposure",
                    fetched=fetched,
                    content_verification_required=(
                        self.config.source_verification.enabled
                    ),
                    support_terms=support_terms,
                    discovery_method=discovery_method,
                    source_authority="SEC" if filing_provenance else None,
                    canonical_event_key=(f"sec-filing:{filing_provenance['filing_cik']}:"
                                         f"{filing_provenance['filing_accession_number']}"
                                         if filing_provenance and filing_provenance["filing_accession_number"]
                                         else None),
                )
                if filing_provenance:
                    filing_provenance = dict(filing_provenance)
                    source_known = filing_provenance.get("known_at")
                    known = max(observed_at, source.published_at,
                                datetime.fromisoformat(source_known) if source_known else observed_at)
                    filing_provenance.update(
                        source_known_at=source_known, relationship_observed_at=observed_at.isoformat(),
                        known_at=known.isoformat(), available_at_cutoff=known <= context.started_at,
                    )
                    document.metadata["filing_exposure_provenance"] = {
                        key: value for key, value in filing_provenance.items() if key != "operational_relation"}
                    period = filing_provenance.get("reporting_period_end")
                    if period:
                        document.reporting_period_end = datetime.fromisoformat(period).replace(tzinfo=timezone.utc)
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
                        "discovery_method": discovery_method,
                        "scoring_applied": False,
                        "filing_exposure_provenance": filing_provenance,
                    },
                )
                scenario = build_resource_margin_scenario(
                    source,
                    as_of=context.started_at,
                )
                exposure.metadata.update(
                    {
                        "price_series_attached": scenario["price_series_attached"],
                        "resource_margin_scenario": scenario,
                        "evidence_quote": source.evidence_quote,
                        "disclosure_period": source.disclosure_period,
                        "causal_impact_assessed": scenario["status"] == "READY",
                        "prediction_input": False,
                    }
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
                    source_urls=[exposure.source_url],
                    metadata={
                        "exposure_id": exposure_id,
                        "dependency_pct": exposure.dependency_pct,
                        "stage": 3,
                        "price_series_attached": exposure.metadata[
                            "price_series_attached"
                        ],
                        "scenario_status": exposure.metadata[
                            "resource_margin_scenario"
                        ]["status"],
                        "causal_impact_assessed": exposure.metadata[
                            "causal_impact_assessed"
                        ],
                        "source_content_support_detected": support_detected,
                        "discovery_method": discovery_method,
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
        elif rejected_sources or rejected_filings:
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
                "scanned_sec_filings": scanned_sec_filings,
                "auto_discovered_exposures": auto_discovered_count,
                "exposures": len(exposures),
                "rejected_sources": rejected_sources,
                "rejected_filings": rejected_filings,
                "price_series_attached": False,
                "scoring_applied": False,
            },
            state_updates={"resource_exposures_by_ticker": by_ticker},
        )
