"""Predeclared layer comparisons on exactly matched out-of-sample cohorts."""
from __future__ import annotations

from collections.abc import Mapping
import json
import pandas as pd

from market_checker_app.services.candidate_model_service import FEATURE_PATHS, _nested_value
from market_checker_app.services.candidate_model_evaluation_service import evaluate_candidate_walk_forward, summarize_candidate_samples
from market_checker_app.services.sec_fundamental_feature_service import SEC_FUNDAMENTAL_FEATURE_VERSION

LAYER_PATHS = {
    "sec": ("sec_fundamentals.values.revenue_yoy_pct", "sec_fundamentals.values.operating_margin_pct", "sec_fundamentals.values.debt_to_assets_ratio"),
    "news": ("news.weighted_sentiment_avg", "news.news_confidence"),
    "macro": ("macro_features.VIX", "macro_features.T10Y2Y", "macro_features.CPI_YOY", "macro_features.INDPRO_YOY"),
}


def evaluate_layer_ablation(snapshots: pd.DataFrame, **evaluation_options: object) -> dict[str, object]:
    """Each layer is tested against price-only on the same available rows.

    No feature selection by observed performance; no production model switch.
    The three comparisons are exploratory and do not constitute a promotion.
    """
    variants = {}
    for layer, extra_paths in LAYER_PATHS.items():
        eligible = []
        for row in snapshots.to_dict(orient="records"):
            raw = row.get("feature_payload_json")
            try:
                payload = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, Mapping):
                continue
            if layer == "sec" and payload.get("sec_fundamentals", {}).get("feature_version") != SEC_FUNDAMENTAL_FEATURE_VERSION:
                continue
            if layer == "news" and not payload.get("news", {}).get("news_count_total", 0):
                continue
            if all(_nested_value(payload, path) is not None for path in (*FEATURE_PATHS, *extra_paths)):
                eligible.append(row)
        frame = pd.DataFrame(eligible)
        if frame.empty:
            variants[layer] = {"status": "INSUFFICIENT_DATA", "reason": "NO_COMPLETE_MATCHED_LAYER_HISTORY", "sample_count": 0}
            continue
        baseline = evaluate_candidate_walk_forward(frame, **evaluation_options)
        augmented = evaluate_candidate_walk_forward(
            frame, feature_paths=(*FEATURE_PATHS, *extra_paths),
            feature_variant="market_plus_" + layer, **evaluation_options,
        )
        base_predictions = {row["snapshot_id"]: row for row in baseline["samples"]}
        matched = []
        for row in augmented["samples"]:
            if row["snapshot_id"] in base_predictions:
                matched.append({**row, "momentum_probability_up": base_predictions[row["snapshot_id"]]["candidate_probability_up"]})
        metrics = summarize_candidate_samples(matched) if matched else None
        sufficient = (metrics is not None
                      and metrics["sample_count"] >= int(evaluation_options.get("minimum_evaluation_samples", 200))
                      and metrics["distinct_weeks"] >= int(evaluation_options.get("minimum_weeks", 12)))
        variants[layer] = {
            "status": "EVALUATED" if sufficient else "INSUFFICIENT_DATA",
            "reason": "" if sufficient else "MINIMUM_MATCHED_HISTORY_NOT_MET",
            "baseline": "market_logistic", "augmented": "market_plus_" + layer,
            "feature_paths": list(extra_paths), "matched_metrics": metrics,
            "sample_count": len(matched),
            "matched_snapshot_ids": [row["snapshot_id"] for row in matched],
        }
    return {
        "report_version": "layer_ablation_v1", "variants": variants,
        "status": "EVALUATED" if all(row["status"] == "EVALUATED" for row in variants.values()) else "INSUFFICIENT_DATA",
        "analysis_only": True, "ranking_modified": False, "activation_allowed": False,
        "comparison_policy": "PREDECLARED_PAIRWISE_MATCHED_COHORTS_EXPLORATORY_NO_PROMOTION",
    }
