from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


DEFAULT_OUTPUT_DIR = Path("outputs")
DEFAULT_DB_PATH = DEFAULT_OUTPUT_DIR / "market_checker_history.db"
DEFAULT_MAX_RSS_ITEMS = 30
DEFAULT_MAX_TICKERS_PER_RUN = 1000
DEFAULT_LARGE_UNIVERSE_THRESHOLD = 100


@dataclass(slots=True)
class ModuleWeights:
    # Legacy linear score (kept for backward compatibility / comparison)
    news: float = 0.40
    tech: float = 0.20
    yahoo: float = 0.20
    behavioral: float = 0.20


@dataclass(slots=True)
class DecisionModuleWeights:
    # New dual-axis decision engine
    technical: float = 0.30
    news: float = 0.40
    panic: float = 0.20
    analysts: float = 0.10


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
    # Legacy fallback thresholds
    strong_buy: float = 68.0
    buy: float = 58.0
    hold: float = 47.0
    sell: float = 38.0


@dataclass(slots=True)
class DecisionThresholds:
    strong_buy_min_bull_score: float = 68.0
    strong_buy_min_spread: float = 18.0
    buy_min_spread: float = 8.0
    hold_band: float = 7.0
    sell_min_spread: float = -8.0
    strong_sell_min_bear_score: float = 68.0
    strong_sell_min_negative_spread: float = -18.0
    minimum_confidence_buy: float = 0.50
    minimum_confidence_strong: float = 0.62
    panic_block_threshold: float = 72.0


@dataclass(slots=True)
class PredictionV21Config:
    """Conservative action guard layered on top of the directional model.

    The model may still publish an UP/DOWN/FLAT forecast, but an executable
    BUY/SELL action is emitted only when an independent confirmation path is
    present and no hard risk veto is active.
    """

    # The decision engine already applies its 0.50 gate before conflict
    # penalties.  This lower post-penalty floor rejects only genuinely weak
    # remnants without discarding otherwise valid consensus trades.
    minimum_action_confidence: float = 0.30
    extreme_panic_threshold: float = 85.0
    strong_signal_levels: tuple[str, ...] = ("strong", "very strong")
    blocked_risk_flags: tuple[str, ...] = (
        "high_atr_ratio",
        "conflicting_module_signals",
    )


@dataclass(slots=True)
class QualityGateConfig:
    max_signal_age_minutes: float = 15.0
    max_future_clock_skew_minutes: float = 1.0
    require_full_v21_coverage: bool = True
    require_external_provenance: bool = True
    provenance_exempt_event_types: tuple[str, ...] = ("PREDICTION_V21",)


@dataclass(slots=True)
class FundamentalIngestionConfig:
    """Stage 2 SEC ingestion settings.

    Live SEC access is opt-in because a declared application/contact
    User-Agent is mandatory and a large watchlist can generate many requests.
    The resulting data remain audit-only in this stage.
    """

    enabled: bool = False
    user_agent: str = ""
    forms: tuple[str, ...] = ("10-K", "10-Q", "8-K")
    max_filings_per_ticker: int = 6
    max_facts_per_concept: int = 4
    request_timeout_seconds: float = 20.0
    min_request_interval_seconds: float = 0.125
    fact_concepts: tuple[str, ...] = (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "Revenues",
        "NetIncomeLoss",
        "ProfitLoss",
        "OperatingIncomeLoss",
        "GrossProfit",
        "Assets",
        "AssetsCurrent",
        "Liabilities",
        "LiabilitiesCurrent",
        "StockholdersEquity",
        "CashAndCashEquivalentsAtCarryingValue",
        "NetCashProvidedByUsedInOperatingActivities",
        "CashFlowsFromUsedInOperatingActivities",
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PurchaseOfPropertyPlantAndEquipment",
        "LongTermDebtCurrent",
        "LongTermDebtNoncurrent",
        "LongTermDebt",
        "ShortTermBorrowings",
        "AccountsReceivableNetCurrent",
        "InventoryNet",
        "EarningsPerShareDiluted",
    )


@dataclass(slots=True)
class FinancialForensicsConfig:
    """Conservative shadow-screen thresholds for normalized filing facts."""

    enabled: bool = True
    minimum_metric_coverage: float = 0.35
    low_cash_conversion_ratio: float = 0.70
    high_liabilities_to_assets_ratio: float = 0.85
    critical_liabilities_to_assets_ratio: float = 1.00
    high_debt_to_assets_ratio: float = 0.50
    critical_debt_to_assets_ratio: float = 0.70
    low_current_ratio: float = 1.00
    critical_current_ratio: float = 0.75
    working_capital_growth_divergence_pct: float = 20.0
    material_restatement_pct: float = 2.0
    quarterly_filing_lag_days: int = 60
    annual_filing_lag_days: int = 120


@dataclass(frozen=True, slots=True)
class ShortReportSourceConfig:
    """One explicitly configured public report and its point-in-time metadata."""

    ticker: str
    publisher: str
    published_at: datetime
    url: str


@dataclass(slots=True)
class ShortReportConfig:
    """Opt-in ingestion of explicitly supplied short-report URLs."""

    enabled: bool = False
    sources: tuple[ShortReportSourceConfig, ...] = ()
    user_agent: str = "JohnySkore/2.1 short-report-audit"
    request_timeout_seconds: float = 20.0
    max_download_bytes: int = 8_000_000
    max_text_characters: int = 500_000
    max_claims_per_report: int = 25
    minimum_claim_characters: int = 40


@dataclass(slots=True)
class ClaimVerificationConfig:
    """Conservative structured checks against already ingested SEC evidence."""

    enabled: bool = True
    minimum_forensic_confidence: float = 0.35
    healthy_cash_conversion_ratio: float = 0.90
    healthy_current_ratio: float = 1.20
    healthy_debt_to_assets_ratio: float = 0.40
    healthy_liabilities_to_assets_ratio: float = 0.70
    max_healthy_working_capital_divergence_pct: float = 10.0


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
    max_tickers_per_run: int = DEFAULT_MAX_TICKERS_PER_RUN
    large_universe_threshold: int = DEFAULT_LARGE_UNIVERSE_THRESHOLD
    agent_stage1_enabled: bool = True
    agent_shadow_mode: bool = True
    module_weights: ModuleWeights = field(default_factory=ModuleWeights)
    decision_weights: DecisionModuleWeights = field(default_factory=DecisionModuleWeights)
    decision_thresholds: DecisionThresholds = field(default_factory=DecisionThresholds)
    prediction_v21: PredictionV21Config = field(default_factory=PredictionV21Config)
    quality_gate: QualityGateConfig = field(default_factory=QualityGateConfig)
    fundamental_ingestion: FundamentalIngestionConfig = field(
        default_factory=FundamentalIngestionConfig
    )
    financial_forensics: FinancialForensicsConfig = field(
        default_factory=FinancialForensicsConfig
    )
    short_reports: ShortReportConfig = field(default_factory=ShortReportConfig)
    claim_verification: ClaimVerificationConfig = field(
        default_factory=ClaimVerificationConfig
    )
    behavioral_weights: BehavioralWeights = field(default_factory=BehavioralWeights)
    adjustment: AdjustmentConfig = field(default_factory=AdjustmentConfig)
    signal_thresholds: SignalThresholds = field(default_factory=SignalThresholds)
    regime_overrides: RegimeOverrides = field(default_factory=RegimeOverrides)

    def ensure_output_dir(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
