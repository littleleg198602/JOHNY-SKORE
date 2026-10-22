"""Run only the new Form144 tests and preserve exact before/after source pins."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import unittest

from evidence.sourcefront_sec_20261020.build_review import HERE, pin, write_new


def main(version):
    if version not in {"v1", "v2", "v3"}:
        raise ValueError("Use a new additive, bounded qualification version")
    paths = ("market_checker_app/services/sec_form4_person_bridge.py",
             "tests/test_sourcefront_form144_account_notice.py",
             "evidence/sourcefront_sec_20261020/build_review.py",
             "evidence/sourcefront_sec_20261020/run_focused.py")
    before = [pin(path) for path in paths]
    started = datetime.now(timezone.utc).isoformat(); monotonic = time.perf_counter()
    log_path = HERE / f"focused_{version}.log"
    with log_path.open("x", encoding="utf-8", newline="\n") as stream:
        suite = unittest.defaultTestLoader.discover("tests", pattern="test_sourcefront_form144_account_notice.py")
        discovered = suite.countTestCases()
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    after = [pin(path) for path in paths]
    report = {"scope": "NEW_FORM144_OFFLINE_TESTS_ONLY_SYNTHETIC_ADVERSARIES_ARE_NOT_LIVE_ACCEPTANCE",
              "started_at": started, "completed_at": datetime.now(timezone.utc).isoformat(),
              "duration_seconds": time.perf_counter()-monotonic, "discovered": discovered,
              "executed": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "skips": len(result.skipped), "expected_failures": len(result.expectedFailures),
              "unexpected_successes": len(result.unexpectedSuccesses), "successful": result.wasSuccessful(),
              "source_pins_before": before, "source_pins_after": after, "source_unchanged_during_run": before == after,
              "full_original_log": pin(log_path.relative_to(HERE.parents[1]).as_posix()),
              "http_requests": 0, "sql_connections": 0, "windows_or_full21_acceptance": False}
    write_new(HERE / f"focused_{version}.json", report)
    print(json.dumps({key: report[key] for key in ("discovered", "executed", "failures", "errors", "successful", "source_unchanged_during_run")}))
    return 0 if result.wasSuccessful() and before == after else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
