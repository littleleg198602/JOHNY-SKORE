from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from market_checker_app.services.evaluation_service import EvaluationService


class Stage4EvaluationService:
    """Create leakage-safe paired OOS samples for a Stage 4 policy."""

    @staticmethod
    def latest_activation(
        activations: pd.DataFrame,
        *,
        policy_name: str,
        policy_version: str,
        as_of: datetime,
    ) -> dict[str, Any]:
        if activations.empty or "policy_name" not in activations.columns:
            return {}
        frame = activations[activations["policy_name"] == policy_name].copy()
        if "policy_version" in frame.columns:
            frame = frame[frame["policy_version"] == policy_version]
        if frame.empty:
            return {}
        frame["observed_at"] = pd.to_datetime(
            frame.get("observed_at"), utc=True, errors="coerce"
        )
        frame = frame[
            frame["observed_at"].notna()
            & (frame["observed_at"] <= pd.Timestamp(as_of))
        ]
        if frame.empty:
            return {}
        row = frame.sort_values(["observed_at", "activation_id"]).iloc[-1]
        return row.to_dict()

    @staticmethod
    def _with_current_snapshot(
        history: pd.DataFrame,
        current_signals: pd.DataFrame,
        *,
        as_of: datetime,
    ) -> pd.DataFrame:
        if current_signals.empty:
            return history.copy()
        current = current_signals.copy()
        current["finished_at"] = pd.Timestamp(as_of)
        previous_ids = pd.to_numeric(
            history.get("run_id", pd.Series(dtype=float)), errors="coerce"
        )
        current["run_id"] = (
            int(previous_ids.max()) + 1 if previous_ids.notna().any() else 1
        )
        if "signal" not in current.columns:
            current["signal"] = current.get("action", "NO_TRADE")
        if "action" not in current.columns:
            current["action"] = current["signal"]
        if "forecast" not in current.columns:
            current["forecast"] = current.get("decision_signal", "FLAT")
        if "current_price_source" not in current.columns:
            current["current_price_source"] = "current_pipeline"
        return pd.concat([history.copy(), current], ignore_index=True, sort=False)

    def build_samples(
        self,
        *,
        history: pd.DataFrame,
        decisions: pd.DataFrame,
        current_signals: pd.DataFrame,
        policy_name: str,
        policy_version: str,
        as_of: datetime,
        hold_tolerance_pct: float,
        minimum_weekly_gap_days: float,
        maximum_weekly_gap_days: float,
    ) -> list[dict[str, Any]]:
        required_decision_columns = {
            "decision_id",
            "pipeline_run_id",
            "ticker",
            "policy_name",
            "policy_version",
            "observed_at",
            "baseline_action",
            "proposed_action",
            "baseline_p_up",
            "baseline_p_flat",
            "baseline_p_down",
            "p_up",
            "p_flat",
            "p_down",
        }
        if decisions.empty or not required_decision_columns.issubset(decisions.columns):
            return []

        history_with_current = self._with_current_snapshot(
            history,
            current_signals,
            as_of=as_of,
        )
        details = EvaluationService().evaluate_predictions(
            history_with_current,
            hold_tolerance_pct=hold_tolerance_pct,
            minimum_weekly_gap_days=minimum_weekly_gap_days,
            maximum_weekly_gap_days=maximum_weekly_gap_days,
        )["prediction_details"]
        if details.empty:
            return []
        details = details[
            details["result"].isin(["HIT", "MISS", "NO_TRADE"])
        ].copy()
        if details.empty:
            return []

        frame = decisions[
            (decisions["policy_name"] == policy_name)
            & (decisions["policy_version"] == policy_version)
        ].copy()
        frame["pipeline_run_id"] = pd.to_numeric(
            frame["pipeline_run_id"], errors="coerce"
        )
        frame["observed_at"] = pd.to_datetime(
            frame["observed_at"], utc=True, errors="coerce"
        )
        frame["ticker"] = frame["ticker"].astype(str).str.strip().str.upper()
        frame = frame[
            frame["pipeline_run_id"].notna()
            & frame["observed_at"].notna()
            & (frame["observed_at"] <= pd.Timestamp(as_of))
        ]
        if frame.empty:
            return []
        frame = frame.sort_values(["observed_at", "decision_id"]).drop_duplicates(
            ["pipeline_run_id", "ticker"], keep="last"
        )

        details["signal_run_id"] = pd.to_numeric(
            details["signal_run_id"], errors="coerce"
        )
        details["signal_at"] = pd.to_datetime(
            details["signal_at"], utc=True, errors="coerce"
        )
        details["evaluated_at"] = pd.to_datetime(
            details["evaluated_at"], utc=True, errors="coerce"
        )
        merged = frame.merge(
            details,
            left_on=["pipeline_run_id", "ticker"],
            right_on=["signal_run_id", "ticker"],
            how="inner",
            suffixes=("_decision", "_baseline"),
        )
        merged = merged[
            merged["signal_at"].notna()
            & merged["evaluated_at"].notna()
            & (merged["observed_at"] <= merged["signal_at"])
            & (merged["evaluated_at"] <= pd.Timestamp(as_of))
        ]
        if merged.empty:
            return []

        actual_index = {"UP": 0, "FLAT": 1, "DOWN": 2}
        samples: list[dict[str, Any]] = []
        for row in merged.to_dict(orient="records"):
            actual_move = str(row.get("actual_move", ""))
            if actual_move not in actual_index:
                continue
            baseline_action = str(row.get("baseline_action", ""))
            realized_return = float(row.get("realized_return_pct", 0.0))
            if baseline_action == "BUY":
                baseline_correct = int(realized_return > 0.0)
            elif baseline_action == "SELL":
                baseline_correct = int(realized_return < 0.0)
            else:
                continue
            proposed_action = str(row.get("proposed_action", ""))
            candidate_directional = proposed_action in {"BUY", "SELL"}
            candidate_correct = (
                baseline_correct if candidate_directional else 1 - baseline_correct
            )
            outcome = [0.0, 0.0, 0.0]
            outcome[actual_index[actual_move]] = 1.0
            baseline_probabilities = [
                float(row["baseline_p_up"]),
                float(row["baseline_p_flat"]),
                float(row["baseline_p_down"]),
            ]
            candidate_probabilities = [
                float(row["p_up"]),
                float(row["p_flat"]),
                float(row["p_down"]),
            ]
            samples.append(
                {
                    "decision_id": str(row["decision_id"]),
                    "ticker": str(row["ticker"]),
                    "policy_name": policy_name,
                    "policy_version": policy_version,
                    "signal_run_id": int(row["signal_run_id"]),
                    "signal_at": row["signal_at"].to_pydatetime(),
                    "evaluated_at": row["evaluated_at"].to_pydatetime(),
                    "week_start": str(row.get("week_start", "")),
                    "actual_move": actual_move,
                    "baseline_action": baseline_action,
                    "proposed_action": proposed_action,
                    "baseline_correct": baseline_correct,
                    "candidate_correct": candidate_correct,
                    "candidate_directional": candidate_directional,
                    "avoided_miss": int(not candidate_directional and not baseline_correct),
                    "missed_hit": int(not candidate_directional and baseline_correct),
                    "baseline_false_positive": int(not baseline_correct),
                    "candidate_false_positive": int(
                        candidate_directional and not baseline_correct
                    ),
                    "baseline_probabilities": baseline_probabilities,
                    "candidate_probabilities": candidate_probabilities,
                    "outcome": outcome,
                }
            )
        return samples
