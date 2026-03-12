from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_OUTPUT_DIR = Path("outputs")
DEFAULT_DB_PATH = DEFAULT_OUTPUT_DIR / "market_checker_history.db"
DEFAULT_MAX_RSS_ITEMS = 30


@dataclass(slots=True)
class ModuleWeights:
    news: float = 0.28
    tech: float = 0.30
    yahoo: float = 0.22
    behavioral: float = 0.20


@dataclass(slots=True)
class BehavioralWeights:
    panic: float = 0.2
    euphoria: float = 0.15
    capitulation: float = 0.15
    uncertainty: float = 0.15
    trust_breakdown: float = 0.15
    fomo: float = 0.1
    shock_surprise: float = 0.1


@dataclass(slots=True)
class AdjustmentConfig:
    quality_center: float = 50.0
    quality_coef: float = 0.12
    risk_center: float = 45.0
    risk_coef: float = 0.16


@dataclass(slots=True)
class SignalThresholds:
    strong_buy: float = 76.0
    buy: float = 63.0
    hold: float = 47.0
    sell: float = 36.0


@dataclass(slots=True)
class RegimeOverrides:
    trend_multiplier: float = 1.08
    range_multiplier: float = 1.08
    behavior_multiplier: float = 1.15


@dataclass(slots=True)
class AppConfig:
    output_dir: Path = DEFAULT_OUTPUT_DIR
    marketcap_file: str = ""
    export_excel: bool = True
    compare_previous_run: bool = True
    save_history: bool = True
    sqlite_path: Path = DEFAULT_DB_PATH
    max_rss_items_per_source: int = DEFAULT_MAX_RSS_ITEMS
    module_weights: ModuleWeights = field(default_factory=ModuleWeights)
    behavioral_weights: BehavioralWeights = field(default_factory=BehavioralWeights)
    adjustment: AdjustmentConfig = field(default_factory=AdjustmentConfig)
    signal_thresholds: SignalThresholds = field(default_factory=SignalThresholds)
    regime_overrides: RegimeOverrides = field(default_factory=RegimeOverrides)

    def ensure_output_dir(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
