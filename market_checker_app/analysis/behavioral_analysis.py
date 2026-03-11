from __future__ import annotations

from market_checker_app.config import BehavioralWeights
from market_checker_app.models import BehavioralAnalysisResult, NewsAnalysisResult, TechAnalysisResult, YahooAnalysisResult


def _clip(value: float) -> float:
    return max(0.0, min(100.0, value))


def analyze_behavioral(
    ticker: str,
    news: NewsAnalysisResult,
    tech: TechAnalysisResult,
    yahoo: YahooAnalysisResult,
    weights: BehavioralWeights,
) -> BehavioralAnalysisResult:
    p1w = float(tech.indicators.get("p1w") or 0.0)
    vol = float(tech.indicators.get("realized_volatility") or 0.02)

    panic = _clip(40 + max(0.0, -p1w * 4) + vol * 500 + news.negative_articles_count * 3)
    euphoria = _clip(35 + max(0.0, p1w * 3.8) + max(0.0, news.weighted_sentiment_avg) * 30)
    capitulation = _clip(25 + max(0.0, -p1w * 5) + (100 - tech.oscillator_score) * 0.35)
    uncertainty = _clip(30 + abs(news.weighted_sentiment_avg) * 10 + news.duplicate_ratio * 40 + (100 - yahoo.yahoo_confidence) * 0.3)
    trust_breakdown = _clip(20 + news.stale_ratio * 30 + (100 - news.source_diversity_score) * 0.2 + news.negative_articles_count * 1.5)
    fomo = _clip(20 + max(0.0, p1w * 4.5) + max(0.0, tech.breakout_score - 65) * 0.5 + max(0.0, news.weighted_sentiment_avg) * 25)
    shock_surprise = _clip(25 + max(0.0, vol * 600 - 10) + max(0.0, abs(p1w) * 2.5) + (12 if "inconsistent analyst targets" in yahoo.warnings else 0))

    score = _clip(
        55
        - panic * weights.panic * 0.6
        + euphoria * weights.euphoria * 0.35
        - capitulation * weights.capitulation * 0.45
        - uncertainty * weights.uncertainty * 0.35
        - trust_breakdown * weights.trust_breakdown * 0.4
        + fomo * weights.fomo * 0.3
        - shock_surprise * weights.shock_surprise * 0.25
    )

    regime = "calm"
    if panic > 75:
        regime = "panic"
    elif euphoria > 75:
        regime = "euphoric"
    elif uncertainty > 60:
        regime = "nervous"
    elif uncertainty > 45:
        regime = "cautious"

    signal_strength = max(abs(p1w), abs(news.weighted_sentiment_avg) * 30, vol * 1000)
    confidence = _clip(30 + min(1.0, news.news_count_total / 12) * 22 + min(1.0, tech.candles_count / 180) * 20 + min(1.0, yahoo.number_of_analyst_opinions / 20) * 15 + min(25.0, signal_strength * 0.4))

    reasons = [f"Behavioral regime {regime} with panic={panic:.1f}, euphoria={euphoria:.1f}."]
    warnings = []
    if regime in {"panic", "nervous"}:
        warnings.append("elevated behavioral stress")

    return BehavioralAnalysisResult(
        ticker=ticker,
        behavioral_score=round(score, 2),
        behavioral_confidence=round(confidence, 2),
        panic_score=round(panic, 2),
        euphoria_score=round(euphoria, 2),
        capitulation_score=round(capitulation, 2),
        uncertainty_score=round(uncertainty, 2),
        trust_breakdown_score=round(trust_breakdown, 2),
        fomo_score=round(fomo, 2),
        shock_surprise_score=round(shock_surprise, 2),
        behavioral_regime=regime,
        reasons=reasons,
        warnings=warnings,
    )
