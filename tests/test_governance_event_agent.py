from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
from market_checker_app.agents.governance_event_agent import (
    _schedule_13_amendment_comparisons,
)
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


def _schedule_13_document(
    document_id: str,
    *,
    form: str,
    published_at: datetime,
    name: str = "Example Asset Manager LLC",
    shares: float | None = 100.0,
    percent: float | None = 5.0,
    primary: bool = True,
) -> DocumentRecord:
    person: dict[str, object] = {
        "name": name,
        "identity_status": "NAME_ONLY",
    }
    if shares is not None:
        person["aggregate_beneficial_shares"] = shares
    if percent is not None:
        person["percent_of_class"] = percent
    return DocumentRecord(
        document_id=document_id,
        ticker="AAPL",
        source="SEC EDGAR evidence",
        source_type="regulatory_filing",
        source_authority="SEC",
        observed_at=published_at + timedelta(hours=1),
        published_at=published_at,
        url=f"https://www.sec.gov/Archives/{document_id}.htm",
        legal_entity_id=LEGAL_ENTITY_ID,
        issuer_id=LEGAL_ENTITY_ID,
        instrument_id="isin:US0378331005",
        metadata={
            "form": form,
            "accession_number": document_id,
            "issuer_cik": "320193",
            "filing_index_only": not primary,
            "source_verified_primary": primary,
            "beneficial_ownership": {
                "instrument_identity_verified": True,
                "instrument": {
                    "cusips": ["037833100"],
                    "identity_status": "REGISTRY_MATCHED",
                    "registry_match": {"cusip": "037833100"},
                },
                "reporting_persons": [person],
                "reporting_person_identity_verified": False,
                "ownership_change_interpreted": False,
                "scoring_applied": False,
            },
        },
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
        transaction_code: str = "P",
        acquired_disposed: str = "A",
    ) -> None:
        self.future = future
        self.future_transaction = future_transaction
        self.transaction_code = transaction_code
        self.acquired_disposed = acquired_disposed

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
            transaction_code=self.transaction_code,
            acquired_disposed=self.acquired_disposed,
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
    transaction_code: str = "P",
    acquired_disposed: str = "A",
):
    orchestrator = OrchestratorAgent(shadow_mode=True)
    orchestrator.register(EntityRegistryAgent({"AAPL": _identity()}))
    orchestrator.register(
        _FilingFixtureAgent(
            future=future,
            future_transaction=future_transaction,
            transaction_code=transaction_code,
            acquired_disposed=acquired_disposed,
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
    def test_schedule_13_amendment_comparison_is_as_filed_only(self) -> None:
        original = _schedule_13_document(
            "original", form="SC 13G",
            published_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )
        amendment = _schedule_13_document(
            "amendment", form="SC 13G/A",
            published_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            shares=125.0, percent=6.25,
        )

        comparisons = _schedule_13_amendment_comparisons(
            [amendment, original],
            knowledge_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )

        self.assertEqual({"amendment"}, set(comparisons))
        comparison = comparisons["amendment"]
        self.assertEqual("AS_FILED_CANDIDATE", comparison["comparison_status"])
        self.assertEqual("original", comparison["prior_accession_number"])
        self.assertEqual("amendment", comparison["current_accession_number"])
        self.assertEqual("EXACT_AS_FILED_NAME_ONLY",
                         comparison["reporting_person_basis"])
        delta = comparison["reporting_person_deltas"][0]
        self.assertEqual(25.0, delta["shares_delta"])
        self.assertEqual(1.25, delta["percent_delta"])
        self.assertFalse(comparison["beneficial_owner_identity_verified"])
        self.assertFalse(comparison["ownership_change_interpreted"])
        self.assertTrue(comparison["human_review_required"])
        self.assertFalse(comparison["scoring_applied"])

        event = next(iter(GovernanceEventAgent()._document_events(
            amendment,
            legal_entity_id=LEGAL_ENTITY_ID,
            text="",
            amendment_comparison=comparison,
        )))
        self.assertEqual(GovernanceEventType.BENEFICIAL_OWNERSHIP_FILING,
                         event.event_type)
        self.assertEqual(GovernanceEventStatus.UNVERIFIED, event.status)
        self.assertEqual(comparison, event.metadata["amendment_comparison"])
        self.assertFalse(event.metadata["ownership_change_interpreted"])
        self.assertFalse(event.metadata["scoring_applied"])

    def test_schedule_13_amendment_comparison_fails_closed(self) -> None:
        cutoff = datetime(2026, 7, 1, tzinfo=timezone.utc)
        original_at = datetime(2026, 5, 1, tzinfo=timezone.utc)
        amendment_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
        original = _schedule_13_document(
            "original", form="SC 13D", published_at=original_at,
        )
        amendment = _schedule_13_document(
            "amendment", form="SC 13D/A", published_at=amendment_at,
            shares=110.0,
        )
        cases = {
            "orphan_amendment": [amendment],
            "changed_name": [
                original,
                _schedule_13_document(
                    "changed-name", form="SC 13D/A",
                    published_at=amendment_at, name="Different Name", shares=110.0,
                ),
            ],
            "missing_numeric_field": [
                original,
                _schedule_13_document(
                    "missing-percent", form="SC 13D/A",
                    published_at=amendment_at, shares=110.0, percent=None,
                ),
            ],
            "not_primary": [
                original,
                _schedule_13_document(
                    "index-only", form="SC 13D/A",
                    published_at=amendment_at, shares=110.0, primary=False,
                ),
            ],
            "future_amendment": [
                original,
                _schedule_13_document(
                    "future", form="SC 13D/A",
                    published_at=cutoff + timedelta(days=1), shares=110.0,
                ),
            ],
            "ambiguous_predecessor": [
                original,
                _schedule_13_document(
                    "second-original", form="SC 13D",
                    published_at=original_at,
                ),
                amendment,
            ],
        }
        for label, documents in cases.items():
            with self.subTest(label=label):
                self.assertEqual({}, _schedule_13_amendment_comparisons(
                    documents, knowledge_at=cutoff,
                ))

    def test_form4_compensation_and_tax_are_not_open_market_trades(self) -> None:
        for code, direction in (("A", "A"), ("M", "A"), ("F", "D")):
            with self.subTest(code=code):
                report = _run_governance(transaction_code=code,
                                         acquired_disposed=direction)
                insider = [event for event in report.governance_events
                           if event.metadata.get("accession_number") == "sec-form4"]
                self.assertEqual(1, len(insider))
                self.assertEqual(GovernanceEventType.STOCK_COMPENSATION,
                                 insider[0].event_type)
                self.assertIsNone(insider[0].event_value)
                self.assertFalse(insider[0].metadata["open_market_trade"])

    def test_inconsistent_purchase_direction_is_not_a_trade(self) -> None:
        report = _run_governance(transaction_code="P", acquired_disposed="D")
        insider = [event for event in report.governance_events
                   if event.metadata.get("accession_number") == "sec-form4"]
        self.assertEqual(GovernanceEventType.INSIDER_OTHER_TRANSACTION,
                         insider[0].event_type)

    def test_all_required_event_families_are_normalized_without_a_trade_signal(self) -> None:
        report = _run_governance()

        self.assertEqual(AgentStatus.SUCCESS, report.status)
        event_types = {item.event_type for item in report.governance_events}
        self.assertTrue(
            {
                GovernanceEventType.INSIDER_TRADE,
                GovernanceEventType.BENEFICIAL_OWNERSHIP_FILING,
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
        ownership_filings = [
            item for item in report.governance_events
            if item.event_type == GovernanceEventType.BENEFICIAL_OWNERSHIP_FILING
        ]
        self.assertEqual(2, len(ownership_filings))
        self.assertTrue(all(
            item.status == GovernanceEventStatus.UNVERIFIED
            and item.metadata["human_review_required"]
            and not item.metadata["beneficial_owner_identity_verified"]
            and not item.metadata["instrument_identity_verified"]
            and not item.metadata["ownership_change_interpreted"]
            and not item.metadata["scoring_applied"]
            for item in ownership_filings
        ))
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
