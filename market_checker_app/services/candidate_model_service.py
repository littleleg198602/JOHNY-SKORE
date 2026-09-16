"""Deterministic shadow baselines and a small PIT logistic candidate model.

The service never mutates the production heuristic score or its ranking.  It
only consumes immutable prediction snapshots, trains on already-resolved labels
that were known before the requested as-of time, and writes an auditable model
artifact for later OOS evaluation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import math
from statistics import median
from typing import Any

import pandas as pd

from market_checker_app.prediction_contract import PRIMARY_TARGET_VERSION


MOMENTUM_BASELINE_MODEL_ID = "momentum_relative_baseline"
MOMENTUM_BASELINE_MODEL_VERSION = "v1"
LOGISTIC_CANDIDATE_MODEL_ID = "pit_logistic_regression"
LOGISTIC_CANDIDATE_MODEL_VERSION = "v1"
CANDIDATE_MODEL_FEATURE_VERSION = "pit_market_features_v1"

FEATURE_PATHS = (
    "market_factors.asset_returns.5d",
    "market_factors.asset_returns.20d",
    "market_factors.asset_returns.60d",
    "market_factors.relative_returns.5d",
    "market_factors.relative_returns.20d",
    "market_factors.relative_returns.60d",
    "market_factors.realized_volatility.20d_annualized",
    "market_factors.drawdown.252d",
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _utc(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return _utc(parsed)


def _json_object(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str):
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}


def _nested_value(payload: Mapping[str, object], path: str) -> float | None:
    current: object = payload
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    try:
        numeric = float(current)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _feature_vector(payload: Mapping[str, object]) -> list[float | None]:
    return [_nested_value(payload, path) for path in FEATURE_PATHS]


def _sigmoid(value: float) -> float:
    bounded = max(-35.0, min(35.0, value))
    return 1.0 / (1.0 + math.exp(-bounded))


def _stable_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def momentum_baseline_probability(payload: Mapping[str, object]) -> tuple[float | None, int]:
    """Return a fixed, transparent probability from momentum/relative inputs."""

    paths_and_scales = (
        ("market_factors.asset_returns.20d", 0.12),
        ("market_factors.asset_returns.60d", 0.22),
        ("market_factors.relative_returns.20d", 0.10),
        ("market_factors.relative_returns.60d", 0.18),
    )
    components = [
        math.tanh(value / scale)
        for path, scale in paths_and_scales
        if (value := _nested_value(payload, path)) is not None
    ]
    if not components:
        return None, 0
    return _sigmoid(1.5 * sum(components) / len(components)), len(components)


def _training_rows(
    snapshots: pd.DataFrame,
    *,
    as_of: datetime,
    target_version: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in snapshots.to_dict(orient="records"):
        if str(row.get("label_status") or "").upper() != "RESOLVED":
            continue
        if str(row.get("target_version") or "") != target_version:
            continue
        snapshot_at = _parse_datetime(row.get("as_of"))
        label_at = _parse_datetime(row.get("target_observed_at"))
        try:
            target = float(row.get("target_value"))
        except (TypeError, ValueError):
            continue
        if (
            snapshot_at is None
            or label_at is None
            or snapshot_at >= as_of
            or label_at > as_of
            or not math.isfinite(target)
        ):
            continue
        payload = _json_object(row.get("feature_payload_json"))
        rows.append(
            {
                "snapshot_id": str(row.get("snapshot_id") or ""),
                "as_of": snapshot_at,
                "target": target,
                "features": _feature_vector(payload),
            }
        )
    return sorted(rows, key=lambda item: (item["as_of"], item["snapshot_id"]))


def _fit_logistic(
    rows: Sequence[Mapping[str, object]],
    *,
    l2: float,
    iterations: int,
    learning_rate: float,
) -> dict[str, object] | None:
    if not rows:
        return None
    vectors = [list(item["features"]) for item in rows]
    labels = [1.0 if float(item["target"]) > 0.0 else 0.0 for item in rows]
    if min(labels) == max(labels):
        return None

    medians: list[float] = []
    means: list[float] = []
    scales: list[float] = []
    for index in range(len(FEATURE_PATHS)):
        observed = [vector[index] for vector in vectors if vector[index] is not None]
        if not observed:
            return None
        fill = float(median(observed))
        completed = [float(value if value is not None else fill) for value in (vector[index] for vector in vectors)]
        mean = sum(completed) / len(completed)
        variance = sum((value - mean) ** 2 for value in completed) / len(completed)
        scale = math.sqrt(variance)
        if scale <= 1e-12:
            return None
        medians.append(fill)
        means.append(mean)
        scales.append(scale)

    matrix = [
        [
            ((float(value) if value is not None else medians[index]) - means[index])
            / scales[index]
            for index, value in enumerate(vector)
        ]
        for vector in vectors
    ]
    weights = [0.0] * len(FEATURE_PATHS)
    intercept = 0.0
    sample_count = len(matrix)
    for _ in range(iterations):
        gradients = [0.0] * len(weights)
        intercept_gradient = 0.0
        for vector, label in zip(matrix, labels):
            error = _sigmoid(intercept + sum(weight * value for weight, value in zip(weights, vector))) - label
            intercept_gradient += error
            for index, value in enumerate(vector):
                gradients[index] += error * value
        intercept -= learning_rate * intercept_gradient / sample_count
        for index, gradient in enumerate(gradients):
            weights[index] -= learning_rate * (
                gradient / sample_count + l2 * weights[index]
            )
    return {
        "feature_names": list(FEATURE_PATHS),
        "imputation_medians": medians,
        "scaler_means": means,
        "scaler_scales": scales,
        "coefficients": weights,
        "intercept": intercept,
        "positive_label_rule": "target_value > 0",
        "l2": l2,
        "iterations": iterations,
        "learning_rate": learning_rate,
    }


def _candidate_probability(
    payload: Mapping[str, object], artifact: Mapping[str, object]
) -> tuple[float, float]:
    vector = _feature_vector(payload)
    medians = [float(value) for value in artifact["imputation_medians"]]
    means = [float(value) for value in artifact["scaler_means"]]
    scales = [float(value) for value in artifact["scaler_scales"]]
    coefficients = [float(value) for value in artifact["coefficients"]]
    usable = sum(value is not None for value in vector)
    normalized = [
        ((float(value) if value is not None else medians[index]) - means[index])
        / scales[index]
        for index, value in enumerate(vector)
    ]
    probability = _sigmoid(
        float(artifact["intercept"])
        + sum(weight * value for weight, value in zip(coefficients, normalized))
    )
    return probability, usable / len(FEATURE_PATHS)


def build_candidate_model_report(
    snapshots: pd.DataFrame,
    *,
    prediction_snapshot_ids: Sequence[str],
    as_of: datetime,
    target_version: str = PRIMARY_TARGET_VERSION,
    minimum_training_samples: int = 200,
    l2: float = 0.05,
    iterations: int = 400,
    learning_rate: float = 0.15,
) -> dict[str, object]:
    """Build a shadow-only model comparison for immutable snapshot IDs.

    Training observations must have both an older snapshot time and a resolved
    outcome no later than ``as_of``.  The current production score/ranking is
    absent from both the inputs and outputs on purpose.
    """

    as_of = _utc(as_of)
    requested_ids = {str(value).strip() for value in prediction_snapshot_ids if str(value).strip()}
    prediction_rows = [
        row
        for row in snapshots.to_dict(orient="records")
        if str(row.get("snapshot_id") or "") in requested_ids
    ]
    training = _training_rows(snapshots, as_of=as_of, target_version=target_version)
    positive_count = sum(float(item["target"]) > 0.0 for item in training)
    negative_count = len(training) - positive_count
    artifact_payload = _fit_logistic(
        training,
        l2=l2,
        iterations=iterations,
        learning_rate=learning_rate,
    ) if len(training) >= minimum_training_samples else None

    artifact: dict[str, object] | None = None
    status = "INSUFFICIENT_DATA"
    reason = "MINIMUM_TRAINING_SAMPLES_NOT_MET"
    if artifact_payload is not None:
        training_ids = [str(item["snapshot_id"]) for item in training]
        artifact = {
            "model_id": LOGISTIC_CANDIDATE_MODEL_ID,
            "model_version": LOGISTIC_CANDIDATE_MODEL_VERSION,
            "feature_version": CANDIDATE_MODEL_FEATURE_VERSION,
            "target_version": target_version,
            "trained_as_of": as_of.isoformat(),
            "training_start": training[0]["as_of"].isoformat(),
            "training_end": training[-1]["as_of"].isoformat(),
            "training_sample_count": len(training),
            "positive_sample_count": positive_count,
            "negative_sample_count": negative_count,
            "training_snapshot_ids": training_ids,
            "parameters": artifact_payload,
        }
        artifact["artifact_id"] = _stable_hash(artifact)
        status = "TRAINED"
        reason = ""
    elif len(training) >= minimum_training_samples:
        reason = "INSUFFICIENT_CLASS_OR_FEATURE_VARIATION"

    predictions: list[dict[str, object]] = []
    for row in prediction_rows:
        snapshot_id = str(row.get("snapshot_id") or "")
        payload = _json_object(row.get("feature_payload_json"))
        baseline_probability, momentum_feature_count = momentum_baseline_probability(payload)
        prediction: dict[str, object] = {
            "snapshot_id": snapshot_id,
            "ticker": str(row.get("ticker") or "").upper(),
            "as_of": str(row.get("as_of") or ""),
            "momentum_baseline_model_id": MOMENTUM_BASELINE_MODEL_ID,
            "momentum_baseline_model_version": MOMENTUM_BASELINE_MODEL_VERSION,
            "momentum_probability_up": baseline_probability,
            "momentum_feature_count": momentum_feature_count,
            "candidate_model_id": LOGISTIC_CANDIDATE_MODEL_ID,
            "candidate_model_version": LOGISTIC_CANDIDATE_MODEL_VERSION,
            "candidate_probability_up": None,
            "candidate_feature_coverage": 0.0,
            "selected_for_analysis": "MOMENTUM_BASELINE",
            "selection_reason": "CANDIDATE_MODEL_UNAVAILABLE",
        }
        if artifact is not None:
            probability, coverage = _candidate_probability(
                payload, artifact["parameters"])
            if coverage > 0.0:
                prediction.update(
                    {
                        "candidate_probability_up": probability,
                        "candidate_feature_coverage": coverage,
                        "selected_for_analysis": "CANDIDATE_MODEL",
                        "selection_reason": "SHADOW_ONLY_NOT_CONNECTED_TO_RANKING",
                        "artifact_id": artifact["artifact_id"],
                    }
                )
            else:
                prediction["selection_reason"] = "CANDIDATE_FEATURES_MISSING"
        predictions.append(prediction)

    def ranking_probability(prediction: Mapping[str, object]) -> float:
        candidate_probability = prediction.get("candidate_probability_up")
        momentum_probability = prediction.get("momentum_probability_up")
        if candidate_probability is not None:
            return float(candidate_probability)
        if momentum_probability is not None:
            return float(momentum_probability)
        return -1.0

    for index, prediction in enumerate(
        sorted(
            predictions,
            key=lambda item: (
                -ranking_probability(item),
                str(item["ticker"]),
            ),
        ),
        start=1,
    ):
        prediction["shadow_rank"] = index

    return {
        "status": status,
        "reason": reason,
        "as_of": as_of.isoformat(),
        "target_version": target_version,
        "minimum_training_samples": int(minimum_training_samples),
        "training_sample_count": len(training),
        "positive_sample_count": positive_count,
        "negative_sample_count": negative_count,
        "artifact": artifact,
        "predictions": predictions,
        "analysis_only": True,
        "ranking_modified": False,
    }


__all__ = [
    "CANDIDATE_MODEL_FEATURE_VERSION",
    "LOGISTIC_CANDIDATE_MODEL_ID",
    "LOGISTIC_CANDIDATE_MODEL_VERSION",
    "MOMENTUM_BASELINE_MODEL_ID",
    "MOMENTUM_BASELINE_MODEL_VERSION",
    "build_candidate_model_report",
    "momentum_baseline_probability",
]
