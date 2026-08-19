from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from hashlib import sha256
import math
from typing import Any

from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.contracts import (
    AgentContext,
    AgentEvidence,
    AgentResult,
    AgentStatus,
    ClaimStatus,
    FundamentalFact,
    ResearchClaim,
    utc_now,
)
from market_checker_app.config import ClaimVerificationConfig
from market_checker_app.utils.text import normalize_ticker


RISK_FINDINGS = {
    "cash_conversion": {
        "positive_income_negative_operating_cash_flow",
        "low_cash_conversion",
        "positive_income_negative_free_cash_flow",
    },
    "liquidity": {"critical_current_ratio", "low_current_ratio"},
    "leverage": {
        "liabilities_exceed_assets",
        "high_liabilities_to_assets",
        "critical_debt_to_assets",
        "high_debt_to_assets",
    },
    "working_capital": {
        "receivables_growth_outpaces_revenue",
        "inventory_growth_outpaces_revenue",
    },
    "restatement": {"potential_restatement_or_recast"},
}

RELEVANT_CONCEPTS = {
    "cash_conversion": {
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "Revenues",
        "NetIncomeLoss",
        "ProfitLoss",
        "NetCashProvidedByUsedInOperatingActivities",
        "CashFlowsFromUsedInOperatingActivities",
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PurchaseOfPropertyPlantAndEquipment",
    },
    "liquidity": {"AssetsCurrent", "LiabilitiesCurrent"},
    "leverage": {
        "Assets",
        "Liabilities",
        "LongTermDebtCurrent",
        "LongTermDebtNoncurrent",
        "LongTermDebt",
        "ShortTermBorrowings",
    },
    "working_capital": {
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "Revenues",
        "AccountsReceivableNetCurrent",
        "InventoryNet",
    },
    "restatement": set(),
}


def _stable_id(*parts: object) -> str:
    return sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def _finite_metric(metrics: dict[str, Any], key: str) -> float | None:
    try:
        value = float(metrics.get(key))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


