from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from market_checker_app.agents import (
    AgentContext,
    AgentResult,
    AgentSignal,
    DecisionAgent,
    DocumentRecord,
    DocumentSourcePriority,
    EntityRegistryAgent,
    EuropeanFilingsAgent,
    GateDecision,
    OrchestratorAgent,
    PredictionV21AdapterAgent,
    QualityGateAgent,
    RegulatoryContractEvent,
    RegulatoryContractEventType,
    RegulatoryEventStatus,
    SecFundamentalsAgent,
)
from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.source_policy import resolve_document_conflicts
from market_checker_app.collectors.european_filing_client import (
    EuropeanFilingClient,
    EuropeanFilingError,
    FetchedEuropeanFiling,
)
from market_checker_app.collectors.sec_edgar_client import (
    SecCompany,
    SecCompanyBundle,
    SecFiling,
)
from market_checker_app.collectors.short_report_client import FetchedShortReport
from market_checker_app.config import (
    DecisionAgentConfig,
    EuropeanFilingConfig,
    EuropeanFilingSourceConfig,
    FundamentalIngestionConfig,
)
from market_checker_app.storage.sqlite_store import SQLiteStore
from market_checker_app.utils.entity_identifiers import normalize_isin, normalize_lei


NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def _lei(body: str) -> str:
    expanded = "".join(
        str(ord(character) - 55) if character.isalpha() else character
        for character in f"{body}00"
    )
    result = f"{body}{98 - int(expanded) % 97:02d}"
    assert normalize_lei(result) == result
    return result


def _isin(sequence: int) -> str:
    base = f"NL{sequence:09d}"
    for digit in range(10):
        candidate = f"{base}{digit}"
        try:
            if normalize_isin(candidate) == candidate:
                return candidate
        except ValueError:
            continue
    raise AssertionError(sequence)


def _source(
    *,
    ticker: str = "TEST",
    authority: str,
    url: str,
    lei: str | None = None,
    isin: str | None = None,
    canonical_event_key: str | None = None,
    audited: bool = False,
    esef: bool = False,
    document_type: str = "annual_report",
) -> EuropeanFilingSourceConfig:
    return EuropeanFilingSourceConfig(
        ticker=ticker,
        authority=authority,
        document_type=document_type,
        title=f"{ticker} annual report",
        published_at=NOW - timedelta(days=1),
        url=url,
        lei=lei,
        isin=isin,
        reporting_period_end=NOW - timedelta(days=60),
        audited=audited,
        esef=esef,
        language="en",
        canonical_event_key=canonical_event_key,
    )


class _FakeEuropeanClient:
    def validate_source(self, source: EuropeanFilingSourceConfig) -> str:
        return source.url

    def fetch(self, source: EuropeanFilingSourceConfig) -> FetchedEuropeanFiling:
        return FetchedEuropeanFiling(
            source=source,
            final_url=source.url,
            mime_type="application/xhtml+xml" if source.esef else "text/html",
            title=source.title,
            text=f"Official filing for {source.ticker}",
            content_hash=f"hash-{source.ticker}-{source.authority}",
            size_bytes=100,
            extractor="fixture",
        )


class _FakeSecClient:
    def __init__(self, bundle: SecCompanyBundle) -> None:
        self.bundle = bundle

    def fetch_company_bundle(self, ticker: str, **_kwargs: object):
        return self.bundle if ticker == "AAPL" else None


