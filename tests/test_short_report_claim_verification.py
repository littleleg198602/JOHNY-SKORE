from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from market_checker_app.agents import (
    AgentResult,
    AgentStatus,
    ClaimStatus,
    ClaimVerificationAgent,
    DocumentRecord,
    EntityRegistryAgent,
    FinancialForensicsAgent,
    FundamentalFact,
    GateDecision,
    OrchestratorAgent,
    PredictionV21AdapterAgent,
    QualityGateAgent,
    ResearchClaim,
    ShortReportAgent,
)
from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.short_report_agent import _extract_claim_statements
from market_checker_app.agents.contracts import AgentContext, utc_now
from market_checker_app.collectors.short_report_client import (
    FetchedShortReport,
    ShortReportClient,
    ShortReportFetchError,
)
from market_checker_app.config import ShortReportConfig, ShortReportSourceConfig
from market_checker_app.storage.sqlite_store import SQLiteStore


def _periods() -> dict[str, datetime]:
    now = utc_now()
    return {
        "prior_start": now - timedelta(days=790),
        "prior_end": now - timedelta(days=425),
        "prior_filed": now - timedelta(days=380),
        "current_start": now - timedelta(days=424),
        "current_end": now - timedelta(days=59),
        "current_filed": now - timedelta(days=30),
    }


def _fact(
    concept: str,
    value: float,
    *,
    accession: str,
    filed_at: datetime,
    period_end: datetime,
    period_start: datetime | None = None,
) -> FundamentalFact:
    document_id = f"sec:0000000001:{accession}"
    source_url = (
        "https://www.sec.gov/Archives/edgar/data/1/"
        f"{accession.replace('-', '')}/filing.htm"
    )
    return FundamentalFact(
        fact_id=(
            f"{accession}:{concept}:"
            f"{period_start.isoformat() if period_start else 'instant'}:{value}"
        ),
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
        form="10-K",
        accession_number=accession,
        source_url=source_url,
        document_id=document_id,
        period_start=period_start,
        period_end=period_end,
    )


def _financial_facts(*, risky: bool) -> list[FundamentalFact]:
    periods = _periods()
    current_accession = "0000000001-26-000001"
    prior_accession = "0000000001-25-000001"
    current_values = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": 100.0,
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
            filed_at=periods["current_filed"],
            period_start=periods["current_start"],
            period_end=periods["current_end"],
        )
        for concept, value in current_values.items()
    ]
    facts.append(
        _fact(
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            95.0,
            accession=prior_accession,
            filed_at=periods["prior_filed"],
            period_start=periods["prior_start"],
            period_end=periods["prior_end"],
        )
    )
    current_instants = {
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
            filed_at=periods["current_filed"],
            period_end=periods["current_end"],
        )
        for concept, value in current_instants.items()
    )
    prior_instants = {
        "AccountsReceivableNetCurrent": 20.0,
        "InventoryNet": 15.0,
    }
    facts.extend(
        _fact(
            concept,
            value,
            accession=prior_accession,
            filed_at=periods["prior_filed"],
            period_end=periods["prior_end"],
        )
        for concept, value in prior_instants.items()
    )
    return facts


class _StaticSecAgent(BaseAgent):
    name = "f2_sec"
    version = "test"
    dependencies = ("entity_registry",)

    def __init__(self, facts: list[FundamentalFact]) -> None:
        self.facts = facts

    def run(self, context: AgentContext) -> AgentResult:
        documents: dict[str, DocumentRecord] = {}
        for fact in self.facts:
            documents.setdefault(
                fact.document_id,
                DocumentRecord(
                    document_id=fact.document_id,
                    ticker=fact.ticker,
                    source="SEC EDGAR",
                    source_type="regulatory_filing",
                    observed_at=utc_now(),
                    url=fact.source_url,
                    published_at=fact.filed_at,
                    mime_type="text/html",
                ),
            )
        return AgentResult(
            documents=list(documents.values()),
            fundamental_facts=list(self.facts),
            metadata={"scoring_applied": False},
            state_updates={"fundamental_facts_by_ticker": {"TEST": self.facts}},
        )


