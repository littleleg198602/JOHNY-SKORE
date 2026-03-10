from __future__ import annotations

import yfinance as yf

from market_checker_app.models import PerformanceSnapshot, YahooSnapshot


class YahooClient:
    def fetch_snapshots(self, ticker: str) -> tuple[YahooSnapshot, PerformanceSnapshot, str | None]:
        try:
            info = yf.Ticker(ticker).info
            if not isinstance(info, dict) or not info:
                raise ValueError("Yahoo vrátil prázdná metadata")
        except Exception as exc:
            return (
                YahooSnapshot(ticker, None, None, "unknown", None, "fallback"),
                PerformanceSnapshot(ticker, None, None, None),
                (
                    f"Yahoo data nejsou dostupná pro {ticker}. "
                    f"Používám fallback hodnoty (neutrální skóre). Detail: {exc}"
                ),
            )

        rec_key = str(info.get("recommendationKey", "neutral"))
        perf = PerformanceSnapshot(
            ticker=ticker,
            last_week_change_pct=info.get("52WeekChange") * 100 if isinstance(info.get("52WeekChange"), float) else None,
            last_1m_change_pct=info.get("fiftyDayAverageChangePercent") * 100
            if isinstance(info.get("fiftyDayAverageChangePercent"), float)
            else None,
            last_3m_change_pct=info.get("threeMonthAverageReturn") * 100
            if isinstance(info.get("threeMonthAverageReturn"), float)
            else None,
        )
        snapshot = YahooSnapshot(
            ticker=ticker,
            beta=info.get("beta"),
            trailing_pe=info.get("trailingPE"),
            recommendation_key=rec_key,
            analyst_target_price=info.get("targetMeanPrice"),
            status="ok",
        )
        return snapshot, perf, None
