from __future__ import annotations

import pandas as pd


class ComparisonService:
    @staticmethod
    def compare_runs(current: pd.DataFrame, previous: pd.DataFrame) -> pd.DataFrame:
        if current.empty or previous.empty:
            return pd.DataFrame()

        cols = ["ticker", "total_score", "news_score", "tech_score", "yahoo_score", "signal"]
        merged = current[cols].merge(previous[cols], on="ticker", suffixes=("", "_prev"), how="inner")
        merged["DeltaTotal"] = merged["total_score"] - merged["total_score_prev"]
        merged["DeltaNews"] = merged["news_score"] - merged["news_score_prev"]
        merged["DeltaTech"] = merged["tech_score"] - merged["tech_score_prev"]
        merged["DeltaYahoo"] = merged["yahoo_score"] - merged["yahoo_score_prev"]
        merged["SignalChange"] = merged["signal_prev"] + " -> " + merged["signal"]
        return merged.sort_values("DeltaTotal", ascending=False)
