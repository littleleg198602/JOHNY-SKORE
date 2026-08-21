from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, IntEnum
import math
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _bounded(value: float, low: float, high: float, label: str) -> float:
    numeric = float(value)
    if not low <= numeric <= high:
        raise ValueError(f"{label} must be between {low} and {high}; got {numeric}")
    return numeric


class AgentStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class GateDecision(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    REJECT = "REJECT"


class ActivationState(str, Enum):
    SHADOW = "SHADOW"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    REJECTED = "REJECTED"
    ELIGIBLE = "ELIGIBLE"
    ENABLED = "ENABLED"


class ClaimStatus(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    CORROBORATED = "CORROBORATED"
    CONTRADICTED = "CONTRADICTED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class RelationshipType(str, Enum):
    SUPPLIER = "SUPPLIER"
    CUSTOMER = "CUSTOMER"
    CONTRACT_MANUFACTURER = "CONTRACT_MANUFACTURER"
    LOGISTICS = "LOGISTICS"
    PARTNER = "PARTNER"


class ResourceExposureType(str, Enum):
    MATERIAL_INPUT = "MATERIAL_INPUT"
    COMMODITY_OUTPUT = "COMMODITY_OUTPUT"
    ELECTRICITY = "ELECTRICITY"
    FUEL = "FUEL"


class RegulatoryContractEventType(str, Enum):
    CONTRACT_AWARD = "CONTRACT_AWARD"
    CONTRACT_LOSS = "CONTRACT_LOSS"
    REGULATORY_APPROVAL = "REGULATORY_APPROVAL"
    INVESTIGATION = "INVESTIGATION"
    SANCTION = "SANCTION"
    LICENSE_CHANGE = "LICENSE_CHANGE"
    GRANT = "GRANT"


class RegulatoryEventStatus(str, Enum):
    ANNOUNCED = "ANNOUNCED"
    ACTIVE = "ACTIVE"
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    SUSPENDED = "SUSPENDED"
    UNKNOWN = "UNKNOWN"


class DocumentSourcePriority(IntEnum):
    """Deterministic trust order; a larger value is a stronger source."""

    UNKNOWN = 0
    MEDIA_ARTICLE = 100
    MANAGEMENT_PRESENTATION = 200
    INVESTOR_RELATIONS = 300
    EXCHANGE_ANNOUNCEMENT = 400
    AUDITED_FINANCIAL_STATEMENT = 500
    REGULATORY_FILING = 600


class IdentityConflictStatus(str, Enum):
    QUARANTINED = "QUARANTINED"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class GovernanceEventType(str, Enum):
    INSIDER_TRADE = "INSIDER_TRADE"
    BENEFICIAL_OWNERSHIP_CHANGE = "BENEFICIAL_OWNERSHIP_CHANGE"
    AUDITOR_CHANGE = "AUDITOR_CHANGE"
    QUALIFIED_OPINION = "QUALIFIED_OPINION"
    RESTATEMENT = "RESTATEMENT"
    MATERIAL_WEAKNESS = "MATERIAL_WEAKNESS"
    EXECUTIVE_RESIGNATION = "EXECUTIVE_RESIGNATION"
    DIRECTOR_RESIGNATION = "DIRECTOR_RESIGNATION"
    RELATED_PARTY_TRANSACTION = "RELATED_PARTY_TRANSACTION"
    STOCK_PLEDGE = "STOCK_PLEDGE"
    DILUTION = "DILUTION"
    STOCK_COMPENSATION = "STOCK_COMPENSATION"


class GovernanceEventStatus(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    DISPUTED = "DISPUTED"
    RESOLVED = "RESOLVED"


@dataclass(slots=True)
class QualityGateCheck:
    check_id: str
    gate_name: str
    decision: GateDecision
    observed_at: datetime
    message: str
    ticker: str | None = None
    related_agent_names: list[str] = field(default_factory=list)
    signal_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    claim_ids: list[str] = field(default_factory=list)
    relationship_ids: list[str] = field(default_factory=list)
    exposure_ids: list[str] = field(default_factory=list)
    regulatory_event_ids: list[str] = field(default_factory=list)
    decision_ids: list[str] = field(default_factory=list)
    evaluation_ids: list[str] = field(default_factory=list)
    activation_ids: list[str] = field(default_factory=list)
    identity_conflict_ids: list[str] = field(default_factory=list)
    governance_event_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EntityRecord:
    entity_id: str
    ticker: str
    yahoo_ticker: str | None = None
    name: str | None = None
    exchange: str | None = None
    cik: str | None = None
    isin: str | None = None
    lei: str | None = None
    sector: str | None = None
    industry: str | None = None
    aliases: list[str] = field(default_factory=list)
    source: str = "entity_registry"
    metadata: dict[str, Any] = field(default_factory=dict)
    legal_entity_id: str | None = None
    issuer_id: str | None = None
    instrument_id: str | None = None
    parent_entity_id: str | None = None
    security_type: str | None = None
    share_class: str | None = None
    mic: str | None = None
    country_code: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    source_url: str | None = None
    confidence: float = 0.0

    def __post_init__(self) -> None:
        self.confidence = _bounded(self.confidence, 0.0, 1.0, "confidence")
        for field_name in ("valid_from", "valid_to"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if value.tzinfo is None or value.utcoffset() is None:
                value = value.replace(tzinfo=timezone.utc)
            else:
                value = value.astimezone(timezone.utc)
            setattr(self, field_name, value)
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to <= self.valid_from
        ):
            raise ValueError("valid_to must be later than valid_from")


@dataclass(slots=True)
class IdentityConflictRecord:
    conflict_id: str
    ticker: str
    entity_id: str
    field_name: str
    existing_value: str
    candidate_value: str
    existing_source: str
    candidate_source: str
    observed_at: datetime
    status: IdentityConflictStatus = IdentityConflictStatus.QUARANTINED
    legal_entity_id: str | None = None
    existing_source_url: str | None = None
    candidate_source_url: str | None = None
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.status, IdentityConflictStatus):
            self.status = IdentityConflictStatus(str(self.status).strip().upper())
        self.field_name = str(self.field_name).strip().lower()
        if self.field_name not in {"cik", "isin", "lei", "legal_entity_id"}:
            raise ValueError(f"Unsupported identity conflict field: {self.field_name}")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            self.observed_at = self.observed_at.replace(tzinfo=timezone.utc)
        else:
            self.observed_at = self.observed_at.astimezone(timezone.utc)


@dataclass(slots=True)
class DocumentRecord:
    document_id: str
    ticker: str
    source: str
    source_type: str
    observed_at: datetime
    url: str | None = None
    published_at: datetime | None = None
    content_hash: str | None = None
    mime_type: str | None = None
    raw_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    source_priority: int | None = None
    source_authority: str | None = None
    legal_entity_id: str | None = None
    issuer_id: str | None = None
    instrument_id: str | None = None
    reporting_period_end: datetime | None = None
    is_audited: bool = False
    language: str | None = None

    def __post_init__(self) -> None:
        priority_by_type = {
            "regulatory_filing": DocumentSourcePriority.REGULATORY_FILING,
            "audited_financial_statement": (
                DocumentSourcePriority.AUDITED_FINANCIAL_STATEMENT
            ),
            "exchange_announcement": DocumentSourcePriority.EXCHANGE_ANNOUNCEMENT,
            "investor_relations": DocumentSourcePriority.INVESTOR_RELATIONS,
            "management_presentation": (
                DocumentSourcePriority.MANAGEMENT_PRESENTATION
            ),
            "media_article": DocumentSourcePriority.MEDIA_ARTICLE,
        }
        if self.source_priority is None:
            self.source_priority = int(
                priority_by_type.get(
                    str(self.source_type).strip().lower(),
                    DocumentSourcePriority.UNKNOWN,
                )
            )
        else:
            self.source_priority = int(self.source_priority)
        if not 0 <= self.source_priority <= int(DocumentSourcePriority.REGULATORY_FILING):
            raise ValueError("source_priority must be between 0 and 600")
        for field_name in ("observed_at", "published_at", "reporting_period_end"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if value.tzinfo is None or value.utcoffset() is None:
                value = value.replace(tzinfo=timezone.utc)
            else:
                value = value.astimezone(timezone.utc)
            setattr(self, field_name, value)


@dataclass(slots=True)
class GovernanceEvent:
    event_id: str
    ticker: str
    event_type: GovernanceEventType
    status: GovernanceEventStatus
    title: str
    observed_at: datetime
    published_at: datetime
    document_id: str
    source_url: str
    legal_entity_id: str
    confidence: float = 0.0
    source_agent_name: str = "governance_event"
    actor: str | None = None
    transaction_type: str | None = None
    shares: float | None = None
    price_per_share: float | None = None
    event_value: float | None = None
    currency: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, GovernanceEventType):
            self.event_type = GovernanceEventType(str(self.event_type).strip().upper())
        if not isinstance(self.status, GovernanceEventStatus):
            self.status = GovernanceEventStatus(str(self.status).strip().upper())
        self.confidence = _bounded(self.confidence, 0.0, 1.0, "confidence")
        for field_name in ("shares", "price_per_share", "event_value"):
            value = getattr(self, field_name)
            if value is None:
                continue
            numeric = float(value)
            if not math.isfinite(numeric) or numeric < 0.0:
                raise ValueError(f"{field_name} must be a non-negative finite number")
            setattr(self, field_name, numeric)
        self.currency = (
            str(self.currency).strip().upper() or None
            if self.currency
            else None
        )
        for field_name in ("observed_at", "published_at"):
            value = getattr(self, field_name)
            if value.tzinfo is None or value.utcoffset() is None:
                value = value.replace(tzinfo=timezone.utc)
            else:
                value = value.astimezone(timezone.utc)
            setattr(self, field_name, value)


@dataclass(slots=True)
class FundamentalFact:
    fact_id: str
    ticker: str
    cik: str
    taxonomy: str
    concept: str
    label: str
    description: str
    unit: str
    value: float
    observed_at: datetime
    filed_at: datetime
    form: str
    accession_number: str
    source_url: str
    document_id: str
    period_start: datetime | None = None
    period_end: datetime | None = None
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    frame: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResearchClaim:
    claim_id: str
    ticker: str
    report_document_id: str
    claim_type: str
    statement: str
    status: ClaimStatus
    observed_at: datetime
    published_at: datetime
    confidence: float = 0.0
    source_agent_name: str = "short_report"
    verification_agent_name: str | None = None
    verification_summary: str = ""
    evidence_document_ids: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.confidence = _bounded(self.confidence, 0.0, 1.0, "confidence")


@dataclass(slots=True)
class CompanyRelationship:
    relationship_id: str
    ticker: str
    counterparty: str
    relationship_type: RelationshipType
    observed_at: datetime
    published_at: datetime
    document_id: str
    source_url: str
    dependency_pct: float | None = None
    confidence: float = 1.0
    source_agent_name: str = "supply_chain"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.relationship_type, RelationshipType):
            self.relationship_type = RelationshipType(
                str(self.relationship_type).strip().upper()
            )
        if self.dependency_pct is not None:
            self.dependency_pct = _bounded(
                self.dependency_pct,
                0.0,
                100.0,
                "dependency_pct",
            )
        self.confidence = _bounded(self.confidence, 0.0, 1.0, "confidence")


@dataclass(slots=True)
class ResourceExposure:
    exposure_id: str
    ticker: str
    resource_name: str
    exposure_type: ResourceExposureType
    observed_at: datetime
    published_at: datetime
    document_id: str
    source_url: str
    dependency_pct: float | None = None
    confidence: float = 1.0
    source_agent_name: str = "commodity_energy"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.exposure_type, ResourceExposureType):
            self.exposure_type = ResourceExposureType(
                str(self.exposure_type).strip().upper()
            )
        if self.dependency_pct is not None:
            self.dependency_pct = _bounded(
                self.dependency_pct,
                0.0,
                100.0,
                "dependency_pct",
            )
        self.confidence = _bounded(self.confidence, 0.0, 1.0, "confidence")


