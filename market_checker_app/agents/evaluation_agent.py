from __future__ import annotations

from datetime import datetime
import hashlib
import math
from statistics import mean, stdev
from typing import Any

from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.contracts import (
    ActivationState,
    AgentContext,
    AgentResult,
    AgentStatus,
    PolicyEvaluation,
    SignalActivationDecision,
    utc_now,
)
from market_checker_app.config import EvaluationAgentConfig


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _brier(probabilities: list[float], outcome: list[float]) -> float:
    return sum((probability - target) ** 2 for probability, target in zip(probabilities, outcome))


def _calibration_error(
    samples: list[dict[str, Any]],
    probability_key: str,
    bins: int = 10,
) -> float:
    if not samples:
        return 0.0
    buckets: list[list[tuple[float, float]]] = [[] for _ in range(bins)]
    for sample in samples:
        probabilities = [float(item) for item in sample[probability_key]]
        outcome = [float(item) for item in sample["outcome"]]
        predicted = max(range(len(probabilities)), key=probabilities.__getitem__)
        confidence = probabilities[predicted]
        correct = outcome[predicted]
        bucket = min(bins - 1, int(confidence * bins))
        buckets[bucket].append((confidence, correct))
    total = len(samples)
    return sum(
        len(bucket) / total
        * abs(mean(item[0] for item in bucket) - mean(item[1] for item in bucket))
        for bucket in buckets
        if bucket
    )


