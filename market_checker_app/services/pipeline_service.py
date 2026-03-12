from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
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
from market_checker_app.models import AnalysisProgressState, RunMetadata
from market_checker_app.services.progress_service import ProgressService
from market_checker_app.services.ranking_service import RankingService
from market_checker_app.storage.sqlite_store import SQLiteStore
from market_checker_app.utils.dates import utc_now


SCORING_VERSION = "v2_multilayer_legacy_compare"


class PipelineService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.mt5_client = MT5Client()
        self.rss_client = RSSClient(max_items_per_source=config.max_rss_items_per_source)
        self.yahoo_client = YahooClient()

    @staticmethod
    def _expand_rss_sources(rss_sources: list[str], watchlist: list[str]) -> list[str]:
        expanded: list[str] = []
        for source in rss_sources:
            if "{ticker}" in source:
                expanded.extend(source.replace("{ticker}", ticker) for ticker in watchlist)
            else:
                expanded.append(source)
        return sorted(set(expanded))

    def run(
        self,
        watchlist: list[str],
        rss_sources: list[str],
        store: SQLiteStore | None,
        progress_callback: Callable[[AnalysisProgressState], None] | None = None,
    ) -> dict[str, pd.DataFrame | RunMetadata | list[str] | int | None | AnalysisProgressState]:
        started_at = utc_now()
        warnings: list[str] = []
        errors: list[str] = []
        progress = ProgressService(total_symbols=len(watchlist), max_logs=30, on_update=progress_callback)

        progress.set_global_step("start", "Inicializuji pipeline", 0.02)
        progress.log("INFO", f"Start analýzy pro {len(watchlist)} tickerů")

        market_caps, marketcap_warning = load_market_caps(self.config.marketcap_file)
        if marketcap_warning:
            warnings.append(marketcap_warning)
            progress.log("WARNING", marketcap_warning)

        expanded_rss_sources = self._expand_rss_sources(rss_sources, watchlist)
        progress.set_global_step("fetch_rss", "Načítám RSS feedy", 0.08)
        articles, rss_warnings = self.rss_client.collect(expanded_rss_sources, watchlist)
        warnings.extend(rss_warnings)
        progress.log("INFO", f"RSS načteny: {len(articles)} článků z {len(expanded_rss_sources)} zdrojů")
        for warning in rss_warnings:
            progress.log("WARNING", warning)

        rows: list[dict[str, object]] = []
        total = len(watchlist)
        for idx, ticker in enumerate(watchlist, start=1):
            progress.set_current(ticker, idx, "start", f"Zpracovávám {ticker} ({idx}/{total})")
            progress.set_step(ticker, "parse_news", f"Vyhodnocuji news pro {ticker}", 0.2)
            ticker_articles = [article for article in articles if article.ticker == ticker]
            news = analyze_news(ticker, ticker_articles)
            progress.log("INFO", f"RSS: načteno {len(ticker_articles)} článků", ticker)

            progress.set_step(ticker, "fetch_yahoo", f"Načítám Yahoo data pro {ticker}", 0.35)
            snapshot, perf, yahoo_warning = self.yahoo_client.fetch_snapshots(ticker)
            if yahoo_warning:
                warnings.append(yahoo_warning)
                progress.log("WARNING", yahoo_warning, ticker)
            progress.set_step(ticker, "score_yahoo", f"Počítám Yahoo score pro {ticker}", 0.5)
            yresult = analyze_yahoo(snapshot)

            tech_source_used = "mt5"
            tech_source_warning: str | None = None
            progress.set_step(ticker, "fetch_tech", f"Načítám OHLC data pro {ticker}", 0.62)
            mt5_ohlc, mt5_warning = self.mt5_client.fetch_ohlcv(ticker)
            if mt5_ohlc is not None and not mt5_ohlc.empty:
                ohlc = mt5_ohlc
            else:
                tech_source_used = "yfinance_fallback"
                ohlc, ohlc_warning = self.yahoo_client.fetch_ohlc(ticker)
                fallback_parts = [f"MT5 not used for {ticker}"]
                if mt5_warning:
                    fallback_parts.append(f"reason: {mt5_warning}")
                if ohlc_warning:
                    fallback_parts.append(f"yfinance: {ohlc_warning}")
                tech_source_warning = " | ".join(fallback_parts)
                warnings.append(tech_source_warning)
                progress.log("FALLBACK", tech_source_warning, ticker)

            progress.set_step(ticker, "score_tech", f"Počítám technickou analýzu pro {ticker}", 0.74)
            tech = analyze_tech(ticker, ohlc if isinstance(ohlc, pd.DataFrame) else pd.DataFrame(), source=tech_source_used)
            if tech_source_warning:
                tech.warnings.append(tech_source_warning)

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
                final_confidence=conf.final_confidence,
                data_quality=conf.data_quality_score,
                risk_score=risk.risk_score,
                adjustment=self.config.adjustment,
                thresholds=self.config.signal_thresholds,
                reasons=combined_reasons,
                warnings=combined_warnings,
                key_drivers=key_drivers,
            )

            row = {
                "ticker": ticker,
                "market_cap_usd": market_caps.get(ticker, snapshot.data.get("marketCap")),
                "current_price": snapshot.data.get("currentPrice"),
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
                "raw_total_score": diag.raw_total_score,
                "quality_adjusted_score": diag.quality_adjusted_score,
                "risk_adjusted_score": diag.risk_adjusted_score,
                "final_total_score": diag.final_total_score,
                "final_confidence": conf.final_confidence,
                "news_confidence": conf.news_confidence,
                "tech_confidence": conf.tech_confidence,
                "yahoo_confidence": conf.yahoo_confidence,
                "behavioral_confidence": conf.behavioral_confidence,
                "data_quality_score": conf.data_quality_score,
                "signal": diag.signal,
                "signal_strength": diag.signal_strength,
                "regime": regime,
                "risk_flags": json.dumps(risk.risk_flags, ensure_ascii=False),
                "reasons": json.dumps(diag.reasons, ensure_ascii=False),
                "warnings": json.dumps(diag.warnings, ensure_ascii=False),
                "key_drivers": json.dumps(diag.key_drivers, ensure_ascii=False),
                "overall_summary": diag.overall_summary,
                "last_week_change_pct": perf.last_week_change_pct,
                "last_1m_change_pct": perf.last_1m_change_pct,
                "last_3m_change_pct": perf.last_3m_change_pct,
            }
            rows.append(row)
            progress.add_completed_row({
                "Ticker": ticker,
                "FinalTotalScore": round(diag.final_total_score, 2),
                "Signal": diag.signal,
                "Confidence": round(conf.final_confidence, 2),
                "TechSource": tech_source_used,
                "Status": "Dokončeno",
            })
            progress.log("DONE", f"Dokončeno: {ticker} → {diag.signal} / {diag.final_total_score:.1f}", ticker)

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
            progress.set_global_step("save_history", "Ukládám výsledky do SQLite historie", 0.96)
            try:
                store.ensure_schema()
                run_id = store.insert_run(metadata)
                store.insert_signal_history(run_id, signals_df, datetime.now(timezone.utc).isoformat())
                progress.log("INFO", "Historie uložena do SQLite")
            except Exception as exc:
                warning_msg = f"SQLite uložení běhu selhalo: {exc}"
                warnings.append(warning_msg)
                progress.log("ERROR", warning_msg)

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
