"""Focused new point-operation tests, with exact source/test/log pins."""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    if not args.name.replace("_", "").isalnum():
        raise ValueError("Select one new qualification name")
    sources = [ROOT / "market_checker_app/services/sec_dated_instrument_point_review.py",
               ROOT / "tests/test_sec_dated_instrument_point_review.py"]
    pins = {q.relative_to(ROOT).as_posix(): hashlib.sha256(q.read_bytes()).hexdigest() for q in sources}
    suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_sec_dated_instrument_point_review")
    count, stream, start = suite.countTestCases(), io.StringIO(), datetime.now(timezone.utc).isoformat()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    assert pins == {q.relative_to(ROOT).as_posix(): hashlib.sha256(q.read_bytes()).hexdigest() for q in sources}
    raw = stream.getvalue().encode()
    with (HERE / (args.name + ".log")).open("xb") as output:
        output.write(raw)
    report = {"schema_version": "dated_point_focused_qualification_v1", "started_at": start,
              "finished_at": datetime.now(timezone.utc).isoformat(), "discovered": count, "executed": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors), "skips": len(result.skipped),
              "expected_failures": len(result.expectedFailures), "unexpected_successes": len(result.unexpectedSuccesses),
              "plain_OK": result.wasSuccessful(), "source_sha256": pins, "log_sha256": hashlib.sha256(raw).hexdigest(),
              "new_source_requests": 0, "db_reads_or_writes": 0, "synthetic_positives_are_acceptance": False}
    with (HERE / (args.name + ".json")).open("x", encoding="utf-8") as output:
        output.write(json.dumps(report, sort_keys=True, indent=2) + "\n")
    print(raw.decode()); print(json.dumps(report))
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
