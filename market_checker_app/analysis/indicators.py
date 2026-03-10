from __future__ import annotations

from typing import Any


def build_basic_tech_indicators(mt5_rates: list[dict[str, Any]] | None) -> dict[str, float | None]:
    """Backward-compatible lightweight indicator helper for MT5 payloads.

    This helper is not the primary scoring engine. The main technical scoring is
    implemented in `analysis/tech_analysis.py`.
    """
    if not mt5_rates:
        return {"close": None, "sma_20": None, "sma_50": None, "rsi_14": None}

    closes = [float(r["close"]) for r in mt5_rates if "close" in r]
    if not closes:
        return {"close": None, "sma_20": None, "sma_50": None, "rsi_14": None}

    close = closes[-1]
    sma_20 = sum(closes[-20:]) / min(20, len(closes))
    sma_50 = sum(closes[-50:]) / min(50, len(closes))

    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    avg_gain = sum(gains[-14:]) / max(1, min(14, len(gains)))
    avg_loss = sum(losses[-14:]) / max(1, min(14, len(losses)))
    rs = avg_gain / avg_loss if avg_loss > 0 else 100.0
    rsi_14 = 100 - (100 / (1 + rs))

    return {"close": close, "sma_20": sma_20, "sma_50": sma_50, "rsi_14": rsi_14}
