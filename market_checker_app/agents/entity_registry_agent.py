from __future__ import annotations

from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.contracts import AgentContext, AgentResult, EntityRecord
from market_checker_app.utils.symbols import normalize_yahoo_symbol
from market_checker_app.utils.text import normalize_ticker


class EntityRegistryAgent(BaseAgent):
    name = "entity_registry"
    version = "1.0"
    required = True

    def run(self, context: AgentContext) -> AgentResult:
        entities: list[EntityRecord] = []
        seen: set[str] = set()
        aliases_by_ticker: dict[str, list[str]] = {}

        for raw_ticker in context.watchlist:
            ticker = normalize_ticker(raw_ticker)
            if not ticker:
                continue
            aliases = aliases_by_ticker.setdefault(ticker, [])
            raw_clean = str(raw_ticker).strip()
            if raw_clean and raw_clean != ticker and raw_clean not in aliases:
                aliases.append(raw_clean)
            if ticker in seen:
                continue
            seen.add(ticker)
            entities.append(
                EntityRecord(
                    entity_id=f"ticker:{ticker}",
                    ticker=ticker,
                    yahoo_ticker=normalize_yahoo_symbol(ticker),
                    aliases=aliases,
                    metadata={"registry_stage": 1},
                )
            )

        by_ticker = {entity.ticker: entity for entity in entities}
        return AgentResult(
            entities=entities,
            metadata={"unique_entities": len(entities)},
            state_updates={"entities_by_ticker": by_ticker},
        )