class EuropeanAuthorityPolicyTests(unittest.TestCase):
    def test_all_planned_authorities_have_an_explicit_host_policy(self) -> None:
        config = EuropeanFilingConfig(
            enabled=True,
            fetch_content=False,
            allowed_local_exchange_hosts=("pse.cz", "investor.example.com"),
        )
        client = EuropeanFilingClient(config)
        examples = (
            ("EURONEXT", "https://live.euronext.com/en/document.xhtml"),
            ("FCA_NSM", "https://data.fca.org.uk/document.xhtml"),
            ("FCA_RNS", "https://www.londonstockexchange.com/rns/123"),
            ("AFM", "https://www.afm.nl/report.xhtml"),
            ("BAFIN", "https://www.bafin.de/report.xhtml"),
            ("CNB", "https://www.cnb.cz/report.xhtml"),
            ("LOCAL_EXCHANGE", "https://www.pse.cz/report.xhtml"),
            ("ISSUER_IR", "https://investor.example.com/report.xhtml"),
        )

        for authority, url in examples:
            with self.subTest(authority=authority):
                self.assertEqual(url, client.validate_source(_source(authority=authority, url=url)))

        with self.assertRaises(EuropeanFilingError):
            client.validate_source(
                _source(authority="EURONEXT", url="https://evil.example/report")
            )

    def test_final_redirect_must_remain_on_the_authority_allowlist(self) -> None:
        class RedirectingClient:
            def fetch(self, source):
                return FetchedShortReport(
                    source=source,
                    final_url="https://evil.example/redirected-report",
                    mime_type="text/html",
                    title="Redirected report",
                    text="content",
                    content_hash="a" * 64,
                    size_bytes=7,
                    extractor="fixture",
                )

        client = EuropeanFilingClient(
            EuropeanFilingConfig(enabled=True),
            client=RedirectingClient(),
        )
        source = _source(
            authority="EURONEXT",
            url="https://live.euronext.com/report.xhtml",
        )

        with self.assertRaises(EuropeanFilingError):
            client.fetch(source)


