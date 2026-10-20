"""Offline actual-source replay; writes new stage evidence without replacing it.

Only the already retained original is read. The capture schema adapter changes
metadata field names, never publisher bytes, source clocks, or request history.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from market_checker_app.services import doj_enforcement_scope_review as producer

BODY = "evidence/sourcefront_20261020/primary/doj_first_amended_complaint.pdf"
META = "evidence/sourcefront_20261020/primary/doj_first_amended_complaint.metadata.json"
BODY_SHA = "3c8eaebc6ebc5106ac3dfbe1751023cf4b16d297ca5e54c65ed9a75bc80fc2a6"
BODY_BYTES = 3_404_671
PRODUCER = "market_checker_app/services/doj_enforcement_scope_review.py"
CAPTURE = "doj_first_amended_complaint"


def pin(relative):
    body = (ROOT / relative).read_bytes()
    return {"path": relative, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def write_new(path, value):
    with path.open("xb") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n")


def prepare():
    body = (ROOT / BODY).read_bytes()
    assert (len(body), hashlib.sha256(body).hexdigest()) == (BODY_BYTES, BODY_SHA)
    raw = json.loads((ROOT / META).read_text())
    assert raw["status"] == "PUBLIC_ORIGINAL_BYTES_CAPTURED"
    assert raw["http_status"] == 200 and raw["redirect"] is False and raw["retry"] is False
    assert raw["body_transformed"] is False and raw["public_raw_available"] is True
    assert raw["final_url"] == raw["requested_url"]
    metadata = {
        "capture_id": raw["key"], "status": "ORIGINAL_BYTES_CAPTURED",
        "requested_url": raw["requested_url"], "final_url": raw["final_url"],
        "started_at": raw["started_at"], "observed_at": raw["observed_at"],
        "http_status": raw["http_status"], "automatic_redirects": raw["redirect"],
        "content_type": raw["response_headers"]["Content-Type"],
        "content_encoding": raw["response_headers"]["Content-Encoding"],
        "raw_response_bytes": raw["bytes"], "raw_response_sha256": raw["raw_sha256"],
        "public_raw_available": raw["public_raw_available"], "archive_path": BODY,
        "origin": "LIVE_PRIMARY_RESEARCH_HTTP", "archive_encoding": "raw",
        "original_capture_metadata_ref": pin(META),
        "metadata_adapter": "root-primary-research-capture-to-doj-original-v1",
    }
    request = {
        "issuer_ticker": "AAPL", "issuer_cik": "0000320193", "issuer_legal_name": "Apple Inc.",
        "issuer_capture_id": "unprovided_earlier_issuer_original",
        "issuer_anchor_date": "2023-09-30",
        "case_entity_name": "APPLE INC.", "case_entity_role": "DEFENDANT",
        "docket": "2:24-cv-04055", "docket_full_literal": "2:24-cv-04055-JXN-LDW",
        "court": "IN THE UNITED STATES DISTRICT COURT FOR THE DISTRICT OF NEW JERSEY",
        "filed_on": "2024-06-11", "document_kind": "AMENDED_COMPLAINT",
        "court_capture_id": CAPTURE,
        "selected_passages": [
            {"capture_id": CAPTURE, "page": 1, "purpose": "CAPTION",
             "excerpt": "Case 2:24-cv-04055-JXN-LDW Document 51 Filed 06/11/24 Page 1 of 91"},
            {"capture_id": CAPTURE, "page": 3, "purpose": "CASE_ENTITY",
             "excerpt": "APPLE INC. One Apple Park Way Cupertino, CA 95014 Defendant."},
            {"capture_id": CAPTURE, "page": 3, "purpose": "DOCUMENT_STAGE",
             "excerpt": "Defendant. FIRST AMENDED COMPLAINT"},
            {"capture_id": CAPTURE, "page": 17, "purpose": "LEGAL_ENTITY",
             "excerpt": "19. Apple is a global technology company with headquarters in Cupertino, California."},
        ],
    }
    plan = {"schema_version": "captured_identity_review_plan_v1", "kind": "DOJ_ENFORCEMENT_SCOPE",
            "request": request, "captures": {CAPTURE: {
                "metadata": metadata, "body_available": True, "body_ref": pin(BODY)}}}
    return plan, {CAPTURE: {"metadata": metadata, "body": body}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("pre_fix", "current"))
    args = parser.parse_args()
    destination = FOLDER / args.stage
    destination.mkdir(exist_ok=False)
    plan, captures = prepare()
    plan_path = FOLDER / "AAPL_puvodni_doplnene_podani.json"
    if not plan_path.exists():
        write_new(plan_path, plan)
    else:
        assert json.loads(plan_path.read_text()) == plan
    if args.stage == "pre_fix":
        shutil.copyfile(ROOT / PRODUCER, destination / "doj_enforcement_scope_review.py")
    started = datetime.now(timezone.utc)
    report = producer.review_doj_enforcement_scope(plan["request"], captures,
        as_of=started, ingested_at=started, legal_records=())
    completed = datetime.now(timezone.utc)
    assert completed >= started
    pages, count = producer._pdf_pages(captures[CAPTURE]["body"], {1, 3, 17})
    # This field-evidence extraction is part of the new interpretation; its
    # availability is the physical end, never the documentary filing date.
    field_evidence = []
    for passage in plan["request"]["selected_passages"]:
        text = pages[passage["page"]]
        normalized = " ".join(text.split())
        excerpt = " ".join(passage["excerpt"].split())
        offset = normalized.find(excerpt)
        field_evidence.append({**passage, "matched_exact_whitespace_normalized": offset >= 0,
            "normalized_excerpt_span": [offset, offset + len(excerpt)] if offset >= 0 else None,
            "page_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "page_text_characters": len(text),
            "evidence_scope": "PARTY_PLEADING_ALLEGATION_NOT_ADJUDICATED_FACT"})
    finished = datetime.now(timezone.utc)
    assert finished >= completed >= started
    result = {
        "schema_version": "sourcefront-doj-first-amended-original-review-v1",
        "review_started_at": started.isoformat(), "source_read_cutoff": started.isoformat(),
        "child_review_returned_at": completed.isoformat(), "known_at": finished.isoformat(),
        "derived_output_available_at_source_read_cutoff": False,
        "documentary_filed_on": "2024-06-11", "documentary_date_is_machine_availability": False,
        "original_source_observed_at": captures[CAPTURE]["metadata"]["observed_at"],
        "producer_pin": pin(PRODUCER), "replay_script_pin": pin(str(Path(__file__).relative_to(ROOT))),
        "plan_pin": pin(str(plan_path.relative_to(ROOT))),
        "original_body_pin": pin(BODY), "original_metadata_pin": pin(META),
        "original_pdf_page_count": count, "reviewed_selected_pages": sorted(pages),
        "field_evidence": field_evidence, "review": report,
        "http_requests_by_this_lane": 0, "collector_requests": 0, "database_operations": 0,
        "issuer_fields_are_unverified_request_context": True,
        "case_entity_financial_issuer_binding_verified": False,
        "legal_continuity_verified": False, "separate_original_order_present": False,
        "current_complete_docket_verified": False, "merits_or_disposition_verified": False,
        "adoptions": 0, "full_specialist_acceptance": False,
        "limitations": [
            "The original is a party pleading, including paragraph19; these are allegations.",
            "The current child API records caller ingested_at. This enclosing evidence stamps physical completion separately.",
            "Only the new complaint original is supplied; no earlier issuer original or legal registry rows are supplied.",
            "The source date and the unzoned retained DOJ index label do not establish historical machine availability.",
            "No separate signed Order, current complete docket, case-date CIK identity or continuity is established.",
        ],
    }
    write_new(destination / "actual_review.json", result)
    print(json.dumps({"stage": args.stage, "producer": result["producer_pin"],
        "known_at": result["known_at"], "case_supported": report["case_document_scope_supported"],
        "stage_supported": report["document_stage_supported"],
        "passages_supported": report.get("selected_passages_all_supported"),
        "status": report["status"]}, sort_keys=True))


if __name__ == "__main__":
    main()
