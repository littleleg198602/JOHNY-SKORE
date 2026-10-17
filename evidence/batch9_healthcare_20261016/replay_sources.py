"""Exact three-capture static-source replay, no HTTP/database/registry calls."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from market_checker_app.services.fda_application_product_review import review_fda_application_product
from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body
from market_checker_app.utils.ticker_universe import load_canonical_tickers

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PINS = {
    1: (145851, "1510a16e20a830e24b4ae3858bad462c5ec7d48b66a2c158a483b3c10833ec1b"),
    2: (34010, "54e7c4548dcc2a2003649db0f12d9658fc50b22180daf7141670ff7226323872"),
    3: (22644, "2a1a9b6d4af00c81a1e144361f4b5679a77c0abb0377f8134ab543343984d730"),
}


def capture(sequence):
    path = HERE / f"attempt-{sequence:02d}-result.json"
    metadata = json.loads(path.read_bytes())
    reserved = json.loads((HERE / f"attempt-{sequence:02d}-reserved.json").read_bytes())
    plan_raw = (HERE / "research_plan.json").read_bytes()
    if (any(metadata.get(key) != value for key, value in reserved.items())
            or metadata["research_plan_sha256"] != hashlib.sha256(plan_raw).hexdigest()
            or metadata["http_status"] != 200 or metadata["status"] != "HTTP_200_COMPLETE"
            or metadata["raw_publication_permitted"] is not True or not metadata["body_complete_within_byte_budget"]
            or metadata["byte_budget"] != 4_000_000 or metadata["redirects_allowed"] or metadata["retries_allowed"]
            or metadata["collector_API_or_quota_DB_used"]):
        raise ValueError("Original research reservation/result scope changed")
    raw = (HERE / metadata["body_path"]).read_bytes()
    expected_size, expected_sha = PINS[sequence]
    if (len(raw) != expected_size or len(raw) != metadata["body_bytes"]
            or hashlib.sha256(raw).hexdigest() != expected_sha or metadata["body_sha256"] != expected_sha
            or len(raw) > 4_000_000 or _has_sensitive_body(raw)):
        raise ValueError("Exact whole original source pin differs")
    if not datetime.fromisoformat(metadata["reserved_at"]) <= datetime.fromisoformat(metadata["requested_at"]) <= datetime.fromisoformat(metadata["observed_at"]):
        raise ValueError("Original physical source clock conflict")
    return metadata, raw


def build_inputs(sequence):
    issuer, page = capture(1), capture(sequence)
    common = {"application_type": "BLA", "application_number": "761081", "issuer_cik": "0000078003", "issuer_ticker": "PFE"}
    selected = ({"source_row_index": 0, "product_number": None, "proprietary_name": "TRAZIMERA", "proper_name": "TRASTUZUMAB-QYYP",
                 "strength": "420MG", "dosage_form": None, "route": None, "dosage_form_route_text": "INJECTABLE;INJECTION", "source_party_name": "PFIZER INC"}
                if sequence == 2 else
                {"source_row_index": 0, "product_number": "001", "proprietary_name": "Trazimera", "proper_name": "trastuzumab-qyyp",
                 "strength": "420MG", "dosage_form": "For Injection", "route": "Intravenous", "dosage_form_route_text": None,
                 "source_party_name": "Pfizer Ireland Pharmaceuticals Unlimited Company"})
    request = {"scope": common, "source_kind": "DRUGSFDA_APPLICATION_PAGE" if sequence == 2 else "PURPLE_BOOK_PRODUCT_DETAIL",
               "source_key": "page", "issuer_key": "issuer", "source_version_sha256": page[0]["body_sha256"], "product_selection": selected}
    captures = {"page": page, "issuer": issuer}
    descriptors = {key: {"metadata": metadata, "body_available": True, "body_ref": {
        "path": (HERE / metadata["body_path"]).relative_to(ROOT).as_posix(), "sha256": metadata["body_sha256"], "bytes": len(raw)}}
        for key, (metadata, raw) in captures.items()}
    plan = {"schema_version": "captured_identity_review_plan_v1", "kind": "FDA_APPLICATION_PRODUCT", "request": request, "captures": descriptors}
    return request, captures, plan


def replay():
    if len(load_canonical_tickers()) != 687 or "PFE" not in load_canonical_tickers():
        raise ValueError("Canonical input scope changed")
    cases = []
    for sequence in (2, 3):
        request, captures, plan = build_inputs(sequence)
        result = review_fda_application_product(request, captures)
        if result["applicant_product_candidate"] is not None or result["new_verified_identities"] != 0:
            raise ValueError("Actual source gap was incorrectly promoted to issuer/product acceptance")
        cases.append({"sequence": sequence, "request": request, "result": result})
        name = "drugsfda_application_review_plan.json" if sequence == 2 else "purple_book_review_plan.json"
        (HERE / name).write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {"schema_version": 1, "recorded_at": datetime.now(timezone.utc).isoformat(), "cases": cases,
              "actual_research_transports": 3, "complete_original_HTTP200_bodies": 3, "complete_original_bytes": sum(v[0] for v in PINS.values()),
              "original_static_FDA_pages": 2, "earlier_independent_SEC_originals": 1, "authorized_maximum_transports": 6,
              "unused_transport_slots": 3, "FDA_CMS_Clinical_API_collector_requests": 0, "quota_database_opened_or_reset": False,
              "whole_withheld_bodies_read_or_restored": 0, "original_captures_rewritten": False,
              "DrugsFDA_product_rows": len(cases[0]["result"]["product_rows"]), "Purple_Book_product_rows": len(cases[1]["result"]["product_rows"]),
              "datasets_merged_or_product_numbers_inferred": False, "current_issuer_applicant_bridge_candidates": 0,
              "registry_adoptions": 0, "new_verified_instruments": 0, "current_Q3_instrument_coverage": 0,
              "canonical_ticker_count": 687, "full_specialist_acceptance": "0/21", "score_model_trading_changes": False,
              "live_ownership_or_historical_OOS_acceptance": False, "research_is_collector_live_acceptance": False}
    (HERE / "source_review.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("actual_research_transports", "DrugsFDA_product_rows", "Purple_Book_product_rows", "registry_adoptions")}))


if __name__ == "__main__":
    replay()
