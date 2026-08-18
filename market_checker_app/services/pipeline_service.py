from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
import time
from typing import Callable

import pandas as pd

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
from market_checker_app.collectors.mt5_client import MT5Client
from market_checker_app.collectors.rss_client import RSSClient
from market_checker_app.collectors.yahoo_client import YahooClient
from market_checker_app.config import AppConfig
from market_checker_app.models import (
    AnalysisProgressState,
    PerformanceSnapshot,
    RunMetadata,
    YahooAnalysisResult,
    YahooSnapshot,
)
from market_checker_app.services.progress_service import ProgressService
from market_checker_app.services.ranking_service import RankingService
from market_checker_app.storage.sqlite_store import SQLiteStore
from market_checker_app.storage.yahoo_cache_store import YahooCacheStore
from market_checker_app.utils.dates import utc_now


SCORING_VERSION = "v2.1_guarded_consensus"


class PipelineService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.mt5_client = MT5Client()
        self.rss_client = RSSClient(max_items_per_source=config.max_rss_items_per_source)
        self.yahoo_client = YahooClient()
        self.yahoo_cache = YahooCacheStore(config.sqlite_path)

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
    def _current_price_from_ohlc(ohlc: pd.DataFrame | None) -> float | None:
        if ohlc is None or ohlc.empty or "Close" not in ohlc.columns:
            return None
        close = pd.to_numeric(ohlc["Close"], errors="coerce").dropna()
        return float(close.iloc[-1]) if not close.empty else None

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
    ) -> dict[str, pd.DataFrame | RunMetadata | list[str] | int | None | AnalysisProgressState]:
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
                errors.append(
                    "MT5 je pro velký universe vypnuté. Technická data budou neutrální; "
                    "pro plnou analýzu zapněte MT5."
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
                errors.append(
                    f"MT5 OHLC selhalo pro všech {len(mt5_tickers)} tickerů. "
                    "Technické skóre bude neutrální s nízkou důvěrou."
                )
            elif mt5_failure_count:
                warnings.append(
                    f"MT5 OHLC není dostupné pro {mt5_failure_count} z {len(mt5_tickers)} tickerů."
                )

        articles_by_ticker: dict[str, list] = {}
        for article in articles:
            articles_by_ticker.setdefault(article.ticker, []).append(article)

        rows: list[dict[str, object]] = []
        yahoo_snapshot_failures = 0
        yahoo_ohlc_attempts = 0
        yahoo_ohlc_failures = 0
        for idx, ticker in enumerate(watchlist, start=1):
            progress.set_current(ticker, idx, "start", f"Zpracovávám {ticker} ({idx}/{total})")
            progress.set_step(ticker, "parse_news", f"Vyhodnocuji news pro {ticker}", 0.2)
            ticker_articles = articles_by_ticker.get(ticker, [])
            news = analyze_news(ticker, ticker_articles)

            yahoo_data_status = "pending"
            yahoo_data_fetched_at: str | None = None
            yahoo_ticker = YahooClient.normalize_yahoo_symbol(ticker)

            if use_yahoo_cache:
                cache_lookup = self.yahoo_cache.get(ticker)
                cache_record = cache_lookup.record
                if cache_lookup.usable and cache_record is not None and cache_record.data:
                    cached_data = dict(cache_record.data)
                    metadata_quality = str(
                        cached_data.pop("_market_checker_yahoo_quality", "ok")
                    )
                    snapshot = YahooSnapshot(
                        ticker=cache_record.yahoo_ticker,
                        data=cached_data,
                        status=metadata_quality if metadata_quality in {"ok", "partial"} else "ok",
                    )
                    perf = PerformanceSnapshot(ticker, None, None, None, None)
                    yahoo_warning = cache_record.error
                    yahoo_data_status = f"cache_{cache_lookup.state}"
                    yahoo_data_fetched_at = cache_record.fetched_at.isoformat()
                    yahoo_ticker = cache_record.yahoo_ticker
                    yresult = analyze_yahoo(snapshot)
                    if cache_lookup.state == "stale":
                        yresult.yahoo_confidence = round(yresult.yahoo_confidence * 0.75, 2)
                        stale_warning = "Yahoo metadata jsou zastaralá; probíhá čekání na obnovení cache"
                        yresult.warnings.append(stale_warning)
                    if metadata_quality == "partial":
                        yresult.yahoo_confidence = round(yresult.yahoo_confidence * 0.8, 2)
                        yresult.warnings.append("Yahoo metadata jsou pouze částečná")
                else:
                    snapshot = YahooSnapshot(ticker=yahoo_ticker, data={}, status=cache_lookup.state)
                    perf = PerformanceSnapshot(ticker, None, None, None, None)
                    yahoo_warning = cache_record.error if cache_record is not None else None
                    yahoo_data_status = cache_lookup.state
                    reason = (
                        f"Yahoo metadata nejsou připravená (stav: {cache_lookup.state}); "
                        "doplňte Yahoo cache"
                    )
                    yresult = self._neutral_yahoo_result(ticker, reason)
            elif yahoo_metadata_enabled:
                snapshot, perf, yahoo_warning = self.yahoo_client.fetch_snapshots(ticker)
                if snapshot.status not in {"ok", "partial"}:
                    yahoo_snapshot_failures += 1
                if yahoo_warning:
                    warnings.append(yahoo_warning)
                    progress.log("WARNING", yahoo_warning, ticker)
                progress.set_step(ticker, "score_yahoo", f"Počítám Yahoo score pro {ticker}", 0.5)
                yresult = analyze_yahoo(snapshot)
                yahoo_data_status = f"live_{snapshot.status}"
                yahoo_data_fetched_at = started_at.isoformat()
                yahoo_ticker = snapshot.ticker
            else:
                snapshot = YahooSnapshot(ticker=ticker, data={}, status="skipped")
                perf = PerformanceSnapshot(ticker, None, None, None, None)
                yahoo_warning = None
                yahoo_data_status = "disabled"
                yresult = self._neutral_yahoo_result(ticker, "Yahoo metadata jsou vypnutá")

            tech_source_used = "mt5"
            tech_source_warning: str | None = None
            progress.set_step(ticker, "fetch_tech", f"Načítám OHLC data pro {ticker}", 0.62)

            if not mt5_enabled or ticker in yahoo_only_tickers:
                if large_universe_mode:
                    tech_source_used = "bulk_price_source_unavailable"
                    ohlc = pd.DataFrame()
                    tech_source_warning = (
                        f"MT5 není použito pro {ticker}; ve velkém universe režimu se "
                        "jednotlivé Yahoo OHLC požadavky nepouštějí."
                    )
                else:
                    yahoo_ohlc_attempts += 1
                    tech_source_used = "yfinance_excel" if ticker in yahoo_only_tickers else "yfinance"
                    fetch_ohlc = (
                        self.yahoo_client.fetch_ohlc
                        if yahoo_metadata_enabled
                        else self.yahoo_client.fetch_ohlc_only
                    )
                    ohlc, ohlc_warning = fetch_ohlc(ticker)
                    if ohlc_warning:
                        yahoo_ohlc_failures += 1
                        tech_source_warning = ohlc_warning
                        if not yahoo_warning or ohlc_warning not in yahoo_warning:
                            warnings.append(ohlc_warning)
                        progress.log("WARNING", ohlc_warning, ticker)
                    else:
                        progress.log("INFO", f"Technická data pro {ticker}: Yahoo Finance", ticker)
            else:
                mt5_ohlc = mt5_ohlc_by_ticker.get(ticker)
                mt5_warning = mt5_warnings_by_ticker.get(ticker)
                if mt5_ohlc is not None and not mt5_ohlc.empty:
                    ohlc = mt5_ohlc
                elif large_universe_mode:
                    tech_source_used = "mt5_unavailable"
                    ohlc = pd.DataFrame()
                    tech_source_warning = mt5_warning or f"MT5 OHLC není dostupné pro {ticker}."
                else:
                    tech_source_used = "yfinance_fallback"
                    yahoo_ohlc_attempts += 1
                    fetch_ohlc = (
                        self.yahoo_client.fetch_ohlc
                        if yahoo_metadata_enabled
                        else self.yahoo_client.fetch_ohlc_only
                    )
                    ohlc, ohlc_warning = fetch_ohlc(ticker)
                    fallback_parts = [f"MT5 not used for {ticker}"]
                    if mt5_warning:
                        fallback_parts.append(f"reason: {mt5_warning}")
                    if ohlc_warning:
                        yahoo_ohlc_failures += 1
                        fallback_parts.append(f"yfinance: {ohlc_warning}")
                    tech_source_warning = " | ".join(fallback_parts)
                    warnings.append(tech_source_warning)
                    progress.log("FALLBACK", tech_source_warning, ticker)

            progress.set_step(ticker, "score_tech", f"Počítám technickou analýzu pro {ticker}", 0.74)
            tech = analyze_tech(ticker, ohlc if isinstance(ohlc, pd.DataFrame) else pd.DataFrame(), source=tech_source_used)
            if tech_source_warning:
                tech.warnings.append(tech_source_warning)

            derived_perf = self._performance_from_ohlc(
                ticker,
                ohlc if isinstance(ohlc, pd.DataFrame) else None,
            )
            mt5_current_price = self._current_price_from_ohlc(
                ohlc if isinstance(ohlc, pd.DataFrame) else None
            )
            yahoo_current_price = snapshot.data.get("currentPrice")
            if tech_source_used == "mt5" and mt5_current_price is not None:
                # Weekly prediction evaluation must not reuse a stale price
                # hidden inside the longer-lived Yahoo metadata cache.
                current_price = mt5_current_price
                current_price_source = "mt5_close"
            elif yahoo_current_price is not None:
                current_price = yahoo_current_price
                current_price_source = "yahoo_metadata"
            else:
                current_price = mt5_current_price
                current_price_source = "ohlc_fallback" if mt5_current_price is not None else "missing"

            progress.set_step(ticker, "behavioral_risk", f"Počítám behavioral a risk vrstvu pro {ticker}", 0.82)
            behavioral = analyze_behavioral(ticker, news, tech, yresult, self.config.behavioral_weights)
            risk = analyze_risk(ticker, news, tech, yresult, behavioral)

            regime = detect_market_regime(
                momentum_1m=float(tech.indicators.get("p1m") or 0.0),
                realized_volatility=float(tech.indicators.get("realized_volatility") or 0.02),
                panic_score=behavioral.panic_score,
                euphoria_score=behavioral.euphoria_score,
            )

            conf = combine_confidence(news.news_confidence, tech.tech_confidence, yresult.yahoo_confidence, behavioral.behavioral_confidence)
            raw_total = compute_raw_total(news.news_score, tech.tech_score, yresult.yahoo_score, behavioral.behavioral_score, self.config.module_weights)
            raw_total = apply_regime_overrides(raw_total, tech.tech_score, tech.oscillator_score, behavioral.behavioral_score, regime, self.config.regime_overrides)
            legacy_total_score = compute_legacy_total(news.news_score, tech.tech_score, yresult.yahoo_score)
            legacy_signal = legacy_signal_from_score(legacy_total_score)

            combined_warnings = merge_warnings(news.warnings, tech.warnings, yresult.warnings, behavioral.warnings, risk.risk_flags)
            combined_reasons = merge_reasons(news.reasons, tech.reasons, yresult.reasons, behavioral.reasons, risk.risk_reasons)
            key_drivers = build_key_drivers(news.news_score, tech.tech_score, yresult.yahoo_score, behavioral.behavioral_score, risk.risk_score, regime)
            progress.set_step(ticker, "merge_scores", f"Skládám finální score pro {ticker}", 0.92)
            diag = finalize_signal(
                raw_score=raw_total,
                data_quality=conf.data_quality_score,
                risk_score=risk.risk_score,
                adjustment=self.config.adjustment,
                thresholds=self.config.signal_thresholds,
                reasons=combined_reasons,
                warnings=combined_warnings,
                key_drivers=key_drivers,
                news_score=news.news_score,
                tech_score=tech.tech_score,
                analyst_score=yresult.yahoo_score,
                panic_score=behavioral.panic_score,
                news_confidence=conf.news_confidence,
                tech_confidence=conf.tech_confidence,
                analyst_confidence=conf.yahoo_confidence,
                panic_confidence=conf.behavioral_confidence,
                decision_weights=self.config.decision_weights,
                decision_thresholds=self.config.decision_thresholds,
                legacy_signal=legacy_signal,
                risk_flags=risk.risk_flags,
                prediction_v21=self.config.prediction_v21,
            )

            row = {
                "ticker": ticker,
                "market_cap_usd": market_caps.get(ticker, snapshot.data.get("marketCap")),
                "current_price": current_price,
                "current_price_source": current_price_source,
                "yahoo_ticker": yahoo_ticker,
                "yahoo_data_status": yahoo_data_status,
                "yahoo_data_fetched_at": yahoo_data_fetched_at,
                "scoring_version": SCORING_VERSION,
                "legacy_total_score": legacy_total_score,
                "legacy_signal": legacy_signal,
                "tech_source_used": tech_source_used,
                "news_count_48h": news.news_count_48h,
                "news_score": news.news_score,
                "tech_score": tech.tech_score,
                "yahoo_score": yresult.yahoo_score,
                "behavioral_score": behavioral.behavioral_score,
                "risk_score": risk.risk_score,
                "panic_score": behavioral.panic_score,
                "raw_total_score": diag.raw_total_score,
                "quality_adjusted_score": diag.quality_adjusted_score,
                "risk_adjusted_score": diag.risk_adjusted_score,
                "final_total_score": diag.final_total_score,
                "final_confidence": diag.final_confidence,
                "module_confidence": diag.module_confidence,
                "decision_confidence": diag.decision_confidence,
                "news_confidence": conf.news_confidence,
                "tech_confidence": conf.tech_confidence,
                "yahoo_confidence": conf.yahoo_confidence,
                "behavioral_confidence": conf.behavioral_confidence,
                "data_quality_score": conf.data_quality_score,
                "decision_signal": diag.signal,
                "forecast": diag.forecast,
                "action": diag.action,
                "action_reasons": json.dumps(diag.action_reasons, ensure_ascii=False),
                # `signal` remains the public recommendation column used by
                # older UI/export consumers.  In v2.1 it mirrors the guarded
                # executable action rather than the unguarded model decision.
                "signal": diag.action,
                "signal_strength": diag.signal_strength,
                "bull_score": diag.bull_score,
                "bear_score": diag.bear_score,
                "bull_bear_spread": diag.bull_bear_spread,
                "bullish_module_count": diag.bullish_module_count,
                "bearish_module_count": diag.bearish_module_count,
                "neutral_module_count": diag.neutral_module_count,
                "downgrade_count": diag.downgrade_count,
                "blocked_reasons": json.dumps(diag.blocked_reasons, ensure_ascii=False),
                "module_breakdown": json.dumps(diag.module_breakdown, ensure_ascii=False),
                "regime": regime,
                "risk_flags": json.dumps(risk.risk_flags, ensure_ascii=False),
                "reasons": json.dumps(diag.reasons, ensure_ascii=False),
                "warnings": json.dumps(diag.warnings, ensure_ascii=False),
                "key_drivers": json.dumps(diag.key_drivers, ensure_ascii=False),
                "overall_summary": diag.overall_summary,
                "last_week_change_pct": perf.last_week_change_pct if perf.last_week_change_pct is not None else derived_perf.last_week_change_pct,
                "last_14d_change_pct": perf.last_14d_change_pct if perf.last_14d_change_pct is not None else derived_perf.last_14d_change_pct,
                "last_1m_change_pct": perf.last_1m_change_pct if perf.last_1m_change_pct is not None else derived_perf.last_1m_change_pct,
                "last_3m_change_pct": perf.last_3m_change_pct if perf.last_3m_change_pct is not None else derived_perf.last_3m_change_pct,
            }
            rows.append(row)
            progress.add_completed_row({
                "Ticker": ticker,
                "FinalTotalScore": round(diag.final_total_score, 2),
                "Signal": diag.action,
                "Forecast": diag.forecast,
                "Confidence": round(diag.decision_confidence, 2),
                "TechSource": tech_source_used,
                "Status": "Dokončeno",
            })
            progress.log(
                "DONE",
                f"Dokončeno: {ticker} → {diag.action} (forecast {diag.forecast}) / {diag.final_total_score:.1f}",
                ticker,
            )

        if yahoo_metadata_enabled and yahoo_snapshot_failures == total:
            errors.append(
                "Yahoo metadata selhala pro všechny tickery. Fundamentální část výsledků používá fallback a není spolehlivá."
            )
        elif yahoo_metadata_enabled and yahoo_snapshot_failures:
            warnings.append(f"Yahoo metadata selhala pro {yahoo_snapshot_failures} z {total} tickerů.")

        if yahoo_ohlc_attempts and yahoo_ohlc_failures == yahoo_ohlc_attempts:
            errors.append(
                "Yahoo cenová historie selhala pro všechny tickery, které ji potřebovaly. Technická část používá fallback."
            )
        elif yahoo_ohlc_failures:
            warnings.append(
                f"Yahoo cenová historie selhala pro {yahoo_ohlc_failures} z {yahoo_ohlc_attempts} tickerů."
            )

        warnings = list(dict.fromkeys(warnings))
        errors = list(dict.fromkeys(errors))
        signals_df = RankingService.apply_ranking(pd.DataFrame(rows))
        if not signals_df.empty and signals_df["market_cap_usd"].notna().any():
            signals_df = signals_df.sort_values("market_cap_usd", ascending=False, na_position="last")
            signals_df["rank_market_cap"] = range(1, len(signals_df) + 1)

        sources_df = pd.DataFrame({"source": expanded_rss_sources})
        articles_df = pd.DataFrame([asdict(article) for article in articles])
        finished_at = utc_now()

        metadata = RunMetadata(started_at, finished_at, len(watchlist), len(signals_df), len(warnings), len(errors), "")
        run_id: int | None = None
        if self.config.save_history and store is not None:
            progress.set_global_step("save_history", "Ukládám výsledky do SQLite historie", 0.98)
            try:
                run_id = store.save_run(
                    metadata,
                    signals_df,
                    datetime.now(timezone.utc).isoformat(),
                )
            except Exception as exc:
                message = f"SQLite uložení běhu selhalo: {exc}"
                warnings.append(message)
                errors.append(message)
                progress.log("ERROR", message)

        metadata.warnings_count = len(warnings)
        metadata.errors_count = len(errors)

        progress.finalize("Analýza dokončena")
        return {
            "metadata": metadata,
            "signals": signals_df,
            "sources": sources_df,
            "articles": articles_df,
            "warnings": warnings,
            "errors": errors,
            "run_id": run_id,
            "progress_state": progress.snapshot(),
        }
