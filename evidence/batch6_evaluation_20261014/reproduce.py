"""Read actual retained corpus/captures and run bounded fixture regressions.

No HTTP, forecast creation, historical clock injection or source archive
rewrites. Choose a new output directory for a later diagnostic; existing
handoff/log files are never overwritten by this script.
"""
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from market_checker_app.services.prospective_specialist_history import ProspectiveSpecialistHistory
from market_checker_app.services.yahoo_price_provenance_service import load_observation


BASE_SHA = "cbc6c4a3f023f4f32778456ae4f73d6d2c91fcc6"
MODULES = ["tests.test_batch6_prospective_specialist_history",
           "tests.test_prediction_snapshot_storage", "tests.test_prediction_contract",
           "tests.test_batch4_evaluation_label_availability"]
DEFAULT_DB = ROOT.parent / "JOHNY-batch4-integration-20261013/outputs/market_checker_history.db"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, record: dict) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, sort_keys=True, indent=2, allow_nan=False) + "\n")


def _old_evidence_integrity() -> dict:
    listing = subprocess.check_output(["git", "ls-tree", "-rz", BASE_SHA, "--", "evidence"], cwd=ROOT)
    manifest, failures = {}, []
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        metadata, relative = entry.split(b"\t", 1)
        _, kind, expected = metadata.split()
        if kind != b"blob":
            continue
        name = relative.decode("utf-8")
        path = ROOT / name
        if not path.is_file():
            failures.append(name)
            continue
        content = path.read_bytes()
        blob_hash = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        if blob_hash != expected.decode():
            failures.append(name)
        manifest[name] = hashlib.sha256(content).hexdigest()
    return {"base_sha": BASE_SHA, "tracked_evidence_files_checked": len(manifest),
        "byte_mismatches_or_missing_files": failures,
        "all_old_evidence_byte_identical": not failures,
        "checked_file_sha256_manifest_digest": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()}


