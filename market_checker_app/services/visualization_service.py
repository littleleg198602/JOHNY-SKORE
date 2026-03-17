from __future__ import annotations

import pandas as pd


class VisualizationService:
    @staticmethod
    def prepare_kpi(signals: pd.DataFrame) -> dict[str, float | int]:
        if signals.empty:
            return {
                "tickers": 0,
                "avg_score": 0.0,
                "avg_confidence": 0.0,
                "avg_risk": 0.0,
                "buy_count": 0,
                "sell_count": 0,
            }
        frame = signals.copy()
        for col in ["final_total_score", "final_confidence", "risk_score"]:
            if col in frame.columns:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")
        signal_series = frame["signal"].fillna("") if "signal" in frame.columns else pd.Series(dtype=str)
        return {
            "tickers": int(len(frame)),
            "avg_score": float(frame.get("final_total_score", pd.Series(dtype=float)).mean() or 0.0),
            "avg_confidence": float(frame.get("final_confidence", pd.Series(dtype=float)).mean() or 0.0),
            "avg_risk": float(frame.get("risk_score", pd.Series(dtype=float)).mean() or 0.0),
            "buy_count": int(signal_series.isin(["BUY", "STRONG BUY"]).sum()),
            "sell_count": int(signal_series.isin(["SELL", "STRONG SELL"]).sum()),
        }

    @staticmethod
    def prepare_signal_distribution_df(signals: pd.DataFrame) -> pd.DataFrame:
        order = ["STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL"]
        if signals.empty or "signal" not in signals.columns:
            return pd.DataFrame({"signal": order, "count": [0] * len(order)})
        counts = signals["signal"].value_counts().reindex(order, fill_value=0).reset_index()
        counts.columns = ["signal", "count"]
        return counts

    @staticmethod
    def prepare_histogram_df(signals: pd.DataFrame, column: str, bucket_size: int = 10) -> pd.DataFrame:
        if signals.empty or column not in signals.columns:
            return pd.DataFrame(columns=["bucket", "count"])
        series = pd.to_numeric(signals[column], errors="coerce").dropna()
        if series.empty:
            return pd.DataFrame(columns=["bucket", "count"])
        bins = list(range(0, 101, bucket_size))
        if bins[-1] != 100:
            bins.append(100)
        bucketed = pd.cut(series.clip(0, 100), bins=bins, include_lowest=True)
        out = bucketed.value_counts(sort=False).reset_index()
        out.columns = ["bucket", "count"]
        out["bucket"] = out["bucket"].astype(str)
        return out

    @staticmethod
    def prepare_top_bottom_df(signals: pd.DataFrame, score_col: str = "final_total_score", n: int = 10) -> tuple[pd.DataFrame, pd.DataFrame]:
        if signals.empty or score_col not in signals.columns:
            return pd.DataFrame(), pd.DataFrame()
        frame = signals.copy()
        frame[score_col] = pd.to_numeric(frame[score_col], errors="coerce")
        cols = [c for c in ["ticker", score_col, "signal", "final_confidence", "risk_score", "rank_in_watchlist"] if c in frame.columns]
        return frame.nlargest(n, score_col)[cols], frame.nsmallest(n, score_col)[cols]

    @staticmethod
    def prepare_scatter_df(signals: pd.DataFrame) -> pd.DataFrame:
        if signals.empty:
            return pd.DataFrame()
        cols = [
            "ticker",
            "signal",
            "final_total_score",
            "final_confidence",
            "risk_score",
            "rank_in_watchlist",
            "market_cap_usd",
            "news_count_48h",
        ]
        data = signals[[c for c in cols if c in signals.columns]].copy()
        for c in ["final_total_score", "final_confidence", "risk_score", "market_cap_usd", "news_count_48h"]:
            if c in data.columns:
                data[c] = pd.to_numeric(data[c], errors="coerce")
        data["point_size"] = data.get("market_cap_usd", pd.Series([50] * len(data))).fillna(50)
        return data.dropna(subset=[c for c in ["final_total_score", "final_confidence"] if c in data.columns])

    @staticmethod
    def prepare_delta_top_movers_df(delta_df: pd.DataFrame, n: int = 10) -> tuple[pd.DataFrame, pd.DataFrame]:
        if delta_df.empty or "DeltaTotal" not in delta_df.columns:
            return pd.DataFrame(), pd.DataFrame()
        frame = delta_df.copy()
        frame["DeltaTotal"] = pd.to_numeric(frame["DeltaTotal"], errors="coerce")
        improvements = frame.sort_values("DeltaTotal", ascending=False).head(n)
        declines = frame.sort_values("DeltaTotal", ascending=True).head(n)
        return improvements, declines

    @staticmethod
    def prepare_signal_transition_df(delta_df: pd.DataFrame) -> pd.DataFrame:
        if delta_df.empty or "SignalChange" not in delta_df.columns:
            return pd.DataFrame(columns=["SignalChange", "count"])
        out = delta_df["SignalChange"].value_counts().reset_index()
        out.columns = ["SignalChange", "count"]
        return out

    @staticmethod
    def prepare_component_delta_df(delta_df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
        required = ["ticker", "DeltaTotal", "DeltaNews", "DeltaTech", "DeltaYahoo"]
        if delta_df.empty or not all(c in delta_df.columns for c in required):
            return pd.DataFrame()
        frame = delta_df.copy()
        for col in ["DeltaTotal", "DeltaNews", "DeltaTech", "DeltaYahoo", "DeltaBehavioral", "DeltaRisk"]:
            if col in frame.columns:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")
        movers = frame.reindex(frame["DeltaTotal"].abs().sort_values(ascending=False).index).head(n)
        melted = movers.melt(
            id_vars=["ticker"],
            value_vars=[c for c in ["DeltaNews", "DeltaTech", "DeltaYahoo", "DeltaBehavioral", "DeltaRisk"] if c in movers.columns],
            var_name="component",
            value_name="delta",
        )
        return melted

    @staticmethod
    def prepare_trend_history_df(global_history: pd.DataFrame) -> dict[str, pd.DataFrame]:
        if global_history.empty:
            empty = pd.DataFrame()
            return {
                "avg_scores": empty,
                "signal_counts": empty,
                "module_scores": empty,
                "bucket_behavior": empty,
            }

        hist = global_history.copy()
        hist["finished_at"] = pd.to_datetime(hist["finished_at"], errors="coerce")
        for col in ["final_total_score", "final_confidence", "risk_score", "news_score", "tech_score", "yahoo_score", "behavioral_score", "percentile_in_watchlist"]:
            if col in hist.columns:
                hist[col] = pd.to_numeric(hist[col], errors="coerce")

        avg_scores = hist.groupby(["run_id", "finished_at"], as_index=False).agg(
            avg_final_total_score=("final_total_score", "mean"),
            avg_final_confidence=("final_confidence", "mean"),
            avg_risk_score=("risk_score", "mean"),
        )
        signal_counts = hist.groupby(["run_id", "finished_at", "signal"], as_index=False).size().rename(columns={"size": "count"})

        module_agg_map = {}
        for col in ["news_score", "tech_score", "yahoo_score", "behavioral_score"]:
            if col in hist.columns:
                module_agg_map[f"avg_{col}"] = (col, "mean")
        module_scores = hist.groupby(["run_id", "finished_at"], as_index=False).agg(**module_agg_map) if module_agg_map else pd.DataFrame()

        bucket_behavior = pd.DataFrame()
        if "percentile_in_watchlist" in hist.columns:
            top = hist[hist["percentile_in_watchlist"] >= 80].groupby(["run_id", "finished_at"], as_index=False)["final_total_score"].mean()
            top["bucket"] = "Top 20 %"
            bottom = hist[hist["percentile_in_watchlist"] <= 20].groupby(["run_id", "finished_at"], as_index=False)["final_total_score"].mean()
            bottom["bucket"] = "Bottom 20 %"
            bucket_behavior = pd.concat([top, bottom], ignore_index=True)

        return {
            "avg_scores": avg_scores,
            "signal_counts": signal_counts,
            "module_scores": module_scores,
            "bucket_behavior": bucket_behavior,
        }

    @staticmethod
    def prepare_ticker_history_df(history_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
        if history_df.empty:
            empty = pd.DataFrame()
            return {
                "series": empty,
                "module_series": empty,
                "table": empty,
                "last_snapshot": empty,
            }

        frame = history_df.copy()
        frame["finished_at"] = pd.to_datetime(frame["finished_at"], errors="coerce")
        numeric_cols = [
            "final_total_score",
            "final_confidence",
            "risk_score",
            "rank_in_watchlist",
            "percentile_in_watchlist",
            "news_score",
            "tech_score",
            "yahoo_score",
            "behavioral_score",
        ]
        for col in numeric_cols:
            if col in frame.columns:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")

        table_cols = [c for c in ["run_id", "finished_at", "signal", "final_total_score", "final_confidence", "risk_score", "rank_in_watchlist", "percentile_in_watchlist"] if c in frame.columns]
        module_cols = [c for c in ["news_score", "tech_score", "yahoo_score", "behavioral_score"] if c in frame.columns]

        module_series = frame[["finished_at", *module_cols]].melt(id_vars=["finished_at"], var_name="module", value_name="score") if module_cols else pd.DataFrame()
        last_snapshot = frame.sort_values("finished_at").tail(1)

        return {
            "series": frame,
            "module_series": module_series,
            "table": frame[table_cols].sort_values("finished_at", ascending=False),
            "last_snapshot": last_snapshot,
        }

    @staticmethod
    def prepare_score_decomposition_df(signals_df: pd.DataFrame, ticker: str) -> pd.DataFrame:
        if signals_df.empty or "ticker" not in signals_df.columns:
            return pd.DataFrame()
        row = signals_df[signals_df["ticker"] == ticker]
        if row.empty:
            return pd.DataFrame()
        r = row.iloc[0]
        data = {
            "modul": ["News", "Tech", "Yahoo", "Behavioral", "Risk"],
            "hodnota": [
                r.get("news_score", 0),
                r.get("tech_score", 0),
                r.get("yahoo_score", 0),
                r.get("behavioral_score", 0),
                r.get("risk_score", 0),
            ],
        }
        return pd.DataFrame(data)

    @staticmethod
    def prepare_confidence_decomposition_df(signals_df: pd.DataFrame, ticker: str) -> pd.DataFrame:
        if signals_df.empty or "ticker" not in signals_df.columns:
            return pd.DataFrame()
        row = signals_df[signals_df["ticker"] == ticker]
        if row.empty:
            return pd.DataFrame()
        r = row.iloc[0]
        data = {
            "modul": ["NewsConfidence", "TechConfidence", "YahooConfidence", "BehavioralConfidence", "FinalConfidence"],
            "hodnota": [
                r.get("news_confidence", 0),
                r.get("tech_confidence", 0),
                r.get("yahoo_confidence", 0),
                r.get("behavioral_confidence", 0),
                r.get("final_confidence", 0),
            ],
        }
        return pd.DataFrame(data)


    @staticmethod
    def prepare_drop_overlap_tables(dashboard_tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        keys = ["weekly_drops", "d14_drops", "m1_drops", "m3_drops"]
        ticker_to_windows: dict[str, set[str]] = {}
        for key in keys:
            frame = dashboard_tables.get(key, pd.DataFrame())
            if frame.empty or "ticker" not in frame.columns:
                continue
            for ticker in frame["ticker"].dropna().astype(str).tolist():
                ticker_to_windows.setdefault(ticker, set()).add(key)

        out: dict[str, pd.DataFrame] = {}
        for key in keys:
            frame = dashboard_tables.get(key, pd.DataFrame())
            if frame.empty or "ticker" not in frame.columns:
                out[key] = frame
                continue
            enhanced = frame.copy()
            enhanced["overlap_count"] = enhanced["ticker"].astype(str).map(lambda t: len(ticker_to_windows.get(t, set())))
            enhanced["overlap_windows"] = enhanced["ticker"].astype(str).map(lambda t: ", ".join(sorted(ticker_to_windows.get(t, set()))))
            enhanced["is_shared_drop"] = enhanced["overlap_count"] > 1
            out[key] = enhanced

        overlap_rows = [
            {"ticker": ticker, "overlap_count": len(windows), "overlap_windows": ", ".join(sorted(windows))}
            for ticker, windows in ticker_to_windows.items()
            if len(windows) > 1
        ]
        out["shared_drop_tickers"] = pd.DataFrame(overlap_rows).sort_values(["overlap_count", "ticker"], ascending=[False, True]) if overlap_rows else pd.DataFrame(columns=["ticker", "overlap_count", "overlap_windows"])
        return out

    @staticmethod
    def prepare_dashboard_export_payload(signals_df: pd.DataFrame, ranking_tables: dict[str, pd.DataFrame], dashboard_tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        kpi = VisualizationService.prepare_kpi(signals_df)
        signal_df = VisualizationService.prepare_signal_distribution_df(signals_df)
        score_hist = VisualizationService.prepare_histogram_df(signals_df, "final_total_score")
        conf_hist = VisualizationService.prepare_histogram_df(signals_df, "final_confidence")
        risk_hist = VisualizationService.prepare_histogram_df(signals_df, "risk_score")
        scatter_df = VisualizationService.prepare_scatter_df(signals_df)
        top10, bottom10 = VisualizationService.prepare_top_bottom_df(signals_df, "final_total_score", n=10)
        overlap = VisualizationService.prepare_drop_overlap_tables(dashboard_tables)

        return {
            "dashboard_kpi": pd.DataFrame([kpi]),
            "signal_distribution": signal_df,
            "score_distribution": score_hist,
            "confidence_distribution": conf_hist,
            "risk_distribution": risk_hist,
            "scatter_confidence_score": scatter_df,
            "top10_final_score": top10,
            "bottom10_final_score": bottom10,
            "ranking_top": ranking_tables.get("top", pd.DataFrame()),
            "ranking_bottom": ranking_tables.get("bottom", pd.DataFrame()),
            "top20_final_total": dashboard_tables.get("top_total", pd.DataFrame()),
            "drop_7d": overlap.get("weekly_drops", pd.DataFrame()),
            "drop_14d": overlap.get("d14_drops", pd.DataFrame()),
            "drop_1m": overlap.get("m1_drops", pd.DataFrame()),
            "drop_3m": overlap.get("m3_drops", pd.DataFrame()),
            "shared_drop_tickers": overlap.get("shared_drop_tickers", pd.DataFrame()),
            "top_marketcap": dashboard_tables.get("top_marketcap", pd.DataFrame()),
            "bottom_marketcap": dashboard_tables.get("bottom_marketcap", pd.DataFrame()),
        }
