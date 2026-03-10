from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

import pandas as pd

from market_checker_app.analysis.scoring import combine_scores, decide_signal, score_news, score_tech, score_yahoo
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

    def run(self, watchlist: list[str], rss_sources: list[str], store: SQLiteStore | None) -> dict[str, pd.DataFrame | RunMetadata | list[str] | int | None]:
        started_at = utc_now()
        warnings: list[str] = []
        errors: list[str] = []

        market_caps, marketcap_warning = load_market_caps(self.config.marketcap_file)
        if marketcap_warning:
            warnings.append(marketcap_warning)

        articles, rss_warnings = self.rss_client.collect(rss_sources=rss_sources, tickers=watchlist)
        warnings.extend(rss_warnings)

        rows: list[dict[str, object]] = []
        for idx, ticker in enumerate(watchlist, start=1):
            related_news = [a for a in articles if a.ticker == ticker]
            news_weighted_48h = float(sum(a.sentiment_weight for a in related_news))
            news_volume_48h = len(related_news)
            news_score = score_news(news_weighted_48h, news_volume_48h)

            yahoo_snapshot, perf, yahoo_warning = self.yahoo_client.fetch_snapshots(ticker)
            if yahoo_warning:
                warnings.append(yahoo_warning)
            tech_score = 50.0
            yahoo_score = score_yahoo(yahoo_snapshot)
            total_score = combine_scores(news_score, tech_score, yahoo_score)
            signal = decide_signal(total_score)

            rows.append(
                {
                    "ticker": ticker,
                    "market_cap_usd": market_caps.get(ticker),
                    "rank_market_cap": idx,
                    "news_weighted_48h": news_weighted_48h,
                    "news_volume_48h": news_volume_48h,
                    "news_score": news_score,
                    "tech_score": tech_score,
                    "yahoo_score": yahoo_score,
                    "total_score": total_score,
                    "signal": signal,
                    "tech_status": "ok",
                    "yahoo_status": yahoo_snapshot.status,
                    "last_week_change_pct": perf.last_week_change_pct,
                    "last_1m_change_pct": perf.last_1m_change_pct,
                    "last_3m_change_pct": perf.last_3m_change_pct,
                }
            )

        signal_columns = [
            "ticker",
            "market_cap_usd",
            "rank_market_cap",
            "news_weighted_48h",
            "news_volume_48h",
            "news_score",
            "tech_score",
            "yahoo_score",
            "total_score",
            "signal",
            "tech_status",
            "yahoo_status",
            "last_week_change_pct",
            "last_1m_change_pct",
            "last_3m_change_pct",
        ]
        signals_df = pd.DataFrame(rows, columns=signal_columns)
        if not signals_df.empty and signals_df["market_cap_usd"].notna().any():
            signals_df = signals_df.sort_values(by="market_cap_usd", ascending=False, na_position="last")
            signals_df["rank_market_cap"] = range(1, len(signals_df) + 1)

        sources_df = pd.DataFrame({"source": rss_sources})
        articles_df = pd.DataFrame([asdict(a) for a in articles])
        finished_at = utc_now()

        metadata = RunMetadata(
            started_at=started_at,
            finished_at=finished_at,
            watchlist_size=len(watchlist),
            processed_symbols=len(signals_df),
            warnings_count=len(warnings),
            errors_count=len(errors),
            excel_path="",
        )

        run_id: int | None = None
        if self.config.save_history and store is not None:
            try:
                store.ensure_schema()
                run_id = store.insert_run(metadata)
                store.insert_signal_history(run_id, signals_df, datetime.now(timezone.utc).isoformat())
            except Exception as exc:
                warnings.append(f"SQLite uložení běhu selhalo. Aplikace pokračuje bez historie tohoto běhu. Detail: {exc}")

        return {
            "metadata": metadata,
            "signals": signals_df,
            "sources": sources_df,
            "articles": articles_df,
            "warnings": warnings,
            "errors": errors,
            "run_id": run_id,
        }
