"""Run the changed lane suite and exact unchanged seven-method peer suite."""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import time
import unittest

from market_checker_app.collectors import fda_legal_applicant_bridge_review as producer
from tests.test_fda_legal_applicant_bridge_review import LegalApplicantBridgeTests
from evidence.batch10_applicant_clock_fix_20261018.regulatory_unchanged_peer import ApplicantScopePeerTests

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    paths = {"producer": Path(producer.__file__), "lane_tests": ROOT / "tests/test_fda_legal_applicant_bridge_review.py",
             "unchanged_peer": HERE / "regulatory_unchanged_peer.py"}
    pins = {key: sha(path) for key, path in paths.items()}
    if pins["unchanged_peer"] != "76c8376da28f1a2a55a5aabf3eed88ad9dfd1ce281b9f588154e39f70e337a5f":
        raise ValueError("Unchanged seven-method peer source pin conflict")
    suites = {"focused": unittest.defaultTestLoader.loadTestsFromTestCase(LegalApplicantBridgeTests),
              "unchanged_peer": unittest.defaultTestLoader.loadTestsFromTestCase(ApplicantScopePeerTests)}
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "source_sha256": pins,
              "synthetic_positive_contracts_are_not_LIVE": True, "new_HTTP_DB_or_registry_writes": 0,
              "full_suite_rerun": False, "suites": {}}
    success = True
    for name, suite in suites.items():
        discovered = suite.countTestCases()
        started = datetime.now(timezone.utc).isoformat()
        timer = time.perf_counter()
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        raw = stream.getvalue().encode()
        log = HERE / (name + ".log")
        with log.open("xb") as output:
            output.write(raw)
        report["suites"][name] = {"started_at": started, "completed_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": time.perf_counter() - timer, "discovered": discovered, "executed": result.testsRun,
            "failures": len(result.failures), "errors": len(result.errors), "skips": len(result.skipped),
            "expected_failures": len(result.expectedFailures), "unexpected_successes": len(result.unexpectedSuccesses),
            "successful": result.wasSuccessful(), "plain_OK": raw.rstrip().endswith(b"\nOK"),
            "log_path": log.relative_to(ROOT).as_posix(), "log_sha256": hashlib.sha256(raw).hexdigest()}
        success = success and result.wasSuccessful()
        print(json.dumps({name: report["suites"][name]}))
    if pins != {key: sha(path) for key, path in paths.items()}:
        raise ValueError("Producer/tests changed during qualification")
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    report["successful"] = success
    with (HERE / "test_results.json").open("x") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
