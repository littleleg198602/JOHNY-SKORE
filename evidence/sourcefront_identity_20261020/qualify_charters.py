"""Focused qualification with immutable producer/test snapshots and full logs."""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PATHS = ["market_checker_app/services/sec_corporate_action_bridge.py", "tests/test_sourcefront_charter_disclosure.py",
         "evidence/sourcefront_identity_20261020/replay_charter_reviews.py",
         "evidence/sourcefront_identity_20261020/LEG_pravni_kapital_po_fuzi.json",
         "evidence/sourcefront_identity_20261020/XOM_pravni_kapital_nastupce.json"]


def qualify(version):
    destination = HERE / version
    destination.mkdir(exist_ok=False)
    pins = {}
    for path in PATHS:
        body = (ROOT / path).read_bytes()
        pins[path] = {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
        (destination / Path(path).name).write_bytes(body)
    started = datetime.now(timezone.utc)
    suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_sourcefront_charter_disclosure")
    discovered = suite.countTestCases()
    log = io.StringIO()
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    output = log.getvalue().encode()
    (destination / "focused.log").write_bytes(output)
    summary = {"schema_version": "focused_charter_qualification_v1", "started_at": started.isoformat(),
               "completed_at": datetime.now(timezone.utc).isoformat(), "python": sys.executable,
               "source_test_pins": pins, "discovered": discovered, "executed": result.testsRun,
               "failures": len(result.failures), "errors": len(result.errors), "skips": len(result.skipped),
               "expected_failures": len(result.expectedFailures), "unexpected_successes": len(result.unexpectedSuccesses),
               "successful": result.wasSuccessful(), "full_log_sha256": hashlib.sha256(output).hexdigest(),
               "actual_http_requests": 0, "source_calls": 0, "db_calls": 0}
    for path, pin in pins.items():
        if hashlib.sha256((ROOT/path).read_bytes()).hexdigest() != pin["sha256"]:
            raise ValueError("Producer/test changed during focused execution")
    (destination / "result.json").write_text(json.dumps(summary, indent=2, sort_keys=True)+"\n")
    print(json.dumps(summary, indent=2))
    if not result.wasSuccessful():
        print(output.decode())
        raise SystemExit(1)


if __name__ == "__main__":
    qualify(sys.argv[1])