@dataclass(slots=True)
class RegulatoryContractEvent:
    event_id: str
    ticker: str
    event_type: RegulatoryContractEventType
    status: RegulatoryEventStatus
    title: str
    authority_or_counterparty: str
    observed_at: datetime
    published_at: datetime
    document_id: str
    source_url: str
    event_value: float | None = None
    currency: str | None = None
    confidence: float = 1.0
    source_agent_name: str = "regulatory_contract"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, RegulatoryContractEventType):
            self.event_type = RegulatoryContractEventType(
                str(self.event_type).strip().upper()
            )
        if not isinstance(self.status, RegulatoryEventStatus):
            self.status = RegulatoryEventStatus(str(self.status).strip().upper())
        if self.event_value is not None:
            self.event_value = float(self.event_value)
            if not math.isfinite(self.event_value) or self.event_value < 0.0:
                raise ValueError("event_value must be a non-negative finite number")
        if self.currency is not None:
            self.currency = str(self.currency).strip().upper() or None
        self.confidence = _bounded(self.confidence, 0.0, 1.0, "confidence")


@dataclass(slots=True)
class AgentEvidence:
    evidence_id: str
    ticker: str
    agent_name: str
    event_type: str
    observed_at: datetime
    summary: str
    direction: float = 0.0
    risk_score: float = 0.0
    confidence: float = 0.0
    hard_veto: bool = False
    reasons: list[str] = field(default_factory=list)
    document_ids: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    valid_until: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.direction = _bounded(self.direction, -1.0, 1.0, "direction")
        self.risk_score = _bounded(self.risk_score, 0.0, 100.0, "risk_score")
        self.confidence = _bounded(self.confidence, 0.0, 1.0, "confidence")


