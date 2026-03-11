from __future__ import annotations

from pathlib import Path
import sys

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import json
import time

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
from market_checker_app.services.history_service import HistoryService
from market_checker_app.services.pipeline_service import PipelineService
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


def _format_event(event: dict[str, str]) -> str:
    ts = event.get("timestamp", "--:--:--")
    ticker = event.get("ticker", "-")
    event_type = event.get("event_type", "INFO")
    message = event.get("message", "")
    return f"`{ts}` **[{event_type}]** `{ticker}` — {message}"


def _render_progress_ui(state: AnalysisProgressState, elapsed_sec: float) -> None:
    st.subheader("Průběh analýzy")
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Aktuální ticker", state.current_symbol or "-")
    col_b.metric("Pořadí", f"{state.current_position} / {state.total_symbols}")
    col_c.metric("Aktuální fáze", state.current_step)

    st.info(state.current_message)
    st.progress(float(state.overall_progress))
    st.caption(f"Celkový průběh: {int(state.overall_progress * 100)} %")
    st.progress(float(state.ticker_progress))
    st.caption(f"Průběh aktuálního tickeru: {int(state.ticker_progress * 100)} %")

    st.markdown("#### Live log")
    if state.recent_logs:
        for event in state.recent_logs:
            st.markdown(f"- {_format_event(event)}")
    else:
        st.write("Log je zatím prázdný.")

    warn_col, fallback_col, error_col = st.columns(3)
    with warn_col:
        st.markdown("#### Warnings")
        for item in state.warnings[-10:]:
            st.warning(item)
    with fallback_col:
        st.markdown("#### Fallbacks")
        for item in state.fallbacks[-10:]:
            st.info(item)
    with error_col:
        st.markdown("#### Errors")
        for item in state.errors[-10:]:
            st.error(item)

    st.markdown("#### Průběžně dokončené tickery")
    if state.completed_rows:
        st.dataframe(pd.DataFrame(state.completed_rows), width="stretch")
    else:
        st.write("Zatím nebyl dokončen žádný ticker.")

    st.sidebar.markdown("### Live souhrn")
    st.sidebar.write(f"Watchlist size: {state.total_symbols}")
    st.sidebar.write(f"Processed: {state.processed_symbols}")
    st.sidebar.write(f"Current ticker: {state.current_symbol or '-'}")
    st.sidebar.write(f"Elapsed: {elapsed_sec:.1f}s")
    st.sidebar.write(f"Warnings: {len(state.warnings)}")
    st.sidebar.write(f"Errors: {len(state.errors)}")


st.set_page_config(page_title="Market Checker", layout="wide")
st.title("Market Checker")

for key, default in {"watchlist": [], "last_result": None, "analysis_progress": None}.items():
    if key not in st.session_state:
        st.session_state[key] = default

with st.sidebar:
    st.header("Nastavení")
    output_dir = Path(st.text_input("Output directory", str(DEFAULT_OUTPUT_DIR)))
    marketcap_file = st.text_input("MarketCap file", "")
    export_excel = st.checkbox("Export do Excelu", value=True)
    compare_prev = st.checkbox("Porovnat s předchozím během", value=True)
    save_history = st.checkbox("Ukládat historii do SQLite", value=True)
    sqlite_path = Path(st.text_input("DB soubor", str(DEFAULT_DB_PATH)))
    max_rss = st.number_input("Max RSS items per source", min_value=1, max_value=200, value=30)
    load_watchlist = st.button("Načíst watchlist z MT5")
    run_analysis = st.button("Spustit analýzu", type="primary")

config = AppConfig(
    output_dir=output_dir,
    marketcap_file=marketcap_file,
    export_excel=export_excel,
    compare_previous_run=compare_prev,
    save_history=save_history,
    sqlite_path=sqlite_path,
    max_rss_items_per_source=int(max_rss),
)
config.ensure_output_dir()
store = SQLiteStore(config.sqlite_path)

if load_watchlist:
    watchlist, err = MT5Client().load_watchlist()
    if err:
        st.error(err)
    else:
        st.session_state.watchlist = watchlist
        st.success(f"Načteno {len(watchlist)} symbolů z MT5")

watchlist_text = st.text_area("Watchlist (1 ticker na řádek)", "\n".join(st.session_state.watchlist), height=130)
watchlist = MT5Client.sanitize_watchlist(watchlist_text.splitlines())
rss_sources = [
    s.strip()
    for s in st.text_area("RSS sources", "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US").splitlines()
    if s.strip()
]

