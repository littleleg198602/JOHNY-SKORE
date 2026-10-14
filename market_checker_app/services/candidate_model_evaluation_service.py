"""Walk-forward evaluation for the OPL-012 shadow candidate model.

The explicit strict path checks recorded price-window availability before each
fit and at the held-out evaluation cutoff. The compatible legacy path remains
an unverified label-timestamp diagnostic. Neither path selects a trading model.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
import math
import random
from pathlib import Path
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
from market_checker_app.services.specialist_label_availability_service import (
    MAX_LABEL_OBSERVATIONS, audit_snapshot_label, prepare_label_observations,
)


CANDIDATE_EVALUATION_REPORT_VERSION = "candidate_walk_forward_evaluation_v2"
STRICT_CANDIDATE_EVALUATION_REPORT_VERSION = "candidate_walk_forward_recorded_price_gate_v1"


def _strict_time(value: object) -> datetime | None:
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            return None
        return stamp.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


class _RecordedPriceGate:
    def __init__(self, snapshots: pd.DataFrame, *, as_of: datetime, label_observations,
                 label_archive_directory: Path | None, maximum_label_observations: int):
        self.cutoff = _strict_time(as_of)
        if self.cutoff is None:
            raise ValueError("Strict candidate evaluation as_of needs an explicit timezone")
        self.records = snapshots.to_dict(orient="records")
        self.id_counts = Counter(str(row.get("snapshot_id") or "") for row in self.records)
        self.observations, self.archive = prepare_label_observations(label_observations,
            directory=label_archive_directory, maximum=maximum_label_observations)
        self.heldout_reasons, self.rejected_snapshots, self.origins = Counter(), Counter(), Counter()
        self.heldout_audits, self.folds = {}, []

    def allowed_snapshot(self, row: Mapping) -> bool:
        identifier = str(row.get("snapshot_id") or "")
        stamp = _strict_time(row.get("as_of"))
        if self.id_counts[identifier] != 1:
            reason = "DUPLICATE_SNAPSHOT_ID"
        elif stamp is None or _strict_time(row.get("target_observed_at")) is None:
            reason = "INVALID_SNAPSHOT_OR_LABEL_TIME"
        elif stamp > self.cutoff:
            reason = "SNAPSHOT_AFTER_REPORT_CUTOFF"
        else:
            return True
        self.rejected_snapshots[reason] += 1
        return False

    def heldout(self, row: Mapping) -> dict:
        identifier = str(row.get("snapshot_id") or "")
        audited = audit_snapshot_label(row, self.observations.get(identifier, ()), as_of=self.cutoff)
        self.heldout_reasons[audited["reason"] or audited["status"]] += 1
        if audited["status"] == "ELIGIBLE_RECORDED_LABEL":
            self.heldout_audits[identifier] = audited
            self.origins[audited.get("evidence_origin") or "UNKNOWN"] += 1
        return audited

    def training_frame(self, *, prediction_at: datetime, prediction_rows: list[dict], target_version: str):
        training, audits, reasons = [], {}, Counter()
        for raw in self.records:
            identifier = str(raw.get("snapshot_id") or "")
            stamp = _strict_time(raw.get("as_of"))
            if (self.id_counts[identifier] != 1 or stamp is None or stamp >= prediction_at
                    or str(raw.get("target_version") or "") != target_version
                    or str(raw.get("label_status") or "").upper() != "RESOLVED"):
                continue
            # Audit versions at this prediction clock, not the later report
            # cutoff: future captures/corrections cannot rewrite an earlier fit.
            audited = audit_snapshot_label(raw, self.observations.get(identifier, ()), as_of=prediction_at)
            if audited["status"] != "ELIGIBLE_RECORDED_LABEL":
                reasons[audited["reason"]] += 1
                continue
            if _strict_time(audited["usable_at"]) >= prediction_at:
                reasons["LABEL_NOT_STRICTLY_BEFORE_PREDICTION"] += 1
                continue
            training.append(dict(raw))
            audits[identifier] = audited
        predictions = []
        for row in prediction_rows:
            feature_row = {key: value for key, value in row.items() if not key.startswith("_")}
            # Only prediction features go to the fitter; held-out outcomes are
            # kept outside it even though they are known at evaluation cutoff.
            feature_row.update(label_status="PENDING", target_value=None, target_observed_at=None)
            predictions.append(feature_row)
        return pd.DataFrame(training + predictions), audits, reasons

    def record_fold(self, *, prediction_at: datetime, report: Mapping, audits: Mapping, reasons: Counter):
        artifact = report.get("artifact")
        selected = list(artifact.get("training_snapshot_ids", ())) if isinstance(artifact, Mapping) else []
        selected_audits = {identifier: audits[identifier] for identifier in selected if identifier in audits}
        latest = max((_strict_time(audit["usable_at"]) for audit in selected_audits.values()), default=None)
        self.folds.append({"as_of": prediction_at.isoformat(), "model_status": report.get("status"),
            "audited_training_label_count": len(audits), "training_sample_count": report.get("training_sample_count", 0),
            "training_snapshot_ids": selected, "prediction_snapshot_ids": [str(item.get("snapshot_id") or "")
                for item in report.get("predictions", ()) if isinstance(item, Mapping)],
            "latest_training_label_available_at": latest.isoformat() if latest else None,
            "training_rejection_reasons": dict(sorted(reasons.items())),
            "training_label_evidence": {identifier: {"observation_hash": audit["observation_hash"],
                "usable_at": audit["usable_at"], "price_capture_hashes": audit["price_capture_hashes"],
                "evidence_origin": audit.get("evidence_origin")} for identifier, audit in sorted(selected_audits.items())}})

    def report(self, result: dict) -> dict:
        result.update(report_version=STRICT_CANDIDATE_EVALUATION_REPORT_VERSION,
            report_type="exploratory_recorded_price_gated_candidate_walk_forward",
            generated_at=datetime.now(timezone.utc).isoformat(), as_of=self.cutoff.isoformat(),
            label_price_gate_applied=True, label_price_provenance_verified=False,
            publisher_authenticity_verified=False, out_of_sample_evaluation_verified=False,
            feature_provenance_verified=False, activation_allowed=False, ranking_modified=False,
            folds=self.folds, label_availability={"archive": self.archive,
                "input_snapshot_count": len(self.records),
                "heldout_label_reason_counts": dict(sorted(self.heldout_reasons.items())),
                "eligible_heldout_label_count": len(self.heldout_audits),
                "eligible_evidence_origin_counts": dict(sorted(self.origins.items())),
                "rejected_snapshot_reasons": dict(sorted(self.rejected_snapshots.items())),
                "training_policy": "recorded label usable_at strictly before each prediction",
                "evaluation_policy": "recorded held-out label usable_at at or before evaluation cutoff",
                "fixtures_count_as_live": False})
        if result["status"] == "EVALUATED":
            result["status"] = "EVALUATED_EXPLORATORY"
        elif not self.heldout_audits:
            result["reason"] = "NO_RECORDED_PRICE_LABELS_AT_EVALUATION_CUTOFF"
        return result


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
    """Summarize supplied shadow samples, without verifying their provenance."""

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


def _evaluate_candidate_walk_forward(
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
    price_gate: _RecordedPriceGate | None = None,
) -> dict[str, object]:
    """Build the compatible candidate/baseline walk-forward samples.

    At most one label horizon per ticker is evaluated at a time.  Candidate
    fitting uses the unchanged OPL-012 service. The strict caller additionally
    supplies a recorded-price gate rather than relying on scalar label clocks.
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
        if price_gate is not None:
            if not price_gate.allowed_snapshot(raw):
                continue
            audited = price_gate.heldout(raw)
            if audited["status"] != "ELIGIBLE_RECORDED_LABEL":
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
        fitting_frame, audits, reasons = (price_gate.training_frame(prediction_at=as_of,
            prediction_rows=eligible, target_version=target_version) if price_gate is not None
            else (source_frame, {}, Counter()))
        report = build_candidate_model_report(
            fitting_frame,
            prediction_snapshot_ids=[str(row.get("snapshot_id") or "") for row in eligible],
            as_of=as_of,
            target_version=target_version,
            minimum_training_samples=minimum_training_samples,
            iterations=iterations,
            feature_paths=feature_paths,
            feature_variant=feature_variant,
            positive_label_hurdle=positive_label_hurdle,
        )
        if price_gate is not None:
            price_gate.record_fold(prediction_at=as_of, report=report, audits=audits, reasons=reasons)
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
            if price_gate is not None:
                audited = price_gate.heldout_audits[str(row.get("snapshot_id") or "")]
                samples[-1].update(label_usable_at=audited["usable_at"],
                    label_observation_hash=audited["observation_hash"],
                    label_price_capture_hashes=audited["price_capture_hashes"],
                    label_evidence_origin=audited.get("evidence_origin"))
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


