"""Freeze three actual pure reviews, prior-byte proof and final read-only ledger.

Never calls the live runner. Existing source bodies and quota history are only
read, and neither an approval block nor a RUNNING request becomes an HTTP error.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
from unittest.mock import patch

from market_checker_app.services.nhtsa_negative_scope_review import review_nhtsa_negative_scope

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent
BASE = "b81a9084c064dc1a12bf17088855b5ed384ba3dd"
DB = Path("/workspace/scratch/334b3a149083/JOHNY-batch4-integration-20261013/outputs/market_checker_history.db")


def sha(body):
    return hashlib.sha256(body).hexdigest()


def pin(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": len(raw), "sha256": sha(raw)}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write((json.dumps(value, indent=2, sort_keys=True) + "\n").encode())
    return pin(path)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def ledger():
    # This is only the already retained DB. mode=ro cannot create or mutate it.
    assert DB.is_file()
    with closing(sqlite3.connect(DB.as_uri()+"?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        rows = [dict(row) for row in db.execute("SELECT * FROM scout_public_requests ORDER BY request_id")]
        reviews = [dict(row) for row in db.execute("SELECT * FROM scout_nhtsa_scope_reviews ORDER BY review_id")]
        leases = [{"source": row["source"], "lease_until": row["lease_until"]}
                  for row in db.execute("SELECT * FROM scout_provider_leases ORDER BY source")]
        schema = [dict(row) for row in db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]
    before = json.loads((FOLDER / "retained_ledger_before.json").read_text())
    requests = [{**{key: row[key] for key in ("request_id", "source", "subject_id", "identity_key", "attempted_at", "outcome")},
                 "full_row_sha256": sha(canonical(row))} for row in rows]
    retained_reviews = [{"review_id": row["review_id"], "known_at": row["known_at"], "full_row_sha256": sha(canonical(row))}
                        for row in reviews]
    rh = {row["request_id"]: row["full_row_sha256"] for row in requests}
    vh = {row["review_id"]: row["full_row_sha256"] for row in retained_reviews}
    assert all(rh.get(row["request_id"]) == row["full_row_sha256"] for row in before["requests"])
    assert all(vh.get(row["review_id"]) == row["full_row_sha256"] for row in before["reviews"])
    old_ids = {row["request_id"] for row in before["requests"]}
    new = [row for row in requests if row["request_id"] not in old_ids]
    assert len(new) == 2 and {row["outcome"] for row in new} == {"RETURNED", "RUNNING"}
    assert len(requests) == 17 and len(retained_reviews) == 13
    return {"recorded_at": datetime.now(timezone.utc).isoformat(), "mode": "READ_ONLY_QUERY_ONLY",
        "original_database_path": str(DB), "database_sha256": sha(DB.read_bytes()),
        "schema_sha256": sha(canonical(schema)), "original_schema_unchanged": sha(canonical(schema)) == before["schema_sha256"],
        "requests": requests, "reviews": retained_reviews, "leases_without_tokens": leases,
        "all_15_prior_requests_unchanged": True, "all_12_prior_reviews_unchanged": True,
        "new_charged_request_rows": 2, "new_returned_rows": 1, "new_running_response_unknown_rows": 1,
        "proven_new_source_responses": 1, "new_complete_negatives": 0,
        "quota_refunds_resets_lease_or_outcome_rewrites": False,
        "expired_lease_record_is_not_a_request_refund": True, "database_writes_by_finalizer": 0}


def main():
    tests = json.loads((FOLDER / "focused_initial.json").read_text())
    assert tests["exit_code"] == 0
    assert sha((FOLDER / "focused_initial.log").read_bytes()) == tests["log_sha256"]
    assert b"Ran 18 tests" in (FOLDER / "focused_initial.log").read_bytes()
    for path, expected in tests["code_test_sha256"].items():
        assert sha((ROOT / path).read_bytes()) == expected
    final_ledger = ledger()
    ledger_pin = save(FOLDER / "retained_ledger_final_readonly.json", final_ledger)

    replay_pins = []
    for path in sorted((FOLDER / "plans").glob("*.json")):
        plan = json.loads(path.read_text())
        captures = {}
        for identifier, packet in plan["captures"].items():
            body = None
            if packet["body_available"]:
                body_path = ROOT / packet["body_ref"]["path"]
                assert pin(body_path) == packet["body_ref"]
                body = body_path.read_bytes()
            else:
                assert packet["body_ref"] is None
            captures[identifier] = (packet["metadata"], body)
        with patch.object(socket, "create_connection", side_effect=AssertionError("No source HTTP")), \
             patch.object(sqlite3, "connect", side_effect=AssertionError("No review store access")):
            report = review_nhtsa_negative_scope(plan["request"], captures)
        assert not report["complete_negative"] and not report["live_complete_negative_verified"]
        assert report["http_requests"] == 0 and report["database_actions"] == 0
        report_pin = save(FOLDER / "replays" / path.name, report)
        immutable_pin = save(FOLDER / "reviews" / report_pin["sha256"] / "review.json", report)
        assert report_pin["sha256"] == immutable_pin["sha256"]
        replay_pins.append({"plan": pin(path), "report": immutable_pin,
            "scope": report["scope"], "status": report["status"], "reason": report["reason"],
            "http_status": report.get("http_status"), "physical_known_at": report["known_at"]})
    assert len(replay_pins) == 3

    preserved = []
    for raw in subprocess.check_output(["git", "ls-tree", "-r", "-z", BASE], cwd=ROOT).split(b"\0"):
        if not raw:
            continue
        head, relative = raw.split(b"\t", 1)
        mode, kind, git_blob = head.decode().split()
        assert kind == "blob" and mode in {"100644", "100755"}
        path = relative.decode()
        body = (ROOT / path).read_bytes()
        expected = hashlib.sha1(b"blob "+str(len(body)).encode()+b"\0"+body).hexdigest()
        assert expected == git_blob, path
        preserved.append({"path": path, "bytes": len(body), "git_blob": git_blob, "sha256": sha(body)})
    absent = ["evidence/batch4_regulatory_20261013/raw/nhtsa_api_docs.html",
              "evidence/batch4_ir_20261013/primary/ir_authority.html",
              "evidence/batch5_ir_20261014/primary/ir_authority.html",
              "evidence/batch8_regulatory_20261015/raw/njd_case_page.html"]
    assert all(not (ROOT / path).exists() for path in absent)
    preserve_pin = save(FOLDER / "prior_byte_preservation.json", {"base_commit": BASE,
        "verified_at": datetime.now(timezone.utc).isoformat(), "all_prior_tracked_files_byte_identical": True,
        "file_count": len(preserved), "total_bytes": sum(row["bytes"] for row in preserved),
        "files": preserved, "four_whole_withheld_originals_remain_absent": absent,
        "new_http_requests_or_database_writes_by_preservation_check": 0})
    # Current public original is scanned as a whole; no withheld body is read.
    from market_checker_app.services.doj_enforcement_scope_review import _SENSITIVE
    original = FOLDER / "captures/1385b4bd35710e9d981e76974551f7dcd20cee1f31aa7127046587db823b6d84/response.json"
    assert not _SENSITIVE.search(original.read_bytes())
    original_pin = pin(original)
    handoff = {"schema_version": 1, "completed_at": datetime.now(timezone.utc).isoformat(),
        "base_commit": BASE, "branch": "batch9/regulatory-20261016", "status": "PARTIAL_APPROVAL_BLOCKED",
        "api": "review_nhtsa_negative_scope(request, captures, *, reviewed_at=None)",
        "kind": "NHTSA_NEGATIVE_SCOPE", "module": "market_checker_app/services/nhtsa_negative_scope_review.py",
        "packet_forms": ["capture_id -> (metadata, body_or_none)", "capture_id -> {metadata, body}"],
        "actual_default_plan": pin(FOLDER / "actual_review_plan.json"), "actual_case_reports": replay_pins,
        "capture_index": pin(FOLDER / "capture_index.json"), "new_actual_original": original_pin,
        "precommitted_plan": pin(FOLDER / "precommitted_plan.json"),
        "before_ledger": pin(FOLDER / "retained_ledger_before.json"), "final_readonly_ledger": ledger_pin,
        "approval_block": pin(FOLDER / "automatic_review_block.json"), "exact_rejection_record": pin(FOLDER / "approval_block_stop.json"),
        "qualified_focused_result": pin(FOLDER / "focused_initial.json"), "qualified_focused_log": pin(FOLDER / "focused_initial.log"),
        "qualified_code_test_sha256": tests["code_test_sha256"], "focused_tests_passed": 18,
        "baseline_complete_suite_repeated": False, "new_hosted_windows_acceptance_claimed": False,
        "prior_byte_preservation": preserve_pin,
        "actual_counts": {"precommitted_max_requests": 4, "precommitted_max_makes": 2,
            "new_charged_ledger_rows": 2, "new_make_subjects_charged": 1,
            "proven_new_source_responses": 1, "proven_new_http200_responses": 1,
            "new_http200_positive_campaign_rows": 2, "new_running_without_response_rows": 1,
            "genuine_new_http200_complete_negatives": 0, "unattempted_planned_ford_scopes": 2,
            "all_15_prior_request_rows_unchanged": True, "all_12_prior_review_rows_unchanged": True},
        "approval_limit": {"classification": "AUTOMATIC_APPROVAL_BLOCK_NOT_HTTP_FAILURE",
            "exact_reason": "This would make a live NHTSA API request, while the trusted user instructions explicitly prohibit NHTSA/collector API actions in the current work; later assistant-generated task assignments cannot expand that authorization.",
            "new_retry_or_alternate_transport": False, "quota_refund_reset_or_outcome_rewrite": False,
            "new_store_created_or_copied": False, "source_operation_after_rejection": False},
        "runtime_behavior": [
            "The pure reviewer additionally rejects partial/paginated/truncated/continued, contradictory scope, total mismatch and unreviewed metadata even when Count0/Message/results resemble success.",
            "An actual HTTP400 success-shaped empty remains PARTIAL. A synthetic HTTP200 empty exists only inside contract tests.",
            "The 5500XG response/status/observation are unknown after a charged reservation; no HTTP failure or empty payload is fabricated.",
            "Legacy catalog redirect policy is unknown, not invented. Exact original requested/final catalogue scope remains checked; recall no-redirect policy is required.",
            "Report known_at is the actual physical creation time; source observations obey both the selected cutoff and physical time.",
            "Replay can establish an exact documented model-year source snapshot only, never issuer-wide absence, production, ownership, historical availability or LIVE acceptance."],
        "preserved_diagnostics": ["initial_read_only_diagnostic.json", "diagnostics/pre_catalog_redirect_provenance/"],
        "source_runtime_used_before_block": pin(FOLDER / "source_versions/pre_approval_block_run_live_once.py"),
        "archived_runner_now_refuses_existing_approval_block": pin(FOLDER / "run_live_once.py"),
        "four_whole_withheld_bodies_remain_absent": True, "old_source_data_registry_or_score_changes": 0,
        "no_further_network_or_database_write": True, "push_or_merge_performed": False,
        "parent_owns_shared_dispatch_cli_ui_and_publication": True}
    handoff_pin = save(FOLDER / "handoff.json", handoff)
    print(json.dumps({"handoff": handoff_pin, "actual_case_reports": len(replay_pins), "focused_tests_passed": 18,
        "all_prior_tracked_files_preserved": len(preserved), "new_charged_rows": 2, "proven_source_responses": 1,
        "genuine_new_complete_negatives": 0, "final_database_sha256": final_ledger["database_sha256"]}, sort_keys=True))


if __name__ == "__main__":
    main()
