from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import pandas as pd

from market_checker_app.agents import (
    AgentResult,
    AgentStatus,
    DocumentRecord,
    EntityRegistryAgent,
    GateDecision,
    GovernanceEventAgent,
    GovernanceEventStatus,
    GovernanceEventType,
    OrchestratorAgent,
    PredictionV21AdapterAgent,
    QualityGateAgent,
)
from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.contracts import AgentContext
from market_checker_app.collectors.sec_edgar_client import SecInsiderTransaction
from market_checker_app.config import GovernanceEventConfig
from market_checker_app.storage.sqlite_store import SQLiteStore


APPLE_LEI = "HWUPKR0MPOU8FGXBT394"
LEGAL_ENTITY_ID = f"lei:{APPLE_LEI}"


def _identity() -> dict[str, object]:
    return {
        "entity_id": "listing:aapl",
        "ticker": "AAPL",
        "name": "Apple Inc.",
        "cik": "320193",
        "lei": APPLE_LEI,
        "isin": "US0378331005",
        "source": "primary_manifest",
        "source_url": "https://example.com/aapl",
        "confidence": 1.0,
    }


def _signals() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ticker": "AAPL",
                "action": "BUY",
                "forecast": "UP",
                "decision_confidence": 0.8,
                "risk_score": 15.0,
                "action_reasons": '["confirmed"]',
            }
        ]
    )


class _FilingFixtureAgent(BaseAgent):
    name = "filing_fixture"
    version = "1.0"
    dependencies = ("entity_registry",)

    def __init__(
        self,
        *,
        future: bool = False,
        future_transaction: bool = False,
    ) -> None:
        self.future = future
        self.future_transaction = future_transaction

    def run(self, context: AgentContext) -> AgentResult:
        published_at = context.started_at + (
            timedelta(days=1) if self.future else -timedelta(days=1)
        )

        def document(document_id: str, form: str, items: list[str] | None = None):
            return DocumentRecord(
                document_id=document_id,
                ticker="AAPL",
                source="SEC EDGAR",
                source_type="regulatory_filing",
                source_authority="SEC",
                observed_at=context.started_at,
                published_at=published_at,
                url=f"https://www.sec.gov/Archives/{document_id}.htm",
                legal_entity_id=LEGAL_ENTITY_ID,
                issuer_id=LEGAL_ENTITY_ID,
                instrument_id="isin:US0378331005",
                metadata={
                    "form": form,
                    "items": list(items or []),
                    "accession_number": document_id,
                },
            )

        documents = [
            document("sec-8k", "8-K", ["3.02", "4.01", "4.02"]),
            document("sec-s1", "S-1"),
            document("sec-424b5", "424B5"),
            document("sec-13d", "SC 13D"),
            document("sec-13g", "SC 13G"),
            document("sec-form4", "4"),
            document("sec-10k", "10-K"),
        ]
        filing_text = (
            "Our independent auditor issued a qualified opinion. "
            "Management identified a material weakness in internal controls. "
            "Our chief financial officer resigned from the company. "
            "A director resigned from the board. "
            "A related-party transaction was disclosed. "
            "Certain shares were pledged as collateral. "
            "Stock-based compensation increased during the year."
        )
        fetched_text = SimpleNamespace(
            text=filing_text,
            final_url=documents[-1].url,
            source=SimpleNamespace(url=documents[-1].url),
        )
        transaction = SecInsiderTransaction(
            accession_number="sec-form4",
            owner_cik="0000012345",
            owner_name="Jane Example",
            transaction_date=(
                context.started_at + timedelta(days=1)
                if self.future_transaction
                else published_at - timedelta(days=1)
            ),
            transaction_code="P",
            acquired_disposed="A",
            shares=1000.0,
            price_per_share=150.0,
            shares_owned_after=5000.0,
            ownership_nature="D",
            derivative=False,
            source_url=documents[5].url or "",
        )
        return AgentResult(
            documents=documents,
            state_updates={
                "sec_filing_texts_by_ticker": {"AAPL": [fetched_text]},
                "sec_insider_transactions_by_ticker": {"AAPL": [transaction]},
            },
        )


