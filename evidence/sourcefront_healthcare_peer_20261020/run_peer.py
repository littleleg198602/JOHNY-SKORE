"""Run only the unchanged independent CMS peer against an exact source root.

Writes new evidence filenames exclusively; never updates producer or registry.
"""
from datetime import datetime, timezone
import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest


EVIDENCE = Path(__file__).resolve().parent
ROOT = EVIDENCE.parents[1]
PRODUCERS = (
    "market_checker_app/services/healthcare_identity_service.py",
    "market_checker_app/services/healthcare_scout_service.py",
    "market_checker_app/services/cms_provider_ownership_review.py",
    "market_checker_app/data/verified_healthcare_identities.json",
)


def pin(path):
    body = path.read_bytes()
    return {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def write_json(path, value):
    with path.open("x", encoding="utf-8") as out:
        json.dump(value, out, indent=2, sort_keys=True)
        out.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--phase", choices=("before", "after"), required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-identity-sha256", required=True)
    args = parser.parse_args()
    source_root = args.source_root.resolve(strict=True)
    actual_commit = subprocess.check_output(["git", "-C", str(source_root), "rev-parse", "HEAD"], text=True).strip()
    if actual_commit != args.expected_commit:
        raise ValueError("Exact candidate commit does not match")
    snapshots = {name: pin(source_root / name) for name in PRODUCERS}
    if snapshots[PRODUCERS[0]]["sha256"] != args.expected_identity_sha256:
        raise ValueError("Exact identity producer bytes do not match")
    test_path = ROOT / "tests/test_cms_snapshot_attribution_peer.py"
    frozen_test = EVIDENCE / "before/tests/test_cms_snapshot_attribution_peer.py"
    if test_path.read_bytes() != frozen_test.read_bytes():
        raise ValueError("Peer assertions changed after the original freeze")
    sys.path.insert(0, str(source_root))
    spec = importlib.util.spec_from_file_location("cms_snapshot_attribution_peer", test_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    discovered = suite.countTestCases()
    output = io.StringIO()
    started = datetime.now(timezone.utc)
    start = time.perf_counter()
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    elapsed = time.perf_counter() - start
    completed = datetime.now(timezone.utc)
    after_pins = {name: pin(source_root / name) for name in PRODUCERS}
    if after_pins != snapshots or test_path.read_bytes() != frozen_test.read_bytes():
        raise ValueError("Source, registry or assertion bytes changed during qualification")
    console = output.getvalue()
    log_path = EVIDENCE / (args.phase + "_tests.log")
    with log_path.open("x", encoding="utf-8") as out:
        out.write(console)
    observations = []
    for row in module.retained_rows():
        for label, clock in (("current_2026_10_20", module.CURRENT_CLOCK),
                             ("legacy_same_UTC_day", datetime(2026, 10, 11, 10, 17, 5, tzinfo=timezone.utc))):
            object_id, detail, resolution = module.runtime_resolution(row, clock)
            strict = module.strict_projection_resolution(row, clock)
            observations.append({"case": label, "as_of": clock.isoformat(),
                "retained_enrollment_id": row["ENROLLMENT ID"], "object_id": object_id,
                "source_lead": detail, "runtime_identity_resolution": resolution,
                "strict_original_byte_review": strict})
    diagnostic_path = EVIDENCE / (args.phase + "_runtime.json")
    write_json(diagnostic_path, {"recorded_at": completed.isoformat(), "source_commit": actual_commit,
        "cases": observations, "source_rows_are_projected_not_complete_originals": True,
        "synthetic_adversarial_provenance_is_not_an_acquired_capture": True,
        "http_requests": 0, "database_operations": 0})
    report = {"schema_version": 1, "phase": args.phase, "started_at": started.isoformat(),
        "completed_at": completed.isoformat(), "elapsed_seconds": elapsed,
        "source_commit": actual_commit, "source_root": str(source_root),
        "producer_and_registry_before": snapshots, "producer_and_registry_after": after_pins,
        "source_and_assertion_bytes_unchanged": True,
        "peer_test": {"path": test_path.relative_to(ROOT).as_posix(), **pin(test_path)},
        "tests_discovered": discovered, "tests_executed": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors),
        "skips": len(result.skipped), "expected_failures": len(result.expectedFailures),
        "unexpected_successes": len(result.unexpectedSuccesses), "plain_ok": result.wasSuccessful(),
        "failure_test_ids": [test.id() for test, _ in result.failures],
        "error_test_ids": [test.id() for test, _ in result.errors],
        "full_console": {"path": log_path.relative_to(ROOT).as_posix(), **pin(log_path)},
        "runtime_diagnostic": {"path": diagnostic_path.relative_to(ROOT).as_posix(), **pin(diagnostic_path)},
        "http_requests": 0, "database_operations": 0, "collector_calls": 0,
        "production_or_registry_edits": False, "prior_suite_repeated": False}
    write_json(EVIDENCE / (args.phase + "_tests.json"), report)
    print(json.dumps({"phase": args.phase, "tests_discovered": discovered,
        "tests_executed": result.testsRun, "failures": len(result.failures),
        "errors": len(result.errors), "plain_ok": result.wasSuccessful(),
        "test_sha256": pin(test_path)["sha256"], "identity_sha256": args.expected_identity_sha256}, indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
