from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from market_checker_app.agents import (
    AgentStatus,
    EntityRegistryAgent,
    OrchestratorAgent,
    PredictionV21AdapterAgent,
    QualityGateAgent,
    SecFundamentalsAgent,
)
from market_checker_app.collectors.sec_edgar_client import (
    SEC_COMPANYFACTS_URL,
    SEC_SUBMISSIONS_URL,
    SEC_TICKER_MAP_URL,
    SecCompany,
    SecCompanyBundle,
    SecCompanyFact,
    SecEdgarClient,
    SecFiling,
)
from market_checker_app.config import FundamentalIngestionConfig
from market_checker_app.storage.sqlite_store import SQLiteStore


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


def _ticker_map_payload() -> dict[str, object]:
    return {
        "fields": ["cik", "name", "ticker", "exchange"],
        "data": [[320193, "Apple Inc.", "AAPL", "Nasdaq"]],
    }


def _submissions_payload() -> dict[str, object]:
    return {
        "filings": {
            "recent": {
                "accessionNumber": [
                    "0000320193-25-000079",
                    "0000320193-25-000081",
                    "0000320193-25-000050",
                ],
                "filingDate": ["2025-08-01", "2025-08-02", "2025-05-02"],
                "reportDate": ["2025-06-28", "2025-08-01", "2025-03-29"],
                "form": ["10-Q", "8-K", "10-Q"],
                "primaryDocument": [
                    "aapl-20250628.htm",
                    "aapl-20250801.htm",
                    "aapl-20250329.htm",
                ],
                "primaryDocDescription": [
                    "Quarterly report",
                    "Current report",
                    "Quarterly report",
                ],
            }
        }
    }


def _companyfacts_payload() -> dict[str, object]:
    return {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue",
                    "description": "Revenue from contracts with customers.",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-03-30",
                                "end": "2025-06-28",
                                "val": 94000000000,
                                "accn": "0000320193-25-000079",
                                "fy": 2025,
                                "fp": "Q3",
                                "form": "10-Q",
                                "filed": "2025-08-01",
                                "frame": "CY2025Q2",
                            }
                        ]
                    },
                },
                "Assets": {
                    "label": "Assets",
                    "description": "Total assets.",
                    "units": {
                        "USD": [
                            {
                                "end": "2025-06-28",
                                "val": 331000000000,
                                "accn": "0000320193-25-000079",
                                "fy": 2025,
                                "fp": "Q3",
                                "form": "10-Q",
                                "filed": "2025-08-01",
                                "frame": "CY2025Q2I",
                            }
                        ]
                    },
                },
                "EntityPublicFloat": {
                    "label": "Public float",
                    "description": "Not requested by the Stage 2 MVP.",
                    "units": {"USD": []},
                },
            }
        }
    }


def _bundle() -> SecCompanyBundle:
    company = SecCompany("AAPL", "0000320193", "Apple Inc.", "Nasdaq")
    filing = SecFiling(
        accession_number="0000320193-25-000079",
        form="10-Q",
        filed_at=_dt("2025-08-01"),
        report_date=_dt("2025-06-28"),
        primary_document="aapl-20250628.htm",
        filing_url=(
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "000032019325000079/aapl-20250628.htm"
        ),
        index_url=(
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "000032019325000079/0000320193-25-000079-index.html"
        ),
        primary_document_description="Quarterly report",
    )
    fact = SecCompanyFact(
        taxonomy="us-gaap",
        concept="RevenueFromContractWithCustomerExcludingAssessedTax",
        label="Revenue",
        description="Revenue from contracts with customers.",
        unit="USD",
        value=94000000000.0,
        filed_at=_dt("2025-08-01"),
        form="10-Q",
        accession_number=filing.accession_number,
        source_url=filing.filing_url,
        period_start=_dt("2025-03-30"),
        period_end=_dt("2025-06-28"),
        fiscal_year=2025,
        fiscal_period="Q3",
        frame="CY2025Q2",
    )
    return SecCompanyBundle(company, (filing,), (fact,))


class _FakeBundleClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch_company_bundle(self, ticker: str, **_: object) -> SecCompanyBundle | None:
        self.calls.append(ticker)
        return _bundle() if ticker == "AAPL" else None


def _signals() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ticker": "AAPL",
                "action": "BUY",
                "forecast": "UP",
                "decision_confidence": 0.73,
                "risk_score": 22.0,
                "action_reasons": '["Confirmed consensus"]',
            }
        ]
    )


