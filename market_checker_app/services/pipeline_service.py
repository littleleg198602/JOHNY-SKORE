from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
import time
from typing import Callable

import pandas as pd

from market_checker_app.agents import (
    AgentStatus,
    ClaimVerificationAgent,
    CommodityEnergyAgent,
    DecisionAgent,
    EntityRegistryAgent,
    EuropeanFilingsAgent,
    EvaluationAgent,
    FinancialForensicsAgent,
    GovernanceEventAgent,
    OrchestrationReport,
    OrchestratorAgent,
    PredictionV21AdapterAgent,
    QualityGateAgent,
    RegulatoryContractAgent,
    SecFundamentalsAgent,
    ShortReportAgent,
    SourceResolutionAgent,
    SupplyChainAgent,
)
from market_checker_app.analysis.behavioral_analysis import analyze_behavioral
from market_checker_app.analysis.confidence import combine_confidence
from market_checker_app.analysis.explanations import build_key_drivers, merge_reasons, merge_warnings
from market_checker_app.analysis.news_analysis import analyze_news
from market_checker_app.analysis.regime_detection import detect_market_regime
from market_checker_app.analysis.risk_analysis import analyze_risk
from market_checker_app.analysis.scoring import (
    apply_regime_overrides,
    compute_legacy_total,
    compute_raw_total,
    finalize_signal,
    legacy_signal_from_score,
)
from market_checker_app.analysis.tech_analysis import analyze_tech
from market_checker_app.analysis.yahoo_analysis import analyze_yahoo
from market_checker_app.collectors.marketcap_loader import load_market_caps
from market_checker_app.collectors.gleif_client import GleifClient
from market_checker_app.collectors.mt5_client import MT5Client
from market_checker_app.collectors.rss_client import RSSClient
from market_checker_app.collectors.yahoo_client import YahooClient
from market_checker_app.config import AppConfig
from market_checker_app.models import (
    AnalysisProgressState,
    NewsItem,
    PerformanceSnapshot,
    RunMetadata,
    YahooAnalysisResult,
    YahooSnapshot,
)
from market_checker_app.prediction_contract import benchmark_for_sector
from market_checker_app.services.market_factor_service import (
    build_market_factor_snapshot,
)
from market_checker_app.services.ohlc_quality import assess_daily_ohlc
from market_checker_app.services.progress_service import ProgressService
from market_checker_app.services.ranking_service import RankingService
from market_checker_app.services.stage4_evaluation_service import (
    Stage4EvaluationService,
)
from market_checker_app.services.source_discovery_service import SourceDiscoveryService
from market_checker_app.storage.sqlite_store import SQLiteStore
from market_checker_app.storage.yahoo_cache_store import YahooCacheStore
from market_checker_app.storage.yahoo_ohlc_cache_store import YahooOhlcCacheStore
from market_checker_app.utils.dates import utc_now


SCORING_VERSION = "v2.1_guarded_consensus"


