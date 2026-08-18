from __future__ import annotations

from pathlib import Path
import re
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

from market_checker_app.analysis.scoring import validate_decision_scenarios
from market_checker_app.collectors.mt5_client import MT5Client
from market_checker_app.config import AppConfig, DEFAULT_DB_PATH, DEFAULT_OUTPUT_DIR, SignalThresholds
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
from market_checker_app.services.yahoo_enrichment_service import YahooEnrichmentService
from market_checker_app.storage.sqlite_store import SQLiteStore
from market_checker_app.storage.yahoo_cache_store import YahooCacheCoverage, YahooCacheStore
from market_checker_app.utils.charts import (
    histogram_chart,
    line_chart,
    multi_line_chart,
    scatter_score_confidence,
    signal_bar_chart,
    top_bottom_bar_chart,
)


MAX_PREVIEW_ROWS = 500
DEFAULT_NEWS_SOURCES_TEXT = "\n".join(
    [
        "https://news.google.com/rss/search?q={ticker}%20stock&hl=en-US&gl=US&ceid=US:en",
        "https://www.nasdaq.com/feed/rssoutbound",
        "https://www.marketscreener.com/rss/news/",
        "https://www.investing.com/rss/news.rss",
        "https://www.benzinga.com/feed",
    ]
)


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


def _resolve_sqlite_path(raw_value: str) -> tuple[Path, str | None]:
    """
    Normalize user-provided SQLite path from UI and try to recover common typos.

    Returns:
        tuple[path, info_message]
        info_message is shown to user when path was auto-corrected.
    """
    raw = (raw_value or "").strip().strip('"').strip("'")
    candidate = Path(raw.replace("\\", "/")) if raw else DEFAULT_DB_PATH

    if candidate.suffix.lower() != ".db":
        candidate = candidate.with_suffix(".db")

    if candidate.exists():
        return candidate, None

    fallback = candidate.parent / "market_checker_history.db"
    if candidate.name != "market_checker_history.db" and fallback.exists():
        return fallback, f"DB soubor `{candidate}` nebyl nalezen, používám `{fallback}`."

    return candidate, None




def _load_yahoo_tickers_from_excel(uploaded_file: object) -> tuple[list[str], str | None]:
    if uploaded_file is None:
        return [], None
    try:
        frame = pd.read_excel(uploaded_file)
    except Exception as exc:
        return [], f"Excel se nepodařilo načíst: {exc}"

    if frame.empty:
        return [], "Excel je prázdný."

    normalized = {str(c).strip().lower(): c for c in frame.columns}
    ticker_col = normalized.get('yahoo ticker') or normalized.get('yahoo_ticker') or normalized.get('ticker')
    if ticker_col is None:
        return [], "Excel musí obsahovat sloupec 'Yahoo ticker' (nebo 'ticker')."

    values = frame[ticker_col].dropna().astype(str).str.strip()
    cleaned = [v for v in values.tolist() if v and v.lower() != 'nan']
    return MT5Client.sanitize_watchlist(cleaned), None

def _parse_finished_at_from_excel_name(path: Path) -> pd.Timestamp | None:
    match = re.search(r"market_checker_(\d{8}_\d{6})\.xlsx$", path.name)
    if not match:
        return None
    return pd.to_datetime(match.group(1), format="%Y%m%d_%H%M%S", errors="coerce")


@st.cache_data(show_spinner=False)
def _load_history_from_excels(output_dir_value: str) -> pd.DataFrame:
    output_dir = Path(output_dir_value)
    files = sorted(output_dir.glob("market_checker_*.xlsx"))
    if not files:
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    for idx, path in enumerate(files, start=1):
        finished_at = _parse_finished_at_from_excel_name(path)
        if finished_at is None or pd.isna(finished_at):
            continue
        try:
            frame = pd.read_excel(path, sheet_name="Signals")
        except Exception:
            continue
        if frame.empty or "ticker" not in frame.columns:
            continue
        frame = frame.copy()
        frame["run_id"] = idx
        frame["finished_at"] = finished_at
        keep_cols = [
            "run_id",
            "finished_at",
            "ticker",
            "current_price",
            "scoring_version",
            "legacy_total_score",
            "legacy_signal",
            "final_total_score",
            "raw_total_score",
            "news_score",
            "tech_score",
            "yahoo_score",
            "behavioral_score",
            "risk_score",
            "rank_in_watchlist",
            "percentile_in_watchlist",
            "signal",
            "final_confidence",
            "tech_source_used",
            "reasons",
            "warnings",
            "risk_flags",
            "key_drivers",
            "overall_summary",
        ]
        frames.append(frame[[c for c in keep_cols if c in frame.columns]])

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values(["run_id", "ticker"], ascending=[True, True])


def _render_progress_ui(state: AnalysisProgressState, elapsed_sec: float) -> None:
    st.write(f"**{state.current_message}**")
    if state.current_symbol:
        st.caption(
            f"Ticker {state.current_symbol} • {state.current_position}/{state.total_symbols}"
        )
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


