"""Point-in-time, score-free features produced by the agent evidence pipeline.

These values are attached to immutable prediction snapshots and may be used by
shadow candidate models.  They never change the production heuristic signal or
send an order.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Iterable

from market_checker_app.agents.contracts import (
    ClaimStatus,
    GovernanceEventStatus,
    OrchestrationReport,
    RegulatoryContractEventType,
)
from market_checker_app.agents.source_policy import is_primary_confirmation
from market_checker_app.utils.text import normalize_ticker


AGENT_FEATURE_VERSION = "agent_evidence_features_v1"

REGULATORY_DIRECTIONS = {
    RegulatoryContractEventType.CONTRACT_AWARD: 1.0,
    RegulatoryContractEventType.CONTRACT_LOSS: -1.0,
    RegulatoryContractEventType.REGULATORY_APPROVAL: 1.0,
    RegulatoryContractEventType.INVESTIGATION: -1.0,
    RegulatoryContractEventType.SANCTION: -1.0,
    RegulatoryContractEventType.LICENSE_CHANGE: -1.0,
    RegulatoryContractEventType.GRANT: 0.6,
    RegulatoryContractEventType.EARNINGS_BEAT: 0.8,
    RegulatoryContractEventType.EARNINGS_MISS: -0.8,
    RegulatoryContractEventType.GUIDANCE_RAISE: 1.0,
    RegulatoryContractEventType.GUIDANCE_CUT: -1.0,
    RegulatoryContractEventType.BUYBACK: 0.6,
    RegulatoryContractEventType.DIVIDEND_INCREASE: 0.5,
    RegulatoryContractEventType.DIVIDEND_CUT: -0.8,
    RegulatoryContractEventType.MERGER_ACQUISITION: 0.0,
    RegulatoryContractEventType.CAPITAL_RAISE: -0.4,
    RegulatoryContractEventType.DEBT_REFINANCING: 0.0,
    RegulatoryContractEventType.EXECUTIVE_CHANGE: -0.2,
}

RISK_GOVERNANCE_TYPES = {
    "AUDITOR_CHANGE",
    "QUALIFIED_OPINION",
    "RESTATEMENT",
    "MATERIAL_WEAKNESS",
    "EXECUTIVE_RESIGNATION",
    "DIRECTOR_RESIGNATION",
    "RELATED_PARTY_TRANSACTION",
    "STOCK_PLEDGE",
    "DILUTION",
}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value)).strip().upper()


def _freshness_weight(published_at: datetime, as_of: datetime) -> float:
    age_days = max(0.0, (_utc(as_of) - _utc(published_at)).total_seconds() / 86400.0)
    return math.exp(-math.log(2.0) * age_days / 30.0)


def _blank_snapshot(ticker: str, as_of: datetime) -> dict[str, object]:
    return {
        "version": AGENT_FEATURE_VERSION,
        "as_of": _utc(as_of).isoformat(),
        "available_at": _utc(as_of).isoformat(),
        "ticker": ticker,
        "analysis_only": True,
        "regulatory": {
            "event_count": 0,
            "verified_event_count": 0,
            "positive_event_count": 0,
            "adverse_event_count": 0,
            "signed_event_score": 0.0,
            "event_types": [],
        },
        "governance": {"event_count": 0, "risk_event_count": 0},
        "forensics": {
            "evidence_count": 0,
            "risk_score": 0.0,
            "hard_veto_count": 0,
        },
        "claims": {
            "claim_count": 0,
            "corroborated_count": 0,
            "contradicted_count": 0,
            "unresolved_count": 0,
        },
        "missingness": {
            "regulatory_events": True,
            "governance_events": True,
            "forensic_evidence": True,
            "research_claims": True,
        },
        "provenance": {
            "orchestration_id": "",
            "point_in_time": True,
            "future_records_rejected": 0,
        },
    }


def build_agent_feature_snapshots(
    report: OrchestrationReport | None,
    *,
    tickers: Iterable[str],
    as_of: datetime,
) -> dict[str, dict[str, object]]:
    """Normalize agent discoveries into reproducible PIT shadow features."""

    as_of = _utc(as_of)
    normalized = sorted(
        {normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)}
    )
    snapshots = {ticker: _blank_snapshot(ticker, as_of) for ticker in normalized}
    if report is None:
        return snapshots

    documents = {document.document_id: document for document in report.documents}
    future_rejected: dict[str, int] = {ticker: 0 for ticker in normalized}

    for event in report.regulatory_contract_events:
        ticker = normalize_ticker(event.ticker)
        if ticker not in snapshots:
            continue
        if _utc(event.published_at) > as_of:
            future_rejected[ticker] += 1
            continue
        section = snapshots[ticker]["regulatory"]
        assert isinstance(section, dict)
        confidence = max(0.0, min(1.0, float(event.confidence)))
        direction = REGULATORY_DIRECTIONS.get(event.event_type, 0.0)
        weighted = direction * confidence * _freshness_weight(event.published_at, as_of)
        section["event_count"] = int(section["event_count"]) + 1
        section["signed_event_score"] = round(
            float(section["signed_event_score"]) + weighted, 6
        )
        event_types = list(section["event_types"])
        event_types.append(event.event_type.value)
        section["event_types"] = sorted(set(event_types))
        if direction > 0:
            section["positive_event_count"] = int(section["positive_event_count"]) + 1
        elif direction < 0:
            section["adverse_event_count"] = int(section["adverse_event_count"]) + 1
        document = documents.get(event.document_id)
        content_supported = bool(
            document
            and isinstance(document.metadata, dict)
            and document.metadata.get("source_content_support_detected")
        )
        if document is not None and (content_supported or is_primary_confirmation(document)):
            section["verified_event_count"] = int(section["verified_event_count"]) + 1

    for event in report.governance_events:
        ticker = normalize_ticker(event.ticker)
        if ticker not in snapshots:
            continue
        if _utc(event.published_at) > as_of:
            future_rejected[ticker] += 1
            continue
        if event.status not in {
            GovernanceEventStatus.VERIFIED,
            GovernanceEventStatus.RESOLVED,
        }:
            continue
        section = snapshots[ticker]["governance"]
        assert isinstance(section, dict)
        section["event_count"] = int(section["event_count"]) + 1
        if _enum_value(event.event_type) in RISK_GOVERNANCE_TYPES:
            section["risk_event_count"] = int(section["risk_event_count"]) + 1

    for evidence in report.evidence:
        ticker = normalize_ticker(evidence.ticker)
        if ticker not in snapshots or evidence.agent_name != "financial_forensics":
            continue
        if _utc(evidence.observed_at) > as_of:
            future_rejected[ticker] += 1
            continue
        section = snapshots[ticker]["forensics"]
        assert isinstance(section, dict)
        section["evidence_count"] = int(section["evidence_count"]) + 1
        section["risk_score"] = max(
            float(section["risk_score"]), float(evidence.risk_score)
        )
        if evidence.hard_veto:
            section["hard_veto_count"] = int(section["hard_veto_count"]) + 1

    for claim in report.claims:
        ticker = normalize_ticker(claim.ticker)
        if ticker not in snapshots:
            continue
        if _utc(claim.published_at) > as_of:
            future_rejected[ticker] += 1
            continue
        section = snapshots[ticker]["claims"]
        assert isinstance(section, dict)
        section["claim_count"] = int(section["claim_count"]) + 1
        if claim.status == ClaimStatus.CORROBORATED:
            section["corroborated_count"] = int(section["corroborated_count"]) + 1
        elif claim.status == ClaimStatus.CONTRADICTED:
            section["contradicted_count"] = int(section["contradicted_count"]) + 1
        else:
            section["unresolved_count"] = int(section["unresolved_count"]) + 1

    for ticker, snapshot in snapshots.items():
        regulatory = snapshot["regulatory"]
        governance = snapshot["governance"]
        forensics = snapshot["forensics"]
        claims = snapshot["claims"]
        missingness = snapshot["missingness"]
        provenance = snapshot["provenance"]
        assert all(
            isinstance(item, dict)
            for item in (
                regulatory,
                governance,
                forensics,
                claims,
                missingness,
                provenance,
            )
        )
        missingness.update(
            {
                "regulatory_events": int(regulatory["event_count"]) == 0,
                "governance_events": int(governance["event_count"]) == 0,
                "forensic_evidence": int(forensics["evidence_count"]) == 0,
                "research_claims": int(claims["claim_count"]) == 0,
            }
        )
        provenance.update(
            {
                "orchestration_id": report.orchestration_id,
                "future_records_rejected": future_rejected[ticker],
            }
        )
        snapshot["available_feature_count"] = sum(
            not bool(value) for value in missingness.values()
        )
    return snapshots
