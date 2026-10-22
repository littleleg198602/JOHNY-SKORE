"""Versioned, offline focused qualification; never overwrite a prior result."""
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


EVIDENCE = Path(__file__).resolve().parent
REPO = EVIDENCE.parents[1]
SOURCE = REPO / "market_checker_app/services/user_windows_readiness_capture.py"
TEST = REPO / "tests/test_user_windows_readiness_capture.py"


def pin(path):
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def run(version):
    if re.fullmatch(r"qualification_v[1-9][0-9]*", version) is None:
        raise ValueError("Use a new bounded qualification version")
    folder = EVIDENCE / version
    folder.mkdir(exist_ok=False)
    source, test = pin(SOURCE), pin(TEST)
    shutil.copyfile(SOURCE, folder / SOURCE.name)
    shutil.copyfile(TEST, folder / TEST.name)
    sys.path.insert(0, str(REPO))
    started, timer = datetime.now(timezone.utc), time.monotonic()
    suite = unittest.defaultTestLoader.discover(str(REPO / "tests"), pattern=TEST.name)
    count = suite.countTestCases()
    with (folder / "tests.log").open("x", encoding="utf-8") as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    report = {"scope": "SYNTHETIC_OFFLINE_BOUNDARY_TESTS_ONLY", "started_at": started.isoformat(),
        "ended_at": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": time.monotonic() - timer,
        "discovered_tests": count, "tests_run": result.testsRun, "failures": len(result.failures),
        "errors": len(result.errors), "skipped": len(result.skipped),
        "expected_failures": len(result.expectedFailures), "unexpected_successes": len(result.unexpectedSuccesses),
        "successful": result.wasSuccessful() and result.testsRun == count,
        "producer_before": source, "producer_after": pin(SOURCE), "test_before": test, "test_after": pin(TEST),
        "runner": pin(Path(__file__)), "log": pin(folder / "tests.log"),
        "native_windows_acceptance": False, "network_requests": 0, "actual_production_DB_writes": 0,
        "fixture_forecasts_are_not_actual_forecasts": True}
    with (folder / "tests.json").open("x", encoding="utf-8") as out:
        out.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if not report["successful"]:
        print((folder / "tests.log").read_text())
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    run(parser.parse_args().version)
