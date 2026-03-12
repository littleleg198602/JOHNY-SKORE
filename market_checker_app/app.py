from __future__ import annotations

from pathlib import Path
import sys

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import json
import time

import altair as alt
import pandas as pd
import streamlit as st

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
from market_checker_app.services.visualization_service import VisualizationService
from market_checker_app.storage.sqlite_store import SQLiteStore
from market_checker_app.utils.charts import (
    histogram_chart,
    line_chart,
    multi_line_chart,
    scatter_score_confidence,
    signal_bar_chart,
    top_bottom_bar_chart,
)


MAX_PREVIEW_ROWS = 500


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


def _show_limited_dataframe(df: pd.DataFrame, title: str, preferred_cols: list[str] | None = None, rows: int = MAX_PREVIEW_ROWS) -> None:
    st.subheader(title)
    if df.empty:
        st.info("Data nejsou dostupná.")
        return
    view = df.copy()
    if preferred_cols:
        cols = [c for c in preferred_cols if c in view.columns]
        if cols:
            view = view[cols]
    if len(view) > rows:
        st.warning(f"Zobrazuji prvních {rows} řádků z {len(view)} kvůli výkonu UI.")
        view = view.head(rows)
    st.dataframe(view, width="stretch")


def _render_detail_ticker(signals_df: pd.DataFrame, ticker: str) -> None:
    row = signals_df[signals_df["ticker"] == ticker].head(1)
    if row.empty:
        st.info("Detail tickeru není dostupný.")
        return

    score_df = VisualizationService.prepare_score_decomposition_df(signals_df, ticker)
    conf_df = VisualizationService.prepare_confidence_decomposition_df(signals_df, ticker)

    col1, col2 = st.columns(2)
    with col1:
        st.altair_chart(
            alt.Chart(score_df)
            .mark_bar()
            .encode(x=alt.X("modul:N", title="Modul"), y=alt.Y("hodnota:Q", title="Skóre"), tooltip=["modul:N", alt.Tooltip("hodnota:Q", format=".2f")])
            .properties(title="Rozklad skóre", height=280),
            width="stretch",
        )
    with col2:
        st.altair_chart(
            alt.Chart(conf_df)
            .mark_bar(color="#1f77b4")
            .encode(x=alt.X("modul:N", title="Modul"), y=alt.Y("hodnota:Q", title="Confidence"), tooltip=["modul:N", alt.Tooltip("hodnota:Q", format=".2f")])
            .properties(title="Rozklad confidence", height=280),
            width="stretch",
        )

    st.markdown("### Shrnutí tickeru")
    st.write(f"**OverallSummary:** {row.iloc[0].get('overall_summary', '')}")
    st.write(f"**RiskScore:** {float(row.iloc[0].get('risk_score', 0)):.2f}")

    for label, key in [("KeyDrivers", "key_drivers"), ("Warnings", "warnings"), ("Reasons", "reasons")]:
        st.markdown(f"**{label}**")
        values = _parse_json_list(row.iloc[0].get(key))
        if not values:
            st.caption("Bez záznamu")
        for item in values:
            st.write(f"- {item}")


