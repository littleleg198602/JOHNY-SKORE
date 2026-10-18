"""Save the exact focused offline qualification, preserving every earlier run."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import argparse
import hashlib
import io
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
MODULES = ["tests.test_sec_person_role_claim_review"]
SOURCES = ["market_checker_app/collectors/sec_person_role_claim_review.py",
    "tests/test_sec_person_role_claim_review.py",
    "market_checker_app/services/sec_person_kind_review.py",
    "market_checker_app/services/sec_owner_instrument_scope_review.py",
    "market_checker_app/services/sec_complete_packet_review.py",
    "market_checker_app/services/sec_form4_person_bridge.py",
    "market_checker_app/services/ir_primary_scout_service.py",
    "evidence/batch10_sec_20261018/build_review.py",
    "evidence/batch10_sec_20261018/run_focused_tests.py"]


def run(name):
    if not name.replace("_", "").isalnum() or len(name)>60:
        raise ValueError("Select a bounded new qualification name")
    log_path,report_path = ROOT/(name+".log"),ROOT/(name+".json")
    if log_path.exists() or report_path.exists():
        raise ValueError("Never overwrite earlier source/test qualification")
    start = datetime.now(timezone.utc); timer = time.perf_counter()
    suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES); count = suite.countTestCases()
    output = io.StringIO()
    with redirect_stdout(output),redirect_stderr(output),patch("socket.socket.connect",side_effect=AssertionError("No HTTP in offline focused tests")):
        result = unittest.TextTestRunner(stream=output,verbosity=2).run(suite)
    raw = output.getvalue().encode("utf-8")
    with log_path.open("xb") as stream: stream.write(raw)
    report = {"started_at":start.isoformat(),"completed_at":datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds":time.perf_counter()-timer,"modules":MODULES,"discovered":count,
        "executed":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),
        "skips":len(result.skipped),"expected_failures":len(result.expectedFailures),
        "unexpected_successes":len(result.unexpectedSuccesses),"successful":result.wasSuccessful(),
        "live_http_requests_in_tests":0,"windows_or_oos_claim":False,
        "log_sha256":hashlib.sha256(raw).hexdigest(),
        "source_pins":{name:{"bytes":(REPO/name).stat().st_size,
            "sha256":hashlib.sha256((REPO/name).read_bytes()).hexdigest()} for name in SOURCES}}
    with report_path.open("x",encoding="utf-8",newline="\n") as stream:
        stream.write(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(json.dumps({key:report[key] for key in ("discovered","executed","failures","errors","skips","successful")}))
    if not result.wasSuccessful(): raise SystemExit(1)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name",required=True)
    run(parser.parse_args().name)
