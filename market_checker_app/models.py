from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class NewsItem:
    ticker: str
    source: str
    title: str
    published_at: datetime
    sentiment_weight: float
    url: str


@dataclass(slots=True)
class TechSnapshot:
    ticker: str
    rsi: Optional[float]
    macd: Optional[float]
    sma_20: Optional[float]
    sma_50: Optional[float]
    close: Optional[float]
    status: str


@dataclass(slots=True)
class YahooSnapshot:
    ticker: str
    beta: Optional[float]
    trailing_pe: Optional[float]
    recommendation_key: str
    analyst_target_price: Optional[float]
    status: str


@dataclass(slots=True)
class PerformanceSnapshot:
    ticker: str
    last_week_change_pct: Optional[float]
    last_1m_change_pct: Optional[float]
    last_3m_change_pct: Optional[float]


@dataclass(slots=True)
class SignalRow:
    ticker: str
    market_cap_usd: Optional[float]
    rank_market_cap: Optional[int]
    news_weighted_48h: float
    news_volume_48h: int
    news_score: float
    tech_score: float
    yahoo_score: float
    total_score: float
    signal: str
    tech_status: str
    yahoo_status: str
    last_week_change_pct: Optional[float]
    last_1m_change_pct: Optional[float]
    last_3m_change_pct: Optional[float]


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
class RunResult:
    run_metadata: RunMetadata
    signals: list[SignalRow] = field(default_factory=list)
    articles: list[NewsItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
