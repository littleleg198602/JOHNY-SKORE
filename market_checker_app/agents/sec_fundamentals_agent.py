from __future__ import annotations

from dataclasses import replace
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
    SEC_TICKER_MAP_URL,
    SecCompanyBundle,
    SecEdgarClient,
)
from market_checker_app.collectors.short_report_client import (
    FetchedShortReport,
    ShortReportClient,
)
from market_checker_app.config import (
    FundamentalIngestionConfig,
    ShortReportSourceConfig,
)
from market_checker_app.utils.text import normalize_ticker


class SecBundleClient(Protocol):
    def fetch_company_bundle(
        self,
        ticker: str,
        *,
        allowed_forms: tuple[str, ...],
        max_filings: int,
        max_historical_submission_files: int,
        concepts: tuple[str, ...],
        max_facts_per_concept: int,
    ) -> SecCompanyBundle | None: ...


def _stable_hash(*parts: object) -> str:
    encoded = "|".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _document_id(cik: str, accession_number: str, ticker: str) -> str:
    """Identify an issuer filing observation for one tradable instrument.

    One SEC issuer can have several tickers/share classes (for example GOOG
    and GOOGL).  The database document contract is instrument-scoped, so CIK
    and accession alone are not unique inside a multi-ticker pipeline run.
    """

    return f"sec:{cik}:{accession_number}:ticker:{normalize_ticker(ticker)}"


