from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass(slots=True)
class NewsItem:
    ticker: str
    source: str
    title: str
    summary: str
    published_at: datetime
    sentiment_weight: float
    url: str


@dataclass(slots=True)
class ArticleFeatures:
    ticker: str
    source: str
    published_at: datetime
    age_hours: float
    title: str
    summary: str
    source_trust: float
    ticker_relevance: float
    sentiment_raw: float
    importance_raw: float
    recency_weight: float
    duplicate_penalty: float
    final_article_weight: float
    is_duplicate: bool


@dataclass(slots=True)
class NewsAnalysisResult:
    ticker: str
    news_score: float
    news_confidence: float
    news_count_total: int
    news_count_48h: int
    unique_sources_count: int
    avg_source_trust: float
    high_importance_count: int
    weighted_sentiment_sum: float
    weighted_sentiment_avg: float
    positive_articles_count: int
    negative_articles_count: int
    duplicate_ratio: float
    stale_ratio: float
    fresh_ratio: float
    source_diversity_score: float
    warnings: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    article_features: list[ArticleFeatures] = field(default_factory=list)


@dataclass(slots=True)
class TechAnalysisResult:
    ticker: str
    tech_score: float
    tech_confidence: float
    trend_score: float
    momentum_score: float
    oscillator_score: float
    macd_score: float
    breakout_score: float
    volume_confirmation_score: float
    volatility_context_adjustment: float
    source: str
    candles_count: int
    regime: str
    warnings: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    indicators: dict[str, float | None] = field(default_factory=dict)


@dataclass(slots=True)
class YahooAnalysisResult:
    ticker: str
    yahoo_score: float
    yahoo_confidence: float
    analyst_sentiment_score: float
    target_attractiveness_score: float
    fundamental_quality_score: float
    valuation_sanity_score: float
    number_of_analyst_opinions: int
    missing_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BehavioralAnalysisResult:
    ticker: str
    behavioral_score: float
    behavioral_confidence: float
    panic_score: float
    euphoria_score: float
    capitulation_score: float
    uncertainty_score: float
    trust_breakdown_score: float
    fomo_score: float
    shock_surprise_score: float
    behavioral_regime: str
    warnings: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RiskAnalysisResult:
    ticker: str
    risk_score: float  # higher == higher risk
    risk_flags: list[str] = field(default_factory=list)
    risk_reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ConfidenceResult:
    news_confidence: float
    tech_confidence: float
    yahoo_confidence: float
    behavioral_confidence: float
    data_quality_score: float
    final_confidence: float


@dataclass(slots=True)
class SignalDiagnostics:
    raw_total_score: float
    quality_adjusted_score: float
    risk_adjusted_score: float
    final_total_score: float
    final_confidence: float
    data_quality_score: float
    signal: str
    signal_strength: str
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    key_drivers: list[str] = field(default_factory=list)
    overall_summary: str = ""


@dataclass(slots=True)
class YahooSnapshot:
    ticker: str
    data: dict[str, Any]
    status: str


@dataclass(slots=True)
class PerformanceSnapshot:
    ticker: str
    last_week_change_pct: Optional[float]
    last_14d_change_pct: Optional[float]
    last_1m_change_pct: Optional[float]
    last_3m_change_pct: Optional[float]


@dataclass(slots=True)
class SignalHistoryRow:
    ticker: str
    scoring_version: str
    legacy_total_score: float
    legacy_signal: str
    tech_source_used: str
    final_total_score: float
    signal: str


@dataclass(slots=True)
class RunMetadata:
    started_at: datetime
    finished_at: datetime
    watchlist_size: int
    processed_symbols: int
    warnings_count: int
    errors_count: int
    excel_path: str = ""


@dataclass(slots=True)
class AnalysisLogEvent:
    timestamp: str
    ticker: str
    event_type: str
    message: str


@dataclass(slots=True)
class AnalysisProgressState:
    total_symbols: int
    processed_symbols: int = 0
    current_position: int = 0
    current_symbol: str = ""
    current_step: str = "start"
    current_message: str = "Připravuji analýzu"
    overall_progress: float = 0.0
    ticker_progress: float = 0.0
    recent_logs: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fallbacks: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    completed_rows: list[dict[str, object]] = field(default_factory=list)
