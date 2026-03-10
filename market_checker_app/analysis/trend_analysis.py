from __future__ import annotations

import pandas as pd


def score_distribution(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame

    bins = pd.cut(frame["total_score"], bins=[0, 20, 40, 60, 80, 100], include_lowest=True)
    distribution = bins.value_counts().sort_index().rename_axis("bucket").reset_index(name="count")
    distribution["bucket"] = distribution["bucket"].astype(str)
    return distribution
