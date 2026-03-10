from __future__ import annotations

from pathlib import Path

import pandas as pd


class ComparisonService:
    @staticmethod
    def compare_runs(current: pd.DataFrame, previous: pd.DataFrame) -> pd.DataFrame:
        if current.empty or previous.empty:
            return pd.DataFrame()
        cols = ["ticker", "final_total_score", "news_score", "tech_score", "yahoo_score", "final_confidence", "signal"]
        merged = current[cols].merge(previous[cols], on="ticker", suffixes=("", "_prev"), how="inner")
        merged["DeltaTotal"] = merged["final_total_score"] - merged["final_total_score_prev"]
        merged["DeltaNews"] = merged["news_score"] - merged["news_score_prev"]
        merged["DeltaTech"] = merged["tech_score"] - merged["tech_score_prev"]
        merged["DeltaYahoo"] = merged["yahoo_score"] - merged["yahoo_score_prev"]
        merged["DeltaConfidence"] = merged["final_confidence"] - merged["final_confidence_prev"]
        merged["SignalChange"] = merged["signal_prev"] + " -> " + merged["signal"]
        return merged.sort_values("DeltaTotal", ascending=False)

    @staticmethod
    def compare_with_previous_excel(current: pd.DataFrame, excel_path: Path) -> pd.DataFrame:
        if current.empty or not excel_path.exists():
            return pd.DataFrame()
        try:
            previous = pd.read_excel(excel_path, sheet_name="Signals")
        except Exception:
            return pd.DataFrame()
        return ComparisonService.compare_runs(current, previous)
