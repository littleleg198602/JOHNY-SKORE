"""Freeze the actual captured-only review and preservation proof, once.

No research, source HTTP, database or registry adoption is performed here.
Existing capture metadata and raw publisher bytes are only read.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
from unittest.mock import patch

from market_checker_app.services.doj_case_posture_review import review_doj_case_posture

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent
BASE = "e252ec62796b0b02e733c55ebef713e0f29d4b44"


def sha(body):
    return hashlib.sha256(body).hexdigest()


def write_new(path, value):
    body = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(body)
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": len(body), "sha256": sha(body)}


def pin(path):
    body = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": len(body), "sha256": sha(body)}


def main():
    started = datetime.now(timezone.utc).isoformat()
    qualified = json.loads((FOLDER / "tests_qualified.json").read_text())
    assert qualified["exit_code"] == 0
    for path, expected in qualified["code_test_sha256"].items():
        assert sha((ROOT / path).read_bytes()) == expected
    log = (FOLDER / "tests_qualified.log").read_bytes()
    assert sha(log) == qualified["log_sha256"]
    assert len(re.findall(rb"^test_", log, re.M)) == 16 and b"Ran 16 tests" in log
    assert log.rstrip().endswith(b"OK")

    plan_path = FOLDER / "captured_review_plan.json"
    plan = json.loads(plan_path.read_text())
    captures, attempts = {}, []
    reservations = sorted((FOLDER / "reservations").glob("*.json"))
    assert len(reservations) == 5
    public_bytes = 0
    for reservation_path in reservations:
        reservation = json.loads(reservation_path.read_text())
        identifier = reservation["capture_id"]
        metadata_path = FOLDER / "raw" / (identifier + ".metadata.json")
        meta = json.loads(metadata_path.read_text())
        assert plan["captures"][identifier]["metadata"] == meta
        assert meta["automatic_redirects"] is False
        for field, value in reservation.items():
            assert meta[field] == value
        body = None
        raw_pin = None
        if meta["public_raw_available"]:
            body_path = ROOT / meta["archive_path"]
            body = body_path.read_bytes()
            raw_pin = pin(body_path)
            assert raw_pin["sha256"] == meta["raw_response_sha256"]
            assert raw_pin["bytes"] == meta["raw_response_bytes"] <= 4_000_000
            assert meta["status"] == "ORIGINAL_BYTES_CAPTURED"
            assert plan["captures"][identifier]["body_ref"] == raw_pin
            public_bytes += len(body)
        else:
            assert meta["archive_path"] is None
            assert plan["captures"][identifier]["body_ref"] is None
            assert not (FOLDER / "raw" / (identifier + ".html")).exists()
            assert not (FOLDER / "raw" / (identifier + ".pdf")).exists()
        captures[identifier] = {"metadata": meta, "body": body}
        attempts.append({"sequence": reservation["sequence"], "capture_id": identifier,
                         "reservation": pin(reservation_path), "metadata": pin(metadata_path),
                         "raw_body": raw_pin, "status": meta["status"],
                         "observed_at": meta["observed_at"], "http_status": meta.get("http_status"),
                         "raw_response_sha256": meta["raw_response_sha256"],
                         "raw_response_bytes": meta["raw_response_bytes"],
                         "public_raw_available": meta["public_raw_available"]})
    assert public_bytes == 2_029_990
    assert sum(row["public_raw_available"] for row in attempts) == 3
    index_pin = write_new(FOLDER / "capture_index.json", {
        "schema_version": 1, "frozen_at": started, "research_plan": pin(FOLDER / "research_plan.json"),
        "reserved_transport_attempts": 5, "maximum_transport_attempts": 6,
        "unused_attempts": 1, "further_http_needed": False, "automatic_redirects": False,
        "maximum_original_body_bytes": 4_000_000, "timeout_seconds": 25,
        "public_original_bodies": 3, "public_original_bytes": public_bytes,
        "whole_withheld_responses": 1, "transport_failures_without_response": 1,
        "collector_api_requests": 0, "quota_database_actions": 0, "attempts": attempts})
    skipped_pin = write_new(FOLDER / "skipped_access_source.json", {
        "status": "SKIPPED_KNOWN_ACCESS_VALUE_SOURCE",
        "document_url": "https://www.njd.uscourts.gov/sites/njd/files/24-4055AppleZoomInstructions.pdf",
        "new_http_attempts": 0, "raw_response_sha256": None, "raw_response_bytes": None,
        "public_raw_available": False, "archive_path": None,
        "structural_reason_only": "Meeting access instructions and access-bearing link query",
        "access_values_or_access_query_recorded": False,
        "source_used_for_case_posture_or_acceptance": False})

    # Prove all pre-existing evidence and registry bytes without rewriting them.
    tree = subprocess.check_output(["git", "ls-tree", "-r", "-z", BASE,
        "--", "evidence", "market_checker_app/data"], cwd=ROOT)
    preserved = []
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        head, raw_path = entry.split(b"\t", 1)
        mode, kind, blob = head.decode().split()
        assert kind == "blob" and mode in {"100644", "100755"}
        path = raw_path.decode()
        body = (ROOT / path).read_bytes()
        actual_blob = hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()
        assert actual_blob == blob, path
        preserved.append({"path": path, "bytes": len(body), "git_blob": blob, "sha256": sha(body)})
    absent = ["evidence/batch4_regulatory_20261013/raw/nhtsa_api_docs.html",
              "evidence/batch4_ir_20261013/primary/ir_authority.html",
              "evidence/batch5_ir_20261014/primary/ir_authority.html",
              "evidence/batch8_regulatory_20261015/raw/njd_case_page.html"]
    assert all(not (ROOT / path).exists() for path in absent)
    prior_pin = write_new(FOLDER / "prior_byte_preservation.json", {
        "base_commit": BASE, "verified_at": datetime.now(timezone.utc).isoformat(),
        "scope": ["evidence/** previously tracked", "market_checker_app/data/** previously tracked"],
        "files": preserved, "file_count": len(preserved),
        "total_bytes": sum(item["bytes"] for item in preserved),
        "all_expected_git_blobs_match_local_original_bytes": True,
        "known_whole_withheld_originals_remain_absent": absent,
        "old_evidence_or_production_registry_writes": 0})

    request_path = FOLDER / "request.json"
    registry_path = ROOT / "market_checker_app/data/verified_counterparty_identities.json"
    records = json.loads(registry_path.read_text())["records"]
    request = json.loads(request_path.read_text())
    physical_ingestion = datetime.now(timezone.utc)
    with patch("socket.create_connection", side_effect=AssertionError("No source HTTP")), \
         patch.object(sqlite3, "connect", side_effect=AssertionError("No store access")):
        report = review_doj_case_posture(request, captures, as_of=None,
            ingested_at=physical_ingestion, legal_records=records)
    assert report["status"] == "PARTIAL" and report["supported_procedural_events"] == 2
    assert report["court_opinions_supported"] == 1 and report["original_court_orders_supported"] == 0
    assert report["latest_supported_documentary_event_day"] == "2025-06-30"
    assert report["known_at"] == physical_ingestion.isoformat()
    assert report["report_available_at_cutoff"] and report["http_requests"] == 0
    assert all(not report[field] for field in (
        "current_complete_docket_verified", "merits_adjudication_verified", "final_judgment_verified",
        "issuer_attribution_verified", "company_wide_liability_verified", "registry_adoption_allowed",
        "historical_availability_reconstructed", "live_acceptance_verified", "scoring_applied", "prediction_input"))
    replay_pin = write_new(FOLDER / "replay.json", report)
    immutable_dir = FOLDER / "reviews" / replay_pin["sha256"]
    immutable_dir.mkdir(parents=True, exist_ok=False)
    immutable_pin = write_new(immutable_dir / "review.json", report)
    assert replay_pin["sha256"] == immutable_pin["sha256"]
    review_pin = write_new(FOLDER / "review_record.json", {
        "schema_version": 1, "kind": "DOJ_CASE_POSTURE", "physical_report_known_at": report["known_at"],
        "actual_default_as_of": report["as_of"], "historical_knowledge_backdated": False,
        "source_observation_clocks_preserved": True, "report": immutable_pin,
        "request": pin(request_path), "central_captured_plan": pin(plan_path), "capture_index": index_pin,
        "legal_registry_read_only": pin(registry_path), "qualified_tests": pin(FOLDER / "tests_qualified.json"),
        "qualified_code_and_test_sha256": qualified["code_test_sha256"],
        "source_http_or_quota_db_actions_during_review": 0})

    handoff = {
        "schema_version": 1, "completed_at": datetime.now(timezone.utc).isoformat(), "base_commit": BASE,
        "owned_branch": "batch8/regulatory-20261015", "status": "PARTIAL",
        "api": "review_doj_case_posture(request, captures, *, as_of=None, ingested_at, legal_records=())",
        "module": "market_checker_app/services/doj_case_posture_review.py",
        "integration_kind": "DOJ_CASE_POSTURE", "request": pin(request_path),
        "central_captured_plan": pin(plan_path), "review_record": review_pin, "immutable_review": immutable_pin,
        "capture_index": index_pin, "prior_byte_preservation": prior_pin, "skipped_access_source": skipped_pin,
        "qualified_tests": pin(FOLDER / "tests_qualified.json"), "qualified_test_log": pin(FOLDER / "tests_qualified.log"),
        "qualified_code_and_test_sha256": qualified["code_test_sha256"], "qualified_tests_passed": 16,
        "earlier_diagnostics_preserved": [pin(FOLDER / name) for name in (
            "tests_initial.json", "tests_initial.log", "tests_final.json", "tests_final.log")],
        "diagnostic_limits": [
            "Initial run: one synthetic PDF apostrophe pin failure, not publisher-data normalization.",
            "Second run: one synthetic fixture-reset index error. Both failed results remain diagnostic.",
            "Final 16-test run qualifies the exact unchanged service/test hashes; no complete-suite rerun.",
            "Synthetic court-order contract tests are not an actual-source order acceptance.",
            "No new hosted Windows run was performed in this lane."],
        "actual_scope": {"docket": request["docket"], "court": request["court"], "issuer_ticker_selected": "AAPL",
            "issuer_attribution_verified": False, "literal_caption_entity": "APPLE, INC.",
            "party_opposition": {"document_number": "106", "filed_on": "2024-09-12", "judicial_decision": False},
            "court_opinion": {"document_number": "283", "filed_on": "2025-06-30", "dated_on": "2025-06-30",
                "motion_document_number": "86", "motion_to_dismiss": "DENIED", "original_order_obtained": False},
            "latest_supported_documentary_event_day": "2025-06-30", "current_complete_docket_verified": False,
            "merits_or_final_judgment_verified": False, "current_disposition_verified": False,
            "original_complaint_rereviewed": False, "source_index_update_timezone": "UNKNOWN"},
        "capture_limits": {"reserved_transport_attempts": 5, "maximum_transport_attempts": 6,
            "unused_attempts": 1, "additional_requests_after_finalization_resume": 0,
            "public_original_bodies": 3, "public_original_bytes": public_bytes, "whole_withheld_responses": 1,
            "transport_failure_without_response": 1, "automatic_redirects": False,
            "collector_api_requests": 0, "quota_database_actions": 0},
        "physical_known_at": report["known_at"], "historical_document_dates_are_capture_or_knowledge_dates": False,
        "missing_evidence": report["missing_evidence"],
        "critical_limits": [
            "Doc283 is the court's opinion, which mentions an accompanying Order. The separate Order was not obtained.",
            "The attempted public-document locator failed before a response; document284 is not verified.",
            "A party opposition records an adversary filing, not judicial acceptance of its claims.",
            "The source OCR caption and punctuation are preserved; APPLE, INC. is not adopted as a legal issuer alias.",
            "Earlier independent case/event-date identity and legal continuity remain unsupported.",
            "The DOJ index is context only; its September2024 update does not prove current docket completeness.",
            "The NJD case-page original is wholly withheld, with hash/size/clocks only and no acceptance.",
            "No ownership, issuer liability, fine, conviction, scoring, OOS, trading, DONE or live verification follows."],
        "pdf_visual_qa": {"original_pdf_pages_inspected_visually": {"govinfo_case_opinion": [1, 33], "doj_opposition_pdf": [1]},
            "full_pdf_text_access_scan_pages": 95, "substitute_rendered_or_extracted_publisher_body_published": False,
            "cryptographic_pdf_signature_validated": False},
        "old_evidence_or_registry_changes": 0, "registry_adoption_allowed": False,
        "source_http_during_finalization": 0, "database_actions_during_finalization": 0,
        "push_or_merge_performed": False, "parent_owns_shared_dispatch_cli_ui_and_publication": True}
    handoff_pin = write_new(FOLDER / "handoff.json", handoff)
    print(json.dumps({"handoff": handoff_pin, "prior_files_preserved": len(preserved),
        "physical_known_at": report["known_at"], "qualified_tests_passed": 16,
        "supported_procedural_events": 2, "original_court_orders_supported": 0}))


if __name__ == "__main__":
    main()