def _render_dashboard(signals_df: pd.DataFrame, ranking_tables: dict[str, pd.DataFrame]) -> None:
    if signals_df.empty:
        st.info("Dashboard zatím nemá data. Spusťte analýzu.")
        return

    st.markdown("## Dashboard")
    kpi = VisualizationService.prepare_kpi(signals_df)
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Počet tickerů", kpi["tickers"])
    c2.metric("Průměrný FinalTotalScore", f"{kpi['avg_score']:.2f}")
    c3.metric("Průměrný FinalConfidence", f"{kpi['avg_confidence']:.2f}")
    c4.metric("Průměrný RiskScore", f"{kpi['avg_risk']:.2f}")
    c5.metric("BUY + STRONG BUY", kpi["buy_count"])
    c6.metric("SELL + STRONG SELL", kpi["sell_count"])

    signals = sorted(signals_df["signal"].dropna().unique().tolist()) if "signal" in signals_df.columns else []
    filter_col1, filter_col2, filter_col3 = st.columns(3)
    with filter_col1:
        selected_signals = st.multiselect("Filtr signálu", options=signals, default=signals)
    with filter_col2:
        confidence_range = st.slider("Rozsah confidence", 0, 100, (0, 100))
    with filter_col3:
        risk_range = st.slider("Rozsah risk", 0, 100, (0, 100))

    filtered = signals_df.copy()
    if selected_signals:
        filtered = filtered[filtered["signal"].isin(selected_signals)]
    filtered = filtered[(pd.to_numeric(filtered["final_confidence"], errors="coerce").between(confidence_range[0], confidence_range[1])) & (pd.to_numeric(filtered["risk_score"], errors="coerce").between(risk_range[0], risk_range[1]))]

    signal_df = VisualizationService.prepare_signal_distribution_df(filtered)
    strong_buy_count = int(signal_df.loc[signal_df["signal"] == "STRONG BUY", "count"].sum())
    strong_sell_count = int(signal_df.loc[signal_df["signal"] == "STRONG SELL", "count"].sum())
    if strong_buy_count == 0 or strong_sell_count == 0:
        st.warning(
            f"Diagnostika signálů: STRONG BUY={strong_buy_count}, STRONG SELL={strong_sell_count}. "
            "To nemusí být chyba – při aktuálním rozložení score a risku se extrémní signály nemusí objevit."
        )

    score_hist = VisualizationService.prepare_histogram_df(filtered, "final_total_score")
    conf_hist = VisualizationService.prepare_histogram_df(filtered, "final_confidence")
    risk_hist = VisualizationService.prepare_histogram_df(filtered, "risk_score")
    scatter_df = VisualizationService.prepare_scatter_df(filtered)
    top10, bottom10 = VisualizationService.prepare_top_bottom_df(filtered, "final_total_score", n=10)

    col1, col2 = st.columns(2)
    with col1:
        st.altair_chart(signal_bar_chart(signal_df, "Distribuce signálů"), width="stretch")
    with col2:
        st.altair_chart(histogram_chart(score_hist, "Rozložení FinalTotalScore", "Bucket skóre"), width="stretch")

    col3, col4 = st.columns(2)
    with col3:
        st.altair_chart(histogram_chart(conf_hist, "Rozložení FinalConfidence", "Bucket confidence"), width="stretch")
    with col4:
        st.altair_chart(histogram_chart(risk_hist, "Rozložení RiskScore", "Bucket risk"), width="stretch")

    st.altair_chart(scatter_score_confidence(scatter_df, "Confidence vs Score (tooltip + velikost dle MarketCap)"), width="stretch")

    ctop, cbottom = st.columns(2)
    with ctop:
        st.altair_chart(top_bottom_bar_chart(top10, "final_total_score", "Top 10 tickerů podle FinalTotalScore", positive_color="#2ca02c"), width="stretch")
        st.dataframe(top10, width="stretch")
    with cbottom:
        st.altair_chart(top_bottom_bar_chart(bottom10, "final_total_score", "Bottom 10 tickerů podle FinalTotalScore", positive_color="#2ca02c", negative_color="#d62728"), width="stretch")
        st.dataframe(bottom10, width="stretch")

    with st.expander("Top/Bottom rank overview", expanded=False):
        st.dataframe(ranking_tables.get("top", pd.DataFrame()).head(20), width="stretch")
        st.dataframe(ranking_tables.get("bottom", pd.DataFrame()).head(20), width="stretch")


