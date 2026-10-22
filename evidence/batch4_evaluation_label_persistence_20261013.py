"""Reproduce label-price persistence and PIT-clock regressions with fake frames.

This deliberately does not download prices or present simulated input prices
as historical observations. No generated fixture sidecars are committed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from market_checker_app.services.specialist_label_availability_service import load_label_observation
from tests.test_batch4_evaluation_label_persistence import (
    PHYSICAL_CAPTURE, _archives, _histories, _report, _resolve, _store,
)


MODULES = [
    "tests.test_batch4_evaluation_label_persistence",
    "tests.test_batch4_evaluation_label_availability",
    "tests.test_specialist_evaluation_service",
    "tests.test_prediction_label_service",
    "tests.test_prediction_label_runner",
    "tests.test_prediction_label_queue_fairness",
    "tests.test_prediction_contract",
    "tests.test_prediction_snapshot_storage",
]
LOG = ROOT / "evidence/batch4_evaluation_label_persistence_20261013.log"
OUTPUT = ROOT / "evidence/batch4_evaluation_label_persistence_20261013.json"
started = time.perf_counter()
suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
discovered = suite.countTestCases()
with LOG.open("w", encoding="utf-8") as handle:
    result = unittest.TextTestRunner(stream=handle, verbosity=2).run(suite)
elapsed = time.perf_counter() - started
controls = []
for supplied in (False, True):
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp)
        store = _store(directory)
        before = store.read_prediction_snapshots().to_dict("records")[0]
        resolution = _resolve(store, _histories(metadata=supplied))
        after = store.read_prediction_snapshots().to_dict("records")[0]
        artifact = load_label_observation(_archives(directory)[0])
        report = _report(store, PHYSICAL_CAPTURE + timedelta(seconds=1))
        controls.append({"actual_provider_data": False, "input_prices": "DETERMINISTIC_TEST_FIXTURE",
            "explicit_synthetic_provider_metadata_supplied": supplied, "resolution": resolution,
            "target_value": after["target_value"], "target_observed_at": after["target_observed_at"],
            "observation_hash": artifact["observation_hash"], "captured_at": artifact["captured_at"],
            "session_prices_consumed": len(artifact["price_captures"]["asset"]["session_prices"]),
            "immutable_original_columns_preserved": all(before[key] == after[key]
                for key in ("feature_payload_json", "provenance_json", "baseline_output_json", "snapshot_id", "as_of", "observed_at")),
            "eligible_pit_label_count": report["input"]["resolved_labels_known_at_cutoff"],
            "label_reason_counts": report["label_availability"]["label_reason_counts"],
            "acceptance_verified": report["completion_verified"],
            "financial_performance_claimed": False})
owned = [
    "market_checker_app/services/prediction_label_service.py",
    "market_checker_app/services/specialist_label_availability_service.py",
    "tests/test_batch4_evaluation_label_persistence.py",
    "evidence/batch4_evaluation_label_persistence_20261013.py",
]
evidence = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
    "base_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    "evidence_type": "deterministic_price_capture_persistence_and_actual_clock_guardrail_tests_only",
    "tests": {"modules": MODULES, "discovered": discovered, "executed": result.testsRun,
        "errors": len(result.errors), "failures": len(result.failures), "skips": len(result.skipped),
        "expected_failures": len(result.expectedFailures), "unexpected_successes": len(result.unexpectedSuccesses),
        "successful": result.wasSuccessful(), "elapsed_seconds": round(elapsed, 6),
        "log_path": str(LOG.relative_to(ROOT)), "log_sha256": hashlib.sha256(LOG.read_bytes()).hexdigest()},
    "explicitly_synthetic_persistence_controls": controls,
    "claims": {"automatic_immutable_label_price_persistence_implemented": True,
        "observed_time_separated_from_exchange_endpoint": True, "metadata_not_inferred_from_plain_frames": True,
        "target_formula_changed": False, "production_scores_changed": False,
        "legacy_finalized_rows_or_hashes_modified": False, "actual_price_provider_calls": 0,
        "historical_price_labels_backdated": False, "windows_runtime_verified": False,
        "out_of_sample_acceptance_verified": False, "specialists_accepted": 0},
    "remaining_work": [
        "Current production action-cache/runners do not supply point_in_time_price_provenance. Their exact prices are preserved, but source/revision/corporate-action metadata remains UNKNOWN and those labels remain excluded from specialist PIT comparisons.",
        "A genuine bounded provider/cache metadata adapter must separately establish actual source identities, revision knowledge and corporate-action window scope; a split column alone is not an independently verified complete history.",
        "No real historical price database, genuine new provider capture, OOS evidence or user Windows end-to-end run was produced by this fixture test run.",
    ],
    "owned_file_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in owned}}
OUTPUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
print(json.dumps({"tests": evidence["tests"], "controls": controls}, sort_keys=True))
raise SystemExit(0 if result.wasSuccessful() else 1)