def _corpus(db_path: Path, clock: datetime) -> dict:
    report = {"observed_at": clock.isoformat(), "evidence_origin": "ACTUAL_READ_ONLY_RETAINED_RUNTIME_DATABASE",
        "database_exists": db_path.is_file(), "database_filename": db_path.name,
        "prediction_snapshot_table_exists": False, "snapshot_rows": 0,
        "authentic_registrations_created_by_this_run": 0, "real_labels_attached_by_this_run": 0,
        "forecasts_created": 0, "historical_backfill_performed": False}
    if db_path.is_file():
        with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0)) as connection:
            exists = bool(connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='prediction_snapshots'").fetchone())
            report["prediction_snapshot_table_exists"] = exists
            if exists:
                report["snapshot_rows"] = connection.execute("SELECT COUNT(*) FROM prediction_snapshots").fetchone()[0]
    report["prospective_eligible_forecasts_in_supplied_corpus"] = 0 if not report["snapshot_rows"] else None
    report["status"] = "WAIT_FORECAST_CORPUS" if not report["snapshot_rows"] else "WAIT_EXPLICIT_AUTHENTIC_FORECAST_SELECTION"
    report["reason"] = ("FORECAST_DATABASE_UNAVAILABLE" if not report["database_exists"]
                        else "FORECAST_TABLE_UNAVAILABLE" if not report["prediction_snapshot_table_exists"]
                        else "NO_STORED_FORECAST_ROWS" if not report["snapshot_rows"]
                        else "SELECTED_FORECAST_SOURCE_PROVENANCE_NOT_YET_AUDITED")
    report["prospective_history_report"] = ProspectiveSpecialistHistory(
        db_path.parent / "prospective_specialist_history").read_report(as_of=clock, maximum=687)
    return report


def _captured_prices() -> dict:
    records = []
    paths = sorted((ROOT / "evidence/batch5_prices_20261014/observations").glob("yahoo_price_*.json"))
    if len(paths) > 32:
        raise ValueError("Preserved price evidence scope changed; refusing unbounded audit")
    for path in paths:
        observation = load_observation(path)
        records.append({"path": path.relative_to(ROOT).as_posix(), "file_sha256": _sha(path),
            "capture_id": observation["capture_id"], "ticker": observation["ticker"],
            "kind": observation["kind"], "frame_sha256": observation["frame_sha256"],
            "observed_at": observation["observed_at"], "captured_at": observation["captured_at"],
            "publisher_revision_timing_status": observation["publisher_revision_timing_status"],
            "dated_publisher_revision_eligible": False,
            "no_label_attachment_attempted_without_authentic_forecast": True})
    raw_files = [{"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size,
                  "sha256": _sha(path)}
                 for path in sorted((ROOT / "evidence/batch5_prices_20261014/raw").glob("*.json"))]
    return {"evidence_origin": "READ_ONLY_AUDIT_OF_PRESERVED_ACTUAL_BATCH5_CAPTURES",
        "current_run_source_http_requests": 0, "preserved_observation_versions": len(records),
        "versions_with_unknown_publisher_revision_timing": sum(record["publisher_revision_timing_status"] == "UNKNOWN" for record in records),
        "publisher_revision_provenance_upgraded": False, "real_forecasts_or_labels_created_from_prices": 0,
        "observations": records, "unchanged_original_http_bodies": raw_files,
        "limitations": ["The two original bodies and their current import/replay versions are not new live requests.",
            "Current capture/import times cannot create an earlier prospective forecast.",
            "Publisher timing is UNKNOWN. Local content changes and action input arrays do not upgrade it.",
            "No synthetic forecast, shifted target or OOS sample is constructed from these historical prices."]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    output = args.output_directory.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for name in ("handoff.json", "tests.log", "actual_corpus_audit.json", "preserved_price_audit.json"):
        if (output / name).exists():
            raise FileExistsError("Use a new output directory; existing evidence is immutable")
    observed_at = datetime.now(timezone.utc)
    corpus = _corpus(args.db, observed_at)
    prices = _captured_prices()
    _write(output / "actual_corpus_audit.json", corpus)
    _write(output / "preserved_price_audit.json", prices)
    suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    discovered = suite.countTestCases()
    started = time.perf_counter()
    with (output / "tests.log").open("x", encoding="utf-8", newline="\n") as handle:
        tested = unittest.TextTestRunner(stream=handle, verbosity=2).run(suite)
    elapsed = time.perf_counter() - started
    preservation = _old_evidence_integrity()
    owned = ["market_checker_app/services/prospective_specialist_history.py",
             "tests/test_batch6_prospective_specialist_history.py",
             "evidence/batch6_evaluation_20261014/reproduce.py",
             "evidence/batch6_evaluation_20261014/README.md"]
    artifact_paths = [output / name for name in ("actual_corpus_audit.json", "preserved_price_audit.json", "tests.log")]
    proof = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_sha": BASE_SHA, "local_head_before_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "deliverable": "Append-only prospective forecast registration and exact later label attachment",
        "integration_api": {"class": "ProspectiveSpecialistHistory(directory, observation_clock=None)",
            "registration": "register_stored_snapshot(db_path, snapshot_id, source_archive_paths=())",
            "attachment": "attach_label(db_path, snapshot_id, label_observation_path=...)",
            "report": "read_report(as_of=..., training_before=None, maximum=1000)",
            "training_rule": "forecast registration, original prediction and label usable_at strictly before training_before",
            "prospective_rule": "first registration before the first forward NYSE session open for the originally pinned full window",
            "dated_revision_rule": "each price capture needs DATED_PUBLISHER_VERSION and KNOWN_REVISION with captured source ID; UNKNOWN/absent stays excluded",
            "source_pin_rule": "archive availability at or before original prediction; later imports never inherit earlier event time",
            "target": "unchanged five-session split-adjusted excess price return excluding dividends",
            "default_history_directory_for_integration": "db_path.parent / prospective_specialist_history",
            "source_archive_types": ["SPECIALIST_SOURCE_RUN", "YAHOO_PRICE_OBSERVATION"]},
        "actual_result": {"status": corpus["status"], "reason": corpus["reason"],
            "authentic_registered_forecasts": corpus["authentic_registrations_created_by_this_run"],
            "real_labels_attached": corpus["real_labels_attached_by_this_run"],
            "eligible_real_labels": corpus["prospective_history_report"]["eligible_real_label_count"],
            "forecast_snapshot_table_exists": corpus["prediction_snapshot_table_exists"],
            "preserved_price_versions_checked": len(prices["observations"]),
            "unknown_publisher_revision_versions": prices["versions_with_unknown_publisher_revision_timing"]},
        "tests": {"modules": MODULES, "discovered": discovered, "executed": tested.testsRun,
            "failures": len(tested.failures), "errors": len(tested.errors), "skips": len(tested.skipped),
            "expected_failures": len(tested.expectedFailures), "unexpected_successes": len(tested.unexpectedSuccesses),
            "successful": tested.wasSuccessful(), "elapsed_seconds": round(elapsed, 6),
            "fixture_only": True, "production_schema_fixture_is_not_actual_live_acceptance": True,
            "windows_current_change_verified": False},
        "initial_local_development_attempt": {"tests": 50, "setup_errors": 50,
            "cause": "new fixture omitted ensure_schema before insert_run", "corrected_in_test_fixture": True,
            "original_tool_output_full_log_available": False, "acceptance_result": False},
        "owned_file_sha256": {name: _sha(ROOT / name) for name in owned},
        "artifact_sha256": {path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name: _sha(path) for path in artifact_paths},
        "preserved_old_evidence": preservation,
        "claims": {"source_http_requests": 0, "forecasts_created": 0, "historic_forecasts_backfilled": 0,
            "old_evidence_changed": False, "price_or_label_values_changed": False,
            "target_formula_changed": False, "sixty_second_budget_changed": False,
            "score_changed": False, "fitter_changed": False, "trading_enabled": False,
            "out_of_sample_acceptance_verified": False, "specialists_accepted": 0},
        "remaining_work": ["Produce authentic future forecasts through the existing authorized runtime, persist them and register before their exact forward window starts.",
            "Pin actual contemporaneously available source archives; missing pins cannot be retroactively added to the same registration.",
            "Obtain genuinely dated publisher price revision and complete action provenance. Yahoo UNKNOWN captures remain excluded.",
            "Wait for the exact target sessions and physically observe/attach their complete asset and benchmark windows.",
            "Keep historical/OOS coverage, all-specialist acceptance and actual end-user Windows end-to-end separate from these fixture regressions."]}
    _write(output / "handoff.json", proof)
    print(json.dumps({"tests": proof["tests"], "actual_result": proof["actual_result"],
                      "old_evidence_byte_identical": preservation["all_old_evidence_byte_identical"]}, sort_keys=True))
    return 0 if tested.wasSuccessful() and preservation["all_old_evidence_byte_identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
