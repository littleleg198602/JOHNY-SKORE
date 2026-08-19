from __future__ import annotations

from datetime import datetime, timezone
import unittest

import pandas as pd

from market_checker_app.agents import (
    AgentResult,
    AgentStatus,
    DocumentRecord,
    EntityRegistryAgent,
    FinancialForensicsAgent,
    FundamentalFact,
    OrchestratorAgent,
    PredictionV21AdapterAgent,
    QualityGateAgent,
)
from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.contracts import AgentContext, utc_now


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def _fact(
    concept: str,
    value: float,
    *,
    accession: str,
    filed_at: datetime,
    period_end: datetime,
    period_start: datetime | None = None,
    form: str = "10-K",
) -> FundamentalFact:
    document_id = f"sec:0000000001:{accession}"
    source_url = f"https://www.sec.gov/Archives/edgar/data/1/{accession}/filing.htm"
    period_key = period_start.isoformat() if period_start else "instant"
    return FundamentalFact(
        fact_id=f"{accession}:{concept}:{period_key}:{value}",
        ticker="TEST",
        cik="0000000001",
        taxonomy="us-gaap",
        concept=concept,
        label=concept,
        description=f"Test value for {concept}.",
        unit="USD",
        value=float(value),
        observed_at=utc_now(),
        filed_at=filed_at,
        form=form,
        accession_number=accession,
        source_url=source_url,
        document_id=document_id,
        period_start=period_start,
        period_end=period_end,
    )


def _financial_facts(*, risky: bool, restatement: bool = False) -> list[FundamentalFact]:
    current_start = _dt("2025-01-01")
    current_end = _dt("2025-12-31")
    current_filed = _dt("2026-02-15")
    prior_start = _dt("2024-01-01")
    prior_end = _dt("2024-12-31")
    prior_filed = _dt("2025-02-15")
    current_accession = "0000000001-26-000001"
    prior_accession = "0000000001-25-000001"

    current_values = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": 100.0 if risky else 105.0,
        "NetIncomeLoss": 10.0,
        "OperatingIncomeLoss": 12.0,
        "NetCashProvidedByUsedInOperatingActivities": -2.0 if risky else 12.0,
        "PaymentsToAcquirePropertyPlantAndEquipment": 5.0 if risky else 2.0,
    }
    facts = [
        _fact(
            concept,
            value,
            accession=current_accession,
            filed_at=current_filed,
            period_start=current_start,
            period_end=current_end,
        )
        for concept, value in current_values.items()
    ]
    facts.append(
        _fact(
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            90.0 if risky else 100.0,
            accession=prior_accession,
            filed_at=prior_filed,
            period_start=prior_start,
            period_end=prior_end,
        )
    )

    instant_values = {
        "Assets": 100.0,
        "Liabilities": 95.0 if risky else 40.0,
        "AssetsCurrent": 20.0 if risky else 60.0,
        "LiabilitiesCurrent": 30.0,
        "LongTermDebtNoncurrent": 60.0 if risky else 20.0,
        "AccountsReceivableNetCurrent": 30.0 if risky else 20.0,
        "InventoryNet": 25.0 if risky else 15.0,
    }
    facts.extend(
        _fact(
            concept,
            value,
            accession=current_accession,
            filed_at=current_filed,
            period_end=current_end,
        )
        for concept, value in instant_values.items()
    )
    prior_instant_values = {
        "AccountsReceivableNetCurrent": 15.0 if risky else 20.0,
        "InventoryNet": 10.0 if risky else 15.0,
    }
    facts.extend(
        _fact(
            concept,
            value,
            accession=prior_accession,
            filed_at=prior_filed,
            period_end=prior_end,
        )
        for concept, value in prior_instant_values.items()
    )

    if restatement:
        facts.append(
            _fact(
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                96.0,
                accession="0000000001-26-000002",
                filed_at=_dt("2026-03-01"),
                period_start=current_start,
                period_end=current_end,
                form="10-K/A",
            )
        )
    return facts


class _StaticSecAgent(BaseAgent):
    name = "f2_sec"
    version = "test"
    dependencies = ("entity_registry",)

    def __init__(self, facts: list[FundamentalFact]) -> None:
        self.facts = facts

    def run(self, context: AgentContext) -> AgentResult:
        observed_at = utc_now()
        documents: dict[str, DocumentRecord] = {}
        for fact in self.facts:
            documents.setdefault(
                fact.document_id,
                DocumentRecord(
                    document_id=fact.document_id,
                    ticker=fact.ticker,
                    source="SEC EDGAR",
                    source_type="regulatory_filing",
                    observed_at=observed_at,
                    url=fact.source_url,
                    published_at=fact.filed_at,
                    mime_type="text/html",
                ),
            )
        return AgentResult(
            status=AgentStatus.SUCCESS,
            documents=list(documents.values()),
            fundamental_facts=list(self.facts),
            metadata={"scoring_applied": False},
            state_updates={"fundamental_facts_by_ticker": {"TEST": self.facts}},
        )


