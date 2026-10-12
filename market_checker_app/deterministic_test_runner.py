"""Run every deterministic test with durable output and immediate tracebacks."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sys
import unittest


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, value):
        for stream in self.streams:
            stream.write(value)
        self.flush()

    def flush(self):
        for stream in self.streams:
            stream.flush()


class _ImmediateResult(unittest.TextTestResult):
    def addError(self, test, err):
        super().addError(test, err)
        self.stream.writeln(self.errors[-1][1])
        self.stream.flush()

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.stream.writeln(self.failures[-1][1])
        self.stream.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-path", type=Path, default=Path("deterministic-tests.log"))
    parser.add_argument("--result-path", type=Path, default=Path("deterministic-tests.json"))
    args = parser.parse_args()
    args.log_path.parent.mkdir(parents=True, exist_ok=True)
    args.result_path.parent.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    suite = unittest.defaultTestLoader.discover("tests", pattern="test_*.py")
    count = suite.countTestCases()
    # Retain the original stream: Streamlit AppTest may replace sys.stderr.
    with args.log_path.open("w", encoding="utf-8") as log:
        result = unittest.TextTestRunner(stream=_Tee(sys.stderr, log), verbosity=2,
                                       resultclass=_ImmediateResult).run(suite)
    report = {"started_at": started, "ended_at": datetime.now(timezone.utc).isoformat(),
              "platform": platform.platform(), "python_version": platform.python_version(),
              "discovered_tests": count, "tests_run": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors),
              "skipped": len(result.skipped), "expected_failures": len(result.expectedFailures),
              "unexpected_successes": len(result.unexpectedSuccesses),
              "successful": result.wasSuccessful() and result.testsRun == count}
    args.result_path.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    return 0 if report["successful"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
