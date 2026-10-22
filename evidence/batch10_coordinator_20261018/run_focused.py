"""Preserve each focused result and full original unittest output exclusively."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stem", required=True, type=Path)
    parser.add_argument("tests", nargs="+")
    args = parser.parse_args()
    started = datetime.now(timezone.utc)
    clock = time.perf_counter()
    suite = unittest.defaultTestLoader.loadTestsFromNames(args.tests)
    discovered = suite.countTestCases()
    names = []
    def collect(s):
        for test in s:
            if isinstance(test, unittest.TestSuite): collect(test)
            else: names.append(test.id())
    collect(suite)
    with args.stem.with_suffix(".log").open("x", encoding="utf-8") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    report = {"started_at": started.isoformat(), "ended_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": time.perf_counter() - clock, "platform": platform.platform(), "python": platform.python_version(),
        "selected": args.tests, "exact_test_ids": names, "discovered": discovered, "executed": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
        "expected_failures": len(result.expectedFailures), "unexpected_successes": len(result.unexpectedSuccesses),
        "successful": result.wasSuccessful() and result.testsRun == discovered,
        "full_log_sha256": hashlib.sha256(args.stem.with_suffix(".log").read_bytes()).hexdigest()}
    raw = (json.dumps(report, indent=2) + "\n").encode()
    with args.stem.with_suffix(".json").open("xb") as handle: handle.write(raw)
    print(json.dumps(report))
    return 0 if report["successful"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
