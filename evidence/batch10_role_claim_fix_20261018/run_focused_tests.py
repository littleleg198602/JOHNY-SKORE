"""Qualify current role guards and the three unchanged independent peer methods."""
import argparse
import ast
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
SOURCES = ["market_checker_app/collectors/sec_person_role_claim_review.py",
    "tests/test_sec_person_role_claim_review.py",
    "evidence/batch10_role_claim_fix_20261018/build_review.py",
    "evidence/batch10_role_claim_fix_20261018/run_focused_tests.py",
    "evidence/batch10_role_claim_fix_20261018/before/peer_test.py",
    "evidence/batch10_sec_20261018/build_review.py",
    "market_checker_app/services/sec_person_kind_review.py",
    "market_checker_app/services/sec_owner_instrument_scope_review.py",
    "market_checker_app/services/sec_complete_packet_review.py",
    "market_checker_app/services/sec_form4_person_bridge.py",
    "market_checker_app/services/ir_primary_scout_service.py"]


def pin(path):
    raw = path.read_bytes()
    return {"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()}


def test_methods(path):
    parsed = ast.parse(path.read_bytes())
    return {node.name:ast.dump(node,include_attributes=False) for node in ast.walk(parsed)
            if isinstance(node,ast.FunctionDef) and node.name.startswith("test_")}


def run(name):
    if not name.replace("_", "").isalnum() or len(name)>60: raise ValueError("Use a fresh bounded qualification name")
    log_path,report_path = ROOT/(name+".log"),ROOT/(name+".json")
    if log_path.exists() or report_path.exists(): raise ValueError("Earlier qualifications must remain immutable")
    before = test_methods(ROOT/"before/owner_test.py")
    current = test_methods(REPO/"tests/test_sec_person_role_claim_review.py")
    if any(current.get(key)!=value for key,value in before.items()):
        raise ValueError("An original owner test/assertion body changed")
    spec = importlib.util.spec_from_file_location("role_claim_peer_frozen",ROOT/"before/peer_test.py")
    peer = importlib.util.module_from_spec(spec); spec.loader.exec_module(peer)
    suite = unittest.TestSuite([
        unittest.defaultTestLoader.loadTestsFromName("tests.test_sec_person_role_claim_review"),
        unittest.defaultTestLoader.loadTestsFromModule(peer)])
    count=suite.countTestCases(); output=io.StringIO(); start=datetime.now(timezone.utc);timer=time.perf_counter()
    with redirect_stdout(output),redirect_stderr(output),patch("socket.socket.connect",side_effect=AssertionError("No HTTP in role qualification")):
        result=unittest.TextTestRunner(stream=output,verbosity=2).run(suite)
    raw=output.getvalue().encode("utf-8")
    with log_path.open("xb") as stream:stream.write(raw)
    report={"started_at":start.isoformat(),"completed_at":datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds":time.perf_counter()-timer,"discovered":count,"executed":result.testsRun,
        "failures":len(result.failures),"errors":len(result.errors),"skips":len(result.skipped),
        "expected_failures":len(result.expectedFailures),"unexpected_successes":len(result.unexpectedSuccesses),
        "successful":result.wasSuccessful(),"original_owner_test_bodies_preserved":len(before),
        "current_owner_test_methods":len(current),"unchanged_independent_peer_test_methods":3,
        "modules":["tests.test_sec_person_role_claim_review","before/peer_test.py"],
        "live_http_requests":0,"DB_mutations":0,"windows_or_oos_claim":False,
        "log_sha256":hashlib.sha256(raw).hexdigest(),"source_pins":{name:pin(REPO/name) for name in SOURCES}}
    with report_path.open("x",encoding="utf-8",newline="\n") as stream:
        stream.write(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(json.dumps({key:report[key] for key in ("discovered","executed","failures","errors","skips","successful")}))
    if not result.wasSuccessful():raise SystemExit(1)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--name",required=True)
    run(parser.parse_args().name)
