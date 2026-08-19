from __future__ import annotations

from hashlib import sha256
import re
from typing import Protocol

from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.contracts import (
    AgentContext,
    AgentEvidence,
    AgentResult,
    AgentStatus,
    ClaimStatus,
    DocumentRecord,
    ResearchClaim,
    utc_now,
)
from market_checker_app.collectors.short_report_client import (
    FetchedShortReport,
    ShortReportClient,
)
from market_checker_app.config import ShortReportConfig, ShortReportSourceConfig
from market_checker_app.utils.text import normalize_ticker


class ShortReportFetcher(Protocol):
    def fetch(self, source: ShortReportSourceConfig) -> FetchedShortReport: ...


ALLEGATION_MARKERS = (
    "accounting irregular",
    "deteriorat",
    "excessive",
    "failed to disclose",
    "fraud",
    "inflated",
    "manipulat",
    "misleading",
    "misrepresented",
    "negative",
    "not supported",
    "overstat",
    "questionable",
    "recast",
    "restat",
    "understat",
    "undisclosed",
    "unsustainable",
    "unsupported",
    "weak",
)

DOMAIN_KEYWORDS = {
    "restatement": ("restat", "recast", "accounting irregular"),
    "cash_conversion": (
        "cash flow",
        "free cash",
        "operating cash",
        "cash conversion",
        "cash generation",
    ),
    "liquidity": ("liquidity", "current ratio", "working capital deficit"),
    "leverage": ("debt", "leverage", "liabilities", "borrowings"),
    "working_capital": ("receivable", "inventory", "working capital"),
    "revenue_quality": (
        "revenue",
        "sales",
        "earnings",
        "profit",
        "margin",
    ),
    "governance": (
        "auditor",
        "chief executive",
        "chief financial",
        " ceo ",
        " cfo ",
        "director",
        "related party",
        "related-party",
        "management",
    ),
}

EXCLUDED_SENTENCE_MARKERS = (
    "not investment advice",
    "terms of use",
    "forward-looking statement",
    "no representation or warranty",
    "legal disclaimer",
)


def _stable_id(*parts: object) -> str:
    return sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def _claim_type(statement: str) -> str:
    normalized = f" {statement.lower()} "
    for claim_type, keywords in DOMAIN_KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            return claim_type
    return "generic"


def _extract_claim_statements(
    text: str,
    *,
    minimum_characters: int,
    limit: int,
) -> list[tuple[str, str]]:
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", str(text or ""))
    extracted: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_sentence in sentences:
        statement = re.sub(r"\s+", " ", raw_sentence).strip(" \t\r\n-•")
        if len(statement) < minimum_characters:
            continue
        statement = statement[:1_500]
        normalized = statement.lower()
        if any(marker in normalized for marker in EXCLUDED_SENTENCE_MARKERS):
            continue
        if not any(marker in normalized for marker in ALLEGATION_MARKERS):
            continue
        dedupe_key = re.sub(r"\W+", " ", normalized).strip()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        extracted.append((_claim_type(statement), statement))
        if len(extracted) >= limit:
            break
    return extracted


