from __future__ import annotations

import pandas as pd


def prepare_delta_for_excel(delta_df: pd.DataFrame) -> pd.DataFrame:
    if delta_df.empty:
        return delta_df
    cols = [
        "ticker",
        "total_score_prev",
        "total_score",
        "DeltaTotal",
        "news_score_prev",
        "news_score",
        "DeltaNews",
        "tech_score_prev",
        "tech_score",
        "DeltaTech",
        "yahoo_score_prev",
        "yahoo_score",
        "DeltaYahoo",
        "signal_prev",
        "signal",
        "SignalChange",
    ]
    return delta_df[[c for c in cols if c in delta_df.columns]]
