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


def _build_decision_modules(
    *,
    news_score: float,
    tech_score: float,
    analyst_score: float,
    panic_score: float,
    news_confidence: float,
    tech_confidence: float,
    analyst_confidence: float,
    panic_confidence: float,
    context: str,
) -> list[ModuleAxisResult]:
    news_mod = _build_module_result(
        "news",
        bull=max(0.0, min(100.0, news_score * 0.95 + max(0.0, (news_score - 55) * 0.25))),
        bear=max(0.0, min(100.0, (100 - news_score) * 0.75 + max(0.0, (45 - news_score) * 0.8))),
        confidence01=news_confidence / 100,
        explanation=f"[{context}] News tone/recency -> score {news_score:.1f}, conf {news_confidence:.1f}.",
    )
    tech_mod = _build_module_result(
        "technical",
        bull=max(0.0, min(100.0, tech_score * 1.05 + max(0.0, (tech_score - 60) * 0.35))),
        bear=max(0.0, min(100.0, (100 - tech_score) * 1.05 + max(0.0, (40 - tech_score) * 0.5))),
        confidence01=tech_confidence / 100,
        explanation=f"[{context}] Technical trend/momentum -> score {tech_score:.1f}, conf {tech_confidence:.1f}.",
    )
    panic_mod = _build_module_result(
        "panic",
        bull=max(0.0, min(100.0, 70 - panic_score * 0.6)),
        bear=max(0.0, min(100.0, panic_score * 1.1)),
        confidence01=panic_confidence / 100,
        explanation=f"[{context}] Panic regime filter (higher panic supports bearish bias): {panic_score:.1f}.",
    )
    analyst_mod = _build_module_result(
        "analysts",
        bull=max(0.0, min(100.0, analyst_score * 0.9 + max(0.0, (analyst_score - 55) * 0.25))),
        bear=max(0.0, min(100.0, (100 - analyst_score) * 0.75 + max(0.0, (45 - analyst_score) * 0.45))),
        confidence01=analyst_confidence / 100,
        explanation=f"[{context}] Analyst revisions/supporting evidence score {analyst_score:.1f}, conf {analyst_confidence:.1f}.",
    )
    return [news_mod, tech_mod, panic_mod, analyst_mod]


