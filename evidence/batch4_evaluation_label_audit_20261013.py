"""Reproduce focused regressions and audit existing source-only evidence.

Run from the repository root with its test interpreter. Synthetic controls
verify PIT mechanics only and are explicitly separate from genuine archives.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from market_checker_app.services.specialist_evaluation_service import build_specialist_evaluation_report
from market_checker_app.services.specialist_incremental_archive_service import load_specialist_source_run_archive
from tests.test_batch4_evaluation_label_availability import _at_capture, _report
from tests.test_specialist_evaluation_service import START, _history, _observations


MODULES = [
    "tests.test_batch4_evaluation_label_availability",
    "tests.test_specialist_evaluation_service",
    "tests.test_prediction_contract",
    "tests.test_prediction_snapshot_storage",
    "tests.test_prediction_label_service",
    "tests.test_specialist_source_archive_service",
    "tests.test_specialist_incremental_archive_service",
    "tests.test_candidate_model_service",
    "tests.test_candidate_model_evaluation_service",
]
LOG = ROOT / "evidence/batch4_evaluation_label_audit_20261013.log"
OUTPUT = ROOT / "evidence/batch4_evaluation_label_audit_20261013.json"
started = time.perf_counter()
suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
discovered = suite.countTestCases()
with LOG.open("w", encoding="utf-8") as handle:
    result = unittest.TextTestRunner(stream=handle, verbosity=2).run(suite)
elapsed = time.perf_counter() - started
clock = datetime.now(timezone.utc)
old_cutoff = datetime(2026, 10, 1, 17, 45, 17, 276552, tzinfo=timezone.utc)
archives = []
for path in sorted((ROOT / "evidence/batch3_evaluation_saved_payload_archives_20261012/fdic").glob("specialist_source_run_*.json")):
    archive = load_specialist_source_run_archive(path)
    archives.append({"path": str(path.relative_to(ROOT)), "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source": archive["source"], "evidence_origin": archive["evidence_origin"],
        "available_at": archive["available_at"], "observed_at": archive["observed_at"],
        "content_hash_verified": True,
        "eligible_at_original_oct1_cutoff": datetime.fromisoformat(archive["available_at"]) <= old_cutoff,
        "price_label_capture_present": "price_captures" in archive})
history = _history(4)
unproven = _report(history)
observations = _observations(history)
delayed = START + timedelta(weeks=2)
observations[:8] = [_at_capture(observation, delayed) for observation in observations[:8]]
with_captures = _report(history, observations)
empty = build_specialist_evaluation_report(pd.DataFrame(), as_of=clock)
owned = [
    "market_checker_app/services/specialist_evaluation_service.py",
    "market_checker_app/services/specialist_label_availability_service.py",
    "tests/test_specialist_evaluation_service.py",
    "tests/test_batch4_evaluation_label_availability.py",
    "evidence/batch4_evaluation_label_audit_20261013.py",
]
evidence = {"schema_version": 1, "generated_at": clock.isoformat(),
    "base_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    "evidence_type": "point_in_time_label_guardrail_tests_and_existing_source_archive_content_audit",
    "environment": {"python": platform.python_version(), "platform": platform.platform(), "pandas": pd.__version__},
    "tests": {"modules": MODULES, "discovered": discovered,
        "executed": result.testsRun, "errors": len(result.errors), "failures": len(result.failures),
        "skips": len(result.skipped), "expected_failures": len(result.expectedFailures),
        "unexpected_successes": len(result.unexpectedSuccesses), "successful": result.wasSuccessful(),
        "elapsed_seconds": round(elapsed, 6), "log_path": str(LOG.relative_to(ROOT)),
        "log_sha256": hashlib.sha256(LOG.read_bytes()).hexdigest()},
    "genuine_existing_source_archive_audit": {"files": archives, "source_archive_count": len(archives),
        "historical_oct1_archives_eligible": sum(row["eligible_at_original_oct1_cutoff"] for row in archives),
        "price_label_captures": sum(row["price_label_capture_present"] for row in archives),
        "network_requests": 0, "new_source_captures": 0},
    "explicitly_synthetic_controls": {"real_historical_data": False,
        "price_capture_origin": "DETERMINISTIC_TEST_FIXTURE", "snapshot_count": len(history),
        "legacy_timestamp_valid_count": unproven["input"]["resolved_label_timestamps_at_cutoff"],
        "labels_eligible_without_price_captures": unproven["input"]["resolved_labels_known_at_cutoff"],
        "missing_capture_reasons": unproven["label_availability"]["label_reason_counts"],
        "labels_eligible_with_explicit_synthetic_captures": with_captures["input"]["resolved_labels_known_at_cutoff"],
        "delayed_capture_at": delayed.isoformat(),
        "first_walk_forward_fold": with_captures["comparisons"]["sec_fundamentals"]["folds"][0],
        "second_fold_training_sample_count": with_captures["comparisons"]["sec_fundamentals"]["folds"][1]["training_sample_count"],
        "financial_performance_claimed": False},
    "actual_prediction_history": {"default_database_exists": (ROOT / "outputs/market_checker_history.db").is_file(),
        "historical_price_database_evaluated": False, "missing_actual_history_status": empty["status"],
        "all_specialists_reported": len(empty["specialist_coverage"])},
    "claims": {"strict_actual_label_availability_gate_implemented": True,
        "legacy_snapshot_or_label_values_rewritten": False, "production_scoring_changed": False,
        "old_source_archives_modified": False, "source_history_backdated": False,
        "out_of_sample_acceptance_verified": False, "specialist_acceptance_verified": False,
        "windows_runtime_verified": False},
    "limitations": [
        "No genuine historical forward-price captures or prediction database were available in this checkout; no price labels or OOS performance evidence were invented.",
        "The three genuine FDIC archives are current replays of saved publisher payloads, are source-only evidence and remain ineligible at their original October 1 cutoff.",
        "Content hashes and recorded complete corporate-action windows establish internal consistency, not publisher authenticity or an independently complete corporate-action history.",
        "Legacy labels with endpoint-only timestamps are still visible but cannot train specialist PIT comparisons without matching price observations.",
        "A future label resolver must persist actual observed/captured prices and supplied loader provenance. Unknown revision/action provenance remains insufficient data.",
    ],
    "owned_file_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in owned}}
OUTPUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
print(json.dumps({"tests": evidence["tests"], "genuine_source_archives": len(archives),
                  "price_label_captures": evidence["genuine_existing_source_archive_audit"]["price_label_captures"]}, sort_keys=True))
raise SystemExit(0 if result.wasSuccessful() else 1)
