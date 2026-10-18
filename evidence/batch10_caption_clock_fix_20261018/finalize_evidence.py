"""Freeze the offline completion-clock replay without replacing prior evidence."""
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
HERE = Path(__file__).resolve().parent
BASE = "bca703329150bbe2478afabffec1590505429504"
MODULE = "market_checker_app/collectors/doj_case_caption_review.py"
OLD_TEST = "tests/test_doj_case_caption_review.py"
OLD_FOLDER = ROOT / "evidence/batch10_regulatory_20261018"
WITHHELD = ["evidence/batch4_regulatory_20261013/raw/nhtsa_api_docs.html",
            "evidence/batch4_ir_20261013/primary/ir_authority.html",
            "evidence/batch5_ir_20261014/primary/ir_authority.html",
            "evidence/batch8_regulatory_20261015/raw/njd_case_page.html"]
CLOCK_ONLY_CHANGES = {"contract_version", "reviewed_at", "known_at", "review_started_at",
    "review_completed_at", "source_read_cutoff", "source_read_cutoff_is_output_availability",
    "completion_clock_verified"}


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


def clock(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None and parsed.utcoffset() is not None
    return parsed.astimezone(timezone.utc)


def main():
    qualified = json.loads((HERE / "after.json").read_text())
    log = (HERE / "after.log").read_bytes()
    assert qualified["successful"] and qualified["executed"] == 31
    assert all(qualified[key] == 0 for key in ("failures", "errors", "skips", "expected_failures", "unexpected_successes"))
    assert sha(log) == qualified["log_sha256"]
    statuses = re.findall(rb"^test_[^\n]+ \.\.\. ([^\n]+)$", log, re.M)
    assert len(statuses) == 31 and set(statuses) == {b"ok"}
    assert b"Ran 31 tests" in log and log.rstrip().endswith(b"OK")
    for path, expected in qualified["producer_test_sha256"].items():
        assert sha((ROOT / path).read_bytes()) == expected
    before = json.loads((HERE / "before.json").read_text())
    assert before["executed"] == before["failures"] == 2 and before["errors"] == 0
    assert sha((HERE / "before.log").read_bytes()) == before["log_sha256"]

    # The old request, source selectors, metadata and raw-body pins are unchanged.
    old_plan = OLD_FOLDER / "actual_review_plan.json"
    plan_path = HERE / "actual_review_plan.json"
    with plan_path.open("xb") as stream:
        stream.write(old_plan.read_bytes())
    plan = json.loads(plan_path.read_text())
    assert plan["kind"] == "DOJ_CASE_CAPTION"
    captures, originals = {}, []
    for identifier, descriptor in plan["captures"].items():
        path = ROOT / descriptor["body_ref"]["path"]
        assert pin(path) == descriptor["body_ref"]
        captures[identifier] = (descriptor["metadata"], path.read_bytes())
        originals.append({"capture_id": identifier, "original": pin(path),
            "original_metadata": pin(path.with_suffix(".metadata.json")),
            "source_observed_at": descriptor["metadata"]["observed_at"]})
    call_started = datetime.now(timezone.utc)
    with patch.object(socket.socket, "connect", side_effect=AssertionError("No HTTP or source request")), \
         patch.object(socket, "create_connection", side_effect=AssertionError("No HTTP or source request")), \
         patch.object(sqlite3, "connect", side_effect=AssertionError("No database")):
        report = review_doj_case_caption(plan["request"], captures)
    call_returned = datetime.now(timezone.utc)
    assert call_started <= clock(report["review_started_at"]) <= clock(report["source_read_cutoff"])
    assert clock(report["source_read_cutoff"]) <= clock(report["known_at"]) <= call_returned
    assert report["known_at"] == report["review_completed_at"] and report["completion_clock_verified"]
    assert not report["source_read_cutoff_is_output_availability"]
    for source in report["source_reviews"]:
        assert clock(source["source_started_at"]) <= clock(source["source_observed_at"]) <= clock(source["source_known_at"])
        assert clock(source["source_known_at"]) <= clock(report["source_read_cutoff"])
        assert clock(source["source_known_at"]) <= clock(report["known_at"])
    old_report = json.loads((OLD_FOLDER / "actual_review.json").read_text())
    without_clocks = lambda data: {k: v for k, v in data.items() if k not in CLOCK_ONLY_CHANGES}
    assert without_clocks(old_report) == without_clocks(report)
    assert report["supported_case_documents"] == 2 and report["supported_caption_blocks"] == 3
    assert report["same_case_corroborated_by_distinct_originals"]
    pages = sum(len(row["page_reviews"]) for row in report["document_reviews"])
    facts = sum(sum(len(page["field_facts"]) for page in row["page_reviews"])
        + sum(len(caption["field_facts"]) for caption in row["captions"])
        + len(row["separate_order_references"]) for row in report["document_reviews"])
    assert pages == 95 and facts == 686
    assert not any(report[key] for key in ("issuer_identity_verified", "legal_continuity_verified",
        "original_separate_order_present", "merits_adjudication_verified", "final_judgment_verified",
        "current_disposition_verified", "registry_adoption_allowed", "historical_availability_reconstructed"))
    assert report["http_requests"] == report["database_actions"] == 0
    replay_pin = write(HERE / "actual_review.json", report)
    immutable_pin = write(HERE / "reviews" / replay_pin["sha256"] / "review.json", report)
    assert replay_pin["sha256"] == immutable_pin["sha256"]

    preserved, changed_frozen = [], []
    for entry in subprocess.check_output(["git", "ls-tree", "-r", "-z", BASE], cwd=ROOT).split(b"\0"):
        if not entry:
            continue
        header, name = entry.split(b"\t", 1)
        path = name.decode()
        if not (path.startswith(("evidence/", "market_checker_app/data/")) or path.endswith(".py")):
            continue
        mode, kind, blob = header.decode().split()
        assert kind == "blob" and mode in {"100644", "100755"}
        frozen = {MODULE: "before/caption_review.py", OLD_TEST: "before/caption_tests.py"}.get(path)
        observed_path = HERE / frozen if frozen else ROOT / path
        body = observed_path.read_bytes()
        assert hashlib.sha1(b"blob "+str(len(body)).encode()+b"\0"+body).hexdigest() == blob, path
        record = {"path": path, "bytes": len(body), "git_blob": blob, "sha256": sha(body)}
        if frozen:
            record["exact_pre_fix_source_preserved_at"] = observed_path.relative_to(ROOT).as_posix()
            changed_frozen.append(record)
        else:
            preserved.append(record)
    assert len(changed_frozen) == 2
    assert all(not (ROOT / path).exists() for path in WITHHELD)
    modified = subprocess.check_output(["git", "diff", "--name-only"], cwd=ROOT).decode().splitlines()
    assert sorted(modified) == sorted([MODULE, OLD_TEST])
    preservation_pin = write(HERE / "prior_byte_preservation.json", {
        "base_commit": BASE, "verified_at": datetime.now(timezone.utc).isoformat(),
        "scope": ["all prior evidence/**", "all prior market_checker_app/data/**",
                  "all prior *.py except the two explicitly changed owned source/test files"],
        "files": preserved, "file_count": len(preserved), "total_bytes": sum(row["bytes"] for row in preserved),
        "all_in_scope_originals_match_base_git_blobs": True, "changed_owned_files_exact_before_versions": changed_frozen,
        "old_bca_reports_logs_failures_plan_and_stamped_facts_modified": False,
        "four_whole_withheld_body_paths_remain_absent": WITHHELD,
        "quota_database_opened_read_hashed_or_mutated": False})
    record_pin = write(HERE / "review_record.json", {
        "review_kind": "DOJ_CASE_CAPTION", "producer_contract_version": report["contract_version"],
        "call_started_at": call_started.isoformat(), "call_returned_at": call_returned.isoformat(),
        "review_started_at": report["review_started_at"], "source_read_cutoff": report["source_read_cutoff"],
        "physical_completion_known_at": report["known_at"], "report": immutable_pin,
        "original_plan": pin(old_plan), "actual_plan": pin(plan_path), "same_exact_plan_bytes": True,
        "old_report_unchanged": pin(OLD_FOLDER / "actual_review.json"),
        "all_non_clock_derived_content_exactly_equals_old_report": True,
        "excluded_clock_contract_fields_for_comparison": sorted(CLOCK_ONLY_CHANGES),
        "qualified_code_test_sha256": qualified["producer_test_sha256"],
        "qualified_result": pin(HERE / "after.json"), "original_source_pins": originals,
        "producer_runtime": {"python": sys.version.split()[0], "pypdf": pypdf.__version__},
        "source_gate_dependency": pin(ROOT / "market_checker_app/services/doj_enforcement_scope_review.py"),
        "captured_pdf_pages": pages, "document_local_caption_blocks": 3, "field_evidence_records": facts,
        "source_dates_backdated_to_output_knowledge": False, "all_supported_source_clocks_not_after_output": True,
        "HTTP_collector_DB_quota_or_registry_actions": 0})
    handoff = {"schema_version": 1, "completed_at": datetime.now(timezone.utc).isoformat(),
        "base_commit": BASE, "branch": "batch10/caption-clock-fix-20261018", "status": "PARTIAL",
        "api": "review_doj_case_caption(request, captures, *, reviewed_at=None)", "kind": "DOJ_CASE_CAPTION",
        "module": MODULE, "request_schema_unchanged": True, "actual_plan": pin(plan_path),
        "packet_forms": ["capture_id -> (metadata, original_pdf_bytes_or_none)", "capture_id -> {metadata, body}"],
        "actual_review": immutable_pin, "review_record": record_pin,
        "qualified_source_test_sha256": qualified["producer_test_sha256"],
        "qualified_result": pin(HERE / "after.json"), "qualified_complete_log": pin(HERE / "after.log"),
        "qualified_counts": {"executed": 31, "passed": 31, "failures": 0, "errors": 0, "skips": 0,
                             "expected_failures": 0, "unexpected_successes": 0},
        "qualification_scope": "10 new clock regressions plus 21 owned caption methods; no baseline/full suite rerun",
        "before_regression": {"result": pin(HERE / "before.json"), "complete_log": pin(HERE / "before.log"),
            "executed": 2, "failures": 2, "errors": 0, "interpretation": "real old-version diagnostics, not current acceptance"},
        "clock_behavior": {"review_started_at": "physical UTC before parsing",
            "reviewed_at_and_source_read_cutoff": "aware source-read cutoff; default physical start; explicit future rejected",
            "known_at_and_review_completed_at": "physical UTC after all parsing, bindings, facts and nested clock validation",
            "future_cutoff_exception": "FUTURE_CAPTION_SOURCE_READ_CUTOFF_UNAVAILABLE",
            "rollback_exception": "CAPTION_REVIEW_PHYSICAL_CLOCK_ROLLBACK",
            "completion_before_source_or_nested_clock_exception": "CAPTION_REVIEW_COMPLETION_PRECEDES_SOURCE_OR_DERIVED_CLOCK",
            "all_report_return_paths_finish_guarded": True, "historical_output_availability_reconstructed": False},
        "owned_test_expectation_change": {"file": OLD_TEST,
            "method": "test_earlier_cutoff_or_future_source_clock_never_decodes",
            "change": "future source still PARTIAL without decoding; explicit future cutoff now must raise ValueError before decode",
            "exact_pre_fix_test_source_preserved": pin(HERE / "before/caption_tests.py"),
            "source_pdf_hash_text_or_identity_assertions_relaxed": False,
            "original_public_base32_test_files_changed": 0},
        "prior_byte_preservation": preservation_pin,
        "actual_counts": {"whole_retained_original_pdfs": 2, "pages": pages, "caption_blocks": 3,
            "field_evidence_records": facts, "all_non_clock_facts_equal_before_report": True,
            "new_HTTP_requests": 0, "new_collector_source_requests": 0,
            "database_connections_reads_writes_or_hashes": 0, "quota_or_lease_operations": 0},
        "unchanged_limits": {"financial_issuer_or_cik_identity_verified": False,
            "legal_continuity_verified": False, "separate_original_order_verified": False,
            "complete_current_docket_or_disposition_verified": False, "merits_or_final_judgment_verified": False,
            "registry_adoptions": 0, "score_model_budget_trading_changes": False, "full_acceptance": "0/21"},
        "old_bca_output_clock_version_superseded_without_rewriting_old_evidence": True,
        "four_whole_withheld_bodies_absent_and_never_inspected": True,
        "retained_ledger_sha256_from_assignment_only_not_read": "6d6a7450d9a66bb0fbc3c006deb3aca6c595fcdf1ca682561b476a2f4d9400af",
        "NHTSA_approval_block_retried_or_worked_around": False, "push_or_merge_performed": False,
        "next_work": ["Root integrates the completion-clock contract into common runtime/UI",
            "Independent dated financial issuer/caption legal continuity remains absent",
            "Separate whole signed Order and genuine current docket/disposition remain absent"],
        "parent_owns_shared_runtime_UI_docs_status_and_publication": True}
    handoff_pin = write(HERE / "handoff.json", handoff)
    print(json.dumps({"handoff": handoff_pin, "report": immutable_pin,
        "prior_files_byte_preserved": len(preserved), "changed_owned_before_sources_frozen": len(changed_frozen),
        "qualified_tests": 31, "actual_non_clock_facts_identical": True,
        "review_started_at": report["review_started_at"], "known_at": report["known_at"],
        "HTTP_collector_DB_quota_actions": 0}, sort_keys=True))


if __name__ == "__main__":
    main()
