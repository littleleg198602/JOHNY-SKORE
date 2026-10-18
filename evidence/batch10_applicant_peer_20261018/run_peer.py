"""Run only the new peer suite with healthcare dependencies and exact producer."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CANDIDATE = Path("/workspace/scratch/334b3a149083/JOHNY-batch8-healthcare-20261015")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def main():
    run = sys.argv[1] if len(sys.argv) == 2 and sys.argv[1].isalnum() else None
    if run is None:
        raise SystemExit("Choose new before or after result identifier")
    log_path, result_path = HERE / (run+".log"), HERE / (run+".json")
    assert not log_path.exists() and not result_path.exists()
    sys.path.insert(0, str(CANDIDATE))
    test_path = ROOT / "tests/test_batch10_applicant_peer.py"
    cases = module(test_path, "batch10_independent_applicant_peer")
    if run == "before":
        cases.target = module(HERE / "candidate_before_diagnostics/fda_legal_applicant_bridge_review.py", "batch10_frozen_applicant_producer")
    else:
        assert Path(cases.target.__file__).resolve() == CANDIDATE / "market_checker_app/collectors/fda_legal_applicant_bridge_review.py"
    producer = Path(cases.target.__file__)
    source_before, test_before = sha(producer), sha(test_path)
    started = datetime.now(timezone.utc).isoformat()
    began = time.perf_counter()
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(cases.ApplicantScopePeerTests))
    body = stream.getvalue().encode()
    with log_path.open("xb") as output:
        output.write(body)
    assert sha(producer) == source_before and sha(test_path) == test_before
    record = {"started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": time.perf_counter()-began, "selected_producer": str(producer), "producer_sha256": source_before,
        "peer_test_sha256": test_before, "candidate_dependency_root": str(CANDIDATE), "only_new_peer_suite_executed": True,
        "executed": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "skips": len(result.skipped),
        "expected_failures": len(result.expectedFailures), "unexpected_successes": len(result.unexpectedSuccesses),
        "successful": result.wasSuccessful(), "failed_methods": [str(case) for case, _ in result.failures],
        "log_sha256": hashlib.sha256(body).hexdigest(), "new_HTTP_DB_or_candidate_writes": 0,
        "synthetic_cases_are_not_acceptance": True, "top_level_ownership_promotion_not_claimed": True}
    with result_path.open("x") as output:
        json.dump(record, output, indent=2, sort_keys=True)
        output.write("\n")
    print(body.decode())
    print(json.dumps(record, sort_keys=True))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
