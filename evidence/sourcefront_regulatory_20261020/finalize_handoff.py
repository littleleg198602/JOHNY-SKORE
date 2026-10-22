"""Pin this owned offline output; never open a quota database or make HTTP."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from evidence.sourcefront_regulatory_20261020 import replay_first_amended as replay
from market_checker_app.services import doj_enforcement_scope_review as producer

BASE = "39e5958b20b66338ad2029e99908609834d67db3"
INITIAL = Path("/workspace/scratch/334b3a149083/sourcefront_20261020/regulatory.json")


def pin(path):
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    body = path.read_bytes()
    return {"path": str(path.relative_to(ROOT)), "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def write_new(path, value):
    with path.open("xb") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n")


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def main():
    started = datetime.now(timezone.utc)
    actual = json.loads((FOLDER / "current/actual_review.json").read_text())
    before = json.loads((FOLDER / "pre_fix/actual_review.json").read_text())
    qualified = json.loads((FOLDER / "tests_final.json").read_text())
    assert qualified["successful"] is True and qualified["tests_run"] == 9
    assert all(qualified[field] == 0 for field in (
        "failures", "errors", "skipped", "expected_failures", "unexpected_successes"))
    for path, digest in qualified["pins_at_execution"].items():
        assert pin(path) == {"path": path, **digest}
    assert actual["producer_pin"] == pin(replay.PRODUCER)
    assert actual["replay_script_pin"] == pin("evidence/sourcefront_regulatory_20261020/replay_first_amended.py")
    assert actual["review"]["case_document_scope_supported"] is True
    assert actual["review"]["document_stage_supported"] is True
    assert before["review"]["case_document_scope_supported"] is False
    assert before["review"]["document_stage_supported"] is False
    preimage = (FOLDER / "pre_fix/doj_enforcement_scope_review.py").read_bytes()
    assert preimage == git("show", BASE + ":" + replay.PRODUCER)
    assert hashlib.sha256(preimage).hexdigest() == before["producer_pin"]["sha256"]
    assert git("diff", "--name-only", BASE).decode().splitlines() == [replay.PRODUCER]
    assert git("diff", "--name-only", BASE, "--", "evidence", "tests").decode() == ""
    initial = INITIAL.read_bytes()
    assert (len(initial), hashlib.sha256(initial).hexdigest()) == (
        20610, "0b22ae8693fc88abf855957e9edf0b5deacec89352cc60be4e85815dfad7fb53")
    with (FOLDER / "initial_source_feasibility_snapshot.json").open("xb") as stream:
        stream.write(initial)

    # Inspect the already retained, safe exact link only; do not fetch any URI.
    index_path = "evidence/batch8_regulatory_20261015/raw/doj_case_index.html"
    index = (ROOT / index_path).read_bytes()
    assert (len(index), hashlib.sha256(index).hexdigest()) == (
        107793, "93e092c53234733cd13e4b6affc8cb49e4d5b1230920dc276b9c77c1c59491b3")
    anchor = index[89848:89975]
    assert hashlib.sha256(anchor).hexdigest() == "76f72287896c5e6ce4f89b1ca3322e00426629a9440f09d19f7178cb067e55c1"
    assert b'href="/atr/media/1358786/dl"' in anchor
    assert b"First Amended Complaint" in anchor
    plan, captures = replay.prepare()
    pages, count = producer._pdf_pages(captures[replay.CAPTURE]["body"], {17})
    paragraph = re.search(r"\b19\.\s+Apple is a global technology company.*?(?=\n\s*20\.)", pages[17], re.S)
    assert paragraph is not None
    finished = datetime.now(timezone.utc)
    assert finished >= started and count == 91
    provenance = {
        "schema_version": "sourcefront-doj-pleading-source-links-v1",
        "review_started_at": started.isoformat(), "known_at": finished.isoformat(),
        "original_body": pin(replay.BODY), "original_metadata": pin(replay.META),
        "metadata_adapter": {
            "original_metadata_unmodified": True,
            "adapted_plan_metadata": plan["captures"][replay.CAPTURE]["metadata"],
            "field_mapping": {"capture_id": "key", "raw_response_bytes": "bytes",
                "raw_response_sha256": "raw_sha256", "automatic_redirects": "redirect",
                "content_type": "response_headers.Content-Type",
                "content_encoding": "response_headers.Content-Encoding",
                "status": "PUBLIC_ORIGINAL_BYTES_CAPTURED maps to ORIGINAL_BYTES_CAPTURED",
                "origin": "Root's primary research GET receipt; not an app collector request."},
        },
        "retained_index_reference": {"original_body": pin(index_path),
            "anchor_raw_byte_span": [89848, 89975], "anchor_raw_sha256": hashlib.sha256(anchor).hexdigest(),
            "anchor_original_text": anchor.decode(), "label": "First Amended Complaint",
            "href": "/atr/media/1358786/dl", "listed_documentary_date": "2024-06-11",
            "listed_date_timezone": "UNKNOWN", "is_current_complete_docket": False},
        "prior_opinion_crossreference": {
            "review_snapshot": pin(FOLDER / "initial_source_feasibility_snapshot.json"),
            "original_snapshot_path": str(INITIAL), "original_snapshot_unchanged": True,
            "locator": "Retained Doc283 page2 cites (Am. Compl. paragraph19); original Doc51 paragraph19 is now supplied.",
            "source_inference_limit": "The opinion's pleading attribution does not independently adjudicate the description or financial figures."},
        "paragraph19": {"capture_id": replay.CAPTURE, "page": 17,
            "page_text_sha256": hashlib.sha256(pages[17].encode()).hexdigest(),
            "page_text_character_span": list(paragraph.span()), "literal": paragraph[0],
            "normalized_literal": " ".join(paragraph[0].split()),
            "scope": "PARTY_PLEADING_ALLEGATION_ONLY", "document_filed_on": "2024-06-11",
            "issuer_cik_identity_verified": False, "figures_adjudicated": False},
        "parent_research_transports_for_this_new_original": 1,
        "parent_research_receipt": pin("evidence/sourcefront_20261020/amended_complaint_verification.json"),
        "source_plan": pin("evidence/sourcefront_20261020/amended_complaint_plan.json"),
        "lane_http_requests": 0, "database_operations": 0,
        "separate_order_present": False, "current_docket_verified": False,
        "case_date_financial_issuer_binding_verified": False, "merits_or_disposition_verified": False,
    }
    write_new(FOLDER / "source_links_and_allegation.json", provenance)
    absent = [
        "evidence/batch4_regulatory_20261013/raw/nhtsa_api_docs.html",
        "evidence/batch4_ir_20261013/primary/ir_authority.html",
        "evidence/batch5_ir_20261014/primary/ir_authority.html",
        "evidence/batch8_regulatory_20261015/raw/njd_case_page.html",
    ]
    assert all(not (ROOT / path).exists() for path in absent)
    preservation = {
        "checked_at": datetime.now(timezone.utc).isoformat(), "base_commit": BASE,
        "base_tree": git("rev-parse", BASE + "^{tree}").decode().strip(),
        "tracked_changes": [replay.PRODUCER], "old_tracked_evidence_changed": [],
        "old_tracked_tests_changed": [], "whole_original_body_matches_base_git_blob":
            (ROOT / replay.BODY).read_bytes() == git("show", BASE + ":" + replay.BODY),
        "whole_original_metadata_matches_base_git_blob":
            (ROOT / replay.META).read_bytes() == git("show", BASE + ":" + replay.META),
        "historical_source_preimage_matches_base_git_blob": True,
        "initial_outside_git_snapshot_unchanged": True,
        "withheld_paths_absent_without_opening": absent,
        "quota_database_opened_or_hashed": False,
    }
    write_new(FOLDER / "preservation.json", preservation)
    pins = [pin(path) for path in sorted(FOLDER.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"]
    handoff = {
        "schema_version": "sourcefront-regulatory-handoff-v1", "known_at": datetime.now(timezone.utc).isoformat(),
        "base_commit": BASE, "producer": pin(replay.PRODUCER),
        "new_test": pin("tests/test_sourcefront_doj_first_amended.py"),
        "api": "review_doj_enforcement_scope(request, captures, *, as_of, ingested_at, legal_records=())",
        "kind": "DOJ_ENFORCEMENT_SCOPE", "contract_version": producer.CONTRACT_VERSION,
        "actual_plan": pin("evidence/sourcefront_regulatory_20261020/AAPL_puvodni_doplnene_podani.json"),
        "actual_report": pin("evidence/sourcefront_regulatory_20261020/current/actual_review.json"),
        "actual_scope": {"docket_full_literal": "2:24-cv-04055-JXN-LDW", "document_number": "51",
            "filed_on": "2024-06-11", "named_case_party": "APPLE INC.", "party_role": "DEFENDANT",
            "document_title_literal": "FIRST AMENDED COMPLAINT", "case_stage": "ALLEGATIONS_ONLY",
            "whole_original_pdf_pages": 91, "selected_pages": [1, 2, 3, 17],
            "paragraph19_original_supplied": True, "issuer_binding_verified": False},
        "change": "Recognize and retain stamped full judge suffix; require selected-page stamp consistency; classify FIRST AMENDED COMPLAINT without adjudication.",
        "optional_request_field": "docket_full_literal: exact full stamp; its printed base must equal docket. Omit for legacy base-only callers; observed suffix is still retained.",
        "new_report_fields": ["court_stamp_observations", "selected_document_stamps_consistent", "document_title_literal"],
        "focused_tests": qualified, "old_suites_rerun": False, "old_tests_changed": False,
        "initial_test_result": {"tests_run": 9, "failures": 1, "errors": 0,
            "cause": "Only the new fixture expected the wrong existing no-match enum; corrected to UNMAPPED_NAME without producer change.",
            "preserved_source_and_log": True},
        "actual_status": "PARTIAL", "adoptions": 0, "full_specialist_acceptance": False,
        "lane_http_requests": 0, "parent_new_primary_transports": 1, "collector_requests": 0,
        "quota_or_other_database_operations": 0, "scores_or_trading_changed": False,
        "known_clock_limit": "Child API's input ingestion clock is retained; actual owned envelope stamps physical completion and explicitly denies output availability at its earlier source-read cutoff.",
        "missing_evidence": ["Independent earlier issuer original not supplied in this plan",
            "Case-date registered issuer identity", "Identity known before case day", "Independent legal continuity",
            "Separate signed original Order", "Current complete docket/disposition", "Adjudicated merits or liability"],
        "next_work": "Use separately authorized exact safe primary originals for missing Order/current docket/dated legal binding; do not refetch this pleading or any withheld body.",
        "evidence_pins": pins,
    }
    write_new(FOLDER / "handoff.json", handoff)
    print(json.dumps({"producer": handoff["producer"], "test": handoff["new_test"],
        "handoff": pin("evidence/sourcefront_regulatory_20261020/handoff.json"),
        "actual_status": "PARTIAL", "focused": "9/9 all other outcomes0", "lane_http": 0}, sort_keys=True))


if __name__ == "__main__":
    main()
