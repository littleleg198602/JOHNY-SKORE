from __future__ import annotations

from typing import Any

from market_checker_app.models import TechSnapshot


def build_tech_snapshot(ticker: str, mt5_rates: list[dict[str, Any]] | None) -> TechSnapshot:
    if not mt5_rates:
        return TechSnapshot(ticker, None, None, None, None, None, "no_data")

    closes = [float(r["close"]) for r in mt5_rates if "close" in r]
    if not closes:
        return TechSnapshot(ticker, None, None, None, None, None, "no_close")

    close = closes[-1]
    sma_20 = sum(closes[-20:]) / min(20, len(closes))
    sma_50 = sum(closes[-50:]) / min(50, len(closes))
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    avg_gain = sum(gains[-14:]) / max(1, min(14, len(gains)))
    avg_loss = sum(losses[-14:]) / max(1, min(14, len(losses)))
    rs = avg_gain / avg_loss if avg_loss > 0 else 100.0
    rsi = 100 - (100 / (1 + rs))

    return TechSnapshot(ticker=ticker, rsi=rsi, macd=None, sma_20=sma_20, sma_50=sma_50, close=close, status="ok")
