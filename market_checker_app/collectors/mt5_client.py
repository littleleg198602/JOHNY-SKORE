from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(slots=True)
class MT5Client:
    """Light wrapper around MetaTrader5 import to keep errors isolated."""

    def load_watchlist(self) -> tuple[list[str], str | None]:
        try:
            import MetaTrader5 as mt5  # type: ignore
        except Exception as exc:  # pragma: no cover - environment specific
            return [], f"MetaTrader5 není dostupné: {exc}"

        if not mt5.initialize():
            return [], "MetaTrader5 initialize() selhalo."

        try:
            symbols = mt5.symbols_get() or []
            watchlist = sorted({s.name for s in symbols if getattr(s, 'visible', True)})
            return watchlist, None
        finally:
            mt5.shutdown()

    @staticmethod
    def sanitize_watchlist(raw_symbols: Iterable[str]) -> list[str]:
        return sorted({s.strip().upper() for s in raw_symbols if s and s.strip()})
