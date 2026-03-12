from __future__ import annotations

from pathlib import Path
import sys

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import json
import time
from typing import Any

import pandas as pd
import streamlit as st

from market_checker_app.analysis.performance import summarize_performance
from market_checker_app.collectors.mt5_client import MT5Client
from market_checker_app.config import AppConfig, DEFAULT_DB_PATH, DEFAULT_OUTPUT_DIR
from market_checker_app.exporters.dashboard_builder import build_dashboard_tables
from market_checker_app.exporters.delta_builder import prepare_delta_for_excel
from market_checker_app.exporters.excel_exporter import ExcelExporter
from market_checker_app.models import AnalysisProgressState
from market_checker_app.services.comparison_service import ComparisonService
from market_checker_app.services.evaluation_service import EvaluationService
from market_checker_app.services.history_service import HistoryService
from market_checker_app.services.pipeline_service import PipelineService
from market_checker_app.services.ranking_service import RankingService
from market_checker_app.storage.sqlite_store import SQLiteStore


def _parse_json_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(v) for v in parsed]
        except json.JSONDecodeError:
            return [value]
    return []


def _render_progress_ui(state: AnalysisProgressState, elapsed_sec: float) -> None:
    st.write(f"Zpracovávám: **{state.current_symbol or '-'}** ({state.current_position}/{state.total_symbols})")
    st.progress(float(state.overall_progress))
    st.caption(f"{int(state.overall_progress * 100)} % • {elapsed_sec:.1f}s")





st.set_page_config(page_title="Market Checker", layout="wide")
st.title("Market Checker")

for key, default in {"watchlist": [], "last_result": None, "analysis_progress": None}.items():
    if key not in st.session_state:
        st.session_state[key] = default

with st.sidebar:
    output_dir = Path(st.text_input("Output directory", str(DEFAULT_OUTPUT_DIR)))
    marketcap_file = st.text_input("MarketCap file", "")
    export_excel = st.checkbox("Export do Excelu", value=True)
    compare_prev = st.checkbox("Porovnat s předchozím během", value=True)
    save_history = st.checkbox("Ukládat historii do SQLite", value=True)
    sqlite_path = Path(st.text_input("DB soubor", str(DEFAULT_DB_PATH)))
    max_rss = st.number_input("Max RSS items per source", min_value=1, max_value=200, value=30)
    load_watchlist = st.button("Načíst watchlist z MT5")
    run_analysis = st.button("Spustit analýzu", type="primary")

config = AppConfig(output_dir=output_dir, marketcap_file=marketcap_file, export_excel=export_excel, compare_previous_run=compare_prev, save_history=save_history, sqlite_path=sqlite_path, max_rss_items_per_source=int(max_rss))
config.ensure_output_dir()
store = SQLiteStore(config.sqlite_path)

if load_watchlist:
    watchlist, err = MT5Client().load_watchlist()
    if err:
        st.error(err)
    else:
        st.session_state.watchlist = watchlist

watchlist_text = st.text_area("Watchlist (1 ticker na řádek)", "\n".join(st.session_state.watchlist), height=130)
watchlist = MT5Client.sanitize_watchlist(watchlist_text.splitlines())
st.caption(f"Počet tickerů ve watchlistu: {len(watchlist)}")
rss_sources = [s.strip() for s in st.text_area("RSS sources", "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US").splitlines() if s.strip()]

if st.session_state.analysis_progress:
    _render_progress_ui(st.session_state.analysis_progress, 0.0)

if run_analysis:
    pipeline = PipelineService(config)
    previous = st.session_state.last_result["signals"] if st.session_state.last_result else pd.DataFrame()
    started = time.time()

    progress_placeholder = st.empty()

    def _on_progress(state: AnalysisProgressState) -> None:
        st.session_state.analysis_progress = state
        with progress_placeholder.container():
            _render_progress_ui(state, time.time() - started)

    result = pipeline.run(watchlist, rss_sources, store if save_history else None, progress_callback=_on_progress)
    delta_df = pd.DataFrame()
    if compare_prev:
        if save_history and result.get("run_id"):
            delta_df = HistoryService(store).build_delta_against_previous(int(result["run_id"]))
        elif not previous.empty:
            delta_df = ComparisonService.compare_runs(result["signals"], previous)

    dashboard_tables = build_dashboard_tables(result["signals"])
    ranking_tables = RankingService.top_bottom_tables(result["signals"])

    if export_excel:
        path = output_dir / f"market_checker_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        ExcelExporter().export(path, result["signals"], result["sources"], result["articles"], dashboard_tables, prepare_delta_for_excel(delta_df))
        st.success(f"Excel export uložen: {path}")

    result["dashboard"] = dashboard_tables
    result["ranking"] = ranking_tables
    result["delta"] = delta_df
    st.session_state.last_result = result

