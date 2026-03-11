from __future__ import annotations

import yfinance as yf

from market_checker_app.models import PerformanceSnapshot, YahooSnapshot


class YahooClient:
    def fetch_snapshots(self, ticker: str) -> tuple[YahooSnapshot, PerformanceSnapshot, str | None]:
        try:
            tk = yf.Ticker(ticker)
            info = tk.info
            if not isinstance(info, dict) or not info:
                raise ValueError("Yahoo vrátil prázdná metadata")
        except Exception as exc:
            return (
                YahooSnapshot(ticker=ticker, data={}, status="fallback"),
                PerformanceSnapshot(ticker, None, None, None),
                f"Yahoo data nejsou dostupná pro {ticker}. Používám fallback. Detail: {exc}",
            )

        perf = PerformanceSnapshot(
            ticker=ticker,
            last_week_change_pct=info.get("52WeekChange") * 100 if isinstance(info.get("52WeekChange"), float) else None,
            last_1m_change_pct=info.get("fiftyDayAverageChangePercent") * 100 if isinstance(info.get("fiftyDayAverageChangePercent"), float) else None,
            last_3m_change_pct=info.get("threeMonthAverageReturn") * 100 if isinstance(info.get("threeMonthAverageReturn"), float) else None,
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