class SecFundamentalsAgent(BaseAgent):
    """Ingest official SEC filings and XBRL facts without scoring them."""

    name = "f2_sec"
    version = "1.1"
    required = False
    dependencies = ("entity_registry",)

    def __init__(
        self,
        config: FundamentalIngestionConfig,
        client: SecBundleClient | None = None,
        *,
        filing_text_client: ShortReportClient | None = None,
    ) -> None:
        self.config = config
        self._client = client
        self._filing_text_client = filing_text_client

    def _client_or_none(self) -> SecBundleClient | None:
        if self._client is not None:
            return self._client
        if (
            not self.config.user_agent.strip()
            or "@" not in self.config.user_agent
        ):
            return None
        self._client = SecEdgarClient(
            user_agent=self.config.user_agent,
            timeout_seconds=self.config.request_timeout_seconds,
            min_request_interval_seconds=(
                self.config.min_request_interval_seconds
            ),
        )
        return self._client

    def _filing_text_client_or_none(self) -> ShortReportClient | None:
        if not self.config.extract_latest_10k_text:
            return None
        if self._filing_text_client is None:
            self._filing_text_client = ShortReportClient(
                user_agent=self.config.user_agent,
                timeout_seconds=self.config.request_timeout_seconds,
                max_download_bytes=self.config.max_filing_download_bytes,
                max_text_characters=self.config.max_filing_text_characters,
            )
        return self._filing_text_client

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
        core_degraded = False
        successful_tickers = 0
        unresolved_tickers = 0
        filing_texts_by_ticker: dict[str, list[FetchedShortReport]] = {}
        insider_transactions_by_ticker: dict[str, list[object]] = {}
        filing_text_failures = 0
        filing_text_failure_details: list[dict[str, str]] = []
        filing_text_client = self._filing_text_client_or_none()

        for raw_ticker in context.watchlist:
            ticker = normalize_ticker(raw_ticker)
            if not ticker:
                continue
            try:
                bundle = client.fetch_company_bundle(
                    ticker,
                    allowed_forms=self.config.forms,
                    max_filings=self.config.max_filings_per_ticker,
                    max_historical_submission_files=(
                        self.config.max_historical_submission_files
                    ),
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
            core_degraded = core_degraded or bool(bundle.warnings)
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
            base_entity = (
                original
                if isinstance(original, EntityRecord)
                else EntityRecord(
                    entity_id=f"ticker:{ticker}",
                    ticker=ticker,
                    instrument_id=f"ticker:{ticker}",
                )
            )
            legal_entity_id = (
                base_entity.legal_entity_id or f"cik:{bundle.company.cik}"
            )
            entities.append(
                replace(
                    base_entity,
                    ticker=ticker,
                    name=bundle.company.name,
                    exchange=bundle.company.exchange,
                    cik=bundle.company.cik,
                    legal_entity_id=legal_entity_id,
                    issuer_id=base_entity.issuer_id or legal_entity_id,
                    instrument_id=(
                        base_entity.instrument_id or f"ticker:{ticker}"
                    ),
                    source="sec_edgar",
                    source_url=SEC_TICKER_MAP_URL,
                    confidence=max(base_entity.confidence, 1.0),
                    metadata=original_metadata,
                )
            )

            document_ids_by_accession: dict[str, str] = {}
            filing_urls_by_accession: dict[str, str] = {}
            document_records_by_accession: dict[str, DocumentRecord] = {}
            for filing in bundle.filings:
                document_id = _document_id(
                    bundle.company.cik,
                    filing.accession_number,
                    ticker,
                )
                document_ids_by_accession[filing.accession_number] = document_id
                filing_urls_by_accession[filing.accession_number] = filing.filing_url
                document = DocumentRecord(
                    document_id=document_id,
                    ticker=ticker,
                    source="SEC EDGAR",
                    source_type="regulatory_filing",
                    observed_at=observed_at,
                    url=filing.filing_url,
                    published_at=filing.filed_at,
                    mime_type="text/html",
                    source_authority="SEC",
                    legal_entity_id=legal_entity_id,
                    issuer_id=base_entity.issuer_id or legal_entity_id,
                    instrument_id=base_entity.instrument_id,
                    reporting_period_end=filing.report_date,
                    is_audited=(
                        filing.form.upper().removesuffix("/A")
                        in {"10-K", "20-F", "40-F"}
                    ),
                    language="en",
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
                        "items": list(filing.items),
                        "historical_submission_files_loaded": (
                            bundle.historical_submission_files_loaded
                        ),
                    },
                )
                documents.append(document)
                document_records_by_accession[filing.accession_number] = document
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

            if filing_text_client is not None:
                recent_annual_filings = [
                    filing
                    for filing in bundle.filings
                    if filing.form.upper().removesuffix("/A")
                    in {"10-K", "20-F", "40-F"}
                    and filing.filing_url
                ][: max(0, int(self.config.max_text_filings_per_ticker))]
                for filing in recent_annual_filings:
                    try:
                        fetched = filing_text_client.fetch(
                            ShortReportSourceConfig(
                                ticker=ticker,
                                publisher="SEC EDGAR",
                                published_at=filing.filed_at,
                                url=filing.filing_url,
                                discovery_method="sec_filing",
                            )
                        )
                    except Exception as exc:
                        filing_text_failures += 1
                        filing_text_failure_details.append(
                            {
                                "ticker": ticker,
                                "form": filing.form,
                                "accession_number": filing.accession_number,
                                "filing_url": filing.filing_url,
                                "filed_at": filing.filed_at.isoformat(),
                                "error_type": type(exc).__name__,
                                "error": str(exc),
                            }
                        )
                        warnings.append(
                            f"F2-SEC {ticker}: text 10-K nelze bezpečně načíst "
                            f"({filing.accession_number}): "
                            f"{type(exc).__name__}: {exc}"
                        )
                        continue
                    filing_texts_by_ticker.setdefault(ticker, []).append(fetched)
                    document = document_records_by_accession.get(
                        filing.accession_number
                    )
                    if document is not None:
                        document.url = fetched.final_url
                        document.content_hash = fetched.content_hash
                        document.mime_type = fetched.mime_type
                        document.metadata.update(
                            {
                                "content_fetched": True,
                                "download_size_bytes": fetched.size_bytes,
                                "extractor": fetched.extractor,
                                "raw_content_persisted": False,
                            }
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
                    ticker,
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

            if bundle.insider_transactions:
                insider_transactions_by_ticker.setdefault(ticker, []).extend(
                    bundle.insider_transactions
                )

        if successful_tickers == 0:
            status = AgentStatus.UNAVAILABLE
        elif unresolved_tickers or core_degraded or filing_text_failures:
            status = AgentStatus.PARTIAL
        else:
            status = AgentStatus.SUCCESS

        facts_by_ticker: dict[str, list[FundamentalFact]] = {}
        for fact in facts:
            facts_by_ticker.setdefault(fact.ticker, []).append(fact)
        updated_registry = dict(registered)
        updated_registry.update({entity.ticker: entity for entity in entities})
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
                "filing_text_documents": sum(
                    len(items) for items in filing_texts_by_ticker.values()
                ),
                "filing_text_failures": filing_text_failures,
                "filing_text_failure_details": filing_text_failure_details,
                "insider_transactions": sum(
                    len(items)
                    for items in insider_transactions_by_ticker.values()
                ),
                "raw_filing_text_persisted": False,
                "forms": list(self.config.forms),
                "scoring_applied": False,
            },
            state_updates={
                "entities_by_ticker": updated_registry,
                "sec_entities_by_ticker": {
                    entity.ticker: entity for entity in entities
                },
                "fundamental_facts_by_ticker": facts_by_ticker,
                "sec_filing_texts_by_ticker": filing_texts_by_ticker,
                "sec_insider_transactions_by_ticker": (
                    insider_transactions_by_ticker
                ),
            },
        )