if st.session_state.last_result:
    result = st.session_state.last_result
    signals_df = result["signals"]

    tab_signals, tab_dashboard, tab_articles, tab_sources, tab_delta, tab_trends, tab_history, tab_ranking = st.tabs(["Signals", "Dashboard", "Articles", "Sources", "Delta", "Trends", "History", "Ranking"])

    with tab_signals:
        signal_filter = st.multiselect("Signal filter", options=sorted(signals_df["signal"].dropna().unique()), default=sorted(signals_df["signal"].dropna().unique()))
        regime_filter = st.multiselect("Regime filter", options=sorted(signals_df["regime"].dropna().unique()), default=sorted(signals_df["regime"].dropna().unique()))
        min_conf = st.slider("Min confidence", 0, 100, 0)
        max_risk = st.slider("Max risk", 0, 100, 100)
        filtered = signals_df[(signals_df["signal"].isin(signal_filter)) & (signals_df["regime"].isin(regime_filter)) & (signals_df["final_confidence"] >= min_conf) & (signals_df["risk_score"] <= max_risk)]

        display_columns = [
            "ticker", "news_score", "tech_score", "yahoo_score", "behavioral_score", "risk_score", "raw_total_score", "quality_adjusted_score", "risk_adjusted_score", "final_total_score", "final_confidence", "news_confidence", "tech_confidence", "yahoo_confidence", "behavioral_confidence", "data_quality_score", "signal", "signal_strength", "rank_in_watchlist", "percentile_in_watchlist", "regime",
        ]
        st.dataframe(filtered[[c for c in display_columns if c in filtered.columns]], width="stretch")
        ticker = st.selectbox("Detail tickeru", options=signals_df["ticker"].tolist())
        row = signals_df[signals_df["ticker"] == ticker].head(1)
        if not row.empty:
            st.markdown("**Reasons**")
            for item in _parse_json_list(row.iloc[0].get("reasons")):
                st.write(f"- {item}")
            st.markdown("**Warnings**")
            for item in _parse_json_list(row.iloc[0].get("warnings")):
                st.write(f"- {item}")
            st.markdown("**KeyDrivers**")
            for item in _parse_json_list(row.iloc[0].get("key_drivers")):
                st.write(f"- {item}")
            st.markdown(f"**OverallSummary:** {row.iloc[0].get('overall_summary', '')}")

    with tab_dashboard:
        perf = summarize_performance(signals_df)
        st.metric("Průměrný FinalTotalScore", round(perf["avg_total_score"], 2))
        st.dataframe(result["dashboard"]["top_total"], width="stretch")
        st.dataframe(result["dashboard"]["bottom_total"], width="stretch")

    with tab_articles:
        st.dataframe(result["articles"], width="stretch")
    with tab_sources:
        st.dataframe(result["sources"], width="stretch")
    with tab_delta:
        st.dataframe(result["delta"], width="stretch")

    with tab_trends:
        if save_history:
            trends = HistoryService(store).load_global_trends()
            if not trends["avg_total"].empty:
                st.line_chart(trends["avg_total"].set_index("finished_at")["final_total_score"])

    with tab_history:
        if save_history:
            hs = HistoryService(store)
            tickers = hs.list_tickers()
            if tickers:
                ticker = st.selectbox("Ticker history", tickers)
                hist = hs.load_ticker_history(ticker)
                st.line_chart(hist.set_index("finished_at")["final_total_score"])
                st.dataframe(hist, width="stretch")

    with tab_ranking:
        st.subheader("Top ranking")
        st.dataframe(result["ranking"]["top"], width="stretch")
        st.subheader("Bottom ranking")
        st.dataframe(result["ranking"]["bottom"], width="stretch")
        if save_history:
            eval_frames = EvaluationService().evaluate_snapshots(store.read_global_history())
            st.subheader("Evaluation / backtest")
            for name, frame in eval_frames.items():
                st.write(name)
                st.dataframe(frame, width="stretch")
