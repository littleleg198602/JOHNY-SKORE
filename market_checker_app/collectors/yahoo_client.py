from __future__ import annotations

import yfinance as yf

from market_checker_app.models import PerformanceSnapshot, YahooSnapshot


class YahooClient:
    @staticmethod
    def _return_from_history(hist, days: int) -> float | None:
        if hist is None or hist.empty or "Close" not in hist.columns:
            return None
        close = hist["Close"].dropna()
        if len(close) <= days:
            return None
        latest = float(close.iloc[-1])
        base = float(close.iloc[-(days + 1)])
        if base == 0:
            return None
        return ((latest / base) - 1) * 100

    def fetch_snapshots(self, ticker: str) -> tuple[YahooSnapshot, PerformanceSnapshot, str | None]:
        try:
            tk = yf.Ticker(ticker)
            info = tk.info
            if not isinstance(info, dict) or not info:
                raise ValueError("Yahoo vrátil prázdná metadata")
            perf_hist = tk.history(period="6mo", interval="1d", auto_adjust=False)
        except Exception as exc:
            return (
                YahooSnapshot(ticker=ticker, data={}, status="fallback"),
                PerformanceSnapshot(ticker, None, None, None, None),
                f"Yahoo data nejsou dostupná pro {ticker}. Používám fallback. Detail: {exc}",
            )

        perf = PerformanceSnapshot(
            ticker=ticker,
            last_week_change_pct=self._return_from_history(perf_hist, 7),
            last_14d_change_pct=self._return_from_history(perf_hist, 14),
            last_1m_change_pct=self._return_from_history(perf_hist, 21),
            last_3m_change_pct=self._return_from_history(perf_hist, 63),
        )
        return YahooSnapshot(ticker=ticker, data=info, status="ok"), perf, None

    def fetch_ohlc(self, ticker: str, period: str = "1y", interval: str = "1d"):
        try:
            hist = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
            if hist is None or hist.empty:
                return hist, f"OHLC data pro {ticker} nejsou na Yahoo dostupná."
            return hist, None
        except Exception as exc:
            return None, f"Stažení OHLC pro {ticker} selhalo: {exc}"
