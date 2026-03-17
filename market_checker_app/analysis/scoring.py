from __future__ import annotations

from dataclasses import asdict

from market_checker_app.config import (
    AdjustmentConfig,
    DecisionModuleWeights,
    DecisionThresholds,
    ModuleWeights,
    RegimeOverrides,
    SignalThresholds,
)
from market_checker_app.models import ModuleAxisResult, SignalDiagnostics


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


def _module_direction(bull: float, bear: float) -> str:
    spread = bull - bear
    if spread >= 12:
        return "bullish"
    if spread <= -12:
        return "bearish"
    return "neutral"


def _build_module_result(name: str, bull: float, bear: float, confidence01: float, explanation: str) -> ModuleAxisResult:
    return ModuleAxisResult(
        module=name,
        direction=_module_direction(bull, bear),
        bull_contribution=round(max(0.0, min(100.0, bull)), 2),
        bear_contribution=round(max(0.0, min(100.0, bear)), 2),
        confidence=round(max(0.0, min(1.0, confidence01)), 3),
        explanation=explanation,
    )


def _strength_from_spread(spread: float, confidence: float) -> str:
    mix = abs(spread) * 0.75 + confidence * 100 * 0.25
    if mix >= 45:
        return "very strong"
    if mix >= 30:
        return "strong"
    if mix >= 18:
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
    *,
    news_score: float,
    tech_score: float,
    analyst_score: float,
    panic_score: float,
    news_confidence: float,
    tech_confidence: float,
    analyst_confidence: float,
    panic_confidence: float,
    decision_weights: DecisionModuleWeights,
    decision_thresholds: DecisionThresholds,
) -> SignalDiagnostics:
    # module axis contributions
    news_mod = _build_module_result(
        "news",
        bull=news_score,
        bear=100 - news_score,
        confidence01=news_confidence / 100,
        explanation=f"News score {news_score:.1f} with confidence {news_confidence:.1f}.",
    )
    tech_mod = _build_module_result(
        "technical",
        bull=tech_score,
        bear=100 - tech_score,
        confidence01=tech_confidence / 100,
        explanation=f"Technical score {tech_score:.1f} with confidence {tech_confidence:.1f}.",
    )
    panic_mod = _build_module_result(
        "panic",
        bull=max(0.0, 100 - panic_score),
        bear=panic_score,
        confidence01=panic_confidence / 100,
        explanation=f"Panic/risk-off score {panic_score:.1f} (higher = bearish pressure).",
    )
    analyst_mod = _build_module_result(
        "analysts",
        bull=analyst_score,
        bear=100 - analyst_score,
        confidence01=analyst_confidence / 100,
        explanation=f"Analyst module score {analyst_score:.1f} with confidence {analyst_confidence:.1f}.",
    )

    modules = [news_mod, tech_mod, panic_mod, analyst_mod]

    bull_score = (
        news_mod.bull_contribution * decision_weights.news
        + tech_mod.bull_contribution * decision_weights.technical
        + panic_mod.bull_contribution * decision_weights.panic
        + analyst_mod.bull_contribution * decision_weights.analysts
    )
    bear_score = (
        news_mod.bear_contribution * decision_weights.news
        + tech_mod.bear_contribution * decision_weights.technical
        + panic_mod.bear_contribution * decision_weights.panic
        + analyst_mod.bear_contribution * decision_weights.analysts
    )
    spread = bull_score - bear_score

    bullish_count = sum(m.direction == "bullish" for m in modules)
    bearish_count = sum(m.direction == "bearish" for m in modules)
    neutral_count = sum(m.direction == "neutral" for m in modules)

    avg_module_conf = sum(m.confidence for m in modules) / len(modules)
    agreement = max(bullish_count, bearish_count) / len(modules)
    overall_conf = max(0.0, min(1.0, avg_module_conf * 0.7 + agreement * 0.3))

    blocked_reasons: list[str] = []
    downgrade_count = 0

    # Confluence rules (no single linear threshold mapping)
    signal = "HOLD"
    tech_bullish_ok = tech_mod.direction in {"bullish", "neutral"}
    tech_bearish_ok = tech_mod.direction in {"bearish", "neutral"}
    panic_block_buy = panic_score >= decision_thresholds.panic_block_threshold

    if (
        bull_score >= decision_thresholds.strong_buy_min_bull_score
        and spread >= decision_thresholds.strong_buy_min_spread
        and bullish_count >= 3
        and tech_mod.direction == "bullish"
        and not panic_block_buy
        and overall_conf >= decision_thresholds.minimum_confidence_strong
        and bearish_count <= 1
    ):
        signal = "STRONG BUY"
    elif (
        spread >= decision_thresholds.buy_min_spread
        and bullish_count > bearish_count
        and tech_bullish_ok
        and overall_conf >= decision_thresholds.minimum_confidence_buy
        and not panic_block_buy
    ):
        signal = "BUY"
    elif (
        bear_score >= decision_thresholds.strong_sell_min_bear_score
        and spread <= decision_thresholds.strong_sell_min_negative_spread
        and bearish_count >= 3
        and tech_mod.direction == "bearish"
        and panic_score >= 60
        and overall_conf >= decision_thresholds.minimum_confidence_strong
        and bullish_count <= 1
    ):
        signal = "STRONG SELL"
    elif (
        spread <= decision_thresholds.sell_min_spread
        and bearish_count > bullish_count
        and tech_bearish_ok
        and overall_conf >= decision_thresholds.minimum_confidence_buy
    ):
        signal = "SELL"

    # conflict / low confidence handling
    if abs(spread) <= decision_thresholds.hold_band:
        blocked_reasons.append("bull_bear_close_to_balance")
        signal = "HOLD"
    if neutral_count >= 2 and signal in {"STRONG BUY", "STRONG SELL"}:
        blocked_reasons.append("too_many_neutral_modules_for_strong_signal")
        signal = "BUY" if signal == "STRONG BUY" else "SELL"
        downgrade_count += 1
    if overall_conf < decision_thresholds.minimum_confidence_buy and signal in {"BUY", "SELL", "STRONG BUY", "STRONG SELL"}:
        blocked_reasons.append("confidence_too_low_for_directional_signal")
        signal = "HOLD"
        downgrade_count += 1
    if panic_block_buy and signal in {"BUY", "STRONG BUY"}:
        blocked_reasons.append("panic_blocked_bullish_signal")
        signal = "HOLD" if signal == "BUY" else "SELL"
        downgrade_count += 1

    # keep legacy-compatible fields for UI sorting while still using dual-axis engine
    final_index = max(0.0, min(100.0, 50 + spread / 2))
    quality_adjusted = max(0.0, min(100.0, final_index + (data_quality - adjustment.quality_center) * adjustment.quality_coef * 0.5))
    risk_adjusted = max(0.0, min(100.0, quality_adjusted - (risk_score - adjustment.risk_center) * adjustment.risk_coef * 0.5))

    explain = f"{signal}: bull={bull_score:.1f}, bear={bear_score:.1f}, spread={spread:.1f}, modules B/N/S={bullish_count}/{neutral_count}/{bearish_count}."

    return SignalDiagnostics(
        raw_total_score=round(final_index, 2),
        quality_adjusted_score=round(quality_adjusted, 2),
        risk_adjusted_score=round(risk_adjusted, 2),
        final_total_score=round(risk_adjusted, 2),
        final_confidence=round(max(final_confidence / 100, overall_conf) * 100, 2),
        data_quality_score=round(data_quality, 2),
        signal=signal,
        signal_strength=_strength_from_spread(spread, overall_conf),
        bull_score=round(bull_score, 2),
        bear_score=round(bear_score, 2),
        bull_bear_spread=round(spread, 2),
        bullish_module_count=bullish_count,
        bearish_module_count=bearish_count,
        neutral_module_count=neutral_count,
        blocked_reasons=blocked_reasons,
        downgrade_count=downgrade_count,
        module_breakdown=[asdict(m) for m in modules],
        reasons=reasons + [explain],
        warnings=warnings,
        key_drivers=key_drivers,
        overall_summary=explain,
    )
