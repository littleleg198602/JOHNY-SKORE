from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import pandas as pd

from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.contracts import (
    AgentContext,
    AgentEvidence,
    AgentResult,
    AgentSignal,
    AgentStatus,
    utc_now,
)


def _optional(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _float(value: Any, default: float = 0.0) -> float:
    value = _optional(value)
    if value is None:
        return default
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return numeric if math.isfinite(numeric) else default


def _upper_text(value: Any, default: str = "") -> str:
    value = _optional(value)
    if value is None:
        return default
    normalized = str(value).strip().upper()
    return normalized or default


def _confidence01(value: Any) -> float:
    numeric = _float(value)
    if numeric > 1.0:
        numeric /= 100.0
    return max(0.0, min(1.0, numeric))


def _json_list(value: Any) -> list[str]:
    value = _optional(value)
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value] if value.strip() else []
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
        return [str(parsed)]
    return [str(value)]


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


class PredictionV21AdapterAgent(BaseAgent):
    """Expose the existing v2.1 output through the common agent contract."""

    name = "prediction_v21_adapter"
    version = "1.0"
    required = True
    dependencies = ("entity_registry",)

    def run(self, context: AgentContext) -> AgentResult:
        frame = context.state.get("signals")
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            return AgentResult(
                status=AgentStatus.UNAVAILABLE,
                warnings=["Adaptér predikce v2.1 nedostal neprázdný DataFrame signals."],
            )

        evidence: list[AgentEvidence] = []
        signals: list[AgentSignal] = []
        observed_at = utc_now()
        for row in frame.to_dict(orient="records"):
            ticker = _upper_text(row.get("ticker"))
            if not ticker:
                continue
            action = _upper_text(row.get("action")) or _upper_text(
                row.get("signal"), "NO_TRADE"
            )
            forecast = _upper_text(row.get("forecast"), "FLAT")
            direction = {"UP": 1.0, "DOWN": -1.0, "FLAT": 0.0}.get(forecast, 0.0)
            confidence = _confidence01(
                row.get("decision_confidence", row.get("final_confidence", 0.0))
            )
            risk_score = max(0.0, min(100.0, _float(row.get("risk_score"))))
            action_reasons = _json_list(row.get("action_reasons"))
            blocked_reasons = _json_list(row.get("blocked_reasons"))
            risk_flags = _json_list(row.get("risk_flags"))
            reasons = list(dict.fromkeys(action_reasons + blocked_reasons + risk_flags))
            hard_veto = action == "NO_TRADE" and any(
                "veto" in reason.lower() for reason in reasons
            )

            evidence_id = _stable_id(
                context.orchestration_id, self.name, ticker, "evidence"
            )
            signal_id = _stable_id(
                context.orchestration_id, self.name, ticker, "signal"
            )
            summary = (
                f"v2.1 action {action}; forecast {forecast}; "
                f"confidence {confidence:.3f}; risk {risk_score:.1f}."
            )
            common_metadata = {
                "scoring_version": _optional(row.get("scoring_version")),
                "decision_signal": _optional(row.get("decision_signal")),
                "signal_strength": _optional(row.get("signal_strength")),
                "final_total_score": _optional(row.get("final_total_score")),
                "bull_bear_spread": _optional(row.get("bull_bear_spread")),
                "shadow_mode": context.shadow_mode,
            }
            evidence.append(
                AgentEvidence(
                    evidence_id=evidence_id,
                    ticker=ticker,
                    agent_name=self.name,
                    event_type="PREDICTION_V21",
                    observed_at=observed_at,
                    summary=summary,
                    direction=direction,
                    risk_score=risk_score,
                    confidence=confidence,
                    hard_veto=hard_veto,
                    reasons=reasons,
                    metadata=common_metadata,
                )
            )
            signals.append(
                AgentSignal(
                    signal_id=signal_id,
                    ticker=ticker,
                    agent_name=self.name,
                    agent_version=self.version,
                    event_type="PREDICTION_V21",
                    observed_at=observed_at,
                    action=action,
                    forecast=forecast,
                    direction=direction,
                    risk_score=risk_score,
                    confidence=confidence,
                    hard_veto=hard_veto,
                    reasons=reasons,
                    evidence_ids=[evidence_id],
                    metadata=common_metadata,
                )
            )

        return AgentResult(
            evidence=evidence,
            signals=signals,
            metadata={"adapted_predictions": len(signals), "shadow_mode": context.shadow_mode},
            state_updates={"prediction_v21_agent_signals": signals},
        )
