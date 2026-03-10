from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone

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
from market_checker_app.models import RunMetadata
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
                expanded.extend(source.replace("{ticker}", t) for t in watchlist)
            else:
                expanded.append(source)
        return sorted(set(expanded))

    def run(self, watchlist: list[str], rss_sources: list[str], store: SQLiteStore | None) -> dict[str, pd.DataFrame | RunMetadata | list[str] | int | None]:
        started_at = utc_now()
        warnings: list[str] = []
        errors: list[str] = []

        market_caps, marketcap_warning = load_market_caps(self.config.marketcap_file)
        if marketcap_warning:
            warnings.append(marketcap_warning)

        expanded_rss_sources = self._expand_rss_sources(rss_sources, watchlist)
        articles, rss_warnings = self.rss_client.collect(expanded_rss_sources, watchlist)
        warnings.extend(rss_warnings)

        rows: list[dict[str, object]] = []
        for idx, ticker in enumerate(watchlist, start=1):
            ticker_articles = [a for a in articles if a.ticker == ticker]
            news = analyze_news(ticker, ticker_articles)

            snapshot, perf, yahoo_warning = self.yahoo_client.fetch_snapshots(ticker)
            if yahoo_warning:
                warnings.append(yahoo_warning)
            yresult = analyze_yahoo(snapshot)

            ohlc, ohlc_warning = self.yahoo_client.fetch_ohlc(ticker)
            if ohlc_warning:
                warnings.append(ohlc_warning)
            tech = analyze_tech(ticker, ohlc if isinstance(ohlc, pd.DataFrame) else pd.DataFrame(), source="yfinance")

            conf = combine_confidence(news.news_confidence, tech.tech_confidence, yresult.yahoo_confidence)
            raw_total = compute_raw_total(news.news_score, tech.tech_score, yresult.yahoo_score)
            combined_warnings = merge_warnings(news.warnings, tech.warnings, yresult.warnings)
            combined_reasons = merge_reasons(news.reasons, tech.reasons, yresult.reasons)
            diag = finalize_signal(raw_total, conf.final_confidence, conf.data_quality_score, combined_warnings, combined_reasons)

            rows.append(
                {
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
            )

        signals_df = pd.DataFrame(rows)
        if not signals_df.empty and signals_df["market_cap_usd"].notna().any():
            signals_df = signals_df.sort_values("market_cap_usd", ascending=False, na_position="last")
            signals_df["rank_market_cap"] = range(1, len(signals_df) + 1)

        sources_df = pd.DataFrame({"source": expanded_rss_sources})
        articles_df = pd.DataFrame([asdict(a) for a in articles])
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

        return {"metadata": metadata, "signals": signals_df, "sources": sources_df, "articles": articles_df, "warnings": warnings, "errors": errors, "run_id": run_id}
