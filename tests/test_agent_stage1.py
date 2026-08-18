from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

import pandas as pd

from market_checker_app.agents import (
    AgentContext,
    AgentEvidence,
    AgentResult,
    AgentSignal,
    AgentStatus,
    DocumentRecord,
    EntityRegistryAgent,
    GateDecision,
    OrchestratorAgent,
    PredictionV21AdapterAgent,
    QualityGateAgent,
)
from market_checker_app.agents.contracts import utc_now
from market_checker_app.agents.base import BaseAgent
from market_checker_app.models import RunMetadata
from market_checker_app.storage.sqlite_store import SQLiteStore


class _SourceAgent(BaseAgent):
    name = "source"
    required = True

    def run(self, context: AgentContext) -> AgentResult:
        return AgentResult(state_updates={"source_ready": True})


class _DependentAgent(BaseAgent):
    name = "dependent"
    required = True
    dependencies = ("source",)

    def run(self, context: AgentContext) -> AgentResult:
        if context.state.get("source_ready") is not True:
            raise RuntimeError("source state was not propagated")
        return AgentResult(metadata={"source_ready": True})


class _FailingAgent(BaseAgent):
    name = "failing"
    required = True

    def run(self, context: AgentContext) -> AgentResult:
        raise RuntimeError("intentional failure")


class _BlockedAgent(BaseAgent):
    name = "blocked"
    required = True
    dependencies = ("failing",)

    def run(self, context: AgentContext) -> AgentResult:
        raise AssertionError("blocked agent must not execute")


class _DocumentAgent(BaseAgent):
    name = "document_fixture"
    dependencies = ("entity_registry",)

    def run(self, context: AgentContext) -> AgentResult:
        now = datetime.now(timezone.utc)
        return AgentResult(
            documents=[
                DocumentRecord(
                    document_id="document:aapl:test",
                    ticker="AAPL",
                    source="test",
                    source_type="filing",
                    observed_at=now,
                    published_at=now,
                    url="https://example.test/aapl",
                    content_hash="abc123",
                )
            ]
        )


class _InvalidPredictionAgent(BaseAgent):
    name = "prediction_v21_adapter"
    required = True
    dependencies = ("entity_registry",)

    def run(self, context: AgentContext) -> AgentResult:
        now = utc_now()
        evidence = AgentEvidence(
            evidence_id="bad-evidence",
            ticker="AAPL",
            agent_name=self.name,
            event_type="PREDICTION_V21",
            observed_at=now,
            summary="Deliberately inconsistent fixture.",
            direction=-1.0,
            confidence=0.1,
            hard_veto=True,
        )
        signal = AgentSignal(
            signal_id="bad-signal",
            ticker="AAPL",
            agent_name=self.name,
            agent_version=self.version,
            event_type="PREDICTION_V21",
            observed_at=now,
            action="BUY",
            forecast="DOWN",
            direction=-1.0,
            risk_score=90.0,
            confidence=0.1,
            hard_veto=True,
            evidence_ids=[evidence.evidence_id],
        )
        return AgentResult(evidence=[evidence], signals=[signal])