class SourceHierarchyTests(unittest.TestCase):
    def test_exchange_host_does_not_promote_management_presentation(self) -> None:
        test_lei = _lei("529900TESTX0000002")
        test_isin = _isin(201)
        source = _source(
            authority="EURONEXT",
            url="https://live.euronext.com/test-presentation.pdf",
            lei=test_lei,
            isin=test_isin,
            document_type="management_presentation",
        )
        orchestrator = OrchestratorAgent()
        orchestrator.register(
            EntityRegistryAgent(
                {
                    "TEST": {
                        "entity_id": "listing:test",
                        "ticker": "TEST",
                        "lei": test_lei,
                        "isin": test_isin,
                        "source": "primary_manifest",
                        "source_url": "https://example.com/test",
                        "confidence": 1.0,
                    }
                }
            )
        )
        orchestrator.register(
            EuropeanFilingsAgent(
                EuropeanFilingConfig(enabled=True, sources=(source,)),
                client=_FakeEuropeanClient(),
            )
        )

        report = orchestrator.run(watchlist=["TEST"])

        self.assertEqual(1, len(report.documents))
        self.assertEqual("management_presentation", report.documents[0].source_type)
        self.assertEqual(200, report.documents[0].source_priority)

    def test_conflicting_documents_keep_both_and_choose_stronger_source(self) -> None:
        regulator = DocumentRecord(
            document_id="regulator",
            ticker="TEST",
            source="FCA",
            source_type="regulatory_filing",
            observed_at=NOW,
            published_at=NOW - timedelta(days=1),
            url="https://www.fca.org.uk/filing",
            metadata={"canonical_event_key": "test:fy2025"},
        )
        media = DocumentRecord(
            document_id="media",
            ticker="TEST",
            source="News",
            source_type="media_article",
            observed_at=NOW,
            published_at=NOW,
            url="https://news.example/article",
            metadata={"canonical_event_key": "test:fy2025"},
        )

        retained, resolutions = resolve_document_conflicts([media, regulator])

        self.assertEqual({"media", "regulator"}, {item.document_id for item in retained})
        self.assertEqual("regulator", resolutions[0].preferred_document_id)
        self.assertEqual(600, regulator.source_priority)
        self.assertEqual(100, media.source_priority)

    def test_media_event_is_not_primary_confirmation_for_decision_agent(self) -> None:
        baseline = AgentSignal(
            signal_id="baseline",
            ticker="TEST",
            agent_name="prediction_v21_adapter",
            agent_version="2.1",
            event_type="PREDICTION_V21",
            observed_at=NOW,
            action="BUY",
            forecast="UP",
            direction=1.0,
            risk_score=0.0,
            confidence=0.8,
            evidence_ids=["baseline-evidence"],
        )

        def decision_for(document: DocumentRecord) -> str:
            event = RegulatoryContractEvent(
                event_id=f"event-{document.document_id}",
                ticker="TEST",
                event_type=RegulatoryContractEventType.INVESTIGATION,
                status=RegulatoryEventStatus.ACTIVE,
                title="Investigation",
                authority_or_counterparty="Authority",
                observed_at=NOW,
                published_at=NOW - timedelta(days=1),
                document_id=document.document_id,
                source_url=document.url or "",
                confidence=1.0,
            )
            context = AgentContext(
                orchestration_id="decision-test",
                watchlist=("TEST",),
                started_at=NOW,
                state={
                    "prediction_v21_agent_signals": [baseline],
                    "regulatory_contract_events_by_ticker": {"TEST": [event]},
                    "agent_results": {
                        "source": AgentResult(documents=[document])
                    },
                },
            )
            result = DecisionAgent(
                DecisionAgentConfig(suppression_score_threshold=1.0)
            ).run(context)
            return result.decisions[0].proposed_action

        media = DocumentRecord(
            document_id="media",
            ticker="TEST",
            source="News",
            source_type="media_article",
            observed_at=NOW,
            published_at=NOW - timedelta(days=1),
            url="https://news.example/investigation",
        )
        exchange = DocumentRecord(
            document_id="exchange",
            ticker="TEST",
            source="Euronext",
            source_type="exchange_announcement",
            observed_at=NOW,
            published_at=NOW - timedelta(days=1),
            url="https://live.euronext.com/investigation",
        )

        self.assertEqual("BUY", decision_for(media))
        self.assertEqual("NO_TRADE", decision_for(exchange))

    def test_quality_gate_rejects_forged_media_priority(self) -> None:
        class ForgedDocumentAgent(BaseAgent):
            name = "forged_document"
            dependencies = ("entity_registry",)

            def run(self, context: AgentContext) -> AgentResult:
                return AgentResult(
                    documents=[
                        DocumentRecord(
                            document_id="forged-media",
                            ticker="TEST",
                            source="News",
                            source_type="media_article",
                            source_priority=600,
                            observed_at=context.started_at,
                            published_at=context.started_at - timedelta(days=1),
                            url="https://news.example/forged",
                        )
                    ]
                )

        test_lei = _lei("529900TESTX0000001")
        test_isin = _isin(200)
        orchestrator = OrchestratorAgent()
        orchestrator.register(
            EntityRegistryAgent(
                {
                    "TEST": {
                        "entity_id": "listing:test",
                        "ticker": "TEST",
                        "lei": test_lei,
                        "isin": test_isin,
                        "source": "primary_manifest",
                        "source_url": "https://example.com/test",
                        "confidence": 1.0,
                    }
                }
            )
        )
        orchestrator.register(ForgedDocumentAgent())
        orchestrator.register(PredictionV21AdapterAgent())
        orchestrator.register(QualityGateAgent())
        signals = pd.DataFrame(
            [
                {
                    "ticker": "TEST",
                    "action": "BUY",
                    "forecast": "UP",
                    "decision_confidence": 0.8,
                    "risk_score": 10.0,
                    "action_reasons": '["confirmed"]',
                }
            ]
        )

        report = orchestrator.run(
            watchlist=["TEST"],
            state={"signals": signals},
        )

        self.assertEqual(GateDecision.REJECT, report.quality_checks[0].decision)
        codes = {
            item["code"] for item in report.quality_checks[0].metadata["rejects"]
        }
        self.assertIn("forged_source_priority", codes)