@dataclass(slots=True)
class AgentSignal:
    signal_id: str
    ticker: str
    agent_name: str
    agent_version: str
    event_type: str
    observed_at: datetime
    action: str
    forecast: str
    direction: float
    risk_score: float
    confidence: float
    hard_veto: bool = False
    reasons: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    expires_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.direction = _bounded(self.direction, -1.0, 1.0, "direction")
        self.risk_score = _bounded(self.risk_score, 0.0, 100.0, "risk_score")
        self.confidence = _bounded(self.confidence, 0.0, 1.0, "confidence")


@dataclass(slots=True)
class DecisionRecord:
    decision_id: str
    ticker: str
    policy_name: str
    policy_version: str
    observed_at: datetime
    baseline_signal_id: str
    baseline_action: str
    baseline_forecast: str
    proposed_action: str
    proposed_forecast: str
    baseline_p_up: float
    baseline_p_flat: float
    baseline_p_down: float
    p_up: float
    p_flat: float
    p_down: float
    confidence: float
    hard_veto: bool
    activation_state: ActivationState
    applied_to_prediction: bool = False
    reasons: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    claim_ids: list[str] = field(default_factory=list)
    regulatory_event_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.activation_state, ActivationState):
            self.activation_state = ActivationState(
                str(self.activation_state).strip().upper()
            )
        allowed_actions = {"BUY", "SELL", "NO_TRADE"}
        allowed_forecasts = {"UP", "DOWN", "FLAT"}
        if self.baseline_action not in allowed_actions:
            raise ValueError(f"Unsupported baseline_action: {self.baseline_action}")
        if self.proposed_action not in allowed_actions:
            raise ValueError(f"Unsupported proposed_action: {self.proposed_action}")
        if self.baseline_forecast not in allowed_forecasts:
            raise ValueError(f"Unsupported baseline_forecast: {self.baseline_forecast}")
        if self.proposed_forecast not in allowed_forecasts:
            raise ValueError(f"Unsupported proposed_forecast: {self.proposed_forecast}")
        baseline_probabilities = (
            _bounded(self.baseline_p_up, 0.0, 1.0, "baseline_p_up"),
            _bounded(self.baseline_p_flat, 0.0, 1.0, "baseline_p_flat"),
            _bounded(self.baseline_p_down, 0.0, 1.0, "baseline_p_down"),
        )
        probabilities = (
            _bounded(self.p_up, 0.0, 1.0, "p_up"),
            _bounded(self.p_flat, 0.0, 1.0, "p_flat"),
            _bounded(self.p_down, 0.0, 1.0, "p_down"),
        )
        if not math.isclose(sum(baseline_probabilities), 1.0, abs_tol=1e-6):
            raise ValueError("Baseline probabilities must sum to 1")
        if not math.isclose(sum(probabilities), 1.0, abs_tol=1e-6):
            raise ValueError("Decision probabilities must sum to 1")
        (
            self.baseline_p_up,
            self.baseline_p_flat,
            self.baseline_p_down,
        ) = baseline_probabilities
        self.p_up, self.p_flat, self.p_down = probabilities
        self.confidence = _bounded(self.confidence, 0.0, 1.0, "confidence")