def _render_delta(delta_df: pd.DataFrame) -> None:
    st.markdown("## Delta vůči předchozímu běhu")
    if delta_df.empty:
        st.info("Delta není dostupná, protože chybí předchozí běh.")
        return

    improvements, declines = VisualizationService.prepare_delta_top_movers_df(delta_df, n=10)
    transitions = VisualizationService.prepare_signal_transition_df(delta_df)
    comp_delta = VisualizationService.prepare_component_delta_df(delta_df, n=12)

    col1, col2 = st.columns(2)
    with col1:
        st.altair_chart(top_bottom_bar_chart(improvements, "DeltaTotal", "Top 10 zlepšení FinalTotalScore", positive_color="#2ca02c"), width="stretch")
        st.dataframe(improvements.head(10), width="stretch")
    with col2:
        st.altair_chart(top_bottom_bar_chart(declines, "DeltaTotal", "Top 10 propadů FinalTotalScore", positive_color="#2ca02c", negative_color="#d62728"), width="stretch")
        st.dataframe(declines.head(10), width="stretch")

    st.subheader("Přechody signálů")
    if transitions.empty:
        st.info("Přechody signálů nejsou dostupné.")
    else:
        st.altair_chart(
            alt.Chart(transitions.head(20))
            .mark_bar()
            .encode(
                y=alt.Y("SignalChange:N", sort="-x", title="Přechod"),
                x=alt.X("count:Q", title="Počet tickerů"),
                tooltip=["SignalChange:N", "count:Q"],
            )
            .properties(height=360, title="Nejčastější přechody signálů"),
            width="stretch",
        )
        st.dataframe(transitions, width="stretch")

    st.subheader("Delta komponent pro největší movery")
    if comp_delta.empty:
        st.info("Component delta není dostupná pro aktuální data.")
    else:
        st.altair_chart(
            alt.Chart(comp_delta)
            .mark_bar()
            .encode(
                x=alt.X("ticker:N", title="Ticker"),
                y=alt.Y("delta:Q", title="Delta"),
                color=alt.Color("component:N", title="Komponenta"),
                tooltip=["ticker:N", "component:N", alt.Tooltip("delta:Q", format=".2f")],
            )
            .properties(height=360, title="Z čeho se skládá změna skóre (top movers)"),
            width="stretch",
        )


def _render_trends(history_service: HistoryService) -> None:
    st.markdown("## Trends napříč běhy")
    global_history = history_service.store.read_global_history()
    trend = VisualizationService.prepare_trend_history_df(global_history)

    if trend["avg_scores"].empty:
        st.info("Zatím není dost historických běhů pro graf.")
        return

    avg_scores = trend["avg_scores"]
    col1, col2, col3 = st.columns(3)
    with col1:
        st.altair_chart(line_chart(avg_scores, "finished_at", "avg_final_total_score", "Průměrný FinalTotalScore v čase"), width="stretch")
    with col2:
        st.altair_chart(line_chart(avg_scores, "finished_at", "avg_final_confidence", "Průměrný FinalConfidence v čase"), width="stretch")
    with col3:
        st.altair_chart(line_chart(avg_scores, "finished_at", "avg_risk_score", "Průměrný RiskScore v čase"), width="stretch")

    signal_counts = trend["signal_counts"]
    if not signal_counts.empty:
        st.altair_chart(
            alt.Chart(signal_counts)
            .mark_bar()
            .encode(
                x=alt.X("finished_at:T", title="Běh"),
                y=alt.Y("count:Q", title="Počet tickerů"),
                color=alt.Color("signal:N", title="Signál"),
                tooltip=["signal:N", "count:Q", "finished_at:T"],
            )
            .properties(height=320, title="Vývoj počtu signálů v čase"),
            width="stretch",
        )

    module_scores = trend["module_scores"]
    if not module_scores.empty:
        melt_cols = [c for c in module_scores.columns if c.startswith("avg_")]
        melted = module_scores.melt(id_vars=["finished_at"], value_vars=melt_cols, var_name="module", value_name="score")
        st.altair_chart(multi_line_chart(melted, "Průměrná skóre modulů v čase"), width="stretch")

    bucket_behavior = trend["bucket_behavior"]
    if not bucket_behavior.empty:
        st.altair_chart(
            alt.Chart(bucket_behavior)
            .mark_line(point=True)
            .encode(
                x=alt.X("finished_at:T", title="Běh"),
                y=alt.Y("final_total_score:Q", title="Průměrný FinalTotalScore"),
                color=alt.Color("bucket:N", title="Bucket"),
                tooltip=["bucket:N", alt.Tooltip("final_total_score:Q", format=".2f"), "finished_at:T"],
            )
            .properties(height=300, title="Top 20 % vs Bottom 20 % v čase"),
            width="stretch",
        )