class ShortReportAgent(BaseAgent):
    """Normalize explicitly configured short reports without judging truth."""

    name = "short_report"
    version = "1.0"
    required = False
    dependencies = ("entity_registry",)

    def __init__(
        self,
        config: ShortReportConfig,
        client: ShortReportFetcher | None = None,
    ) -> None:
        self.config = config
        self._client = client

    def _client_or_default(self) -> ShortReportFetcher:
        if self._client is None:
            self._client = ShortReportClient(
                user_agent=self.config.user_agent,
                timeout_seconds=self.config.request_timeout_seconds,
                max_download_bytes=self.config.max_download_bytes,
                max_text_characters=self.config.max_text_characters,
            )
        return self._client

    def run(self, context: AgentContext) -> AgentResult:
        if not self.config.enabled:
            return AgentResult(
                status=AgentStatus.UNAVAILABLE,
                warnings=["ShortReportAgent je vypnutý v konfiguraci."],
            )
        if not self.config.sources:
            return AgentResult(
                status=AgentStatus.UNAVAILABLE,
                warnings=["ShortReportAgent nemá nakonfigurované žádné reporty."],
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
                status=AgentStatus.SUCCESS,
                metadata={
                    "configured_sources": len(self.config.sources),
                    "matching_sources": 0,
                    "scoring_applied": False,
                },
                state_updates={"short_report_claims_by_ticker": {}},
            )

        client = self._client_or_default()
        observed_at = utc_now()
        documents: list[DocumentRecord] = []
        evidence: list[AgentEvidence] = []
        claims: list[ResearchClaim] = []
        warnings: list[str] = []
        failed_reports = 0
        reports_without_claims = 0
        seen_sources: set[tuple[str, str, str]] = set()

        for source in matching_sources:
            ticker = normalize_ticker(source.ticker)
            source_key = (
                ticker,
                source.url.strip(),
                source.published_at.isoformat(),
            )
            if source_key in seen_sources:
                continue
            seen_sources.add(source_key)
            if source.published_at.tzinfo is None or source.published_at.utcoffset() is None:
                failed_reports += 1
                warnings.append(
                    f"ShortReportAgent {ticker}: datum reportu nemá časové pásmo."
                )
                continue
            if source.published_at > context.started_at:
                failed_reports += 1
                warnings.append(
                    f"ShortReportAgent {ticker}: report má budoucí datum publikace."
                )
                continue
            try:
                fetched = client.fetch(source)
            except Exception as exc:
                failed_reports += 1
                warnings.append(
                    f"ShortReportAgent {ticker}: {type(exc).__name__}: {exc}"
                )
                continue

            document_id = "short:" + _stable_id(
                ticker,
                source.publisher.strip().lower(),
                source.published_at.isoformat(),
                fetched.final_url,
                fetched.content_hash,
            )
            document = DocumentRecord(
                document_id=document_id,
                ticker=ticker,
                source=source.publisher.strip() or "Unknown short-report publisher",
                source_type="short_report",
                observed_at=observed_at,
                url=fetched.final_url,
                published_at=source.published_at,
                content_hash=fetched.content_hash,
                mime_type=fetched.mime_type,
                metadata={
                    "title": fetched.title,
                    "publisher": source.publisher.strip(),
                    "size_bytes": fetched.size_bytes,
                    "text_characters": len(fetched.text),
                    "extractor": fetched.extractor,
                    "explicitly_configured_source": True,
                    "truth_assessed": False,
                    "scoring_applied": False,
                },
            )
            documents.append(document)
            extracted = _extract_claim_statements(
                fetched.text,
                minimum_characters=max(1, self.config.minimum_claim_characters),
                limit=max(1, self.config.max_claims_per_report),
            )
            if not extracted:
                reports_without_claims += 1
                warnings.append(
                    f"ShortReportAgent {ticker}: report byl uložen, ale nebylo bezpečně extrahováno žádné tvrzení."
                )

            report_claims: list[ResearchClaim] = []
            for claim_type, statement in extracted:
                claim = ResearchClaim(
                    claim_id="claim:" + _stable_id(
                        document_id,
                        claim_type,
                        statement.lower(),
                    ),
                    ticker=ticker,
                    report_document_id=document_id,
                    claim_type=claim_type,
                    statement=statement,
                    status=ClaimStatus.UNVERIFIED,
                    observed_at=observed_at,
                    published_at=source.published_at,
                    confidence=0.0,
                    source_agent_name=self.name,
                    verification_summary=(
                        "Tvrzení bylo extrahováno ze short reportu a zatím nebylo ověřeno."
                    ),
                    evidence_document_ids=[document_id],
                    source_urls=[fetched.final_url],
                    metadata={
                        "publisher": source.publisher.strip(),
                        "report_title": fetched.title,
                        "allegation_direction": "risk",
                        "extraction_method": "deterministic_keyword_sentence_v1",
                        "truth_assessed": False,
                        "scoring_applied": False,
                    },
                )
                report_claims.append(claim)
                claims.append(claim)

            evidence.append(
                AgentEvidence(
                    evidence_id=_stable_id(
                        context.orchestration_id,
                        self.name,
                        document_id,
                    ),
                    ticker=ticker,
                    agent_name=self.name,
                    event_type="SHORT_REPORT_INGESTED",
                    observed_at=observed_at,
                    summary=(
                        f"Short report od {source.publisher.strip()} byl uložen; "
                        f"extrahováno {len(report_claims)} neověřených tvrzení."
                    ),
                    direction=0.0,
                    risk_score=0.0,
                    confidence=1.0,
                    hard_veto=False,
                    reasons=["unverified_external_allegations"],
                    document_ids=[document_id],
                    source_urls=[fetched.final_url],
                    metadata={
                        "claims_extracted": len(report_claims),
                        "claim_truth_confidence": 0.0,
                        "report_capture_confidence": 1.0,
                        "fraud_conclusion": False,
                        "scoring_applied": False,
                        "shadow_mode": context.shadow_mode,
                    },
                )
            )

        claims_by_ticker: dict[str, list[ResearchClaim]] = {}
        for claim in claims:
            claims_by_ticker.setdefault(claim.ticker, []).append(claim)
        if not documents:
            status = AgentStatus.UNAVAILABLE
        elif failed_reports or reports_without_claims:
            status = AgentStatus.PARTIAL
        else:
            status = AgentStatus.SUCCESS
        return AgentResult(
            status=status,
            documents=documents,
            claims=claims,
            evidence=evidence,
            warnings=list(dict.fromkeys(warnings)),
            metadata={
                "configured_sources": len(self.config.sources),
                "matching_sources": len(matching_sources),
                "documents": len(documents),
                "claims": len(claims),
                "failed_reports": failed_reports,
                "reports_without_claims": reports_without_claims,
                "fraud_conclusion": False,
                "scoring_applied": False,
            },
            state_updates={"short_report_claims_by_ticker": claims_by_ticker},
        )
