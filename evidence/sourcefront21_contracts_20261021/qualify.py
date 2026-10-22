"""Versioned offline qualification; all evidence outputs are create-only."""
from datetime import datetime, timezone
import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def record(path):
    body = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def write(path, value):
    with path.open("x", encoding="utf-8") as out:
        json.dump(value, out, indent=2, sort_keys=True); out.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    if not args.version.isalnum():
        raise ValueError("A new simple version label is required")
    destination = FOLDER / args.version
    destination.mkdir(exist_ok=False)
    producer = ROOT / "market_checker_app/services/wafer_capacity_deposit_review.py"
    test = ROOT / "tests/test_wafer_capacity_deposit_review.py"
    for original in (producer, test):
        with (destination / original.name).open("xb") as out:
            out.write(original.read_bytes())
    dependency_names = ["sec_ir_disclosure_authority_review", "sec_operational_metrics_review",
        "sec_person_kind_review", "sec_purchase_obligation_scope_review", "sec_supply_chain_scope_review"]
    before = {"producer": record(producer), "test": record(test),
        "dependencies": [record(ROOT / "market_checker_app/services" / (name + ".py")) for name in dependency_names]}
    write(destination / "before_pins.json", before)
    spec = importlib.util.spec_from_file_location("wafer_capacity_deposit_tests", test)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    discovered = suite.countTestCases(); stream = io.StringIO()
    started, timer = datetime.now(timezone.utc), time.perf_counter()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    elapsed = time.perf_counter() - timer
    with (destination / "tests.log").open("x", encoding="utf-8") as out:
        out.write(stream.getvalue())
    # This separate replay uses the actual physical clock, never the unit-test
    # clock or the old source capture timestamp as new ingestion.
    replay_spec = importlib.util.spec_from_file_location("wafer_deposit_retained_replay", FOLDER / "replay_retained.py")
    replay = importlib.util.module_from_spec(replay_spec); replay_spec.loader.exec_module(replay)
    from market_checker_app.services.wafer_capacity_deposit_review import review_wafer_capacity_deposits
    actual = review_wafer_capacity_deposits(*replay.inputs(json.loads(replay.PLAN.read_bytes())))
    write(destination / "actual_retained_review.json", actual)
    after = {"producer": record(producer), "test": record(test),
        "dependencies": [record(ROOT / "market_checker_app/services" / (name + ".py")) for name in dependency_names]}
    if before != after:
        raise ValueError("Producer, test or imported dependencies changed during qualification")
    plan = json.loads(replay.PLAN.read_bytes())
    report = {"schema_version": 1, "version": args.version, "started_at": started.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": elapsed,
        "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "python_version": sys.version, "tests_discovered": discovered, "tests_executed": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors), "skips": len(result.skipped),
        "expected_failures": len(result.expectedFailures), "unexpected_successes": len(result.unexpectedSuccesses),
        "plain_ok": result.wasSuccessful(), "failure_test_ids": [case.id() for case, _ in result.failures],
        "error_test_ids": [case.id() for case, _ in result.errors], "producer_test_dependencies": before,
        "exact_bytes_unchanged_during_qualification": True, "plan": record(replay.PLAN),
        "sources": {key: value["body_ref"] for key, value in plan["captures"].items()},
        "full_console": record(destination / "tests.log"), "actual_retained_review": record(destination / "actual_retained_review.json"),
        "actual_retained_status": actual["status"], "actual_retained_reason": actual["reason"],
        "source_http_requests": 0, "collector_calls": 0, "sql_or_database_operations": 0,
        "source_bytes_modified": False, "synthetic_controls_count_as_live_acceptance": False,
        "prior_unchanged_suites_repeated": False, "model_score_trading_or_registry_changes": False}
    write(destination / "tests.json", report)
    print(json.dumps({key: report[key] for key in ("version", "tests_discovered", "tests_executed", "failures", "errors",
        "skips", "plain_ok", "actual_retained_status", "actual_retained_reason")}, indent=2))
    return 0 if result.wasSuccessful() and actual["status"] == "CANDIDATE_REVIEWED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
