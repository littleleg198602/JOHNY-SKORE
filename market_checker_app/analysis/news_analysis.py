from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime, timezone

from market_checker_app.models import ArticleFeatures, NewsAnalysisResult, NewsItem
from market_checker_app.utils.text import normalize_text

POSITIVE = {"beat", "beats", "growth", "upgrade", "upgraded", "outperform", "strong", "record", "profit", "bullish", "guidance raised"}
NEGATIVE = {"miss", "misses", "downgrade", "downgraded", "lawsuit", "probe", "weak", "loss", "bearish", "guidance cut"}
STRONG_POSITIVE = {"surge", "acquisition", "buyback", "sec approval", "raises guidance"}
STRONG_NEGATIVE = {"investigation", "halt", "fraud", "bankruptcy", "sec probe"}
HIGH_IMPORTANCE = {"earnings", "guidance", "merger", "acquisition", "sec", "regulatory", "investigation", "halt", "downgrade", "upgrade"}

SOURCE_TRUST = {
    "reuters": 1.0,
    "sec": 1.0,
    "bloomberg": 0.9,
    "ft.com": 0.9,
    "wsj": 0.9,
    "cnbc": 0.8,
    "yahoo": 0.65,
    "benzinga": 0.6,
    "marketwatch": 0.6,
}


def _source_trust(source: str) -> float:
    src = source.lower()
    for key, trust in SOURCE_TRUST.items():
        if key in src:
            return trust
    return 0.4


def _calc_sentiment(text: str) -> float:
    t = normalize_text(text)
    score = 0.0
    for token in POSITIVE:
        if token in t:
            score += 1
    for token in NEGATIVE:
        if token in t:
            score -= 1
    for token in STRONG_POSITIVE:
        if token in t:
            score += 2
    for token in STRONG_NEGATIVE:
        if token in t:
            score -= 2
    if re.search(r"\bnot\s+(good|strong|beat|upgrade)\b", t):
        score -= 1.5
    if re.search(r"\bnot\s+(bad|weak|miss|downgrade)\b", t):
        score += 1.0
    return max(-1.0, min(1.0, score / 5.0))


def _importance(text: str) -> float:
    t = normalize_text(text)
    hits = sum(1 for k in HIGH_IMPORTANCE if k in t)
    if hits >= 2:
        return 1.0
    if hits == 1:
        return 0.75
    if "commentary" in t or "opinion" in t:
        return 0.3
    return 0.2


def _relevance(ticker: str, title: str, summary: str, url: str) -> float:
    up = ticker.upper()
    if up in title.upper():
        return 1.0
    if up in summary.upper():
        return 0.8
    if up in url.upper():
        return 0.6
    return 0.35


def _recency_weight(age_hours: float, half_life_hours: float = 48.0) -> float:
    decay = math.exp(-math.log(2) * age_hours / half_life_hours)
    return max(0.08, decay)


def analyze_news(ticker: str, articles: list[NewsItem]) -> NewsAnalysisResult:
    now = datetime.now(timezone.utc)
    if not articles:
        return NewsAnalysisResult(
            ticker=ticker,
            news_score=45.0,
            news_confidence=20.0,
            news_count_total=0,
            news_count_48h=0,
            unique_sources_count=0,
            high_importance_count=0,
            weighted_sentiment_sum=0.0,
            weighted_sentiment_avg=0.0,
            positive_articles_count=0,
            negative_articles_count=0,
            duplicate_ratio=0.0,
            stale_ratio=1.0,
            fresh_ratio=0.0,
            source_diversity_score=0.0,
            warnings=["no recent news"],
            reasons=["No relevant news articles were detected."],
        )

    norm_titles = [normalize_text(a.title) for a in articles]
    counts = Counter(norm_titles)
    features: list[ArticleFeatures] = []
    positive = negative = high_importance = fresh = stale = 0
    weighted_sum = weight_total = 0.0

    for article, norm_title in zip(articles, norm_titles):
        age_hours = max(0.0, (now - article.published_at).total_seconds() / 3600)
        sentiment = _calc_sentiment(f"{article.title} {article.summary}")
        importance = _importance(f"{article.title} {article.summary}")
        trust = _source_trust(article.source)
        relevance = _relevance(ticker, article.title, article.summary, article.url)
        recency = _recency_weight(age_hours)
        dupe_penalty = 1.0 / counts[norm_title]
        final_weight = trust * relevance * importance * recency * dupe_penalty

        if sentiment > 0.05:
            positive += 1
        if sentiment < -0.05:
            negative += 1
        if importance >= 0.75:
            high_importance += 1
        if age_hours <= 48:
            fresh += 1
        if age_hours > 24 * 14:
            stale += 1

        weighted_sum += sentiment * final_weight
        weight_total += final_weight
        features.append(ArticleFeatures(ticker, article.source, article.published_at, age_hours, article.title, article.summary, trust, relevance, sentiment, importance, recency, dupe_penalty, final_weight))

    weighted_avg = weighted_sum / weight_total if weight_total > 0 else 0.0
    total = len(articles)
    unique_sources = len({a.source for a in articles})
    duplicate_ratio = 1 - (len(counts) / total)
    source_diversity = min(1.0, unique_sources / 6.0)

    sentiment_component = 50 + weighted_avg * 35
    importance_component = min(100.0, 40 + high_importance * 8)
    coverage_component = min(100.0, 30 + math.log1p(total) * 20)
    freshness_component = min(100.0, 25 + (fresh / total) * 75)
    diversity_component = source_diversity * 100
    penalties = duplicate_ratio * 20 + (stale / total) * 15
    news_score = max(0.0, min(100.0, sentiment_component * 0.35 + importance_component * 0.2 + coverage_component * 0.15 + freshness_component * 0.15 + diversity_component * 0.15 - penalties))

    confidence = max(0.0, min(100.0, (min(1.0, total / 12) * 30 + source_diversity * 20 + (fresh / total) * 20 + (sum(f.source_trust for f in features) / total) * 20 + (sum(f.ticker_relevance for f in features) / total) * 15 - duplicate_ratio * 20)))

    warnings: list[str] = []
    if fresh == 0:
        warnings.append("stale news fallback used")
    if source_diversity < 0.35:
        warnings.append("low source diversity")

    reasons = [
        f"News sentiment average is {weighted_avg:.2f} with {positive} positive vs {negative} negative articles.",
        f"{high_importance} high-importance articles detected from {unique_sources} unique sources.",
    ]

    return NewsAnalysisResult(
        ticker=ticker,
        news_score=round(news_score, 2),
        news_confidence=round(confidence, 2),
        news_count_total=total,
        news_count_48h=fresh,
        unique_sources_count=unique_sources,
        high_importance_count=high_importance,
        weighted_sentiment_sum=round(weighted_sum, 4),
        weighted_sentiment_avg=round(weighted_avg, 4),
        positive_articles_count=positive,
        negative_articles_count=negative,
        duplicate_ratio=round(duplicate_ratio, 4),
        stale_ratio=round(stale / total, 4),
        fresh_ratio=round(fresh / total, 4),
        source_diversity_score=round(diversity_component, 2),
        warnings=warnings,
        reasons=reasons,
        article_features=features,
    )