@dataclass(slots=True)
class PolicyEvaluation:
    evaluation_id: str
    policy_name: str
    policy_version: str
    observed_at: datetime
    evaluated_through: datetime | None
    sample_count: int
    distinct_weeks: int
    baseline_accuracy_pct: float
    candidate_accuracy_pct: float
    lift_pct_points: float
    lift_lower_bound_pct_points: float
    baseline_false_positive_rate_pct: float
    candidate_false_positive_rate_pct: float
    coverage_pct: float
    baseline_brier_score: float
    candidate_brier_score: float
    baseline_calibration_error: float
    candidate_calibration_error: float
    gate_passed: bool
    gate_results: dict[str, bool] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.sample_count = max(0, int(self.sample_count))
        self.distinct_weeks = max(0, int(self.distinct_weeks))
        for label in (
            "baseline_accuracy_pct",
            "candidate_accuracy_pct",
            "baseline_false_positive_rate_pct",
            "candidate_false_positive_rate_pct",
            "coverage_pct",
        ):
            setattr(self, label, _bounded(getattr(self, label), 0.0, 100.0, label))
        for label in (
            "baseline_brier_score",
            "candidate_brier_score",
            "baseline_calibration_error",
            "candidate_calibration_error",
        ):
            setattr(self, label, _bounded(getattr(self, label), 0.0, 2.0, label))
        for label in ("lift_pct_points", "lift_lower_bound_pct_points"):
            numeric = float(getattr(self, label))
            if not math.isfinite(numeric):
                raise ValueError(f"{label} must be finite")
            setattr(self, label, numeric)


