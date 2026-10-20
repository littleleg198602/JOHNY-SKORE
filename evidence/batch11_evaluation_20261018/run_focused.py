"""Preserve every focused producer/test/log version; no full-suite discovery."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import time
import unittest


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SOURCE = REPO / "market_checker_app/services/user_windows_analysis_capture.py"
TEST = REPO / "tests/test_user_windows_analysis_capture.py"


def pin(path):
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def main(version):
    if re.fullmatch(r"qualification_v[1-9][0-9]*", version) is None:
        raise ValueError("Select a new bounded version")
    destination = HERE / version
    destination.mkdir(exist_ok=False)
    source, test = pin(SOURCE), pin(TEST)
    for path in (SOURCE, TEST):
        shutil.copyfile(path, destination / path.name)
    sys.path.insert(0, str(REPO))
    started, timer = datetime.now(timezone.utc), time.monotonic()
    suite = unittest.defaultTestLoader.discover(str(REPO / "tests"), pattern=TEST.name)
    count = suite.countTestCases()
    with (destination / "tests.log").open("x", encoding="utf-8") as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    report = {"scope": "SYNTHETIC_OFFLINE_BOUNDARY_TESTS_NOT_NATIVE_RUN_ACCEPTANCE", "started_at": started.isoformat(),
        "ended_at": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": time.monotonic() - timer,
        "discovered_tests": count, "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "skipped": len(result.skipped), "expected_failures": len(result.expectedFailures), "unexpected_successes": len(result.unexpectedSuccesses),
        "successful": result.wasSuccessful() and result.testsRun == count,
        "producer_before": source, "producer_after": pin(SOURCE), "test_before": test, "test_after": pin(TEST),
        "runner": pin(Path(__file__)), "log": pin(destination / "tests.log"),
        "source_HTTP_requests": 0, "original_retained_DB_access": False, "native_windows_acceptance": False,
        "actual_native_pipeline_executions": 0, "all_populated_pipeline_results_are_test_doubles": True}
    with (destination / "tests.json").open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if not report["successful"]:
        print((destination / "tests.log").read_text())
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    main(parser.parse_args().version)
