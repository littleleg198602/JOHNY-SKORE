from __future__ import annotations

import pandas as pd


NUMERIC_DASHBOARD_COLUMNS = ("final_total_score", "last_week_change_pct", "last_1m_change_pct", "last_3m_change_pct", "market_cap_usd")


def build_dashboard_tables(signals: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if signals.empty:
        empty = pd.DataFrame()
        return {"top_total": empty, "weekly_drops": empty, "m1_drops": empty, "m3_drops": empty, "top_marketcap": empty, "bottom_marketcap": empty}

    normalized = signals.copy()
    for col in NUMERIC_DASHBOARD_COLUMNS:
        if col in normalized.columns:
            normalized[col] = pd.to_numeric(normalized[col], errors="coerce")

    return {
        "top_total": normalized.nlargest(20, "final_total_score"),
        "weekly_drops": normalized.nsmallest(20, "last_week_change_pct"),
        "m1_drops": normalized.nsmallest(20, "last_1m_change_pct"),
        "m3_drops": normalized.nsmallest(20, "last_3m_change_pct"),
        "top_marketcap": normalized.nlargest(20, "market_cap_usd"),
        "bottom_marketcap": normalized.nsmallest(20, "market_cap_usd"),
    }
