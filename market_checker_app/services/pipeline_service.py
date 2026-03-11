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
from market_checker_app.analysis.scoring import apply_regime_overrides, compute_raw_total, finalize_signal
from market_checker_app.analysis.tech_analysis import analyze_tech
from market_checker_app.analysis.yahoo_analysis import analyze_yahoo
from market_checker_app.collectors.marketcap_loader import load_market_caps
from market_checker_app.collectors.rss_client import RSSClient
from market_checker_app.collectors.yahoo_client import YahooClient
from market_checker_app.config import AppConfig
from market_checker_app.models import AnalysisProgressState, RunMetadata
from market_checker_app.services.progress_service import ProgressService
from market_checker_app.services.ranking_service import RankingService
from market_checker_app.storage.sqlite_store import SQLiteStore
from market_checker_app.utils.dates import utc_now


class PipelineService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
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

        market_caps, marketcap_warning = load_market_caps(self.config.marketcap_file)
        if marketcap_warning:
            warnings.append(marketcap_warning)

        expanded_rss_sources = self._expand_rss_sources(rss_sources, watchlist)
        articles, rss_warnings = self.rss_client.collect(expanded_rss_sources, watchlist)
        warnings.extend(rss_warnings)

        rows: list[dict[str, object]] = []
        total = len(watchlist)
        for idx, ticker in enumerate(watchlist, start=1):
            progress.set_current(ticker, idx, "start", f"Zpracovávám {ticker} ({idx}/{total})")
            ticker_articles = [article for article in articles if article.ticker == ticker]
            news = analyze_news(ticker, ticker_articles)

            snapshot, perf, yahoo_warning = self.yahoo_client.fetch_snapshots(ticker)
            if yahoo_warning:
                warnings.append(yahoo_warning)
            yresult = analyze_yahoo(snapshot)

            ohlc, ohlc_warning = self.yahoo_client.fetch_ohlc(ticker)
            if ohlc_warning:
                warnings.append(ohlc_warning)
            tech = analyze_tech(ticker, ohlc if isinstance(ohlc, pd.DataFrame) else pd.DataFrame(), source="yfinance")
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

            combined_warnings = merge_warnings(news.warnings, tech.warnings, yresult.warnings, behavioral.warnings, risk.risk_flags)
            combined_reasons = merge_reasons(news.reasons, tech.reasons, yresult.reasons, behavioral.reasons, risk.risk_reasons)
            key_drivers = build_key_drivers(news.news_score, tech.tech_score, yresult.yahoo_score, behavioral.behavioral_score, risk.risk_score, regime)
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
            try:
                store.ensure_schema()
                run_id = store.insert_run(metadata)
                store.insert_signal_history(run_id, signals_df, datetime.now(timezone.utc).isoformat())
            except Exception as exc:
                warnings.append(f"SQLite uložení běhu selhalo: {exc}")

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
