from __future__ import annotations

import pandas as pd


class EvaluationService:
    def evaluate_snapshots(self, history: pd.DataFrame) -> dict[str, pd.DataFrame]:
        if history.empty:
            return {
                "top_bottom": pd.DataFrame(),
                "by_signal": pd.DataFrame(),
                "by_percentile_bucket": pd.DataFrame(),
                "hit_rate": pd.DataFrame(),
                "version_comparison": pd.DataFrame(),
            }

        hist = history.sort_values(["ticker", "run_id"]).copy()
        hist["next_price"] = hist.groupby("ticker")["current_price"].shift(-1)
        hist["next_return_pct"] = ((hist["next_price"] / hist["current_price"]) - 1) * 100
        valid = hist.dropna(subset=["next_return_pct"]).copy()
        if valid.empty:
            return {
                "top_bottom": pd.DataFrame(),
                "by_signal": pd.DataFrame(),
                "by_percentile_bucket": pd.DataFrame(),
                "hit_rate": pd.DataFrame(),
                "version_comparison": pd.DataFrame({"note": ["Forward return nelze spočítat: chybí current_price historie."]}),
            }

        valid["percentile_bucket"] = pd.cut(valid["percentile_in_watchlist"], bins=[0, 10, 20, 40, 60, 80, 90, 100], include_lowest=True)
        valid["decile_group"] = pd.cut(valid["percentile_in_watchlist"], bins=[0, 10, 90, 100], labels=["bottom_decile", "middle", "top_decile"], include_lowest=True)

        top_bottom = (
            valid[valid["decile_group"].isin(["top_decile", "bottom_decile"])]
            .groupby("decile_group", as_index=False)["next_return_pct"]
            .mean()
            .rename(columns={"next_return_pct": "avg_next_period_return_pct"})
        )

        by_signal = (
            valid.groupby("signal", as_index=False)["next_return_pct"]
            .mean()
            .rename(columns={"next_return_pct": "avg_next_period_return_pct"})
        )

        by_percentile_bucket = (
            valid.groupby("percentile_bucket", as_index=False)["next_return_pct"]
            .mean()
            .rename(columns={"next_return_pct": "avg_next_period_return_pct"})
        )

        buy = valid[valid["signal"].isin(["BUY", "STRONG BUY"])]
        sell = valid[valid["signal"].isin(["SELL", "STRONG SELL"])]
        hit_rate = pd.DataFrame(
            {
                "bucket": ["BUY+STRONG_BUY", "SELL+STRONG_SELL"],
                "hit_rate": [
                    float((buy["next_return_pct"] > 0).mean()) if not buy.empty else 0.0,
                    float((sell["next_return_pct"] < 0).mean()) if not sell.empty else 0.0,
                ],
            }
        )

        version_comparison = pd.DataFrame(
            {
                "note": [
                    "Old vs new scoring comparison není dostupný: v historii není uložen old/legacy score."
                ]
            }
        )

        return {
            "top_bottom": top_bottom,
            "by_signal": by_signal,
            "by_percentile_bucket": by_percentile_bucket,
            "hit_rate": hit_rate,
            "version_comparison": version_comparison,
        }