def _render_history(history_service: HistoryService) -> None:
    st.markdown("## History tickeru")
    tickers = history_service.list_tickers()
    if not tickers:
        st.info("Pro vybraný ticker zatím není dostatek historických dat.")
        return

    ticker = st.selectbox("Vyber ticker pro historii", tickers)
    hist = history_service.load_ticker_history(ticker)
    prepared = VisualizationService.prepare_ticker_history_df(hist)

    if prepared["series"].empty:
        st.info("Pro vybraný ticker zatím není dostatek historických dat.")
        return

    series = prepared["series"]
    col1, col2, col3 = st.columns(3)
    with col1:
        st.altair_chart(line_chart(series, "finished_at", "final_total_score", "FinalTotalScore v čase"), width="stretch")
    with col2:
        st.altair_chart(line_chart(series, "finished_at", "final_confidence", "FinalConfidence v čase"), width="stretch")
    with col3:
        st.altair_chart(line_chart(series, "finished_at", "risk_score", "RiskScore v čase"), width="stretch")

    col4, col5 = st.columns(2)
    with col4:
        if "rank_in_watchlist" in series.columns:
            st.altair_chart(line_chart(series, "finished_at", "rank_in_watchlist", "Rank v čase (nižší je lepší)"), width="stretch")
    with col5:
        if "percentile_in_watchlist" in series.columns:
            st.altair_chart(line_chart(series, "finished_at", "percentile_in_watchlist", "Percentil v čase"), width="stretch")

    module_series = prepared["module_series"]
    if not module_series.empty:
        st.altair_chart(multi_line_chart(module_series, "Skóre modulů v čase"), width="stretch")

    st.subheader("Signálová historie")
    st.dataframe(prepared["table"], width="stretch")

    st.subheader("Poslední běhy tickeru (detail tabulka)")
    st.dataframe(series.sort_values("finished_at", ascending=False).head(20), width="stretch")

    snap = prepared["last_snapshot"]
    if not snap.empty:
        row = snap.iloc[0]
        st.markdown("### Poslední snapshot")
        st.write(f"**OverallSummary:** {row.get('overall_summary', '')}")
        for label, key in [("KeyDrivers", "key_drivers"), ("Warnings", "warnings"), ("Reasons", "reasons")]:
            st.markdown(f"**{label}**")
            values = _parse_json_list(row.get(key))
            if not values:
                st.caption("Bez záznamu")
            for item in values:
                st.write(f"- {item}")


def _render_signals(signals_df: pd.DataFrame) -> None:
    signal_filter = st.multiselect("Signal filter", options=sorted(signals_df["signal"].dropna().unique()), default=sorted(signals_df["signal"].dropna().unique()))
    regime_filter = st.multiselect("Regime filter", options=sorted(signals_df["regime"].dropna().unique()), default=sorted(signals_df["regime"].dropna().unique()))
    min_conf = st.slider("Min confidence", 0, 100, 0)
    max_risk = st.slider("Max risk", 0, 100, 100)
    filtered = signals_df[(signals_df["signal"].isin(signal_filter)) & (signals_df["regime"].isin(regime_filter)) & (pd.to_numeric(signals_df["final_confidence"], errors="coerce") >= min_conf) & (pd.to_numeric(signals_df["risk_score"], errors="coerce") <= max_risk)]

    display_columns = [
        "ticker",
        "news_score",
        "tech_score",
        "yahoo_score",
        "behavioral_score",
        "risk_score",
        "raw_total_score",
        "quality_adjusted_score",
        "risk_adjusted_score",
        "final_total_score",
        "final_confidence",
        "signal",
        "signal_strength",
        "rank_in_watchlist",
        "percentile_in_watchlist",
        "regime",
    ]
    st.dataframe(filtered[[c for c in display_columns if c in filtered.columns]], width="stretch")

    ticker = st.selectbox("Detail tickeru", options=signals_df["ticker"].tolist())
    _render_detail_ticker(signals_df, ticker)


