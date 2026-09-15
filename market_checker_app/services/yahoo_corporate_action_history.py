from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any

import pandas as pd
import yfinance as yf

from market_checker_app.utils.symbols import normalize_yahoo_symbol


class YahooCorporateActionHistoryClient:
    """Fetch unadjusted Yahoo OHLC together with stock-split actions.

    This client exists specifically for versioned prediction-label price data.
    ``yfinance.download`` defaults to ``actions=False``; that is not safe for a
    split-adjusted price-return target. Every bulk and single-symbol request here
    therefore requests actions explicitly and rejects frames without the
    ``Stock Splits`` column.
    """

    def __init__(
        self,
        *,
        retry_attempts: int = 2,
        retry_delay_seconds: float = 1.0,
    ) -> None:
        self.retry_attempts = max(1, int(retry_attempts))
        self.retry_delay_seconds = max(0.0, float(retry_delay_seconds))

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(
            marker in message
            for marker in (
                "429",
                "rate limit",
                "too many requests",
                "timeout",
                "timed out",
                "temporarily unavailable",
                "connection",
            )
        )

    def _call(self, operation: Callable[[], Any]) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.retry_attempts):
            try:
                return operation()
            except Exception as exc:
                last_error = exc
                if not self._is_transient(exc) or attempt + 1 >= self.retry_attempts:
                    break
                time.sleep(self.retry_delay_seconds * (2**attempt))
        assert last_error is not None
        raise last_error

    @staticmethod
    def _extract(
        history: object,
        yahoo_symbol: str,
        *,
        single_symbol: bool,
    ) -> pd.DataFrame | None:
        if not isinstance(history, pd.DataFrame) or history.empty:
            return None
        frame: pd.DataFrame | None = None
        if isinstance(history.columns, pd.MultiIndex):
            level_zero = {str(value) for value in history.columns.get_level_values(0)}
            level_one = {str(value) for value in history.columns.get_level_values(1)}
            if yahoo_symbol in level_zero:
                frame = history[yahoo_symbol]
            elif yahoo_symbol in level_one:
                frame = history.xs(yahoo_symbol, axis=1, level=1, drop_level=True)
        elif single_symbol:
            frame = history
        if frame is None or frame.empty:
            return None

        normalized = {str(column).strip().lower(): column for column in frame.columns}
        rename: dict[object, str] = {}
        for canonical, key in (
            ("Open", "open"),
            ("High", "high"),
            ("Low", "low"),
            ("Close", "close"),
            ("Volume", "volume"),
            ("Dividends", "dividends"),
            ("Stock Splits", "stock splits"),
        ):
            source = normalized.get(key)
            if source is not None and source != canonical:
                rename[source] = canonical
        selected = frame.rename(columns=rename).dropna(how="all")
        if "Close" not in selected.columns or "Stock Splits" not in selected.columns:
            return None
        if "Dividends" not in selected.columns:
            selected["Dividends"] = 0.0
        return selected

    def fetch_one(
        self,
        ticker: str,
        *,
        period: str = "1y",
        interval: str = "1d",
    ) -> tuple[pd.DataFrame | None, str | None]:
        yahoo_symbol = normalize_yahoo_symbol(ticker)
        try:
            history = self._call(
                lambda: yf.Ticker(yahoo_symbol).history(
                    period=period,
                    interval=interval,
                    auto_adjust=False,
                    actions=True,
                )
            )
        except Exception as exc:
            return None, f"Yahoo action OHLC pro {ticker} selhalo: {exc}"
        frame = self._extract(history, yahoo_symbol, single_symbol=True)
        if frame is None:
            return None, (
                f"Yahoo action OHLC pro {ticker} neobsahuje Close + Stock Splits."
            )
        return frame, None

    def fetch_batch(
        self,
        tickers: list[str],
        *,
        period: str = "1y",
        interval: str = "1d",
        batch_size: int = 50,
        missing_symbol_retry_limit: int = 5,
    ) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
        canonical = list(
            dict.fromkeys(
                str(ticker).strip().upper()
                for ticker in tickers
                if str(ticker).strip()
            )
        )
        if not canonical:
            return {}, {}

        frames: dict[str, pd.DataFrame] = {}
        warnings: dict[str, str] = {}
        size = max(1, int(batch_size))
        retry_limit = max(0, int(missing_symbol_retry_limit))
        batches = [canonical[index : index + size] for index in range(0, len(canonical), size)]

        for batch_index, batch in enumerate(batches, start=1):
            yahoo_symbols = [normalize_yahoo_symbol(ticker) for ticker in batch]
            try:
                history = self._call(
                    lambda symbols=yahoo_symbols: yf.download(
                        tickers=symbols,
                        period=period,
                        interval=interval,
                        actions=True,
                        auto_adjust=False,
                        group_by="ticker",
                        threads=False,
                        progress=False,
                    )
                )
            except Exception as exc:
                message = (
                    f"Yahoo action OHLC dávka {batch_index}/{len(batches)} selhala: "
                    f"{type(exc).__name__}: {exc}"
                )
                warnings.update({ticker: message for ticker in batch})
                continue

            missing: list[str] = []
            for ticker, yahoo_symbol in zip(batch, yahoo_symbols):
                frame = self._extract(
                    history,
                    yahoo_symbol,
                    single_symbol=len(batch) == 1,
                )
                if frame is None:
                    missing.append(ticker)
                else:
                    frames[ticker] = frame

            for ticker in missing[:retry_limit]:
                frame, warning = self.fetch_one(
                    ticker,
                    period=period,
                    interval=interval,
                )
                if frame is not None:
                    frames[ticker] = frame
                else:
                    warnings[ticker] = warning or "Yahoo action OHLC retry selhal."
            for ticker in missing[retry_limit:]:
                warnings[ticker] = (
                    f"Yahoo action OHLC nevrátil corporate-action data pro {ticker}; "
                    "individuální retry nebyl proveden."
                )

        return frames, warnings
