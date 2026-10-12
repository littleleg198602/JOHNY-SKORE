"""Reproducible, read-only specialist diagnostics on archived PIT snapshots.

An archived numeric zero is different from a collector that was never run.
Aggregate agent features cannot prove FDA/FDIC/13F collector coverage or lift.
This exploratory report never accepts a specialist or changes production scores.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from contextlib import closing
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import sqlite3
from statistics import mean

import numpy as np
import pandas as pd

from market_checker_app.prediction_contract import (
    PRIMARY_HORIZON_TRADING_DAYS, PRIMARY_TARGET_NAME, PRIMARY_TARGET_VERSION,
    SNAPSHOT_SCHEMA_VERSION, canonical_hash, make_snapshot_id,
)
from market_checker_app.services.agent_feature_service import AGENT_FEATURE_VERSION
from market_checker_app.services.candidate_model_evaluation_service import (
    summarize_candidate_samples,
)
from market_checker_app.services.candidate_model_service import (
    CORE_MARKET_FEATURE_PATHS, DEFAULT_COST_HURDLE,
)
from market_checker_app.services.market_factor_service import SUPPORTED_MARKET_FACTOR_VERSIONS
from market_checker_app.services.sec_fundamental_feature_service import SEC_FUNDAMENTAL_FEATURE_VERSION
from market_checker_app.services.specialist_status_service import load_specialist_status
from market_checker_app.services.us_equity_calendar import target_us_equity_window


REPORT_VERSION = "specialist_pit_evaluation_v1"
COMPARISON_SPECS = {
    "sec_fundamentals": {
        "specialist_id": "sec", "section": "sec_fundamentals",
        "paths": ("sec_fundamentals.values.revenue_yoy_pct",
                  "sec_fundamentals.values.operating_margin_pct",
                  "sec_fundamentals.values.debt_to_assets_ratio"),
        "scope": "archived_sec_fundamental_features",
    },
    "regulatory_events": {
        "specialist_id": "regulatory", "section": "agent_features",
        "subsection": "regulatory", "missingness": "regulatory_events",
        "paths": ("agent_features.regulatory.signed_event_score",
                  "agent_features.regulatory.verified_event_count"),
        "scope": "aggregate_contract_regulatory_and_guidance_events",
    },
    "governance_events": {
        "specialist_id": "regulatory", "section": "agent_features",
        "subsection": "governance", "missingness": "governance_events",
        "paths": ("agent_features.governance.event_count",
                  "agent_features.governance.risk_event_count"),
        "scope": "aggregate_governance_events",
    },
    "financial_forensics": {
        "specialist_id": "sec", "section": "agent_features",
        "subsection": "forensics", "missingness": "forensic_evidence",
        "paths": ("agent_features.forensics.risk_score",
                  "agent_features.forensics.hard_veto_count"),
        "scope": "aggregate_financial_forensic_evidence",
    },
    "claim_verification": {
        "specialist_id": "verification", "section": "agent_features",
        "subsection": "claims", "missingness": "research_claims",
        "paths": ("agent_features.claims.corroborated_count",
                  "agent_features.claims.contradicted_count",
                  "agent_features.claims.unresolved_count"),
        "scope": "aggregate_research_claim_verification",
    },
}

_SNAPSHOT_FIELDS = (
    "snapshot_schema_version", "snapshot_id", "run_id", "ticker", "as_of",
    "observed_at", "target_name", "target_version", "horizon_trading_days",
    "benchmark_ticker", "benchmark_selection", "label_status", "target_value",
    "target_observed_at", "baseline_model_id", "baseline_model_version",
    "feature_payload", "baseline_output", "provenance",
)


def _time(value: object) -> datetime | None:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _object(value: object) -> dict | None:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            result = json.loads(value)
        except (TypeError, ValueError):
            return None
        return dict(result) if isinstance(result, Mapping) else None
    return None


def _value(payload: Mapping, path: str) -> float | None:
    current: object = payload
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    if isinstance(current, bool):
        return None
    try:
        result = float(current)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _week(clock: datetime) -> str:
    year, week, _ = clock.isocalendar()
    return f"{year}-W{week:02d}"


def _normalise(row: Mapping) -> dict | None:
    record = {field: row.get(field) for field in _SNAPSHOT_FIELDS}
    for field in ("feature_payload", "baseline_output", "provenance"):
        # SQLite uses JSON column names, the in-memory contract uses objects.
        parsed = _object(row.get(field + "_json", row.get(field)))
        if parsed is None:
            return None
        record[field] = parsed
    for field in ("target_value", "target_observed_at"):
        value = record[field]
        if value is None or (isinstance(value, float) and math.isnan(value)):
            record[field] = None
    record["snapshot_hash"] = str(row.get("snapshot_hash") or "")
    body = {field: record[field] for field in _SNAPSHOT_FIELDS}
    expected = {canonical_hash(body)}
    # The existing label resolver hashes the SQLite row including created_at;
    # an initial in-memory snapshot hashes the same body without that column.
    if row.get("created_at") is not None:
        expected.add(canonical_hash({**body, "created_at": row["created_at"]}))
    if record["snapshot_hash"] not in expected:
        return None
    record["feature_payload_json"] = json.dumps(record["feature_payload"], sort_keys=True)
    return record


def _availability(section: Mapping, as_of: datetime, fields: Sequence[str]) -> str | None:
    supplied = [section[field] for field in fields if field in section]
    if not supplied:
        return "MISSING_FEATURE_AVAILABILITY"
    times = [_time(value) for value in supplied]
    if any(stamp is None for stamp in times):
        return "INVALID_FEATURE_AVAILABILITY"
    if any(stamp > as_of for stamp in times):
        return "FEATURE_AVAILABLE_AFTER_SNAPSHOT"
    return None


def _market_issue(row: Mapping) -> str | None:
    payload = row["feature_payload"]
    section = payload.get("market_factors")
    if not isinstance(section, Mapping):
        return "NO_ARCHIVED_MARKET_FEATURES"
    if section.get("version") not in SUPPORTED_MARKET_FACTOR_VERSIONS:
        return "UNSUPPORTED_MARKET_FEATURE_VERSION"
    issue = _availability(section, row["_as_of"], ("as_of", "observed_at", "available_at"))
    if issue:
        return issue
    contract = section.get("feature_contract")
    if isinstance(contract, Mapping):
        issue = _availability(contract, row["_as_of"], ("observed_at", "available_at"))
        if issue:
            return issue
    if not all(_value(payload, path) is not None for path in CORE_MARKET_FEATURE_PATHS):
        return "INCOMPLETE_MATCHED_MARKET_FEATURES"
    return None


def _group_issue(row: Mapping, spec: Mapping) -> str | None:
    payload = row["feature_payload"]
    section = payload.get(spec["section"])
    if not isinstance(section, Mapping):
        return "NO_ARCHIVED_SPECIALIST_FEATURES"
    if spec["section"] == "sec_fundamentals":
        if section.get("feature_version") != SEC_FUNDAMENTAL_FEATURE_VERSION:
            return "UNSUPPORTED_SPECIALIST_FEATURE_VERSION"
        # availability_at is the real SEC feature contract field.
        if "availability_at" not in section:
            return "MISSING_FEATURE_AVAILABILITY"
        issue = _availability(section, row["_as_of"], ("as_of", "observed_at", "availability_at"))
    else:
        if section.get("version") != AGENT_FEATURE_VERSION:
            return "UNSUPPORTED_SPECIALIST_FEATURE_VERSION"
        if "available_at" not in section:
            return "MISSING_FEATURE_AVAILABILITY"
        issue = _availability(section, row["_as_of"], ("as_of", "observed_at", "available_at"))
        if issue is None:
            missingness = section.get("missingness")
            if not isinstance(missingness, Mapping) or not isinstance(missingness.get(spec["missingness"]), bool):
                return "UNPROVEN_AGENT_EVIDENCE_PRESENCE"
            # Blank pipeline snapshots contain numeric zeros even when no
            # evidence exists. They must never count as observed source data.
            if missingness[spec["missingness"]]:
                return "NO_AGENT_EVIDENCE_OBSERVED"
            provenance = section.get("provenance")
            if not isinstance(provenance, Mapping) or provenance.get("point_in_time") is not True or not provenance.get("orchestration_id"):
                return "UNPROVEN_AGENT_ORCHESTRATION"
    if issue:
        return issue
    if not all(_value(payload, path) is not None for path in spec["paths"]):
        return "INCOMPLETE_MATCHED_SPECIALIST_FEATURES"
    return None


def _select_nonoverlap(rows: Sequence[dict]) -> tuple[list[dict], int]:
    selected = []
    last_end: dict[str, datetime] = {}
    weeks: set[tuple[str, str]] = set()
    excluded = 0
    for row in sorted(rows, key=lambda item: (item["_as_of"], item["snapshot_id"])):
        ticker = row["ticker"]
        cohort = (ticker, _week(row["_as_of"]))
        if cohort in weeks or (ticker in last_end and row["_as_of"] < last_end[ticker]):
            excluded += 1
            continue
        selected.append(row)
        # Label availability gates training separately. A resolver that ran
        # late must not extend the actual five-session economic horizon.
        last_end[ticker] = row["_horizon_end"]
        weeks.add(cohort)
    return selected, excluded


def _paired_intervals(metrics: Mapping, *, minimum_weeks: int) -> dict:
    # Both models use the same dates and rows; bootstrap the weekly difference,
    # never separately bootstrap two populations or treat tickers as independent.
    result = {}
    for key in ("brier_score", "ranking_ic", "top_decile_excess_return", "directional_accuracy"):
        differences = [row["candidate"][key] - row["baseline"][key]
                       for row in metrics["weekly_metrics"]
                       if row["candidate"][key] is not None and row["baseline"][key] is not None]
        interval = {"method": "paired_weekly_bootstrap", "week_count": len(differences),
                    "lower": None, "upper": None, "seed": 13, "draws": 1000}
        if len(differences) >= max(2, minimum_weeks):
            generator = random.Random(13)
            estimates = sorted(mean(generator.choice(differences) for _ in differences) for _ in range(1000))
            interval.update(lower=estimates[24], upper=estimates[974])
        else:
            interval["reason"] = "INSUFFICIENT_WEEK_BLOCKS_FOR_INTERVAL"
        result[key] = interval
    return result


def _fit_matched_logistic(training: Sequence[dict], predicted: Sequence[dict],
                          paths: Sequence[str], *, cutoff: datetime,
                          iterations: int, positive_label_hurdle: float) -> tuple[dict | None, dict]:
    """Same fixed logistic equations as the existing shadow model, vectorized.

    Complete-case gating already guarantees observed finite values. No current
    score, outcome at prediction time, feature selection or production artifact
    enters this fit. einsum avoids a thread pool for these narrow matrices.
    """
    values = np.asarray([[_value(row["feature_payload"], path) for path in paths] for row in training], dtype=float)
    labels = np.asarray([float(row["target_value"] > positive_label_hurdle) for row in training], dtype=float)
    with np.errstate(over="ignore", invalid="ignore"):
        means, scales = values.mean(axis=0), values.std(axis=0)
    if not np.isfinite(means).all() or not np.isfinite(scales).all():
        return None, {}
    active = scales > 1e-12
    if labels.min() == labels.max() or not active.any():
        return None, {}
    scales = np.where(active, scales, 1.0)
    with np.errstate(over="ignore", invalid="ignore"):
        matrix = (values - means) / scales
    if not np.isfinite(matrix).all():
        return None, {}
    coefficients = np.zeros(len(paths))
    intercept = 0.0
    for _ in range(iterations):
        logits = np.clip(intercept + np.einsum("ij,j->i", matrix, coefficients), -35.0, 35.0)
        error = 1.0 / (1.0 + np.exp(-logits)) - labels
        intercept -= 0.15 * float(error.mean())
        coefficients -= 0.15 * (np.einsum("ij,i->j", matrix, error) / len(training) + 0.05 * coefficients)
    artifact = {"model_id": "matched_specialist_logistic", "model_version": "v1_vectorized",
        "trained_as_of": cutoff.isoformat(), "target_version": PRIMARY_TARGET_VERSION,
        "training_snapshot_ids": [row["snapshot_id"] for row in training],
        "feature_paths": list(paths), "scaler_means": means.tolist(), "scaler_scales": scales.tolist(),
        "coefficients": coefficients.tolist(), "intercept": intercept,
        "iterations": iterations, "l2": 0.05, "learning_rate": 0.15,
        "positive_label_hurdle": positive_label_hurdle}
    artifact["artifact_id"] = canonical_hash(artifact)
    prediction_values = np.asarray([[_value(row["feature_payload"], path) for path in paths] for row in predicted], dtype=float)
    with np.errstate(over="ignore", invalid="ignore"):
        prediction_matrix = (prediction_values - means) / scales
    if not np.isfinite(prediction_matrix).all():
        return None, {}
    logits = np.clip(intercept + np.einsum("ij,j->i", prediction_matrix, coefficients), -35.0, 35.0)
    probabilities = 1.0 / (1.0 + np.exp(-logits))
    return artifact, {row["snapshot_id"]: float(value) for row, value in zip(predicted, probabilities)}


def _comparison(rows: Sequence[dict], spec: Mapping, *, minimum_training_samples: int,
                minimum_evaluation_samples: int, minimum_weeks: int,
                iterations: int, positive_label_hurdle: float) -> dict:
    eligible = [row for row in rows if row.get("_label_at") is not None and
                _market_issue(row) is None and _group_issue(row, spec) is None]
    evaluation_rows, overlap_count = _select_nonoverlap(eligible)
    periods: dict[datetime, list[dict]] = defaultdict(list)
    for row in evaluation_rows:
        periods[row["_as_of"]].append(row)
    samples = []
    folds = []
    training_failures: Counter = Counter()
    for cutoff, predicted in sorted(periods.items()):
        # Strict '<' is intentional: a label first observed at the prediction
        # timestamp cannot have been used to train that prediction.
        training = [row for row in eligible if row["_as_of"] < cutoff and row["_label_at"] < cutoff]
        training, _ = _select_nonoverlap(training)
        if len(training) < minimum_training_samples:
            training_failures["MINIMUM_TRAINING_SAMPLES_NOT_MET"] += 1
            continue
        ids = [row["snapshot_id"] for row in predicted]
        shared = dict(cutoff=cutoff, iterations=iterations, positive_label_hurdle=positive_label_hurdle)
        baseline, base_predictions = _fit_matched_logistic(training, predicted, CORE_MARKET_FEATURE_PATHS, **shared)
        augmented, augmented_predictions = _fit_matched_logistic(training, predicted,
            (*CORE_MARKET_FEATURE_PATHS, *spec["paths"]), **shared)
        if baseline is None or augmented is None:
            training_failures["INSUFFICIENT_CLASS_OR_FEATURE_VARIATION"] += 1
            continue
        base_ids = baseline["training_snapshot_ids"]
        extra_ids = augmented["training_snapshot_ids"]
        if base_ids != extra_ids:
            raise RuntimeError("Matched specialist comparison training cohorts diverged")
        folds.append({"as_of": cutoff.isoformat(), "training_sample_count": len(base_ids),
                      "training_snapshot_ids": base_ids,
                      "latest_training_label_at": max(row["_label_at"] for row in training).isoformat(),
                      "baseline_artifact_id": baseline["artifact_id"],
                      "augmented_artifact_id": augmented["artifact_id"],
                      "prediction_snapshot_ids": ids})
        for row in predicted:
            base_probability = base_predictions.get(row["snapshot_id"])
            augmented_probability = augmented_predictions.get(row["snapshot_id"])
            if base_probability is None or augmented_probability is None:
                continue
            samples.append({"snapshot_id": row["snapshot_id"], "ticker": row["ticker"],
                "as_of": cutoff.isoformat(), "week": _week(cutoff), "target_value": row["target_value"],
                "outcome_up": float(row["target_value"] > positive_label_hurdle),
                # Existing summarizer names its left-hand column momentum.
                # Here it contains a separately trained market-only logistic.
                "momentum_probability_up": base_probability,
                "candidate_probability_up": augmented_probability})
    metrics = summarize_candidate_samples(samples) if samples else None
    sufficient = bool(metrics and metrics["sample_count"] >= minimum_evaluation_samples and metrics["distinct_weeks"] >= minimum_weeks)
    if metrics:
        metrics["paired_candidate_minus_baseline_intervals"] = _paired_intervals(metrics, minimum_weeks=minimum_weeks)
        # Descriptive metrics remain visible, but an insufficient report must
        # not show apparently persuasive intervals. Each metric also needs
        # enough usable weeks (ranking IC can be undefined on constant weeks).
        intervals = list(metrics["paired_candidate_minus_baseline_intervals"].values())
        intervals.extend(interval for model in ("baseline", "candidate")
                         for interval in metrics[model]["weekly_intervals"].values())
        for interval in intervals:
            if interval["week_count"] < max(2, minimum_weeks):
                interval.update(lower=None, upper=None, reason="INSUFFICIENT_WEEK_BLOCKS_FOR_INTERVAL")
            elif not sufficient:
                interval.update(lower=None, upper=None, reason="MINIMUM_MATCHED_HISTORY_NOT_MET")
    return {"status": "EVALUATED_EXPLORATORY" if sufficient else "INSUFFICIENT_DATA",
            "reason": "" if sufficient else ("MINIMUM_MATCHED_HISTORY_NOT_MET" if samples else "NO_TRAINABLE_MATCHED_PERIOD"),
            "baseline": "matched_market_logistic", "augmented": "matched_market_plus_specialist_logistic",
            "scope": spec["scope"], "feature_paths": list(spec["paths"]),
            "eligible_labeled_snapshot_count": len(eligible), "overlap_excluded_count": overlap_count,
            "sample_count": len(samples), "distinct_weeks": metrics["distinct_weeks"] if metrics else 0,
            "insufficient_training_periods": dict(sorted(training_failures.items())),
            "matched_metrics": metrics, "folds": folds, "samples": samples,
            "source_specific_attribution_proven": False,
            "acceptance_proven": False}


def build_specialist_evaluation_report(snapshots: pd.DataFrame, *, as_of: datetime | None = None,
        minimum_training_samples: int = 200, minimum_evaluation_samples: int = 200,
        minimum_weeks: int = 12, iterations: int = 400,
        positive_label_hurdle: float = DEFAULT_COST_HURDLE) -> dict:
    """Measure archive coverage and paired shadow comparisons without writes.

    Only the stored snapshot body is used; current scout findings and today's
    identity registries are never joined into an earlier prediction. The
    inventory supplies names only and cannot promote a report to DONE.
    """
    clock = _time(as_of or datetime.now(timezone.utc))
    if clock is None:
        raise ValueError("Evaluation as_of must include timezone")
    for name, value in (("minimum_training_samples", minimum_training_samples),
                        ("minimum_evaluation_samples", minimum_evaluation_samples),
                        ("minimum_weeks", minimum_weeks), ("iterations", iterations)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if isinstance(positive_label_hurdle, bool) or not isinstance(positive_label_hurdle, (int, float)) or not math.isfinite(positive_label_hurdle):
        raise ValueError("positive_label_hurdle must be finite")
    raw_rows = snapshots.to_dict(orient="records")
    id_counts = Counter(str(row.get("snapshot_id") or "") for row in raw_rows)
    rejected: Counter = Counter()
    unusable_labels: Counter = Counter()
    label_counts: Counter = Counter()
    hash_verified = 0
    rows = []
    for raw in raw_rows:
        if id_counts[str(raw.get("snapshot_id") or "")] > 1:
            rejected["DUPLICATE_SNAPSHOT_ID"] += 1
            continue
        row = _normalise(raw)
        if row is None:
            rejected["INVALID_SNAPSHOT_HASH_OR_JSON"] += 1
            continue
        hash_verified += 1
        if (row["snapshot_schema_version"] != SNAPSHOT_SCHEMA_VERSION
                or row["target_version"] != PRIMARY_TARGET_VERSION
                or row["target_name"] != PRIMARY_TARGET_NAME
                or row["horizon_trading_days"] != PRIMARY_HORIZON_TRADING_DAYS):
            rejected["UNSUPPORTED_SNAPSHOT_OR_TARGET_VERSION"] += 1
            continue
        snapshot_at = _time(row["as_of"])
        observed_at = _time(row["observed_at"])
        if snapshot_at is None or observed_at is None or observed_at > snapshot_at:
            rejected["INVALID_SNAPSHOT_TIME"] += 1
            continue
        if snapshot_at > clock:
            rejected["SNAPSHOT_AFTER_REPORT_CUTOFF"] += 1
            continue
        ticker = str(row["ticker"] or "").strip().upper()
        try:
            run_id = int(row["run_id"])
            expected_id = make_snapshot_id(run_id, ticker, PRIMARY_TARGET_VERSION)
        except (TypeError, ValueError):
            run_id, expected_id = 0, ""
        if not ticker or run_id < 1 or row["snapshot_id"] != expected_id or row["ticker"] != ticker:
            rejected["INCOMPLETE_SNAPSHOT_IDENTITY"] += 1
            continue
        row["_as_of"] = snapshot_at
        row["_label_at"] = None
        status = str(row["label_status"] or "UNKNOWN").upper()
        label_counts[status] += 1
        if status == "RESOLVED":
            label_at = _time(row["target_observed_at"])
            target = _value(row, "target_value")
            try:
                _, horizon = target_us_equity_window(snapshot_at, int(row["horizon_trading_days"]))
                due_at = horizon[-1].close_at
                row["_horizon_end"] = due_at
            except (TypeError, ValueError, IndexError):
                unusable_labels["INVALID_LABEL_HORIZON"] += 1
                rows.append(row)
                continue
            if label_at is None or label_at <= snapshot_at or label_at < due_at or target is None:
                unusable_labels["INVALID_OR_PREMATURE_RESOLVED_LABEL"] += 1
            elif label_at > clock:
                unusable_labels["LABEL_AFTER_REPORT_CUTOFF"] += 1
            else:
                row["_label_at"] = label_at
                row["target_value"] = target
        rows.append(row)
    rows.sort(key=lambda row: (row["_as_of"], row["snapshot_id"]))
    coverage = {}
    comparisons = {}
    for name, spec in COMPARISON_SPECS.items():
        issues = Counter(_group_issue(row, spec) or "OBSERVED" for row in rows)
        market_matched = sum(_group_issue(row, spec) is None and _market_issue(row) is None for row in rows)
        coverage[name] = {"specialist_id": spec["specialist_id"], "scope": spec["scope"],
            "snapshot_count": len(rows), "observed_feature_snapshot_count": issues.get("OBSERVED", 0),
            "matched_market_feature_snapshot_count": market_matched,
            "missing_or_rejected_reasons": {key: value for key, value in sorted(issues.items()) if key != "OBSERVED"},
            "coverage_denominator": "all_integrity_verified_snapshots_at_report_cutoff_not_applicable_provider_subjects",
            "provider_coverage_proven": False}
        comparisons[name] = _comparison(rows, spec,
            minimum_training_samples=minimum_training_samples,
            minimum_evaluation_samples=minimum_evaluation_samples,
            minimum_weeks=minimum_weeks, iterations=iterations,
            positive_label_hurdle=positive_label_hurdle)
    market_count = sum(_market_issue(row) is None for row in rows)
    specialist_coverage = []
    for item in load_specialist_status()["specialists"]:
        groups = [name for name, spec in COMPARISON_SPECS.items() if spec["specialist_id"] == item["id"]]
        observed_rows = [row for row in rows if any(_group_issue(row, COMPARISON_SPECS[name]) is None for name in groups)]
        if item["id"] == "prices":
            observed_rows = [row for row in rows if _market_issue(row) is None]
        matched_rows = [row for row in observed_rows if _market_issue(row) is None]
        feature_paths = [path for name in groups for path in COMPARISON_SPECS[name]["paths"]]
        if item["id"] == "prices":
            feature_paths = list(CORE_MARKET_FEATURE_PATHS)
        if groups or item["id"] == "prices":
            status = ("FEATURE_GROUP_HISTORY_MEASURED" if groups else "MARKET_BASELINE_HISTORY_MEASURED") if observed_rows else "NO_OBSERVED_ARCHIVED_FEATURES"
        else:
            status = "NO_ATTRIBUTABLE_SPECIALIST_FEATURES"
        specialist_coverage.append({"specialist_id": item["id"], "name": item["name"],
            "archived_feature_groups": groups,
            "archived_feature_paths": feature_paths,
            "status": status, "snapshot_count": len(rows),
            "observed_feature_snapshot_count": len(observed_rows),
            "matched_market_feature_snapshot_count": len(matched_rows),
            "eligible_labeled_snapshot_count": sum(row["_label_at"] is not None for row in matched_rows),
            "feature_group_coverage": {name: coverage[name] for name in groups},
            "coverage_denominator": "all_integrity_verified_snapshots_at_report_cutoff_not_applicable_provider_subjects",
            "market_feature_snapshot_count": market_count if item["id"] == "prices" else None,
            "source_specific_attribution_proven": False, "acceptance_proven": False})
    fingerprint = [{"snapshot_id": row["snapshot_id"], "snapshot_hash": row["snapshot_hash"]} for row in rows]
    return {"schema_version": 1, "report_version": REPORT_VERSION,
        "report_type": "exploratory_history_diagnostic_not_completion_certificate",
        "generated_at": clock.isoformat(), "target_version": PRIMARY_TARGET_VERSION,
        "status": "EVALUATED_EXPLORATORY" if any(row["status"] == "EVALUATED_EXPLORATORY" for row in comparisons.values()) else "INSUFFICIENT_DATA",
        "reason": "" if any(row["status"] == "EVALUATED_EXPLORATORY" for row in comparisons.values()) else (("SNAPSHOT_ROWS_REJECTED" if raw_rows else "NO_ARCHIVED_SNAPSHOT_HISTORY") if not rows else "MINIMUM_MATCHED_HISTORY_NOT_MET"),
        "input": {"snapshot_rows": len(raw_rows), "hash_verified_rows": hash_verified,
                  "integrity_verified_rows": len(rows),
                  "resolved_labels_known_at_cutoff": sum(row["_label_at"] is not None for row in rows),
                  "snapshot_id_hash_sha256": canonical_hash({"snapshots": fingerprint}),
                  "rejected_reasons": dict(sorted(rejected.items())),
                  "unusable_resolved_label_reasons": dict(sorted(unusable_labels.items())),
                  "label_status_counts": dict(sorted(label_counts.items())),
                  "non_win_loss_status_counts": {key: label_counts.get(key, 0) for key in ("MARKET_CLOSED", "DATA_GAP", "PENDING", "UNAVAILABLE")}},
        "evaluation_policy": {"minimum_training_samples": minimum_training_samples,
            "minimum_evaluation_samples": minimum_evaluation_samples, "minimum_weeks": minimum_weeks,
            "iterations": iterations, "positive_label_hurdle": positive_label_hurdle,
            "model_id": "matched_specialist_logistic", "model_version": "v1_vectorized",
            "l2": 0.05, "learning_rate": 0.15,
            "training_labels": "strictly_observed_before_prediction_timestamp",
            "feature_availability": "archived_feature_availability_not_after_snapshot",
            "cohorts": "paired_training_and_evaluation_ids_one_ticker_per_week_no_overlapping_label_horizons",
            "baseline_feature_paths": list(CORE_MARKET_FEATURE_PATHS),
            "baseline": "market_only_logistic_not_momentum_heuristic",
            "comparison_selection": "predeclared_exploratory_no_performance_based_selection",
            "missing_features": "complete_matched_rows_no_blank_agent_zero_as_source_evidence"},
        "feature_group_coverage": coverage, "specialist_coverage": specialist_coverage,
        "comparisons": comparisons, "analysis_only": True, "ranking_modified": False,
        "activation_allowed": False, "completion_verified": False,
        "limitations": ["Current canonical universe is not a historical survivor-free universe.",
            "Aggregate agent features do not attribute performance or coverage to FDA, FDIC, 13F or other collectors.",
            "Observed evidence cohorts are selective; matched lift is exploratory, not causal.",
            "Weekly block intervals do not establish independence across sectors or market regimes.",
            "No historical rows, identities or labels are synthesized from current scout findings."],
        "acceptance": {"out_of_sample_evaluation_verified": False}}


def read_archived_prediction_snapshots(db_path: Path) -> pd.DataFrame:
    """Read the existing SQLite table in mode=ro, without schema creation."""
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Snapshot history database does not exist: {path}")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5.0)) as connection:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='prediction_snapshots'").fetchone()
        if not exists:
            return pd.DataFrame()
        return pd.read_sql_query("SELECT * FROM prediction_snapshots ORDER BY as_of, ticker, snapshot_id", connection)


__all__ = ["REPORT_VERSION", "build_specialist_evaluation_report", "read_archived_prediction_snapshots"]
