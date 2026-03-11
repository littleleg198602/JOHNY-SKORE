from __future__ import annotations

import pandas as pd


class RankingService:
    @staticmethod
    def apply_ranking(signals: pd.DataFrame) -> pd.DataFrame:
        if signals.empty:
            return signals
        ranked = signals.sort_values("final_total_score", ascending=False).reset_index(drop=True)
        ranked["rank_in_watchlist"] = ranked.index + 1
        ranked["percentile_in_watchlist"] = ranked["final_total_score"].rank(pct=True, ascending=True) * 100
        return ranked

    @staticmethod
    def top_bottom_tables(signals: pd.DataFrame, size: int = 10) -> dict[str, pd.DataFrame]:
        if signals.empty:
            return {"top": pd.DataFrame(), "bottom": pd.DataFrame()}
        ordered = signals.sort_values("final_total_score", ascending=False)
        return {"top": ordered.head(size), "bottom": ordered.tail(size)}