@dataclass(slots=True)
class SignalActivationDecision:
    activation_id: str
    policy_name: str
    policy_version: str
    observed_at: datetime
    evaluated_through: datetime | None
    state: ActivationState
    evaluation_id: str
    sample_count: int
    distinct_weeks: int
    consecutive_passes: int
    gate_passed: bool
    live_application_authorized: bool = False
    reasons: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.state, ActivationState):
            self.state = ActivationState(str(self.state).strip().upper())
        self.sample_count = max(0, int(self.sample_count))
        self.distinct_weeks = max(0, int(self.distinct_weeks))
        self.consecutive_passes = max(0, int(self.consecutive_passes))


@dataclass(slots=True)
class AgentResult:
    status: AgentStatus = AgentStatus.SUCCESS
    entities: list[EntityRecord] = field(default_factory=list)
    identity_conflicts: list[IdentityConflictRecord] = field(default_factory=list)
    documents: list[DocumentRecord] = field(default_factory=list)
    governance_events: list[GovernanceEvent] = field(default_factory=list)
    fundamental_facts: list[FundamentalFact] = field(default_factory=list)
    claims: list[ResearchClaim] = field(default_factory=list)
    company_relationships: list[CompanyRelationship] = field(default_factory=list)
    resource_exposures: list[ResourceExposure] = field(default_factory=list)
    regulatory_contract_events: list[RegulatoryContractEvent] = field(
        default_factory=list
    )
    evidence: list[AgentEvidence] = field(default_factory=list)
    signals: list[AgentSignal] = field(default_factory=list)
    decisions: list[DecisionRecord] = field(default_factory=list)
    policy_evaluations: list[PolicyEvaluation] = field(default_factory=list)
    activation_decisions: list[SignalActivationDecision] = field(default_factory=list)
    quality_checks: list[QualityGateCheck] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    state_updates: dict[str, Any] = field(default_factory=dict)

    @property
    def output_count(self) -> int:
        return (
            len(self.entities)
            + len(self.identity_conflicts)
            + len(self.documents)
            + len(self.governance_events)
            + len(self.fundamental_facts)
            + len(self.claims)
            + len(self.company_relationships)
            + len(self.resource_exposures)
            + len(self.regulatory_contract_events)
            + len(self.evidence)
            + len(self.signals)
            + len(self.decisions)
            + len(self.policy_evaluations)
            + len(self.activation_decisions)
            + len(self.quality_checks)
        )


