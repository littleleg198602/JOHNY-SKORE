"""Qualify only additive temporal regressions, never the original32 methods."""
from datetime import datetime, timezone
import argparse
import ast
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import time
import unittest


FOLDER = Path(__file__).resolve().parent
ROOT = FOLDER.parents[2]
sys.path.insert(0, str(ROOT))


def pin(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def write(path, value):
    with path.open("x", encoding="utf-8") as out:
        json.dump(value, out, indent=2, sort_keys=True); out.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    if not args.version.isalnum():
        raise ValueError("Select a fresh simple version")
    destination = FOLDER / args.version
    destination.mkdir(exist_ok=False)
    producer = ROOT / "market_checker_app/services/wafer_capacity_deposit_review.py"
    test = ROOT / "tests/test_wafer_capacity_deposit_review.py"
    before = {"producer": pin(producer), "test": pin(test)}
    for path in (producer, test):
        with (destination / path.name).open("xb") as out:
            out.write(path.read_bytes())
    old = ast.parse((FOLDER / "before/test_wafer_capacity_deposit_review.py").read_text())
    new = ast.parse(test.read_text())
    original_class = next(node for node in old.body if isinstance(node, ast.ClassDef) and node.name == "WaferCapacityDepositReviewTests")
    current_class = next(node for node in new.body if isinstance(node, ast.ClassDef) and node.name == original_class.name)
    if ast.dump(original_class) != ast.dump(current_class):
        raise ValueError("The original32 test bodies must stay unchanged")
    spec = importlib.util.spec_from_file_location("wafer_temporal_tests", test)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(module.WaferCapacityDepositTemporalReviewTests)
    discovered, stream = suite.countTestCases(), io.StringIO()
    started, timer = datetime.now(timezone.utc), time.perf_counter()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    elapsed = time.perf_counter() - timer
    with (destination / "new_tests.log").open("x", encoding="utf-8") as out:
        out.write(stream.getvalue())
    from market_checker_app.services.wafer_capacity_deposit_review import review_wafer_capacity_deposits
    actual = review_wafer_capacity_deposits(*module.retained_inputs())
    write(destination / "actual_retained_review.json", actual)
    after = {"producer": pin(producer), "test": pin(test)}
    if before != after:
        raise ValueError("Qualified bytes changed during the focused run")
    report = {"schema_version": 1, "version": args.version, "started_at": started.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": elapsed,
        "tests_discovered": discovered, "tests_executed": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors), "skips": len(result.skipped),
        "expected_failures": len(result.expectedFailures), "unexpected_successes": len(result.unexpectedSuccesses),
        "plain_ok": result.wasSuccessful(), "original32_methods_rerun": 0, "original32_test_bodies_ast_unchanged": True,
        "producer_test_before": before, "producer_test_after": after, "qualified_bytes_unchanged": True,
        "full_console": pin(destination / "new_tests.log"), "actual_retained_review": pin(destination / "actual_retained_review.json"),
        "actual_status": actual["status"], "actual_reason": actual["reason"],
        "source_http_requests": 0, "collector_calls": 0, "sql_database_operations": 0,
        "registry_or_raw_source_edits": False, "synthetic_controls_are_live_evidence": False}
    write(destination / "new_tests.json", report)
    print(json.dumps({key: report[key] for key in ("tests_discovered", "tests_executed", "failures", "errors", "skips",
        "plain_ok", "original32_methods_rerun", "actual_status", "actual_reason")}, indent=2))
    return 0 if result.wasSuccessful() and actual["status"] == "CANDIDATE_REVIEWED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