class UnifiedUsEuropeanFilingTests(unittest.TestCase):
    def test_us_euronext_and_uk_documents_share_contract_and_persistence(self) -> None:
        adyen_lei = _lei("529900ADYEN0000001")
        vod_lei = _lei("529900VODAF0000001")
        adyen_isin = _isin(100)
        vod_isin = _isin(101)
        identities = {
            "AAPL": {
                "entity_id": "listing:aapl",
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "cik": "320193",
                "lei": "HWUPKR0MPOU8FGXBT394",
                "isin": "US0378331005",
                "source": "primary_manifest",
                "source_url": "https://example.com/aapl",
                "confidence": 1.0,
            },
            "ADYEN": {
                "entity_id": "listing:adyen",
                "ticker": "ADYEN",
                "name": "Adyen N.V.",
                "lei": adyen_lei,
                "isin": adyen_isin,
                "source": "primary_manifest",
                "source_url": "https://example.com/adyen",
                "confidence": 1.0,
            },
            "VOD": {
                "entity_id": "listing:vod",
                "ticker": "VOD",
                "name": "Vodafone Group Plc",
                "lei": vod_lei,
                "isin": vod_isin,
                "source": "primary_manifest",
                "source_url": "https://example.com/vod",
                "confidence": 1.0,
            },
        }
        us_filing = SecFiling(
            accession_number="0000320193-26-000001",
            form="S-1",
            filed_at=NOW - timedelta(days=2),
            report_date=NOW - timedelta(days=30),
            primary_document="s1.htm",
            filing_url="https://www.sec.gov/Archives/edgar/data/320193/s1.htm",
            index_url="https://www.sec.gov/Archives/edgar/data/320193/s1-index.htm",
        )
        sec_bundle = SecCompanyBundle(
            SecCompany("AAPL", "0000320193", "Apple Inc.", "Nasdaq"),
            (us_filing,),
            (),
        )
        sources = (
            _source(
                ticker="ADYEN",
                authority="EURONEXT",
                url="https://live.euronext.com/adyen-fy2025.xhtml",
                lei=adyen_lei,
                isin=adyen_isin,
                canonical_event_key="ADYEN:FY2025",
                audited=True,
                esef=True,
            ),
            _source(
                ticker="ADYEN",
                authority="ISSUER_IR",
                url="https://investor.adyen.com/fy2025.xhtml",
                lei=adyen_lei,
                isin=adyen_isin,
                canonical_event_key="ADYEN:FY2025",
                audited=False,
                esef=True,
            ),
            _source(
                ticker="VOD",
                authority="FCA_NSM",
                url="https://data.fca.org.uk/vod-fy2025.xhtml",
                lei=vod_lei,
                isin=vod_isin,
                canonical_event_key="VOD:FY2025",
                audited=True,
                esef=True,
            ),
        )
        orchestrator = OrchestratorAgent()
        orchestrator.register(EntityRegistryAgent(identities))
        orchestrator.register(
            SecFundamentalsAgent(
                FundamentalIngestionConfig(
                    enabled=True,
                    user_agent="JohnySkoreTests tests@example.com",
                    extract_latest_10k_text=False,
                ),
                client=_FakeSecClient(sec_bundle),
            )
        )
        orchestrator.register(
            EuropeanFilingsAgent(
                EuropeanFilingConfig(enabled=True, sources=sources),
                client=_FakeEuropeanClient(),
            )
        )

        report = orchestrator.run(watchlist=["AAPL", "ADYEN", "VOD"])

        self.assertEqual(4, len(report.documents))
        self.assertTrue(all(isinstance(item, DocumentRecord) for item in report.documents))
        by_authority = {item.source_authority: item for item in report.documents}
        self.assertEqual(600, by_authority["SEC"].source_priority)
        self.assertEqual(500, by_authority["EURONEXT"].source_priority)
        self.assertEqual(300, by_authority["ISSUER_IR"].source_priority)
        self.assertEqual(600, by_authority["FCA_NSM"].source_priority)
        self.assertEqual("application/xhtml+xml", by_authority["FCA_NSM"].mime_type)
        self.assertTrue(by_authority["FCA_NSM"].metadata["exact_identity_match"])

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "filings.db")
            store.save_orchestration_report(report)
            documents = store.read_documents()
        self.assertEqual(4, len(documents))
        priorities = dict(zip(documents["source_authority"], documents["source_priority"]))
        self.assertEqual(600, priorities["SEC"])
        self.assertEqual(500, priorities["EURONEXT"])
        self.assertEqual(300, priorities["ISSUER_IR"])
        self.assertEqual(600, priorities["FCA_NSM"])


if __name__ == "__main__":
    unittest.main()
