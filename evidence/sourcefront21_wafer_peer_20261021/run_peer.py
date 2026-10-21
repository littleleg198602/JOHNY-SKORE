"""Preserve exact source/test/log for independent before/after peer methods."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PRODUCER = "market_checker_app/services/wafer_capacity_deposit_review.py"
TEST = "tests/test_wafer_capacity_deposit_peer.py"


def pin(root, relative):
    body = (root / relative).read_bytes()
    return {"path": relative, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def write_new(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("before", "after"))
    parser.add_argument("--producer-root", type=Path, default=ROOT)
    parser.add_argument("--expected-producer-sha", required=True)
    args = parser.parse_args()
    producer_root = args.producer_root.resolve()
    output = HERE / args.phase
    output.mkdir(exist_ok=True)
    producer = pin(producer_root, PRODUCER)
    if producer["sha256"] != args.expected_producer_sha:
        raise ValueError("Qualified frozen producer hash differs before peer execution")
    test = pin(ROOT, TEST)
    with (output / "test_wafer_capacity_deposit_peer.py").open("xb") as stream:
        stream.write((ROOT / TEST).read_bytes())
    if args.phase == "after":
        with (output / "wafer_capacity_deposit_review.py").open("xb") as stream:
            stream.write((producer_root / PRODUCER).read_bytes())
    started = datetime.now(timezone.utc).isoformat()
    tick = time.perf_counter()
    sys.path.insert(0, str(producer_root))
    sys.path.insert(1, str(ROOT))
    with (output / "tests.log").open("x", encoding="utf-8", newline="\n") as stream:
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_wafer_capacity_deposit_peer.py")
        discovered = suite.countTestCases()
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    current_producer, current_test = pin(producer_root, PRODUCER), pin(ROOT, TEST)
    module = sys.modules.get("test_wafer_capacity_deposit_peer")
    if module is not None:
        write_new(output / "case_diagnostics.json", module.PEER_DIAGNOSTICS)
        write_new(output / "actual_control_report.json", module.ACTUAL_CONTROL_REPORT)
    report = {"schema_version": "independent_wafer_capacity_deposit_peer_v1", "phase": args.phase,
        "owner_commit": subprocess.check_output(["git", "-C", str(producer_root), "rev-parse", "HEAD"]).decode().strip(),
        "producer_before": producer, "producer_after": current_producer,
        "test_before": test, "test_after": current_test,
        "producer_and_test_unchanged": producer == current_producer and test == current_test,
        "started_at": started, "completed_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": time.perf_counter()-tick, "discovered": discovered, "executed": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors), "skips": len(result.skipped),
        "expected_failures": len(result.expectedFailures), "unexpected_successes": len(result.unexpectedSuccesses),
        "successful": result.wasSuccessful(),
        "full_original_log": pin(ROOT, (output / "tests.log").relative_to(ROOT).as_posix()),
        "http_sql_collector_calls": 0, "source_capture_acceptance_or_full21": False,
        "fixture_scope": "Unchanged real retained control plus synthetic rehashed in-memory negatives; never new LIVE responses"}
    write_new(output / "tests.json", report)
    print(json.dumps({key: report[key] for key in ("phase", "discovered", "executed", "failures", "errors", "successful", "producer_and_test_unchanged")}))
    return 0 if result.wasSuccessful() and report["producer_and_test_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
