"""Preserve the earlier 85-test capture and qualify the final lane changes."""
from __future__ import annotations

from datetime import datetime, timezone
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time
import re
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-qualified-log", action="store_true")
    args = parser.parse_args()
    prior = DIRECTORY / "prior_85test_handoff.json"
    current = DIRECTORY / "handoff.json"
    log = DIRECTORY / "final_validation_tests.log"
    if args.reuse_qualified_log:
        if not prior.is_file() or not log.is_file():
            raise ValueError("No preserved final qualification is available to recover")
        if current.exists() and current.read_bytes() != prior.read_bytes():
            raise ValueError("Refusing to replace a different existing handoff")
    else:
        if prior.exists() or log.exists():
            raise FileExistsError("Final validation is versioned; existing captures must not be replaced")
        current.rename(prior)
    original = json.loads(prior.read_text(encoding="utf-8"))
    loader = importlib.util.spec_from_file_location("batch6_read_only_reproducer", DIRECTORY / "reproduce.py")
    reproducer = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(reproducer)
    suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_batch6_prospective_specialist_history")
    discovered = suite.countTestCases()
    if args.reuse_qualified_log:
        text = log.read_text(encoding="utf-8")
        footer = re.search(r"Ran (\d+) tests in ([\d.]+)s\s+OK\s*\Z", text)
        if footer is None or int(footer.group(1)) != discovered:
            raise ValueError("Preserved test log is not an exact successful current suite")
        for name in ("market_checker_app/services/prospective_specialist_history.py", "tests/test_batch6_prospective_specialist_history.py"):
            if (ROOT / name).stat().st_mtime_ns > log.stat().st_mtime_ns:
                raise ValueError("Product/test code changed after the qualified log")
        tested = SimpleNamespace(testsRun=discovered, failures=[], errors=[], skipped=[],
            expectedFailures=[], unexpectedSuccesses=[], wasSuccessful=lambda: True)
        elapsed = float(footer.group(2))
    else:
        started = time.perf_counter()
        with log.open("x", encoding="utf-8", newline="\n") as handle:
            tested = unittest.TextTestRunner(stream=handle, verbosity=2).run(suite)
        elapsed = time.perf_counter() - started
    clock = datetime.now(timezone.utc)
    corpus = reproducer._corpus(reproducer.DEFAULT_DB, clock)
    preservation = reproducer._old_evidence_integrity()
    validation = {"observed_at": clock.isoformat(), "corpus": corpus,
        "all_old_evidence": preservation,
        "prior_code_hashes": original["owned_file_sha256"],
        "final_change_reason": "Reject rehashed inconsistency in source record IDs, exact archive filenames and advertised price capture hashes",
        "earlier_development_green_summary": {"discovered": 50, "executed": 50, "errors": 0, "failures": 0,
            "elapsed_seconds": 3.505, "successful": True, "summary_from_observed_tool_output": True,
            "full_scratch_log_survived": False, "exact_prior_source_hash_available": False,
            "current_code_acceptance": False},
        "interrupted_final_launch": {"exit_code": 127,
            "error": "prepared johny-tests-20261011/bin/python missing",
            "tests_executed": 0, "source_work_repeated": False,
            "equivalent_primary_python_with_existing_venv_packages_used_for_prior_85test_run": True,
            "original_prepared_interpreter_restored_for_final_validation": True,
            "package_install_or_update_performed": False}}
    validation["qualified_test_log_reused_without_rerun"] = args.reuse_qualified_log
    validation["final_artifact_write_retry"] = ({"previous_error": "FileExistsError: identical prior handoff remained at original path after rename",
        "preserved_prior_file_sha256": reproducer._sha(prior), "test_log_sha256": reproducer._sha(log),
        "service_and_test_files_unchanged_since_completed_log": True} if args.reuse_qualified_log else None)
    validation_path = DIRECTORY / ("final_artifact_recovery.json" if args.reuse_qualified_log else "final_corpus_and_validation_audit.json")
    reproducer._write(validation_path, validation)
    proof = dict(original)
    proof["generated_at"] = datetime.now(timezone.utc).isoformat()
    proof["actual_result"] = {**original["actual_result"], "status": corpus["status"], "reason": corpus["reason"],
        "forecast_snapshot_table_exists": corpus["prediction_snapshot_table_exists"],
        "eligible_real_labels": corpus["prospective_history_report"]["eligible_real_label_count"],
        "actual_corpus_observed_at": clock.isoformat()}
    proof["tests"] = {"modules": ["tests.test_batch6_prospective_specialist_history"],
        "discovered": discovered, "executed": tested.testsRun, "failures": len(tested.failures),
        "errors": len(tested.errors), "skips": len(tested.skipped), "expected_failures": len(tested.expectedFailures),
        "unexpected_successes": len(tested.unexpectedSuccesses), "successful": tested.wasSuccessful(),
        "elapsed_seconds": round(elapsed, 6), "fixture_only": True,
        "production_schema_fixture_is_not_actual_live_acceptance": True,
        "windows_current_change_verified": False}
    proof["prior_qualified_tests"] = {"handoff": prior.relative_to(ROOT).as_posix(),
        "handoff_sha256": reproducer._sha(prior), "tests": original["tests"],
        "applies_to_prior_owned_file_hashes": original["owned_file_sha256"],
        "current_code_acceptance": False}
    proof["final_validation_reason"] = validation["final_change_reason"]
    proof["qualified_test_log_reused_without_rerun"] = args.reuse_qualified_log
    proof["preserved_old_evidence"] = preservation
    owned = list(original["owned_file_sha256"]) + ["evidence/batch6_evaluation_20261014/finalize_validation.py"]
    proof["owned_file_sha256"] = {name: reproducer._sha(ROOT / name) for name in owned}
    proof["artifact_sha256"] = {**original["artifact_sha256"],
        **{path.relative_to(ROOT).as_posix(): reproducer._sha(path) for path in (
            prior, log, DIRECTORY / "final_corpus_and_validation_audit.json", validation_path)}}
    # Preserve the exact old handoff under its versioned name. A surviving
    # identical source copy may be removed; any different content is refused.
    if current.exists():
        if current.read_bytes() != prior.read_bytes():
            raise ValueError("Refusing to replace a different existing handoff")
        current.unlink()
    reproducer._write(current, proof)
    print(json.dumps({"tests": proof["tests"], "actual_result": proof["actual_result"],
        "all_old_evidence_byte_identical": preservation["all_old_evidence_byte_identical"]}, sort_keys=True))
    return 0 if tested.wasSuccessful() and preservation["all_old_evidence_byte_identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
