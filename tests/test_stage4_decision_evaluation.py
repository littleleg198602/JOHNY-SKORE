from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from market_checker_app.agents import (
    ActivationState,
    AgentContext,
    AgentEvidence,
    AgentResult,
    AgentSignal,
    DecisionAgent,
    DecisionRecord,
    DocumentRecord,
    EntityRegistryAgent,
    EvaluationAgent,
    GateDecision,
    OrchestratorAgent,
    PredictionV21AdapterAgent,
    QualityGateAgent,
)
from market_checker_app.agents.base import BaseAgent
from market_checker_app.config import DecisionAgentConfig, EvaluationAgentConfig
from market_checker_app.models import RunMetadata
from market_checker_app.services.stage4_evaluation_service import (
    Stage4EvaluationService,
)
from market_checker_app.storage.sqlite_store import SQLiteStore


def _baseline(*, action: str = "BUY", forecast: str = "UP") -> AgentSignal:
    now = datetime.now(timezone.utc)
    return AgentSignal(
        signal_id="baseline-aapl",
        ticker="AAPL",
        agent_name="prediction_v21_adapter",
        agent_version="1.0",
        event_type="PREDICTION_V21",
        observed_at=now,
        action=action,
        forecast=forecast,
        direction={"UP": 1.0, "DOWN": -1.0, "FLAT": 0.0}[forecast],
        risk_score=20.0,
        confidence=0.70,
        evidence_ids=["baseline-evidence"],
    )


def _signals() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ticker": "AAPL",
                "action": "BUY",
                "forecast": "UP",
                "decision_confidence": 0.70,
                "risk_score": 20.0,
                "action_reasons": '["baseline"]',
            }
        ]
    )


def _sample(
    index: int,
    *,
    evaluated_at: datetime,
    baseline_correct: int = 0,
    candidate_directional: bool = False,
) -> dict[str, object]:
    return {
        "decision_id": f"decision-{index}",
        "ticker": "AAPL",
        "policy_name": "conservative_risk_overlay",
        "policy_version": "1.0",
        "week_start": f"2026-0{index + 1}-05",
        "signal_at": evaluated_at - timedelta(days=7),
        "evaluated_at": evaluated_at,
        "baseline_correct": baseline_correct,
        "candidate_correct": (
            baseline_correct if candidate_directional else 1 - baseline_correct
        ),
        "candidate_directional": candidate_directional,
        "avoided_miss": int(not candidate_directional and not baseline_correct),
        "missed_hit": int(not candidate_directional and baseline_correct),
        "baseline_false_positive": int(not baseline_correct),
        "candidate_false_positive": int(
            candidate_directional and not baseline_correct
        ),
        "baseline_probabilities": [0.6, 0.2, 0.2],
        "candidate_probabilities": [0.6, 0.2, 0.2],
        "outcome": [0.0, 0.0, 1.0],
    }


class _ForgedLiveDecisionAgent(BaseAgent):
    name = "decision_agent"
    dependencies = ("prediction_v21_adapter",)

    def run(self, context: AgentContext) -> AgentResult:
        baseline = context.state["prediction_v21_agent_signals"][0]
        probabilities = DecisionAgent._probabilities(
            baseline.forecast,
            baseline.confidence,
        )
        return AgentResult(
            decisions=[
                DecisionRecord(
                    decision_id="forged-live",
                    ticker="AAPL",
                    policy_name="conservative_risk_overlay",
                    policy_version="1.0",
                    observed_at=datetime.now(timezone.utc),
                    baseline_signal_id=baseline.signal_id,
                    baseline_action=baseline.action,
                    baseline_forecast=baseline.forecast,
                    proposed_action="NO_TRADE",
                    proposed_forecast=baseline.forecast,
                    baseline_p_up=probabilities["UP"],
                    baseline_p_flat=probabilities["FLAT"],
                    baseline_p_down=probabilities["DOWN"],
                    p_up=probabilities["UP"],
                    p_flat=probabilities["FLAT"],
                    p_down=probabilities["DOWN"],
                    confidence=max(probabilities.values()),
                    hard_veto=True,
                    activation_state=ActivationState.ENABLED,
                    applied_to_prediction=True,
                    metadata={"live_application_authorized": True},
                )
            ]
        )


