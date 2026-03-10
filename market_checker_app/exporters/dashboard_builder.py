from __future__ import annotations

import pandas as pd


def build_dashboard_tables(signals: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if signals.empty:
        empty = pd.DataFrame()
        return {
            "top_total": empty,
            "weekly_drops": empty,
            "m1_drops": empty,
            "m3_drops": empty,
            "top_marketcap": empty,
            "bottom_marketcap": empty,
        }

    return {
        "top_total": signals.nlargest(20, "total_score"),
        "weekly_drops": signals.nsmallest(20, "last_week_change_pct"),
        "m1_drops": signals.nsmallest(20, "last_1m_change_pct"),
        "m3_drops": signals.nsmallest(20, "last_3m_change_pct"),
        "top_marketcap": signals.nlargest(20, "market_cap_usd"),
        "bottom_marketcap": signals.nsmallest(20, "market_cap_usd"),
    }
