from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any

import pandas as pd
import yfinance as yf

from market_checker_app.models import PerformanceSnapshot, YahooSnapshot


class YahooClient:
    """Small resilient wrapper around yfinance.

    A single one-year history is reused for performance and technical analysis.
    Results are cached across Streamlit reruns and short-lived Yahoo throttling is
    handled without repeatedly hammering the service for every ticker.
    """

    _cache: dict[
        str,
        tuple[
            float,
            YahooSnapshot,
            PerformanceSnapshot,
            pd.DataFrame | None,
            str | None,
            str | None,
        ],
    ] = {}
    _rate_limited_until: float = 0.0

    def __init__(
        self,
        cache_ttl_seconds: int = 15 * 60,
        retry_attempts: int = 2,
        retry_delay_seconds: float = 1.0,
    ) -> None:
        self.cache_ttl_seconds = max(30, cache_ttl_seconds)
        self.retry_attempts = max(1, retry_attempts)
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)

    @staticmethod
    def _return_from_history(hist: pd.DataFrame | None, days: int) -> float | None:
        if hist is None or hist.empty or "Close" not in hist.columns:
            return None
        close = pd.to_numeric(hist["Close"], errors="coerce").dropna()
        if len(close) <= days:
            return None
        latest = float(close.iloc[-1])
        base = float(close.iloc[-(days + 1)])
        if base == 0:
            return None
        return ((latest / base) - 1) * 100

    @staticmethod
    def _is_transient_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(
            marker in message
            for marker in (
                "too many requests",
                "rate limit",
                "429",
                "timeout",
                "timed out",
                "temporarily unavailable",
                "connection",
            )
        )

    @staticmethod
    def _is_rate_limit_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return "too many requests" in message or "rate limit" in message or "429" in message

    def _call_with_retry(self, operation: Callable[[], Any]) -> Any:
        remaining_pause = type(self)._rate_limited_until - time.monotonic()
        if remaining_pause > 0:
            raise RuntimeError(f"Yahoo je po omezení požadavků v ochranné pauze ještě {remaining_pause:.0f} s")

        last_error: Exception | None = None
        for attempt in range(self.retry_attempts):
            try:
                return operation()
            except Exception as exc:  # yfinance exposes several backend exception types
                last_error = exc
                can_retry = self._is_transient_error(exc) and attempt + 1 < self.retry_attempts
                if can_retry:
                    time.sleep(self.retry_delay_seconds * (2**attempt))
                    continue
                if self._is_rate_limit_error(exc):
                    type(self)._rate_limited_until = time.monotonic() + 60
                break

        assert last_error is not None
        raise last_error

    @staticmethod
    def _copy_history(history: pd.DataFrame | None) -> pd.DataFrame | None:
        return history.copy() if isinstance(history, pd.DataFrame) else None

    def _fetch_bundle(
        self, ticker: str
    ) -> tuple[YahooSnapshot, PerformanceSnapshot, pd.DataFrame | None, str | None, str | None]:
        cache_key = ticker.strip().upper()
        cached = type(self)._cache.get(cache_key)
        now = time.monotonic()
        if cached and cached[0] > now:
            _, snapshot, performance, history, metadata_warning, history_warning = cached
            return (
                YahooSnapshot(snapshot.ticker, dict(snapshot.data), snapshot.status),
                performance,
                self._copy_history(history),
                metadata_warning,
                history_warning,
            )

        tk = yf.Ticker(cache_key)
        metadata_warning: str | None = None
        history_warning: str | None = None

        try:
            info = self._call_with_retry(lambda: tk.info)
            if not isinstance(info, dict) or not info:
                raise ValueError("Yahoo vrátil prázdná metadata")
            snapshot = YahooSnapshot(ticker=cache_key, data=info, status="ok")
        except Exception as exc:
            snapshot = YahooSnapshot(ticker=cache_key, data={}, status="fallback")
            metadata_warning = f"Yahoo metadata nejsou dostupná pro {cache_key}. Detail: {exc}"

        history: pd.DataFrame | None
        try:
            raw_history = self._call_with_retry(
                lambda: tk.history(period="1y", interval="1d", auto_adjust=False)
            )
            if raw_history is None or raw_history.empty:
                raise ValueError("Yahoo vrátil prázdnou cenovou historii")
            history = raw_history
        except Exception as exc:
            history = None
            history_warning = f"Yahoo cenová historie není dostupná pro {cache_key}. Detail: {exc}"

        performance = PerformanceSnapshot(
            ticker=cache_key,
            last_week_change_pct=self._return_from_history(history, 7),
            last_14d_change_pct=self._return_from_history(history, 14),
            last_1m_change_pct=self._return_from_history(history, 21),
            last_3m_change_pct=self._return_from_history(history, 63),
        )

        # Successful data can be reused for a full UI session. Failed responses
        # are cached briefly to prevent dozens of immediate rate-limited calls.
        ttl = self.cache_ttl_seconds if snapshot.status == "ok" and history is not None else 30
        type(self)._cache[cache_key] = (
            time.monotonic() + ttl,
            snapshot,
            performance,
            self._copy_history(history),
            metadata_warning,
            history_warning,
        )
        return snapshot, performance, history, metadata_warning, history_warning

    def fetch_snapshots(self, ticker: str) -> tuple[YahooSnapshot, PerformanceSnapshot, str | None]:
        snapshot, performance, _, metadata_warning, history_warning = self._fetch_bundle(ticker)
        warnings = [warning for warning in (metadata_warning, history_warning) if warning]
        return snapshot, performance, " | ".join(warnings) if warnings else None

    def fetch_ohlc(
        self, ticker: str, period: str = "1y", interval: str = "1d"
    ) -> tuple[pd.DataFrame | None, str | None]:
        if period == "1y" and interval == "1d":
            _, _, history, _, history_warning = self._fetch_bundle(ticker)
            return self._copy_history(history), history_warning

        try:
            history = self._call_with_retry(
                lambda: yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
            )
            if history is None or history.empty:
                return history, f"OHLC data pro {ticker} nejsou na Yahoo dostupná."
            return history, None
        except Exception as exc:
            return None, f"Stažení OHLC pro {ticker} selhalo: {exc}"

    def fetch_ohlc_only(
        self, ticker: str, period: str = "1y", interval: str = "1d"
    ) -> tuple[pd.DataFrame | None, str | None]:
        """Fetch history without the expensive Yahoo metadata endpoint."""
        try:
            history = self._call_with_retry(
                lambda: yf.Ticker(ticker).history(
                    period=period,
                    interval=interval,
                    auto_adjust=False,
                )
            )
            if history is None or history.empty:
                return history, f"OHLC data pro {ticker} nejsou na Yahoo dostupná."
            return history, None
        except Exception as exc:
            return None, f"Stažení OHLC pro {ticker} selhalo: {exc}"