class _ForensicRiskFixtureAgent(BaseAgent):
    name = "financial_forensics"
    dependencies = ("entity_registry",)

    def run(self, context: AgentContext) -> AgentResult:
        now = datetime.now(timezone.utc)
        document = DocumentRecord(
            document_id="sec-document",
            ticker="AAPL",
            source="SEC",
            source_type="regulatory_filing",
            observed_at=now,
            published_at=now - timedelta(days=1),
            url="https://www.sec.gov/Archives/example",
        )
        evidence = AgentEvidence(
            evidence_id="forensic-risk",
            ticker="AAPL",
            agent_name=self.name,
            event_type="FINANCIAL_FORENSICS",
            observed_at=now,
            summary="Two high-risk forensic findings.",
            risk_score=50.0,
            confidence=0.8,
            reasons=["one", "two"],
            document_ids=[document.document_id],
            source_urls=[document.url or ""],
        )
        return AgentResult(
            documents=[document],
            evidence=[evidence],
            state_updates={
                "financial_forensics_by_ticker": {
                    "AAPL": {
                        "confidence": 0.8,
                        "findings": [
                            {"severity": "HIGH", "code": "one"},
                            {"severity": "HIGH", "code": "two"},
                        ],
                    }
                }
            },
        )


class _ForgedClusterEvaluationAgent(EvaluationAgent):
    def run(self, context: AgentContext) -> AgentResult:
        result = super().run(context)
        evaluation = result.policy_evaluations[0]
        evaluation.gate_results.pop("minimum_positive_week_ratio", None)
        evaluation.metadata.pop("statistical_unit", None)
        return result


class DecisionAgentTests(unittest.TestCase):
    def test_shadow_overlay_suppresses_high_forensic_risk_without_emitting_signal(self) -> None:
        now = datetime.now(timezone.utc)
        baseline = _baseline()
        source_evidence = AgentEvidence(
            evidence_id="forensic-evidence",
            ticker="AAPL",
            agent_name="financial_forensics",
            event_type="FINANCIAL_FORENSICS",
            observed_at=now,
            summary="Two high findings",
            risk_score=50.0,
            confidence=0.8,
            document_ids=["sec-document"],
            source_urls=["https://www.sec.gov/example"],
        )
        context = AgentContext(
            orchestration_id="stage4-shadow",
            watchlist=("AAPL",),
            started_at=now,
            shadow_mode=True,
            state={
                "prediction_v21_agent_signals": [baseline],
                "financial_forensics_by_ticker": {
                    "AAPL": {
                        "confidence": 0.8,
                        "findings": [
                            {"severity": "HIGH", "code": "one"},
                            {"severity": "HIGH", "code": "two"},
                        ],
                    }
                },
                "agent_results": {
                    "financial_forensics": AgentResult(
                        evidence=[source_evidence]
                    )
                },
            },
        )

        result = DecisionAgent(DecisionAgentConfig(enabled=True)).run(context)

        self.assertEqual(1, len(result.decisions))
        decision = result.decisions[0]
        self.assertEqual("BUY", decision.baseline_action)
        self.assertEqual("NO_TRADE", decision.proposed_action)
        self.assertTrue(decision.hard_veto)
        self.assertFalse(decision.applied_to_prediction)
        self.assertEqual([], result.signals)
        self.assertAlmostEqual(1.0, decision.p_up + decision.p_flat + decision.p_down)
        self.assertGreater(decision.p_flat, decision.baseline_p_flat)

    def test_overlay_never_promotes_no_trade(self) -> None:
        now = datetime.now(timezone.utc)
        result = DecisionAgent(DecisionAgentConfig(enabled=True)).run(
            AgentContext(
                orchestration_id="stage4-no-promotion",
                watchlist=("AAPL",),
                started_at=now,
                shadow_mode=True,
                state={"prediction_v21_agent_signals": [_baseline(action="NO_TRADE")]},
            )
        )
        self.assertEqual("NO_TRADE", result.decisions[0].proposed_action)
        self.assertFalse(result.decisions[0].hard_veto)


