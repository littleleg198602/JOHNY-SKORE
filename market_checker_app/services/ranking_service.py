from __future__ import annotations

import math

import pandas as pd


class RankingService:
    @staticmethod
    def _eligibility(signals: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        """Return an explicit eligibility mask instead of ranking bad rows.

        Compatibility callers that only provide a score remain rankable.  The
        production pipeline supplies the price and OHLC fields, where a row
        must have a dated positive price and usable technical history.
        """
        eligible = pd.Series(True, index=signals.index, dtype="bool")
        reasons = pd.Series("", index=signals.index, dtype="object")

        def reject(mask: pd.Series, reason: str) -> None:
            nonlocal eligible, reasons
            rejected = ~mask
            reasons.loc[rejected] = reasons.loc[rejected].map(
                lambda current: reason if not current else f"{current};{reason}"
            )
            eligible &= mask

        scores = pd.to_numeric(signals.get("final_total_score"), errors="coerce")
        reject(
            scores.map(lambda value: math.isfinite(float(value)) if pd.notna(value) else False),
            "INVALID_SCORE",
        )
        if "current_price" in signals.columns:
            prices = pd.to_numeric(signals["current_price"], errors="coerce")
            reject(
                prices.map(lambda value: math.isfinite(float(value)) and float(value) > 0.0 if pd.notna(value) else False),
                "NO_DATED_PRICE",
            )
        if "current_price_source" in signals.columns:
            reject(
                signals["current_price_source"].fillna("").astype(str)
                != "yahoo_metadata_quote_undated",
                "UNDATED_PRICE",
            )
        if "ohlc_history_usable" in signals.columns:
            reject(
                signals["ohlc_history_usable"].fillna(False).astype(bool),
                "OHLC_HISTORY_UNUSABLE",
            )
        return eligible, reasons

    @staticmethod
    def apply_ranking(signals: pd.DataFrame) -> pd.DataFrame:
        if signals.empty:
            return signals
        ranked = signals.copy()
        eligible, reasons = RankingService._eligibility(ranked)
        ranked["ranking_eligible"] = eligible
        ranked["ranking_reason"] = reasons.where(~eligible, None)
        ranked["ranking_status"] = ranked["ranking_eligible"].map(
            {True: "ELIGIBLE", False: "INELIGIBLE"}
        )
        ranked["rank_in_watchlist"] = pd.Series(pd.NA, index=ranked.index, dtype="Int64")
        ranked["percentile_in_watchlist"] = float("nan")

        eligible_rows = ranked.loc[ranked["ranking_eligible"]].sort_values(
            "final_total_score", ascending=False
        )
        eligible_rows["rank_in_watchlist"] = pd.Series(
            range(1, len(eligible_rows) + 1), index=eligible_rows.index, dtype="Int64"
        )
        eligible_rows["percentile_in_watchlist"] = (
            eligible_rows["final_total_score"].rank(pct=True, ascending=True) * 100
        )
        ineligible_rows = ranked.loc[~ranked["ranking_eligible"]]
        return pd.concat([eligible_rows, ineligible_rows]).reset_index(drop=True)

    @staticmethod
    def top_bottom_tables(signals: pd.DataFrame, size: int = 10) -> dict[str, pd.DataFrame]:
        if signals.empty:
            return {"top": pd.DataFrame(), "bottom": pd.DataFrame()}
        if "ranking_eligible" in signals.columns:
            signals = signals.loc[signals["ranking_eligible"].fillna(False)]
        if signals.empty:
            return {"top": pd.DataFrame(), "bottom": pd.DataFrame()}
        ordered = signals.sort_values("final_total_score", ascending=False)
        return {"top": ordered.head(size), "bottom": ordered.tail(size)}
