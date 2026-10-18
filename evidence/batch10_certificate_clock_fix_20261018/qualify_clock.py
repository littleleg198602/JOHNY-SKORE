"""Only the new clock cases before correction; full changed module afterward."""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import unittest

from market_checker_app.collectors import sec_certificate_instrument_review as service

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CLOCK_METHODS = (
    "test_completion_clock_records_new_knowledge_after_the_start_source_cutoff",
    "test_completion_clock_is_sampled_after_slow_image_and_derived_class_parsing",
    "test_physical_completion_rollback_cannot_backdate_the_new_interpretation",
    "test_explicit_source_cutoff_cannot_include_an_interpretation_completed_later",
    "test_equal_start_and_completion_are_valid_with_an_explicit_aware_cutoff",
    "test_unavailable_image_partial_review_also_records_its_completion",
    "test_physical_completion_clock_requires_an_explicit_timezone",
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("before", "after"), required=True)
    args = parser.parse_args()
    base = "tests.test_sec_certificate_instrument_review"
    if args.phase == "before":
        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(base + ".CertificateInstrumentReviewTests." + name)
                                   for name in CLOCK_METHODS)
    else:
        suite = unittest.defaultTestLoader.loadTestsFromName(base)
    count, stream = suite.countTestCases(), io.StringIO()
    source, tests = Path(service.__file__), ROOT / "tests/test_sec_certificate_instrument_review.py"
    source_sha, test_sha = hashlib.sha256(source.read_bytes()).hexdigest(), hashlib.sha256(tests.read_bytes()).hexdigest()
    start = datetime.now(timezone.utc).isoformat()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_sha
    assert hashlib.sha256(tests.read_bytes()).hexdigest() == test_sha
    raw = stream.getvalue().encode()
    with (HERE / (args.phase + "_tests.log")).open("xb") as output:
        output.write(raw)
    report = {"schema_version": "certificate_clock_focused_qualification_v1", "phase": args.phase,
              "started_at": start, "finished_at": datetime.now(timezone.utc).isoformat(),
              "discovered": count, "executed": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "skips": len(result.skipped), "expected_failures": len(result.expectedFailures),
              "unexpected_successes": len(result.unexpectedSuccesses), "plain_OK": result.wasSuccessful(),
              "producer_sha256": source_sha, "test_sha256": test_sha, "log_sha256": hashlib.sha256(raw).hexdigest(),
              "new_clock_methods": len(CLOCK_METHODS), "unchanged_old_methods": 22,
              "new_source_requests": 0, "db_writes": 0, "synthetic_clock_regressions_are_acceptance": False}
    with (HERE / (args.phase + "_tests.json")).open("x", encoding="utf-8") as output:
        output.write(json.dumps(report, sort_keys=True, indent=2) + "\n")
    print(raw.decode()); print(json.dumps(report))
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