@dataclass(slots=True)
class AgentContext:
    orchestration_id: str
    watchlist: tuple[str, ...]
    started_at: datetime
    pipeline_run_id: int | None = None
    shadow_mode: bool = True
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentExecution:
    agent_name: str
    agent_version: str
    required: bool
    dependencies: tuple[str, ...]
    started_at: datetime
    finished_at: datetime
    elapsed_ms: float
    input_count: int
    result: AgentResult

    @property
    def status(self) -> AgentStatus:
        return self.result.status


@dataclass(slots=True)
class OrchestrationReport:
    orchestration_id: str
    started_at: datetime
    finished_at: datetime
    status: AgentStatus
    shadow_mode: bool
    watchlist_size: int
    executions: list[AgentExecution] = field(default_factory=list)
    pipeline_run_id: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def entities(self) -> list[EntityRecord]:
        return [item for execution in self.executions for item in execution.result.entities]

    @property
    def documents(self) -> list[DocumentRecord]:
        return [item for execution in self.executions for item in execution.result.documents]

    @property
    def identity_conflicts(self) -> list[IdentityConflictRecord]:
        return [
            item
            for execution in self.executions
            for item in execution.result.identity_conflicts
        ]

    @property
    def governance_events(self) -> list[GovernanceEvent]:
        return [
            item
            for execution in self.executions
            for item in execution.result.governance_events
        ]

    @property
    def evidence(self) -> list[AgentEvidence]:
        return [item for execution in self.executions for item in execution.result.evidence]

    @property
    def fundamental_facts(self) -> list[FundamentalFact]:
        return [
            item
            for execution in self.executions
            for item in execution.result.fundamental_facts
        ]

    @property
    def claims(self) -> list[ResearchClaim]:
        return [item for execution in self.executions for item in execution.result.claims]

    @property
    def company_relationships(self) -> list[CompanyRelationship]:
        return [
            item
            for execution in self.executions
            for item in execution.result.company_relationships
        ]

    @property
    def resource_exposures(self) -> list[ResourceExposure]:
        return [
            item
            for execution in self.executions
            for item in execution.result.resource_exposures
        ]

    @property
    def regulatory_contract_events(self) -> list[RegulatoryContractEvent]:
        return [
            item
            for execution in self.executions
            for item in execution.result.regulatory_contract_events
        ]

    @property
    def signals(self) -> list[AgentSignal]:
        return [item for execution in self.executions for item in execution.result.signals]

    @property
    def decisions(self) -> list[DecisionRecord]:
        return [item for execution in self.executions for item in execution.result.decisions]

    @property
    def policy_evaluations(self) -> list[PolicyEvaluation]:
        return [
            item
            for execution in self.executions
            for item in execution.result.policy_evaluations
        ]

    @property
    def activation_decisions(self) -> list[SignalActivationDecision]:
        return [
            item
            for execution in self.executions
            for item in execution.result.activation_decisions
        ]

    @property
    def quality_checks(self) -> list[QualityGateCheck]:
        return [
            item
            for execution in self.executions
            for item in execution.result.quality_checks
        ]

    @property
    def warnings(self) -> list[str]:
        return [warning for execution in self.executions for warning in execution.result.warnings]

    @property
    def failed_agents(self) -> list[str]:
        return [
            execution.agent_name
            for execution in self.executions
            if execution.status
            in {AgentStatus.FAILED, AgentStatus.BLOCKED, AgentStatus.UNAVAILABLE}
        ]
