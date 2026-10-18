"""Focused offline qualification; does not repeat the complete baseline suite."""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]
LANE = ROOT / "evidence/batch10_contracts_20261018"
MODULES = ["tests.test_sec_supplier_revenue_denominator_review","tests.test_batch9_supplier_concentration"]
SOURCES = ["market_checker_app/collectors/sec_supplier_revenue_denominator_review.py",
    "market_checker_app/services/sec_supplier_concentration_review.py",
    "market_checker_app/services/sec_operational_metrics_review.py",
    "market_checker_app/services/sec_purchase_obligation_scope_review.py",
    "market_checker_app/services/sec_supply_chain_scope_review.py",
    "market_checker_app/services/sec_ir_disclosure_authority_review.py",
    "market_checker_app/services/sec_person_kind_review.py",
    *(name.replace(".","/")+".py" for name in MODULES)]


if __name__ == "__main__":
    started = datetime.now(timezone.utc).isoformat()
    suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    discovered = suite.countTestCases()
    stream = io.StringIO();before = time.perf_counter()
    result = unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    report = {"schema_version":1,"started_at":started,"completed_at":datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds":time.perf_counter()-before,"scope":"NEW_DENOMINATOR_CONSUMER_AND_DIRECT_CONCENTRATION_DEPENDENCY",
        "modules":MODULES,"discovered":discovered,"executed":result.testsRun,"successful":result.wasSuccessful(),
        "failures":len(result.failures),"errors":len(result.errors),"skips":len(result.skipped),
        "expected_failures":len(result.expectedFailures),"unexpected_successes":len(result.unexpectedSuccesses),
        "new_module_cases":unittest.defaultTestLoader.loadTestsFromName(MODULES[0]).countTestCases(),
        "source_sha256":{path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in SOURCES},
        "source_http_requests":0,"production_database_writes":0,"whole_baseline2231_repeated":False,
        "synthetic_mechanism_fixtures_count_as_live":False,"new_hosted_windows_qualification":False,
        "historical_oos_verified":False,
        "development_checks":[{"new_cases":24,"successful":True,
            "scope":"initial focused mechanisms and real retained replay before two additional numeric-bound/duplicate-ID regressions; not final qualification"}]}
    with (LANE/"tests.log").open("x",encoding="utf-8") as handle:handle.write(stream.getvalue())
    with (LANE/"tests.json").open("x",encoding="utf-8") as handle:
        json.dump(report,handle,sort_keys=True,indent=2,allow_nan=False);handle.write("\n")
    print(json.dumps({key:report[key] for key in ("discovered","executed","new_module_cases","successful","failures","errors","skips","elapsed_seconds")}))
    raise SystemExit(not result.wasSuccessful())
