from __future__ import annotations


def detect_market_regime(momentum_1m: float, realized_volatility: float, panic_score: float, euphoria_score: float) -> str:
    if panic_score > 75:
        return "panic_regime"
    if euphoria_score > 75:
        return "euphoric_regime"
    if momentum_1m > 6 and realized_volatility < 0.03:
        return "trending_up"
    if momentum_1m < -6 and realized_volatility < 0.03:
        return "trending_down"
    if abs(momentum_1m) < 3 and realized_volatility < 0.018:
        return "sideways_low_vol"
    if abs(momentum_1m) < 4 and realized_volatility >= 0.018:
        return "sideways_high_vol"
    return "mixed"
