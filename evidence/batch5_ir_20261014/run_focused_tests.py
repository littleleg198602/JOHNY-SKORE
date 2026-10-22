"""Persist the focused offline verification result without making HTTP calls."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import unittest


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
MODULES = ["tests.test_batch5_ir_primary_scout", "tests.test_batch4_ir_primary_verification",
           "tests.test_public_source_budget"]


def run():
    started = datetime.now(timezone.utc)
    timer = time.perf_counter()
    suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    with (ROOT / "tests.log").open("w", encoding="utf-8", newline="\n") as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    evidence = {
        "started_at": started.isoformat(), "completed_at": datetime.now(timezone.utc).isoformat(),
        "modules": MODULES, "discovered": suite.countTestCases() if hasattr(suite, "countTestCases") else result.testsRun,
        "executed": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "skips": len(result.skipped), "expected_failures": len(result.expectedFailures),
        "unexpected_successes": len(result.unexpectedSuccesses), "successful": result.wasSuccessful(),
        "elapsed_seconds": time.perf_counter() - timer, "live_source_requests_in_this_test_run": 0,
        "log_sha256": hashlib.sha256((ROOT / "tests.log").read_bytes()).hexdigest(),
        "source_sha256": {str(path): hashlib.sha256((REPO / path).read_bytes()).hexdigest() for path in (
            "market_checker_app/services/ir_primary_scout_service.py", "market_checker_app/ir_primary_runner.py",
            "tests/test_batch5_ir_primary_scout.py", "market_checker_app/services/ir_primary_verification_service.py",
            "market_checker_app/services/news_scout_service.py")},
    }
    (ROOT / "tests.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: evidence[key] for key in ("executed", "failures", "errors", "skips", "successful")}))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(run())
