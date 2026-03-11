from __future__ import annotations

import pandas as pd


class EvaluationService:
    def evaluate_snapshots(self, history: pd.DataFrame) -> dict[str, pd.DataFrame]:
        if history.empty:
            return {"top_bottom": pd.DataFrame(), "by_signal": pd.DataFrame(), "by_percentile_bucket": pd.DataFrame(), "hit_rate": pd.DataFrame()}

        hist = history.sort_values(["ticker", "run_id"]).copy()
        hist["next_return"] = hist.groupby("ticker")["final_total_score"].shift(-1) - hist["final_total_score"]
        valid = hist.dropna(subset=["next_return"])
        if valid.empty:
            return {"top_bottom": pd.DataFrame(), "by_signal": pd.DataFrame(), "by_percentile_bucket": pd.DataFrame(), "hit_rate": pd.DataFrame()}

        valid["percentile_bucket"] = pd.cut(valid["percentile_in_watchlist"], bins=[0, 20, 40, 60, 80, 100], include_lowest=True)
        top_bottom = valid.groupby(valid["percentile_in_watchlist"] >= 90)["next_return"].mean().rename(index={True: "top_decile", False: "others"}).reset_index(name="avg_next_period_return")
        by_signal = valid.groupby("signal", as_index=False)["next_return"].mean().rename(columns={"next_return": "avg_next_period_return"})
        by_percentile_bucket = valid.groupby("percentile_bucket", as_index=False)["next_return"].mean().rename(columns={"next_return": "avg_next_period_return"})

        buy = valid[valid["signal"].isin(["BUY", "STRONG BUY"])]
        sell = valid[valid["signal"].isin(["SELL", "STRONG SELL"])]
        hit_rate = pd.DataFrame(
            {
                "bucket": ["BUY+STRONG_BUY", "SELL+STRONG_SELL"],
                "hit_rate": [float((buy["next_return"] > 0).mean()) if not buy.empty else 0.0, float((sell["next_return"] < 0).mean()) if not sell.empty else 0.0],
            }
        )
        return {
            "top_bottom": top_bottom,
            "by_signal": by_signal,
            "by_percentile_bucket": by_percentile_bucket,
            "hit_rate": hit_rate,
        }
