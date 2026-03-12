from __future__ import annotations

from market_checker_app.config import AdjustmentConfig, ModuleWeights, RegimeOverrides, SignalThresholds
from market_checker_app.models import SignalDiagnostics


LEGACY_NEWS_WEIGHT = 0.33
LEGACY_TECH_WEIGHT = 0.39
LEGACY_YAHOO_WEIGHT = 0.28


def compute_raw_total(news_score: float, tech_score: float, yahoo_score: float, behavioral_score: float, weights: ModuleWeights) -> float:
    value = news_score * weights.news + tech_score * weights.tech + yahoo_score * weights.yahoo + behavioral_score * weights.behavioral
    return round(max(0.0, min(100.0, value)), 2)


def compute_legacy_total(news_score: float, tech_score: float, yahoo_score: float) -> float:
    legacy = news_score * LEGACY_NEWS_WEIGHT + tech_score * LEGACY_TECH_WEIGHT + yahoo_score * LEGACY_YAHOO_WEIGHT
    return round(max(0.0, min(100.0, legacy)), 2)


def apply_regime_overrides(raw_score: float, tech_score: float, oscillator_score: float, behavioral_score: float, regime: str, overrides: RegimeOverrides) -> float:
    score = raw_score
    if regime in {"trending_up", "trending_down"}:
        score += (tech_score - 50) * 0.08 * overrides.trend_multiplier
    elif regime in {"sideways_low_vol", "sideways_high_vol"}:
        score += (oscillator_score - 50) * 0.06 * overrides.range_multiplier
    elif regime in {"panic_regime", "euphoric_regime"}:
        score += (behavioral_score - 50) * 0.09 * overrides.behavior_multiplier
    return max(0.0, min(100.0, score))


def _signal_from_score(score: float, thresholds: SignalThresholds) -> str:
    if score >= thresholds.strong_buy:
        return "STRONG BUY"
    if score >= thresholds.buy:
        return "BUY"
    if score >= thresholds.hold:
        return "HOLD"
    if score >= thresholds.sell:
        return "SELL"
    return "STRONG SELL"


def legacy_signal_from_score(score: float, thresholds: SignalThresholds | None = None) -> str:
    """Legacy signal mapping with safe fallback thresholds.

    Using explicit constants prevents NameError regressions when callers do not
    pass threshold objects.
    """
    if thresholds is None:
        if score >= 80:
            return "STRONG BUY"
        if score >= 66:
            return "BUY"
        if score >= 48:
            return "HOLD"
        if score >= 32:
            return "SELL"
        return "STRONG SELL"

    if score >= thresholds.strong_buy:
        return "STRONG BUY"
    if score >= thresholds.buy:
        return "BUY"
    if score >= thresholds.hold:
        return "HOLD"
    if score >= thresholds.sell:
        return "SELL"
    return "STRONG SELL"


def _strength(score: float, confidence: float) -> str:
    mix = score * 0.68 + confidence * 0.32
    if mix >= 82:
        return "very strong"
    if mix >= 68:
        return "strong"
    if mix >= 52:
        return "moderate"
    return "weak"


def finalize_signal(
    raw_score: float,
    final_confidence: float,
    data_quality: float,
    risk_score: float,
    adjustment: AdjustmentConfig,
    thresholds: SignalThresholds,
    reasons: list[str],
    warnings: list[str],
    key_drivers: list[str],
) -> SignalDiagnostics:
    quality_adjusted = max(0.0, min(100.0, raw_score + (data_quality - adjustment.quality_center) * adjustment.quality_coef))
    risk_adjusted = max(0.0, min(100.0, quality_adjusted - (risk_score - adjustment.risk_center) * adjustment.risk_coef))
    final_score = round(max(0.0, min(100.0, risk_adjusted)), 2)

    signal = _signal_from_score(final_score, thresholds)
    if final_confidence < 42 and signal in {"BUY", "STRONG BUY"}:
        signal = "HOLD"
        warnings.append("signal downgraded due to low confidence")
    if risk_score > 75 and signal in {"BUY", "STRONG BUY"}:
        signal = "HOLD"
        warnings.append("signal downgraded due to high risk")

    return SignalDiagnostics(
        raw_total_score=round(raw_score, 2),
        quality_adjusted_score=round(quality_adjusted, 2),
        risk_adjusted_score=round(risk_adjusted, 2),
        final_total_score=final_score,
        final_confidence=round(final_confidence, 2),
        data_quality_score=round(data_quality, 2),
        signal=signal,
        signal_strength=_strength(final_score, final_confidence),
        reasons=reasons,
        warnings=warnings,
        key_drivers=key_drivers,
        overall_summary=f"{signal} with final score {final_score:.1f}, confidence {final_confidence:.1f}, risk {risk_score:.1f}.",
    )