def _run_governance(
    *,
    future: bool = False,
    future_transaction: bool = False,
    quality_gate: bool = True,
):
    orchestrator = OrchestratorAgent(shadow_mode=True)
    orchestrator.register(EntityRegistryAgent({"AAPL": _identity()}))
    orchestrator.register(
        _FilingFixtureAgent(
            future=future,
            future_transaction=future_transaction,
        )
    )
    orchestrator.register(
        GovernanceEventAgent(
            GovernanceEventConfig(enabled=True),
            dependencies=("entity_registry", "filing_fixture"),
        )
    )
    if quality_gate:
        orchestrator.register(PredictionV21AdapterAgent())
        orchestrator.register(QualityGateAgent())
    return orchestrator.run(
        watchlist=["AAPL"],
        state={"signals": _signals()},
    )


class GovernanceEventAgentTests(unittest.TestCase):
    def test_all_required_event_families_are_normalized_without_a_trade_signal(self) -> None:
        report = _run_governance()

        self.assertEqual(AgentStatus.SUCCESS, report.status)
        event_types = {item.event_type for item in report.governance_events}
        self.assertTrue(
            {
                GovernanceEventType.INSIDER_TRADE,
                GovernanceEventType.BENEFICIAL_OWNERSHIP_CHANGE,
                GovernanceEventType.AUDITOR_CHANGE,
                GovernanceEventType.QUALIFIED_OPINION,
                GovernanceEventType.RESTATEMENT,
                GovernanceEventType.MATERIAL_WEAKNESS,
                GovernanceEventType.EXECUTIVE_RESIGNATION,
                GovernanceEventType.DIRECTOR_RESIGNATION,
                GovernanceEventType.RELATED_PARTY_TRANSACTION,
                GovernanceEventType.STOCK_PLEDGE,
                GovernanceEventType.DILUTION,
                GovernanceEventType.STOCK_COMPENSATION,
            }.issubset(event_types)
        )
        insider = next(
            item
            for item in report.governance_events
            if item.event_type == GovernanceEventType.INSIDER_TRADE
        )
        self.assertEqual(GovernanceEventStatus.VERIFIED, insider.status)
        self.assertEqual("PURCHASE", insider.transaction_type)
        self.assertEqual(150000.0, insider.event_value)
        self.assertTrue(
            all(item.legal_entity_id == LEGAL_ENTITY_ID for item in report.governance_events)
        )
        governance_execution = next(
            item for item in report.executions if item.agent_name == "governance_event"
        )
        self.assertEqual([], governance_execution.result.signals)
        self.assertEqual(0, governance_execution.result.metadata["signals_emitted"])
        self.assertEqual(1, len(report.signals))
        self.assertEqual("BUY", report.signals[0].action)
        self.assertEqual(GateDecision.PASS, report.quality_checks[0].decision)

    def test_future_filing_is_ignored_by_governance_extraction(self) -> None:
        report = _run_governance(future=True, quality_gate=False)

        execution = next(
            item for item in report.executions if item.agent_name == "governance_event"
        )
        self.assertEqual(AgentStatus.PARTIAL, execution.status)
        self.assertEqual([], execution.result.governance_events)
        self.assertEqual(7, execution.result.metadata["future_documents_ignored"])

    def test_future_form4_transaction_is_ignored(self) -> None:
        report = _run_governance(
            future_transaction=True,
            quality_gate=False,
        )

        execution = next(
            item for item in report.executions if item.agent_name == "governance_event"
        )
        self.assertEqual(AgentStatus.PARTIAL, execution.status)
        self.assertEqual(1, execution.result.metadata["future_transactions_ignored"])
        self.assertFalse(
            any(
                item.event_type == GovernanceEventType.INSIDER_TRADE
                for item in execution.result.governance_events
            )
        )

    def test_events_and_observation_history_persist_idempotently(self) -> None:
        first = _run_governance()
        second = _run_governance()

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "governance.db")
            store.save_orchestration_report(first)
            store.save_orchestration_report(second)
            events = store.read_governance_events("AAPL")
            with store._connect() as connection:
                observations = connection.execute(
                    "SELECT COUNT(*) FROM governance_event_observations"
                ).fetchone()[0]

        self.assertEqual(len(first.governance_events), len(events))
        self.assertEqual(len(first.governance_events) * 2, observations)


if __name__ == "__main__":
    unittest.main()
