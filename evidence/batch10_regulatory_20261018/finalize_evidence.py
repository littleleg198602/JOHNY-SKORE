"""Freeze the pure actual case review and exact protected-file preservation.

No network, database connection, quota-file read or registry write. The original
PDFs remain in their existing batch8 paths; no substitute body is published.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
from unittest.mock import patch

import pypdf

from market_checker_app.collectors.doj_case_caption_review import review_doj_case_caption

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent
BASE = "32adb8edd6aa6ccc1748d2cfbc228f7db3a16fd4"
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
    qualified = json.loads((FOLDER / "focused_02.json").read_text())
    assert qualified["exit_code"] == 0
    log = (FOLDER / "focused_02.log").read_bytes()
    assert sha(log) == qualified["log_sha256"]
    statuses = re.findall(rb"^test_[^\n]+ \.\.\. ([^\n]+)$", log, re.M)
    assert len(statuses) == 21 and set(statuses) == {b"ok"}
    assert b"Ran 21 tests" in log and log.rstrip().endswith(b"OK")
    for path, expected in qualified["code_test_sha256"].items():
        assert sha((ROOT / path).read_bytes()) == expected
    plan_path = FOLDER / "actual_review_plan.json"
    plan = json.loads(plan_path.read_text())
    captures, originals = {}, []
    for identifier, descriptor in plan["captures"].items():
        original_path = ROOT / descriptor["body_ref"]["path"]
        assert pin(original_path) == descriptor["body_ref"]
        captures[identifier] = (descriptor["metadata"], original_path.read_bytes())
        originals.append({"capture_id": identifier, "original": pin(original_path),
            "original_metadata": pin(original_path.with_suffix(".metadata.json")),
            "source_capture_clock": descriptor["metadata"]["observed_at"]})
    with patch.object(socket, "create_connection", side_effect=AssertionError("No network")), \
         patch.object(sqlite3, "connect", side_effect=AssertionError("No database")):
        report = review_doj_case_caption(plan["request"], captures)
    assert report["supported_case_documents"] == 2 and report["supported_caption_blocks"] == 3
    assert report["same_case_corroborated_by_distinct_originals"]
    assert sum(len(row["page_reviews"]) for row in report["document_reviews"]) == 95
    assert not report["issuer_identity_verified"] and not report["original_separate_order_present"]
    assert not report["current_disposition_verified"] and report["http_requests"] == report["database_actions"] == 0
    replay_pin = write(FOLDER / "actual_review.json", report)
    immutable_pin = write(FOLDER / "reviews" / replay_pin["sha256"] / "review.json", report)
    assert replay_pin["sha256"] == immutable_pin["sha256"]

    # Exact raw bytes for all older evidence/data and Python source versions.
    # Root .bat worktree line endings follow .gitattributes and are not covered
    # by this raw-blob manifest. No tracked file is edited by this lane.
    preserved = []
    for entry in subprocess.check_output(["git", "ls-tree", "-r", "-z", BASE], cwd=ROOT).split(b"\0"):
        if not entry:
            continue
        head, name = entry.split(b"\t", 1)
        path = name.decode()
        if not (path.startswith(("evidence/", "market_checker_app/data/")) or path.endswith(".py")):
            continue
        mode, kind, blob = head.decode().split()
        assert kind == "blob" and mode in {"100644", "100755"}
        body = (ROOT / path).read_bytes()
        assert hashlib.sha1(b"blob "+str(len(body)).encode()+b"\0"+body).hexdigest() == blob, path
        preserved.append({"path": path, "bytes": len(body), "git_blob": blob, "sha256": sha(body)})
    assert all(not (ROOT / path).exists() for path in WITHHELD)
    assert subprocess.check_output(["git", "diff", "--name-only"], cwd=ROOT) == b""
    preservation_pin = write(FOLDER / "prior_byte_preservation.json", {
        "base_commit": BASE, "verified_at": datetime.now(timezone.utc).isoformat(),
        "scope": ["all prior evidence/**", "all prior market_checker_app/data/**", "all prior *.py source/test/version files"],
        "files": preserved, "file_count": len(preserved), "total_bytes": sum(row["bytes"] for row in preserved),
        "all_protected_originals_match_base_git_blobs": True, "tracked_files_modified_by_lane": 0,
        "four_whole_withheld_body_paths_remain_absent": WITHHELD, "quota_database_opened_or_read": False})
    facts = sum(sum(len(page["field_facts"]) for page in row["page_reviews"])
                +sum(len(caption["field_facts"]) for caption in row["captions"])
                +len(row["separate_order_references"]) for row in report["document_reviews"])
    record_pin = write(FOLDER / "review_record.json", {
        "review_kind": "DOJ_CASE_CAPTION", "physical_known_at": report["known_at"], "report": immutable_pin,
        "plan": pin(plan_path), "qualified_code_test_sha256": qualified["code_test_sha256"],
        "qualified_result": pin(FOLDER / "focused_02.json"), "original_source_pins": originals,
        "producer_runtime": {"python": sys.version.split()[0], "pypdf": pypdf.__version__},
        "source_gate_dependency": pin(ROOT / "market_checker_app/services/doj_enforcement_scope_review.py"),
        "captured_pdf_pages": 95, "document_local_caption_blocks": 3, "field_evidence_records": facts,
        "source_publication_or_filing_dates_backdated_to_knowledge": False,
        "raw_page_text_exported_as_substitute_original": False})
    handoff = {"schema_version": 1, "completed_at": datetime.now(timezone.utc).isoformat(),
        "base_commit": BASE, "branch": "batch10/regulatory-20261018", "status": "PARTIAL",
        "api": "review_doj_case_caption(request, captures, *, reviewed_at=None)", "kind": "DOJ_CASE_CAPTION",
        "module": "market_checker_app/collectors/doj_case_caption_review.py",
        "packet_forms": ["capture_id -> (metadata, original_pdf_bytes_or_none)", "capture_id -> {metadata, body}"],
        "request_schema": "case_docket, court, documents[{capture_id,document_number,filed_on,document_kind}]",
        "no_financial_issuer_or_registry_input": True, "actual_plan": pin(plan_path), "actual_review": immutable_pin,
        "review_record": record_pin, "qualified_source_test_sha256": qualified["code_test_sha256"],
        "qualified_tests": pin(FOLDER / "focused_02.json"), "qualified_complete_log": pin(FOLDER / "focused_02.log"),
        "qualified_counts": {"executed": 21, "passed": 21, "failures": 0, "errors": 0, "skips": 0,
                             "expected_failures": 0, "unexpected_successes": 0},
        "source_counts": {"whole_original_pdfs": 2, "whole_original_bytes": sum(row["original"]["bytes"] for row in originals),
            "decoded_and_stamped_pdf_pages": 95, "document_local_caption_blocks": 3, "field_evidence_records": facts,
            "new_http_requests": 0, "new_collector_source_requests": 0, "database_connections_reads_or_writes": 0},
        "novel_actual_facts": [
            "Every one of62 Doc106 pages and33 Doc283 pages has its own matching full docket/document/filed/page stamp with exact PDF/page-text/span hashes.",
            "Doc106 p1 says APPLE, INC./Defendant; its p62 service certificate independently says APPLE INC./Defendants with a differently worded plaintiff caption. These are separate literal facts, not adopted aliases.",
            "Doc283 p1 has APPLE, INC.,/Defendant and OCR-layer ef at preserved. Its printed24-cv-4055 caption lacks division and full suffix; omitted values remain null.",
            "Full 2:24-cv-04055-JXN-LDW appears explicitly in the same-page stamps. Numeric abbreviation compatibility corroborates the case base only, not omitted suffixes or financial identity.",
            "The existing opinion's separate-Order reference is preserved as a reference only; no separate original Order is supplied."],
        "earlier_qualification_and_regression": {
            "earlier_20_method_version": pin(FOLDER / "focused_01.json"),
            "original_pre_span_sources_preserved": True,
            "new_same_clause_parenthetical_regression_against_prior_version": pin(FOLDER / "pre_span_regression.json"),
            "pre_span_regression_expected_failures": 1, "pre_span_regression_errors": 0,
            "current_21_method_version_passed": True, "publisher_pdf_hashes_or_text_assertions_relaxed": False},
        "prior_byte_preservation": preservation_pin,
        "limits": {"max_documents": 3, "max_body_bytes": 4_000_000, "max_pages_per_document": 120,
            "max_text_characters_per_document": 2_000_000, "modern_two_digit_filed_stamp_century_scope": "2000_2099",
            "complete_named_plaintiff_list_proven": False, "financial_issuer_cik_or_continuity_verified": False,
            "separate_signed_order_verified": False, "current_docket_or_disposition_verified": False,
            "merits_or_final_judgment_verified": False, "score_model_budget_registry_changes": False},
        "visual_qa": {"newly_inspected_original_page": {"capture_id": "doj_opposition_pdf", "page": 62,
            "original_pdf_sha256": "8d68256e4a356aff8a501eeaa887439185e97ea836e830c434adf51adf6d45c3"},
            "caption_name_and_plural_defendants_confirmed": True, "derived_render_published": False,
            "cryptographic_signature_validation_claimed": False},
        "retained_ledger_sha256_from_assignment_only_not_read_by_lane": "6d6a7450d9a66bb0fbc3c006deb3aca6c595fcdf1ca682561b476a2f4d9400af",
        "nhtsa_approval_block_retried_or_worked_around": False,
        "four_whole_withheld_bodies_absent_and_never_inspected": True,
        "full_baseline_or_hosted_suite_repeated": False, "new_windows_or_full_specialist_acceptance_claimed": False,
        "registry_adoptions": 0, "full_acceptance": "0/21", "push_or_merge_performed": False,
        "next_work": ["Independent dated financial/legal issuer-to-caption continuity",
            "Whole original separate signed court Order", "Genuine bounded current docket/disposition originals when separately authorized"],
        "parent_owns_shared_dispatch_cli_ui_status_and_publication": True}
    handoff_pin = write(FOLDER / "handoff.json", handoff)
    print(json.dumps({"handoff": handoff_pin, "review": immutable_pin, "protected_prior_files": len(preserved),
        "qualified_tests_passed": 21, "actual_documents": 2, "caption_blocks": 3, "page_stamps": 95,
        "field_evidence_records": facts, "physical_known_at": report["known_at"], "http_or_database_actions": 0}, sort_keys=True))


if __name__ == "__main__":
    main()