class PipelineService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.mt5_client = MT5Client()
        self.rss_client = RSSClient(max_items_per_source=config.max_rss_items_per_source)
        self.yahoo_client = YahooClient()
        self.yahoo_cache = YahooCacheStore(config.sqlite_path)
        self.yahoo_ohlc_cache = YahooOhlcCacheStore(config.sqlite_path)
        self.gleif_client = None
        self.sec_client = None
        self.european_filing_client = None
        self.european_filing_feed_client = None
        self.short_report_client = None
        self.stage3_source_client = None

    def _run_agents(
        self,
        watchlist: list[str],
        signals: pd.DataFrame,
        store: SQLiteStore | None = None,
        news_items: list[NewsItem] | None = None,
    ) -> OrchestrationReport:
        discovered = SourceDiscoveryService().discover(
            list(news_items or []),
            as_of=utc_now(),
            discover_short_reports=self.config.short_reports.auto_discover_from_news,
            discover_regulatory_events=(
                self.config.regulatory_contract.auto_discover_from_news
            ),
            max_short_reports=self.config.short_reports.max_auto_discovered_reports,
            max_regulatory_events=(
                self.config.regulatory_contract.max_auto_discovered_events
            ),
        )

        def merge_sources(manual: tuple[object, ...], automatic: tuple[object, ...]) -> tuple[object, ...]:
            merged: list[object] = []
            seen: set[tuple[str, str, str]] = set()
            for source in manual + automatic:
                key = (
                    str(getattr(source, "ticker", "")).strip().upper(),
                    str(getattr(source, "url", "")).strip(),
                    str(getattr(source, "published_at", "")),
                )
                if key in seen:
                    continue
                seen.add(key)
                merged.append(source)
            return tuple(merged)

        short_report_sources = merge_sources(
            tuple(self.config.short_reports.sources),
            tuple(discovered.short_reports),
        )
        regulatory_sources = merge_sources(
            tuple(self.config.regulatory_contract.sources),
            tuple(discovered.regulatory_events),
        )
        short_report_config = replace(
            self.config.short_reports,
            enabled=bool(self.config.short_reports.enabled or short_report_sources),
            sources=short_report_sources,
        )
        regulatory_contract_config = replace(
            self.config.regulatory_contract,
            enabled=bool(self.config.regulatory_contract.enabled or regulatory_sources),
            sources=regulatory_sources,
        )
        identity_required_tickers = {
            str(getattr(source, "ticker", "")).strip().upper()
            for source in (
                tuple(self.config.european_filings.sources)
                + tuple(self.config.european_filings.feeds)
            )
            if str(getattr(source, "ticker", "")).strip()
        }
        identity_required_tickers.update(
            str(getattr(source, "ticker", "")).strip().upper()
            for source in regulatory_sources
            if str(getattr(source, "ticker", "")).strip()
            and str(getattr(source, "source_type", "media_article"))
            .strip()
            .lower()
            != "media_article"
        )
        agent_state: dict[str, object] = {
            "signals": signals,
            "stage4_evaluation_enabled": self.config.evaluation_agent.enabled,
            "auto_discovered_short_reports": len(discovered.short_reports),
            "auto_discovered_regulatory_events": len(discovered.regulatory_events),
            "identity_required_tickers": sorted(identity_required_tickers),
        }
        if self.config.decision_agent.enabled:
            stage4_service = Stage4EvaluationService()
            stage4_as_of = utc_now()
            try:
                if store is not None and self.config.save_history:
                    history = store.read_global_history()
                    decisions = store.read_decision_records(
                        policy_name=self.config.decision_agent.policy_name
                    )
                    activations = store.read_signal_activation_decisions(
                        self.config.decision_agent.policy_name
                    )
                else:
                    history = pd.DataFrame()
                    decisions = pd.DataFrame()
                    activations = pd.DataFrame()
                agent_state["stage4_evaluation_samples"] = stage4_service.build_samples(
                    history=history,
                    decisions=decisions,
                    current_signals=signals,
                    policy_name=self.config.decision_agent.policy_name,
                    policy_version=self.config.decision_agent.policy_version,
                    as_of=stage4_as_of,
                    hold_tolerance_pct=self.config.evaluation_agent.hold_tolerance_pct,
                    minimum_weekly_gap_days=(
                        self.config.evaluation_agent.minimum_weekly_gap_days
                    ),
                    maximum_weekly_gap_days=(
                        self.config.evaluation_agent.maximum_weekly_gap_days
                    ),
                )
                agent_state["stage4_prior_activation"] = (
                    stage4_service.latest_activation(
                        activations,
                        policy_name=self.config.decision_agent.policy_name,
                        policy_version=self.config.decision_agent.policy_version,
                        as_of=stage4_as_of,
                    )
                )
                agent_state["stage4_activation_history"] = (
                    activations.to_dict(orient="records")
                    if not activations.empty
                    else []
                )
            except Exception as exc:
                agent_state["stage4_evaluation_samples"] = []
                agent_state["stage4_prior_activation"] = {}
                agent_state["stage4_activation_history"] = []
                agent_state["stage4_preparation_error"] = (
                    f"{type(exc).__name__}: {exc}"
                )

        orchestrator = OrchestratorAgent(shadow_mode=self.config.agent_shadow_mode)
        if self.config.entity_registry.enable_gleif and self.gleif_client is None:
            self.gleif_client = GleifClient(
                timeout_seconds=(
                    self.config.entity_registry.request_timeout_seconds
                )
            )
        orchestrator.register(
            EntityRegistryAgent(
                self.config.entity_registry.identity_records,
                primary_registry_client=(
                    self.gleif_client
                    if self.config.entity_registry.enable_gleif
                    else None
                ),
            )
        )
        if self.config.fundamental_ingestion.enabled:
            orchestrator.register(
                SecFundamentalsAgent(
                    self.config.fundamental_ingestion,
                    client=self.sec_client,
                )
            )
            if self.config.financial_forensics.enabled:
                orchestrator.register(
                    FinancialForensicsAgent(self.config.financial_forensics)
                )
        if self.config.european_filings.enabled:
            orchestrator.register(
                EuropeanFilingsAgent(
                    self.config.european_filings,
                    client=self.european_filing_client,
                    discovery_client=self.european_filing_feed_client,
                )
            )
        if self.config.governance_events.enabled and (
            self.config.fundamental_ingestion.enabled
            or self.config.european_filings.enabled
        ):
            governance_dependencies = ["entity_registry"]
            if self.config.fundamental_ingestion.enabled:
                governance_dependencies.append("f2_sec")
            if self.config.european_filings.enabled:
                governance_dependencies.append("european_filings")
            orchestrator.register(
                GovernanceEventAgent(
                    self.config.governance_events,
                    dependencies=tuple(governance_dependencies),
                )
            )
        if short_report_config.enabled:
            orchestrator.register(
                ShortReportAgent(
                    short_report_config,
                    client=self.short_report_client,
                )
            )
            if (
                self.config.claim_verification.enabled
                and self.config.fundamental_ingestion.enabled
                and self.config.financial_forensics.enabled
            ):
                orchestrator.register(
                    ClaimVerificationAgent(self.config.claim_verification)
                )
        if self.config.supply_chain.enabled:
            orchestrator.register(
                SupplyChainAgent(
                    self.config.supply_chain,
                    client=self.stage3_source_client,
                    dependencies=(
                        ("entity_registry", "f2_sec")
                        if (
                            self.config.fundamental_ingestion.enabled
                            and self.config.supply_chain.auto_discover_from_sec_filings
                        )
                        else ("entity_registry",)
                    ),
                )
            )
        if self.config.commodity_energy.enabled:
            orchestrator.register(
                CommodityEnergyAgent(
                    self.config.commodity_energy,
                    client=self.stage3_source_client,
                    dependencies=(
                        ("entity_registry", "f2_sec")
                        if (
                            self.config.fundamental_ingestion.enabled
                            and self.config.commodity_energy.auto_discover_from_sec_filings
                        )
                        else ("entity_registry",)
                    ),
                )
            )
        if regulatory_contract_config.enabled:
            orchestrator.register(
                RegulatoryContractAgent(
                    regulatory_contract_config,
                    client=self.stage3_source_client,
                )
            )
        # The resolver is registered after every producer and therefore sees all
        # completed AgentResults. Optional producers must not block resolution
        # merely because they had no matching document in this run.
        orchestrator.register(
            SourceResolutionAgent(dependencies=("entity_registry",))
        )
        orchestrator.register(PredictionV21AdapterAgent())
        if self.config.decision_agent.enabled:
            if self.config.evaluation_agent.enabled:
                orchestrator.register(
                    EvaluationAgent(
                        self.config.evaluation_agent,
                        policy_name=self.config.decision_agent.policy_name,
                        policy_version=self.config.decision_agent.policy_version,
                    )
                )
            decision_dependencies = (
                (
                    "prediction_v21_adapter",
                    "evaluation_agent",
                    "source_resolution",
                )
                if self.config.evaluation_agent.enabled
                else ("prediction_v21_adapter", "source_resolution")
            )
            orchestrator.register(
                DecisionAgent(
                    self.config.decision_agent,
                    dependencies=decision_dependencies,
                )
            )
        orchestrator.register(
            QualityGateAgent(
                self.config.quality_gate,
                minimum_action_confidence=(
                    self.config.prediction_v21.minimum_action_confidence
                ),
            )
        )
        report = orchestrator.run(
            watchlist=watchlist,
            state=agent_state,
        )
        report.metadata.update(
            {
                "auto_discovered_short_reports": len(discovered.short_reports),
                "auto_discovered_regulatory_events": len(discovered.regulatory_events),
            }
        )
        return report

    @staticmethod
    def _expand_rss_sources(rss_sources: list[str], watchlist: list[str]) -> list[str]:
        expanded: list[str] = []
        for source in rss_sources:
            if "{ticker}" in source:
                expanded.extend(source.replace("{ticker}", ticker) for ticker in watchlist)
            else:
                expanded.append(source)
        return sorted(set(expanded))

    @staticmethod
    def _neutral_yahoo_result(ticker: str, reason: str) -> YahooAnalysisResult:
        return YahooAnalysisResult(
            ticker=ticker,
            yahoo_score=50.0,
            yahoo_confidence=0.0,
            analyst_sentiment_score=50.0,
            target_attractiveness_score=50.0,
            fundamental_quality_score=50.0,
            valuation_sanity_score=50.0,
            number_of_analyst_opinions=0,
            missing_fields=[reason],
            warnings=[reason],
            reasons=["Yahoo analyst/fundamental module has no directional contribution because data is unavailable."],
        )

    @staticmethod
    def _performance_from_ohlc(ticker: str, ohlc: pd.DataFrame | None) -> PerformanceSnapshot:
        def _return(days: int) -> float | None:
            if ohlc is None or ohlc.empty or "Close" not in ohlc.columns:
                return None
            close = pd.to_numeric(ohlc["Close"], errors="coerce").dropna()
            if len(close) <= days:
                return None
            latest = float(close.iloc[-1])
            base = float(close.iloc[-(days + 1)])
            if base == 0:
                return None
            return round(((latest / base) - 1) * 100, 4)

        return PerformanceSnapshot(ticker, _return(7), _return(14), _return(21), _return(63))

    @staticmethod
    def _current_price_from_ohlc(
        ohlc: pd.DataFrame | None,
        *,
        as_of: datetime | None = None,
    ) -> float | None:
        """Return only a recent, dated and positive OHLC close."""
        quality = assess_daily_ohlc(ohlc, as_of=as_of or utc_now())
        return quality.close

    @classmethod
    def _select_current_price(
        cls,
        *,
        ohlc: pd.DataFrame | None,
        tech_source: str,
        yahoo_metadata_price: object,
        as_of: datetime | None = None,
    ) -> tuple[float | None, str]:
        """Choose a validated close; an undated quote is a last-resort quote.

        If an OHLC frame exists but is stale or invalid, it is not silently
        replaced with possibly older metadata.  This makes the absence visible
        to the confidence and source-health layers.
        """
        evaluated_at = as_of or utc_now()
        quality = assess_daily_ohlc(ohlc, as_of=evaluated_at)
        close = quality.close
        if close is not None:
            if tech_source == "mt5":
                return close, "mt5_close"
            if tech_source.startswith("yfinance"):
                return close, "yahoo_ohlc_close"
            return close, "ohlc_close"

        # A stale/future positive close is evidence of a bad time series and
        # must not be masked by an undated quote.  A completely empty or
        # malformed series may still expose a quote, but it remains explicitly
        # undated and cannot make the ranking usable on its own.
        if quality.observation_count:
            return None, "ohlc_unusable"

        metadata = pd.to_numeric(
            pd.Series([yahoo_metadata_price]), errors="coerce"
        ).iloc[0]
        if pd.notna(metadata) and float(metadata) > 0:
            return float(metadata), "yahoo_metadata_quote_undated"
        return None, "missing"

    def run(
        self,
        watchlist: list[str],
        rss_sources: list[str],
        store: SQLiteStore | None,
        progress_callback: Callable[[AnalysisProgressState], None] | None = None,
        yahoo_only_tickers: set[str] | None = None,
        yahoo_only_mode: bool = False,
        rss_enabled: bool | None = None,
        mt5_enabled: bool | None = None,
        yahoo_metadata_enabled: bool | None = None,
    ) -> dict[str, object]:
        if not watchlist:
            raise ValueError("Watchlist je prázdný. Nahrajte Excel nebo zadejte alespoň jeden ticker.")
        if len(watchlist) > self.config.max_tickers_per_run:
            raise ValueError(
                f"Watchlist má {len(watchlist)} tickerů, povolené maximum pro jeden běh je "
                f"{self.config.max_tickers_per_run}. Zmenšete seznam nebo vědomě zvyšte limit v nastavení."
            )

        rss_enabled = not yahoo_only_mode if rss_enabled is None else rss_enabled
        mt5_enabled = not yahoo_only_mode if mt5_enabled is None else mt5_enabled
        total = len(watchlist)
        large_universe_mode = total > self.config.large_universe_threshold
        use_yahoo_cache = large_universe_mode
        if yahoo_metadata_enabled is None:
            yahoo_metadata_enabled = not large_universe_mode
        started_at = utc_now()
        warnings: list[str] = []
        errors: list[str] = []
        progress_on_update = progress_callback
        if large_universe_mode and progress_callback is not None:
            last_progress_emit = 0.0

            def _throttled_progress_callback(state: AnalysisProgressState) -> None:
                nonlocal last_progress_emit
                now = time.monotonic()
                if last_progress_emit == 0.0 or state.current_step == "done" or now - last_progress_emit >= 0.15:
                    last_progress_emit = now
                    progress_callback(state)

            progress_on_update = _throttled_progress_callback

        progress = ProgressService(
            total_symbols=len(watchlist),
            max_logs=30,
            on_update=progress_on_update,
        )

        progress.set_global_step("start", "Inicializuji pipeline", 0.01)
        progress.log("INFO", f"Start analýzy pro {len(watchlist)} tickerů")
        if use_yahoo_cache:
            yahoo_coverage = self.yahoo_cache.coverage(watchlist)
            warnings.append(
                f"Yahoo cache: použitelná metadata pro {yahoo_coverage.usable}/{total} tickerů "
                f"(fresh {yahoo_coverage.fresh}, stale {yahoo_coverage.stale}, "
                f"failed {yahoo_coverage.failed}, pending {yahoo_coverage.missing + yahoo_coverage.corrupt})."
            )
            if not mt5_enabled:
                warnings.append(
                    "MT5 je pro velký universe vypnuté; technická data budou "
                    "neutrální. Tato konfigurace je analyticky degradovaná, "
                    "nikoli technicky rozbitá."
                )

        market_caps, marketcap_warning = load_market_caps(self.config.marketcap_file)
        if marketcap_warning:
            warnings.append(marketcap_warning)

        expanded_rss_sources: list[str] = []
        articles = []
        if rss_enabled:
            expanded_rss_sources = self._expand_rss_sources(rss_sources, watchlist)

            def _on_rss_progress(completed: int, total_sources: int, source: str) -> None:
                if completed == 1 or completed == total_sources or completed % 10 == 0:
                    phase_progress = 0.01 + 0.04 * (completed / max(1, total_sources))
                    progress.set_global_step(
                        "rss",
                        f"Načítám RSS zdroje: {completed}/{total_sources}",
                        phase_progress,
                    )

            progress.set_global_step(
                "rss",
                f"Načítám RSS zdroje: 0/{len(expanded_rss_sources)}",
                0.01,
            )
            articles, rss_warnings = self.rss_client.collect(
                expanded_rss_sources,
                watchlist,
                progress_callback=_on_rss_progress,
            )
            warnings.extend(rss_warnings)
        else:
            progress.log("INFO", "RSS zprávy jsou pro tento běh vypnuté")

        yahoo_only_tickers = yahoo_only_tickers or set()
        mt5_ohlc_by_ticker: dict[str, pd.DataFrame] = {}
        mt5_warnings_by_ticker: dict[str, str] = {}
        bulk_yahoo_ohlc_by_ticker: dict[str, pd.DataFrame] = {}
        bulk_yahoo_ohlc_warnings: dict[str, str] = {}
        mt5_tickers = [ticker for ticker in watchlist if ticker not in yahoo_only_tickers]
        if mt5_enabled and mt5_tickers:
            progress.set_global_step(
                "mt5_batch",
                f"Načítám MT5 OHLC: 0/{len(mt5_tickers)}",
                0.05,
            )

            def _on_mt5_progress(completed: int, mt5_total: int, ticker: str) -> None:
                if completed == 1 or completed == mt5_total or completed % 10 == 0:
                    phase_progress = 0.05 + 0.03 * (completed / max(1, mt5_total))
                    progress.set_global_step(
                        "mt5_batch",
                        f"Načítám MT5 OHLC: {completed}/{mt5_total} ({ticker})",
                        phase_progress,
                    )

            mt5_ohlc_by_ticker, mt5_warnings_by_ticker = self.mt5_client.fetch_ohlcv_batch(
                mt5_tickers,
                progress_callback=_on_mt5_progress,
            )
            mt5_success_count = len(mt5_ohlc_by_ticker)
            mt5_failure_count = len(mt5_tickers) - mt5_success_count
            if mt5_failure_count == len(mt5_tickers):
                warnings.append(
                    f"MT5 OHLC nebylo dostupné pro žádný z {len(mt5_tickers)} tickerů; "
                    "pro chybějící symboly se použije Yahoo bulk fallback."
                )
            elif mt5_failure_count:
                warnings.append(
                    f"MT5 OHLC není dostupné pro {mt5_failure_count} z {len(mt5_tickers)} tickerů; "
                    "pro chybějící symboly se použije Yahoo bulk fallback."
                )

        bulk_yahoo_requested_tickers = [
            ticker
            for ticker in watchlist
            if ticker in yahoo_only_tickers
            or ticker not in mt5_ohlc_by_ticker
        ]
        bulk_yahoo_ohlc_cache_state: dict[str, str] = {}
        bulk_yahoo_ohlc_retry_deferred: dict[str, str] = {}
        if large_universe_mode:
            for ticker in bulk_yahoo_requested_tickers:
                cache_lookup = self.yahoo_ohlc_cache.get(ticker)
                if cache_lookup.state == "fresh" and cache_lookup.frame is not None:
                    bulk_yahoo_ohlc_by_ticker[ticker] = cache_lookup.frame
                    bulk_yahoo_ohlc_cache_state[ticker] = "fresh"
                elif not cache_lookup.can_retry(started_at):
                    if cache_lookup.usable and cache_lookup.frame is not None:
                        bulk_yahoo_ohlc_by_ticker[ticker] = cache_lookup.frame
                        bulk_yahoo_ohlc_cache_state[ticker] = "stale_backoff"
                    bulk_yahoo_ohlc_retry_deferred[ticker] = (
                        cache_lookup.error
                        or "Předchozí Yahoo OHLC pokus je v ochranné čekací lhůtě."
                    )
        bulk_yahoo_tickers = [
            ticker
            for ticker in bulk_yahoo_requested_tickers
            if ticker not in bulk_yahoo_ohlc_by_ticker
            and ticker not in bulk_yahoo_ohlc_retry_deferred
        ]
        bulk_yahoo_ohlc_attempted_count = (
            len(bulk_yahoo_tickers) if large_universe_mode else 0
        )
        if large_universe_mode and bulk_yahoo_tickers:
            progress.set_global_step(
                "yahoo_ohlc_batch",
                f"Načítám Yahoo OHLC dávky: 0/{len(bulk_yahoo_tickers)} tickerů",
                0.08,
            )

            def _on_yahoo_ohlc_progress(
                completed: int,
                batch_total: int,
                ticker: str,
            ) -> None:
                phase_progress = 0.08 + 0.04 * (
                    completed / max(1, batch_total)
                )
                progress.set_global_step(
                    "yahoo_ohlc_batch",
                    f"Načítám Yahoo OHLC dávku {completed}/{batch_total} ({ticker})",
                    phase_progress,
                )

            fetched_ohlc, bulk_yahoo_ohlc_warnings = self.yahoo_client.fetch_ohlc_batch(
                bulk_yahoo_tickers,
                progress_callback=_on_yahoo_ohlc_progress,
            )
            for ticker, frame in fetched_ohlc.items():
                try:
                    self.yahoo_ohlc_cache.upsert_success(ticker, frame)
                except (TypeError, ValueError) as exc:
                    bulk_yahoo_ohlc_warnings[ticker] = (
                        f"Yahoo OHLC cache odmítla {ticker}: {exc}"
                    )
                else:
                    bulk_yahoo_ohlc_by_ticker[ticker] = frame
                    bulk_yahoo_ohlc_cache_statmݯϭǲڮ݆͹ۙ܋ܛݜؙ\ʋȈٙYYȎț[ʘۛٚY˙]\ۜX[יڛ[ٜ˙ٙYʋȈKȈܛݜؙWܙ\ۛ][ۈΈܝ]\ȎȜٜݛٙ]