class _FakeShortReportClient:
    REPORT_TEXT = (
        "We believe operating cash flow is weak and cash conversion is unsustainable. "
        "The company carries excessive debt and leverage that threaten its balance sheet. "
        "Management failed to disclose material related-party arrangements, according to the report."
    )

    def fetch(self, source: ShortReportSourceConfig) -> FetchedShortReport:
        return FetchedShortReport(
            source=source,
            final_url=source.url,
            mime_type="text/html",
            title="Test short report",
            text=self.REPORT_TEXT,
            content_hash="a" * 64,
            size_bytes=len(self.REPORT_TEXT.encode("utf-8")),
            extractor="fixture",
        )


def _source(*, scheme: str = "https") -> ShortReportSourceConfig:
    return ShortReportSourceConfig(
        ticker="TEST",
        publisher="Example Research",
        published_at=utc_now() - timedelta(days=10),
        url=f"{scheme}://research.example.test/report.html",
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


def _run_claim_pipeline(
    *,
    risky: bool,
    facts: list[FundamentalFact] | None = None,
):
    frame = _signals()
    original = frame.copy(deep=True)
    orchestrator = OrchestratorAgent(shadow_mode=True)
    orchestrator.register(EntityRegistryAgent())
    orchestrator.register(
        _StaticSecAgent(facts if facts is not None else _financial_facts(risky=risky))
    )
    orchestrator.register(FinancialForensicsAgent())
    orchestrator.register(
        ShortReportAgent(
            ShortReportConfig(enabled=True, sources=(_source(),)),
            client=_FakeShortReportClient(),
        )
    )
    orchestrator.register(ClaimVerificationAgent())
    orchestrator.register(PredictionV21AdapterAgent())
    orchestrator.register(QualityGateAgent())
    report = orchestrator.run(watchlist=["TEST"], state={"signals": frame})
    pd.testing.assert_frame_equal(original, frame)
    return report


class ShortReportClientTests(unittest.TestCase):
    def test_html_report_is_bounded_and_normalized(self) -> None:
        calls: list[str] = []

        def transport(url, headers, timeout, limit):
            calls.append(url)
            self.assertIn("User-Agent", headers)
            self.assertGreater(timeout, 0)
            self.assertEqual(4096, limit)
            return (
                b"<html><head><title>Report</title></head><body>"
                b"<script>ignore me</script><p>Debt is excessive.</p></body></html>",
                "text/html",
                url,
            )

        client = ShortReportClient(
            user_agent="JohnySkoreTests/1.0",
            max_download_bytes=4096,
            transport=transport,
        )
        report = client.fetch(_source())

        self.assertEqual([_source().url], calls)
        self.assertEqual("Report", report.title)
        self.assertIn("Debt is excessive.", report.text)
        self.assertNotIn("ignore me", report.text)
        self.assertEqual(64, len(report.content_hash))

    def test_page_sections_and_bullets_produce_claims(self) -> None:
        calls: list[str] = []

        def transport(url, headers, timeout, limit):
            calls.append(url)
            return (
                b"<html><body><h1>MSCI</h1>"
                b"<p>The business segments are under pressure and facing client retention challenges.</p>"
                b"<ul><li>Evidence that retention rates are declining and reputational risk is rising.</li>"
                b"<li>Aggressive accounting decisions flatter revenue and costs.</li></ul>"
                b"</body></html>",
                "text/html",
                url,
            )

        client = ShortReportClient(
            user_agent="JohnySkoreTests/1.0",
            transport=transport,
        )
        report = client.fetch(_source())
        claims = _extract_claim_statements(
            report.text,
            minimum_characters=40,
            limit=10,
        )

        self.assertEqual([_source().url], calls)
        self.assertGreaterEqual(len(claims), 3)
        statements = [statement.lower() for _, statement in claims]
        self.assertTrue(any("under pressure" in item for item in statements))
        self.assertTrue(any("declining" in item for item in statements))
        self.assertTrue(any("aggressive accounting" in item for item in statements))

    def test_non_https_private_and_oversized_sources_are_rejected(self) -> None:
        never_called = lambda *args: (_ for _ in ()).throw(
            AssertionError("transport must not be called")
        )
        client = ShortReportClient(
            user_agent="tests",
            max_download_bytes=1024,
            transport=never_called,
        )
        with self.assertRaisesRegex(ShortReportFetchError, "HTTPS"):
            client.fetch(_source(scheme="http"))
        private_source = ShortReportSourceConfig(
            ticker="TEST",
            publisher="Unsafe",
            published_at=utc_now() - timedelta(days=1),
            url="https://127.0.0.1/report",
        )
        with self.assertRaisesRegex(ShortReportFetchError, "IP"):
            client.fetch(private_source)

        oversized = ShortReportClient(
            user_agent="tests",
            max_download_bytes=1024,
            transport=lambda *args: (b"x" * 1025, "text/plain", _source().url),
        )
        with self.assertRaisesRegex(ShortReportFetchError, "překročil limit"):
            oversized.fetch(_source())

    def test_private_redirect_target_is_rejected(self) -> None:
        client = ShortReportClient(
            user_agent="tests",
            transport=lambda *args: (
                b"report",
                "text/plain",
                "https://10.0.0.1/internal",
            ),
        )
        with self.assertRaisesRegex(ShortReportFetchError, "IP"):
            client.fetch(_source())


class ClaimVerificationTests(unittest.TestCase):
    def test_unverified_reports_pass_audit_without_changing_prediction(self) -> None:
        frame = _signals()
        original = frame.copy(deep=True)
        orchestrator = OrchestratorAgent(shadow_mode=True)
        orchestrator.register(EntityRegistryAgent())
        orchestrator.register(
            ShortReportAgent(
                ShortReportConfig(enabled=True, sources=(_source(),)),
                client=_FakeShortReportClient(),
            )
        )
        orchestrator.register(PredictionV21AdapterAgent())
        orchestrator.register(QualityGateAgent())

        report = orchestrator.run(
            watchlist=["TEST"],
            state={"signals": frame},
        )

        pd.testing.assert_frame_equal(original, frame)
        self.assertEqual(GateDecision.PASS, report.quality_checks[0].decision)
        self.assertEqual(3, len(report.claims))
        self.assertTrue(
            all(claim.status == ClaimStatus.UNVERIFIED for claim in report.claims)
        )
        self.assertEqual(1, len(report.signals))
        self.assertEqual("BUY", report.signals[0].action)

    def test_risky_sec_findings_corroborate_only_matching_claims(self) -> None:
        report = _run_claim_pipeline(risky=True)

        verification = next(
            execution
            for execution in report.executions
            if execution.agent_name == "claim_verification"
        )
        statuses = {
            claim.claim_type: claim.status for claim in verification.result.claims
        }
        self.assertEqual(ClaimStatus.CORROBORATED, statuses["cash_conversion"])
        self.assertEqual(ClaimStatus.CORROBORATED, statuses["leverage"])
        self.assertEqual(ClaimStatus.INSUFFICIENT_DATA, statuses["governance"])
        self.assertEqual(AgentStatus.PARTIAL, verification.status)
        self.assertTrue(
            all(not evidence.hard_veto for evidence in verification.result.evidence)
        )
        self.assertTrue(
            all(evidence.direction == 0.0 for evidence in verification.result.evidence)
        )
        self.assertTrue(
            all(
                evidence.metadata["scoring_applied"] is False
                and evidence.metadata["fraud_conclusion"] is False
                for evidence in verification.result.evidence
            )
        )
        self.assertEqual(1, len(report.signals))
        self.assertEqual("prediction_v21_adapter", report.signals[0].agent_name)
        self.assertEqual("BUY", report.signals[0].action)
        self.assertEqual(GateDecision.PASS, report.quality_checks[0].decision)
        self.assertEqual(3, len(report.quality_checks[0].claim_ids))

    def test_healthy_sec_metrics_do_not_corroborate_risk_claims(self) -> None:
        report = _run_claim_pipeline(risky=False)
        verification = next(
            execution
            for execution in report.executions
            if execution.agent_name == "claim_verification"
        )
        statuses = {
            claim.claim_type: claim.status for claim in verification.result.claims
        }
        self.assertEqual(ClaimStatus.CONTRADICTED, statuses["cash_conversion"])
        self.assertEqual(ClaimStatus.CONTRADICTED, statuses["leverage"])
        self.assertEqual(ClaimStatus.INSUFFICIENT_DATA, statuses["governance"])
        self.assertEqual(GateDecision.PASS, report.quality_checks[0].decision)

    def test_future_filing_cannot_change_point_in_time_verification(self) -> None:
        healthy_facts = _financial_facts(risky=False)
        future_facts = []
        for fact in _financial_facts(risky=True):
            if fact.accession_number.endswith("25-000001"):
                continue
            future_accession = "0000000001-27-000001"
            future_document_id = f"sec:0000000001:{future_accession}"
            future_facts.append(
                replace(
                    fact,
                    fact_id="future:" + fact.fact_id,
                    accession_number=future_accession,
                    document_id=future_document_id,
                    source_url=(
                        "https://www.sec.gov/Archives/edgar/data/1/"
                        "000000000127000001/filing.htm"
                    ),
                    filed_at=utc_now() + timedelta(days=30),
                )
            )

        report = _run_claim_pipeline(
            risky=False,
            facts=healthy_facts + future_facts,
        )
        verification = next(
            execution
            for execution in report.executions
            if execution.agent_name == "claim_verification"
        )
        statuses = {
            claim.claim_type: claim.status for claim in verification.result.claims
        }
        self.assertEqual(ClaimStatus.CONTRADICTED, statuses["cash_conversion"])
        self.assertEqual(ClaimStatus.CONTRADICTED, statuses["leverage"])
        for claim in verification.result.claims:
            self.assertNotIn(
                "sec:0000000001:0000000001-27-000001",
                claim.evidence_document_ids,
            )

    def test_claim_versions_and_primary_links_are_persisted(self) -> None:
        report = _run_claim_pipeline(risky=True)
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            store.save_orchestration_report(report)

            claims = store.read_research_claims("TEST")
            self.assertEqual(3, len(claims))
            self.assertEqual(
                {"CORROBORATED", "INSUFFICIENT_DATA"},
                set(claims["status"]),
            )
            corroborated = claims.loc[claims["status"] == "CORROBORATED"]
            self.assertTrue(
                all(
                    len(json.loads(value)) >= 2
                    for value in corroborated["evidence_document_ids_json"]
                )
            )
            with store._connect() as conn:
                observations = conn.execute(
                    "SELECT COUNT(*) FROM research_claim_observations"
                ).fetchone()[0]
                check_claim_ids = conn.execute(
                    "SELECT claim_ids_json FROM quality_gate_checks"
                ).fetchone()[0]
            self.assertEqual(6, observations)
            self.assertEqual(3, len(json.loads(check_claim_ids)))


class _InvalidVerifiedClaimAgent(BaseAgent):
    name = "invalid_verified_claim"
    dependencies = ("entity_registry",)

    def run(self, context: AgentContext) -> AgentResult:
        now = utc_now()
        document = DocumentRecord(
            document_id="short:invalid",
            ticker="TEST",
            source="Fixture",
            source_type="short_report",
            observed_at=now,
            published_at=now - timedelta(days=1),
            url="https://example.test/report",
        )
        non_sec_document = DocumentRecord(
            document_id="article:invalid",
            ticker="TEST",
            source="Company blog",
            source_type="press_release",
            observed_at=now,
            published_at=now - timedelta(days=1),
            url="https://example.test/blog",
        )
        claim = ResearchClaim(
            claim_id="claim:invalid",
            ticker="TEST",
            report_document_id=document.document_id,
            claim_type="leverage",
            statement="The report alleges that debt is excessive and unsustainable.",
            status=ClaimStatus.CORROBORATED,
            observed_at=now,
            published_at=now - timedelta(days=1),
            confidence=0.8,
            source_agent_name=self.name,
            verification_agent_name="fake_verifier",
            verification_summary="Fixture claims verification.",
            evidence_document_ids=[
                document.document_id,
                non_sec_document.document_id,
            ],
            source_urls=[document.url or ""],
        )
        return AgentResult(
            documents=[document, non_sec_document],
            claims=[claim],
        )


class ClaimQualityGateTests(unittest.TestCase):
    def test_verified_claim_without_primary_sec_document_is_rejected(self) -> None:
        orchestrator = OrchestratorAgent(shadow_mode=True)
        orchestrator.register(EntityRegistryAgent())
        orchestrator.register(_InvalidVerifiedClaimAgent())
        orchestrator.register(PredictionV21AdapterAgent())
        orchestrator.register(QualityGateAgent())
        report = orchestrator.run(
            watchlist=["TEST"],
            state={"signals": _signals()},
        )

        self.assertEqual(GateDecision.REJECT, report.quality_checks[0].decision)
        reject_codes = {
            item["code"] for item in report.quality_checks[0].metadata["rejects"]
        }
        self.assertIn("claim_missing_primary_sec_evidence", reject_codes)


if __name__ == "__main__":
    unittest.main()
