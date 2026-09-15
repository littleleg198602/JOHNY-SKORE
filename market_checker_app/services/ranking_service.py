from __future__ import annotations

import pandas as pd

from market_checker_app.release_manifest import (
    ACTIVE_MODEL_VERSION,
    ACTIVE_SCORING_VERSION,
    FEATURE_SET_VERSION,
    build_release_manifest,
)


class RankingService:
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
        ordered = signals.sort_values("final_total_score", ascending=False)
        return {"top": ordered.head(size), "bottom": ordered.tail(size)}
