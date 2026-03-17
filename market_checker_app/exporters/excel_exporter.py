from __future__ import annotations

from pathlib import Path

import pandas as pd


class ExcelExporter:
    SIGNAL_EXPORT_COLUMNS = [
        "ticker",
        "market_cap_usd",
        "rank_market_cap",
        "scoring_version",
        "legacy_total_score",
        "legacy_signal",
        "tech_source_used",
        "news_count_48h",
        "news_score",
        "tech_score",
        "yahoo_score",
        "raw_total_score",
        "quality_adjusted_score",
        "risk_adjusted_score",
        "final_total_score",
        "final_confidence",
        "news_confidence",
        "tech_confidence",
        "yahoo_confidence",
        "behavioral_confidence",
        "data_quality_score",
        "risk_score",
        "behavioral_score",
        "bull_score",
        "bear_score",
        "bull_bear_spread",
        "bullish_module_count",
        "bearish_module_count",
        "neutral_module_count",
        "downgrade_count",
        "blocked_reasons",
        "module_breakdown",
        "rank_in_watchlist",
        "percentile_in_watchlist",
        "regime",
        "signal",
        "signal_strength",
        "reasons",
        "warnings",
        "risk_flags",
        "key_drivers",
        "overall_summary",
        "last_week_change_pct",
        "last_14d_change_pct",
        "last_1m_change_pct",
        "last_3m_change_pct",
    ]

    @staticmethod
    def _sanitize_for_excel(frame: pd.DataFrame) -> pd.DataFrame:
        cleaned = frame.copy()
        for column in cleaned.columns:
            series = cleaned[column]
            if pd.api.types.is_datetime64tz_dtype(series):
                cleaned[column] = series.dt.tz_localize(None)
                continue

            if series.dtype == object:
                sample = series.dropna()
                if not sample.empty:
                    first = sample.iloc[0]
                    if isinstance(first, pd.Timestamp) and first.tzinfo is not None:
                        cleaned[column] = pd.to_datetime(series, errors="coerce").dt.tz_localize(None)
        return cleaned

    def export(
        self,
        output_path: Path,
        signals: pd.DataFrame,
        sources: pd.DataFrame,
        articles: pd.DataFrame,
        dashboard: dict[str, pd.DataFrame],
        delta: pd.DataFrame | None = None,
        dashboard_export: dict[str, pd.DataFrame] | None = None,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        signal_cols = [c for c in self.SIGNAL_EXPORT_COLUMNS if c in signals.columns]
        signals_xlsx = self._sanitize_for_excel(signals[signal_cols] if signal_cols else signals)
        sources_xlsx = self._sanitize_for_excel(sources)
        articles_xlsx = self._sanitize_for_excel(articles)

        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            signals_xlsx.to_excel(writer, sheet_name="Signals", index=False)
            sources_xlsx.to_excel(writer, sheet_name="Sources", index=False)
            articles_xlsx.to_excel(writer, sheet_name="Articles", index=False)

            dashboard_sheet = pd.concat(
                [
                    dashboard.get("top_total", pd.DataFrame()).assign(section="Top20 FinalTotalScore"),
                    dashboard.get("weekly_drops", pd.DataFrame()).assign(section="Top20 7D Drops"),
                    dashboard.get("d14_drops", pd.DataFrame()).assign(section="Top20 14D Drops"),
                    dashboard.get("m1_drops", pd.DataFrame()).assign(section="Top20 1M Drops"),
                    dashboard.get("m3_drops", pd.DataFrame()).assign(section="Top20 3M Drops"),
                    dashboard.get("top_marketcap", pd.DataFrame()).assign(section="Top20 MarketCap"),
                    dashboard.get("bottom_marketcap", pd.DataFrame()).assign(section="Bottom20 MarketCap"),
                ],
                ignore_index=True,
            )
            self._sanitize_for_excel(dashboard_sheet).to_excel(writer, sheet_name="Dashboard", index=False)

            if dashboard_export:
                sheet_mapping = {
                    "dashboard_kpi": "Dash_KPI",
                    "signal_distribution": "Dash_SignalDist",
                    "score_distribution": "Dash_ScoreDist",
                    "confidence_distribution": "Dash_ConfDist",
                    "risk_distribution": "Dash_RiskDist",
                    "scatter_confidence_score": "Dash_Scatter",
                    "top10_final_score": "Dash_Top10",
                    "bottom10_final_score": "Dash_Bottom10",
                    "ranking_top": "Dash_RankTop",
                    "ranking_bottom": "Dash_RankBottom",
                    "drop_7d": "Dash_Drop7D",
                    "drop_14d": "Dash_Drop14D",
                    "drop_1m": "Dash_Drop1M",
                    "drop_3m": "Dash_Drop3M",
                    "shared_drop_tickers": "Dash_SharedDrops",
                    "top_marketcap": "Dash_McapTop",
                    "bottom_marketcap": "Dash_McapBottom",
                }
                for key, sheet_name in sheet_mapping.items():
                    frame = dashboard_export.get(key, pd.DataFrame())
                    if frame is not None and not frame.empty:
                        self._sanitize_for_excel(frame).to_excel(writer, sheet_name=sheet_name, index=False)

            if delta is not None and not delta.empty:
                self._sanitize_for_excel(delta).to_excel(writer, sheet_name="DeltaVsPrev", index=False)
        return output_path