def _render_dashboard(signals_df: pd.DataFrame, ranking_tables: dict[str, pd.DataFrame], dashboard_tables: dict[str, pd.DataFrame]) -> None:
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
    c5.metric("Akce BUY", kpi["buy_count"])
    c6.metric("Akce SELL", kpi["sell_count"])

    st.markdown("### Diagnostika rozhodovacího enginu")
    bull_series = pd.to_numeric(signals_df.get("bull_score", pd.Series(dtype=float)), errors="coerce")
    bear_series = pd.to_numeric(signals_df.get("bear_score", pd.Series(dtype=float)), errors="coerce")
    spread_series = pd.to_numeric(signals_df.get("bull_bear_spread", pd.Series(dtype=float)), errors="coerce")
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Bull score min/max", f"{bull_series.min():.1f} / {bull_series.max():.1f}" if not bull_series.dropna().empty else "n/a")
    d2.metric("Bear score min/max", f"{bear_series.min():.1f} / {bear_series.max():.1f}" if not bear_series.dropna().empty else "n/a")
    d3.metric("Spread min/max", f"{spread_series.min():.1f} / {spread_series.max():.1f}" if not spread_series.dropna().empty else "n/a")
    d4.metric("Signal downgrady", int(pd.to_numeric(signals_df.get("downgrade_count", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()))

    app_cfg = AppConfig()
    scenario_rows = validate_decision_scenarios(app_cfg.decision_weights, app_cfg.decision_thresholds)
    scenario_df = pd.DataFrame(scenario_rows)
    st.dataframe(scenario_df, width="stretch")

    blocked_series = signals_df.get("blocked_reasons")
    if blocked_series is not None:
        blocked_items: list[str] = []
        for value in blocked_series.dropna().tolist():
            blocked_items.extend(_parse_json_list(value))
        if blocked_items:
            blocked_df = pd.Series(blocked_items).value_counts().reset_index()
            blocked_df.columns = ["důvod_blokace", "count"]
            st.dataframe(blocked_df, width="stretch")

    st.markdown("### Kalibrace HOLD (analýza, bez změny produkčních thresholdů)")
    calibration = VisualizationService.prepare_hold_calibration(signals_df)
    hold_diag_df = calibration["hold_diagnostics"]
    hold_concentration_df = calibration["hold_concentration"]
    sensitivity_df = calibration["sensitivity_distribution"]
    confidence_sanity = calibration["confidence_sanity"]
    tech_effectiveness = calibration["technical_driver_effectiveness"]

    if hold_diag_df.empty:
        st.info("HOLD diagnostika není dostupná.")
    else:
        st.caption("Top HOLD tickery (bull/bear spread, confidence, primary driver, směry modulů, blokace).")
        st.dataframe(hold_diag_df.head(25), width="stretch")

    c_hold1, c_hold2, c_hold3 = st.columns(3)
    c_hold1.metric("HOLD count", int(confidence_sanity.get("hold_count", 0)))
    c_hold2.metric("High-confidence HOLD", int(confidence_sanity.get("high_conf_hold_count", 0)))
    c_hold3.metric("High-confidence HOLD ratio", f"{float(confidence_sanity.get('high_conf_hold_ratio', 0.0)):.2%}")
    st.caption(str(confidence_sanity.get("explanation", "")))

    cc1, cc2 = st.columns(2)
    with cc1:
        st.subheader("HOLD concentration podle primární příčiny")
        st.dataframe(hold_concentration_df, width="stretch")
    with cc2:
        st.subheader("Sensitivity simulace hold-band")
        if sensitivity_df.empty:
            st.info("Sensitivity simulace není dostupná.")
        else:
            st.altair_chart(
                alt.Chart(sensitivity_df)
                .mark_bar()
                .encode(
                    x=alt.X("scenario:N", title="Simulace"),
                    y=alt.Y("count:Q", title="Počet"),
                    color=alt.Color("signal:N", title="Signál"),
                    tooltip=["scenario:N", "signal:N", "count:Q"],
                )
                .properties(height=280),
                width="stretch",
            )
            pivot = sensitivity_df.pivot(index="scenario", columns="signal", values="count").fillna(0).reset_index()
            st.dataframe(pivot, width="stretch")

    st.subheader("Technical-driver effectiveness (HOLD trap)")
    st.metric(
        "Strong technical states trapped in HOLD",
        f"{int(tech_effectiveness.get('strong_technical_hold_count', 0))} / {int(tech_effectiveness.get('hold_count', 0))}",
    )
    st.caption(f"Podíl: {float(tech_effectiveness.get('strong_technical_hold_ratio', 0.0)):.2%}")
    examples_df = tech_effectiveness.get("examples", pd.DataFrame())
    if isinstance(examples_df, pd.DataFrame) and not examples_df.empty:
        st.dataframe(examples_df.head(25), width="stretch")
    else:
        st.caption("Nenalezeny výrazné technické stavy, které by končily HOLD.")

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
    thresholds = SignalThresholds()
    score_series = pd.to_numeric(filtered.get("final_total_score", pd.Series(dtype=float)), errors="coerce")
    observed_min = float(score_series.min()) if not score_series.dropna().empty else 0.0
    observed_max = float(score_series.max()) if not score_series.dropna().empty else 0.0
    if observed_max < thresholds.strong_buy or observed_min > thresholds.sell:
        st.warning(
            f"Dosažitelnost hranic: min={observed_min:.2f}, max={observed_max:.2f}, "
            f"STRONG BUY >= {thresholds.strong_buy}, STRONG SELL < {thresholds.sell}. "
            "Pokud jsou hranice mimo rozsah score, extrémy se neobjeví."
        )

    decision_series = signals_df.get("decision_signal", signals_df.get("signal", pd.Series(dtype=str)))
    strong_buy_count = int(decision_series.eq("STRONG BUY").sum())
    strong_sell_count = int(decision_series.eq("STRONG SELL").sum())
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

    overlap = VisualizationService.prepare_drop_overlap_tables(dashboard_tables)
    shared = overlap.get("shared_drop_tickers", pd.DataFrame())
    st.subheader("Stejné tickery napříč propady")
    if shared.empty:
        st.info("Žádný ticker se neopakuje napříč 7D/14D/1M/3M propady.")
    else:
        st.dataframe(shared, width="stretch")

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

    st.subheader("Detailní delta tabulka (side-by-side)")
    mode = st.radio(
        "Zobrazení",
        options=["Vše", "Jen propady (DeltaTotal < 0)"],
        horizontal=True,
    )
    preferred_cols = [
        "ticker",
        "market_cap_usd_prev",
        "market_cap_usd",
        "DeltaMarketCap",
        "final_total_score_prev",
        "final_total_score",
        "DeltaTotal",
        "rank_in_watchlist_prev",
        "rank_in_watchlist",
        "DeltaRank",
        "final_confidence_prev",
        "final_confidence",
        "DeltaConfidence",
        "signal_prev",
        "signal",
        "SignalChange",
        "DeltaNews",
        "DeltaTech",
        "DeltaYahoo",
        "DeltaBehavioral",
        "DeltaRisk",
    ]
    detail_cols = [c for c in preferred_cols if c in delta_df.columns]
    detail = delta_df[detail_cols].copy() if detail_cols else delta_df.copy()
    if mode.startswith("Jen propady") and "DeltaTotal" in detail.columns:
        detail = detail[detail["DeltaTotal"] < 0]
    if "DeltaTotal" in detail.columns:
        detail = detail.sort_values("DeltaTotal", ascending=True)
    st.caption("Pozn.: DeltaRank > 0 = zlepšení pořadí (nižší rank je lepší).")
    delta_cols = [c for c in ["DeltaTotal", "DeltaRank", "DeltaConfidence", "DeltaNews", "DeltaTech", "DeltaYahoo", "DeltaBehavioral", "DeltaRisk", "DeltaMarketCap"] if c in detail.columns]
    styled = (
        detail.head(500)
        .style.format(
            {
                "DeltaTotal": "{:+.2f}",
                "DeltaRank": "{:+.0f}",
                "DeltaConfidence": "{:+.2f}",
                "DeltaNews": "{:+.2f}",
                "DeltaTech": "{:+.2f}",
                "DeltaYahoo": "{:+.2f}",
                "DeltaBehavioral": "{:+.2f}",
                "DeltaRisk": "{:+.2f}",
                "DeltaMarketCap": "{:+,.0f}",
                "market_cap_usd_prev": "{:,.0f}",
                "market_cap_usd": "{:,.0f}",
            },
            na_rep="-",
        )
        .map(lambda v: "background-color: rgba(40,167,69,0.25)" if isinstance(v, (int, float)) and v > 0 else ("background-color: rgba(220,53,69,0.25)" if isinstance(v, (int, float)) and v < 0 else ""), subset=delta_cols)
    )
    st.dataframe(styled, width="stretch")


def _render_trends(history_service: HistoryService, output_dir: Path) -> None:
    st.markdown("## Trends napříč běhy")
    global_history = history_service.store.read_global_history()
    if global_history["run_id"].nunique() < 2 if not global_history.empty else True:
        excel_history = _load_history_from_excels(str(output_dir))
        if excel_history["run_id"].nunique() >= 2:
            global_history = excel_history
            st.info("SQLite zatím nemá dost běhů, trendy načítám z historických Excel exportů v outputs.")
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


def _render_history(history_service: HistoryService, output_dir: Path) -> None:
    st.markdown("## History tickeru")
    source_df = history_service.store.read_global_history()
    if source_df.empty:
        source_df = _load_history_from_excels(str(output_dir))
        if not source_df.empty:
            st.info("SQLite historie je prázdná, historii tickeru načítám z Excel exportů v outputs.")

    tickers = sorted(source_df["ticker"].dropna().astype(str).unique().tolist()) if not source_df.empty and "ticker" in source_df.columns else []
    if not tickers:
        st.info("Pro vybraný ticker zatím není dostatek historických dat.")
        return

    ticker = st.selectbox("Vyber ticker pro historii", tickers)
    hist = source_df[source_df["ticker"] == ticker].copy()
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
    action_column = "action" if "action" in signals_df.columns else "signal"
    action_options = sorted(signals_df[action_column].dropna().unique())
    signal_filter = st.multiselect("Action filter", options=action_options, default=action_options)
    regime_filter = st.multiselect("Regime filter", options=sorted(signals_df["regime"].dropna().unique()), default=sorted(signals_df["regime"].dropna().unique()))
    min_conf = st.slider("Min confidence", 0, 100, 0)
    max_risk = st.slider("Max risk", 0, 100, 100)
    filtered = signals_df[(signals_df[action_column].isin(signal_filter)) & (signals_df["regime"].isin(regime_filter)) & (pd.to_numeric(signals_df["final_confidence"], errors="coerce") >= min_conf) & (pd.to_numeric(signals_df["risk_score"], errors="coerce") <= max_risk)]

    display_columns = [
        "ticker",
        "action",
        "forecast",
        "decision_signal",
        "action_reasons",
        "yahoo_ticker",
        "yahoo_data_status",
        "news_score",
        "tech_score",
        "yahoo_score",
        "behavioral_score",
        "risk_score",
        "bull_score",
        "bear_score",
        "bull_bear_spread",
        "raw_total_score",
        "quality_adjusted_score",
        "risk_adjusted_score",
        "final_total_score",
        "final_confidence",
        "data_quality_score",
        "signal",
        "signal_strength",
        "tech_source_used",
        "rank_in_watchlist",
        "percentile_in_watchlist",
        "regime",
    ]
    st.dataframe(filtered[[c for c in display_columns if c in filtered.columns]], width="stretch")

    ticker = st.selectbox("Detail tickeru", options=signals_df["ticker"].tolist())
    _render_detail_ticker(signals_df, ticker)


st.set_page_config(page_title="Market Checker", layout="wide")
st.title("Market Checker")

for key, default in {
    "watchlist": [],
    "watchlist_text": "",
    "excel_watchlist": [],
    "last_result": None,
    "analysis_progress": None,
    "mt5_loaded_count": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

with st.sidebar:
    output_dir = Path(st.text_input("Output directory", str(DEFAULT_OUTPUT_DIR)))
    marketcap_file = st.text_input("MarketCap file", "")
    export_excel = st.checkbox("Export do Excelu", value=True)
    compare_prev = st.checkbox("Porovnat s předchozím během", value=True)
    save_history = st.checkbox("Ukládat historii do SQLite", value=True)
    sqlite_raw_input = st.text_input("DB soubor", str(DEFAULT_DB_PATH))
    max_rss = st.number_input("Max RSS items per source", min_value=1, max_value=200, value=30)
    use_rss = st.checkbox("Použít RSS zprávy", value=True)
    use_mt5 = st.checkbox("Použít MT5 pro watchlist a technická data", value=False)
    load_watchlist = st.button("Načíst watchlist z MT5", disabled=not use_mt5)
    st.metric("Tickery načtené z MT5", st.session_state.mt5_loaded_count if st.session_state.mt5_loaded_count is not None else 0)
    yahoo_batch_size = st.number_input(
        "Yahoo tickerů v jedné automatické dávce",
        min_value=1,
        max_value=1000,
        value=100,
        help=(
            "Jedno kliknutí automaticky spustí další dávky, dokud nejsou zpracované všechny "
            "aktuálně dostupné tickery nebo Yahoo nezapne ochranný rate limit."
        ),
    )
    yahoo_delay_ms = st.number_input(
        "Pauza mezi Yahoo požadavky (ms)",
        min_value=0,
        max_value=5000,
        value=750,
        step=250,
    )
    refresh_yahoo = st.button("Doplnit Yahoo cache")
    run_analysis = st.button("Spustit analýzu", type="primary")

sqlite_path, sqlite_info = _resolve_sqlite_path(sqlite_raw_input)

config = AppConfig(output_dir=output_dir, marketcap_file=marketcap_file, export_excel=export_excel, compare_previous_run=compare_prev, save_history=save_history, sqlite_path=sqlite_path, max_rss_items_per_source=int(max_rss))
config.ensure_output_dir()
store = SQLiteStore(config.sqlite_path)
yahoo_cache = YahooCacheStore(config.sqlite_path)

if sqlite_info:
    st.warning(sqlite_info)
st.caption(f"Aktivní DB: `{config.sqlite_path}`")

if load_watchlist:
    loaded_watchlist, mt5_error = MT5Client().load_watchlist()
    if mt5_error:
        st.error(mt5_error)
        st.session_state.mt5_loaded_count = 0
    else:
        st.session_state.watchlist = loaded_watchlist
        st.session_state.watchlist_text = "\n".join(loaded_watchlist)
        st.session_state.mt5_loaded_count = len(loaded_watchlist)
        st.success(f"Z MT5 načteno {len(loaded_watchlist)} tickerů.")

uploaded_excel = st.file_uploader("Excel s Yahoo tickery (XLSX)", type=["xlsx"])
if uploaded_excel is not None:
    excel_watchlist, excel_err = _load_yahoo_tickers_from_excel(uploaded_excel)
    if excel_err:
        st.error(excel_err)
        st.session_state.excel_watchlist = []
    else:
        st.session_state.excel_watchlist = excel_watchlist
        st.success(f"Načteno z Excelu: {len(excel_watchlist)} Yahoo tickerů")
else:
    st.session_state.excel_watchlist = []

watchlist_text = st.text_area("Ruční watchlist (jeden ticker na řádek)", height=130, key="watchlist_text")
mt5_watchlist = MT5Client.sanitize_watchlist(watchlist_text.splitlines())
excel_watchlist = MT5Client.sanitize_watchlist(st.session_state.excel_watchlist)
excel_mode = len(excel_watchlist) > 0

if excel_mode:
    watchlist = excel_watchlist
    yahoo_only_tickers = set(excel_watchlist) if not use_mt5 else set()
    active_sources = ["Yahoo"]
    if use_rss:
        active_sources.append("RSS")
    if use_mt5:
        active_sources.append("MT5 technika")
    st.info(f"Excel režim aktivní. Zdroje: {', '.join(active_sources)}.")
else:
    watchlist = mt5_watchlist
    yahoo_only_tickers = set()

if st.session_state.mt5_loaded_count is not None:
    st.info(f"Načteno z MT5: {st.session_state.mt5_loaded_count} tickerů")
else:
    st.info("Načteno z MT5: 0 tickerů")

st.write(f"**Aktuálně ve watchlistu:** {len(watchlist)} tickerů (Excel/Yahoo-only: {len(excel_watchlist)})")


def _render_yahoo_coverage(coverage: YahooCacheCoverage) -> None:
    if coverage.total == 0:
        st.caption("Yahoo cache: watchlist je prázdný")
        return
    st.write(
        f"**Yahoo cache:** {coverage.usable}/{coverage.total} použitelných "
        f"(fresh {coverage.fresh}, stale {coverage.stale}, failed {coverage.failed}, "
        f"pending {coverage.missing + coverage.corrupt}, unsupported {coverage.unsupported})"
    )
    st.progress(coverage.usable / coverage.total)


yahoo_coverage_placeholder = st.empty()
with yahoo_coverage_placeholder.container():
    _render_yahoo_coverage(yahoo_cache.coverage(watchlist))

if refresh_yahoo:
    if not watchlist:
        st.error("Nejdřív načtěte nebo zadejte watchlist.")
    else:
        yahoo_refresh_status = st.empty()
        yahoo_refresh_progress = st.progress(0.0)

        def _on_yahoo_refresh(
            completed: int,
            total_candidates: int,
            ticker: str,
            status: str,
            coverage: YahooCacheCoverage,
        ) -> None:
            yahoo_refresh_status.write(
                f"Yahoo metadata: {completed}/{total_candidates} • {ticker} • {status} • "
                f"celkové pokrytí {coverage.usable}/{coverage.total}"
            )
            yahoo_refresh_progress.progress(completed / max(1, total_candidates))

        try:
            refresh_result = YahooEnrichmentService(yahoo_cache).refresh_all(
                watchlist,
                batch_size=int(yahoo_batch_size),
                delay_seconds=float(yahoo_delay_ms) / 1000.0,
                progress_callback=_on_yahoo_refresh,
            )
        except Exception as exc:
            st.error(f"Doplnění Yahoo cache selhalo: {exc}")
            refresh_result = None
        if refresh_result is None:
            st.stop()
        yahoo_refresh_progress.progress(
            min(1.0, refresh_result.attempted / max(1, refresh_result.candidates))
            if refresh_result.rate_limited
            else 1.0
        )
        with yahoo_coverage_placeholder.container():
            _render_yahoo_coverage(refresh_result.coverage)
        if refresh_result.rate_limited:
            st.warning(
                "Yahoo dočasně omezilo požadavky. Dosavadní data jsou uložená; "
                "po ochranné pauze spusťte doplnění znovu."
            )
        elif refresh_result.remaining:
            st.warning(
                f"Automaticky dokončeno {refresh_result.batches} dávek: "
                f"{refresh_result.succeeded} úspěšně, {refresh_result.partial} částečně, "
                f"{refresh_result.failed} chyb. {refresh_result.remaining} tickerů čeká "
                "na pozdější opakování po chybě nebo cooldownu."
            )
        else:
            st.success(
                f"Yahoo cache je hotová: automaticky proběhlo {refresh_result.batches} dávek, "
                f"{refresh_result.succeeded} tickerů úspěšně "
                f"({refresh_result.partial} částečných dat)."
            )

if len(watchlist) > config.large_universe_threshold:
    if use_mt5:
        st.info(
            f"Velký universe režim: analyzuji všech {len(watchlist)} tickerů. "
            "Technická data poběží hromadně přes MT5 a RSS paralelně. "
            "Yahoo fundamenty a odhady analytiků se načtou z trvalé cache; "
            "chybějící tickery doplní jedno kliknutí automaticky po dávkách."
        )
    else:
        st.warning(
            f"Ve watchlistu je {len(watchlist)} tickerů, ale MT5 je vypnuté. "
            "Zapněte MT5, jinak bude technická část ve velkém universe režimu neutrální."
        )

rss_default = DEFAULT_NEWS_SOURCES_TEXT if use_rss else ""
rss_sources = [s.strip() for s in st.text_area("RSS sources", rss_default).splitlines() if s.strip()]
if use_rss:
    st.caption(
        "Tickerové zprávy používají Google News RSS bez registrace (experimentální zdroj). "
        "Nefunkční Yahoo Finance RSS není ve výchozím seznamu."
    )

if st.session_state.analysis_progress:
    _render_progress_ui(st.session_state.analysis_progress, 0.0)

if run_analysis and not watchlist:
    st.error("Watchlist je prázdný. Nahrajte Excel nebo zadejte alespoň jeden ticker.")
    run_analysis = False

if run_analysis:
    pipeline = PipelineService(config)
    previous = st.session_state.last_result["signals"] if st.session_state.last_result else pd.DataFrame()
    started = time.time()

    progress_placeholder = st.empty()

    def _on_progress(state: AnalysisProgressState) -> None:
        st.session_state.analysis_progress = state
        with progress_placeholder.container():
            _render_progress_ui(state, time.time() - started)

    try:
        result = pipeline.run(
            watchlist,
            rss_sources,
            store if save_history else None,
            progress_callback=_on_progress,
            yahoo_only_tickers=yahoo_only_tickers,
            yahoo_only_mode=False,
            rss_enabled=use_rss,
            mt5_enabled=use_mt5,
        )
    except Exception as exc:
        st.error(f"Analýza selhala: {exc}")
        st.exception(exc)
        st.stop()

    result_errors = list(result.get("errors", []))
    result_warnings = list(result.get("warnings", []))
    if result_errors:
        st.error(f"Analýza doběhla s {len(result_errors)} závažnými problémy. Výsledky mohou být fallback.")
        for message in result_errors:
            st.write(f"- {message}")
    if result_warnings:
        with st.expander(f"Upozornění z analýzy ({len(result_warnings)})", expanded=bool(result_errors)):
            for message in result_warnings[:200]:
                st.write(f"- {message}")
            if len(result_warnings) > 200:
                st.caption(f"Zobrazeno prvních 200 z {len(result_warnings)} upozornění.")
    result["configured_sources"] = pd.DataFrame({"source": rss_sources})
    delta_df = pd.DataFrame()
    if compare_prev:
        if save_history and result.get("run_id"):
            delta_df = HistoryService(store).build_delta_against_previous(int(result["run_id"]))
            if delta_df.empty:
                delta_df = HistoryService(store).build_delta_with_excel_fallback(result["signals"], output_dir)
        elif not previous.empty:
            delta_df = ComparisonService.compare_runs(result["signals"], previous)

    dashboard_tables = build_dashboard_tables(result["signals"])
    dashboard_tables.update(VisualizationService.prepare_drop_overlap_tables(dashboard_tables))
    ranking_tables = RankingService.top_bottom_tables(result["signals"])

    if export_excel:
        path = output_dir / f"market_checker_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        dashboard_export = VisualizationService.prepare_dashboard_export_payload(result["signals"], ranking_tables, dashboard_tables)
        try:
            ExcelExporter().export(path, result["signals"], result["sources"], result["articles"], dashboard_tables, prepare_delta_for_excel(delta_df), dashboard_export)
            st.success(f"Excel export uložen: {path}")
            if result.get("run_id"):
                store.update_run_excel_path(int(result["run_id"]), str(path))
        except Exception as exc:
            st.error(f"Excel export selhal: {exc}")

    result["dashboard"] = dashboard_tables
    result["ranking"] = ranking_tables
    result["delta"] = delta_df
    st.session_state.last_result = result

if st.session_state.last_result:
    result = st.session_state.last_result
    signals_df = result["signals"]

    (
        tab_signals,
        tab_dashboard,
        tab_articles,
        tab_sources,
        tab_delta,
        tab_trends,
        tab_history,
        tab_predictions,
        tab_ranking,
    ) = st.tabs(
        [
            "Signals",
            "Dashboard",
            "Articles",
            "Sources",
            "Delta",
            "Trends",
            "History",
            "Predikce",
            "Ranking",
        ]
    )

    with tab_signals:
        _render_signals(signals_df)

    with tab_dashboard:
        _render_dashboard(signals_df, result.get("ranking", {}), result.get("dashboard", {}))
        st.markdown("### Přehledové tabulky")
        _show_limited_dataframe(result["dashboard"].get("top_total", pd.DataFrame()), "Top 20 by FinalTotalScore")
        _show_limited_dataframe(result["dashboard"].get("weekly_drops", pd.DataFrame()), "Top 20: 7denní propad", preferred_cols=["ticker", "last_week_change_pct", "overlap_count", "overlap_windows", "is_shared_drop", "signal", "final_total_score"])
        _show_limited_dataframe(result["dashboard"].get("d14_drops", pd.DataFrame()), "Top 20: 14denní propad", preferred_cols=["ticker", "last_14d_change_pct", "overlap_count", "overlap_windows", "is_shared_drop", "signal", "final_total_score"])
        _show_limited_dataframe(result["dashboard"].get("m1_drops", pd.DataFrame()), "Top 20: 1M propad", preferred_cols=["ticker", "last_1m_change_pct", "overlap_count", "overlap_windows", "is_shared_drop", "signal", "final_total_score"])
        _show_limited_dataframe(result["dashboard"].get("m3_drops", pd.DataFrame()), "Top 20: 3M propad", preferred_cols=["ticker", "last_3m_change_pct", "overlap_count", "overlap_windows", "is_shared_drop", "signal", "final_total_score"])
        _show_limited_dataframe(result["dashboard"].get("top_marketcap", pd.DataFrame()), "Top 20 by MarketCap")

    with tab_articles:
        _show_limited_dataframe(
            result.get("articles", pd.DataFrame()),
            "Články",
            preferred_cols=["ticker", "source", "published_at", "title", "sentiment"],
            rows=1500,
        )

    with tab_sources:
        configured_sources = result.get("configured_sources", pd.DataFrame())
        _show_limited_dataframe(configured_sources, "Nakonfigurované zdroje (přesně podle pole RSS sources)", rows=2000)

        sources_df = result.get("sources", pd.DataFrame())
        _show_limited_dataframe(sources_df, "Rozbalené zdroje použité během běhu", rows=1000)

        articles_df = result.get("articles", pd.DataFrame())
        st.subheader("Reálně nalezené články podle zdroje")
        if isinstance(articles_df, pd.DataFrame) and not articles_df.empty and "source" in articles_df.columns:
            by_source = articles_df["source"].value_counts().reset_index()
            by_source.columns = ["source", "count_articles"]
            st.dataframe(by_source, width="stretch")
        else:
            st.info("Z článků nebyly detekovány žádné zdroje (nebo nejsou dostupná data).")

    with tab_delta:
        _render_delta(result.get("delta", pd.DataFrame()))

    with tab_trends:
        if save_history:
            _render_trends(HistoryService(store), output_dir)
        else:
            st.info("Trendy nejsou dostupné, protože je vypnuto ukládání historie do SQLite.")

    with tab_history:
        if save_history:
            _render_history(HistoryService(store), output_dir)
        else:
            st.info("Historie není dostupná, protože je vypnuto ukládání historie do SQLite.")

    with tab_predictions:
        st.subheader("Vyšly minulé pondělní predikce v2.1?")
        st.caption(
            "Obchodní akce BUY/SELL se hodnotí jen podle směru a NO_TRADE se do hit rate "
            "nezapočítává. Cenová předpověď UP/DOWN/FLAT se vyhodnocuje zvlášť."
        )
        if save_history:
            hold_tolerance = st.number_input(
                "Tolerance pro skutečný pohyb FLAT (%)",
                min_value=0.0,
                max_value=10.0,
                value=2.0,
                step=0.5,
                key="prediction_hold_tolerance_pct",
                help="Např. 2 % znamená, že skutečný týdenní pohyb od -2 % do +2 % je FLAT.",
            )
            prediction_frames = EvaluationService().evaluate_predictions(
                store.read_global_history(),
                hold_tolerance_pct=float(hold_tolerance),
            )
            overall = prediction_frames["prediction_overall"]
            overall_values = (
                dict(zip(overall["metric"], overall["value"])) if not overall.empty else {}
            )
            evaluated = int(overall_values.get("evaluated_directional_trades") or 0)
            correct = int(overall_values.get("correct_directional_trades") or 0)
            wrong = int(overall_values.get("wrong_directional_trades") or 0)
            hit_rate = overall_values.get("directional_hit_rate_pct")
            coverage = overall_values.get("trade_coverage_pct")
            forecast_accuracy = overall_values.get("forecast_accuracy_pct")
            no_trade = int(overall_values.get("no_trade_predictions") or 0)
            metric_cols = st.columns(6)
            metric_cols[0].metric("Obchody", evaluated)
            metric_cols[1].metric("HIT", correct)
            metric_cols[2].metric("MISS", wrong)
            metric_cols[3].metric(
                "Directional hit rate",
                f"{float(hit_rate):.1f} %" if pd.notna(hit_rate) else "čeká na další běh",
            )
            metric_cols[4].metric(
                "Trade coverage",
                f"{float(coverage):.1f} %" if pd.notna(coverage) else "n/a",
            )
            metric_cols[5].metric(
                "Forecast accuracy",
                f"{float(forecast_accuracy):.1f} %" if pd.notna(forecast_accuracy) else "n/a",
                help=f"NO_TRADE pozorování: {no_trade}",
            )

            if evaluated == 0:
                st.info(
                    "Zatím není uzavřený žádný směrový obchod. NO_TRADE je záměrná abstence; "
                    "forecast se může vyhodnotit i bez obchodu."
                )

            st.caption(
                "Výpočet používá všechny týdny uložené ve stejné SQLite databázi. "
                "Nejnovější týden zůstává PENDING, starší výsledky HIT/MISS/NO_TRADE se nemažou. "
                "Pravděpodobné splity se před výpočtem výnosu auditovatelně upraví."
            )
            cumulative = prediction_frames["prediction_cumulative"].copy()
            weekly = prediction_frames["prediction_weekly"].copy()
            if not cumulative.empty:
                cumulative["week_start"] = pd.to_datetime(
                    cumulative["week_start"], errors="coerce"
                )
                rate_chart_data = cumulative[
                    ["week_start", "hit_rate_pct", "cumulative_hit_rate_pct"]
                ].melt(
                    id_vars="week_start",
                    var_name="series",
                    value_name="hit_rate_pct_value",
                )
                rate_chart_data["series"] = rate_chart_data["series"].map(
                    {
                        "hit_rate_pct": "Úspěšnost daného týdne",
                        "cumulative_hit_rate_pct": "Kumulativní úspěšnost",
                    }
                )
                rate_chart = (
                    alt.Chart(rate_chart_data)
                    .mark_line(point=True)
                    .encode(
                        x=alt.X("week_start:T", title="Týden predikce"),
                        y=alt.Y(
                            "hit_rate_pct_value:Q",
                            title="Úspěšnost (%)",
                            scale=alt.Scale(domain=[0, 100]),
                        ),
                        color=alt.Color("series:N", title="Řada"),
                        tooltip=[
                            alt.Tooltip("week_start:T", title="Týden"),
                            alt.Tooltip("series:N", title="Metrika"),
                            alt.Tooltip(
                                "hit_rate_pct_value:Q",
                                title="Úspěšnost (%)",
                                format=".2f",
                            ),
                        ],
                    )
                    .properties(title="Týdenní a dlouhodobý directional hit rate", height=320)
                )
                st.altair_chart(rate_chart, width="stretch")
                st.caption(
                    "Kumulativní čára je vážená všemi uskutečněnými BUY/SELL akcemi: například 100 HIT a "
                    "1 MISS znamená 99,01 %, nikoli průměr dvou týdnů."
                )

                weekly["week_start"] = pd.to_datetime(weekly["week_start"], errors="coerce")
                result_counts = weekly[["week_start", "hits", "misses"]].melt(
                    id_vars="week_start",
                    var_name="result",
                    value_name="count",
                )
                result_counts["result"] = result_counts["result"].map(
                    {"hits": "HIT", "misses": "MISS"}
                )
                count_chart = (
                    alt.Chart(result_counts)
                    .mark_bar()
                    .encode(
                        x=alt.X("week_start:T", title="Týden predikce"),
                        y=alt.Y("count:Q", title="Počet predikcí", stack=True),
                        color=alt.Color(
                            "result:N",
                            title="Výsledek",
                            scale=alt.Scale(
                                domain=["HIT", "MISS"],
                                range=["#2ca02c", "#d62728"],
                            ),
                        ),
                        tooltip=[
                            alt.Tooltip("week_start:T", title="Týden"),
                            alt.Tooltip("result:N", title="Výsledek"),
                            alt.Tooltip("count:Q", title="Počet"),
                        ],
                    )
                    .properties(title="Počet obchodních HIT a MISS po týdnech", height=280)
                )
                st.altair_chart(count_chart, width="stretch")

            st.write("Výsledek podle obchodní akce")
            st.dataframe(prediction_frames["prediction_summary"], width="stretch")
            st.write("Přesnost cenového forecastu")
            st.dataframe(prediction_frames["forecast_summary"], width="stretch")
            st.write("Srovnání podle verze scoringu")
            st.dataframe(prediction_frames["prediction_by_version"], width="stretch")
            st.write("Dlouhodobý directional hit rate podle tickeru")
            _show_limited_dataframe(
                prediction_frames["prediction_by_ticker"],
                "Souhrn všech uzavřených týdenních predikcí pro každý ticker",
                rows=3000,
            )
            st.write("Detail všech týdenních predikcí")
            _show_limited_dataframe(
                prediction_frames["prediction_details"],
                "HIT/MISS = výsledek BUY/SELL; NO_TRADE = bez obchodu; forecast_result se hodnotí samostatně",
                rows=3000,
            )
        else:
            st.warning(
                "Zapněte `Ukládat historii do SQLite`. Bez uložené ceny a signálu "
                "nelze příští pondělí ověřit, zda predikce vyšla."
            )

    with tab_ranking:
        st.subheader("Top ranking")
        st.dataframe(result["ranking"].get("top", pd.DataFrame()), width="stretch")
        st.subheader("Bottom ranking")
        st.dataframe(result["ranking"].get("bottom", pd.DataFrame()), width="stretch")
        if save_history:
            eval_frames = EvaluationService().evaluate_snapshots(store.read_global_history())
            st.subheader("Historické srovnání skóre (není obchodní backtest)")
            for name, frame in eval_frames.items():
                if name in EvaluationService.PREDICTION_FRAME_NAMES:
                    continue
                st.write(name)
                st.dataframe(frame, width="stretch")
