from __future__ import annotations

from pathlib import Path

import pandas as pd


class ComparisonService:
    @staticmethod
    def compare_runs(current: pd.DataFrame, previous: pd.DataFrame) -> pd.DataFrame:
        if current.empty or previous.empty:
            return pd.DataFrame()
        base_cols = [
            "ticker",
            "market_cap_usd",
            "final_total_score",
            "news_score",
            "tech_score",
            "yahoo_score",
            "behavioral_score",
            "risk_score",
            "final_confidence",
            "signal",
            "rank_in_watchlist",
        ]
        cols = [c for c in base_cols if c in current.columns and c in previous.columns]
        merged = current[cols].merge(previous[cols], on="ticker", suffixes=("", "_prev"), how="inner")
        merged["DeltaTotal"] = merged["final_total_score"] - merged["final_total_score_prev"]
        merged["DeltaNews"] = merged["news_score"] - merged["news_score_prev"]
        merged["DeltaTech"] = merged["tech_score"] - merged["tech_score_prev"]
        merged["DeltaYahoo"] = merged["yahoo_score"] - merged["yahoo_score_prev"]
        if "behavioral_score" in merged.columns and "behavioral_score_prev" in merged.columns:
            merged["DeltaBehavioral"] = merged["behavioral_score"] - merged["behavioral_score_prev"]
        if "risk_score" in merged.columns and "risk_score_prev" in merged.columns:
            merged["DeltaRisk"] = merged["risk_score"] - merged["risk_score_prev"]
        merged["DeltaConfidence"] = merged["final_confidence"] - merged["final_confidence_prev"]
        if "rank_in_watchlist" in merged.columns and "rank_in_watchlist_prev" in merged.columns:
            merged["DeltaRank"] = merged["rank_in_watchlist_prev"] - merged["rank_in_watchlist"]
        if "market_cap_usd" in merged.columns and "market_cap_usd_prev" in merged.columns:
            merged["DeltaMarketCap"] = merged["market_cap_usd"] - merged["market_cap_usd_prev"]
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
