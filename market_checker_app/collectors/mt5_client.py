from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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

    def load_open_positions(self) -> tuple[pd.DataFrame, str | None]:
        """Read current MT5 positions without sending or changing any order.

        This adapter intentionally calls only ``positions_get``.  It is kept
        separate from OHLC loading so a portfolio audit cannot accidentally
        acquire an execution capability.
        """
        columns = [
            "position_ticket", "ticker", "side", "volume", "opened_at",
            "entry_price", "current_price", "stop_loss", "take_profit",
            "swap", "profit", "comment",
        ]
        try:
            import MetaTrader5 as mt5  # type: ignore
        except Exception as exc:  # pragma: no cover - environment specific
            return pd.DataFrame(columns=columns), f"MT5 není dostupné pro audit pozic: {exc}"
        if not mt5.initialize():
            return pd.DataFrame(columns=columns), "MT5 initialize() selhalo při čtení otevřených pozic."
        try:
            positions = mt5.positions_get()
            if positions is None:
                return pd.DataFrame(columns=columns), "MT5 positions_get() selhalo nebo nevrátilo data."
            buy_type = int(getattr(mt5, "POSITION_TYPE_BUY", 0))
            rows = []
            for position in positions:
                opened_at = pd.to_datetime(getattr(position, "time", None), unit="s", utc=True, errors="coerce")
                rows.append({
                    "position_ticket": str(getattr(position, "ticket", "")),
                    "ticker": str(getattr(position, "symbol", "")).strip().upper(),
                    "side": "BUY" if int(getattr(position, "type", -1)) == buy_type else "SELL",
                    "volume": float(getattr(position, "volume", 0.0) or 0.0),
                    "opened_at": opened_at.isoformat() if not pd.isna(opened_at) else None,
                    "entry_price": float(getattr(position, "price_open", 0.0) or 0.0),
                    "current_price": float(getattr(position, "price_current", 0.0) or 0.0),
                    "stop_loss": float(getattr(position, "sl", 0.0) or 0.0),
                    "take_profit": float(getattr(position, "tp", 0.0) or 0.0),
                    "swap": float(getattr(position, "swap", 0.0) or 0.0),
                    "profit": float(getattr(position, "profit", 0.0) or 0.0),
                    "comment": str(getattr(position, "comment", "") or ""),
                })
            return pd.DataFrame(rows, columns=columns), None
        except Exception as exc:
            return pd.DataFrame(columns=columns), f"MT5 čtení otevřených pozic selhalo: {exc}"
        finally:
            mt5.shutdown()

    @staticmethod
    def _rates_to_frame(rates: object, ticker: str) -> tuple[pd.DataFrame | None, str | None]:
        if rates is None or len(rates) == 0:  # type: ignore[arg-type]
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

    def fetch_ohlcv_batch(
        self,
        tickers: list[str],
        bars: int = 300,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
        """Load all symbols through one MT5 session.

        MetaTrader initialization is relatively expensive. Keeping one terminal
        connection open makes large Market Watch lists practical and also avoids
        hundreds of repeated initialize/shutdown cycles.
        """
        frames: dict[str, pd.DataFrame] = {}
        errors: dict[str, str] = {}
        if not tickers:
            return frames, errors

        try:
            import MetaTrader5 as mt5  # type: ignore
        except Exception as exc:  # pragma: no cover
            message = f"MT5 import selhal: {exc}"
            return frames, {ticker: message for ticker in tickers}

        if not mt5.initialize():
            message = "MT5 initialize() selhalo při načítání OHLCV."
            return frames, {ticker: message for ticker in tickers}

        try:
            total = len(tickers)
            for completed, ticker in enumerate(tickers, start=1):
                try:
                    rates = mt5.copy_rates_from_pos(ticker, mt5.TIMEFRAME_D1, 0, bars)
                    frame, warning = self._rates_to_frame(rates, ticker)
                    if frame is not None:
                        frames[ticker] = frame
                    if warning:
                        errors[ticker] = warning
                except Exception as exc:
                    errors[ticker] = f"MT5 OHLCV načtení selhalo pro {ticker}: {exc}"
                if progress_callback:
                    progress_callback(completed, total, ticker)
        finally:
            mt5.shutdown()
        return frames, errors

    def fetch_ohlcv(self, ticker: str, bars: int = 300) -> tuple[pd.DataFrame | None, str | None]:
        frames, errors = self.fetch_ohlcv_batch([ticker], bars=bars)
        return frames.get(ticker), errors.get(ticker)

    @staticmethod
    def sanitize_watchlist(raw_symbols: Iterable[str]) -> list[str]:
        return sorted({s.strip().upper() for s in raw_symbols if s and s.strip()})
