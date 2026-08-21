from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math
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
class DecisionAgentConfig:
    """Conservative Stage 4 overlay; collected in shadow mode by default."""

    enabled: bool = True
    policy_name: str = "conservative_risk_overlay"
    policy_version: str = "1.0"
    suppression_score_threshold: float = 3.0
    probability_flat_shift: float = 0.18
    minimum_forensic_confidence: float = 0.50
    minimum_claim_confidence: float = 0.50
    minimum_regulatory_confidence: float = 0.70
    live_application_enabled: bool = False
    live_policy_allowlist: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.policy_name.strip() or not self.policy_version.strip():
            raise ValueError("Stage 4 policy name and version must not be empty")
        if (
            not math.isfinite(self.suppression_score_threshold)
            or self.suppression_score_threshold <= 0.0
        ):
            raise ValueError("suppression_score_threshold must be positive")
        for label in (
            "probability_flat_shift",
            "minimum_forensic_confidence",
            "minimum_claim_confidence",
            "minimum_regulatory_confidence",
        ):
            value = float(getattr(self, label))
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{label} must be between 0 and 1")


@dataclass(slots=True)
class EvaluationAgentConfig:
    """Out-of-sample evidence required before a Stage 4 policy can be enabled."""

    enabled: bool = True
    minimum_oos_samples: int = 200
    minimum_distinct_weeks: int = 12
    minimum_lift_pct_points: float = 2.0
    minimum_lift_lower_bound_pct_points: float = 0.0
    minimum_positive_week_ratio: float = 0.60
    minimum_coverage_pct: float = 35.0
    maximum_false_positive_increase_pct_points: float = 0.0
    maximum_brier_increase: float = 0.0
    maximum_calibration_error_increase: float = 0.0
    required_consecutive_passes: int = 3
    hold_tolerance_pct: float = 2.0
    minimum_weekly_gap_days: float = 4.0
    maximum_weekly_gap_days: float = 10.0
    enable_after_gate: bool = False
    enabled_policy_allowlist: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.minimum_oos_samples < 1:
            raise ValueError("minimum_oos_samples must be at least 1")
        if self.minimum_distinct_weeks < 1:
            raise ValueError("minimum_distinct_weeks must be at least 1")
        if self.required_consecutive_passes < 1:
            raise ValueError("required_consecutive_passes must be at least 1")
        if not 0.0 <= self.minimum_coverage_pct <= 100.0:
            raise ValueError("minimum_coverage_pct must be between 0 and 100")
        if not 0.0 <= self.minimum_positive_week_ratio <= 1.0:
            raise ValueError("minimum_positive_week_ratio must be between 0 and 1")
        if self.hold_tolerance_pct < 0.0:
            raise ValueError("hold_tolerance_pct must not be negative")
        if (
            self.minimum_weekly_gap_days < 0.0
            or self.maximum_weekly_gap_days < self.minimum_weekly_gap_days
        ):
            raise ValueError("weekly gap bounds are invalid")
        for label in (
            "minimum_lift_pct_points",
            "minimum_lift_lower_bound_pct_points",
            "maximum_false_positive_increase_pct_points",
            "maximum_brier_increase",
            "maximum_calibration_error_increase",
        ):
            if not math.isfinite(float(getattr(self, label))):
                raise ValueError(f"{label} must be finite")


