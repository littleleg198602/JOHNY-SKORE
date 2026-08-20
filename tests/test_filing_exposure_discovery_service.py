from __future__ import annotations

from datetime import datetime, timezone
import unittest

import pandas as pd

from market_checker_app.agents import (
    CommodityEnergyAgent,
    EntityRegistryAgent,
    GateDecision,
    OrchestratorAgent,
    PredictionV21AdapterAgent,
    QualityGateAgent,
    SecFundamentalsAgent,
    SupplyChainAgent,
)
from market_checker_app.collectors.sec_edgar_client import (
    SecCompany,
    SecCompanyBundle,
    SecCompanyFact,
    SecFiling,
)
from market_checker_app.collectors.short_report_client import FetchedShortReport
from market_checker_app.config import (
    CommodityEnergyConfig,
    FundamentalIngestionConfig,
    ShortReportSourceConfig,
    Stage3SourceVerificationConfig,
    SupplyChainConfig,
)
from market_checker_app.services.filing_exposure_discovery_service import (
    FilingExposureDiscoveryService,
)


PUBLISHED_AT = datetime(2025, 10, 31, tzinfo=timezone.utc)
FILING_URL = "https://www.sec.gov/Archives/edgar/data/1/test-10k.htm"
FILING_TEXT = (
    "Our largest customer accounted for 24% of revenue. "
    "We rely on a limited number of suppliers for critical components. "
    "We outsource manufacturing to contract manufacturers. "
    "Prices for steel, copper and electricity affect our input costs."
)


def _source() -> ShortReportSourceConfig:
    return ShortReportSourceConfig(
        ticker="TEST",
        publisher="SEC EDGAR",
        published_at=PUBLISHED_AT,
        url=FILING_URL,
        discovery_method="sec_filing",
    )


def _fetched(text: str = FILING_TEXT) -> FetchedShortReport:
    return FetchedShortReport(
        source=_source(),
        final_url=FILING_URL,
        mime_type="text/html",
        title="TEST 2025 Form 10-K",
        text=text,
        content_hash="d" * 64,
        size_bytes=len(text.encode("utf-8")),
        extractor="fixture",
    )


class _SecClient:
    def fetch_company_bundle(self, ticker: str, **_: object):
        filing = SecFiling(
            accession_number="0000000001-25-000001",
            form="10-K",
            filed_at=PUBLISHED_AT,
            report_date=datetime(2025, 9, 30, tzinfo=timezone.utc),
            primary_document="test-10k.htm",
            filing_url=FILING_URL,
            index_url=FILING_URL,
        )
        fact = SecCompanyFact(
            taxonomy="us-gaap",
            concept="Assets",
            label="Assets",
            description="Assets",
            unit="USD",
            value=1_000_000.0,
            filed_at=PUBLISHED_AT,
            form="10-K",
            accession_number=filing.accession_number,
            source_url=FILING_URL,
        )
        return SecCompanyBundle(
            company=SecCompany("TEST", "0000000001", "Test Inc.", "NYSE"),
            filings=(filing,),
            facts=(fact,),
        )


class _FilingTextClient:
    def fetch(self, source: ShortReportSourceConfig) -> FetchedShortReport:
        fetched = _fetched()
        return FetchedShortReport(
            source=source,
            final_url=fetched.final_url,
            mime_type=fetched.mime_type,
            title=fetched.title,
            text=fetched.text,
            content_hash=fetched.content_hash,
            size_bytes=fetched.size_bytes,
            extractor=fetched.extractor,
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


class FilingExposureDiscoveryTests(unittest.TestCase):
    def test_explicit_concentrations_and_inputs_are_extracted_conservatively(self) -> None:
        findings = FilingExposureDiscoveryService().discover(_fetched())

        relationships = findings.supply_chain
        self.assertEqual(3, len(relationships))
        self.assertEqual(
            {"CUSTOMER", "SUPPLIER", "CONTRACT_MANUFACTURER"},
            {item.source.relationship_type for item in relationships},
        )
        customer = next(
            item for item in relationships if item.source.relationship_type == "CUSTOMER"
        )
        self.assertEqual(24.0, customer.source.dependency_pct)
        self.assertTrue(all(item.source.confidence <= 0.45 for item in relationships))
        self.assertEqual(
            {"Steel", "Copper", "Electricity"},
            {item.source.resource_name for item in findings.commodity_energy},
        )
        self.assertTrue(
            all(item.source.confidence <= 0.40 for item in findings.commodity_energy)
        )

    def test_neutral_filing_language_does_not_create_inferred_relationships(self) -> None:
        findings = FilingExposureDiscoveryService().discover(
            _fetched("We sell software subscriptions in several markets.")
        )

        self.assertEqual((), findings.supply_chain)
        self.assertEqual((), findings.commodity_energy)

    def test_sec_text_flows_to_stage3_without_persisting_raw_content_or_scoring(self) -> None:
        verification = Stage3SourceVerificationConfig(enabled=True)
        orchestrator = OrchestratorAgent(shadow_mode=True)
        orchestrator.register(EntityRegistryAgent())
        orchestrator.register(
            SecFundamentalsAgent(
                FundamentalIngestionConfig(
                    enabled=True,
                    user_agent="JohnySkoreTests tests@example.com",
                ),
                client=_SecClient(),
                filing_text_client=_FilingTextClient(),
            )
        )
        orchestrator.register(
            SupplyChainAgent(
                SupplyChainConfig(
                    enabled=True,
                    auto_discover_from_sec_filings=True,
                    source_verification=verification,
                ),
                dependencies=("entity_registry", "f2_sec"),
            )
        )
        orchestrator.register(
            CommodityEnergyAgent(
                CommodityEnergyConfig(
                    enabled=True,
                    auto_discover_from_sec_filings=True,
                    source_verification=verification,
                ),
                dependencies=("entity_registry", "f2_sec"),
            )
        )
        orchestrator.register(PredictionV21AdapterAgent())
        orchestrator.register(QualityGateAgent())

        report = orchestrator.run(
            watchlist=["TEST"],
            state={"signals": _signals()},
        )

        self.assertEqual("SUCCESS", report.status.value)
        self.assertEqual(GateDecision.PASS, report.quality_checks[0].decision)
        self.assertEqual(3, len(report.company_relationships))
        self.assertEqual(3, len(report.resource_exposures))
        stage3_evidence = [
            item
            for item in report.evidence
            if item.agent_name in {"supply_chain", "commodity_energy"}
        ]
        self.assertTrue(
            all(item.metadata["scoring_applied"] is False for item in stage3_evidence)
        )
        self.assertTrue(all(document.raw_path is None for document in report.documents))
        sec_execution = next(
            item for item in report.executions if item.agent_name == "f2_sec"
        )
        self.assertEqual(1, sec_execution.result.metadata["filing_text_documents"])
        self.assertFalse(sec_execution.result.metadata["raw_filing_text_persisted"])


if __name__ == "__main__":
    unittest.main()
