from __future__ import annotations

import time
from uuid import uuid4

from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.contracts import (
    AgentContext,
    AgentExecution,
    AgentResult,
    AgentStatus,
    OrchestrationReport,
    utc_now,
)


class OrchestratorAgent:
    """Deterministic dependency runner for analytical agents.

    Stage 1 intentionally runs agents sequentially.  The dependency graph and
    immutable execution records are established now; independent layers can be
    parallelised later without changing the agent contract.
    """

    def __init__(self, *, shadow_mode: bool = True) -> None:
        self.shadow_mode = shadow_mode
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        if not agent.name:
            raise ValueError("Agent name must not be empty")
        if agent.name in self._agents:
            raise ValueError(f"Duplicate agent name: {agent.name}")
        self._agents[agent.name] = agent

    def _ordered_agents(self) -> list[BaseAgent]:
        for agent in self._agents.values():
            unknown = set(agent.dependencies).difference(self._agents)
            if unknown:
                raise ValueError(
                    f"Agent {agent.name} has unknown dependencies: {sorted(unknown)}"
                )

        ordered: list[BaseAgent] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            if name in visiting:
                raise ValueError(f"Agent dependency cycle detected at {name}")
            visiting.add(name)
            for dependency in self._agents[name].dependencies:
                visit(dependency)
            visiting.remove(name)
            visited.add(name)
            ordered.append(self._agents[name])

        for name in self._agents:
            visit(name)
        return ordered

    def run(
        self,
        *,
        watchlist: list[str] | tuple[str, ...],
        state: dict[str, object] | None = None,
        pipeline_run_id: int | None = None,
        orchestration_id: str | None = None,
    ) -> OrchestrationReport:
        started_at = utc_now()
        context = AgentContext(
            orchestration_id=orchestration_id or uuid4().hex,
            watchlist=tuple(watchlist),
            started_at=started_at,
            pipeline_run_id=pipeline_run_id,
            shadow_mode=self.shadow_mode,
            state=dict(state or {}),
        )
        executions: list[AgentExecution] = []
        status_by_agent: dict[str, AgentStatus] = {}

        for agent in self._ordered_agents():
            agent_started = utc_now()
            timer_started = time.perf_counter()
            blocked_by = [
                dependency
                for dependency in agent.dependencies
                if status_by_agent.get(dependency)
                not in {AgentStatus.SUCCESS, AgentStatus.PARTIAL}
            ]
            if blocked_by:
                result = AgentResult(
                    status=AgentStatus.BLOCKED,
                    error=f"Blocked by dependencies: {', '.join(blocked_by)}",
                )
            else:
                try:
                    result = agent.run(context)
                    if not isinstance(result, AgentResult):
                        raise TypeError(
                            f"Agent {agent.name} returned {type(result).__name__}, expected AgentResult"
                        )
                except Exception as exc:  # an agent failure must be an auditable result
                    result = AgentResult(
                        status=AgentStatus.FAILED,
                        error=f"{type(exc).__name__}: {exc}",
                    )

            if result.state_updates:
                context.state.update(result.state_updates)
            agent_results = context.state.get("agent_results")
            if not isinstance(agent_results, dict):
                agent_results = {}
                context.state["agent_results"] = agent_results
            agent_results[agent.name] = result
            elapsed_ms = (time.perf_counter() - timer_started) * 1000.0
            execution = AgentExecution(
                agent_name=agent.name,
                agent_version=agent.version,
                required=agent.required,
                dependencies=agent.dependencies,
                started_at=agent_started,
                finished_at=utc_now(),
                elapsed_ms=elapsed_ms,
                input_count=len(context.watchlist),
                result=result,
            )
            executions.append(execution)
            status_by_agent[agent.name] = result.status

        required_failed = any(
            execution.required
            and execution.status in {AgentStatus.FAILED, AgentStatus.BLOCKED, AgentStatus.UNAVAILABLE}
            for execution in executions
        )
        any_degraded = any(
            execution.status != AgentStatus.SUCCESS for execution in executions
        )
        overall = (
            AgentStatus.FAILED
            if required_failed
            else (AgentStatus.PARTIAL if any_degraded else AgentStatus.SUCCESS)
        )
        return OrchestrationReport(
            orchestration_id=context.orchestration_id,
            started_at=started_at,
            finished_at=utc_now(),
            status=overall,
            shadow_mode=self.shadow_mode,
            watchlist_size=len(context.watchlist),
            executions=executions,
            pipeline_run_id=pipeline_run_id,
            metadata={
                "registered_agents": list(self._agents),
                "state_keys": sorted(str(key) for key in context.state),
            },
        )
