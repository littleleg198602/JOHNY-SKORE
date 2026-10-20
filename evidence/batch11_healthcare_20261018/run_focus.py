"""Only this lane's new chronology tests, preserving every qualification log."""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import time
import unittest

from tests.test_fda_applicant_chronology_review import ApplicantChronologyTests

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    label = sys.argv[1] if len(sys.argv) == 2 and sys.argv[1].isalnum() else "focus"
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ApplicantChronologyTests)
    producer = ROOT / "market_checker_app/services/fda_applicant_chronology_review.py"
    tests = ROOT / "tests/test_fda_applicant_chronology_review.py"
    pins = {"producer": sha(producer), "tests": sha(tests)}
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "discovered": suite.countTestCases(),
              "source_sha256": pins, "synthetic_positives_are_not_LIVE": True, "full_suite_rerun": False}
    timer = time.perf_counter()
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    raw = stream.getvalue().encode()
    with (HERE / (label + ".log")).open("xb") as output:
        output.write(raw)
    if pins != {"producer": sha(producer), "tests": sha(tests)}:
        raise ValueError("Qualified source changed during test run")
    report.update(completed_at=datetime.now(timezone.utc).isoformat(), elapsed_seconds=time.perf_counter() - timer,
        executed=result.testsRun, failures=len(result.failures), errors=len(result.errors), skips=len(result.skipped),
        expected_failures=len(result.expectedFailures), unexpected_successes=len(result.unexpectedSuccesses),
        successful=result.wasSuccessful(), plain_OK=raw.rstrip().endswith(b"\nOK"),
        log_sha256=hashlib.sha256(raw).hexdigest(), failed_methods=[str(case) for case, _ in result.failures],
        error_methods=[str(case) for case, _ in result.errors])
    with (HERE / (label + ".json")).open("x") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    print(json.dumps(report))
    if not result.wasSuccessful():
        print(raw.decode())
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
