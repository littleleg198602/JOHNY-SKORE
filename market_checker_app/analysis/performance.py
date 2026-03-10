from __future__ import annotations

import pandas as pd


def summarize_performance(signals: pd.DataFrame) -> dict[str, float]:
    if signals.empty:
        return {"avg_total_score": 0.0, "count_buy": 0.0, "count_sell": 0.0}
    buys = signals[signals["signal"].isin(["BUY", "STRONG BUY"])].shape[0]
    sells = signals[signals["signal"].isin(["SELL", "STRONG SELL"])].shape[0]
    return {"avg_total_score": float(signals["final_total_score"].mean()), "count_buy": float(buys), "count_sell": float(sells)}
