"""Walk-forward evaluation for the OPL-012 shadow candidate model.

This module deliberately evaluates only immutable point-in-time snapshots.  It
never reads production ranking/actions and never selects a model for trading.
Every weekly candidate prediction is rebuilt using labels that were available
strictly before that week's prediction time.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
import math
import random
from statistics import mean

import pandas as pd

from market_checker_app.prediction_contract import PRIMARY_TARGET_VERSION
from market_checker_app.services.candidate_model_service import (
    LOGISTIC_CANDIDATE_MODEL_ID,
    LOGISTIC_CANDIDATE_MODEL_VERSION,
    MOMENTUM_BASELINE_MODEL_ID,
    MOMENTUM_BASELINE_MODEL_VERSION,
    build_candidate_model_report,
    FEATURE_PATHS,
    DEFAULT_COST_HURDLE,
)
from market_checker_app.services.portfolio_backtest_service import (
    build_cost_aware_portfolio_backtest,
)


CANDIDATE_EVALUATION_REPORT_VERSION = "candidate_walk_forward_evaluation_v2"


def _week(value: datetime) -> str:
    year, week, _ = value.isocalendar()
    return f"{year}-W{week:02d}"


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sector(row: Mapping[str, object]) -> str:
    for key in ("sector", "sector_name"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    payload = row.get("feature_payload_json")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}
    if isinstance(payload, Mapping):
        yahoo = payload.get("yahoo")
        yahoo_data = yahoo.get("data") if isinstance(yahoo, Mapping) else {}
        for source in (payload.get("market"), yahoo_data, payload):
            if not isinstance(source, Mapping):
                continue
            value = str(source.get("sector") or "").strip()
            if value:
                return value
    return "UNKNOWN"


def _rank(values: Sequence[float]) -> list[float]:
    """Average ranks for ties, without a scipy dependency."""

    ordered = sorted(enumerate(values), key=lambda item: item[1])
    result = [0.0] * len(values)
    position = 0
    while position < len(ordered):
        end = position + 1
        while end < len(ordered) and ordered[end][1] == ordered[position][1]:
            end += 1
        average_rank = (position + 1 + end) / 2.0
        for index, _ in ordered[position:end]:
            result[index] = average_rank
        position = end
    return result


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_mean = mean(left)
    right_mean = mean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_scale = math.sqrt(sum((a - left_mean) ** 2 for a in left))
    right_scale = math.sqrt(sum((b - right_mean) ** 2 for b in right))
    if left_scale <= 1e-12 or right_scale <= 1e-12:
        return None
    return numerator / (left_scale * right_scale)


def _ranking_ic(probabilities: Sequence[float], outcomes: Sequence[float]) -> float | None:
    return _pearson(_rank(probabilities), _rank(outcomes))


def _calibration(samples: Sequence[Mapping[str, object]], probability_key: str, bins: int) -> dict[str, object]:
    buckets: list[list[tuple[float, float]]] = [[] for _ in range(bins)]
    for sample in samples:
        probability = float(sample[probability_key])
        outcome = float(sample["outcome_up"])
        index = min(bins - 1, max(0, int(probability * bins)))
        buckets[index].append((probability, outcome))
    details: list[dict[str, object]] = []
    ece = 0.0
    count = len(samples)
    for index, bucket in enumerate(buckets):
        if not bucket:
            continue
        avg_probability = mean(value[0] for value in bucket)
        empirical_rate = mean(value[1] for value in bucket)
        weight = len(bucket) / count
        ece += weight * abs(avg_probability - empirical_rate)
        details.append(
            {
                "lower": index / bins,
                "upper": (index + 1) / bins,
                "sample_count": len(bucket),
                "mean_probability": avg_probability,
                "observed_positive_rate": empirical_rate,
            }
        )
    return {"expected_calibration_error": ece, "bins": details}


def _metric_for_week(samples: Sequence[Mapping[str, object]], probability_key: str, top_fraction: float) -> dict[str, object]:
    probabilities = [float(sample[probability_key]) for sample in samples]
    outcomes = [float(sample["target_value"]) for sample in samples]
    outcomes_up = [float(sample["outcome_up"]) for sample in samples]
    top_count = max(1, math.ceil(len(samples) * top_fraction))
    top = sorted(samples, key=lambda item: (-float(item[probability_key]), str(item["ticker"])))[:top_count]
    return {
        "sample_count": len(samples),
        "brier_score": mean((probability - outcome) ** 2 for probability, outcome in zip(probabilities, outcomes_up)),
        "ranking_ic": _ranking_ic(probabilities, outcomes),
        "top_decile_excess_return": mean(float(sample["target_value"]) for sample in top),
        "directional_accuracy": mean(
            float((probability >= 0.5) == bool(outcome))
            for probability, outcome in zip(probabilities, outcomes_up)
        ),
    }


def _weekly_interval(values: Sequence[float | None], *, seed: int = 13, draws: int = 1000) -> dict[str, object]:
    usable = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not usable:
        return {"method": "weekly_bootstrap", "week_count": 0, "lower": None, "upper": None}
    if len(usable) == 1:
        return {"method": "weekly_bootstrap", "week_count": 1, "lower": None, "upper": None}
    generator = random.Random(seed)
    estimates = sorted(
        mean(generator.choice(usable) for _ in usable)
        for _ in range(draws)
    )
    return {
        "method": "weekly_bootstrap",
        "week_count": len(usable),
        "lower": estimates[int(0.025 * (draws - 1))],
        "upper": estimates[int(0.975 * (draws - 1))],
    }


def summarize_candidate_samples(
    samples: Sequence[Mapping[str, object]],
    *,
    top_fraction: float = 0.10,
    calibration_bins: int = 10,
) -> dict[str, object]:
    """Summarize already-created OOS samples, with week-level uncertainty."""

    if not 0.0 < top_fraction <= 1.0:
        raise ValueError("top_fraction must be in (0, 1]")
    if calibration_bins < 2:
        raise ValueError("calibration_bins must be at least 2")
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for sample in samples:
        stamp = _parse_datetime(sample.get("as_of") or sample["week"])
        grouped[_week(stamp) if stamp else str(sample["week"])].append(sample)
    weekly: list[dict[str, object]] = []
    baseline_values: dict[str, list[float | None]] = defaultdict(list)
    candidate_values: dict[str, list[float | None]] = defaultdict(list)
    for week in sorted(grouped):
        week_samples = grouped[week]
        baseline = _metric_for_week(week_samples, "momentum_probability_up", top_fraction)
        candidate = _metric_for_week(week_samples, "candidate_probability_up", top_fraction)
        weekly.append({"week": week, "sample_count": len(week_samples), "baseline": baseline, "candidate": candidate})
        for key, value in baseline.items():
            baseline_values[key].append(value if isinstance(value, (int, float)) else None)
        for key, value in candidate.items():
            candidate_values[key].append(value if isinstance(value, (int, float)) else None)

    def aggregate(values: Mapping[str, Sequence[float | None]], probability_key: str) -> dict[str, object]:
        return {
            "brier_score": mean(value for value in values["brier_score"] if value is not None),
            "ranking_ic": mean(value for value in values["ranking_ic"] if value is not None) if any(value is not None for value in values["ranking_ic"]) else None,
            "top_decile_excess_return": mean(value for value in values["top_decile_excess_return"] if value is not None),
            "directional_accuracy": mean(value for value in values["directional_accuracy"] if value is not None),
            "calibration": _calibration(samples, probability_key, calibration_bins),
            "weekly_intervals": {
                key: _weekly_interval(values[key])
                for key in ("brier_score", "ranking_ic", "top_decile_excess_return", "directional_accuracy")
            },
        }

    baseline = aggregate(baseline_values, "momentum_probability_up")
    candidate = aggregate(candidate_values, "candidate_probability_up")
    return {
        "sample_count": len(samples),
        "distinct_weeks": len(weekly),
        "weekly_metrics": weekly,
        "baseline": baseline,
        "candidate": candidate,
        "candidate_minus_baseline": {
            key: (
                None
                if baseline[key] is None or candidate[key] is None
                else candidate[key] - baseline[key]
            )
            for key in ("brier_score", "ranking_ic", "top_decile_excess_return", "directional_accuracy")
        },
    }


def evaluate_candidate_walk_forward(
    snapshots: pd.DataFrame,
    *,
    target_version: str = PRIMARY_TARGET_VERSION,
    minimum_training_samples: int = 200,
    minimum_evaluation_samples: int = 200,
    minimum_weeks: int = 12,
    top_fraction: float = 0.10,
    calibration_bins: int = 10,
    iterations: int = 400,
    feature_paths: Sequence[str] = FEATURE_PATHS,
    feature_variant: str = "market",
    positive_label_hurdle: float = DEFAULT_COST_HURDLE,
) -> dict[str, object]:
    """Evaluate the candidate against its momentum baseline without leakage.

    At most one label horizon per ticker is evaluated at a time.  Candidate
    fitting is delegated to the OPL-012 service with the weekly as-of cutoff,
    so a label observed later cannot enter an earlier fit.
    """

    rows: list[dict[str, object]] = []
    for raw in snapshots.to_dict(orient="records"):
        if str(raw.get("target_version") or "") != target_version:
            continue
        if str(raw.get("label_status") or "").upper() != "RESOLVED":
            continue
        as_of = _parse_datetime(raw.get("as_of"))
        observed = _parse_datetime(raw.get("target_observed_at"))
        try:
            target = float(raw.get("target_value"))
        except (TypeError, ValueError):
            continue
        if as_of is None or observed is None or observed <= as_of or not math.isfinite(target):
            continue
        row = dict(raw)
        row["_as_of"] = as_of
        row["_observed"] = observed
        rows.append(row)
    rows.sort(key=lambda row: (row["_as_of"], str(row.get("ticker") or ""), str(row.get("snapshot_id") or "")))

    periods: dict[datetime, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        periods[row["_as_of"]].append(row)
    last_label_end_by_ticker: dict[str, datetime] = {}
    overlap_excluded = 0
    insufficient_periods = 0
    samples: list[dict[str, object]] = []
    seen_cohorts: set[tuple[str, str]] = set()
    source_frame = snapshots.copy()
    for as_of, period_rows in sorted(periods.items()):
        eligible: list[dict[str, object]] = []
        selected_tickers: set[str] = set()
        for row in period_rows:
            ticker = str(row.get("ticker") or "").upper()
            prior_label_end = last_label_end_by_ticker.get(ticker)
            cohort = (ticker, _week(as_of))
            if ticker in selected_tickers or cohort in seen_cohorts or (
                prior_label_end is not None and as_of < prior_label_end
            ):
                overlap_excluded += 1
                continue
            eligible.append(row)
            selected_tickers.add(ticker)
            seen_cohorts.add(cohort)
        if not eligible:
            continue
        report = build_candidate_model_report(
            source_frame,
            prediction_snapshot_ids=[str(row.get("snapshot_id") or "") for row in eligible],
            as_of=as_of,
            target_version=target_version,
            minimum_training_samples=minimum_training_samples,
            iterations=iterations,
            feature_paths=feature_paths,
            feature_variant=feature_variant,
            positive_label_hurdle=positive_label_hurdle,
        )
        if report.get("status") != "TRAINED":
            insufficient_periods += 1
            continue
        predictions = {
            str(item.get("snapshot_id") or ""): item
            for item in report.get("predictions", [])
            if isinstance(item, Mapping)
        }
        for row in eligible:
            ticker = str(row.get("ticker") or "").upper()
            prediction = predictions.get(str(row.get("snapshot_id") or ""))
            if not prediction:
                continue
            baseline = prediction.get("momentum_probability_up")
            candidate = prediction.get("candidate_probability_up")
            if baseline is None or candidate is None:
                continue
            target = float(row["target_value"])
            samples.append(
                {
                    "snapshot_id": str(row.get("snapshot_id") or ""),
                    "ticker": ticker,
                    "sector": _sector(row),
                    "week": _week(as_of),
                    "as_of": as_of.isoformat(),
                    "target_value": target,
                    "outcome_up": 1.0 if target > positive_label_hurdle else 0.0,
                    "momentum_probability_up": float(baseline),
                    "candidate_probability_up": float(candidate),
                    "training_sample_count": int(report.get("training_sample_count") or 0),
                }
            )
            last_label_end_by_ticker[ticker] = row["_observed"]
    if not samples:
        return {
            "report_version": CANDIDATE_EVALUATION_REPORT_VERSION,
            "status": "INSUFFICIENT_DATA",
            "reason": "NO_WALK_FORWARD_PERIOD_WITH_TRAINED_CANDIDATE",
            "target_version": target_version,
            "minimum_training_samples": minimum_training_samples,
            "minimum_evaluation_samples": minimum_evaluation_samples,
            "minimum_weeks": minimum_weeks,
            "overlap_excluded_count": overlap_excluded,
            "insufficient_training_period_count": insufficient_periods,
            "analysis_only": True,
            "ranking_modified": False,
            "activation_allowed": False,
            "samples": [],
        }
    metrics = summarize_candidate_samples(
        samples,
        top_fraction=top_fraction,
        calibration_bins=calibration_bins,
    )
    cost_aware_backtest = build_cost_aware_portfolio_backtest(
        samples,
        top_fraction=top_fraction,
    )
    status = "EVALUATED"
    reason = ""
    if metrics["sample_count"] < minimum_evaluation_samples or metrics["distinct_weeks"] < minimum_weeks:
        status = "INSUFFICIENT_DATA"
        reason = "MINIMUM_EVALUATION_HISTORY_NOT_MET"
    sector_metrics: dict[str, dict[str, object]] = {}
    for sector in sorted({str(sample["sector"]) for sample in samples}):
        sector_samples = [sample for sample in samples if sample["sector"] == sector]
        sector_metrics[sector] = summarize_candidate_samples(sector_samples, top_fraction=top_fraction, calibration_bins=calibration_bins)
    return {
        "report_version": CANDIDATE_EVALUATION_REPORT_VERSION,
        "status": status,
        "reason": reason,
        "target_version": target_version,
        "baseline_model": {"model_id": MOMENTUM_BASELINE_MODEL_ID, "model_version": MOMENTUM_BASELINE_MODEL_VERSION},
        "candidate_model": {"model_id": LOGISTIC_CANDIDATE_MODEL_ID, "model_version": LOGISTIC_CANDIDATE_MODEL_VERSION},
        "minimum_training_samples": minimum_training_samples,
        "minimum_evaluation_samples": minimum_evaluation_samples,
        "minimum_weeks": minimum_weeks,
        "positive_label_hurdle": positive_label_hurdle,
        "overlap_excluded_count": overlap_excluded,
        "insufficient_training_period_count": insufficient_periods,
        "analysis_only": True,
        "ranking_modified": False,
        "activation_allowed": False,
        "metrics": metrics,
        "sector_metrics": sector_metrics,
        "cost_aware_portfolio_backtest": cost_aware_backtest,
        "samples": samples,
    }


__all__ = [
    "CANDIDATE_EVALUATION_REPORT_VERSION",
    "evaluate_candidate_walk_forward",
    "summarize_candidate_samples",
]
