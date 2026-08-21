from __future__ import annotations

from collections.abc import Mapping
from datetime import timezone
import hashlib
from typing import Protocol

from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.contracts import (
    AgentContext,
    AgentEvidence,
    AgentResult,
    AgentStatus,
    DocumentRecord,
    DocumentSourcePriority,
    EntityRecord,
    IdentityConflictRecord,
    utc_now,
)
from market_checker_app.agents.source_policy import resolve_document_conflicts
from market_checker_app.collectors.european_filing_client import (
    EuropeanFilingClient,
    FetchedEuropeanFiling,
    normalized_authority,
)
from market_checker_app.config import EuropeanFilingConfig, EuropeanFilingSourceConfig
from market_checker_app.utils.entity_identifiers import normalize_isin, normalize_lei
from market_checker_app.utils.text import normalize_ticker


class EuropeanDocumentClient(Protocol):
    def validate_source(self, source: EuropeanFilingSourceConfig) -> str: ...

    def fetch(self, source: EuropeanFilingSourceConfig) -> FetchedEuropeanFiling: ...


def _stable_id(*parts: object) -> str:
    return hashlib.sha256(
        "|".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()


def _source_policy(source: EuropeanFilingSourceConfig) -> tuple[str, int]:
    authority = normalized_authority(source.authority)
    document_type = str(source.document_type or "").strip().lower()
    if authority in {"FCA_NSM", "AFM", "BAFIN", "CNB"}:
        return "regulatory_filing", int(DocumentSourcePriority.REGULATORY_FILING)
    if authority == "ISSUER_IR":
        if "presentation" in document_type:
            return (
                "management_presentation",
                int(DocumentSourcePriority.MANAGEMENT_PRESENTATION),
            )
        return "investor_relations", int(DocumentSourcePriority.INVESTOR_RELATIONS)
    if "presentation" in document_type:
        return (
            "management_presentation",
            int(DocumentSourcePriority.MANAGEMENT_PRESENTATION),
        )
    if source.audited:
        return (
            "audited_financial_statement",
            int(DocumentSourcePriority.AUDITED_FINANCIAL_STATEMENT),
        )
    return "exchange_announcement", int(DocumentSourcePriority.EXCHANGE_ANNOUNCEMENT)


class EuropeanFilingsAgent(BaseAgent):
    """Normalize European official documents into the same contract as SEC."""

    name = "european_filings"
    version = "1.0"
    required = False
    dependencies = ("entity_registry",)

    def __init__(
        self,
        config: EuropeanFilingConfig,
        *,
        client: EuropeanDocumentClient | None = None,
    ) -> None:
        self.config = config
        self._client = client or EuropeanFilingClient(config)

    @staticmethod
    def _identity_conflict(
        *,
        entity: EntityRecord,
        source: EuropeanFilingSourceConfig,
        field_name: str,
        existing_value: str,
        candidate_value: str,
    ) -> IdentityConflictRecord:
        observed_at = utc_now()
        return IdentityConflictRecord(
            conflict_id=_stable_id(
                entity.entity_id,
                source.url,
                field_name,
                existing_value,
                candidate_value,
            ),
            ticker=entity.ticker,
            entity_id=entity.entity_id,
            legal_entity_id=entity.legal_entity_id,
            field_name=field_name,
            existing_value=existing_value,
            candidate_value=candidate_value,
            existing_source=entity.source,
            candidate_source=normalized_authority(source.authority),
            observed_at=observed_at,
            existing_source_url=entity.source_url,
            candidate_source_url=source.url,
            reason="Evropský dokument odkazuje na jinou přesnou identitu emitenta.",
            metadata={"document_rejected": True},
        )

    def run(self, context: AgentContext) -> AgentResult:
        if not self.config.enabled:
            return AgentResult(
                status=AgentStatus.UNAVAILABLE,
                warnings=["EuropeanFilingsAgent je vypnutý v konfiguraci."],
            )
        if not self.config.sources:
            return AgentResult(
                status=AgentStatus.UNAVAILABLE,
                warnings=["EuropeanFilingsAgent nemá nakonfigurované zdroje."],
            )
        registry = context.state.get("entities_by_ticker")
        registry = registry if isinstance(registry, Mapping) else {}
        watchlist = {
            normalize_ticker(value)
            for value in context.watchlist
            if normalize_ticker(value)
        }
        observed_at = utc_now()
        documents: list[DocumentRecord] = []
        evidence: list[AgentEvidence] = []
        conflicts: list[IdentityConflictRecord] = []
        warnings: list[str] = []
        text_by_ticker: dict[str, list[FetchedEuropeanFiling]] = {}
        rejected = 0

        for source in self.config.sources:
            ticker = normalize_ticker(source.ticker)
            if ticker not in watchlist:
                continue
            entity = registry.get(ticker)
            if not isinstance(entity, EntityRecord):
                rejected += 1
                warnings.append(f"Europe {ticker}: ticker nemá registry entitu.")
                continue
            try:
                lei = normalize_lei(source.lei)
                isin = normalize_isin(source.isin)
            except ValueError as exc:
                rejected += 1
                warnings.append(f"Europe {ticker}: {exc}.")
                continue
            if self.config.require_exact_identity and not (lei or isin):
                rejected += 1
                warnings.append(
                    f"Europe {ticker}: dokument nemá přesný LEI ani ISIN."
                )
                continue
            mismatches: list[tuple[str, str, str]] = []
            if lei and entity.lei and lei != entity.lei:
                mismatches.append(("lei", entity.lei, lei))
            if isin and entity.isin and isin != entity.isin:
                mismatches.append(("isin", entity.isin, isin))
            if mismatches:
                rejected += 1
                conflicts.extend(
                    self._identity_conflict(
                        entity=entity,
                        source=source,
                        field_name=field_name,
                        existing_value=existing,
                        candidate_value=candidate,
                    )
                    for field_name, existing, candidate in mismatches
                )
                continue
            exact_match = bool(
                (lei and entity.lei == lei) or (isin and entity.isin == isin)
            )
            if self.config.require_exact_identity and not exact_match:
                rejected += 1
                warnings.append(
                    f"Europe {ticker}: zdrojovou identitu nelze spojit s registry bez hádání."
                )
                continue
            published_at = source.published_at
            if published_at.tzinfo is None or published_at.utcoffset() is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
            else:
                published_at = published_at.astimezone(timezone.utc)
            if published_at > context.started_at:
                rejected += 1
                warnings.append(f"Europe {ticker}: dokument má budoucí datum.")
                continue
            try:
                validated_url = self._client.validate_source(source)
                fetched = self._client.fetch(source) if self.config.fetch_content else None
            except Exception as exc:
                rejected += 1
                warnings.append(
                    f"Europe {ticker}: {type(exc).__name__}: {exc}"
                )
                continue
            final_url = fetched.final_url if fetched else validated_url
            source_type, source_priority = _source_policy(source)
            canonical_key = (
                str(source.canonical_event_key or "").strip()
                or "|".join(
                    (
                        ticker,
                        str(source.document_type).strip().lower(),
                        (
                            source.reporting_period_end.date().isoformat()
                            if source.reporting_period_end
                            else published_at.date().isoformat()
                        ),
                    )
                )
            )
            document_id = "eu:" + _stable_id(
                normalized_authority(source.authority),
                ticker,
                published_at.isoformat(),
                validated_url,
            )
            document = DocumentRecord(
                document_id=document_id,
                ticker=ticker,
                source=normalized_authority(source.authority),
                source_type=source_type,
                source_priority=source_priority,
                source_authority=normalized_authority(source.authority),
                observed_at=observed_at,
                url=final_url,
                published_at=published_at,
                content_hash=fetched.content_hash if fetched else None,
                mime_type=(
                    fetched.mime_type
                    if fetched
                    else ("application/xhtml+xml" if source.esef else None)
                ),
                legal_entity_id=entity.legal_entity_id,
                issuer_id=entity.issuer_id,
                instrument_id=entity.instrument_id,
                reporting_period_end=source.reporting_period_end,
                is_audited=source.audited,
                language=source.language,
                metadata={
                    "authority": normalized_authority(source.authority),
                    "document_type": source.document_type,
                    "title": source.title,
                    "issuer_name": source.issuer_name,
                    "lei": lei,
                    "isin": isin,
                    "esef": source.esef,
                    "exact_identity_match": exact_match,
                    "canonical_event_key": canonical_key,
                    "content_fetched": fetched is not None,
                    "download_size_bytes": fetched.size_bytes if fetched else 0,
                    "extractor": fetched.extractor if fetched else "",
                    "raw_content_persisted": False,
                    "scoring_applied": False,
                },
            )
            documents.append(document)
            if fetched:
                text_by_ticker.setdefault(ticker, []).append(fetched)
            evidence.append(
                AgentEvidence(
                    evidence_id=_stable_id(
                        context.orchestration_id,
                        self.name,
                        document_id,
                    ),
                    ticker=ticker,
                    agent_name=self.name,
                    event_type="EUROPEAN_FILING_INGESTED",
                    observed_at=observed_at,
                    summary=(
                        f"{normalized_authority(source.authority)}: {source.title}"
                    ),
                    direction=0.0,
                    risk_score=0.0,
                    confidence=1.0,
                    hard_veto=False,
                    reasons=["exact_entity_identity", "source_priority_applied"],
                    document_ids=[document_id],
                    source_urls=[final_url],
                    metadata={
                        "source_priority": source_priority,
                        "scoring_applied": False,
                    },
                )
            )

        retained, resolutions = resolve_document_conflicts(documents)
        preferred = {
            item.conflict_key: item.preferred_document_id for item in resolutions
        }
        if not retained:
            status = AgentStatus.UNAVAILABLE
        elif rejected or conflicts:
            status = AgentStatus.PARTIAL
        else:
            status = AgentStatus.SUCCESS
        return AgentResult(
            status=status,
            identity_conflicts=conflicts,
            documents=retained,
            evidence=evidence,
            warnings=list(dict.fromkeys(warnings)),
            metadata={
                "configured_sources": len(self.config.sources),
                "documents": len(retained),
                "rejected_sources": rejected,
                "identity_conflicts": len(conflicts),
                "source_conflicts_resolved": len(resolutions),
                "scoring_applied": False,
            },
            state_updates={
                "european_filing_documents_by_ticker": {
                    ticker: [item for item in retained if item.ticker == ticker]
                    for ticker in sorted({item.ticker for item in retained})
                },
                "european_filing_texts_by_ticker": text_by_ticker,
                "preferred_documents_by_canonical_event_key": preferred,
            },
        )
