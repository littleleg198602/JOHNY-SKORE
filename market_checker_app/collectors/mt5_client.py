from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

import pandas as pd


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

    def fetch_ohlcv(self, ticker: str, bars: int = 300) -> tuple[pd.DataFrame | None, str | None]:
        try:
            import MetaTrader5 as mt5  # type: ignore
        except Exception as exc:  # pragma: no cover
            return None, f"MT5 import selhal: {exc}"

        if not mt5.initialize():
            return None, "MT5 initialize() selhalo při načítání OHLCV."

        try:
            rates = mt5.copy_rates_from_pos(ticker, mt5.TIMEFRAME_D1, 0, bars)
            if rates is None or len(rates) == 0:
                return None, f"MT5 nevrátil OHLCV data pro {ticker}."
            df = pd.DataFrame(rates)
            if df.empty:
                return None, f"MT5 vrátil prázdný DataFrame pro {ticker}."
            df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
            df = df.rename(
                columns={
                    "open": "Open",
                    "high": "High",
                    "low": "Low",
                    "close": "Close",
                    "tick_volume": "Volume",
                }
            )
            if "Volume" not in df.columns and "real_volume" in df.columns:
                df["Volume"] = df["real_volume"]
            df = df.set_index("time")
            for col in ["Open", "High", "Low", "Close", "Volume"]:
                if col not in df.columns:
                    df[col] = pd.NA
            return df[["Open", "High", "Low", "Close", "Volume"]], None
        except Exception as exc:
            return None, f"MT5 OHLCV načtení selhalo pro {ticker}: {exc}"
        finally:
            mt5.shutdown()

    @staticmethod
    def sanitize_watchlist(raw_symbols: Iterable[str]) -> list[str]:
        return sorted({s.strip().upper() for s in raw_symbols if s and s.strip()})
