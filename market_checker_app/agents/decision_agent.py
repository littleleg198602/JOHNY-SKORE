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
    DocumentRecord,
    RegulatoryContractEventType,
    RegulatoryEventStatus,
    ResearchClaim,
    SignalActivationDecision,
    utc_now,
)
from market_checker_app.agents.source_policy import is_primary_confirmation
from market_checker_app.config import DecisionAgentConfig


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


class DecisionAgent(BaseAgent):
    """Build a conservative, auditable analytical overlay over the v2.1 decision.

    The policy can retain a baseline action or propose suppression of BUY/SELL
    to NO_TRADE. It never executes, applies, or routes an order; all outputs
    remain analytical records.
    """

    name = "decision_agent"
    version = "1.1"
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
    def _agent_documents(context: AgentContext) -> dict[str, DocumentRecord]:
        results = context.state.get("agent_results")
        if not isinstance(results, dict):
            return {}
        return {
            document.document_id: document
            for result in results.values()
            if isinstance(result, AgentResult)
            for document in result.documents
        }

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
        documents_by_id = self._agent_documents(context)
        preferred_documents = context.state.get(
            "preferred_documents_by_canonical_event_key"
        )
        preferred_documents = (
            preferred_documents if isinstance(preferred_documents, dict) else {}
        )
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
        governance_by_ticker = context.state.get("governance_events_by_ticker")
        governance_by_ticker = governance_by_ticker if isinstance(governance_by_ticker, dict) else {}
        supply_chain_by_ticker = context.state.get("supply_chain_relationships_by_ticker")
        supply_chain_by_ticker = supply_chain_by_ticker if isinstance(supply_chain_by_ticker, dict) else {}
        resource_by_ticker = context.state.get("resource_exposures_by_ticker")
        resource_by_ticker = resource_by_ticker if isinstance(resource_by_ticker, dict) else {}
        activation_state = self._activation_state(context)

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
                    and (
                        (document := documents_by_id.get(
                            str(getattr(event, "document_id", ""))
                        ))
                        is not None
                    )
                    and is_primary_confirmation(document)
                    and (
                        not document.canonical_event_key
                        or preferred_documents.get(document.canonical_event_key)
                        in (None, document.document_id)
                    )
                    and bool(getattr(event, "legal_entity_id", None))
                    and getattr(event, "legal_entity_id", None)
                    == document.legal_entity_id
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

            def enum_value(value: object) -> str:
                return str(getattr(value, "value", value))

            if self.config.governance_component_enabled:
                governance_events = [
                    event for event in governance_by_ticker.get(ticker, [])
                    if enum_value(getattr(event, "status", "")) in {"VERIFIED", "RESOLVED"}
                    and float(getattr(event, "confidence", 0.0)) >= self.config.minimum_claim_confidence
                    and getattr(event, "published_at", observed_at) <= context.started_at
                ]
                high_governance_types = {
                    "AUDITOR_CHANGE", "QUALIFIED_OPINION", "RESTATEMENT",
                    "MATERIAL_WEAKNESS", "EXECUTIVE_RESIGNATION",
                    "DIRECTOR_RESIGNATION", "RELATED_PARTY_TRANSACTION",
                    "STOCK_PLEDGE", "DILUTION",
                }
                high_governance = [
                    event for event in governance_events
                    if enum_value(getattr(event, "event_type", "")) in high_governance_types
                ]
                if governance_events:
                    component = min(4.0, len(high_governance) * 1.25 + (len(governance_events) - len(high_governance)) * 0.35)
                    risk_components["governance_events"] = component
                    reasons.append(f"governance_events={len(governance_events)}")
                    event_ids = {str(getattr(event, "event_id", "")) for event in governance_events}
                    linked_evidence_ids.extend(
                        item.evidence_id for item in evidence_by_ticker.get(ticker, [])
                        if item.agent_name == "governance_event"
                        and (
                            item.metadata.get("governance_event_id") in event_ids
                            or item.metadata.get("event_id") in event_ids
                        )
                    )

            if self.config.supply_chain_component_enabled:
                relationships = [
                    item for item in supply_chain_by_ticker.get(ticker, [])
                    if float(getattr(item, "confidence", 0.0)) >= self.config.minimum_claim_confidence
                ]
                weighted = sum(
                    1.0 if getattr(item, "dependency_pct", None) is not None and float(item.dependency_pct) >= 40.0 else 0.35
                    for item in relationships
                )
                if weighted:
                    risk_components["supply_chain_relationships"] = min(3.0, weighted)
                    reasons.append(f"supply_chain_relationships={len(relationships)}")
                    relationship_ids = {str(getattr(item, "relationship_id", "")) for item in relationships}
                    linked_evidence_ids.extend(
                        item.evidence_id for item in evidence_by_ticker.get(ticker, [])
                        if item.agent_name == "supply_chain"
                        and item.metadata.get("relationship_id") in relationship_ids
                    )

            if self.config.resource_component_enabled:
                exposures = [
                    item for item in resource_by_ticker.get(ticker, [])
                    if float(getattr(item, "confidence", 0.0)) >= self.config.minimum_claim_confidence
                ]
                weighted = sum(
                    1.0 if getattr(item, "dependency_pct", None) is not None and float(item.dependency_pct) >= 40.0 else 0.35
                    for item in exposures
                )
                if weighted:
                    risk_components["resource_exposures"] = min(3.0, weighted)
                    reasons.append(f"resource_exposures={len(exposures)}")
                    exposure_ids = {str(getattr(item, "exposure_id", "")) for item in exposures}
                    linked_evidence_ids.extend(
                        item.evidence_id for item in evidence_by_ticker.get(ticker, [])
                        if item.agent_name == "commodity_energy"
                        and item.metadata.get("exposure_id") in exposure_ids
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
                            "scoring_applied": False,
                            "shadow_mode": context.shadow_mode,
                        },
                    )
                )
                linked_evidence_ids.append(overlay_evidence_id)

            applied = False
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
                    "analysis_only": True,
                    "shadow_mode": context.shadow_mode,
                    "baseline_agent": baseline.agent_name,
                },
            )
            decisions.append(decision)

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
                "applied_decisions": 0,
                "activation_state": activation_state.value,
                "analysis_only": True,
                "shadow_mode": context.shadow_mode,
            },
            state_updates={
                "stage4_decisions": decisions,
                "stage4_applied_signals": applied_signals,
            },
        )
