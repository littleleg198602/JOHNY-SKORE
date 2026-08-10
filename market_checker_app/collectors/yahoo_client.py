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

    # A Yahoo metadata response is considered usable only when it contains at
    # least METADATA_MIN_USEFUL_FIELDS non-null values from this allow-list.
    # Six or more useful values are classified as a complete (``ok``)
    # response; smaller usable responses are explicitly marked ``partial``.
    # This prevents payloads such as {"symbol": "AAPL"} from looking like
    # valid analyst/fundamental data.
    METADATA_USEFUL_FIELDS: tuple[str, ...] = (
        "currentPrice",
        "regularMarketPrice",
        "targetMeanPrice",
        "targetMedianPrice",
        "recommendationMean",
        "numberOfAnalystOpinions",
        "forwardPE",
        "trailingPE",
        "profitMargins",
        "revenueGrowth",
        "earningsGrowth",
        "debtToEquity",
        "marketCap",
    )
    METADATA_MIN_USEFUL_FIELDS = 2
    METADATA_COMPLETE_USEFUL_FIELDS = 6

    # Yahoo uses hyphens for these US class-share symbols. Do not apply a
    # generic dot/suffix rewrite: symbols such as VOD.L must remain intact.
    _YAHOO_SYMBOL_ALIASES: dict[str, str] = {
        "BRK.B": "BRK-B",
        "BF.B": "BF-B",
    }

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
    def is_rate_limit_error(exc: Exception) -> bool:
        """Return whether an exception represents Yahoo request throttling."""
        message = str(exc).lower()
        return "too many requests" in message or "rate limit" in message or "429" in message

    # Backwards-compatible private name used internally and by any callers
    # which may already have relied on the implementation detail.
    _is_rate_limit_error = is_rate_limit_error

    @classmethod
    def rate_limit_remaining_seconds(cls) -> float:
        """Return the active circuit-breaker delay; zero means requests may run."""
        return max(0.0, cls._rate_limited_until - time.monotonic())

    @classmethod
    def is_rate_limited(cls) -> bool:
        """Machine-readable signal for batch refreshers to stop issuing calls."""
        return cls.rate_limit_remaining_seconds() > 0

    @classmethod
    def normalize_yahoo_symbol(cls, ticker: str) -> str:
        """Map only known class-share notation while preserving other suffixes."""
        normalized = ticker.strip().upper()
        return cls._YAHOO_SYMBOL_ALIASES.get(normalized, normalized)

    @classmethod
    def _metadata_status(cls, info: Any) -> tuple[str, int]:
        if not isinstance(info, dict):
            return "fallback", 0
        useful_count = sum(info.get(field) is not None for field in cls.METADATA_USEFUL_FIELDS)
        if useful_count < cls.METADATA_MIN_USEFUL_FIELDS:
            return "fallback", useful_count
        if useful_count < cls.METADATA_COMPLETE_USEFUL_FIELDS:
            return "partial", useful_count
        return "ok", useful_count

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

    def fetch_metadata(self, ticker: str) -> tuple[YahooSnapshot, str | None]:
        """Fetch analyst/fundamental metadata without requesting price history.

        Status is ``ok`` for at least six useful fields, ``partial`` for two to
        five, and ``fallback`` for fewer than two or a failed request. After a
        throttling error, :meth:`is_rate_limited` lets a batch refresher stop
        immediately instead of continuing through the remaining watchlist.
        """
        yahoo_symbol = self.normalize_yahoo_symbol(ticker)
        try:
            info = self._call_with_retry(lambda: yf.Ticker(yahoo_symbol).info)
        except Exception as exc:
            throttled = self.is_rate_limit_error(exc) or type(self).is_rate_limited()
            reason = "rate_limit" if throttled else "request_failed"
            warning = f"Yahoo metadata [{reason}] pro {yahoo_symbol}: {exc}"
            return YahooSnapshot(ticker=yahoo_symbol, data={}, status="fallback"), warning

        status, useful_count = self._metadata_status(info)
        if status == "fallback":
            warning = (
                f"Yahoo metadata [unusable] pro {yahoo_symbol}: pouze "
                f"{useful_count}/{len(self.METADATA_USEFUL_FIELDS)} užitečných polí "
                f"(minimum {self.METADATA_MIN_USEFUL_FIELDS})."
            )
            return YahooSnapshot(ticker=yahoo_symbol, data={}, status=status), warning

        snapshot = YahooSnapshot(ticker=yahoo_symbol, data=dict(info), status=status)
        if status == "partial":
            warning = (
                f"Yahoo metadata [partial] pro {yahoo_symbol}: "
                f"{useful_count}/{len(self.METADATA_USEFUL_FIELDS)} užitečných polí."
            )
            return snapshot, warning
        return snapshot, None

    @staticmethod
    def _copy_history(history: pd.DataFrame | None) -> pd.DataFrame | None:
        return history.copy() if isinstance(history, pd.DataFrame) else None

    def _fetch_bundle(
        self, ticker: str
    ) -> tuple[YahooSnapshot, PerformanceSnapshot, pd.DataFrame | None, str | None, str | None]:
        cache_key = self.normalize_yahoo_symbol(ticker)
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
            status, useful_count = self._metadata_status(info)
            if status == "fallback":
                raise ValueError(
                    "Yahoo vrátil nepoužitelná metadata "
                    f"({useful_count}/{len(self.METADATA_USEFUL_FIELDS)} užitečných polí)"
                )
            snapshot = YahooSnapshot(ticker=cache_key, data=dict(info), status=status)
            if status == "partial":
                metadata_warning = (
                    f"Yahoo metadata jsou pro {cache_key} pouze částečná "
                    f"({useful_count}/{len(self.METADATA_USEFUL_FIELDS)} užitečných polí)."
                )
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
        ttl = (
            self.cache_ttl_seconds
            if snapshot.status in {"ok", "partial"} and history is not None
            else 30
        )
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