class StageOneAgentTests(unittest.TestCase):
    def test_orchestrator_resolves_dependencies_and_propagates_state(self):
        orchestrator = OrchestratorAgent()
        orchestrator.register(_DependentAgent())
        orchestrator.register(_SourceAgent())

        report = orchestrator.run(watchlist=["AAPL"])

        self.assertEqual(AgentStatus.SUCCESS, report.status)
        self.assertEqual(
            ["source", "dependent"],
            [execution.agent_name for execution in report.executions],
        )
        self.assertTrue(report.executions[1].result.metadata["source_ready"])

    def test_orchestrator_records_failure_and_blocks_dependency(self):
        orchestrator = OrchestratorAgent()
        orchestrator.register(_BlockedAgent())
        orchestrator.register(_FailingAgent())

        report = orchestrator.run(watchlist=["AAPL"])

        self.assertEqual(AgentStatus.FAILED, report.status)
        statuses = {execution.agent_name: execution.status for execution in report.executions}
        self.assertEqual(AgentStatus.FAILED, statuses["failing"])
        self.assertEqual(AgentStatus.BLOCKED, statuses["blocked"])
        self.assertIn("intentional failure", report.executions[0].result.error or "")

    def test_entity_registry_normalizes_and_deduplicates_symbols(self):
        orchestrator = OrchestratorAgent()
        orchestrator.register(EntityRegistryAgent())

        report = orchestrator.run(watchlist=["brk.b", "BRK.B", " aapl "])

        entities = {entity.ticker: entity for entity in report.entities}
        self.assertEqual({"AAPL", "BRK.B"}, set(entities))
        self.assertEqual("BRK-B", entities["BRK.B"].yahoo_ticker)
        self.assertIn("brk.b", entities["BRK.B"].aliases)

    def test_v21_adapter_emits_bounded_signal_and_veto_evidence(self):
        frame = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "scoring_version": "v2.1_guarded_consensus",
                    "action": " no_trade ",
                    "forecast": " down ",
                    "decision_confidence": 31.0,
                    "risk_score": 87.0,
                    "action_reasons": '["Weak consensus"]',
                    "blocked_reasons": '["Hard risk veto: high_atr_ratio"]',
                    "risk_flags": '["high_atr_ratio"]',
                }
            ]
        )
        original = frame.copy(deep=True)
        orchestrator = OrchestratorAgent()
        orchestrator.register(EntityRegistryAgent())
        orchestrator.register(PredictionV21AdapterAgent())

        report = orchestrator.run(watchlist=["AAPL"], state={"signals": frame})

        self.assertEqual(AgentStatus.SUCCESS, report.status)
        self.assertEqual(1, len(report.evidence))
        self.assertEqual(1, len(report.signals))
        signal = report.signals[0]
        self.assertEqual("NO_TRADE", signal.action)
        self.assertEqual("DOWN", signal.forecast)
        self.assertEqual(-1.0, signal.direction)
        self.assertAlmostEqual(0.31, signal.confidence)
        self.assertTrue(signal.hard_veto)
        self.assertEqual([report.evidence[0].evidence_id], signal.evidence_ids)
        pd.testing.assert_frame_equal(original, frame)

    def test_v21_adapter_marks_missing_predictions_as_unavailable(self):
        orchestrator = OrchestratorAgent()
        orchestrator.register(EntityRegistryAgent())
        orchestrator.register(PredictionV21AdapterAgent())

        report = orchestrator.run(
            watchlist=["AAPL"],
            state={"signals": pd.DataFrame()},
        )

        self.assertEqual(AgentStatus.FAILED, report.status)
        self.assertEqual(["prediction_v21_adapter"], report.failed_agents)
        self.assertEqual(AgentStatus.UNAVAILABLE, report.executions[-1].status)

    def test_quality_gate_passes_consistent_v21_prediction(self):
        frame = pd.DataFrame(
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
        orchestrator = OrchestratorAgent(shadow_mode=True)
        orchestrator.register(EntityRegistryAgent())
        orchestrator.register(PredictionV21AdapterAgent())
        orchestrator.register(QualityGateAgent())

        report = orchestrator.run(watchlist=["AAPL"], state={"signals": frame})

        self.assertEqual(AgentStatus.SUCCESS, report.status)
        self.assertEqual(1, len(report.quality_checks))
        self.assertEqual(GateDecision.PASS, report.quality_checks[0].decision)
        self.assertTrue(report.shadow_mode)

    def test_quality_gate_rejects_inconsistent_prediction_without_mutating_it(self):
        orchestrator = OrchestratorAgent(shadow_mode=True)
        orchestrator.register(EntityRegistryAgent())
        orchestrator.register(_InvalidPredictionAgent())
        orchestrator.register(QualityGateAgent())

        report = orchestrator.run(watchlist=["AAPL"])

        self.assertEqual(AgentStatus.FAILED, report.status)
        self.assertEqual(GateDecision.REJECT, report.quality_checks[0].decision)
        reject_codes = {
            item["code"]
            for item in report.quality_checks[0].metadata["rejects"]
        }
        self.assertTrue(
            {
                "buy_forecast_conflict",
                "veto_action_conflict",
                "low_action_confidence",
            }.issubset(reject_codes)
        )
        invalid_signal = report.signals[0]
        self.assertEqual("BUY", invalid_signal.action)
        self.assertEqual("DOWN", invalid_signal.forecast)

    def test_stage_one_report_is_persisted_with_full_audit_links(self):
        frame = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "scoring_version": "v2.1_guarded_consensus",
                    "action": "BUY",
                    "forecast": "UP",
                    "decision_confidence": 0.73,
                    "risk_score": 22.0,
                    "action_reasons": '["Confirmed consensus"]',
                }
            ]
        )
        orchestrator = OrchestratorAgent(shadow_mode=True)
        orchestrator.register(PredictionV21AdapterAgent())
        orchestrator.register(_DocumentAgent())
        orchestrator.register(EntityRegistryAgent())
        orchestrator.register(QualityGateAgent())
        report = orchestrator.run(watchlist=["AAPL"], state={"signals": frame})

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            store.ensure_schema()
            now = datetime.now(timezone.utc)
            run_id = store.insert_run(RunMetadata(now, now, 1, 1, 0, 0))
            store.save_orchestration_report(report, pipeline_run_id=run_id)

            self.assertEqual(1, len(store.read_orchestration_runs()))
            agent_runs = store.read_agent_runs(report.orchestration_id)
            self.assertEqual(4, len(agent_runs))
            self.assertTrue((agent_runs["pipeline_run_id"] == run_id).all())
            self.assertEqual(1, len(store.read_entities()))
            self.assertEqual(1, len(store.read_evidence(report.orchestration_id)))
            signals = store.read_agent_signals(report.orchestration_id)
            self.assertEqual(1, len(signals))
            self.assertEqual("BUY", signals.iloc[0]["action"])
            self.assertGreater(int(signals.iloc[0]["agent_run_id"]), 0)
            quality_checks = store.read_quality_gate_checks(report.orchestration_id)
            self.assertEqual(1, len(quality_checks))
            self.assertEqual("PASS", quality_checks.iloc[0]["decision"])

            with store._connect() as conn:
                documents = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                observations = conn.execute(
                    "SELECT COUNT(*) FROM document_observations"
                ).fetchone()[0]
                linked_run_id = conn.execute(
                    "SELECT pipeline_run_id FROM orchestration_runs"
                ).fetchone()[0]
            self.assertEqual(1, documents)
            self.assertEqual(1, observations)
            self.assertEqual(run_id, linked_run_id)

    def test_agent_audit_persistence_is_atomic(self):
        frame = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "action": "BUY",
                    "forecast": "UP",
                    "decision_confidence": 0.7,
                    "risk_score": 20.0,
                }
            ]
        )
        orchestrator = OrchestratorAgent()
        orchestrator.register(EntityRegistryAgent())
        orchestrator.register(PredictionV21AdapterAgent())
        report = orchestrator.run(watchlist=["AAPL"], state={"signals": frame})
        report.executions[-1].result.evidence.append(report.evidence[0])

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            store.ensure_schema()
            with self.assertRaises(sqlite3.IntegrityError):
                store.save_orchestration_report(report)

            self.assertTrue(store.read_orchestration_runs().empty)
            self.assertTrue(store.read_agent_runs().empty)
            self.assertTrue(store.read_entities().empty)


if __name__ == "__main__":
    unittest.main()
