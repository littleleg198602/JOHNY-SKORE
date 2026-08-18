from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
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
class AgentResult:
    status: AgentStatus = AgentStatus.SUCCESS
    entities: list[EntityRecord] = field(default_factory=list)
    documents: list[DocumentRecord] = field(default_factory=list)
    evidence: list[AgentEvidence] = field(default_factory=list)
    signals: list[AgentSignal] = field(default_factory=list)
    quality_checks: list[QualityGateCheck] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    state_updates: dict[str, Any] = field(default_factory=dict)

    @property
    def output_count(self) -> int:
        return (
            len(self.entities)
            + len(self.documents)
            + len(self.evidence)
            + len(self.signals)
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
    def evidence(self) -> list[AgentEvidence]:
        return [item for execution in self.executions for item in execution.result.evidence]

    @property
    def signals(self) -> list[AgentSignal]:
        return [item for execution in self.executions for item in execution.result.signals]

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