class ClaimVerificationAgent(BaseAgent):
    """Compare narrow report allegations with point-in-time SEC diagnostics."""

    name = "claim_verification"
    version = "1.0"
    required = False
    dependencies = ("short_report", "financial_forensics")

    def __init__(self, config: ClaimVerificationConfig | None = None) -> None:
        self.config = config or ClaimVerificationConfig()

    def _evaluate(
        self,
        claim: ResearchClaim,
        forensic_summary: dict[str, Any] | None,
    ) -> tuple[ClaimStatus, str, str, float]:
        if not isinstance(forensic_summary, dict):
            return (
                ClaimStatus.INSUFFICIENT_DATA,
                "Chybí finanční forenzní souhrn pro nezávislé ověření tvrzení.",
                "missing_forensic_summary",
                0.0,
            )
        raw_findings = forensic_summary.get("findings", [])
        finding_codes = {
            str(item.get("code"))
            for item in raw_findings
            if isinstance(item, dict) and item.get("code")
        }
        metrics = (
            forensic_summary.get("metrics", {})
            if isinstance(forensic_summary.get("metrics"), dict)
            else {}
        )
        try:
            raw_confidence = float(forensic_summary.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            raw_confidence = 0.0
        base_confidence = (
            max(0.0, min(1.0, raw_confidence))
            if math.isfinite(raw_confidence)
            else 0.0
        )
        if base_confidence < self.config.minimum_forensic_confidence:
            return (
                ClaimStatus.INSUFFICIENT_DATA,
                "Pokrytí finanční diagnostiky je příliš nízké pro spolehlivé ověření tvrzení.",
                "low_forensic_confidence",
                0.0,
            )
        expected_findings = RISK_FINDINGS.get(claim.claim_type, set())
        matched = sorted(expected_findings.intersection(finding_codes))
        if matched:
            return (
                ClaimStatus.CORROBORATED,
                "Dostupná SEC diagnostika je s úzkým tvrzením konzistentní; nejde o důkaz celého short reportu.",
                "sec_diagnostic_consistent:" + ",".join(matched),
                base_confidence * 0.85,
            )

        if claim.claim_type == "cash_conversion":
            conversion = _finite_metric(metrics, "cash_conversion_ratio")
            free_cash_flow = _finite_metric(metrics, "free_cash_flow_proxy")
            if (
                conversion is not None
                and free_cash_flow is not None
                and conversion >= self.config.healthy_cash_conversion_ratio
                and free_cash_flow >= 0.0
            ):
                return (
                    ClaimStatus.CONTRADICTED,
                    "Dostupné SEC metriky ukazují zdravou konverzi zisku do cash flow a úzké tvrzení nepodporují.",
                    "healthy_cash_conversion",
                    base_confidence * 0.80,
                )
        elif claim.claim_type == "liquidity":
            current_ratio = _finite_metric(metrics, "current_ratio")
            if (
                current_ratio is not None
                and current_ratio >= self.config.healthy_current_ratio
            ):
                return (
                    ClaimStatus.CONTRADICTED,
                    "Dostupný current ratio je nad konzervativní zdravou hranicí a úzké tvrzení o slabé likviditě nepodporuje.",
                    "healthy_current_ratio",
                    base_confidence * 0.80,
                )
        elif claim.claim_type == "leverage":
            debt_ratio = _finite_metric(metrics, "debt_to_assets_ratio")
            liabilities_ratio = _finite_metric(metrics, "liabilities_to_assets_ratio")
            if (
                debt_ratio is not None
                and liabilities_ratio is not None
                and debt_ratio <= self.config.healthy_debt_to_assets_ratio
                and liabilities_ratio <= self.config.healthy_liabilities_to_assets_ratio
            ):
                return (
                    ClaimStatus.CONTRADICTED,
                    "Dostupné poměry dluhu a závazků jsou pod konzervativními hranicemi a úzké tvrzení o nadměrném zadlužení nepodporují.",
                    "healthy_leverage_ratios",
                    base_confidence * 0.80,
                )
        elif claim.claim_type == "working_capital":
            revenue_growth = _finite_metric(metrics, "revenue_growth_pct")
            item_growth = [
                value
                for value in (
                    _finite_metric(metrics, "receivables_growth_pct"),
                    _finite_metric(metrics, "inventory_growth_pct"),
                )
                if value is not None
            ]
            if revenue_growth is not None and item_growth:
                divergence = max(value - revenue_growth for value in item_growth)
                if divergence <= self.config.max_healthy_working_capital_divergence_pct:
                    return (
                        ClaimStatus.CONTRADICTED,
                        "Růst pohledávek a zásob podle dostupných SEC dat výrazně nepřevyšuje růst tržeb.",
                        "healthy_working_capital_growth",
                        base_confidence * 0.75,
                    )

        return (
            ClaimStatus.INSUFFICIENT_DATA,
            "Dostupná strukturovaná SEC data nestačí k potvrzení ani vyvrácení tohoto tvrzení.",
            "unsupported_or_incomplete_claim_type",
            base_confidence * 0.25,
        )

    @staticmethod
    def _relevant_facts(
        claim: ResearchClaim,
        facts: list[FundamentalFact],
        as_of: datetime,
    ) -> list[FundamentalFact]:
        available = [
            fact
            for fact in facts
            if fact.filed_at.tzinfo is not None
            and fact.filed_at.utcoffset() is not None
            and fact.filed_at <= as_of
            and fact.ticker == claim.ticker
        ]
        concepts = RELEVANT_CONCEPTS.get(claim.claim_type)
        if concepts is None:
            return []
        if not concepts:
            return available
        return [fact for fact in available if fact.concept in concepts]

    def run(self, context: AgentContext) -> AgentResult:
        if not self.config.enabled:
            return AgentResult(
                status=AgentStatus.UNAVAILABLE,
                warnings=["ClaimVerificationAgent je vypnutý v konfiguraci."],
            )
        raw_claims = context.state.get("short_report_claims_by_ticker")
        raw_facts = context.state.get("fundamental_facts_by_ticker")
        raw_forensics = context.state.get("financial_forensics_by_ticker")
        if not isinstance(raw_claims, dict):
            return AgentResult(
                status=AgentStatus.UNAVAILABLE,
                warnings=["ClaimVerificationAgent nedostal short report claims."],
            )
        facts_by_ticker = raw_facts if isinstance(raw_facts, dict) else {}
        forensics_by_ticker = raw_forensics if isinstance(raw_forensics, dict) else {}
        observed_at = utc_now()
        verified_claims: list[ResearchClaim] = []
        evidence: list[AgentEvidence] = []
        warnings: list[str] = []
        insufficient = 0

        for raw_ticker in context.watchlist:
            ticker = normalize_ticker(raw_ticker)
            ticker_claims = raw_claims.get(ticker, [])
            if not isinstance(ticker_claims, list):
                continue
            facts = [
                fact
                for fact in facts_by_ticker.get(ticker, [])
                if isinstance(fact, FundamentalFact)
            ]
            forensic_summary = forensics_by_ticker.get(ticker)
            for claim in ticker_claims:
                if not isinstance(claim, ResearchClaim):
                    continue
                status, summary, reason, confidence = self._evaluate(
                    claim,
                    forensic_summary if isinstance(forensic_summary, dict) else None,
                )
                relevant_facts = self._relevant_facts(
                    claim,
                    facts,
                    context.started_at,
                )
                sec_document_ids = sorted(
                    {fact.document_id for fact in relevant_facts if fact.document_id}
                )
                sec_urls = sorted(
                    {fact.source_url for fact in relevant_facts if fact.source_url}
                )
                if status in {ClaimStatus.CORROBORATED, ClaimStatus.CONTRADICTED} and not sec_document_ids:
                    status = ClaimStatus.INSUFFICIENT_DATA
                    summary = (
                        "Ověření nemá dohledatelný primární SEC dokument; tvrzení zůstává neověřené."
                    )
                    reason = "missing_primary_document"
                    confidence = 0.0
                if status == ClaimStatus.INSUFFICIENT_DATA:
                    insufficient += 1

                document_ids = list(
                    dict.fromkeys([claim.report_document_id] + sec_document_ids)
                )
                source_urls = list(dict.fromkeys(claim.source_urls + sec_urls))
                verification_metadata = dict(claim.metadata)
                verification_metadata.update(
                    {
                        "verification_method": "structured_sec_crosscheck_v1",
                        "verification_as_of": context.started_at.isoformat(),
                        "verification_reason": reason,
                        "primary_sec_document_count": len(sec_document_ids),
                        "fraud_conclusion": False,
                        "scoring_applied": False,
                        "shadow_mode": context.shadow_mode,
                    }
                )
                verified = replace(
                    claim,
                    status=status,
                    observed_at=observed_at,
                    confidence=max(0.0, min(1.0, confidence)),
                    verification_agent_name=self.name,
                    verification_summary=summary,
                    evidence_document_ids=document_ids,
                    source_urls=source_urls,
                    metadata=verification_metadata,
                )
                verified_claims.append(verified)
                risk_score = {
                    ClaimStatus.CORROBORATED: 55.0,
                    ClaimStatus.CONTRADICTED: 5.0,
                    ClaimStatus.INSUFFICIENT_DATA: 15.0,
                    ClaimStatus.UNVERIFIED: 15.0,
                }[status]
                evidence.append(
                    AgentEvidence(
                        evidence_id=_stable_id(
                            context.orchestration_id,
                            self.name,
                            claim.claim_id,
                        ),
                        ticker=ticker,
                        agent_name=self.name,
                        event_type="SHORT_REPORT_CLAIM_VERIFICATION",
                        observed_at=observed_at,
                        summary=f"{status.value}: {summary}",
                        direction=0.0,
                        risk_score=risk_score,
                        confidence=verified.confidence,
                        hard_veto=False,
                        reasons=[status.value.lower(), reason],
                        document_ids=document_ids,
                        source_urls=source_urls,
                        metadata={
                            "claim_id": claim.claim_id,
                            "claim_type": claim.claim_type,
                            "status": status.value,
                            "verification_reason": reason,
                            "fraud_conclusion": False,
                            "scoring_applied": False,
                            "shadow_mode": context.shadow_mode,
                        },
                    )
                )

        if not verified_claims:
            status = AgentStatus.UNAVAILABLE
            warnings.append(
                "ClaimVerificationAgent neměl žádné extrahované tvrzení k ověření."
            )
        elif insufficient:
            status = AgentStatus.PARTIAL
            warnings.append(
                f"ClaimVerificationAgent: {insufficient} tvrzení nemá dostatek strukturovaných primárních dat."
            )
        else:
            status = AgentStatus.SUCCESS
        by_ticker: dict[str, list[ResearchClaim]] = {}
        for claim in verified_claims:
            by_ticker.setdefault(claim.ticker, []).append(claim)
        status_counts = {
            claim_status.value: sum(
                claim.status == claim_status for claim in verified_claims
            )
            for claim_status in ClaimStatus
        }
        return AgentResult(
            status=status,
            claims=verified_claims,
            evidence=evidence,
            warnings=list(dict.fromkeys(warnings)),
            metadata={
                "verified_claims": len(verified_claims),
                "status_counts": status_counts,
                "fraud_conclusion": False,
                "scoring_applied": False,
            },
            state_updates={"verified_claims_by_ticker": by_ticker},
        )
