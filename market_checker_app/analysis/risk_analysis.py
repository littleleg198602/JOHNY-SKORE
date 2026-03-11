from __future__ import annotations

from market_checker_app.models import BehavioralAnalysisResult, NewsAnalysisResult, RiskAnalysisResult, TechAnalysisResult, YahooAnalysisResult


def _clip(value: float) -> float:
    return max(0.0, min(100.0, value))


def analyze_risk(
    ticker: str,
    news: NewsAnalysisResult,
    tech: TechAnalysisResult,
    yahoo: YahooAnalysisResult,
    behavioral: BehavioralAnalysisResult,
) -> RiskAnalysisResult:
    atr = float(tech.indicators.get("atr14") or 0.0)
    close_ref = float(tech.indicators.get("sma20") or 0.0) or 1.0
    atr_ratio = atr / close_ref if close_ref else 0.0
    realized_vol = float(tech.indicators.get("realized_volatility") or 0.02)
    conflicting = int((tech.tech_score > 65 and news.news_score < 45) or (tech.tech_score < 45 and news.news_score > 65))

    risk = _clip(
        22
        + atr_ratio * 260
        + realized_vol * 420
        + (100 - news.news_confidence) * 0.10
        + (100 - tech.tech_confidence) * 0.12
        + (100 - yahoo.yahoo_confidence) * 0.10
        + max(0.0, 60 - yahoo.number_of_analyst_opinions * 3) * 0.2
        + max(0.0, 55 - news.source_diversity_score) * 0.25
        + behavioral.uncertainty_score * 0.18
        + conflicting * 8
    )

    flags: list[str] = []
    reasons: list[str] = []
    if realized_vol > 0.04:
        flags.append("high_realized_volatility")
    if atr_ratio > 0.035:
        flags.append("high_atr_ratio")
    if conflicting:
        flags.append("conflicting_module_signals")
    if news.news_confidence < 40:
        flags.append("weak_news_confidence")
    if yahoo.yahoo_confidence < 40:
        flags.append("weak_analyst_coverage")

    reasons.append(f"RiskScore uses higher=more risk; ATR ratio {atr_ratio:.3f}, realized vol {realized_vol:.3f}.")
    if flags:
        reasons.append(f"Risk flags: {', '.join(flags)}")

    return RiskAnalysisResult(ticker=ticker, risk_score=round(risk, 2), risk_flags=flags, risk_reasons=reasons)
