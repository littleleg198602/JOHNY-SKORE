from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Callable

import pandas as pd

from market_checker_app.analysis.confidence import combine_confidence
from market_checker_app.analysis.explanations import merge_reasons, merge_warnings
from market_checker_app.analysis.news_analysis import analyze_news
from market_checker_app.analysis.scoring import compute_raw_total, finalize_signal
from market_checker_app.analysis.tech_analysis import analyze_tech
from market_checker_app.analysis.yahoo_analysis import analyze_yahoo
from market_checker_app.collectors.marketcap_loader import load_market_caps
from market_checker_app.collectors.rss_client import RSSClient
from market_checker_app.collectors.yahoo_client import YahooClient
from market_checker_app.config import AppConfig
from market_checker_app.models import AnalysisProgressState, RunMetadata
from market_checker_app.services.progress_service import ProgressService
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

        progress.set_global_step("start", "Inicializuji pipeline", 0.02)
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
            progress.set_current(ticker, idx, "start", f"Zpracovávám ticker {ticker} ({idx}/{total})")
            progress.log("INFO", f"Start analýzy {ticker} ({idx}/{total})", ticker)

            progress.set_step(ticker, "fetch_rss", f"Načítám RSS feedy pro {ticker}", 0.1)
            ticker_articles = [article for article in articles if article.ticker == ticker]

            progress.set_step(ticker, "parse_news", f"Vyhodnocuji sentiment článků pro {ticker}", 0.2)
            news = analyze_news(ticker, ticker_articles)
            progress.log("INFO", f"RSS: načteno {len(ticker_articles)} článků", ticker)

            progress.set_step(ticker, "fetch_yahoo", f"Načítám Yahoo data pro {ticker}", 0.35)
            snapshot, perf, yahoo_warning = self.yahoo_client.fetch_snapshots(ticker)
            if yahoo_warning:
                warnings.append(yahoo_warning)
                event_type = "FALLBACK" if "fallback" in yahoo_warning.lower() else "WARNING"
                progress.log(event_type, yahoo_warning, ticker)

            progress.set_step(ticker, "score_yahoo", f"Počítám Yahoo score pro {ticker}", 0.5)
            yresult = analyze_yahoo(snapshot)

            progress.set_step(ticker, "fetch_tech", f"Načítám OHLC data pro {ticker}", 0.62)
            ohlc, ohlc_warning = self.yahoo_client.fetch_ohlc(ticker)
            if ohlc_warning:
                warnings.append(ohlc_warning)
                progress.log("FALLBACK", ohlc_warning, ticker)

            progress.set_step(ticker, "score_tech", f"Počítám technickou analýzu pro {ticker}", 0.74)
            tech = analyze_tech(ticker, ohlc if isinstance(ohlc, pd.DataFrame) else pd.DataFrame(), source="yfinance")

            progress.set_step(ticker, "merge_scores", f"Skládám finální score pro {ticker}", 0.9)
            conf = combine_confidence(news.news_confidence, tech.tech_confidence, yresult.yahoo_confidence)
            raw_total = compute_raw_total(news.news_score, tech.tech_score, yresult.yahoo_score)
            combined_warnings = merge_warnings(news.warnings, tech.warnings, yresult.warnings)
            combined_reasons = merge_reasons(news.reasons, tech.reasons, yresult.reasons)
            diag = finalize_signal(raw_total, conf.final_confidence, conf.data_quality_score, combined_warnings, combined_reasons)

            for diag_warning in diag.warnings:
                progress.log("WARNING", diag_warning, ticker)

            row = {
                "ticker": ticker,
                "market_cap_usd": market_caps.get(ticker, snapshot.data.get("marketCap")),
                "rank_market_cap": idx,
                "news_count_48h": news.news_count_48h,
                "news_score": news.news_score,
                "tech_score": tech.tech_score,
                "yahoo_score": yresult.yahoo_score,
                "raw_total_score": diag.raw_total_score,
                "final_total_score": diag.final_total_score,
                "final_confidence": conf.final_confidence,
                "news_confidence": conf.news_confidence,
                "tech_confidence": conf.tech_confidence,
                "yahoo_confidence": conf.yahoo_confidence,
                "data_quality_score": conf.data_quality_score,
                "signal": diag.signal,
                "signal_strength": diag.signal_strength,
                "warnings": json.dumps(diag.warnings, ensure_ascii=False),
                "reasons": json.dumps(diag.reasons, ensure_ascii=False),
                "last_week_change_pct": perf.last_week_change_pct,
                "last_1m_change_pct": perf.last_1m_change_pct,
                "last_3m_change_pct": perf.last_3m_change_pct,
            }
            rows.append(row)

            progress.add_completed_row(
                {
                    "Ticker": ticker,
                    "NewsScore": round(news.news_score, 2),
                    "TechScore": round(tech.tech_score, 2),
                    "YahooScore": round(yresult.yahoo_score, 2),
                    "FinalTotalScore": round(diag.final_total_score, 2),
                    "Signal": diag.signal,
                    "FinalConfidence": round(conf.final_confidence, 2),
                    "Status": "Dokončeno",
                }
            )
            progress.log(
                "DONE",
                f"Dokončeno: {ticker} → {diag.signal} / {diag.final_total_score:.1f} / confidence {conf.final_confidence:.1f}",
                ticker,
            )

        signals_df = pd.DataFrame(rows)
        if not signals_df.empty and signals_df["market_cap_usd"].notna().any():
            signals_df = signals_df.sort_values("market_cap_usd", ascending=False, na_position="last")
            signals_df["rank_market_cap"] = range(1, len(signals_df) + 1)

        sources_df = pd.DataFrame({"source": expanded_rss_sources})
        articles_df = pd.DataFrame([asdict(article) for article in articles])
        finished_at = utc_now()

        metadata = RunMetadata(started_at, finished_at, len(watchlist), len(signals_df), len(warnings), len(errors), "")
        run_id: int | None = None
        if self.config.save_history and store is not None:
            progress.set_global_step("save_history", "Ukládám výsledky do SQLite historie", 0.95)
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