def _decision_from_modules(
    modules: list[ModuleAxisResult],
    panic_score: float,
    decision_weights: DecisionModuleWeights,
    decision_thresholds: DecisionThresholds,
) -> tuple[str, float, float, float, float, int, int, int, list[str], int, str]:
    by_name = {m.module: m for m in modules}
    news_mod = by_name["news"]
    tech_mod = by_name["technical"]
    panic_mod = by_name["panic"]
    analyst_mod = by_name["analysts"]

    bull_score = (
        tech_mod.bull_contribution * decision_weights.technical
        + news_mod.bull_contribution * decision_weights.news
        + panic_mod.bull_contribution * decision_weights.panic
        + analyst_mod.bull_contribution * decision_weights.analysts
    )
    bear_score = (
        tech_mod.bear_contribution * decision_weights.technical
        + news_mod.bear_contribution * decision_weights.news
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
    signal = "HOLD"

    tech_is_bullish = tech_mod.direction == "bullish"
    tech_is_bearish = tech_mod.direction == "bearish"
    tech_ok_for_buy = tech_mod.direction in {"bullish", "neutral"}
    tech_ok_for_sell = tech_mod.direction in {"bearish", "neutral"}

    panic_elevated = panic_score >= decision_thresholds.panic_block_threshold
    panic_extreme = panic_score >= 85

    # Strong BUY: must be technical-led with confluence
    if (
        bull_score >= decision_thresholds.strong_buy_min_bull_score
        and spread >= decision_thresholds.strong_buy_min_spread
        and bullish_count >= 3
        and tech_is_bullish
        and not panic_elevated
        and overall_conf >= decision_thresholds.minimum_confidence_strong
        and bearish_count <= 1
    ):
        signal = "STRONG BUY"
    # BUY: technical should not be bearish
    elif (
        spread >= decision_thresholds.buy_min_spread
        and bullish_count >= bearish_count
        and tech_ok_for_buy
        and overall_conf >= decision_thresholds.minimum_confidence_buy
    ):
        signal = "BUY"
    # Strong SELL: must be technical bearish + confluence
    elif (
        bear_score >= decision_thresholds.strong_sell_min_bear_score
        and spread <= decision_thresholds.strong_sell_min_negative_spread
        and bearish_count >= 3
        and tech_is_bearish
        and overall_conf >= decision_thresholds.minimum_confidence_strong
        and bullish_count <= 1
    ):
        signal = "STRONG SELL"
    elif (
        spread <= decision_thresholds.sell_min_spread
        and bearish_count >= bullish_count
        and tech_ok_for_sell
        and overall_conf >= decision_thresholds.minimum_confidence_buy
    ):
        signal = "SELL"

    # explicit conflict handling
    if abs(spread) <= decision_thresholds.hold_band:
        blocked_reasons.append("bull_bear_balance_hold_band")
        signal = "HOLD"

    if tech_is_bearish and signal in {"BUY", "STRONG BUY"}:
        blocked_reasons.append("technical_bearish_blocks_bullish_signal")
        signal = "HOLD" if signal == "BUY" else "SELL"
        downgrade_count += 1

    if tech_is_bullish and signal in {"SELL", "STRONG SELL"} and news_mod.direction != "bearish":
        blocked_reasons.append("technical_bullish_blocks_bearish_signal_without_news_confluence")
        signal = "HOLD"
        downgrade_count += 1

    if panic_elevated and signal == "STRONG BUY":
        blocked_reasons.append("panic_elevated_blocks_strong_buy")
        signal = "BUY"
        downgrade_count += 1

    if panic_extreme and signal in {"BUY", "STRONG BUY"}:
        blocked_reasons.append("panic_extreme_blocks_bullish_signal")
        signal = "HOLD" if signal == "BUY" else "SELL"
        downgrade_count += 1

    if overall_conf < decision_thresholds.minimum_confidence_buy and signal in {"BUY", "SELL", "STRONG BUY", "STRONG SELL"}:
        blocked_reasons.append("low_confidence_blocks_directional_signal")
        signal = "HOLD"
        downgrade_count += 1

    driver = "mixed"
    if signal in {"BUY", "STRONG BUY"}:
        driver = "technical_led_bullish"
        if news_mod.direction == "bullish":
            driver += "+news_acceleration"
        if panic_elevated:
            driver += "+panic_constrained"
    elif signal in {"SELL", "STRONG SELL"}:
        driver = "technical_led_bearish"
        if news_mod.direction == "bearish":
            driver += "+news_acceleration"
        if panic_elevated:
            driver += "+panic_support"
    else:
        if blocked_reasons:
            driver = "conflict_downgraded"

    return (
        signal,
        bull_score,
        bear_score,
        spread,
        overall_conf,
        bullish_count,
        bearish_count,
        neutral_count,
        blocked_reasons,
        downgrade_count,
        driver,
    )


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
    # Independent bull/bear contributions per module (NOT 100-bull complements)
    modules = _build_decision_modules(
        news_score=news_score,
        tech_score=tech_score,
        analyst_score=analyst_score,
        panic_score=panic_score,
        news_confidence=news_confidence,
        tech_confidence=tech_confidence,
        analyst_confidence=analyst_confidence,
        panic_confidence=panic_confidence,
        context="live",
    )

    (
        signal,
        bull_score,
        bear_score,
        spread,
        overall_conf,
        bullish_count,
        bearish_count,
        neutral_count,
        blocked_reasons,
        downgrade_count,
        driver,
    ) = _decision_from_modules(modules, panic_score, decision_weights, decision_thresholds)

    # Compatibility scores for legacy UI sorting while preserving dual-axis semantics
    final_index = max(0.0, min(100.0, 50 + spread / 2))
    quality_adjusted = max(0.0, min(100.0, final_index + (data_quality - adjustment.quality_center) * adjustment.quality_coef * 0.5))
    risk_adjusted = max(0.0, min(100.0, quality_adjusted - (risk_score - adjustment.risk_center) * adjustment.risk_coef * 0.5))

    explain = (
        f"{signal}: {driver}; bull={bull_score:.1f}, bear={bear_score:.1f}, spread={spread:.1f}; "
        f"modules bullish/neutral/bearish={bullish_count}/{neutral_count}/{bearish_count}."
    )

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


def validate_decision_scenarios(weights: DecisionModuleWeights, thresholds: DecisionThresholds) -> list[dict[str, object]]:
    scenarios = [
        {
            "name": "A bullish alignment",
            "inputs": dict(news_score=72, tech_score=76, panic_score=35, analyst_score=64, news_confidence=70, tech_confidence=78, analyst_confidence=65, panic_confidence=70),
            "expected": {"BUY", "STRONG BUY"},
        },
        {
            "name": "B bearish tech + elevated panic",
            "inputs": dict(news_score=68, tech_score=34, panic_score=78, analyst_score=50, news_confidence=68, tech_confidence=75, analyst_confidence=55, panic_confidence=74),
            "expected": {"HOLD", "SELL"},
        },
        {
            "name": "C full bearish confluence",
            "inputs": dict(news_score=32, tech_score=28, panic_score=84, analyst_score=36, news_confidence=78, tech_confidence=82, analyst_confidence=70, panic_confidence=80),
            "expected": {"SELL", "STRONG SELL"},
        },
        {
            "name": "D bullish tech vs negative news",
            "inputs": dict(news_score=40, tech_score=74, panic_score=42, analyst_score=64, news_confidence=65, tech_confidence=80, analyst_confidence=68, panic_confidence=65),
            "expected": {"BUY", "HOLD"},
        },
        {
            "name": "E neutral mixed",
            "inputs": dict(news_score=52, tech_score=50, panic_score=48, analyst_score=50, news_confidence=55, tech_confidence=58, analyst_confidence=52, panic_confidence=55),
            "expected": {"HOLD"},
        },
        {
            "name": "F bullish but extreme panic",
            "inputs": dict(news_score=74, tech_score=78, panic_score=90, analyst_score=66, news_confidence=72, tech_confidence=80, analyst_confidence=62, panic_confidence=82),
            "expected": {"BUY", "HOLD", "SELL"},
        },
    ]

    rows: list[dict[str, object]] = []
    for scenario in scenarios:
        i = scenario["inputs"]
        modules = _build_decision_modules(
            news_score=i["news_score"],
            tech_score=i["tech_score"],
            analyst_score=i["analyst_score"],
            panic_score=i["panic_score"],
            news_confidence=i["news_confidence"],
            tech_confidence=i["tech_confidence"],
            analyst_confidence=i["analyst_confidence"],
            panic_confidence=i["panic_confidence"],
            context="scenario",
        )
        by_name = {m.module: m for m in modules}
        news_mod = by_name["news"]
        tech_mod = by_name["technical"]
        panic_mod = by_name["panic"]
        analyst_mod = by_name["analysts"]

        signal, bull_score, bear_score, spread, conf, bc, brc, nc, blocked, _, _ = _decision_from_modules(modules, i["panic_score"], weights, thresholds)
        expected = scenario["expected"]
        rows.append(
            {
                "scenario_name": scenario["name"],
                "module_states": f"news={news_mod.direction}, tech={tech_mod.direction}, panic={panic_mod.direction}, analysts={analyst_mod.direction}",
                "bull_score": round(bull_score, 2),
                "bear_score": round(bear_score, 2),
                "spread": round(spread, 2),
                "confidence": round(conf, 3),
                "blocked_reasons": ", ".join(blocked),
                "final_signal": signal,
                "expected_signal_band": ", ".join(sorted(expected)),
                "pass": signal in expected,
                "bullish_modules": bc,
                "bearish_modules": brc,
                "neutral_modules": nc,
            }
        )
    return rows
