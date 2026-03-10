from __future__ import annotations

import pandas as pd


def prepare_delta_for_excel(delta_df: pd.DataFrame) -> pd.DataFrame:
    if delta_df.empty:
        return delta_df
    cols = [
        "ticker",
        "final_total_score_prev",
        "final_total_score",
        "DeltaTotal",
        "final_confidence_prev",
        "final_confidence",
        "DeltaConfidence",
        "signal_prev",
        "signal",
        "SignalChange",
    ]
    return delta_df[[c for c in cols if c in delta_df.columns]]
