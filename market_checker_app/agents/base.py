from __future__ import annotations

from abc import ABC, abstractmethod

from market_checker_app.agents.contracts import AgentContext, AgentResult


class BaseAgent(ABC):
    name: str
    version: str = "1.0"
    required: bool = False
    dependencies: tuple[str, ...] = ()

    @abstractmethod
    def run(self, context: AgentContext) -> AgentResult:
        """Run without mutating external state and return auditable records."""
