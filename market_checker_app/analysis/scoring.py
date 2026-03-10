from __future__ import annotations

from market_checker_app.models import SignalRow, TechSnapshot, YahooSnapshot


def score_news(news_weighted_48h: float, news_volume_48h: int) -> float:
    return round(min(100.0, news_weighted_48h * 10 + news_volume_48h * 1.5), 2)


def score_tech(snapshot: TechSnapshot) -> float:
    score = 50.0
    if snapshot.rsi is not None:
        if snapshot.rsi < 30:
            score += 25
        elif snapshot.rsi > 70:
            score -= 25
    if snapshot.close is not None and snapshot.sma_20 is not None and snapshot.close > snapshot.sma_20:
        score += 10
    if snapshot.close is not None and snapshot.sma_50 is not None and snapshot.close > snapshot.sma_50:
        score += 15
    return round(max(0, min(100, score)), 2)


def score_yahoo(snapshot: YahooSnapshot) -> float:
    mapping = {
        "strong_buy": 90,
        "buy": 75,
        "hold": 50,
        "underperform": 35,
        "sell": 20,
    }
    return float(mapping.get(snapshot.recommendation_key.lower(), 50))


def combine_scores(news_score: float, tech_score: float, yahoo_score: float) -> float:
    return round(news_score * 0.35 + tech_score * 0.35 + yahoo_score * 0.30, 2)


def decide_signal(total_score: float) -> str:
    if total_score >= 80:
        return "STRONG BUY"
    if total_score >= 65:
        return "BUY"
    if total_score >= 50:
        return "HOLD"
    if total_score >= 35:
        return "SELL"
    return "STRONG SELL"


def enrich_signal(row: SignalRow) -> SignalRow:
    row.signal = decide_signal(row.total_score)
    return row
