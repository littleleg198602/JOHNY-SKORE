"""Focused offline qualification for the new reviewer and direct dependencies."""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]
LANE = ROOT / "evidence/batch8_contracts_20261015"
MODULES = ["tests.test_batch8_purchase_obligation_scope", "tests.test_batch7_supply_chain_scope",
           "tests.test_batch6_sec_operational_metrics", "tests.test_batch7_sec_ir_authority"]
SOURCE_PATHS = ["market_checker_app/services/sec_purchase_obligation_scope_review.py",
    "market_checker_app/services/sec_supply_chain_scope_review.py",
    "market_checker_app/services/sec_operational_metrics_review.py",
    "market_checker_app/services/sec_ir_disclosure_authority_review.py",
    "market_checker_app/services/sec_person_kind_review.py",
    *(name.replace(".", "/") + ".py" for name in MODULES)]


if __name__ == "__main__":
    started = datetime.now(timezone.utc).isoformat()
    suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    discovered = suite.countTestCases()
    stream = io.StringIO(); before = time.perf_counter()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    report = {"schema_version": 1, "started_at": started,
        "completed_at": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": time.perf_counter() - before,
        "scope": "OFFLINE_CHANGED_LANE_AND_DIRECT_REVIEW_DEPENDENCIES", "modules": MODULES,
        "discovered": discovered, "executed": result.testsRun, "successful": result.wasSuccessful(),
        "failures": len(result.failures), "errors": len(result.errors), "skips": len(result.skipped),
        "expected_failures": len(result.expectedFailures), "unexpected_successes": len(result.unexpectedSuccesses),
        "new_module_cases": unittest.defaultTestLoader.loadTestsFromName(MODULES[0]).countTestCases(),
        "source_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in SOURCE_PATHS},
        "source_http_attempts": 0, "production_database_writes": 0, "fixtures_count_as_live": False,
        "windows_end_to_end_verified": False, "historical_oos_verified": False,
        "development_checks": [
            {"new_cases": 21, "successful": True, "qualification": "initial implementation before namespace/index regressions"},
            {"new_cases": 25, "failures": 1, "qualification": "new table-scope regression initially pinned the wrapper instead of the inner div; fixture pin corrected, guard unchanged"}]}
    with (LANE / "tests.log").open("x", encoding="utf-8") as handle:
        handle.write(stream.getvalue())
    with (LANE / "tests.json").open("x", encoding="utf-8") as handle:
        json.dump(report, handle, sort_keys=True, indent=2); handle.write("\n")
    print(json.dumps({key: report[key] for key in ("discovered", "executed", "successful", "failures", "errors", "skips", "elapsed_seconds")}))
    raise SystemExit(not result.wasSuccessful())
