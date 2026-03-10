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
            return [], (
                "MT5 není dostupné v tomto prostředí (import MetaTrader5 selhal). "
                f"Detail: {exc}. Zkontroluj instalaci balíčku, bit verzi Pythonu a dostupnost terminálu."
            )

        if not mt5.initialize():
            return [], (
                "MT5 initialize() selhalo. Ověř, že je spuštěný MetaTrader terminál, "
                "povolené API připojení a účet je přihlášen."
            )

        try:
            symbols = mt5.symbols_get() or []
            watchlist = sorted({s.name for s in symbols if getattr(s, "visible", True)})
            if not watchlist:
                return [], (
                    "MT5 vrátil prázdný seznam symbolů. Zkontroluj Market Watch ve tvém terminálu "
                    "a viditelnost instrumentů."
                )
            return watchlist, None
        except Exception as exc:
            return [], f"MT5 načtení watchlistu selhalo: {exc}"
        finally:
            mt5.shutdown()

    @staticmethod
    def sanitize_watchlist(raw_symbols: Iterable[str]) -> list[str]:
        return sorted({s.strip().upper() for s in raw_symbols if s and s.strip()})