def _signals() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ticker": "TEST",
                "action": "BUY",
                "forecast": "UP",
                "decision_confidence": 0.75,
                "risk_score": 20.0,
                "action_reasons": '["Confirmed consensus"]',
            }
        ]
    )


def _run(facts: list[FundamentalFact]):
    frame = _signals()
    original = frame.copy(deep=True)
    orchestrator = OrchestratorAgent(shadow_mode=True)
    orchestrator.register(EntityRegistryAgent())
    orchestrator.register(_StaticSecAgent(facts))
    orchestrator.register(FinancialForensicsAgent())
    orchestrator.register(PredictionV21AdapterAgent())
    orchestrator.register(QualityGateAgent())
    report = orchestrator.run(watchlist=["TEST"], state={"signals": frame})
    pd.testing.assert_frame_equal(original, frame)
    return report


class FinancialForensicsAgentTests(unittest.TestCase):
    def test_risky_financials_create_auditable_findings_without_rescoring(self) -> None:
        report = _run(_financial_facts(risky=True))

        self.assertEqual(AgentStatus.SUCCESS, report.status)
        self.assertEqual(
            [
                "entity_registry",
                "f2_sec",
                "financial_forensics",
                "prediction_v21_adapter",
                "quality_gate",
            ],
            [execution.agent_name for execution in report.executions],
        )
        execution = next(
            item for item in report.executions if item.agent_name == "financial_forensics"
        )
        self.assertEqual(AgentStatus.SUCCESS, execution.status)
        self.assertEqual(1, len(execution.result.evidence))
        evidence = execution.result.evidence[0]
        finding_codes = {
            item["code"] for item in evidence.metadata["findings"]
        }
        self.assertIn("positive_income_negative_operating_cash_flow", finding_codes)
        self.assertIn("critical_current_ratio", finding_codes)
        self.assertIn("receivables_growth_outpaces_revenue", finding_codes)
        self.assertEqual(0.0, evidence.direction)
        self.assertFalse(evidence.hard_veto)
        self.assertFalse(evidence.metadata["fraud_conclusion"])
        self.assertFalse(evidence.metadata["scoring_applied"])
        self.assertGreaterEqual(evidence.risk_score, 50.0)
        self.assertEqual(1, len(report.signals))
        self.assertEqual("prediction_v21_adapter", report.signals[0].agent_name)
        self.assertEqual("BUY", report.signals[0].action)
        self.assertEqual("PASS", report.quality_checks[0].decision.value)

    def test_healthy_financials_remain_low_risk(self) -> None:
        report = _run(_financial_facts(risky=False))

        execution = next(
            item for item in report.executions if item.agent_name == "financial_forensics"
        )
        evidence = execution.result.evidence[0]
        self.assertEqual([], evidence.metadata["findings"])
        self.assertEqual([], evidence.reasons)
        self.assertEqual(0.0, evidence.risk_score)
        self.assertGreaterEqual(evidence.confidence, 0.9)
        self.assertEqual("PASS", report.quality_checks[0].decision.value)

    def test_material_change_between_filings_is_flagged_as_potential_only(self) -> None:
        report = _run(_financial_facts(risky=False, restatement=True))

        execution = next(
            item for item in report.executions if item.agent_name == "financial_forensics"
        )
        evidence = execution.result.evidence[0]
        finding_codes = {
            item["code"] for item in evidence.metadata["findings"]
        }
        self.assertIn("potential_restatement_or_recast", finding_codes)
        self.assertGreaterEqual(
            evidence.metadata["metrics"]["potential_restatement_max_change_pct"],
            2.0,
        )
        self.assertFalse(evidence.metadata["fraud_conclusion"])
        self.assertEqual("PASS", report.quality_checks[0].decision.value)

    def test_low_metric_coverage_is_explicitly_partial(self) -> None:
        facts = [
            _fact(
                "Assets",
                100.0,
                accession="0000000001-26-000001",
                filed_at=_dt("2026-02-15"),
                period_end=_dt("2025-12-31"),
            )
        ]

        report = _run(facts)

        execution = next(
            item for item in report.executions if item.agent_name == "financial_forensics"
        )
        self.assertEqual(AgentStatus.PARTIAL, execution.status)
        self.assertLess(execution.result.evidence[0].confidence, 0.35)
        self.assertTrue(
            any("nízké pokrytí" in warning for warning in execution.result.warnings)
        )
        self.assertEqual("PASS", report.quality_checks[0].decision.value)


if __name__ == "__main__":
    unittest.main()