class EvaluationAgentTests(unittest.TestCase):
    def _config(self, **overrides: object) -> EvaluationAgentConfig:
        values = {
            "enabled": True,
            "minimum_oos_samples": 3,
            "minimum_distinct_weeks": 3,
            "minimum_lift_pct_points": 50.0,
            "minimum_lift_lower_bound_pct_points": 0.0,
            "minimum_coverage_pct": 0.0,
            "required_consecutive_passes": 1,
        }
        values.update(overrides)
        return EvaluationAgentConfig(**values)

    def test_insufficient_history_stays_locked(self) -> None:
        now = datetime.now(timezone.utc)
        result = EvaluationAgent(self._config()).run(
            AgentContext(
                orchestration_id="eval-insufficient",
                watchlist=("AAPL",),
                started_at=now,
                state={"stage4_evaluation_samples": []},
            )
        )
        self.assertEqual(
            ActivationState.INSUFFICIENT_DATA,
            result.activation_decisions[0].state,
        )
        self.assertFalse(result.policy_evaluations[0].gate_passed)

    def test_positive_paired_oos_lift_becomes_eligible_but_not_enabled(self) -> None:
        now = datetime.now(timezone.utc)
        samples = [
            _sample(index, evaluated_at=now - timedelta(days=3 - index))
            for index in range(3)
        ]
        result = EvaluationAgent(self._config()).run(
            AgentContext(
                orchestration_id="eval-eligible",
                watchlist=("AAPL",),
                started_at=now,
                shadow_mode=True,
                state={"stage4_evaluation_samples": samples},
            )
        )
        evaluation = result.policy_evaluations[0]
        activation = result.activation_decisions[0]
        self.assertTrue(evaluation.gate_passed)
        self.assertEqual(100.0, evaluation.lift_pct_points)
        self.assertEqual(ActivationState.ELIGIBLE, activation.state)
        self.assertFalse(activation.live_application_authorized)

    def test_future_sample_fails_point_in_time_gate(self) -> None:
        now = datetime.now(timezone.utc)
        result = EvaluationAgent(self._config()).run(
            AgentContext(
                orchestration_id="eval-future",
                watchlist=("AAPL",),
                started_at=now,
                state={
                    "stage4_evaluation_samples": [
                        _sample(0, evaluated_at=now + timedelta(days=1))
                    ]
                },
            )
        )
        evaluation = result.policy_evaluations[0]
        self.assertFalse(evaluation.gate_results["point_in_time_integrity"])
        self.assertEqual(1, evaluation.metadata["future_samples_rejected"])

    def test_week_clusters_prevent_one_busy_week_from_swamping_oos_gate(self) -> None:
        now = datetime.now(timezone.utc)
        samples = [
            _sample(index, evaluated_at=now - timedelta(days=1))
            for index in range(100)
        ]
        for sample in samples:
            sample["week_start"] = "2026-01-05"
        for index, week_start in enumerate(("2026-01-12", "2026-01-19"), start=100):
            sample = _sample(
                index,
                evaluated_at=now - timedelta(days=1),
                baseline_correct=1,
            )
            sample["week_start"] = week_start
            samples.append(sample)

        result = EvaluationAgent(
            self._config(
                minimum_oos_samples=100,
                minimum_lift_pct_points=-100.0,
                minimum_lift_lower_bound_pct_points=-1000.0,
            )
        ).run(
            AgentContext(
                orchestration_id="eval-week-clusters",
                watchlist=("AAPL",),
                started_at=now,
                state={"stage4_evaluation_samples": samples},
            )
        )

        evaluation = result.policy_evaluations[0]
        self.assertAlmostEqual(-100.0 / 3.0, evaluation.lift_pct_points)
        self.assertGreater(
            evaluation.metadata["sample_weighted_lift_pct_points"],
            90.0,
        )
        self.assertEqual(3, evaluation.metadata["effective_cluster_count"])
        self.assertEqual("week", evaluation.metadata["statistical_unit"])
        self.assertFalse(
            evaluation.gate_results["minimum_positive_week_ratio"]
        )
        self.assertFalse(evaluation.gate_passed)

    def test_one_week_never_gets_artificially_narrow_confidence_interval(self) -> None:
        now = datetime.now(timezone.utc)
        samples = [
            _sample(index, evaluated_at=now - timedelta(days=1))
            for index in range(200)
        ]
        for sample in samples:
            sample["week_start"] = "2026-01-05"

        evaluation = EvaluationAgent(
            self._config(minimum_oos_samples=200, minimum_distinct_weeks=1)
        ).run(
            AgentContext(
                orchestration_id="eval-single-week",
                watchlist=("AAPL",),
                started_at=now,
                state={"stage4_evaluation_samples": samples},
            )
        ).policy_evaluations[0]

        self.assertEqual(-100.0, evaluation.lift_lower_bound_pct_points)
        self.assertFalse(evaluation.gate_results["positive_lift_lower_bound"])
        self.assertFalse(evaluation.gate_passed)

    def test_same_evaluated_window_does_not_increment_consecutive_passes(self) -> None:
        now = datetime.now(timezone.utc)
        evaluated_at = now - timedelta(days=1)
        samples = [_sample(index, evaluated_at=evaluated_at) for index in range(3)]
        result = EvaluationAgent(
            self._config(required_consecutive_passes=3)
        ).run(
            AgentContext(
                orchestration_id="eval-repeat",
                watchlist=("AAPL",),
                started_at=now,
                state={
                    "stage4_evaluation_samples": samples,
                    "stage4_activation_history": [
                        {
                            "policy_name": "conservative_risk_overlay",
                            "policy_version": "1.0",
                            "observed_at": (now - timedelta(hours=1)).isoformat(),
                            "evaluated_through": evaluated_at.isoformat(),
                            "gate_passed": 1,
                            "consecutive_passes": 2,
                        }
                    ],
                },
            )
        )
        self.assertEqual(2, result.activation_decisions[0].consecutive_passes)
        self.assertEqual(ActivationState.SHADOW, result.activation_decisions[0].state)