def _run_acceptance(client: _FakeBundleClient):
    config = FundamentalIngestionConfig(
        enabled=True,
        user_agent="JohnySkoreTests tests@example.com",
    )
    orchestrator = OrchestratorAgent(shadow_mode=True)
    orchestrator.register(EntityRegistryAgent())
    orchestrator.register(SecFundamentalsAgent(config, client=client))
    orchestrator.register(PredictionV21AdapterAgent())
    orchestrator.register(QualityGateAgent())
    frame = _signals()
    original = frame.copy(deep=True)
    report = orchestrator.run(watchlist=["AAPL"], state={"signals": frame})
    pd.testing.assert_frame_equal(original, frame)
    return report


class SecEdgarClientTests(unittest.TestCase):
    def test_official_payloads_are_normalized_without_live_network(self) -> None:
        clock = _FakeClock()
        calls: list[tuple[str, dict[str, str]]] = []

        def transport(
            url: str,
            headers: dict[str, str],
            _: float,
        ) -> dict[str, object]:
            calls.append((url, headers))
            if url == SEC_TICKER_MAP_URL:
                return _ticker_map_payload()
            if url == SEC_SUBMISSIONS_URL.format(cik="0000320193"):
                return _submissions_payload()
            if url == SEC_COMPANYFACTS_URL.format(cik="0000320193"):
                return _companyfacts_payload()
            raise AssertionError(f"Unexpected URL: {url}")

        client = SecEdgarClient(
            user_agent="JohnySkoreTests tests@example.com",
            min_request_interval_seconds=0.0,
            transport=transport,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )
        bundle = client.fetch_company_bundle(
            "aapl",
            allowed_forms=("10-K", "10-Q", "8-K"),
            max_filings=2,
            concepts=(
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                "Assets",
            ),
            max_facts_per_concept=2,
        )

        self.assertIsNotNone(bundle)
        assert bundle is not None
        self.assertEqual("0000320193", bundle.company.cik)
        self.assertEqual(["8-K", "10-Q"], [item.form for item in bundle.filings])
        self.assertEqual(
            {"Assets", "RevenueFromContractWithCustomerExcludingAssessedTax"},
            {item.concept for item in bundle.facts},
        )
        self.assertTrue(all("tests@example.com" in h["User-Agent"] for _, h in calls))
        self.assertEqual(3, len(calls))
        self.assertEqual(2, len(clock.sleeps))
        self.assertTrue(all(delay >= 0.11 for delay in clock.sleeps))


class StageTwoAcceptanceTests(unittest.TestCase):
    def test_sec_agent_passes_quality_gate_without_changing_prediction(self) -> None:
        client = _FakeBundleClient()
        report = _run_acceptance(client)

        self.assertEqual(AgentStatus.SUCCESS, report.status)
        self.assertEqual(
            ["entity_registry", "f2_sec", "prediction_v21_adapter", "quality_gate"],
            [execution.agent_name for execution in report.executions],
        )
        self.assertEqual(["AAPL"], client.calls)
        self.assertEqual(1, len(report.documents))
        self.assertEqual(1, len(report.fundamental_facts))
        self.assertEqual("0000320193", report.fundamental_facts[0].cik)
        self.assertEqual(1, len(report.signals))
        self.assertEqual("BUY", report.signals[0].action)
        self.assertEqual("PASS", report.quality_checks[0].decision.value)
        sec_execution = next(
            item for item in report.executions if item.agent_name == "f2_sec"
        )
        self.assertFalse(sec_execution.result.metadata["scoring_applied"])

    def test_repeated_ingestion_upserts_sources_and_records_observations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            first = _run_acceptance(_FakeBundleClient())
            second = _run_acceptance(_FakeBundleClient())

            store.save_orchestration_report(first)
            store.save_orchestration_report(second)

            self.assertEqual(1, len(store.read_fundamental_facts("AAPL")))
            with store._connect() as conn:
                document_count = conn.execute(
                    "SELECT COUNT(*) FROM documents WHERE source = 'SEC EDGAR'"
                ).fetchone()[0]
                document_observations = conn.execute(
                    "SELECT COUNT(*) FROM document_observations"
                ).fetchone()[0]
                fact_observations = conn.execute(
                    "SELECT COUNT(*) FROM fundamental_fact_observations"
                ).fetchone()[0]
            self.assertEqual(1, document_count)
            self.assertEqual(2, document_observations)
            self.assertEqual(2, fact_observations)

    def test_missing_user_agent_is_audited_without_network_call(self) -> None:
        config = FundamentalIngestionConfig(enabled=True, user_agent="")
        orchestrator = OrchestratorAgent()
        orchestrator.register(EntityRegistryAgent())
        orchestrator.register(SecFundamentalsAgent(config))

        report = orchestrator.run(watchlist=["AAPL"])

        sec_execution = report.executions[-1]
        self.assertEqual(AgentStatus.UNAVAILABLE, sec_execution.status)
        self.assertIn("User-Agent", sec_execution.result.warnings[0])
        self.assertEqual(AgentStatus.PARTIAL, report.status)


if __name__ == "__main__":
    unittest.main()