def evaluate_candidate_walk_forward(
    snapshots: pd.DataFrame, *, target_version: str = PRIMARY_TARGET_VERSION,
    minimum_training_samples: int = 200, minimum_evaluation_samples: int = 200,
    minimum_weeks: int = 12, top_fraction: float = 0.10, calibration_bins: int = 10,
    iterations: int = 400, feature_paths: Sequence[str] = FEATURE_PATHS,
    feature_variant: str = "market", positive_label_hurdle: float = DEFAULT_COST_HURDLE,
) -> dict[str, object]:
    """Compatible legacy label-timestamp diagnostic; prices are not verified."""
    report = _evaluate_candidate_walk_forward(snapshots, target_version=target_version,
        minimum_training_samples=minimum_training_samples, minimum_evaluation_samples=minimum_evaluation_samples,
        minimum_weeks=minimum_weeks, top_fraction=top_fraction, calibration_bins=calibration_bins,
        iterations=iterations, feature_paths=feature_paths, feature_variant=feature_variant,
        positive_label_hurdle=positive_label_hurdle)
    report.update(report_type="legacy_label_timestamp_diagnostic", label_price_gate_applied=False,
                  label_price_provenance_verified=False, out_of_sample_evaluation_verified=False,
                  publisher_authenticity_verified=False)
    return report


