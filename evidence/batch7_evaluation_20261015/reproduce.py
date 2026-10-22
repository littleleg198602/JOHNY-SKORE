"""Offline qualification plus actual read-only corpus audit; no forecasts made."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from market_checker_app.services.prospective_forecast_registration_adapter import ProspectiveForecastRegistrationAdapter


PUBLIC_BASE = "089ce4dbe03952d84daac5cfe47760b09020954b"
DEPENDENCY_COMMIT = "e8fc94050e5b3dfbc721f99e9b549068ba6ca160"
MODULES = ["tests.test_batch7_forecast_registration_adapter", "tests.test_weekly_shadow_runner"]
OWNED = ["market_checker_app/services/prospective_forecast_registration_adapter.py",
    "market_checker_app/weekly_shadow_runner.py", "tests/test_batch7_forecast_registration_adapter.py",
    "evidence/batch7_evaluation_20261015/README.md", "evidence/batch7_evaluation_20261015/reproduce.py"]
UNCHANGED = ["market_checker_app/prediction_contract.py", "market_checker_app/services/prediction_label_service.py",
    "market_checker_app/services/candidate_model_service.py", "market_checker_app/services/candidate_model_evaluation_service.py",
    "market_checker_app/services/prospective_specialist_history.py"]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")


def retained_corpus(path):
    result = {"observed_at": datetime.now(timezone.utc).isoformat(), "evidence_origin": "ACTUAL_READ_ONLY_RETAINED_DATABASE",
        "database_exists": path.is_file(), "prediction_table_exists": False, "prediction_rows": 0,
        "forecasts_created": 0, "registrations_created": 0, "labels_created": 0,
        "historical_backfill_performed": False, "source_http_requests": 0}
    before = sha(path) if path.is_file() else None
    if path.is_file():
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0)) as connection:
            result["prediction_table_exists"] = bool(connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='prediction_snapshots'").fetchone())
            if result["prediction_table_exists"]:
                result["prediction_rows"] = connection.execute("SELECT COUNT(*) FROM prediction_snapshots").fetchone()[0]
    result["database_main_file_sha256_before"] = before
    result["database_main_file_sha256_after"] = sha(path) if path.is_file() else None
    result["database_main_bytes_unchanged_during_read"] = result["database_main_file_sha256_after"] == before
    adapter = ProspectiveForecastRegistrationAdapter(db_path=path, audit_archive_root=path.parent)
    # No invented ID is supplied when no authentic forecast exists. In
    # particular, this operation cannot initialize an absent prediction table.
    result["empty_selected_corpus_adapter_report"] = adapter.register_after_persistence([])
    result["history_report"] = adapter.read_history_report()
    result["status"] = "WAIT_FORECAST_CORPUS" if not result["prediction_rows"] else "WAIT_EXPLICIT_AUTHENTIC_SELECTION"
    result["reason"] = "FORECAST_TABLE_UNAVAILABLE" if path.is_file() and not result["prediction_table_exists"] else "NO_AUTHENTIC_FORECAST_SELECTED"
    return result


def old_evidence():
    listing = subprocess.check_output(["git", "ls-tree", "-rz", PUBLIC_BASE, "--", "evidence"], cwd=ROOT)
    manifest, failures = {}, []
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        metadata, relative = entry.split(b"\t", 1)
        _, kind, expected = metadata.split()
        if kind != b"blob":
            continue
        name = relative.decode()
        path = ROOT / name
        if not path.is_file():
            failures.append(name)
            continue
        data = path.read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if actual != expected.decode():
            failures.append(name)
        manifest[name] = sha(path)
    return {"public_base": PUBLIC_BASE, "old_evidence_files_checked": len(manifest),
        "mismatches_or_missing": failures, "all_old_evidence_byte_identical": not failures,
        "sha256_manifest_digest": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--retained-db", type=Path, default=ROOT.parent / "JOHNY-batch4-integration-20261013/outputs/market_checker_history.db")
    args = parser.parse_args()
    output = args.output_directory.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ("tests.log", "actual_corpus.json", "handoff.json")):
        raise FileExistsError("Choose a new directory; qualified evidence is not overwritten")
    hashes_before = {name: sha(ROOT / name) for name in OWNED}
    suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    discovered = suite.countTestCases()
    started = time.perf_counter()
    with patch("socket.socket.connect", side_effect=AssertionError("Qualification is strictly offline")), \
         (output / "tests.log").open("x", encoding="utf-8", newline="\n") as log:
        tested = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    elapsed = time.perf_counter() - started
    corpus = retained_corpus(args.retained_db)
    write(output / "actual_corpus.json", corpus)
    preservation = old_evidence()
    hashes_after = {name: sha(ROOT / name) for name in OWNED}
    stable = hashes_before == hashes_after
    baseline_unchanged = {}
    for name in UNCHANGED:
        original = subprocess.check_output(["git", "show", PUBLIC_BASE + ":" + name], cwd=ROOT)
        baseline_unchanged[name] = {"sha256": sha(ROOT / name), "byte_identical_to_public_base": (ROOT / name).read_bytes() == original}
    attempts = [{"path": "evidence/batch7_evaluation_20261015/development_tests_1.log", "executed": 22, "failures": 4,
                 "cause": "synthetic client incorrectly rejected normal pre-persistence OHLC fetch", "current_acceptance": False},
                {"path": "evidence/batch7_evaluation_20261015/development_normal_tests_2.log", "executed": 4, "failures": 2,
                 "cause": "fixture missed producer utc_now; strict source availability correctly refused the mismatched clocks", "current_acceptance": False},
                {"path": "evidence/batch7_evaluation_20261015/development_normal_tests_3.log", "executed": 4, "failures": 0,
                 "cause": "normal synthetic persistence fixtures corrected without changing source/clock gates", "fixture_only": True}]
    artifacts = [output / "tests.log", output / "actual_corpus.json"] + [ROOT / attempt["path"] for attempt in attempts]
    proof = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "public_base": PUBLIC_BASE,
        "root_owned_dependency_commit": DEPENDENCY_COMMIT,
        "local_head_before_lane_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "deliverable": "Explicit adapter from normal persisted forecasts to prospective history, source/settings pins and later exact labels",
        "api": {"class": "ProspectiveForecastRegistrationAdapter(db_path=..., audit_archive_root=..., maximum_forecasts=687)",
            "methods": ["register_after_persistence(snapshot_ids)", "attach_explicit_labels()", "read_history_report()"],
            "normal_hook": "finalize_analysis_run immediately after actual store.save_prediction_snapshots; committed subsets are reread after store exceptions",
            "cli": ["--register-prospective-history", "--prospective-audit-archive-root PATH"],
            "config": {"prospective_history_enabled": False, "prospective_audit_archive_root": None},
            "manifest": "<audit-root>/prospective_source_manifest.json", "manifest_schema": "prospective_forecast_source_manifest_v1",
            "result_key": "prospective_forecast_history", "result_sections": ["registration", "label_attachment", "history"],
            "settings_and_binding_directory": "<audit-root>/prospective_specialist_history/adapter_audits",
            "source_selection": "bounded explicit paths only; no glob, fallback source query or automatic label load",
            "default_finalizer_output_and_model_inputs_changed": False},
        "tests": {"modules": MODULES, "discovered": discovered, "executed": tested.testsRun,
            "failures": len(tested.failures), "errors": len(tested.errors), "skips": len(tested.skipped),
            "expected_failures": len(tested.expectedFailures), "unexpected_successes": len(tested.unexpectedSuccesses),
            "successful": tested.wasSuccessful(), "elapsed_seconds": round(elapsed, 6), "fixture_only": True,
            "normal_pipeline_positive_is_synthetic_not_live_acceptance": True, "clock_injection_only_in_fixtures": True},
        "prior_development_attempts": attempts, "owned_file_sha256": hashes_after, "source_files_stable_through_tests": stable,
        "artifact_sha256": {str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else path.name: sha(path) for path in artifacts},
        "unchanged_model_label_history_files": baseline_unchanged, "preserved_old_evidence": preservation,
        "actual_result": {"status": corpus["status"], "reason": corpus["reason"], "prediction_rows": corpus["prediction_rows"],
            "authentic_new_forecasts": 0, "authentic_new_registrations": 0, "real_new_labels": 0,
            "eligible_real_labels": corpus["history_report"]["eligible_real_label_count"],
            "retained_database_not_initialized_or_modified_by_adapter": True},
        "claims": {"source_http_requests": 0, "quota_writes": 0, "source_archives_rewritten": False,
            "past_source_availability_replaced_by_current_clock": False, "historical_forecasts_backfilled": False,
            "score_or_fitter_changed": False, "price_or_label_values_changed": False, "sixty_second_budget_changed": False,
            "trading_enabled": False, "current_windows_change_verified": False, "out_of_sample_acceptance_verified": False,
            "specialists_accepted": 0},
        "remaining_work": ["Supply an explicit manifest with authentic, contemporaneously available source archives from the normal authorized run.",
            "Allow a genuine future normal forecast to persist and register before the first forward NYSE session opens; do not backfill expired rows.",
            "List actual later label sidecars after they exist. Dated publisher revision and complete action provenance remain mandatory; UNKNOWN stays excluded.",
            "Genuine prospective observations, historical/OOS coverage, all-specialist acceptance and actual Windows end-to-end are separate evidence."]}
    write(output / "handoff.json", proof)
    print(json.dumps({"tests": proof["tests"], "actual_result": proof["actual_result"],
        "source_files_stable": stable, "old_evidence_byte_identical": preservation["all_old_evidence_byte_identical"]}, sort_keys=True))
    return 0 if tested.wasSuccessful() and stable and preservation["all_old_evidence_byte_identical"] and all(item["byte_identical_to_public_base"] for item in baseline_unchanged.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