st.set_page_config(page_title="Market Checker", layout="wide")
st.title("Market Checker")

for key, default in {"watchlist": [], "last_result": None, "analysis_progress": None, "mt5_loaded_count": None}.items():
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
    st.metric("Tickery načtené z MT5", st.session_state.mt5_loaded_count if st.session_state.mt5_loaded_count is not None else 0)
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
        st.session_state.mt5_loaded_count = len(watchlist)

watchlist_text = st.text_area("Watchlist (1 ticker na řádek)", "\n".join(st.session_state.watchlist), height=130)
watchlist = MT5Client.sanitize_watchlist(watchlist_text.splitlines())
if st.session_state.mt5_loaded_count is not None:
    st.info(f"Načteno z MT5: {st.session_state.mt5_loaded_count} tickerů")
else:
    st.info("Načteno z MT5: 0 tickerů")
st.write(f"**Aktuálně ve watchlistu:** {len(watchlist)} tickerů")
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
        _render_signals(signals_df)

    with tab_dashboard:
        _render_dashboard(signals_df, result.get("ranking", {}))
        st.markdown("### Přehledové tabulky")
        _show_limited_dataframe(result["dashboard"].get("top_total", pd.DataFrame()), "Top 20 by FinalTotalScore")
        _show_limited_dataframe(result["dashboard"].get("weekly_drops", pd.DataFrame()), "Top 20: 7denní propad")
        _show_limited_dataframe(result["dashboard"].get("d14_drops", pd.DataFrame()), "Top 20: 14denní propad")
        _show_limited_dataframe(result["dashboard"].get("m1_drops", pd.DataFrame()), "Top 20: 1M propad")
        _show_limited_dataframe(result["dashboard"].get("m3_drops", pd.DataFrame()), "Top 20: 3M propad")
        _show_limited_dataframe(result["dashboard"].get("top_marketcap", pd.DataFrame()), "Top 20 by MarketCap")

    with tab_articles:
        _show_limited_dataframe(
            result.get("articles", pd.DataFrame()),
            "Články",
            preferred_cols=["ticker", "source", "published_at", "title", "sentiment"],
            rows=1500,
        )

    with tab_sources:
        _show_limited_dataframe(result.get("sources", pd.DataFrame()), "RSS zdroje", rows=300)

    with tab_delta:
        _render_delta(result.get("delta", pd.DataFrame()))

    with tab_trends:
        if save_history:
            _render_trends(HistoryService(store))
        else:
            st.info("Trendy nejsou dostupné, protože je vypnuto ukládání historie do SQLite.")

    with tab_history:
        if save_history:
            _render_history(HistoryService(store))
        else:
            st.info("Historie není dostupná, protože je vypnuto ukládání historie do SQLite.")

    with tab_ranking:
        st.subheader("Top ranking")
        st.dataframe(result["ranking"].get("top", pd.DataFrame()), width="stretch")
        st.subheader("Bottom ranking")
        st.dataframe(result["ranking"].get("bottom", pd.DataFrame()), width="stretch")
        if save_history:
            eval_frames = EvaluationService().evaluate_snapshots(store.read_global_history())
            st.subheader("Evaluation / backtest")
            for name, frame in eval_frames.items():
                st.write(name)
                st.dataframe(frame, width="stretch")
