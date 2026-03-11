from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime, timezone

from market_checker_app.models import ArticleFeatures, NewsAnalysisResult, NewsItem
from market_checker_app.utils.text import normalize_text

POSITIVE = {"beat", "growth", "upgrade", "outperform", "record", "profit", "bullish", "guidance raised"}
NEGATIVE = {"miss", "downgrade", "lawsuit", "investigation", "weak", "loss", "bearish", "guidance cut"}
HIGH_IMPORTANCE = {"earnings", "guidance", "merger", "acquisition", "regulatory", "investigation", "downgrade", "upgrade"}
SOURCE_TRUST = {"reuters": 1.0, "sec": 1.0, "bloomberg": 0.9, "cnbc": 0.8, "yahoo": 0.65, "benzinga": 0.6}


def _source_trust(source: str) -> float:
    src = source.lower()
    for key, trust in SOURCE_TRUST.items():
        if key in src:
            return trust
    return 0.45


def _calc_sentiment(text: str) -> float:
    t = normalize_text(text)
    score = sum(1 for token in POSITIVE if token in t) - sum(1 for token in NEGATIVE if token in t)
    if re.search(r"\bnot\s+(good|strong|beat|upgrade)\b", t):
        score -= 1.5
    return max(-1.0, min(1.0, score / 4.0))


def _importance(text: str) -> float:
    t = normalize_text(text)
    hits = sum(1 for k in HIGH_IMPORTANCE if k in t)
    return 1.0 if hits >= 2 else (0.7 if hits == 1 else 0.3)


def _relevance(ticker: str, title: str, summary: str, url: str) -> float:
    up = ticker.upper()
    if up in title.upper():
        return 1.0
    if up in summary.upper():
        return 0.8
    if up in url.upper():
        return 0.6
    return 0.3


def _recency_weight(age_hours: float, half_life_hours: float = 36.0) -> float:
    return max(0.05, math.exp(-math.log(2) * age_hours / half_life_hours))


def analyze_news(ticker: str, articles: list[NewsItem]) -> NewsAnalysisResult:
    now = datetime.now(timezone.utc)
    if not articles:
        return NewsAnalysisResult(ticker, 42.0, 18.0, 0, 0, 0, 0.0, 0, 0.0, 0.0, 0, 0, 0.0, 1.0, 0.0, 0.0, ["no recent news"], ["No relevant news articles were detected."], [])

    norm_titles = [normalize_text(a.title) for a in articles]
    counts = Counter(norm_titles)
    features: list[ArticleFeatures] = []
    positive = negative = high_importance = fresh = stale = 0
    weighted_sum = weight_total = trust_sum = relevance_sum = 0.0

    for article, norm_title in zip(articles, norm_titles):
        age_hours = max(0.0, (now - article.published_at).total_seconds() / 3600)
        sentiment = _calc_sentiment(f"{article.title} {article.summary}")
        importance = _importance(f"{article.title} {article.summary}")
        trust = _source_trust(article.source)
        relevance = _relevance(ticker, article.title, article.summary, article.url)
        recency = _recency_weight(age_hours)
        is_duplicate = counts[norm_title] > 1
        dupe_penalty = 1.0 / counts[norm_title]
        final_weight = trust * relevance * importance * recency * dupe_penalty

        positive += int(sentiment > 0.05)
        negative += int(sentiment < -0.05)
        high_importance += int(importance >= 0.7)
        fresh += int(age_hours <= 48)
        stale += int(age_hours > 24 * 14)

        weighted_sum += sentiment * final_weight
        weight_total += final_weight
        trust_sum += trust
        relevance_sum += relevance
        features.append(ArticleFeatures(ticker, article.source, article.published_at, age_hours, article.title, article.summary, trust, relevance, sentiment, importance, recency, dupe_penalty, final_weight, is_duplicate))

    total = len(articles)
    unique_sources = len({a.source for a in articles})
    duplicate_ratio = 1 - (len(counts) / total)
    stale_ratio = stale / total
    fresh_ratio = fresh / total
    source_diversity = min(1.0, unique_sources / 6.0)
    avg_trust = trust_sum / total
    weighted_avg = weighted_sum / weight_total if weight_total > 0 else 0.0

    sentiment_component = 50 + weighted_avg * 36
    importance_component = min(100.0, 35 + high_importance * 9)
    coverage_component = min(100.0, 25 + math.log1p(total) * 24)
    freshness_component = 30 + fresh_ratio * 70
    diversity_component = source_diversity * 100
    duplicate_penalty_component = duplicate_ratio * 18
    staleness_penalty_component = stale_ratio * 18
    low_trust_penalty_component = max(0.0, (0.65 - avg_trust) * 30)

    news_score = max(0.0, min(100.0, sentiment_component * 0.35 + importance_component * 0.18 + coverage_component * 0.14 + freshness_component * 0.15 + diversity_component * 0.18 - duplicate_penalty_component - staleness_penalty_component - low_trust_penalty_component))

    confidence = max(0.0, min(100.0, min(1.0, total / 14) * 28 + source_diversity * 20 + avg_trust * 18 + fresh_ratio * 16 + (1 - duplicate_ratio) * 10 + (relevance_sum / total) * 12))

    warnings: list[str] = []
    if source_diversity < 0.35:
        warnings.append("low source diversity")
    if stale_ratio > 0.5:
        warnings.append("stale coverage dominates")

    reasons = [
        f"Sentiment {weighted_avg:.2f}, articles {total} ({fresh} fresh / {stale} stale).",
        f"High-importance articles: {high_importance}, average source trust {avg_trust:.2f}.",
    ]

    return NewsAnalysisResult(
        ticker=ticker,
        news_score=round(news_score, 2),
        news_confidence=round(confidence, 2),
        news_count_total=total,
        news_count_48h=fresh,
        unique_sources_count=unique_sources,
        avg_source_trust=round(avg_trust, 3),
        high_importance_count=high_importance,
        weighted_sentiment_sum=round(weighted_sum, 4),
        weighted_sentiment_avg=round(weighted_avg, 4),
        positive_articles_count=positive,
        negative_articles_count=negative,
        duplicate_ratio=round(duplicate_ratio, 4),
        stale_ratio=round(stale_ratio, 4),
        fresh_ratio=round(fresh_ratio, 4),
        source_diversity_score=round(diversity_component, 2),
        warnings=warnings,
        reasons=reasons,
        article_features=features,
    )
