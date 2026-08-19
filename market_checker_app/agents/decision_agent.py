from __future__ import annotations

import hashlib
from typing import Any

from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.contracts import (
    ActivationState,
    AgentContext,
    AgentEvidence,
    AgentResult,
    AgentSignal,
    AgentStatus,
    ClaimStatus,
    DecisionRecord,
    RegulatoryContractEventType,
    RegulatoryEventStatus,
    ResearchClaim,
    SignalActivationDecision,
    utc_now,
)
from market_checker_app.config import DecisionAgentConfig


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


class DecisionAgent(BaseAgent):
    """Build a conservative, auditable risk overlay over the v2.1 decision.

    The policy can only retain a baseline action or suppress BUY/SELL to
    NO_TRADE.  It cannot reverse direction or turn a baseline abstention into a
    trade.  Live application additionally requires a previously ENABLED OOS
    activation record and explicit configuration; the default is shadow-only.
    """

    name = "decision_agent"
    version = "1.0"
    required = False
    dependencies = ("prediction_v21_adapter",)

    SERIOUS_REGULATORY_TYPES = {
        RegulatoryContractEventType.CONTRACT_LOSS,
        RegulatoryContractEventType.INVESTIGATION,
        RegulatoryContractEventType.SANCTION,
        RegulatoryContractEventType.LICENSE_CHANGE,
    }
    ACTIVE_REGULATORY_STATUSES = {
        RegulatoryEventStatus.ANNOUNCED,
        RegulatoryEventStatus.ACTIVE,
        RegulatoryEventStatus.PENDING,
        RegulatoryEventStatus.SUSPENDED,
    }

    def __init__(
        self,
        config: DecisionAgentConfig | None = None,
        *,
        dependencies: tuple[str, ...] | None = None,
    ) -> None:
        self.config = config or DecisionAgentConfig()
        if dependencies is not None:
            self.dependencies = dependencies

    @staticmethod
    def _probabilities(forecast: str, confidence: float) -> dict[str, float]:
        confidence = max(0.0, min(1.0, float(confidence)))
        dominant = (1.0 / 3.0) + (2.0 / 3.0) * confidence
        other = (1.0 - dominant) / 2.0
        probabilities = {"UP": other, "FLAT": other, "DOWN": other}
        probabilities[forecast if forecast in probabilities else "FLAT"] = dominant
        return probabilities

    @staticmethod
    def _agent_evidence(context: AgentContext) -> list[AgentEvidence]:
        results = context.state.get("agent_results")
        if not isinstance(results, dict):
            return []
        return [
            evidence
            for result in results.values()
            if isinstance(result, AgentResult)
            for evidence in result.evidence
        ]

    @staticmethod
    def _claims_for_ticker(context: AgentContext, ticker: str) -> list[ResearchClaim]:
        verified = context.state.get("verified_claims_by_ticker")
        raw = context.state.get("short_report_claims_by_ticker")
        source = verified if isinstance(verified, dict) and ticker in verified else raw
        if not isinstance(source, dict):
            return []
        values = source.get(ticker, [])
        return [item for item in values if isinstance(item, ResearchClaim)]

    def _activation_state(self, context: AgentContext) -> ActivationState:
        current = context.state.get("stage4_activation_decision")
        if isinstance(current, SignalActivationDecision):
            if (
                current.policy_name == self.config.policy_name
                and current.policy_version == self.config.policy_version
            ):
                return current.state
        value = context.state.get("stage4_prior_activation")
        if not isinstance(value, dict):
            return ActivationState.INSUFFICIENT_DATA
        if (
            value.get("policy_name") != self.config.policy_name
            or value.get("policy_version") != self.config.policy_version
        ):
            return ActivationState.INSUFFICIENT_DATA
        try:
            return ActivationState(str(value.get("state", "")).upper())
        except ValueError:
            return ActivationState.INSUFFICIENT_DATA

    def _activation_authorized(self, context: AgentContext) -> bool:
        current = context.state.get("stage4_activation_decision")
        if isinstance(current, SignalActivationDecision):
            return (
                current.policy_name == self.config.policy_name
                and current.policy_version == self.config.policy_version
                and current.state == ActivationState.ENABLED
                and current.live_application_authorized
            )
        value = context.state.get("stage4_prior_activation")
        return bool(
            isinstance(value, dict)
            and value.get("policy_name") == self.config.policy_name
            and value.get("policy_version") == self.config.policy_version
            and str(value.get("state", "")).upper() == ActivationState.ENABLED.value
            and value.get("live_application_authorized") in {True, 1}
        )

    def run(self, context: AgentContext) -> AgentResult:
        raw_signals = context.state.get("prediction_v21_agent_signals")
        baselines = (
            [item for item in raw_signals if isinstance(item, AgentSignal)]
            if isinstance(raw_signals, list)
            else []
        )
        if not baselines:
            return AgentResult(
                status=AgentStatus.UNAVAILABLE,
                warnings=["DecisionAgent nedostal predikce v2.1."],
            )

        observed_at = utc_now()
        evidence_index = {
            item.evidence_id: item for item in self._agent_evidence(context)
        }
        evidence_by_ticker: dict[str, list[AgentEvidence]] = {}
        for item in evidence_index.values():
            evidence_by_ticker.setdefault(item.ticker, []).append(item)
        forensic_by_ticker = context.state.get("financial_forensics_by_ticker")
        forensic_by_ticker = (
            forensic_by_ticker if isinstance(forensic_by_ticker, dict) else {}
        )
        regulatory_by_ticker = context.state.get(
            "regulatory_contract_events_by_ticker"
        )
        regulatory_by_ticker = (
            regulatory_by_ticker if isinstance(regulatory_by_ticker, dict) else {}
        )
        activation_state = self._activation_state(context)
        live_authorized = (
            not context.shadow_mode
            and self.config.live_application_enabled
            and context.state.get("stage4_evaluation_enabled") is True
            and self.config.policy_name in self.config.live_policy_allowlist
            and activation_state == ActivationState.ENABLED
            and self._activation_authorized(context)
        )

        decisions: list[DecisionRecord] = []
        overlay_evidence: list[AgentEvidence] = []
        applied_signals: list[AgentSignal] = []
        suppressed = 0

        for baseline in baselines:
            ticker = baseline.ticker
            reasons: list[str] = []
            conflicts: list[str] = []
            linked_evidence_ids: list[str] = []
            linked_claim_ids: list[str] = []
            linked_regulatory_ids: list[str] = []
            risk_components: dict[str, float] = {}

            forensic = forensic_by_ticker.get(ticker)
            if isinstance(forensic, dict):
                confidence = float(forensic.get("confidence", 0.0) or 0.0)
                findings = forensic.get("findings", [])
                findings = findings if isinstance(findings, list) else []
                high_count = sum(
                    isinstance(item, dict) and item.get("severity") == "HIGH"
                    for item in findings
                )
                warn_count = sum(
                    isinstance(item, dict) and item.get("severity") == "WARN"
                    for item in findings
                )
                if confidence >= self.config.minimum_forensic_confidence:
                    component = min(4.0, high_count * 1.5 + warn_count * 0.25)
                    if component:
                        risk_components["financial_forensics"] = component
                        reasons.append(
                            f"financial_forensics: HIGH={high_count}, WARN={warn_count}"
                        )
                        linked_evidence_ids.extend(
                            item.evidence_id
                            for item in evidence_by_ticker.get(ticker, [])
                            if item.agent_name == "financial_forensics"
                        )

            claims = self._claims_for_ticker(context, ticker)
            corroborated = [
                claim
                for claim in claims
                if claim.status == ClaimStatus.CORROBORATED
                and claim.confidence >= self.config.minimum_claim_confidence
            ]
            unresolved = [
                claim
                for claim in claims
                if claim.status
                in {ClaimStatus.UNVERIFIED, ClaimStatus.INSUFFICIENT_DATA}
                and claim.confidence >= self.config.minimum_claim_confidence
            ]
            contradicted = [
                claim for claim in claims if claim.status == ClaimStatus.CONTRADICTED
            ]
            if corroborated:
                risk_components["corroborated_short_claims"] = min(
                    4.5, 3.0 + (len(corroborated) - 1) * 0.75
                )
                reasons.append(
                    f"corroborated_short_claims={len(corroborated)}"
                )
            if unresolved:
                risk_components["unresolved_short_claims"] = min(
                    1.5, len(unresolved) * 0.5
                )
                conflicts.append(
                    f"unresolved_short_claims={len(unresolved)}; claims are not facts"
                )
            if contradicted:
                conflicts.append(
                    f"contradicted_short_claims={len(contradicted)}"
                )
            relevant_claims = corroborated + unresolved
            linked_claim_ids.extend(claim.claim_id for claim in relevant_claims)
            relevant_claim_id_set = set(linked_claim_ids)
            linked_evidence_ids.extend(
                item.evidence_id
                for item in evidence_by_ticker.get(ticker, [])
                if item.metadata.get("claim_id") in relevant_claim_id_set
            )

            serious_events = []
            raw_events = regulatory_by_ticker.get(ticker, [])
            if isinstance(raw_events, list):
                serious_events = [
                    event
                    for event in raw_events
                    if getattr(event, "event_type", None)
                    in self.SERIOUS_REGULATORY_TYPES
                    and getattr(event, "status", None)
                    in self.ACTIVE_REGULATORY_STATUSES
                    and float(getattr(event, "confidence", 0.0))
                    >= self.config.minimum_regulatory_confidence
                    and getattr(event, "published_at", observed_at)
                    <= context.started_at
                ]
            if serious_events:
                risk_components["serious_regulatory_events"] = min(
                    4.0, len(serious_events) * 2.0
                )
                reasons.append(f"serious_regulatory_events={len(serious_events)}")
                linked_regulatory_ids.extend(event.event_id for event in serious_events)
                event_id_set = set(linked_regulatory_ids)
                linked_evidence_ids.extend(
                    item.evidence_id
                    for item in evidence_by_ticker.get(ticker, [])
                    if item.metadata.get("regulatory_event_id") in event_id_set
                )

            linked_evidence_ids = list(dict.fromkeys(linked_evidence_ids))
            linked_claim_ids = list(dict.fromkeys(linked_claim_ids))
            linked_regulatory_ids = list(dict.fromkeys(linked_regulatory_ids))
            risk_score = sum(risk_components.values())
            should_suppress = (
                baseline.action in {"BUY", "SELL"}
                and risk_score >= self.config.suppression_score_threshold
            )
            proposed_action = "NO_TRADE" if should_suppress else baseline.action
            hard_veto = bool(should_suppress)
            if should_suppress:
                suppressed += 1
                reasons.append(
                    "conservative_risk_overlay_suppressed_directional_trade"
                )

            baseline_probabilities = self._probabilities(
                baseline.forecast,
                baseline.confidence,
            )
            probabilities = dict(baseline_probabilities)
            if should_suppress and baseline.forecast in {"UP", "DOWN"}:
                shift = min(
                    probabilities[baseline.forecast],
                    max(0.0, min(0.45, self.config.probability_flat_shift)),
                )
                probabilities[baseline.forecast] -= shift
                probabilities["FLAT"] += shift

            overlay_evidence_id: str | None = None
            if should_suppress:
                source_evidence = [
                    evidence_index[evidence_id]
                    for evidence_id in linked_evidence_ids
                    if evidence_id in evidence_index
                ]
                document_ids = list(
                    dict.fromkeys(
                        document_id
                        for item in source_evidence
                        for document_id in item.document_ids
                    )
                )
                source_urls = list(
                    dict.fromkeys(
                        source_url
                        for item in source_evidence
                        for source_url in item.source_urls
                    )
                )
                overlay_evidence_id = _stable_id(
                    context.orchestration_id,
                    self.name,
                    ticker,
                    "risk-overlay",
                )
                overlay_evidence.append(
                    AgentEvidence(
                        evidence_id=overlay_evidence_id,
                        ticker=ticker,
                        agent_name=self.name,
                        event_type="STAGE4_RISK_OVERLAY",
                        observed_at=observed_at,
                        summary=(
                            "Konzervativní overlay navrhl potlačit směrový obchod; "
                            "nejde o samostatný SELL/BUY signál."
                        ),
                        direction=0.0,
                        risk_score=min(100.0, risk_score * 20.0),
                        confidence=max(probabilities.values()),
                        hard_veto=True,
                        reasons=reasons,
                        document_ids=document_ids,
                        source_urls=source_urls,
                        metadata={
                            "policy_name": self.config.policy_name,
                            "policy_version": self.config.policy_version,
                            "baseline_signal_id": baseline.signal_id,
                            "risk_components": risk_components,
                            "source_evidence_ids": linked_evidence_ids,
                            "scoring_applied": live_authorized,
                            "shadow_mode": context.shadow_mode,
                        },
                    )
                )
                linked_evidence_ids.append(overlay_evidence_id)

            applied = bool(should_suppress and live_authorized)
            decision_id = _stable_id(
                context.orchestration_id,
                self.name,
                self.config.policy_name,
                ticker,
            )
            decision = DecisionRecord(
                decision_id=decision_id,
                ticker=ticker,
                policy_name=self.config.policy_name,
                policy_version=self.config.policy_version,
                observed_at=observed_at,
                baseline_signal_id=baseline.signal_id,
                baseline_action=baseline.action,
                baseline_forecast=baseline.forecast,
                proposed_action=proposed_action,
                proposed_forecast=baseline.forecast,
                baseline_p_up=baseline_probabilities["UP"],
                baseline_p_flat=baseline_probabilities["FLAT"],
                baseline_p_down=baseline_probabilities["DOWN"],
                p_up=probabilities["UP"],
                p_flat=probabilities["FLAT"],
                p_down=probabilities["DOWN"],
                confidence=max(probabilities.values()),
                hard_veto=hard_veto,
                activation_state=activation_state,
                applied_to_prediction=applied,
                reasons=list(dict.fromkeys(reasons)),
                conflicts=list(dict.fromkeys(conflicts)),
                evidence_ids=list(dict.fromkeys(linked_evidence_ids)),
                claim_ids=linked_claim_ids,
                regulatory_event_ids=linked_regulatory_ids,
                metadata={
                    "risk_components": risk_components,
                    "risk_component_total": round(risk_score, 4),
                    "suppression_threshold": self.config.suppression_score_threshold,
                    "live_application_authorized": live_authorized,
                    "shadow_mode": context.shadow_mode,
                    "baseline_agent": baseline.agent_name,
                },
            )
            decisions.append(decision)

            if applied and overlay_evidence_id is not None:
                applied_signals.append(
                    AgentSignal(
                        signal_id=_stable_id(decision_id, "applied-signal"),
                        ticker=ticker,
                        agent_name=self.name,
                        agent_version=self.version,
                        event_type="STAGE4_RISK_OVERLAY_APPLIED",
                        observed_at=observed_at,
                        action="NO_TRADE",
                        forecast=baseline.forecast,
                        direction=baseline.direction,
                        risk_score=min(100.0, risk_score * 20.0),
                        confidence=decision.confidence,
                        hard_veto=True,
                        reasons=decision.reasons,
                        evidence_ids=[overlay_evidence_id],
                        metadata={
                            "decision_id": decision_id,
                            "baseline_signal_id": baseline.signal_id,
                            "policy_name": self.config.policy_name,
                            "activation_state": activation_state.value,
                            "live_application_authorized": True,
                        },
                    )
                )

        return AgentResult(
            status=AgentStatus.SUCCESS,
            evidence=overlay_evidence,
            signals=applied_signals,
            decisions=decisions,
            metadata={
                "policy_name": self.config.policy_name,
                "policy_version": self.config.policy_version,
                "decisions": len(decisions),
                "suppressed_proposals": suppressed,
                "applied_decisions": len(applied_signals),
                "activation_state": activation_state.value,
                "live_application_authorized": live_authorized,
                "shadow_mode": context.shadow_mode,
            },
            state_updates={
                "stage4_decisions": decisions,
                "stage4_applied_signals": applied_signals,
            },
        )
