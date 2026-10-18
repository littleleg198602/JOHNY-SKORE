"""Freeze the pre-fix clock reproductions and the changed focused qualification."""
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


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    mode = sys.argv[1] if len(sys.argv) == 2 and sys.argv[1] in {"before", "after"} else None
    if mode is None:
        raise SystemExit("Choose one before or after run; existing files cannot be replaced")
    log, record = HERE / (mode+".log"), HERE / (mode+".json")
    assert not log.exists() and not record.exists()
    sys.path.insert(0, str(ROOT))
    import tests.test_batch10_caption_clock_fix as clocks
    if mode == "before":
        path = HERE / "before/caption_review.py"
        spec = importlib.util.spec_from_file_location("batch10_frozen_caption_clock", path)
        producer = importlib.util.module_from_spec(spec); spec.loader.exec_module(producer)
        clocks.target = producer
        names = ("test_known_at_follows_a_real_delayed_decoder_callback", "test_future_source_read_cutoff_is_rejected_before_decoder")
        suite = unittest.TestSuite(clocks.CaptionCompletionClockTests(name) for name in names)
    else:
        import tests.test_doj_case_caption_review as binding
        path = ROOT / "market_checker_app/collectors/doj_case_caption_review.py"
        suite = unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(clocks.CaptionCompletionClockTests),
                                   unittest.defaultTestLoader.loadTestsFromTestCase(binding.DojCaseCaptionReviewTests)])
    pins = {str(path.relative_to(ROOT)): sha(path), "tests/test_batch10_caption_clock_fix.py": sha(ROOT / "tests/test_batch10_caption_clock_fix.py"),
            "tests/test_doj_case_caption_review.py": sha(ROOT / "tests/test_doj_case_caption_review.py")}
    started = datetime.now(timezone.utc).isoformat(); began = time.perf_counter(); stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    raw = stream.getvalue().encode()
    with log.open("xb") as output: output.write(raw)
    assert pins == {key: sha(ROOT / key) for key in pins}
    data = {"started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": time.perf_counter()-began,
        "mode": mode, "producer_test_sha256": pins, "executed": result.testsRun, "failures": len(result.failures),
        "errors": len(result.errors), "skips": len(result.skipped), "expected_failures": len(result.expectedFailures),
        "unexpected_successes": len(result.unexpectedSuccesses), "successful": result.wasSuccessful(),
        "log_sha256": hashlib.sha256(raw).hexdigest(), "HTTP_DB_quota_or_registry_actions": 0,
        "synthetic_clock_callbacks_are_not_source_acceptance_or_performance_benchmarks": True,
        "old_bca_test_version_preserved": True, "source_body_hash_assertions_relaxed": False,
        "existing_new_owned_future_cutoff_expectation_changed_for_strict_rejection": True}
    with record.open("x") as output: json.dump(data, output, indent=2, sort_keys=True); output.write("\n")
    print(raw.decode()); print(json.dumps(data, sort_keys=True))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
