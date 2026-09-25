from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from market_checker_app.collectors.sec_edgar_client import (
    SecAccessBlockedError, SecCompany, SecFiling, SecRateLimitedError,
)
from market_checker_app.agents import (
    EntityRegistryAgent, OrchestratorAgent, PredictionV21AdapterAgent,
    QualityGateAgent, SourceResolutionAgent,
)
from market_checker_app.agents.scout_index_agent import ScoutIndexAgent
from market_checker_app.services.sec_scout_service import SecScoutService
from market_checker_app.storage.scout_store import ScoutStore
import pandas as pd


class FakeIndex:
    def __init__(self, filed_at: datetime) -> None:
        self.calls = 0
        self.filed_at = filed_at

    def fetch_filing_index(self, ticker: str, **kwargs):
        self.calls += 1
        return SecCompany(ticker=ticker, cik="0000320193", name="Apple Inc."), (
            SecFiling(
                accession_number="0000320193-26-000001", form="10-K",
                filed_at=self.filed_at, report_date=self.filed_at,
                primary_document="test.htm", filing_url="https://www.sec.gov/Archives/test.htm",
                index_url="https://www.sec.gov/Archives/index.htm",
            ),
        )


class SecScoutServiceTests(unittest.TestCase):
    def test_filing_is_one_finding_even_after_catchup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            now = datetime.now(timezone.utc)
            client = FakeIndex(now - timedelta(days=1))
            scout = SecScoutService(store, client=client)
            self.assertEqual(1, scout.schedule(["AAPL", "AAPL"], as_of=now))
            self.assertEqual(1, scout.run_batch(as_of=now)["new_findings"])
            self.assertEqual([], store.findings_as_of(
                "AAPL", as_of=now - timedelta(minutes=1)
            ))
            self.assertEqual(1, len(store.findings_as_of("AAPL", as_of=now)))
            later = now + timedelta(days=1)
            scout.schedule(["AAPL"], as_of=later)
            self.assertEqual(0, scout.run_batch(as_of=later)["new_findings"])
            self.assertEqual(2, client.calls)

    def test_unconfigured_source_does_not_claim_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            now = datetime.now(timezone.utc)
            scout = SecScoutService(store)
            scout.schedule(["AAPL"], as_of=now)
            self.assertEqual("WAIT_ACCESS", scout.run_batch(as_of=now)["status"])
            self.assertEqual({"READY": 1}, store.metrics())

    def test_identity_conflict_is_reported_without_a_false_finding(self) -> None:
        class WrongCompany(FakeIndex):
            def fetch_filing_index(self, ticker, **kwargs):
                company, filings = super().fetch_filing_index(ticker, **kwargs)
                return SecCompany(ticker="MSFT", cik=company.cik, name=company.name), filings

        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            now = datetime.now(timezone.utc)
            scout = SecScoutService(store, client=WrongCompany(now))
            scout.schedule(["AAPL"], as_of=now)
            summary = scout.run_batch(as_of=now)
            self.assertEqual("PARTIAL", summary["status"])
            self.assertEqual(1, summary["failed"])
            self.assertEqual([], store.findings_as_of("AAPL", as_of=now))

    def test_long_provider_cooldown_stops_batch_and_is_durable(self) -> None:
        class LimitedIndex:
            def fetch_filing_index(self, ticker, **kwargs):
                raise SecRateLimitedError(3600)

        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            now = datetime.now(timezone.utc)
            scout = SecScoutService(store, client=LimitedIndex())
            scout.schedule(["AAPL", "MSFT"], as_of=now)
            first = scout.run_batch(as_of=now, limit=2)
            self.assertEqual("RATE_LIMITED", first["status"])
            self.assertEqual(1, first["processed"])
            self.assertEqual("RATE_LIMITED", scout.run_batch(as_of=now)["status"])
            self.assertEqual([], store.findings_as_of("AAPL", as_of=now))

    def test_http_403_blocks_provider_instead_of_probing_every_ticker(self) -> None:
        class BlockedIndex:
            def __init__(self):
                self.calls = 0

            def fetch_filing_index(self, ticker, **kwargs):
                self.calls += 1
                raise SecAccessBlockedError("SEC returned HTTP 403")

        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            now = datetime.now(timezone.utc)
            client = BlockedIndex()
            scout = SecScoutService(store, client=client)
            scout.schedule(["AAPL", "MSFT"], as_of=now)
            self.assertEqual("ACCESS_BLOCKED", scout.run_batch(as_of=now)["status"])
            self.assertEqual("ACCESS_BLOCKED", scout.run_batch(as_of=now)["status"])
            self.assertEqual(1, client.calls)

    def test_saved_filing_reaches_quality_checked_agent_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scout.db"
            store = ScoutStore(path)
            now = datetime.now(timezone.utc)
            scout = SecScoutService(store, client=FakeIndex(now - timedelta(days=1)))
            scout.schedule(["AAPL"], as_of=now - timedelta(minutes=2))
            scout.run_batch(as_of=now - timedelta(minutes=2))
            orchestrator = OrchestratorAgent(shadow_mode=True)
            orchestrator.register(EntityRegistryAgent())
            orchestrator.register(ScoutIndexAgent(path))
            orchestrator.register(SourceResolutionAgent(dependencies=("entity_registry",)))
            orchestrator.register(PredictionV21AdapterAgent())
            orchestrator.register(QualityGateAgent())
            report = orchestrator.run(
                watchlist=["AAPL"],
                state={"signals": pd.DataFrame([{
                    "ticker": "AAPL", "action": "NO_TRADE", "forecast": "FLAT",
                    "decision_confidence": 0.5, "risk_score": 0.0,
                    "action_reasons": '["test"]',
                }])},
            )
            self.assertEqual(1, len([item for item in report.documents
                                     if item.source == "SEC EDGAR index"]))
            self.assertEqual("PASS", report.quality_checks[0].decision.value)
            self.assertEqual(0.0, [item for item in report.evidence
                                   if item.agent_name == "scout_index"][0].direction)


if __name__ == "__main__":
    unittest.main()