def evaluate_candidate_walk_forward_strict(
    snapshots: pd.DataFrame, *, as_of: datetime,
    label_observations: Sequence[Mapping] | None = None, label_archive_directory: Path | None = None,
    maximum_label_observations: int = MAX_LABEL_OBSERVATIONS, target_version: str = PRIMARY_TARGET_VERSION,
    minimum_training_samples: int = 200, minimum_evaluation_samples: int = 200,
    minimum_weeks: int = 12, top_fraction: float = 0.10, calibration_bins: int = 10,
    iterations: int = 400, feature_paths: Sequence[str] = FEATURE_PATHS,
    feature_variant: str = "market", positive_label_hurdle: float = DEFAULT_COST_HURDLE,
) -> dict[str, object]:
    """Explicit recorded-price gate, with unchanged candidate formula and no activation."""
    for name, value in (("minimum_training_samples", minimum_training_samples),
                        ("minimum_evaluation_samples", minimum_evaluation_samples),
                        ("minimum_weeks", minimum_weeks), ("iterations", iterations)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if (isinstance(positive_label_hurdle, bool) or not isinstance(positive_label_hurdle, (int, float))
            or not math.isfinite(positive_label_hurdle)):
        raise ValueError("positive_label_hurdle must be finite")
    if isinstance(top_fraction, bool) or not isinstance(top_fraction, (int, float)) or not 0 < top_fraction <= 1:
        raise ValueError("top_fraction must be in (0, 1]")
    if isinstance(calibration_bins, bool) or not isinstance(calibration_bins, int) or calibration_bins < 2:
        raise ValueError("calibration_bins must be an integer at least 2")
    gate = _RecordedPriceGate(snapshots, as_of=as_of, label_observations=label_observations,
        label_archive_directory=label_archive_directory, maximum_label_observations=maximum_label_observations)
    report = _evaluate_candidate_walk_forward(snapshots, target_version=target_version,
        minimum_training_samples=minimum_training_samples, minimum_evaluation_samples=minimum_evaluation_samples,
        minimum_weeks=minimum_weeks, top_fraction=top_fraction, calibration_bins=calibration_bins,
        iterations=iterations, feature_paths=feature_paths, feature_variant=feature_variant,
        positive_label_hurdle=positive_label_hurdle, price_gate=gate)
    return gate.report(report)


__all__ = [
    "CANDIDATE_EVALUATION_REPORT_VERSION",
    "STRICT_CANDIDATE_EVALUATION_REPORT_VERSION",
    "evaluate_candidate_walk_forward",
    "evaluate_candidate_walk_forward_strict",
    "summarize_candidate_samples",
]
