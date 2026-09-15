from __future__ import annotations

import math

import pandas as pd

from market_checker_app.release_manifest import (
    ACTIVE_MODEL_VERSION,
    ACTIVE_SCORING_VERSION,
    FEATURE_SET_VERSION,
    build_release_manifest,
)


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
        ranked = signals.sort_values(
            "final_total_score",
            ascending=False,
        ).reset_index(drop=True)

        # The pipeline historically constructed rows with the frozen v2.1
        # baseline identifier before ranking. Final analytical results are
        # stamped here with the active scoring contract so SQLite/run outputs
        # cannot mislabel post-v2.1 logic as the legacy baseline.
        manifest = build_release_manifest()
        ranked["scoring_version"] = ACTIVE_SCORING_VERSION
        ranked["model_version"] = ACTIVE_MODEL_VERSION
        ranked["feature_set_version"] = FEATURE_SET_VERSION
        ranked["target_version"] = manifest["target_version"]
        ranked["code_sha"] = manifest["code_sha"]
        ranked["config_hash"] = manifest["config_hash"]
        ranked["release_manifest_hash"] = manifest["manifest_hash"]

        ranked["rank_in_watchlist"] = ranked.index + 1
        ranked["percentile_in_watchlist"] = (
            ranked["final_total_score"].rank(pct=True, ascending=True) * 100
        )
        return ranked

    @staticmethod
    def top_bottom_tables(
        signals: pd.DataFrame,
        size: int = 10,
    ) -> dict[str, pd.DataFrame]:
        if signals.empty:
            return {"top": pd.DataFrame(), "bottom": pd.DataFrame()}
        if "ranking_eligible" in signals.columns:
            signals = signals.loc[signals["ranking_eligible"].fillna(False)]
        if signals.empty:
            return {"top": pd.DataFrame(), "bottom": pd.DataFrame()}
        ordered = signals.sort_values("final_total_score", ascending=False)
        return {"top": ordered.head(size), "bottom": ordered.tail(size)}
