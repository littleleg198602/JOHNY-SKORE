"""Run unchanged peer tests against an explicitly selected evaluator worktree."""
import argparse
from contextlib import redirect_stderr,redirect_stdout
from datetime import datetime,timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent
REPO=ROOT.parents[1]
TEST=REPO/"tests/test_batch10_readiness_peer.py"


def pin(path):
    raw=path.read_bytes()
    return {"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()}


def run(candidate_root,name):
    if not name.replace("_","").isalnum():raise ValueError("Select a new bounded run name")
    log_path,report_path=ROOT/(name+".log"),ROOT/(name+".json")
    if log_path.exists() or report_path.exists():raise ValueError("Preserve every prior qualification")
    source=candidate_root/"market_checker_app/services/user_windows_readiness_capture.py"
    before=pin(source)
    sys.path.insert(0,str(candidate_root))
    spec=importlib.util.spec_from_file_location("batch10_readiness_peer",TEST)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    suite=unittest.defaultTestLoader.loadTestsFromModule(module)
    count=suite.countTestCases();out=io.StringIO();started=datetime.now(timezone.utc);timer=time.perf_counter()
    with redirect_stdout(out),redirect_stderr(out),patch("socket.socket.connect",side_effect=AssertionError("No HTTP in peer fixtures")):
        result=unittest.TextTestRunner(stream=out,verbosity=2).run(suite)
    raw=out.getvalue().encode()
    with log_path.open("xb") as f:f.write(raw)
    report={"started_at":started.isoformat(),"completed_at":datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds":time.perf_counter()-timer,"candidate_before":before,"candidate_after":pin(source),
        "test_pin":pin(TEST),"runner_pin":pin(Path(__file__)),"candidate_root":str(candidate_root),
        "discovered":count,"executed":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),
        "skips":len(result.skipped),"expected_failures":len(result.expectedFailures),
        "unexpected_successes":len(result.unexpectedSuccesses),"successful":result.wasSuccessful(),
        "log_sha256":hashlib.sha256(raw).hexdigest(),"source_http_requests":0,
        "actual_production_database_mutations":0,"synthetic_fixture_only":True,"real_windows_acceptance_claim":False}
    with report_path.open("x") as f:f.write(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(json.dumps({key:report[key] for key in ("candidate_before","discovered","executed","failures","errors","successful")}))
    if not result.wasSuccessful():raise SystemExit(1)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-root",required=True,type=Path)
    parser.add_argument("--name",required=True)
    args=parser.parse_args();run(args.candidate_root.resolve(),args.name)