@dataclass(slots=True)
class FundamentalIngestionConfig:
    """Stage 2 SEC ingestion settings.

    Live SEC access is opt-in because a declared application/contact
    User-Agent is mandatory and a large watchlist can generate many requests.
    The resulting data remain audit-only in this stage.
    """

    enabled: bool = False
    user_agent: str = ""
    forms: tuple[str, ...] = (
        "10-K",
        "10-Q",
        "8-K",
        "20-F",
        "6-K",
        "40-F",
    )
    max_filings_per_ticker: int = 6
    max_facts_per_concept: int = 4
    extract_latest_10k_text: bool = True
    max_text_filings_per_ticker: int = 1
    max_filing_download_bytes: int = 20_000_000
    max_filing_text_characters: int = 750_000
    request_timeout_seconds: float = 20.0
    min_request_interval_seconds: float = 0.125
    fact_concepts: tuple[str, ...] = (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "Revenues",
        "Revenue",
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
        "CashAndCashEquivalents",
        "NetCashProvidedByUsedInOperatingActivities",
        "CashFlowsFromUsedInOperatingActivities",
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PurchaseOfPropertyPlantAndEquipment",
        "PropertyPlantAndEquipment",
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
    discovery_method: str = "manual"


@dataclass(slots=True)
class ShortReportConfig:
    """Opt-in ingestion of explicitly supplied short-report URLs."""

    enabled: bool = False
    sources: tuple[ShortReportSourceConfig, ...] = ()
    auto_discover_from_news: bool = True
    max_auto_discovered_reports: int = 10
    user_agent: str = "JohnySkore/2.1 short-report-audit"
    request_timeout_seconds: float = 20.0
    max_download_bytes: int = 8_000_000
    max_text_characters: int = 500_000
    max_claims_per_report: int = 25
    minimum_claim_characters: int = 40

    def __post_init__(self) -> None:
        if self.max_auto_discovered_reports < 0:
            raise ValueError("max_auto_discovered_reports must not be negative")


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


@dataclass(frozen=True, slots=True)
class SupplyChainSourceConfig:
    """One explicit company relationship backed by a public source."""

    ticker: str
    counterparty: str
    relationship_type: str
    publisher: str
    published_at: datetime
    url: str
    dependency_pct: float | None = None
    confidence: float = 1.0


@dataclass(slots=True)
class Stage3SourceVerificationConfig:
    """Safe content attestation for Stage 3 public source references."""

    enabled: bool = False
    user_agent: str = "JohnySkore/2.1 stage3-source-audit"
    request_timeout_seconds: float = 20.0
    max_download_bytes: int = 8_000_000
    max_text_characters: int = 500_000

    def __post_init__(self) -> None:
        if (
            not math.isfinite(float(self.request_timeout_seconds))
            or self.request_timeout_seconds <= 0.0
        ):
            raise ValueError("request_timeout_seconds must be positive")
        if self.max_download_bytes < 1_024:
            raise ValueError("max_download_bytes must be at least 1024")
        if self.max_text_characters < 1_000:
            raise ValueError("max_text_characters must be at least 1000")


@dataclass(slots=True)
class SupplyChainConfig:
    """Shadow-only supplier/customer intelligence with SEC 10-K discovery."""

    enabled: bool = False
    sources: tuple[SupplyChainSourceConfig, ...] = ()
    auto_discover_from_sec_filings: bool = True
    max_auto_discovered_relationships_per_filing: int = 6
    source_verification: Stage3SourceVerificationConfig = field(
        default_factory=Stage3SourceVerificationConfig
    )


@dataclass(frozen=True, slots=True)
class CommodityEnergySourceConfig:
    """One material, commodity, power, or fuel exposure with provenance."""

    ticker: str
    resource_name: str
    exposure_type: str
    publisher: str
    published_at: datetime
    url: str
    dependency_pct: float | None = None
    confidence: float = 1.0


@dataclass(slots=True)
class CommodityEnergyConfig:
    """Shadow-only material/energy intelligence with SEC 10-K discovery."""

    enabled: bool = False
    sources: tuple[CommodityEnergySourceConfig, ...] = ()
    auto_discover_from_sec_filings: bool = True
    max_auto_discovered_exposures_per_filing: int = 12
    source_verification: Stage3SourceVerificationConfig = field(
        default_factory=Stage3SourceVerificationConfig
    )


@dataclass(frozen=True, slots=True)
class RegulatoryContractSourceConfig:
    """One public regulatory or contract event supplied by the user."""

    ticker: str
    event_type: str
    status: str
    title: str
    authority_or_counterparty: str
    publisher: str
    published_at: datetime
    url: str
    event_value: float | None = None
    currency: str | None = None
    confidence: float = 1.0
    discovery_method: str = "manual"


@dataclass(slots=True)
class RegulatoryContractConfig:
    """Shadow-only ingestion of explicit regulatory and contract events."""

    enabled: bool = False
    sources: tuple[RegulatoryContractSourceConfig, ...] = ()
    auto_discover_from_news: bool = True
    max_auto_discovered_events: int = 20
    source_verification: Stage3SourceVerificationConfig = field(
        default_factory=Stage3SourceVerificationConfig
    )

    def __post_init__(self) -> None:
        if self.max_auto_discovered_events < 0:
            raise ValueError("max_auto_discovered_events must not be negative")


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
    decision_agent: DecisionAgentConfig = field(default_factory=DecisionAgentConfig)
    evaluation_agent: EvaluationAgentConfig = field(
        default_factory=EvaluationAgentConfig
    )
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
    supply_chain: SupplyChainConfig = field(default_factory=SupplyChainConfig)
    commodity_energy: CommodityEnergyConfig = field(
        default_factory=CommodityEnergyConfig
    )
    regulatory_contract: RegulatoryContractConfig = field(
        default_factory=RegulatoryContractConfig
    )
    behavioral_weights: BehavioralWeights = field(default_factory=BehavioralWeights)
    adjustment: AdjustmentConfig = field(default_factory=AdjustmentConfig)
    signal_thresholds: SignalThresholds = field(default_factory=SignalThresholds)
    regime_overrides: RegimeOverrides = field(default_factory=RegimeOverrides)

    def ensure_output_dir(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
