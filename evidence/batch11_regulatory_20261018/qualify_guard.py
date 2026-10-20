"""Qualify the changed explicit interest-array guard without replacing v1 proof."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from market_checker_app.services.epa_frs_program_scope_review import review_epa_frs_program_scope

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
FINAL = HERE / "interest_array_guard"
MODULE = "market_checker_app/services/epa_frs_program_scope_review.py"
TEST = "tests/test_epa_frs_program_scope_review.py"
BASE = "6ba4f44bc3e1ca240615c91dbec5bf52c93f3846"


def sha(body):
    return hashlib.sha256(body).hexdigest()


def pin(path):
    body = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": len(body), "sha256": sha(body)}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write((json.dumps(value, indent=2, sort_keys=True)+"\n").encode())
    return pin(path)


def main():
    old_handoff = json.loads((HERE/"handoff.json").read_text())
    before = json.loads((FINAL/"before.json").read_text())
    assert before["executed"] == 1 and before["failure_entries"] == 2 and before["errors"] == 0
    assert sha((FINAL/"before.log").read_bytes()) == before["log_sha256"]
    assert sha((ROOT/before["frozen_producer"]).read_bytes()) == before["frozen_producer_sha256"]
    assert sha((ROOT/TEST).read_bytes()) == before["current_new_regression_sha256"]
    protected = {path: pin(ROOT/path) for path in (
        "evidence/epa_echo_live_20261002.json", "evidence/batch6_regulatory_20261014/capture_plan.json",
        "evidence/batch6_regulatory_20261014/proposal.json", "evidence/batch6_regulatory_20261014/handoff.json")}
    import tests.test_epa_frs_program_scope_review as controls
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(controls.EpaFrsProgramScopeReviewTests)
    assert suite.countTestCases() == 32
    source_pins = {path: sha((ROOT/path).read_bytes()) for path in (MODULE, TEST)}
    versions = {}
    for path, name in ((MODULE, "qualified_review.py"), (TEST, "qualified_tests.py")):
        target = FINAL/"source_versions"/name; target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream: stream.write((ROOT/path).read_bytes())
        versions[path] = pin(target)
    started = datetime.now(timezone.utc).isoformat(); began = time.perf_counter(); stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    log = stream.getvalue().encode()
    with (FINAL/"focused.log").open("xb") as output: output.write(log)
    assert source_pins == {path: sha((ROOT/path).read_bytes()) for path in source_pins}
    focus = {"started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": time.perf_counter()-began, "discovered": 32, "executed": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors), "skips": len(result.skipped),
        "expected_failures": len(result.expectedFailures), "unexpected_successes": len(result.unexpectedSuccesses),
        "successful": result.wasSuccessful(), "log_sha256": sha(log), "source_test_sha256": source_pins,
        "source_versions": versions, "source_gate_dependency": pin(ROOT/"market_checker_app/services/epa_frs_ownership_review.py"),
        "old_31_method_qualification_is_preserved_historical_version": True,
        "rerun_reason": "producer gained actual direct-array type and 100-row guard after the earlier qualification",
        "unchanged_new_regression_passed_after_fix": result.wasSuccessful(),
        "HTTP_search_collector_DB_quota_actions": 0, "synthetic_controls_are_not_source_acceptance": True,
        "python": sys.version.split()[0]}
    focus_pin = write(FINAL/"focused.json", focus)
    print(log.decode())
    if not result.wasSuccessful():
        print(json.dumps({"qualification_failed": focus_pin}, sort_keys=True)); return 1
    plan_path = HERE/"actual_review_plan.json"
    assert pin(plan_path) == old_handoff["actual_plan"]
    plan = json.loads(plan_path.read_text())
    captures = {key: (value["metadata"], None) for key, value in plan["captures"].items()}
    assert all(not item["body_available"] and item["body_ref"] is None for item in plan["captures"].values())
    called_at = datetime.now(timezone.utc)
    with patch.object(socket.socket, "connect", side_effect=AssertionError("No HTTP or collector operation")), \
         patch.object(socket, "create_connection", side_effect=AssertionError("No HTTP or collector operation")), \
         patch.object(sqlite3, "connect", side_effect=AssertionError("No database")):
        report = review_epa_frs_program_scope(plan["request"], captures)
    returned_at = datetime.now(timezone.utc)
    assert called_at <= datetime.fromisoformat(report["review_started_at"]) <= datetime.fromisoformat(report["known_at"]) <= returned_at
    assert report["known_at"] == report["review_completed_at"]
    old_report = json.loads((HERE/"actual_review.json").read_text())
    exclude = {"contract_version", "review_started_at", "review_completed_at", "known_at", "reviewed_at", "source_read_cutoff"}
    assert {k:v for k,v in report.items() if k not in exclude} == {k:v for k,v in old_report.items() if k not in exclude}
    report_pin = write(FINAL/"actual_review.json", report)
    immutable_pin = write(FINAL/"reviews"/report_pin["sha256"]/"review.json", report)
    assert pin(HERE/"handoff.json")["sha256"] == "d9d0c5852005cec4e02f40d0760c16d504c95be66017958b5ab8058f1b324244"
    for path, expected in protected.items():
        assert pin(ROOT/path) == expected
        body = (ROOT/path).read_bytes()
        expected_blob = subprocess.check_output(["git", "rev-parse", BASE+":"+path], cwd=ROOT).decode().strip()
        assert hashlib.sha1(b"blob "+str(len(body)).encode()+b"\0"+body).hexdigest() == expected_blob
    assert subprocess.check_output(["git", "diff", "--name-only", BASE], cwd=ROOT) == b""
    record_pin = write(FINAL/"review_record.json", {
        "actual_plan": pin(plan_path), "report": immutable_pin, "source_test_sha256": source_pins,
        "physical_call_started_at": called_at.isoformat(), "physical_call_returned_at": returned_at.isoformat(),
        "review_started_at": report["review_started_at"], "physical_completion_known_at": report["known_at"],
        "source_read_cutoff": report["source_read_cutoff"], "original_plan_metadata_and_clocks_unchanged": True,
        "prior_actual_v1_report": pin(HERE/"actual_review.json"), "all_non_clock_actual_content_unchanged": True,
        "comparison_excluded_clock_contract_fields": sorted(exclude),
        "referenced_prior_files_exact_base_git_blobs": list(protected.values()),
        "all_preexisting_tracked_files_unchanged_in_Git_diff": True,
        "HTTP_collector_search_DB_quota_actions": 0})
    final = deepcopy(old_handoff)
    final.update(completed_at=datetime.now(timezone.utc).isoformat(), actual_report=immutable_pin,
        review_record=record_pin, qualified_result=focus_pin, complete_qualified_log=pin(FINAL/"focused.log"),
        qualified_source_test_sha256=source_pins, frozen_qualified_source_versions=versions)
    final["qualified_counts"] = {"discovered":32,"executed":32,"passed":32,"failures":0,"errors":0,
        "skips":0,"expected_failures":0,"unexpected_successes":0}
    final["source_gate_dependency"] = focus["source_gate_dependency"]
    final["earlier_31_method_version"] = {"handoff":pin(HERE/"handoff.json"), "result":pin(HERE/"focused.json"),
        "original_source_and_test_versions_preserved":True,
        "current_producer_and_test_version_is_the_32_method_version":True}
    final["confirmed_source_array_gap_and_fix"] = {"before_result":pin(FINAL/"before.json"),
        "before_log":pin(FINAL/"before.log"),"executed_method":1,"failure_entries":2,"errors":0,
        "gap":"numeric-key child object or over-100-row child array was admitted as a selected program interest",
        "legal_or_issuer_ownership_promotion_occurred":False,
        "fix":"require the actual direct child collection to be a list of at most100 rows before any interest field is returned",
        "same_regression_assertions_changed_or_relaxed":False}
    final["bounds"] = {"original_body_bytes":4_000_000,"captures":4,"facility_rows":100,"program_rows_per_facility":100,
        "selected_programs":25,"selected_interest_records_total":100,"source_interest_array_rows":100,
        "JSON_nodes":100_000,"JSON_depth":32}
    final["actual_source_counts"]["retained_missing_original_review_versions_created"] = 2
    handoff_pin = write(FINAL/"handoff.json", final)
    print(json.dumps({"current_handoff":handoff_pin,"actual_plan":pin(plan_path),"current_report":immutable_pin,
        "qualified_tests":result.testsRun,"current_producer_sha256":source_pins[MODULE],
        "physical_completion_known_at":report["known_at"],"HTTP_DB_quota_actions":0},sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
