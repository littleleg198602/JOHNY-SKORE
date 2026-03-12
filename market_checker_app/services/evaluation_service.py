from __future__ import annotations

import pandas as pd


class EvaluationService:
    def evaluate_snapshots(self, history: pd.DataFrame) -> dict[str, pd.DataFrame]:
        if history.empty:
            return {
                "score_comparison": pd.DataFrame(),
                "top_bottom_new": pd.DataFrame(),
                "top_bottom_legacy": pd.DataFrame(),
                "by_signal_new": pd.DataFrame(),
                "by_signal_legacy": pd.DataFrame(),
                "strategy_side_by_side": pd.DataFrame(),
                "signal_transition": pd.DataFrame(),
                "hit_rate_new_vs_legacy": pd.DataFrame(),
                "coverage": pd.DataFrame(),
            }

        hist = history.sort_values(["ticker", "run_id"]).copy()
        hist["score_delta_new_minus_legacy"] = hist["final_total_score"] - hist["legacy_total_score"]
        score_comparison = pd.DataFrame(
            {
                "metric": ["avg_final_total", "avg_legacy_total", "avg_delta_new_minus_legacy", "score_correlation"],
                "value": [
                    float(hist["final_total_score"].mean()),
                    float(hist["legacy_total_score"].mean()),
                    float(hist["score_delta_new_minus_legacy"].mean()),
                    float(hist[["final_total_score", "legacy_total_score"]].corr().iloc[0, 1]) if len(hist) > 1 else 1.0,
                ],
            }
        )

        coverage = pd.DataFrame(
            {
                "metric": ["rows", "scoring_versions", "mt5_rows", "yfinance_fallback_rows"],
                "value": [
                    int(len(hist)),
                    int(hist["scoring_version"].nunique(dropna=True)) if "scoring_version" in hist.columns else 0,
                    int((hist["tech_source_used"] == "mt5").sum()) if "tech_source_used" in hist.columns else 0,
                    int((hist["tech_source_used"] == "yfinance_fallback").sum()) if "tech_source_used" in hist.columns else 0,
                ],
            }
        )

        hist["next_price"] = hist.groupby("ticker")["current_price"].shift(-1)
        hist["next_return_pct"] = ((hist["next_price"] / hist["current_price"]) - 1) * 100
        valid = hist.dropna(subset=["next_return_pct"]).copy()
        if valid.empty:
            return {
                "score_comparison": score_comparison,
                "top_bottom_new": pd.DataFrame(),
                "top_bottom_legacy": pd.DataFrame(),
                "by_signal_new": pd.DataFrame(),
                "by_signal_legacy": pd.DataFrame(),
                "strategy_side_by_side": pd.DataFrame({"note": ["Forward return nelze spočítat: chybí current_price historie."]}),
                "signal_transition": pd.DataFrame(),
                "hit_rate_new_vs_legacy": pd.DataFrame(),
                "coverage": coverage,
            }

        valid["new_decile_group"] = pd.cut(valid["percentile_in_watchlist"], bins=[0, 10, 90, 100], labels=["bottom_decile", "middle", "top_decile"], include_lowest=True)
        valid["legacy_percentile"] = valid.groupby("run_id")["legacy_total_score"].rank(pct=True, ascending=True) * 100
        valid["legacy_decile_group"] = pd.cut(valid["legacy_percentile"], bins=[0, 10, 90, 100], labels=["bottom_decile", "middle", "top_decile"], include_lowest=True)

        top_bottom_new = (
            valid[valid["new_decile_group"].isin(["top_decile", "bottom_decile"])]
            .groupby("new_decile_group", as_index=False)["next_return_pct"]
            .mean()
            .rename(columns={"new_decile_group": "decile_group", "next_return_pct": "avg_next_period_return_pct"})
        )

        top_bottom_legacy = (
            valid[valid["legacy_decile_group"].isin(["top_decile", "bottom_decile"])]
            .groupby("legacy_decile_group", as_index=False)["next_return_pct"]
            .mean()
            .rename(columns={"legacy_decile_group": "decile_group", "next_return_pct": "avg_next_period_return_pct"})
        )

        by_signal_new = (
            valid.groupby("signal", as_index=False)["next_return_pct"]
            .mean()
            .rename(columns={"signal": "new_signal", "next_return_pct": "avg_next_period_return_pct"})
        )
        by_signal_legacy = (
            valid.groupby("legacy_signal", as_index=False)["next_return_pct"]
            .mean()
            .rename(columns={"legacy_signal": "legacy_signal", "next_return_pct": "avg_next_period_return_pct"})
        )

        signal_transition = (
            valid.groupby(["legacy_signal", "signal"], as_index=False)
            .size()
            .rename(columns={"size": "count"})
            .sort_values("count", ascending=False)
        )

        def _hit(df: pd.DataFrame, signal_col: str) -> tuple[float, float]:
            buy = df[df[signal_col].isin(["BUY", "STRONG BUY"])]
            sell = df[df[signal_col].isin(["SELL", "STRONG SELL"])]
            return (
                float((buy["next_return_pct"] > 0).mean()) if not buy.empty else 0.0,
                float((sell["next_return_pct"] < 0).mean()) if not sell.empty else 0.0,
            )

        new_buy_hit, new_sell_hit = _hit(valid, "signal")
        legacy_buy_hit, legacy_sell_hit = _hit(valid, "legacy_signal")
        hit_rate_new_vs_legacy = pd.DataFrame(
            {
                "strategy": ["new", "legacy", "new", "legacy"],
                "bucket": ["BUY+STRONG_BUY", "BUY+STRONG_BUY", "SELL+STRONG_SELL", "SELL+STRONG_SELL"],
                "hit_rate": [new_buy_hit, legacy_buy_hit, new_sell_hit, legacy_sell_hit],
            }
        )

        strategy_side_by_side = pd.DataFrame(
            {
                "metric": [
                    "top_decile_avg_return_pct",
                    "bottom_decile_avg_return_pct",
                    "buy_hit_rate",
                    "sell_hit_rate",
                ],
                "new": [
                    float(top_bottom_new[top_bottom_new["decile_group"] == "top_decile"]["avg_next_period_return_pct"].mean()),
                    float(top_bottom_new[top_bottom_new["decile_group"] == "bottom_decile"]["avg_next_period_return_pct"].mean()),
                    new_buy_hit,
                    new_sell_hit,
                ],
                "legacy": [
                    float(top_bottom_legacy[top_bottom_legacy["decile_group"] == "top_decile"]["avg_next_period_return_pct"].mean()),
                    float(top_bottom_legacy[top_bottom_legacy["decile_group"] == "bottom_decile"]["avg_next_period_return_pct"].mean()),
                    legacy_buy_hit,
                    legacy_sell_hit,
                ],
            }
        )

        return {
            "score_comparison": score_comparison,
            "top_bottom_new": top_bottom_new,
            "top_bottom_legacy": top_bottom_legacy,
            "by_signal_new": by_signal_new,
            "by_signal_legacy": by_signal_legacy,
            "strategy_side_by_side": strategy_side_by_side,
            "signal_transition": signal_transition,
            "hit_rate_new_vs_legacy": hit_rate_new_vs_legacy,
            "coverage": coverage,
        }
