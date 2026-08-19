from __future__ import annotations

import hashlib
from typing import Protocol

from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.contracts import (
    AgentContext,
    AgentEvidence,
    AgentResult,
    AgentStatus,
    DocumentRecord,
    EntityRecord,
    FundamentalFact,
    utc_now,
)
from market_checker_app.collectors.sec_edgar_client import (
    SecCompanyBundle,
    SecEdgarClient,
)
from market_checker_app.config import FundamentalIngestionConfig
from market_checker_app.utils.text import normalize_ticker


class SecBundleClient(Protocol):
    def fetch_company_bundle(
        self,
        ticker: str,
        *,
        allowed_forms: tuple[str, ...],
        max_filings: int,
        concepts: tuple[str, ...],
        max_facts_per_concept: int,
    ) -> SecCompanyBundle | None: ...


def _stable_hash(*parts: object) -> str:
    encoded = "|".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _document_id(cik: str, accession_number: str) -> str:
    return f"sec:{cik}:{accession_number}"


class SecFundamentalsAgent(BaseAgent):
    """Ingest official SEC filings and XBRL facts without scoring them."""

    name = "f2_sec"
    version = "1.0"
    required = False
    dependencies = ("entity_registry",)

    def __init__(
        self,
        config: FundamentalIngestionConfig,
        client: SecBundleClient | None = None,
    ) -> None:
        self.config = config
        self._client = client

    def _client_or_none(self) -> SecBundleClient | None:
        if self._client is not None:
            return self._client
        if not self.config.user_agent.strip():
            return None
        self._client = SecEdgarClient(
            user_agent=self.config.user_agent,
            timeout_seconds=self.config.request_timeout_seconds,
            min_request_interval_seconds=(
                self.config.min_request_interval_seconds
            ),
        )
        return self._client

    def run(self, context: AgentContext) -> AgentResult:
        if not self.config.enabled:
            return AgentResult(
                status=AgentStatus.UNAVAILABLE,
                warnings=["F2-SEC je vypnutý v konfiguraci."],
            )
        client = self._client_or_none()
        if client is None:
            return AgentResult(
                status=AgentStatus.UNAVAILABLE,
                warnings=[
                    "F2-SEC nebyl spuštěn: chybí deklarovaný SEC User-Agent s kontaktem."
                ],
            )

        registry = context.state.get("entities_by_ticker")
        registered = registry if isinstance(registry, dict) else {}
        observed_at = utc_now()
        entities: list[EntityRecord] = []
        documents: list[DocumentRecord] = []
        evidence: list[AgentEvidence] = []
        facts: list[FundamentalFact] = []
        warnings: list[str] = []
        successful_tickers = 0
        unresolved_tickers = 0

        for raw_ticker in context.watchlist:
            ticker = normalize_ticker(raw_ticker)
            if not ticker:
                continue
            try:
                bundle = client.fetch_company_bundle(
                    ticker,
                    allowed_forms=self.config.forms,
                    max_filings=self.config.max_filings_per_ticker,
                    concepts=self.config.fact_concepts,
                    max_facts_per_concept=self.config.max_facts_per_concept,
                )
            except Exception as exc:
                unresolved_tickers += 1
                warnings.append(
                    f"F2-SEC {ticker}: {type(exc).__name__}: {exc}"
                )
                continue
            if bundle is None:
                unresolved_tickers += 1
                warnings.append(f"F2-SEC {ticker}: ticker nebyl nalezen v SEC CIK mapě.")
                continue

            successful_tickers += 1
            warnings.extend(bundle.warnings)
            original = registered.get(ticker)
            original_metadata = (
                dict(original.metadata)
                if isinstance(original, EntityRecord)
                else {}
            )
            original_metadata.update(
                {
                    "fundamental_ingestion_stage": 2,
                    "sec_exchange": bundle.company.exchange,
                }
            )
            entities.append(
                EntityRecord(
                    entity_id=(
                        original.entity_id
                        if isinstance(original, EntityRecord)
                        else f"ticker:{ticker}"
                    ),
                    ticker=ticker,
                    yahoo_ticker=(
                        original.yahoo_ticker
                        if isinstance(original, EntityRecord)
                        else None
                    ),
                    name=bundle.company.name,
                    exchange=bundle.company.exchange,
                    cik=bundle.company.cik,
                    aliases=(
                        list(original.aliases)
                        if isinstance(original, EntityRecord)
                        else []
                    ),
                    source="sec_edgar",
                    metadata=original_metadata,
                )
            )

            document_ids_by_accession: dict[str, str] = {}
            filing_urls_by_accession: dict[str, str] = {}
            for filing in bundle.filings:
                document_id = _document_id(
                    bundle.company.cik,
                    filing.accession_number,
                )
                document_ids_by_accession[filing.accession_number] = document_id
                filing_urls_by_accession[filing.accession_number] = filing.filing_url
                documents.append(
                    DocumentRecord(
                        document_id=document_id,
                        ticker=ticker,
                        source="SEC EDGAR",
                        source_type="regulatory_filing",
                        observed_at=observed_at,
                        url=filing.filing_url,
                        published_at=filing.filed_at,
                        mime_type="text/html",
                        metadata={
                            "cik": bundle.company.cik,
                            "accession_number": filing.accession_number,
                            "form": filing.form,
                            "report_date": (
                                filing.report_date.isoformat()
                                if filing.report_date
                                else None
                            ),
                            "primary_document": filing.primary_document,
                            "primary_document_description": (
                                filing.primary_document_description
                            ),
                            "index_url": filing.index_url,
                        },
                    )
                )
                evidence.append(
                    AgentEvidence(
                        evidence_id=_stable_hash(
                            context.orchestration_id,
                            self.name,
                            ticker,
                            filing.accession_number,
                        ),
                        ticker=ticker,
                        agent_name=self.name,
                        event_type="SEC_FILING_INGESTED",
                        observed_at=observed_at,
                        summary=(
                            f"SEC {filing.form} filed {filing.filed_at.date().isoformat()} "
                            f"for {ticker} (CIK {bundle.company.cik})."
                        ),
                        confidence=1.0,
                        reasons=["official_sec_edgar_source"],
                        document_ids=[document_id],
                        source_urls=[filing.filing_url],
                        metadata={
                            "cik": bundle.company.cik,
                            "accession_number": filing.accession_number,
                            "form": filing.form,
                            "stage": 2,
                            "scoring_applied": False,
                        },
                    )
                )

            for fact in bundle.facts:
                document_id = document_ids_by_accession.get(
                    fact.accession_number
                )
                if document_id is None:
                    warnings.append(
                        f"F2-SEC {ticker}: fakt {fact.concept} nemá stažený filing "
                        f"{fact.accession_number}."
                    )
                    continue
                fact_id = _stable_hash(
                    bundle.company.cik,
                    fact.taxonomy,
                    fact.concept,
                    fact.unit,
                    fact.accession_number,
                    fact.period_start.isoformat() if fact.period_start else "",
                    fact.period_end.isoformat() if fact.period_end else "",
                    repr(fact.value),
                )
                facts.append(
                    FundamentalFact(
                        fact_id=fact_id,
                        ticker=ticker,
                        cik=bundle.company.cik,
                        taxonomy=fact.taxonomy,
                        concept=fact.concept,
                        label=fact.label,
                        description=fact.description,
                        unit=fact.unit,
                        value=fact.value,
                        observed_at=observed_at,
                        filed_at=fact.filed_at,
                        form=fact.form,
                        accession_number=fact.accession_number,
                        source_url=(
                            filing_urls_by_accession.get(fact.accession_number)
                            or fact.source_url
                        ),
                        document_id=document_id,
                        period_start=fact.period_start,
                        period_end=fact.period_end,
                        fiscal_year=fact.fiscal_year,
                        fiscal_period=fact.fiscal_period,
                        frame=fact.frame,
                        metadata={
                            "stage": 2,
                            "scoring_applied": False,
                        },
                    )
                )

        if successful_tickers == 0:
            status = AgentStatus.UNAVAILABLE
        elif unresolved_tickers or warnings:
            status = AgentStatus.PARTIAL
        else:
            status = AgentStatus.SUCCESS

        facts_by_ticker: dict[str, list[FundamentalFact]] = {}
        for fact in facts:
            facts_by_ticker.setdefault(fact.ticker, []).append(fact)
        return AgentResult(
            status=status,
            entities=entities,
            documents=documents,
            fundamental_facts=facts,
            evidence=evidence,
            warnings=list(dict.fromkeys(warnings)),
            metadata={
                "successful_tickers": successful_tickers,
                "unresolved_tickers": unresolved_tickers,
                "documents": len(documents),
                "fundamental_facts": len(facts),
                "forms": list(self.config.forms),
                "scoring_applied": False,
            },
            state_updates={
                "sec_entities_by_ticker": {
                    entity.ticker: entity for entity in entities
                },
                "fundamental_facts_by_ticker": facts_by_ticker,
            },
        )
