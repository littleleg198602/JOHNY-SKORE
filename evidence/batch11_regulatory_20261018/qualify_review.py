"""Exclusive offline qualification, actual missing-body replay and hash handoff."""
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
BASE = "6ba4f44bc3e1ca240615c91dbec5bf52c93f3846"
MODULE = "market_checker_app/services/epa_frs_program_scope_review.py"
TEST = "tests/test_epa_frs_program_scope_review.py"
OLD_PLAN = ROOT / "evidence/batch6_regulatory_20261014/capture_plan.json"
OLD_SUMMARY = ROOT / "evidence/epa_echo_live_20261002.json"
WITHHELD = ["evidence/batch4_regulatory_20261013/raw/nhtsa_api_docs.html",
            "evidence/batch4_ir_20261013/primary/ir_authority.html",
            "evidence/batch5_ir_20261014/primary/ir_authority.html",
            "evidence/batch8_regulatory_20261015/raw/njd_case_page.html"]


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
    assert not (HERE / "handoff.json").exists()
    inputs_before = {str(path.relative_to(ROOT)): pin(path) for path in
        (OLD_PLAN, OLD_SUMMARY, ROOT/"evidence/batch6_regulatory_20261014/proposal.json",
         ROOT/"evidence/batch6_regulatory_20261014/handoff.json")}
    meta = json.loads(OLD_PLAN.read_text())[0]
    assert meta["capture_id"] == "epa_dow_20261002_summary_only"
    assert not meta["public_raw_available"] and meta["archive_path"] is None
    assert meta["original_source_body_recovered"] is False and meta["raw_response_bytes"] is None
    assert meta["raw_response_sha256"] == "df9d373119eb5ecbdc01b8034aa42f6d382bbdb197f99bf20d937606936f1cd3"
    request = {"capture_id": meta["capture_id"], "frs_registry_id": "110019986586", "facility_name": "DOW INC",
               "facility_pointer": "/Results/Facilities/0", "programs": []}
    plan = {"schema_version": "captured_identity_review_plan_v1", "kind": "EPA_FRS_PROGRAM_SCOPE",
        "request": request, "captures": {meta["capture_id"]: {"metadata": meta, "body_available": False, "body_ref": None}}}
    plan_pin = write(HERE / "actual_review_plan.json", plan)
    availability_pin = write(HERE / "source_availability.json", {
        "recorded_at": datetime.now(timezone.utc).isoformat(), "base_commit": BASE,
        "actual_source_record": meta, "historical_summary": pin(OLD_SUMMARY), "capture_plan": pin(OLD_PLAN),
        "whole_original_body_retained": False, "summary_is_original_response": False,
        "program_acronyms_ids_interests_or_dates_from_missing_body_known": False,
        "new_HTTP_or_collector_or_search_or_DB_actions": 0,
        "live_source_scope_requested": False,
        "source_schema_support": "narrow existing Results/Message/QueryRows/Facilities/ProgramFacilities contract only",
        "synthetic_field_names_document_publisher_schema": False,
        "independent_publisher_program_interest_field_semantics_available": False})

    import tests.test_epa_frs_program_scope_review as controls
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(controls.EpaFrsProgramScopeReviewTests)
    assert suite.countTestCases() == 31
    code_pins = {MODULE: sha((ROOT/MODULE).read_bytes()), TEST: sha((ROOT/TEST).read_bytes())}
    frozen_pins = {}
    for path, filename in ((MODULE, "qualified_review.py"), (TEST, "qualified_tests.py")):
        target = HERE / "source_versions" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write((ROOT/path).read_bytes())
        frozen_pins[path] = pin(target)
    started, began = datetime.now(timezone.utc), time.perf_counter()
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    log = stream.getvalue().encode()
    with (HERE / "focused.log").open("xb") as output:
        output.write(log)
    assert code_pins == {path: sha((ROOT/path).read_bytes()) for path in code_pins}
    focused = {"started_at": started.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": time.perf_counter()-began, "discovered": 31, "executed": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors), "skips": len(result.skipped),
        "expected_failures": len(result.expectedFailures), "unexpected_successes": len(result.unexpectedSuccesses),
        "successful": result.wasSuccessful(), "log_sha256": sha(log), "source_test_sha256": code_pins,
        "source_versions": frozen_pins, "synthetic_controls_are_not_acceptance": True,
        "baseline_or_old_qualified_suite_repeated": False, "HTTP_DB_quota_or_registry_actions": 0,
        "python": sys.version.split()[0]}
    focus_pin = write(HERE / "focused.json", focused)
    print(log.decode())
    if not result.wasSuccessful():
        print(json.dumps({"qualification_failed": focus_pin}, sort_keys=True))
        return 1

    before_call = datetime.now(timezone.utc)
    with patch.object(socket.socket, "connect", side_effect=AssertionError("No HTTP or collector operation")), \
         patch.object(socket, "create_connection", side_effect=AssertionError("No HTTP or collector operation")), \
         patch.object(sqlite3, "connect", side_effect=AssertionError("No database")):
        report = review_epa_frs_program_scope(request, {meta["capture_id"]: (meta, None)})
    after_call = datetime.now(timezone.utc)
    assert report["status"] == "PARTIAL" and report["source_review"]["reason"] == "MISSING_OR_WHOLE_WITHHELD_ORIGINAL"
    assert report["program_inventory"] == report["selected_program_reviews"] == report["facility_field_evidence"] == []
    assert report["publisher_query_rows"] is None and report["observed_program_rows"] is None
    assert not report["facility_identity_supported"] and not report["negative_facility_or_program_absence_verified"]
    assert before_call <= datetime.fromisoformat(report["review_started_at"]) <= datetime.fromisoformat(report["known_at"]) <= after_call
    assert report["known_at"] == report["review_completed_at"]
    review_pin = write(HERE / "actual_review.json", report)
    immutable_pin = write(HERE / "reviews" / review_pin["sha256"] / "review.json", report)
    assert immutable_pin["sha256"] == review_pin["sha256"]

    for path, expected in inputs_before.items():
        assert pin(ROOT/path) == expected
        body = (ROOT/path).read_bytes()
        expected_blob = subprocess.check_output(["git", "rev-parse", BASE+":"+path], cwd=ROOT).decode().strip()
        assert hashlib.sha1(b"blob "+str(len(body)).encode()+b"\0"+body).hexdigest() == expected_blob
    assert subprocess.check_output(["git", "diff", "--name-only", BASE], cwd=ROOT) == b""
    assert all(not (ROOT/path).exists() for path in WITHHELD)
    record_pin = write(HERE / "review_record.json", {
        "actual_plan": plan_pin, "source_availability": availability_pin, "report": immutable_pin,
        "physical_call_started_at": before_call.isoformat(), "physical_call_returned_at": after_call.isoformat(),
        "physical_review_started_at": report["review_started_at"], "physical_completion_known_at": report["known_at"],
        "source_read_cutoff": report["source_read_cutoff"], "original_metadata_capture_clock_preserved": meta["observed_at"],
        "original_metadata_body_hash_preserved": meta["raw_response_sha256"], "original_body_length_unknown": True,
        "no_substitute_body_or_program_facts_created": True, "referenced_prior_files_exact_base_blobs": list(inputs_before.values()),
        "all_preexisting_tracked_files_unchanged_in_Git_diff": True, "four_whole_withheld_body_paths_absent": WITHHELD,
        "HTTP_collector_search_DB_quota_or_registry_actions": 0, "source_test_sha256": code_pins})
    handoff_pin = write(HERE / "handoff.json", {
        "schema_version": 1, "completed_at": datetime.now(timezone.utc).isoformat(), "base_commit": BASE,
        "branch": "batch11/regulatory-20261018", "status": "PARTIAL", "kind": "EPA_FRS_PROGRAM_SCOPE",
        "api": "review_epa_frs_program_scope(request, captures, *, reviewed_at=None)", "module": MODULE,
        "capture_forms": ["capture_id -> (metadata, original_bytes_or_none)", "capture_id -> {metadata, body}"],
        "request_schema": {"required": ["capture_id", "frs_registry_id", "facility_name", "facility_pointer", "programs"],
            "facility_pointer": "/Results/Facilities/N",
            "programs": [{"program_pointer": "same facility /ProgramFacilities/N", "program_system_acronym": "exact source literal",
                "program_system_id": "exact source literal", "interests": [{"interest_pointer": "same program object or its direct child-array row",
                    "role_field": "exact source field name", "date_fields": {"start": "optional exact source field name",
                        "end": "optional exact source field name", "last_reported": "optional exact source field name"}}]}]},
        "actual_plan": plan_pin, "actual_report": immutable_pin, "review_record": record_pin,
        "source_availability": availability_pin, "qualified_result": focus_pin, "complete_qualified_log": pin(HERE/"focused.log"),
        "qualified_counts": {"discovered": 31, "executed": 31, "passed": 31, "failures": 0, "errors": 0,
            "skips": 0, "expected_failures": 0, "unexpected_successes": 0},
        "qualified_source_test_sha256": code_pins, "frozen_qualified_source_versions": frozen_pins,
        "novel_behavior": ["Exact registry-ID/name selection, program acronym+system-ID and same-object interest/date pointers cannot borrow another facility, program, parent or sibling row.",
            "Literal source-field evidence has its exact JSON pointer, canonical value hash and whole original-body hash; missing/null/unknown dates stay field-level partial.",
            "Complete original byte/hash and primary transport gates precede row interpretation; duplicate/error/partial responses never become absence or ownership.",
            "Caller-selected role/date field meanings remain unverified even for parseable literals; registration does not prove ownership, permitted operation or continuous issuer exposure.",
            "Source-read cutoff is distinct from physical completion known_at; future cutoff, physical rollback and later supporting clocks fail closed."],
        "actual_source_counts": {"new_HTTP_requests": 0, "new_collector_requests": 0, "new_searches": 0,
            "DB_connections_reads_writes_hashes": 0, "quota_or_lease_operations": 0,
            "whole_original_EPA_bodies_available": 0, "actual_program_or_dated_interest_facts": 0,
            "actual_retained_missing_original_case_reviews": 1, "registry_adoptions": 0},
        "actual_blockers": ["The retained DOW EPA record is a historical summary only; original df9d3731 response bytes and length are unavailable.",
            "Program acronyms, IDs, interest/date field names and semantic documentation are not established by that summary.",
            "No independent dated facility legal-owner/operator-to-issuer relationship or continuous exposure is established.",
            "NHTSA live remains approval-blocked; no retry, alternate transport, quota reset, new database or lease/outcome rewrite was attempted."],
        "original_frs_source_clocks_and_pins_unchanged": True, "old_evidence_or_tests_modified": False,
        "four_whole_withheld_bodies_absent_and_never_inspected": True, "model_score_budget_or_trading_changes": False,
        "live_or_Windows_or_OOS_or_full_specialist_acceptance": False, "full_acceptance": "0/21",
        "next_work": ["Root wires the explicit captured-only kind into shared CLI/UI.",
            "Genuine original-byte EPA facility/program and independently documented interest/date semantics remain missing.",
            "Independent dated legal ownership and issuer continuity remain separate requirements."],
        "shared_runtime_UI_docs_status_registry_and_publication_owned_by_parent": True, "push_or_merge_performed": False})
    print(json.dumps({"handoff": handoff_pin, "actual_plan": plan_pin, "actual_report": immutable_pin,
        "qualified_tests": result.testsRun, "known_at": report["known_at"], "HTTP_DB_quota_actions": 0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
