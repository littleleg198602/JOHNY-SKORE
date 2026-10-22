"""Run the exact unchanged seven-method peer regression on this final producer."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TEST = HERE / "independent_peer_originals/test_batch10_readiness_peer.py"
SOURCE = REPO / "market_checker_app/services/user_windows_readiness_capture.py"


def pin(path):
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def main():
    folder = HERE / "peer_recheck_final"
    folder.mkdir(exist_ok=False)
    sys.path.insert(0, str(REPO))
    before = pin(SOURCE)
    spec = importlib.util.spec_from_file_location("unchanged_readiness_peer", TEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    count, started, timer = suite.countTestCases(), datetime.now(timezone.utc), time.monotonic()
    with (folder / "tests.log").open("x", encoding="utf-8") as log, \
         patch("socket.socket.connect", side_effect=AssertionError("No peer source HTTP")):
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    report = {"scope": "UNCHANGED_INDEPENDENT_SYNTHETIC_PEER_REGRESSION", "started_at": started.isoformat(),
        "ended_at": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": time.monotonic() - timer,
        "discovered_tests": count, "tests_run": result.testsRun, "failures": len(result.failures),
        "errors": len(result.errors), "skipped": len(result.skipped), "expected_failures": len(result.expectedFailures),
        "unexpected_successes": len(result.unexpectedSuccesses), "successful": result.wasSuccessful() and result.testsRun == count,
        "producer_before": before, "producer_after": pin(SOURCE), "unchanged_test": pin(TEST),
        "runner": pin(Path(__file__)), "log": pin(folder / "tests.log"),
        "network_requests": 0, "actual_production_database_writes": 0, "real_windows_acceptance": False}
    with (folder / "tests.json").open("x", encoding="utf-8") as out:
        out.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if not report["successful"]:
        print((folder / "tests.log").read_text())
        raise SystemExit(1)


if __name__ == "__main__":
    main()