class StageFourIntegrationTests(unittest.TestCase):
    def test_real_shadow_suppression_passes_quality_gate_without_rescoring(self) -> None:
        frame = _signals()
        original = frame.copy(deep=True)
        orchestrator = OrchestratorAgent(shadow_mode=True)
        orchestrator.register(EntityRegistryAgent())
        orchestrator.register(_ForensicRiskFixtureAgent())
        orchestrator.register(PredictionV21AdapterAgent())
        orchestrator.register(DecisionAgent(DecisionAgentConfig(enabled=True)))
        orchestrator.register(QualityGateAgent())

        report = orchestrator.run(watchlist=["AAPL"], state={"signals": frame})

        self.assertEqual(GateDecision.PASS, report.quality_checks[0].decision)
        self.assertEqual("NO_TRADE", report.decisions[0].proposed_action)
        self.assertFalse(report.decisions[0].applied_to_prediction)
        self.assertEqual(1, len(report.signals))
        pd.testing.assert_frame_equal(original, frame)

    def test_quality_gate_rejects_forged_live_application_in_shadow_mode(self) -> None:
        orchestrator = OrchestratorAgent(shadow_mode=True)
        orchestrator.register(EntityRegistryAgent())
        orchestrator.register(PredictionV21AdapterAgent())
        orchestrator.register(_ForgedLiveDecisionAgent())
        orchestrator.register(QualityGateAgent())

        report = orchestrator.run(watchlist=["AAPL"], state={"signals": _signals()})

        self.assertEqual(GateDecision.REJECT, report.quality_checks[0].decision)
        codes = {
            item["code"] for item in report.quality_checks[0].metadata["rejects"]
        }
        self.assertIn("unauthorized_stage4_application", codes)
        self.assertIn("missing_applied_stage4_signal", codes)

    def test_quality_gate_rejects_missing_week_cluster_controls(self) -> None:
        orchestrator = OrchestratorAgent(shadow_mode=True)
        orchestrator.register(EntityRegistryAgent())
        orchestrator.register(PredictionV21AdapterAgent())
        orchestrator.register(
            _ForgedClusterEvaluationAgent(EvaluationAgentConfig(enabled=True))
        )
        orchestrator.register(
            QualityGateAgent(
                dependencies=(
                    "entity_registry",
                    "prediction_v21_adapter",
                    "evaluation_agent",
                )
            )
        )

        report = orchestrator.run(
            watchlist=["AAPL"],
            state={"signals": _signals(), "stage4_evaluation_samples": []},
        )

        self.assertEqual(GateDecision.REJECT, report.quality_checks[-1].decision)
        codes = {
            item["code"] for item in report.quality_checks[-1].metadata["rejects"]
        }
        self.assertIn("missing_stage4_gate_results", codes)
        self.assertIn("invalid_stage4_cluster_metadata", codes)

    def test_stage4_audit_records_persist_atomically(self) -> None:
        orchestrator = OrchestratorAgent(shadow_mode=True)
        orchestrator.register(EntityRegistryAgent())
        orchestrator.register(PredictionV21AdapterAgent())
        orchestrator.register(DecisionAgent(DecisionAgentConfig(enabled=True)))
        orchestrator.register(
            EvaluationAgent(EvaluationAgentConfig(enabled=True))
        )
        orchestrator.register(QualityGateAgent())
        report = orchestrator.run(
            watchlist=["AAPL"],
            state={"signals": _signals(), "stage4_evaluation_samples": []},
        )
        self.assertTrue(all(check.decision == GateDecision.PASS for check in report.quality_checks))

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            store.ensure_schema()
            now = datetime.now(timezone.utc)
            run_id = store.insert_run(RunMetadata(now, now, 1, 1, 0, 0))
            store.save_orchestration_report(report, pipeline_run_id=run_id)

            self.assertEqual(1, len(store.read_decision_records()))
            self.assertEqual(1, len(store.read_policy_evaluations()))
            activations = store.read_signal_activation_decisions()
            self.assertEqual(1, len(activations))
            self.assertEqual("INSUFFICIENT_DATA", activations.iloc[0]["state"])
            self.assertEqual(2, len(store.read_quality_gate_checks()))

    def test_evaluation_service_uses_only_next_week_and_current_as_of(self) -> None:
        first = datetime(2026, 1, 5, 12, tzinfo=timezone.utc)
        second = first + timedelta(days=7)
        history = pd.DataFrame(
            [
                {
                    "run_id": 1,
                    "finished_at": first,
                    "ticker": "AAPL",
                    "current_price": 100.0,
                    "current_price_source": "fixture",
                    "signal": "BUY",
                    "action": "BUY",
                    "forecast": "UP",
                }
            ]
        )
        decisions = pd.DataFrame(
            [
                {
                    "decision_id": "decision-1",
                    "pipeline_run_id": 1,
                    "ticker": "AAPL",
                    "policy_name": "conservative_risk_overlay",
                    "policy_version": "1.0",
                    "observed_at": first - timedelta(minutes=1),
                    "baseline_action": "BUY",
                    "proposed_action": "NO_TRADE",
                    "baseline_p_up": 0.6,
                    "baseline_p_flat": 0.2,
                    "baseline_p_down": 0.2,
                    "p_up": 0.4,
                    "p_flat": 0.4,
                    "p_down": 0.2,
                }
            ]
        )
        current = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "current_price": 90.0,
                    "current_price_source": "fixture",
                    "signal": "SELL",
                    "action": "SELL",
                    "forecast": "DOWN",
                }
            ]
        )

        samples = Stage4EvaluationService().build_samples(
            history=history,
            decisions=decisions,
            current_signals=current,
            policy_name="conservative_risk_overlay",
            policy_version="1.0",
            as_of=second,
            hold_tolerance_pct=2.0,
            minimum_weekly_gap_days=4.0,
            maximum_weekly_gap_days=10.0,
        )

        self.assertEqual(1, len(samples))
        self.assertEqual(0, samples[0]["baseline_correct"])
        self.assertEqual(1, samples[0]["candidate_correct"])
        self.assertEqual(second, samples[0]["evaluated_at"])


if __name__ == "__main__":
    unittest.main()