class EvaluationAgent(BaseAgent):
    """Evaluate one decision policy on paired, point-in-time OOS outcomes."""

    name = "evaluation_agent"
    version = "1.0"
    required = False
    dependencies = ("prediction_v21_adapter",)

    def __init__(
        self,
        config: EvaluationAgentConfig | None = None,
        *,
        policy_name: str = "conservative_risk_overlay",
        policy_version: str = "1.0",
    ) -> None:
        self.config = config or EvaluationAgentConfig()
        self.policy_name = policy_name
        self.policy_version = policy_version

    @staticmethod
    def _as_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        return None

    def _prior_activation(self, context: AgentContext) -> dict[str, Any]:
        raw = context.state.get("stage4_activation_history")
        if not isinstance(raw, list):
            return {}
        candidates = [
            item
            for item in raw
            if isinstance(item, dict)
            and item.get("policy_name") == self.policy_name
            and item.get("policy_version") == self.policy_version
        ]
        if not candidates:
            return {}

        def sort_key(item: dict[str, Any]) -> str:
            return str(item.get("observed_at", ""))

        return max(candidates, key=sort_key)

    def run(self, context: AgentContext) -> AgentResult:
        raw_samples = context.state.get("stage4_evaluation_samples")
        raw_samples = raw_samples if isinstance(raw_samples, list) else []
        valid_samples: list[dict[str, Any]] = []
        future_samples = 0
        invalid_samples = 0
        seen_decisions: set[str] = set()
        for sample in raw_samples:
            if not isinstance(sample, dict):
                invalid_samples += 1
                continue
            required = {
                "decision_id",
                "policy_name",
                "policy_version",
                "week_start",
                "signal_at",
                "evaluated_at",
                "baseline_correct",
                "candidate_correct",
                "candidate_directional",
                "avoided_miss",
                "missed_hit",
                "baseline_false_positive",
                "candidate_false_positive",
                "baseline_probabilities",
                "candidate_probabilities",
                "outcome",
            }
            if not required.issubset(sample):
                invalid_samples += 1
                continue
            decision_id = str(sample.get("decision_id", ""))
            signal_at = self._as_datetime(sample.get("signal_at"))
            evaluated_at = self._as_datetime(sample.get("evaluated_at"))
            if (
                not decision_id
                or not str(sample.get("week_start", "")).strip()
                or sample.get("policy_name") != self.policy_name
                or sample.get("policy_version") != self.policy_version
                or signal_at is None
                or evaluated_at is None
            ):
                invalid_samples += 1
                continue
            if (
                signal_at.tzinfo is None
                or signal_at.utcoffset() is None
                or evaluated_at.tzinfo is None
                or evaluated_at.utcoffset() is None
                or signal_at >= evaluated_at
            ):
                invalid_samples += 1
                continue
            try:
                binary_values = [
                    int(sample[key])
                    for key in (
                        "baseline_correct",
                        "candidate_correct",
                        "avoided_miss",
                        "missed_hit",
                        "baseline_false_positive",
                        "candidate_false_positive",
                    )
                ]
                probability_sets = [
                    [float(value) for value in sample[key]]
                    for key in (
                        "baseline_probabilities",
                        "candidate_probabilities",
                        "outcome",
                    )
                ]
            except (KeyError, TypeError, ValueError):
                invalid_samples += 1
                continue
            if (
                any(value not in {0, 1} for value in binary_values)
                or not isinstance(sample["candidate_directional"], bool)
                or any(len(values) != 3 for values in probability_sets)
                or any(
                    not math.isfinite(value) or not 0.0 <= value <= 1.0
                    for values in probability_sets
                    for value in values
                )
                or any(
                    not math.isclose(sum(values), 1.0, abs_tol=1e-6)
                    for values in probability_sets
                )
                or any(value not in {0.0, 1.0} for value in probability_sets[2])
            ):
                invalid_samples += 1
                continue
            (
                baseline_correct,
                candidate_correct,
                avoided_miss,
                missed_hit,
                baseline_false_positive,
                candidate_false_positive,
            ) = binary_values
            candidate_directional = sample["candidate_directional"]
            if (
                candidate_correct
                != (
                    baseline_correct
                    if candidate_directional
                    else 1 - baseline_correct
                )
                or avoided_miss
                != int(not candidate_directional and not baseline_correct)
                or missed_hit != int(not candidate_directional and baseline_correct)
                or baseline_false_positive != int(not baseline_correct)
                or candidate_false_positive
                != int(candidate_directional and not baseline_correct)
            ):
                invalid_samples += 1
                continue
            if evaluated_at > context.started_at:
                future_samples += 1
                continue
            if decision_id in seen_decisions:
                invalid_samples += 1
                continue
            seen_decisions.add(decision_id)
            valid_samples.append(sample)

        sample_count = len(valid_samples)
        distinct_weeks = len(
            {str(sample.get("week_start", "")) for sample in valid_samples}
        )
        baseline_values = [
            int(sample["baseline_correct"]) for sample in valid_samples
        ]
        candidate_values = [
            int(sample["candidate_correct"]) for sample in valid_samples
        ]
        differences = [
            candidate - baseline
            for candidate, baseline in zip(candidate_values, baseline_values)
        ]
        baseline_accuracy = mean(baseline_values) * 100.0 if sample_count else 0.0
        candidate_accuracy = mean(candidate_values) * 100.0 if sample_count else 0.0
        lift = mean(differences) * 100.0 if sample_count else 0.0
        standard_error = (
            stdev(differences) / math.sqrt(sample_count)
            if sample_count > 1
            else 0.0
        )
        lift_lower_bound = lift - 1.96 * standard_error * 100.0
        baseline_false_positive_rate = (
            mean(int(sample["baseline_false_positive"]) for sample in valid_samples)
            * 100.0
            if sample_count
            else 0.0
        )
        candidate_false_positive_rate = (
            mean(int(sample["candidate_false_positive"]) for sample in valid_samples)
            * 100.0
            if sample_count
            else 0.0
        )
        coverage = (
            mean(int(bool(sample["candidate_directional"])) for sample in valid_samples)
            * 100.0
            if sample_count
            else 0.0
        )
        baseline_brier = (
            mean(
                _brier(sample["baseline_probabilities"], sample["outcome"])
                for sample in valid_samples
            )
            if sample_count
            else 0.0
        )
        candidate_brier = (
            mean(
                _brier(sample["candidate_probabilities"], sample["outcome"])
                for sample in valid_samples
            )
            if sample_count
            else 0.0
        )
        baseline_calibration_error = _calibration_error(
            valid_samples,
            "baseline_probabilities",
        )
        candidate_calibration_error = _calibration_error(
            valid_samples,
            "candidate_probabilities",
        )
        evaluated_through = max(
            (sample["evaluated_at"] for sample in valid_samples),
            default=None,
        )

        gates = {
            "minimum_oos_samples": sample_count >= self.config.minimum_oos_samples,
            "minimum_distinct_weeks": (
                distinct_weeks >= self.config.minimum_distinct_weeks
            ),
            "minimum_lift": lift >= self.config.minimum_lift_pct_points,
            "positive_lift_lower_bound": (
                lift_lower_bound
                >= self.config.minimum_lift_lower_bound_pct_points
            ),
            "minimum_coverage": coverage >= self.config.minimum_coverage_pct,
            "false_positive_non_increase": (
                candidate_false_positive_rate
                <= baseline_false_positive_rate
                + self.config.maximum_false_positive_increase_pct_points
            ),
            "brier_non_increase": (
                candidate_brier
                <= baseline_brier + self.config.maximum_brier_increase
            ),
            "calibration_non_increase": (
                candidate_calibration_error
                <= baseline_calibration_error
                + self.config.maximum_calibration_error_increase
            ),
            "point_in_time_integrity": future_samples == 0 and invalid_samples == 0,
        }
        gate_passed = all(gates.values())

        observed_at = utc_now()
        evaluation_id = _stable_id(
            context.orchestration_id,
            self.name,
            self.policy_name,
            "evaluation",
        )
        evaluation = PolicyEvaluation(
            evaluation_id=evaluation_id,
            policy_name=self.policy_name,
            policy_version=self.policy_version,
            observed_at=observed_at,
            evaluated_through=evaluated_through,
            sample_count=sample_count,
            distinct_weeks=distinct_weeks,
            baseline_accuracy_pct=baseline_accuracy,
            candidate_accuracy_pct=candidate_accuracy,
            lift_pct_points=lift,
            lift_lower_bound_pct_points=lift_lower_bound,
            baseline_false_positive_rate_pct=baseline_false_positive_rate,
            candidate_false_positive_rate_pct=candidate_false_positive_rate,
            coverage_pct=coverage,
            baseline_brier_score=baseline_brier,
            candidate_brier_score=candidate_brier,
            baseline_calibration_error=baseline_calibration_error,
            candidate_calibration_error=candidate_calibration_error,
            gate_passed=gate_passed,
            gate_results=gates,
            metadata={
                "avoided_misses": sum(
                    int(sample["avoided_miss"]) for sample in valid_samples
                ),
                "missed_hits": sum(
                    int(sample["missed_hit"]) for sample in valid_samples
                ),
                "future_samples_rejected": future_samples,
                "invalid_samples_rejected": invalid_samples,
                "oos_only": True,
                "paired_evaluation": True,
                "confidence_interval": "paired_normal_95pct",
            },
        )

        prior = self._prior_activation(context)
        prior_passes = int(prior.get("consecutive_passes", 0) or 0)
        prior_through = prior.get("evaluated_through")
        if isinstance(prior_through, str):
            try:
                prior_through = datetime.fromisoformat(prior_through)
            except ValueError:
                prior_through = None
        if gate_passed:
            if (
                isinstance(prior_through, datetime)
                and evaluated_through is not None
                and prior_through.tzinfo is not None
                and evaluated_through > prior_through
                and bool(prior.get("gate_passed"))
            ):
                consecutive_passes = prior_passes + 1
            elif (
                isinstance(prior_through, datetime)
                and evaluated_through is not None
                and prior_through == evaluated_through
                and bool(prior.get("gate_passed"))
            ):
                consecutive_passes = prior_passes
            else:
                consecutive_passes = 1
        else:
            consecutive_passes = 0

        enough_data = (
            gates["minimum_oos_samples"] and gates["minimum_distinct_weeks"]
        )
        live_authorized = (
            gate_passed
            and consecutive_passes >= self.config.required_consecutive_passes
            and self.config.enable_after_gate
            and self.policy_name in self.config.enabled_policy_allowlist
            and not context.shadow_mode
        )
        if not enough_data:
            state = ActivationState.INSUFFICIENT_DATA
        elif not gate_passed:
            state = ActivationState.REJECTED
        elif consecutive_passes < self.config.required_consecutive_passes:
            state = ActivationState.SHADOW
        elif live_authorized:
            state = ActivationState.ENABLED
        else:
            state = ActivationState.ELIGIBLE

        failed_gates = [name for name, passed in gates.items() if not passed]
        reasons = [f"failed_gate:{name}" for name in failed_gates]
        if state == ActivationState.SHADOW:
            reasons.append(
                "waiting_for_independent_weekly_passes:"
                f"{consecutive_passes}/{self.config.required_consecutive_passes}"
            )
        elif state == ActivationState.ELIGIBLE:
            reasons.append("eligible_but_live_application_not_authorized")
        activation = SignalActivationDecision(
            activation_id=_stable_id(
                context.orchestration_id,
                self.name,
                self.policy_name,
                "activation",
            ),
            policy_name=self.policy_name,
            policy_version=self.policy_version,
            observed_at=observed_at,
            evaluated_through=evaluated_through,
            state=state,
            evaluation_id=evaluation_id,
            sample_count=sample_count,
            distinct_weeks=distinct_weeks,
            consecutive_passes=consecutive_passes,
            gate_passed=gate_passed,
            live_application_authorized=live_authorized,
            reasons=reasons,
            metadata={
                "required_consecutive_passes": (
                    self.config.required_consecutive_passes
                ),
                "enable_after_gate": self.config.enable_after_gate,
                "policy_allowlisted": (
                    self.policy_name in self.config.enabled_policy_allowlist
                ),
                "shadow_mode": context.shadow_mode,
            },
        )
        preparation_error = context.state.get("stage4_preparation_error")
        warnings: list[str] = []
        if future_samples or invalid_samples:
            warnings.append(
                "EvaluationAgent odmítl vstupní vzorky, které nesplnily "
                "point-in-time nebo unikátní OOS kontrakt."
            )
        if preparation_error:
            warnings.append(
                "Příprava historických dat Etapy 4 selhala; aktivace zůstává "
                f"uzamčená: {preparation_error}"
            )
        return AgentResult(
            status=AgentStatus.SUCCESS,
            policy_evaluations=[evaluation],
            activation_decisions=[activation],
            warnings=warnings,
            metadata={
                "policy_name": self.policy_name,
                "sample_count": sample_count,
                "distinct_weeks": distinct_weeks,
                "activation_state": state.value,
                "gate_passed": gate_passed,
                "live_application_authorized": live_authorized,
            },
            state_updates={
                "stage4_policy_evaluation": evaluation,
                "stage4_activation_decision": activation,
            },
        )
