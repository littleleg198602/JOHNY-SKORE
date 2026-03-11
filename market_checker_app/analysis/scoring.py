from __future__ import annotations

from market_checker_app.models import SignalDiagnostics


# Weights favor technical regime slightly because it updates most frequently and reacts first to trend shifts.
NEWS_WEIGHT = 0.33
TECH_WEIGHT = 0.39
YAHOO_WEIGHT = 0.28


def compute_raw_total(news_score: float, tech_score: float, yahoo_score: float) -> float:
    return round(max(0.0, min(100.0, news_score * NEWS_WEIGHT + tech_score * TECH_WEIGHT + yahoo_score * YAHOO_WEIGHT)), 2)


def _signal_from_score(score: float) -> str:
    if score >= 80:
        return "STRONG BUY"
    if score >= 66:
        return "BUY"
    if score >= 48:
        return "HOLD"
    if score >= 32:
        return "SELL"
    return "STRONG SELL"


def _strength(score: float, confidence: float) -> str:
    mix = score * 0.7 + confidence * 0.3
    if mix >= 82:
        return "very strong"
    if mix >= 68:
        return "strong"
    if mix >= 52:
        return "moderate"
    return "weak"


def finalize_signal(raw_score: float, final_confidence: float, data_quality: float, warnings: list[str], reasons: list[str]) -> SignalDiagnostics:
    adjustment = (data_quality - 50) * 0.08  # quality nudges result, but does not dominate score.
    final_score = max(0.0, min(100.0, raw_score + adjustment))
    signal = _signal_from_score(final_score)
    if final_confidence < 45 and signal in {"BUY", "STRONG BUY"}:
        signal = "HOLD"
    if final_confidence < 35 and signal == "HOLD" and final_score < 52:
        signal = "SELL"
    return SignalDiagnostics(
        raw_total_score=round(raw_score, 2),
        final_total_score=round(final_score, 2),
        final_confidence=round(final_confidence, 2),
        data_quality_score=round(data_quality, 2),
        signal=signal,
        signal_strength=_strength(final_score, final_confidence),
        warnings=warnings,
        reasons=reasons,
    )
