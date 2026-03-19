from __future__ import annotations

import json

import pandas as pd

from market_checker_app.analysis.scoring import _build_decision_modules, _decision_from_modules
from market_checker_app.config import DecisionModuleWeights, DecisionThresholds


class VisualizationService:
    @staticmethod
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

    @staticmethod
    def _parse_module_breakdown(value: object) -> list[dict[str, object]]:
        if isinstance(value, list):
            return [v for v in value if isinstance(v, dict)]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [v for v in parsed if isinstance(v, dict)]
            except json.JSONDecodeError:
                return []
        return []

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

    @staticmethod
    def prepare_hold_calibration(
        signals_df: pd.DataFrame,
        *,
        hold_bands: tuple[float, float, float] = (3.0, 7.0, 11.0),
        high_conf_threshold: float = 70.0,
    ) -> dict[str, object]:
        if signals_df.empty or "signal" not in signals_df.columns:
            return {
                "hold_diagnostics": pd.DataFrame(),
                "hold_concentration": pd.DataFrame(),
                "sensitivity_distribution": pd.DataFrame(),
                "sensitivity_audit": pd.DataFrame(),
                "confidence_sanity": {},
                "technical_driver_effectiveness": {},
            }

        frame = signals_df.copy()
        for col in [
            "bull_score",
            "bear_score",
            "bull_bear_spread",
            "final_confidence",
            "news_score",
            "tech_score",
            "risk_score",
            "panic_score",
            "yahoo_score",
            "news_confidence",
            "tech_confidence",
            "yahoo_confidence",
            "behavioral_confidence",
        ]:
            if col in frame.columns:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")

        hold_rows = frame[frame["signal"] == "HOLD"].copy()
        hold_rows["blocked_reasons_parsed"] = hold_rows.get("blocked_reasons", pd.Series(dtype=object)).apply(VisualizationService._parse_json_list)
        hold_rows["modules_parsed"] = hold_rows.get("module_breakdown", pd.Series(dtype=object)).apply(VisualizationService._parse_module_breakdown)

        def module_directions(modules: list[dict[str, object]]) -> dict[str, str]:
            return {str(m.get("module", "")): str(m.get("direction", "unknown")) for m in modules}

        def primary_driver(modules: list[dict[str, object]]) -> str:
            if not modules:
                return "unknown"
            ranked = sorted(
                modules,
                key=lambda m: abs(float(m.get("bull_contribution", 0.0)) - float(m.get("bear_contribution", 0.0))),
                reverse=True,
            )
            return str(ranked[0].get("module", "unknown"))

        hold_rows["module_directions"] = hold_rows["modules_parsed"].apply(module_directions)
        hold_rows["primary_driver"] = hold_rows["modules_parsed"].apply(primary_driver)

        hold_diag_cols = [
            "ticker",
            "bull_score",
            "bear_score",
            "bull_bear_spread",
            "final_confidence",
            "primary_driver",
            "module_directions",
            "blocked_reasons_parsed",
        ]
        hold_diag = hold_rows[hold_diag_cols].rename(
            columns={
                "bull_bear_spread": "spread",
                "final_confidence": "overall_confidence",
                "blocked_reasons_parsed": "blocked_reasons",
            }
        )

        thresholds = DecisionThresholds()
        concentration_counts = {
            "small_spread": 0,
            "technical_news_conflict": 0,
            "panic_block": 0,
            "low_confidence": 0,
            "mixed_neutral_modules": 0,
            "other": 0,
        }

        for _, row in hold_rows.iterrows():
            blocked = set(row["blocked_reasons_parsed"])
            dirs = row["module_directions"]
            spread = float(row.get("bull_bear_spread", 0.0) or 0.0)
            neutral_count = int(sum(1 for d in dirs.values() if d == "neutral"))
            if abs(spread) <= thresholds.hold_band or "bull_bear_balance_hold_band" in blocked:
                concentration_counts["small_spread"] += 1
            elif dirs.get("technical") in {"bullish", "bearish"} and dirs.get("news") in {"bullish", "bearish"} and dirs.get("technical") != dirs.get("news"):
                concentration_counts["technical_news_conflict"] += 1
            elif any(reason.startswith("panic_") for reason in blocked):
                concentration_counts["panic_block"] += 1
            elif "low_confidence_blocks_directional_signal" in blocked:
                concentration_counts["low_confidence"] += 1
            elif neutral_count >= 2:
                concentration_counts["mixed_neutral_modules"] += 1
            else:
                concentration_counts["other"] += 1

        concentration_df = pd.DataFrame(
            [{"cause": key, "count": value} for key, value in concentration_counts.items()]
        ).sort_values("count", ascending=False)

        signal_order = ["STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL"]
        weights = DecisionModuleWeights()
        sensitivity_rows: list[dict[str, object]] = []
        for band in hold_bands:
            simulated = {k: 0 for k in signal_order}
            adj_thresholds = DecisionThresholds(
                strong_buy_min_bull_score=thresholds.strong_buy_min_bull_score,
                strong_buy_min_spread=thresholds.strong_buy_min_spread,
                buy_min_spread=thresholds.buy_min_spread,
                hold_band=band,
                sell_min_spread=thresholds.sell_min_spread,
                strong_sell_min_bear_score=thresholds.strong_sell_min_bear_score,
                strong_sell_min_negative_spread=thresholds.strong_sell_min_negative_spread,
                minimum_confidence_buy=thresholds.minimum_confidence_buy,
                minimum_confidence_strong=thresholds.minimum_confidence_strong,
                panic_block_threshold=thresholds.panic_block_threshold,
            )
            for _, r in frame.iterrows():
                modules = _build_decision_modules(
                    news_score=float(r.get("news_score", 50) or 50),
                    tech_score=float(r.get("tech_score", 50) or 50),
                    analyst_score=float(r.get("yahoo_score", 50) or 50),
                    panic_score=float(r.get("panic_score", r.get("risk_score", 50)) or 50),
                    news_confidence=float(r.get("news_confidence", 50) or 50),
                    tech_confidence=float(r.get("tech_confidence", 50) or 50),
                    analyst_confidence=float(r.get("yahoo_confidence", 50) or 50),
                    panic_confidence=float(r.get("behavioral_confidence", 50) or 50),
                    context="hold-calibration",
                )
                signal, *_ = _decision_from_modules(modules, float(r.get("panic_score", r.get("risk_score", 50)) or 50), weights, adj_thresholds)
                simulated[signal] += 1
            for signal in signal_order:
                sensitivity_rows.append({"scenario": f"hold_band_{band:g}", "signal": signal, "count": simulated[signal]})

        sensitivity_df = pd.DataFrame(sensitivity_rows)
        sensitivity_audit = (
            sensitivity_df[sensitivity_df["signal"] == "HOLD"][["scenario", "count"]]
            .rename(columns={"count": "hold_count"})
            .sort_values("scenario")
            .reset_index(drop=True)
        )
        if not sensitivity_audit.empty:
            baseline_row = sensitivity_audit[sensitivity_audit["scenario"] == "hold_band_7"]
            baseline_hold = float(baseline_row.iloc[0]["hold_count"]) if not baseline_row.empty else float(sensitivity_audit.iloc[0]["hold_count"])
            sensitivity_audit["delta_vs_baseline"] = sensitivity_audit["hold_count"] - baseline_hold

        high_conf_holds = hold_rows[hold_rows["final_confidence"] >= high_conf_threshold]
        confidence_sanity = {
            "high_conf_threshold": high_conf_threshold,
            "hold_count": int(len(hold_rows)),
            "high_conf_hold_count": int(len(high_conf_holds)),
            "high_conf_hold_ratio": float(len(high_conf_holds) / len(hold_rows)) if len(hold_rows) else 0.0,
            "explanation": (
                "Vysoká confidence u HOLD je častá; pravděpodobně jde o silné, ale konfliktní moduly (ne nutně nízkou confidence logiku)."
                if len(high_conf_holds) > 0
                else "Vysoká confidence u HOLD není častá."
            ),
        }

        tech_trapped: list[dict[str, object]] = []
        for _, row in hold_rows.iterrows():
            technical = next((m for m in row["modules_parsed"] if str(m.get("module")) == "technical"), None)
            if technical is None:
                continue
            spread = float(technical.get("bull_contribution", 0.0)) - float(technical.get("bear_contribution", 0.0))
            if abs(spread) >= 20:
                hold_why = "small_spread_or_conflict"
                if "low_confidence_blocks_directional_signal" in row["blocked_reasons_parsed"]:
                    hold_why = "low_confidence"
                elif any(reason.startswith("panic_") for reason in row["blocked_reasons_parsed"]):
                    hold_why = "panic_block"
                elif "bull_bear_balance_hold_band" in row["blocked_reasons_parsed"]:
                    hold_why = "hold_band"
                tech_trapped.append(
                    {
                        "ticker": row.get("ticker"),
                        "tech_direction": technical.get("direction"),
                        "tech_spread": round(spread, 2),
                        "bull_score": row.get("bull_score"),
                        "bear_score": row.get("bear_score"),
                        "spread": row.get("bull_bear_spread"),
                        "module_directions": row.get("module_directions"),
                        "blocked_reasons": row["blocked_reasons_parsed"],
                        "final_signal": row.get("signal"),
                        "why_hold_won": hold_why,
                    }
                )
        technical_effectiveness = {
            "strong_technical_hold_count": len(tech_trapped),
            "hold_count": int(len(hold_rows)),
            "strong_technical_hold_ratio": float(len(tech_trapped) / len(hold_rows)) if len(hold_rows) else 0.0,
            "examples": pd.DataFrame(tech_trapped),
        }

        return {
            "hold_diagnostics": hold_diag.sort_values(["spread", "overall_confidence"], ascending=[True, False]),
            "hold_concentration": concentration_df,
            "sensitivity_distribution": sensitivity_df,
            "sensitivity_audit": sensitivity_audit,
            "confidence_sanity": confidence_sanity,
            "technical_driver_effectiveness": technical_effectiveness,
        }
