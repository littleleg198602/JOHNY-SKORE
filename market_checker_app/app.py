from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from market_checker_app.analysis.performance import summarize_performance
from market_checker_app.collectors.mt5_client import MT5Client
from market_checker_app.config import AppConfig, DEFAULT_DB_PATH, DEFAULT_OUTPUT_DIR
from market_checker_app.exporters.dashboard_builder import build_dashboard_tables
from market_checker_app.exporters.delta_builder import prepare_delta_for_excel
from market_checker_app.exporters.excel_exporter import ExcelExporter
from market_checker_app.services.history_service import HistoryService
from market_checker_app.services.pipeline_service import PipelineService
from market_checker_app.storage.sqlite_store import SQLiteStore

st.set_page_config(page_title="Market Checker (interní analytika)", layout="wide")
st.title("Market Checker (interní analytika)")

if "watchlist" not in st.session_state:
    st.session_state.watchlist = []
if "last_result" not in st.session_state:
    st.session_state.last_result = None

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
        st.success(f"Načteno {len(watchlist)} symbolů.")

watchlist_text = st.text_area("Watchlist (1 ticker na řádek)", "\n".join(st.session_state.watchlist), height=120)
watchlist = MT5Client.sanitize_watchlist(watchlist_text.splitlines())
rss_sources_text = st.text_area(
    "RSS sources (1 URL na řádek)",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL&region=US&lang=en-US",
)
rss_sources = [s.strip() for s in rss_sources_text.splitlines() if s.strip()]

if run_analysis:
    pipeline = PipelineService(config)
    result = pipeline.run(watchlist=watchlist, rss_sources=rss_sources, store=store if save_history else None)
    signals = result["signals"]
    dashboard_tables = build_dashboard_tables(signals)
    delta_df = pd.DataFrame()

    if compare_prev and save_history and result.get("run_id"):
        history_service = HistoryService(store)
        delta_df = history_service.build_delta_against_previous(int(result["run_id"]))

    excel_path = ""
    if export_excel:
        excel_name = f"market_checker_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        excel_file = output_dir / excel_name
        excel_delta = prepare_delta_for_excel(delta_df)
        ExcelExporter().export(
            output_path=excel_file,
            signals=signals,
            sources=result["sources"],
            articles=result["articles"],
            dashboard=dashboard_tables,
            delta=excel_delta,
        )
        excel_path = str(excel_file)
        st.success(f"Excel export uložen: {excel_file}")

    st.session_state.last_result = {
        **result,
        "dashboard": dashboard_tables,
        "delta": delta_df,
        "excel_path": excel_path,
    }

result = st.session_state.last_result

tab_signals, tab_dashboard, tab_articles, tab_sources, tab_delta, tab_trends, tab_history = st.tabs(
    ["Signals", "Dashboard", "Articles", "Sources", "Delta", "Trends", "History"]
)

if result is None:
    st.info("Spusť analýzu pro zobrazení výsledků.")
else:
    signals_df: pd.DataFrame = result["signals"]

    with tab_signals:
        ticker_filter = st.text_input("Filtr ticker", "")
        signal_filter = st.multiselect(
            "Filtr Signal",
            options=sorted(signals_df["signal"].dropna().unique().tolist()) if not signals_df.empty else [],
        )
        score_range = st.slider("Rozsah TotalScore", 0.0, 100.0, (0.0, 100.0))
        only_with_news = st.checkbox("Pouze tickery s news", value=False)
        row_limit = st.number_input("Počet zobrazených řádků", min_value=10, max_value=5000, value=200)

        filtered = signals_df.copy()
        if ticker_filter:
            filtered = filtered[filtered["ticker"].str.contains(ticker_filter.upper(), na=False)]
        if signal_filter:
            filtered = filtered[filtered["signal"].isin(signal_filter)]
        filtered = filtered[filtered["total_score"].between(score_range[0], score_range[1])]
        if only_with_news:
            filtered = filtered[filtered["news_volume_48h"] > 0]
        st.dataframe(filtered.head(int(row_limit)), use_container_width=True)

    with tab_dashboard:
        perf = summarize_performance(signals_df)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Tickery (poslední běh)", int(len(signals_df)))
        c2.metric("Průměrný TotalScore", round(perf["avg_total_score"], 2))
        c3.metric("BUY + STRONG BUY", int(perf["count_buy"]))
        c4.metric("SELL + STRONG SELL", int(perf["count_sell"]))

        for title, key in [
            ("Top 20 by TotalScore", "top_total"),
            ("Top 20 weekly drops", "weekly_drops"),
            ("Top 20 1M drops", "m1_drops"),
            ("Top 20 3M drops", "m3_drops"),
            ("Top 20 by MarketCap", "top_marketcap"),
            ("Bottom 20 by MarketCap", "bottom_marketcap"),
        ]:
            st.subheader(title)
            st.dataframe(result["dashboard"][key], use_container_width=True)

    with tab_articles:
        st.dataframe(result["articles"], use_container_width=True)

    with tab_sources:
        st.dataframe(result["sources"], use_container_width=True)

    with tab_delta:
        delta_df = result["delta"]
        if delta_df.empty:
            st.info("Delta proti minulému běhu zatím není dostupná.")
        else:
            st.dataframe(delta_df, use_container_width=True)

    with tab_trends:
        if save_history:
            history_service = HistoryService(store)
            trends = history_service.load_global_trends()
            if trends["avg_total"].empty:
                st.info("V SQLite historii zatím nejsou data.")
            else:
                st.subheader("Průměrný TotalScore v čase")
                st.line_chart(trends["avg_total"].set_index("finished_at")["total_score"])

                st.subheader("Počty signalů podle běhů")
                signal_counts = trends["signal_counts"].pivot(index="run_id", columns="signal", values="size").fillna(0)
                st.bar_chart(signal_counts)

                st.subheader("Top 10 změn TotalScore vs předchozí běh")
                st.dataframe(trends["top_delta"], use_container_width=True)

                st.subheader("Distribuce TotalScore v posledním běhu")
                if not trends["latest_distribution"].empty:
                    st.bar_chart(trends["latest_distribution"].set_index("bucket")["count"])
        else:
            st.warning("SQLite historie je vypnutá.")

    with tab_history:
        if not save_history:
            st.warning("Pro History tab zapni ukládání do SQLite.")
        else:
            history_service = HistoryService(store)
            candidates = sorted(signals_df["ticker"].dropna().unique().tolist())
            ticker = st.selectbox("Ticker", options=candidates if candidates else [""])
            if ticker:
                ticker_hist = history_service.load_ticker_history(ticker)
                if ticker_hist.empty:
                    st.info("Historie tickeru není dostupná.")
                else:
                    ticker_hist["finished_at"] = pd.to_datetime(ticker_hist["finished_at"])
                    st.subheader("TotalScore v čase")
                    st.line_chart(ticker_hist.set_index("finished_at")["total_score"])
                    st.subheader("News / Tech / Yahoo score v čase")
                    st.line_chart(ticker_hist.set_index("finished_at")[["news_score", "tech_score", "yahoo_score"]])
                    st.subheader("Signal změny")
                    st.dataframe(ticker_hist[["run_id", "finished_at", "signal", "total_score"]], use_container_width=True)
                    st.subheader("Plná historie tickeru")
                    st.dataframe(ticker_hist, use_container_width=True)