if run_analysis:
    pipeline = PipelineService(config)
    previous = st.session_state.last_result["signals"] if st.session_state.last_result else pd.DataFrame()
    started = time.time()

    progress_container = st.container()

    def _on_progress(state: AnalysisProgressState) -> None:
        st.session_state.analysis_progress = state
        with progress_container:
            _render_progress_ui(state, time.time() - started)

    result = pipeline.run(
        watchlist,
        rss_sources,
        store if save_history else None,
        progress_callback=_on_progress,
    )
    st.session_state.analysis_progress = result.get("progress_state")

    delta_df = pd.DataFrame()
    if compare_prev:
        if save_history and result.get("run_id"):
            delta_df = HistoryService(store).build_delta_against_previous(int(result["run_id"]))
        elif not previous.empty:
            delta_df = ComparisonService.compare_runs(result["signals"], previous)

    if st.session_state.analysis_progress and export_excel:
        st.session_state.analysis_progress.current_step = "export_excel"
        st.session_state.analysis_progress.current_message = "Exportuji Excel"
        with progress_container:
            _render_progress_ui(st.session_state.analysis_progress, time.time() - started)

    dashboard_tables = build_dashboard_tables(result["signals"])

    if export_excel:
        path = output_dir / f"market_checker_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        ExcelExporter().export(
            path,
            result["signals"],
            result["sources"],
            result["articles"],
            dashboard_tables,
            prepare_delta_for_excel(delta_df),
        )
        st.success(f"Excel export uložen: {path}")

    result["dashboard"] = dashboard_tables
    result["delta"] = delta_df
    st.session_state.last_result = result

if st.session_state.last_result:
    result = st.session_state.last_result
    signals_df = result["signals"]
    st.write("### Warnings")
    for warning in result["warnings"]:
        st.warning(warning)

    tab_signals, tab_dashboard, tab_articles, tab_sources, tab_delta, tab_trends, tab_history = st.tabs(
        ["Signals", "Dashboard", "Articles", "Sources", "Delta", "Trends", "History"]
    )

    with tab_signals:
        display_columns = [
            "ticker",
            "market_cap_usd",
            "news_score",
            "tech_score",
            "yahoo_score",
            "raw_total_score",
            "final_total_score",
            "final_confidence",
            "data_quality_score",
            "news_confidence",
            "tech_confidence",
            "yahoo_confidence",
            "signal",
            "signal_strength",
        ]
        st.dataframe(signals_df[[column for column in display_columns if column in signals_df.columns]], width="stretch")

        ticker = st.selectbox("Detail tickeru", options=signals_df["ticker"].tolist())
        row = signals_df[signals_df["ticker"] == ticker].head(1)
        if not row.empty:
            parsed_reasons = _parse_json_list(row.iloc[0].get("reasons"))
            parsed_warnings = _parse_json_list(row.iloc[0].get("warnings"))
            st.markdown("**Reasons**")
            for reason in parsed_reasons:
                st.write(f"- {reason}")
            st.markdown("**Warnings**")
            if parsed_warnings:
                for warning in parsed_warnings:
                    st.write(f"- {warning}")
            else:
                st.write("- none")

    with tab_dashboard:
        perf = summarize_performance(signals_df)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Tickery", len(signals_df))
        c2.metric("Průměrný FinalTotalScore", round(perf["avg_total_score"], 2))
        c3.metric("BUY + STRONG BUY", int(perf["count_buy"]))
        c4.metric("SELL + STRONG SELL", int(perf["count_sell"]))

        st.subheader("Top 20 by FinalTotalScore")
        st.dataframe(result["dashboard"]["top_total"], width="stretch")
        st.subheader("Top 20 weekly drops")
        st.dataframe(result["dashboard"]["weekly_drops"], width="stretch")
        st.subheader("Top 20 1M drops")
        st.dataframe(result["dashboard"]["m1_drops"], width="stretch")
        st.subheader("Top 20 3M drops")
        st.dataframe(result["dashboard"]["m3_drops"], width="stretch")
        st.subheader("Top 20 by MarketCap")
        st.dataframe(result["dashboard"]["top_marketcap"], width="stretch")
        st.subheader("Bottom 20 by MarketCap")
        st.dataframe(result["dashboard"]["bottom_marketcap"], width="stretch")

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
                st.dataframe(trends["top_delta"], width="stretch")

    with tab_history:
        if save_history:
            hs = HistoryService(store)
            tickers = hs.list_tickers()
            if tickers:
                ticker = st.selectbox("Ticker history", tickers)
                hist = hs.load_ticker_history(ticker)
                st.line_chart(hist.set_index("finished_at")["final_total_score"])
                st.dataframe(hist, width="stretch")