ܛݜؙWܙ\ۛ][ۗܝ]\ȊKȈؘ[ۛژ؛ٝٛݜȎȚ[݊ٜݛٙ]
ܛݜؙWܙ\ۛ][ۗ؛ݛ݈ʈ܈
KȈ؛ۙۚXݜלٜۛٙΈ[݊Ȉٜݛٙ]
ܛݜؙWܙ\ۛ][ۗ؛ۙۚXݗ؛ݛ݈ʈ܈Ȉ
KȈKȈܚܝܙ\ܝȎȞ؛ۙڙݜٙΈۛۊȈۛٚY˜ڛܝܙ\ܝ˙[ؘۙYȈ܈ۛٚY˜ڛܝܙ\ܝ˘]]י\؛ݙ\יܛۗۙ]܂Ȉ
KȈܝ]\ȎȜٜݛٙ]
ܚܝܙ\ܝܝ]\ȊKȈٛ؝[Y[ݜȎȚ[݊ٜݛٙ]
ܚܝܙ\ܝٛ؝[Y[ݗ؛ݛ݈ʈ܈
KȈ؛Z[\ȎȚ[݊ٜݛٙ]
ܚܝܙ\ܝ؛Z[W؛ݛ݈ʈ܈
KȈ؝]י\؛ݙ\ٙΈ[݊Ȉٜݛٙ]
؝]י\؛ݙ\ٙܚܝܙ\ܝȊH܈Ȉ
KȈKȈܝ\WؚZ[ȎȞ؛ۙڙݜٙΈۛٚY˜ݜWؚZ[˙[ؘۙYȈܝ]\ȎȜٜݛٙ]
ܝ\WؚZ[לݘ]\ȊKȈܙ[][ۜښ\ȎȚ[݊Ȉٜݛٙ]
ܝ\WؚZ[לٛ][ۜښ\؛ݛ݈ʈ܈Ȉ
KȈ؝]י\؛ݙ\ٙΈ[݊Ȉٜݛٙ]
؝]י\؛ݙ\ٙܝ\WؚZ[לٛ][ۜښ\ȊH܈Ȉ
KȈKȈ؛ۛ[ٚ]WٜٛٞHΈ؛ۙڙݜٙΈۛٚY˘ۛ[[ٚ]WٜٛٞKؘٛۙYȈܝ]\ȎȜٜݛٙ]
؛ۛ[ٚ]WٜٛٞWܝ]\ȊKȈٞܝ\ٜȎȚ[݊Ȉٜݛٙ]
؛ۛ[ٚ]WٜٛٞWٞܝ\ٗ؛ݛ݈ʈ܈Ȉ
KȈ؝]י\؛ݙ\ٙΈ[݊Ȉٜݛٙ]
؝]י\؛ݙ\ٙ؛ۛ[ٚ]WٜٛٞWٞܝ\ٜȊH܈Ȉ
KȈKȈܙYݛ]ܞW؛۝ؘݜȎȞ؛ۙڙݜٙΈۛۊȈۛٚY˜ٙݛ]ܞW؛۝ؘ݋ؘٛۙYȈ܈ۛٚY˜ٙݛ]ܞW؛۝ؘ݋؝]י\؛ݙ\יܛۗۙ]܂Ȉ
KȈܝ]\ȎȜٜݛٙ]
ܙYݛ]ܞW؛۝ؘݗܝ]\ȊKȈٝٛݜȎȚ[݊Ȉٜݛٙ]
ܙYݛ]ܞW؛۝ؘݗٝٛݗ؛ݛ݈ʈ܈Ȉ
KȈ؝]י\؛ݙ\ٙΈ[݊Ȉٜݛٙ]
؝]י\؛ݙ\ٙܙYݛ]ܞWٝٛݜȊH܈Ȉ
KȈKȈBהґӐSёURSГӕSSԈH
ȈݚXڙ\ȋȈ؝\ܙ[ݗܜژو˂Ȉ؝\ܙ[ݗܜژٗܛݜؙH˂Ȉ؝\ܙ[ݗܜژٗܝ]\ȋȈ؝\ܙ[ݗܜژٗܙX\ۛȋȈ؝\ܙ[ݗܜژٗٙ]ڙY؝˂ȈۚטۛܙW؝˂Ȉۚכ؜ٜݘ][ۗ؛ݛ݈˂ȈۚלژٗݜؘۙH˂Ȉۚך\ݛܞWݜؘۙH˂Ȉۚט]ؚ[XۙWؘۛۚXڜȋȈۚכZ\ܚ[ؘٗۛۚXڜȋȈݙXڗܛݜؙWݜٙ˂ȈݙXڛژ؛ܝ]\ȋȈݙXڛژ؛ܙX\ۛȋȈܘ[ښ[ٗٛYژۙH˂Ȉܘ[ښ[ٗܝ]\ȋȈܘ[ښ[ٗܙX\ۛȋȈܘ[ڗڛם؝ڛ\݈˂Ȉܙ\ؙ[ݚ[Wڛם؝ڛ\݈˂Ȉܘ]םݘ[ܘۜو˂Ȉٚ[؛ݛݘ[ܘۜو˂Ȉٚ[؛؛ۙڙ[ؙH˂Ȉ؛ۙڙ[ؙWښ[و˂Ȉ٘]WܝX[]Wܘۜو˂Ȉۙ]ܗ؛ۙڙ[ؙH˂ȈݙXڗ؛ۙڙ[ؙH˂ȈޘZۗ؛ۙڙ[ؙH˂ȈٙXڜڛۗܚYۘ[˂ȈٛܙX؜݈˂Ȉؘݚ[ۈ˂Ȉؘݚ[ۗܙX\ۛ܈˂ȈܚYۘ[ܝٛٝ˂Ȉ؛ؚٙܙX\ۛ܈˂ȈܙX\ۛ܈˂Ȉݘ\ۚ[ٜȋʂٙYȗڜۛלؙي؛YNțؚ٘݊HOțؚ٘ݎYȝ؛YH\ȓَۛٝ\ۈۛقȈ[ݛWݘ[YHHٝ]ʝ؛YKݘ[YHˈۛيBȈYș[ݛWݘ[YH\ț۝ۛو[و\ڛܝ[ؙJ[ݛWݘ[YK
ݜˈ[݋ۛ؝ۛۊJNٝ\ۈ[ݛWݘ[YBȈ][HHٝ]ʝ؛YKڝ[HˈۛيBȈYȘ؛XۙJ][JNގٝ\ۈڜۛלؙي][J
JBȈ^ٜ
\Q\ܛ܋؛YQ\ܛ܊N\܂ȈYȚ\ڛܝ[ؙJ؛YK]][YJNٝ\ۈ؛YKڜۙۜۘ]

BȈYȚ\ڛܝ[ؙJ؛YKۛ؝
H[و۝X]ڜٚ[ڝJ؛YJNٝ\ۈۛقȈYȚ\ڛܝ[ؙJ؛YKX݊Nٝ\ۈܝʚٞJNȗڜۛלؙي][JHۜȚٞK][H[ȝ؛YKڝ[\ʊ_BȈYȚ\ڛܝ[ؙJ؛YK
\݋\JJNٝ\ۈךܛۗܘYي][JHۜȚ][H[ȝ؛YWBȈYȚ\ڛܝ[ؙJ؛YK
ݜˈ[݋ۛ؝ۛۊJNٝ\ۈ؛YBȈٝ\ۈݜʝ؛YJBٙYȗݛڝٜܙW؛ݙ\ؙيȈٜ]Y\ݙYݚXڙ\܎ț\ݖܝ׋ȈXڙ\לٜݛΈ\ݖٚXݖܝˈؚ٘ݗWKʈOșXݖܝˈؚ٘ݗNٜ]Y\ݙYH\݊X݋ٜۛZٞ\ʜݜʝXڙ\ʋܝڜ

Kݜ\ʊHۜȝXڙ\Ț[Ȝٜ]Y\ݙYݚXڙ\܈YȜݜʝXڙ\ʋܝڜ

JJBȈٜܝYHݜʜ۝˙ٝ
ݚXڙ\ȊH܈ȊKܝڜ

Kݜ\ʊBȈۜȜ۝Ț[ȝXڙ\לٜݛYȜݜʜ۝˙ٝ
ݚXڙ\ȊH܈ȊKܝڜ

BȈBȈZ\ܚ[وHݚXڙ\șۜȝXڙ\Ț[Ȝٜ]Y\ݙYYȝXڙ\ț۝[ȜٜܝYBȈ[YژۙHH۝ۜȜ۝Ț[ȝXڙ\לٜݛYȘۛۊ۝˙ٝ
ܘ[ښ[ٗٛYژۙHʊBȈBȈٝ\ۈܙ\]Y\ݙYΈ[ʜٜ]Y\ݙY
KȈܙ\ܝYΈ[ʜٜܝY
KȈۚ\ܚ[وΈ[ʛZ\ܚ[يKȈۚ\ܚ[ٗݚXڙ\܈ΈZ\ܚ[ًȈ؛ݙ\ؙٗܘ݈Έ۝[يL̈
ț[ʜٜܝY
Hț[ʜٜ]Y\ݙY
KʈYȜٜ]Y\ݙY[و̋Ȉܘ[ښ[ٗٛYژۙHΈ[ʙ[YژۙJKȈܘ[ښ[ٗڛٛYژۙHΈ[ʜٜܝY
HH[ʙ[YژۙJKȈܘ[ښ[ٗݜؘۙW؛ݙ\ؙٗܘ݈Έ
Ȉ۝[يL̈
ț[ʙ[YژۙJHț[ʜٜ]Y\ݙY
KʈYȜٜ]Y\ݙY[و̂Ȉ
KȈBٙYȗܚYۘ[ٙ]Z[ܙXٜۜʜٜݛșXݖܝˈؚ٘ݗJHOț\ݖٚXݖܝˈؚ٘ݗWNڙۘ[ȏHٜݛٙ]
ܚYۘ[ȊBȈYȜڙۘ[Ț\ȓۛو܈۝\؝ʜڙۘ[ˈݛיX݈ʎٝ\ۈׂȈۛ[[܈Hٝ
ٝ]ʜڙۘ[ˈ؛۝[[܈ˈ׊JBȈٛXݙYH؛۝[[șۜȘۛ[[Ț[ȗԒQӐSёURSГӕSSԈYȘۛ[[Ț[Șۛ[[ܗBȈٝ\ۈۛ[[Έڜۛלؙيً٘ۜٙ]
ۛ[[ʊBȈۜȘۛ[[Ț[ȜٛXݙYȈBȈۜȜ٘ۜو[Ȝڙۘ[˝יX݊ܚY[ݏHܙXٜۜȊBȈBבPҔғӗёURSђQSȏH
ȈٙXڜڛۗڙ˂ȈݚXڙ\ȋȈܛۚXޗۘ[YH˂ȈܛۚXޗݙ\ܚ[ۈ˂Ȉؘ\ٛ[ؘٗݚ[ۈ˂Ȉؘ\ٛ[ٗٛܙX؜݈˂ȈܜۜܙYؘݚ[ۈ˂ȈܜۜܙYٛܙX؜݈˂Ȉؘ\ٛ[ٗܗݜ˂Ȉؘ\ٛ[ٗܗٛ]˂Ȉؘ\ٛ[ٗܗٛݛȋȈܗݜ˂Ȉܗٛ]˂ȈܗٛݛȋȈ؛ۙڙ[ؙH˂Ȉژ\ٗݙ]ȋȈؘݚ]؝[ۗܝ]H˂Ȉ؜YYݛלٙXݚ[ۈ˂ȈܙX\ۛ܈˂Ȉ؛ۙۚXݜȋȈٝڙ[ؙWڙȋȈ؛Z[WڙȋȈܙYݛ]ܞWٝٛݗڙȋȈۙ]Y]H˂ʂٙYȗٙXڜڛۗٙ]Z[ܙXٜۜʜٜݛșXݖܝˈؚ٘ݗJHOț\ݖٚXݖܝˈؚ٘ݗWNٜܝHٜݛٙ]
ؙٛݗܙ\ܝʂȈXڜڛۜȏHٝ]ʜٜܝٙXڜڛۜȋ׊HYȜٜܝ\ț۝ۛو[وׂȈٝ\ۈڙ[ȗڜۛלؙيٝ]ʙXڜڛۋڙ[ۛيJBȈۜșڙ[[ȗёPҔғӗёURSђQSBȈۜșXڜڛۈ[șXڜڛۜBٙYȗܝX[]W٘]WڜܝY\ʜٜݛșXݖܝˈؚ٘ݗJHOț\ݖٚXݖܝˈؚ٘ݗWNٜܝHٜݛٙ]
ؙٛݗܙ\ܝʂȈ^Xݝ[ۜȏHٝ]ʜٜܝٞXݝ[ۜȋ׊BȈ\ܝY\Έ\ݖٚXݖܝˈؚ٘ݗWHHׂȈۜș^Xݝ[ۈ[ș^Xݝ[ۜ΂ȈYșٝ]ʙ^Xݝ[ۋؙٛݗۘ[YHˈȊHOHܝX[]W٘]H΂Ȉۛݚ[ݙBȈڙXڜȏHٝ]ʙ^Xݝ[ۋܙ\ݛܝX[]WؚXڜȋ׊BȈۜȘڙXڈ[ȘڙXڜ΂ȈY]Y]HHٝ]ʘڙXڋۙ]Y]HˈߊH܈߂Ȉٚ٘ݜȏHY]Y]Kٙ]
ܙZ٘ݜȋ׊BȈ؜ۚ[ٜȏHY]Y]Kٙ]
ݘ\ۚ[ٜȋ׊BȈXڜڛۈHڜۛלؙيٝ]ʘڙXڋٙXڜڛۈˈۛيJBȈYșXڜڛۈOHԐTԈȘ[و۝ٚ٘ݜȘ[و۝؜ۚ[ٜ΂Ȉۛݚ[ݙBȈ\ܝY\˘\[يȈݚXڙ\Ȏȗڜۛלؙيٝ]ʘڙXڋݚXڙ\ȋۛيJKȈ٘]Wۘ[YHΈڜۛלؙيٝ]ʘڙXڋ٘]Wۘ[YHˈۛيJKȈٙXڜڛۈΈXڜڛۋȈۙ\ܘYوΈڜۛלؙيٝ]ʘڙXڋۙ\ܘYوˈȊJKȈܙZ٘ݜȎȗڜۛלؙيٚ٘ݜʋȈݘ\ۚ[ٜȎȗڜۛלؙي؜ۚ[ٜʋȈBȈ
BȈٝ\ۈ\ܝY\YȜݛםٙZ۞WܚY݊Ȉ
˂ȈۛٚYΈ\ۛٚY˂ȈXڙ\܎ț\ݖܝ׋Ȉܜל۝\ؙ\Έ\ݖܝ׋Ȉܜי[ؘۙYȘۛۋȈ]WؘٛۙYȘۛۋȈXZۗۙ]Y]WؘٛۙYȘۛۈًۛȈٜۛٗۘXٛΈۛۈH؛ًʈOșXݖܝˈؚ٘ݗNܛۈX\ڙ]ؚXڙ\ט\ܙ\ݚXٜ˜\[[ٗܙ\ݚXو[\ܝ\[[ٜٔݚXقYț۝ۛٚY˘YٛݗܚYݗۛٙNؚ\وݛݚ[YPۛٚYݜ؝[ۑ\ܛ܊հ[۰눜ݛۙ\Ȝ۰눘Ѧ񯙝ݞوȜژY݈ٱoڛ]KȊBȈYț۝Xڙ\܎ؚ\وݛݚ[YPۛٚYݜ؝[ۑ\ܛ܊ȈКXЫHXڙ\ގȞؙZݙHK]Xڙ\܈٘ۈٚܜݙHޝ۱f]H\ݛܚZHȔԓ]KȂȈ
BۛٚY˙[ܝ\ٗ۝]]ٚ\ʊBȈݛܙHHԓ]TݛܙJۛٚY˜ܛ]Wܘ]
BȈݛܙKٛܝ\ٗܘڙ[XJ
BȈڛ݊Ȗғѓ׈ر#p뛰蛈ܜؘ۝Ш[ЫHۙ[ʝXڙ\܊_HXڙ\ѫΈѫر&ڈوݙHۘܘ^۝؝Ыqoًȋ۝\ڏUݙJBȈ\[[وH\[[ٜٔݚXيۛٚYʂȈٜݛH\[[ًܝ[ʂȈXڙ\܋Ȉܜל۝\ؙ\˂ȈݛܙKȈۙܙ\ܗؘ[ؘڏW؛ۜۛWܜۙܙ\܋Ȉܜי[ؘۙY\ܜי[ؘۙYȈ]WؘٛۙY[]WؘٛۙYȈXZۗۙ]Y]WؘٛۙY^XZۗۙ]Y]WؘٛۙYȈ
BȈڛ݊ȋ۝\ڏUݙJBȈ٘Y[ٜ܈HܙXY[ٜܗܝ[[X\ފٜݛۛٚYʂȈۘ\ڛݗܙXٜۜΈ\ݖٚXݖܝˈؚ٘ݗWHHׂȈۘ\ڛݗٜܛ܎ȜݜȟۛوHۛقȈ؝ٙܛ؜ڛݗ؛ݛ݈HȈݛךYHٜݛٙ]
ܝ[ךYʂȈ؝לڛݗڛם[YWڛܝ]ȏHٜݛٙ]
ܛڛݗڛם[YWڛܝ]ȊBȈYȜݛךY\ț۝ۛو[و\ڛܝ[ؙJ؝לڛݗڛם[YWڛܝ]ˈ\݊NY]Y]HHٜݛٙ]
ۙ]Y]HʂȈ؜ٜݙY؝Hٝ]ʛY]Y]Kٚ[ڜڙY؝ˈ]][YKۛ݊[Y^ًۛݝʊBȈۜȚ][H[Ȝ؝לڛݗڛם[YWڛܝ]΂ȈYț۝\ڛܝ[ؙJ][KX݊Nۛݚ[ݙBȈގۘ\ڛݗܙXٜۜ˘\[يȈݚ[ܛڛݗڛם[YWܛ؜ڛ݊ȈݛךYZ[݊ݛךY
KȈXڙ\Ϝݜʚ][Kٙ]
ݚXڙ\ȊH܈ȊKȈ؜ٜݙY؝[؜ٜݙY؝Ȉ٘]\ٗܘ^[ؙZ][Kٙ]
ٙX]\ٗܘ^[ؙʈ܈ߋȈ؜ٛ[ٗ۝]]Z][Kٙ]
ؘ\ٛ[ٗ۝]]ʈ܈ߋȈ۝ٛ؛ؙOZ][Kٙ]
ܜ۝ٛ؛ؙHʈ܈ߋȈؚٛX\ڗݚXڙ\ϜݜʂȈ][Kٙ]
ؙ[ؚX\ڗݚXڙ\ȊH܈ԔH
KȈؚٛX\ڗܙ[Xݚ[ۏ\ݜʂȈ][Kٙ]
ؙ[ؚX\ڗܙ[Xݚ[ۈʈ܈ݛڛ۝ۈ
KȈ
BȈ
BȈ^ٜ
\Q\ܛ܋؛YQ\ܛ܊H\ș^΂Ȉۘ\ڛݗٜܛ܈HȜۘ\ڛ݈ۛܝݚؙHٛ[NȞٞ߈ܙXZYȜۘ\ڛݗٜܛ܈\ȓۛو[و[ʜۘ\ڛݗܙXٜۜʈOH[ʝXڙ\܊Nۘ\ڛݗٜܛ܈H
Ȉܛ؜ڛ݈ۛݜؚ݈ޝ۱fZ[Ȟۙ[ʜۘ\ڛݗܙXٜۜʟKޛ[ʝXڙ\܊_HШ^ۘ[qkȂȈ
BȈYȜۘ\ڛݗٜܛ܈\ȓَۛގ؝ٙܛ؜ڛݗ؛ݛ݈HݛܙKܘ]ٗܜٙXݚ[ۗܛ؜ڛݜʂȈۘ\ڛݗܙXٜۜ
BȈ^ٜ^ٜ[ۈ\ș^΂Ȉۘ\ڛݗٜܛ܈HȜۘ\ڛ݈[񯙛ЫHٛ[Έݞ\J^ʋחۘ[YWןNȞٞ߈[YȜݛךY\ț۝َۛۘ\ڛݗٜܛ܈Hܚ\[[وٝܰ蝚[ڛ݋Z[˝[YHܝ\HXٛܙ\ۛ][ێșXݖܝˈؚ٘ݗHHܝ]\ȎȈђTАӑQ˂Ȉܙ[ٚ[ؙٗYۜوΈȈܙ\ۛٙΈȈݛ؝ؚ[XۙHΈȈٙYٜܙYΈȈܛݜؙW٘Z[\ٜȎȌȈBȈXٛܙ\ۛ][ٜۗܛ܎ȜݜȟۛوHۛقȈYȜٜۛٗۘXٛȘ[وۘ\ڛݗٜܛ܈\ȓۛو[وݛךY\ț۝َۛގXٛܙ\ۛ][ۈHܝ]\ȎȈԕPБTԈ˂Ȉ
ʔٙXݚ[ۓXٛٜݚXي
Kܙ\ۛٗܙ[ٚ[ٗܛ؜ڛݜʂȈݛܙO\ݛܙKȈژؙٗۛ\ϛ[X٘HޛXۛȜ\[[ًޘZۗ؛Y[݋ٙ]ڗۚכۛJȈޛXۛȈ\ڛُH̞H˂Ȉ[ݙ\ݘ[H̙˂Ȉ
KȈ\כُY]][YKۛ݊[Y^ًۛݝʋȈ
KȈBȈYȊȈ[݊Xٛܙ\ۛ][ۋٙ]
ܛݜؙW٘Z[\ٜȊH܈
HȌȈ܈[݊Xٛܙ\ۛ][ۋٙ]
ٙYٜܙYʈ܈
HȌȈ
NXٛܙ\ۛ][ۖȜݘ]\ȗHHԐTՒPS^ٜ^ٜ[ۈ\ș^΂ȈXٛܙ\ۛ][ٜۗܛ܈H
ȈțXٛٜۛٜȜٛ[Ȟݞ\J^ʋחۘ[YWןNȞٞ߈
BȈXٛܙ\ۛ][ۈHܝ]\ȎȈѐRSQ˂Ȉܙ[ٚ[ؙٗYۜوΈȈܙ\ۛٙΈȈݛ؝ؚ[XۙHΈȈٙYٜܙYΈȈܛݜؙW٘Z[\ٜȎȌȈBȈݛ[X\ޗݘ\ۚ[ٜȏH\݊ٜݛٙ]
ݘ\ۚ[ٜȋ׊JBȈYțXٛܙ\ۛ][ٜۗܛ܎ݛ[X\ޗݘ\ۚ[ٜ˘\[يXٛܙ\ۛ][ٜۗܛ܊BȈ[YȚ[݊Xٛܙ\ۛ][ۋٙ]
ܛݜؙW٘Z[\ٜȊH܈
HȌݛ[X\ޗݘ\ۚ[ٜ˘\[يȈӘXٛٜۛٜț؜؞ڛ؈ٙܝ\ЯHٜۚΈݱ#Y[ЪHۘ\ڛݞHѫܝ[HSђSыȂȈ
BȈXڙ\לٜݛȏHܚYۘ[ٙ]Z[ܙXٜۜʜٜݛ
BȈ[ڝٜܙW؛ݙ\ؙوHݛڝٜܙW؛ݙ\ؙيXڙ\܋Xڙ\לٜݛʂȈݛ[X\ގșXݖܝˈؚ٘ݗHHܘڙ[XWݙ\ܚ[ۈΈ˂Ȉٚ[ڜڙY؝Έ]][YKۛ݊[Y^ًۛݝʋڜۙۜۘ]

KȈܝ[ךYΈٜݛٙ]
ܝ[ךYʋȈݚXڙ\ט۝[݈Έ[ʝXڙ\܊KȈܙ\]Y\ݙYݚXڙ\܈Έ\݊Xڙ\܊KȈݛڝٜܙW؛ݙ\ؙوΈ[ڝٜܙW؛ݙ\ًؙȈ؛؛\ڜכۛHΈݙKȈ؝]ۘ]Yݜؙ[وΈؘٛۙYΈ؛ًȈܙ\ۘ[ٛݛWٚ\ؘۙYΈݙKȈٞXݝ[ۗܘ]Έܙ[[ݙY˂ȈKȈܛڛݗڛם[YWܛ؜ڛݗ؛ݛ݈Έ؝ٙܛ؜ڛݗ؛ݛ݋Ȉܛڛݗڛם[YWܛ؜ڛݗܝ]\ȎȊȈԕPБTԈȚYȜۘ\ڛݗٜܛ܈\ȓۛو[وѐRSQ
KȈܜٙXݚ[ۗۘXٛܙ\ۛ][ۈΈXٛܙ\ۛ][ۋȈܜٙXݚ[ۗۘXٛܙ\ۛ][ٜۗܛ܈ΈXٛܙ\ۛ][ٜۗܛ܋Ȉؙٛݗܝ]\ȎȜٜݛٙ]
ؙٛݗܝ]\ȊKȈܝX[]W٘]WٙXڜڛۈΈٜݛٙ]
ܝX[]W٘]WٙXڜڛۈʋȈٙXڜڛۗ؛ݛ݈Έٜݛٙ]
ٙXڜڛۗ؛ݛ݈ʋȈٙXڜڛۗܝ\ٜܙY؛ݛ݈Έٜݛٙ]
ٙXڜڛۗܝ\ٜܙY؛ݛ݈ʋȈٙXڜڛۗ؜YY؛ݛ݈Έٜݛٙ]
ٙXڜڛۗ؜YY؛ݛ݈ʋȈؘݚ]؝[ۗܝ]HΈٜݛٙ]
ؘݚ]؝[ۗܝ]HʋȈٝ؛X][ۗܘ[\W؛ݛ݈Έٜݛٙ]
ٝ؛X][ۗܘ[\W؛ݛ݈ʋȈٝ؛X][ۗٚ\ݚ[؝ݙYZ܈Έٜݛٙ]
ٝ؛X][ۗٚ\ݚ[؝ݙYZ܈ʋȈٝ؛X][ؘۗ\ٛ[ؘٗ؝\ؘޗܘ݈Έٜݛٙ]
Ȉٝ؛X][ؘۗ\ٛ[ؘٗ؝\ؘޗܘ݈
KȈٝ؛X][ؘۗ[ٚY]Wؘ؝\ؘޗܘ݈Έٜݛٙ]
Ȉٝ؛X][ؘۗ[ٚY]Wؘ؝\ؘޗܘ݈
KȈٝ؛X][ۗۚYݗܘݗܛڛݜȎȜٜݛٙ]
ٝ؛X][ۗۚYݗܘݗܛڛݜȊKȈٝ؛X][ۗۚYݗۛݙ\ט۝[ٗܘݗܛڛݜȎȜٜݛٙ]
Ȉٝ؛X][ۗۚYݗۛݙ\ט۝[ٗܘݗܛڛݜȂȈ
KȈٝ؛X][ۗܛܚ]]ٗݙYZל؝[ȎȜٜݛٙ]
Ȉٝ؛X][ۗܛܚ]]ٗݙYZל؝[ȂȈ
KȈٝ؛X][ۗ٘]Wܘ\ܙYΈٜݛٙ]
ٝ؛X][ۗ٘]Wܘ\ܙYʋȈٝ؛X][ۗ٘]Wܙ\ݛȎȜٜݛٙ]
ٝ؛X][ۗ٘]Wܙ\ݛȊKȈٝ؛X][ۗ؛ۜ٘ݝ]ٗܘ\ܙ\ȎȜٜݛٙ]
Ȉٝ؛X][ۗ؛ۜ٘ݝ]ٗܘ\ܙ\ȂȈ
KȈٝ؛X][ۗܙ\]Z\ٙ؛ۜ٘ݝ]ٗܘ\ܙ\ȎȜٜݛٙ]
Ȉٝ؛X][ۗܙ\]Z\ٙ؛ۜ٘ݝ]ٗܘ\ܙ\ȂȈ
KȈؘ؝\ؘޗڛ\۝ٛY[ݗܜ۝ٛȎȜ٘Y[ٜܖؘ؝\ؘޗڛ\۝ٛY[ݗܜ۝ٛȂȈKȈ؛؛\ڜם؛Y][ۗܙXYHΈ٘Y[ٜܖȘ[؛\ڜם؛Y][ۗܙXYH׋ȈܙXY[ٜ܈Έ٘Y[ٜ܋ȈܛݜؙWڙX[ΈܛݜؙWڙX[ܝ[[X\ފٜݛۛٚYʋȈܝX[]W٘]WڜܝY\ȎȗܝX[]W٘]WڜܝY\ʜٜݛ
KȈ؝]י\؛ݙ\ٙܚܝܙ\ܝȎȜٜݛٙ]
Ȉ؝]י\؛ݙ\ٙܚܝܙ\ܝȂȈ
KȈ؝]י\؛ݙ\ٙܙYݛ]ܞWٝٛݜȎȜٜݛٙ]
Ȉ؝]י\؛ݙ\ٙܙYݛ]ܞWٝٛݜȂȈ
KȈݘ\ۚ[ٗ؛ݛ݈Έ[ʜݛ[X\ޗݘ\ۚ[ٜʋȈٜܛܗ؛ݛ݈Έ[ʜٜݛٙ]
ٜܛܜȋ׊JKȈݘ\ۚ[ٜȎȜݛ[X\ޗݘ\ۚ[ٜ˂ȈٜܛܜȎț\݊ٜݛٙ]
ٜܛܜȋ׊JKȈٙ]Z[ܘڙ[XWݙ\ܚ[ۈΈKȈݚXڙ\לٜݛȎȝXڙ\לٜݛ˂ȈٙXڜڛۗܙ\ݛȎȗٙXڜڛۗٙ]Z[ܙXٜۜʜٜݛ
KȈBȈؚ[\ٜΈ\ݖܝ׈HׂȈYܘY][ۜΈ\ݖܝ׈HׂȈYȜٜݛٙ]
ؙٛݗܝ]\ȊHOHԐTՒPS΂ȈYܘY][ۜ˘\[يȈؙٛݛЫH\[[وڛ۱#Z[Hݘ]ٛHTՒPSțѦڝ\ЪHۛ][ЪHٜۚوܛ݈ٰЪH
BȈYȜٜݛٙ]
ܝ[ךYʈ\ȓَۛؚ[\ٜ˘\[ير&ڈوٝ[񯚛Ȕԓ]HʂȈYȜۘ\ڛݗٜܛ܈\ț۝َۛؚ[\ٜ˘\[يۘ\ڛݗٜܛ܊BȈ[YȜ؝ٙܛ؜ڛݗ؛ݛ݈OH[ʝXڙ\܊Nؚ[\ٜ˘\[يȈȜڛ݋Z[˝[YHۘ\ڛݞNȞܘ]ٙܛ؜ڛݗ؛ݛݟKޛ[ʝXڙ\܊_H
BȈٜܝHٜݛٙ]
ؙٛݗܙ\ܝʂȈ^Xݝ[ۜȏHٝ]ʜٜܝٞXݝ[ۜȋ׊BȈ\ؙٗٛݗ٘Z[\ٜȏHȞٞXݝ[ۋؙٛݗۘ[Y_Nޙ^Xݝ[ۋܝ]\˝؛Y_Hۜș^Xݝ[ۈ[ș^Xݝ[ۜYș^Xݝ[ۋܝ]\˝؛YH[ȞȑВSQˈГВё߂ȈBȈYȜٜݛٙ]
ؙٛݗܝ]\ȊHOHѐRSQț܈\ؙٗٛݗ٘Z[\ٜ΂Ȉؚ[\ٜ˘\[يȈؙٛݛЫHѦڈ؜ؚZوܙ0ꈜٛ0蛰눂Ȉ
ȊȈΈȊȈˈ˚ۚ[ʚ\ؙٗٛݗ٘Z[\ٜʂȈYȚ\ؙٗٛݗ٘Z[\ٜ[وȂȈ
BȈ
BȈYȘۛٚY˙ݛ٘[Y[ݘ[ڛٙ\ݚ[ۋؘٛۙYYȜٜݛٙ]
ٝ[٘[Y[ݘ[ڛٙ\ݚ[ۗܝ]\ȊH۝[ȞԕPБTԈ˂ȈԐTՒPS˂ȈNؚ[\ٜ˘\[يԑPȚ[ٙ\݈ٛЫH۝۞ݜؚܛЯHʂȈYȚ[݊ٜݛٙ]
ٝ[٘[Y[ݘ[ٛ؝[Y[ݗ؛ݛ݈ʈ܈
HOHؚ[\ٜ˘\[يԑPȚ[ٙ\݈ٝ[񯚛1oШYЯHڛ[وʂȈYȚ[݊ٜݛٙ]
ٝ[٘[Y[ݘ[٘Xݗ؛ݛ݈ʈ܈
HOHؚ[\ٜ˘\[يԑPȚ[ٙ\݈ٝ[񯚛1oШYЯHԓؚ݈ʂȈYȊȈۛٚY˙ݛ٘[Y[ݘ[ڛٙ\ݚ[ۋؘٛۙYȈ[و
ȈۛٚY˜ݜWؚZ[˘]]י\؛ݙ\יܛۗܙXיڛ[ٜ܈ۛٚY˘ۛ[[ٚ]WٜٛٞK؝]י\؛ݙ\יܛۗܙXיڛ[ٜ
BȈ
Nڛ[ٗݙ^ٛ؝[Y[ݜȏH[݊Ȉٜݛٙ]
ٝ[٘[Y[ݘ[ٚ[[ٗݙ^ٛ؝[Y[ݗ؛ݛ݈ʈ܈Ȉ
BȈڛ[ٗݙ^٘Z[\ٜȏH[݊Ȉٜݛٙ]
ٝ[٘[Y[ݘ[ٚ[[ٗݙ^٘Z[\ٗ؛ݛ݈ʈ܈Ȉ
BȈYșڛ[ٗݙ^ٛ؝[Y[ݜȏOHYܘY][ۜ˘\[يȈۙXޛٞܙq#[Ѧțر#][ȱoШYЯH^Я\۱#[ЫZșڛ[ٝNȈٛ٘]؝[ڰ舘HX]\ڰ蛛ݰ舙\؛ݙ\ވܜݝ؈ٛp舝^ݰܝ\
BȈYșڛ[ٗݙ^٘Z[\ٜȏȌYܘY][ۜ˘\[يȈțٜ٘qfZ[Ȝور#p뜝ٚ[[ٗݙ^٘Z[\ٜ߈^1kȝЯ\۱#[ЫXڈڛ[ٱkΈٛݱ#Y[ЪHXڙ\۝ЪH[ܚXڛY[ݞHѫܝ0蝘ZЫHٰЪH
BȈYȘۛٚY˜ڛܝܙ\ܝ˙[ؘۙY[و[݊Ȉٜݛٙ]
ܚܝܙ\ܝٛ؝[Y[ݗ؛ݛ݈ʈ܈Ȉ
HOHYܘY][ۜ˘\[يܚܝ\ٜܝ؛؜ވ٘ޛر#][ȊBȈYȘۛٚY˜ݜWؚZ[˙[ؘۙY[وٜݛٙ]
ܝ\WؚZ[לݘ]\ȊHOHԕPБTԈ΂ȈYܘY][ۜ˘\[يȈȔݜPژZ[Й݈ٛڛ۱#Z[ܙ\ݛٙ]
	ܝ\WؚZ[לݘ]\Ɋ_NȈް蚛YЫHٙZؙHѫܝ0蝰舘ٞو۱&۞H
BȈYȊȈۛٚY˘ۛ[[ٚ]WٜٛٞKؘٛۙYȈ[وٜݛٙ]
؛ۛ[ٚ]WٜٛٞWܝ]\ȊHOHԕPБTԈ
NYܘY][ۜ˘\[يȈȐۛ[[ٚ]Q[ٜٞPY݈ٛڛ۱#Z[ܙ\ݛٙ]
	؛ۛ[ٚ]WٜٛٞWܝ]\Ɋ_NȈް蚛YЫHٙZؙHѫܝ0蝰舘ٞو۱&۞H
BȈYȜٜݛٙ]
ܝX[]W٘]WٙXڜڛۈʈOHԐTԈ΂Ȉؚ[\ٜ˘\[يȈȔ]X[]Q؝Hڛ۱#Z[ܙ\ݛٙ]
	ܝX[]W٘]WٙXڜڛۉʟH
BȈYȚ[݊ٜݛٙ]
ٙXڜڛۗ؜YY؛ݛ݈ʈ܈
HOHؚ[\ٜ˘\[يȈ؛؛]Xڰ舝ܜݝ؈وڝ\ڛH1fY\؝]۰눜ٙZؚNȈ؝]ۘ]Xڰꈛؘڛٛݰ蛰눚وȜۚٚݝHٜݜ؛Ѧ۰ꈂȈ
BȈYȘۛٚY˙Xڜڛؙۗٛ݋ؘٛۙY[و[݊ٜݛٙ]
ٙXڜڛۗ؛ݛ݈ʈ܈
HOH[ʂȈXڙ\܂Ȉ
Nؚ[\ٜ˘\[يљXڜڛېY݈ٛٝޝ۱fZ[Ш]ѦȚٙۈ۞ڛٛݝ0눜ۈرoٰXڙ\ȊBȈYȜٜݛٙ]
ٜܛܜȊNؚ[\ٜ˘\[يܚ\[[وڛ0蜚[HۙZ񣛰눘ڞXވʂȈݛ[X\ޖȜ\[[ٗٙYܘY][ۜȗHH\݊X݋ٜۛZٞ\ʙYܘY][ۜʊBȈݛ[X\ޖȜ\[[ٗܝ]\ȗHH
ȈѐRSQYșؚ[\ٜ[و
ԐTՒPSȚYșYܘY][ۜș[وԕPБTԈʂȈ
BȈݛ[X\ޖș]؛X][ۗܝ]\ȗHH
ȈԐTԈȚYȜݛ[X\ދٙ]
ٝ؛X][ۗ٘]Wܘ\ܙYʈ[وԑSђSш
BȈݛ[X\ޖȜ\[[ٗ٘Z[\ٜȗHH\݊ؚ[\ٜʂȈݛ[X\ޖȜڛݗڛם[YWܛ؜ڛݗٜܛ܈׈Hۘ\ڛݗٜܛ܂Ȉ؝ۚXךܛۊۛٚY˛ݝ]ٚ\ȋȈݙYZ۞WܚYݗۘ]\݋ڜۛȋݛ[X\ފBYșؚ[\ٜ΂Ȉؚ\وݛݚ[YQ\ܛ܊Έ˚ۚ[ʙؚ[\ٜʊBȈٝ\ۈݛ[X\ނٙYȗܘ\ܙ\ʊHOȘ\ٜ\ܙKМٝ[Y[ݔ\ܙ\΂Ȉ\ܙ\ȏH\ٜ\ܙKМٝ[Y[ݔ\ܙ\ʂȈ\؜ڜ[ۏHЙ^ۘܛqo۰0[۰눔ݘYوژY݈ѦڈȘ]Y]Ы[ZHۛݜۛ[ZKȂȈ
BȈ\ܙ\˘Y؜ٝ[Y[݊ˋ]Xڙ\܈ˈ؜ٜψʈˈY؝[V׊BȈ\ܙ\˘Y؜ٝ[Y[݊Ȉˋ]Xڙ\˙ڛH˂Ȉ\OT]Ȉ[JȈՙ\ޛݘ[ЯHXڙ\ȝ[ڝٜܙNȜݱoښوًڝYٚܛ݈ؙ0蛞Hˋ]Xڙ\܋ȐٞțѦڈݛۙ\ț؛۝ЫHXڙ\ވوԓ]H\ݛܚYKȂȈ
KȈ
BȈ\ܙ\˘Y؜ٝ[Y[݊Ȉˋ]Xڙ\˛[Z]˂Ȉ\OZ[݋Ȉ[H՛ۚ][ЯH[ݛЫH[Z]ؘڛݰ蝘ZЫX𫈜񦘙0눝Xڙ\Ȝ۝Xۜ݋ȋȈ
BȈ\ܙ\˘Y؜ٝ[Y[݊Ȉˋ[ݝ]Y\ȋȈ\OT]ȈY؝[T]
۝]]ȊKȈ
BȈ\ܙ\˘Y؜ٝ[Y[݊ȈˋY˜]˂Ȉ\OT]ȈY؝[T]
۝]]˛X\ڙ]ؚXڙ\ך\ݛܞK٘ȊKȈ
BȈ\ܙ\˘Y؜ٝ[Y[݊ˋ\ݛݚ[YKXۛٚYȋ\OT]
BȈ\ܙ\˘Y؜ٝ[Y[݊ˋ\ܜ˜۝\ؙHˈXݚ[ۏH؜[وˈ\ݏHܜܗܛݜؙ\ȊBȈ\ܙ\˘Y؜ٝ[Y[݊Ȉˋ\ܜȋȈXݚ[ۏX\ٜ\ܙKЛۛX[Ӝ[ۘ[Xݚ[ۋȈY؝[UݙKȈ
BȈ\ܙ\˘Y؜ٝ[Y[݊Ȉˋ[]H˂ȈXݚ[ۏX\ٜ\ܙKЛۛX[Ӝ[ۘ[Xݚ[ۋȈY؝[\]ۜۋܞ\ݙ[J
HOH՚[ٛݜȋȈ
BȈ\ܙ\˘Y؜ٝ[Y[݊Ȉˋ^XZۋ[Y]Y]H˂ȈXݚ[ۏX\ٜ\ܙKЛۛX[Ӝ[ۘ[Xݚ[ۋȈY؝[SًۛȈ
BȈ\ܙ\˘Y؜ٝ[Y[݊Ȉˋ\ٜۛً[XٛȋȈXݚ[ۏX\ٜ\ܙKЛۛX[Ӝ[ۘ[Xݚ[ۋȈY؝[Q؛ًȈ[JȈ՛ۚ][Ѧȝ^؝ѦYHܘ[0ꈜڛ݋Z[˝[YHۘ\ڛݞHȜޙ1&ڱhp똚ؙ[ΈٞȝڛݛȜ1fY\0뛘q#YHو[1hp눖XZۈ0蝚؈ٜܛݱh]0니
KȈ
BȈٝ\ۈ\ܙ\YțXZ[ʊHOȓَۛ\ٜȏHܘ\ܙ\ʊKܘ\ܙW؜ٜʊBȈݛݚ[YWܙ\ݚXوHYٛݔݛݚ[YTٜݚXي\ٜ˜ݛݚ[YW؛ۙڙʂȈٝ[ٜˈ؜ۚ[وHݛݚ[YWܙ\ݚXًؙۛ

BȈYȝ؜ۚ[َؚ\وޜݙ[Q^]
؜ۚ[يBȈގۛٚYȏHݚ[ܝ[ݚ[YW؛ۙڙʂȈٝ[ٜ˂Ȉݝ]ٚ\Ϙ\ٜ˛ݝ]ٚ\˂Ȉܛ]Wܘ]X\ٜ˙ל]Ȉ٘ם\ٜטYٛݏ[܋ٙ][݊ғғ֗ԒӔїԑPוTєאQѓՈˈȊKȈ
BȈݛܙHHԓ]TݛܙJۛٚY˜ܛ]Wܘ]
BȈݛܙKٛܝ\ٗܘڙ[XJ
BȈȕHڛX\ވ[݈\ȜٛXݙYܛۈHٜ]Y\ݙY؝ڛ\݈ۛKȐ]^[X\ވ۝\ؙHX[ڙٜݜț]\݈ٜٝș\ܛXو[݈Xڙ\܋Xڙ\܈HݚXڙ\܊Ȉ\ٜ˝Xڙ\܋ȈݛܙKȈXڙ\יڛOX\ٜ˝Xڙ\יڛKȈXڙ\כ[Z]X\ٜ˝Xڙ\כ[Z]Ȉ
BȈݛ[X\ވHݛםٙZ۞WܚY݊ȈۛٚYϘۛٚY˂ȈXڙ\܏]Xڙ\܋Ȉܜל۝\ؙ\Ϙ\ٜ˜ܜל۝\ؙ\ț܈\݊QЕSԔԗԓՔБTʋȈܜי[ؘۙYX\ٜ˜ܜ˂Ȉ]WؘٛۙYX\ٜ˛]KȈXZۗۙ]Y]WؘٛۙYX\ٜ˞XZۗۙ]Y]KȈٜۛٗۘXٛϘ\ٜ˜ٜۛٗۘXٛ˂Ȉ
BȈ^ٜ
Ȉݛݚ[YPۛٚYݜ؝[ۑ\ܛ܋Ȉݛݚ[YQ\ܛ܋Ȉ؝ڛ\ݑ\ܛ܋Ȉԑ\ܛ܋Ȉ
H\ș^΂Ȉؚ\وޜݙ[Q^]
ȖԒQՈҖPЗHٞ߈ʈܛۈ^ڛ݊ܛۋٝ[\ʜݛ[X\ދ[ܝ\ٗ؜ؚZOQ؛ً[ٙ[ݏLˈۜݗڙ^\ϕݙJJBڙȗכ؛YW׈OHחۘZ[חȎXZ[ʊB