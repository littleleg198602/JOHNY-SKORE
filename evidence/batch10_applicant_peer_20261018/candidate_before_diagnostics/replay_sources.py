"""Explicit retained-source replay; no request, quota, registry or DB access."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urljoin

from evidence.batch3_healthcare_20261012.replay import capture
from market_checker_app.collectors.fda_legal_applicant_bridge_review import review_fda_legal_applicant_bridge
from market_checker_app.services.fda_application_product_review import _Page
from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OLD = ROOT / "evidence/batch9_healthcare_20261016"
EXHIBIT_URL = "https://www.sec.gov/Archives/edgar/data/78003/000007800318000027/pfe-exhibit21x12312017.htm"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def descriptor(meta, ref):
    raw = (ROOT / ref).read_bytes()
    if (len(raw) != meta["body_bytes"] or sha(raw) != meta["body_sha256"] or len(raw) > 4_000_000
            or _has_sensitive_body(raw) or meta.get("raw_publication_permitted") is False):
        raise ValueError("Exact safe original body unavailable")
    return {"metadata": meta, "body_available": True, "body_ref": {"path": ref, "sha256": sha(raw), "bytes": len(raw)}}


def inputs():
    filing_meta, filing_raw = capture("sec_pfe_2017_10k")
    if sha(filing_raw) != "77948deb20b5f70af5f9d789c92035085d16447d3b96b99269ab364c8ea1af3c":
        raise ValueError("Original legacy parent filing changed")
    exhibit_meta = json.loads((ROOT / "evidence/batch7_healthcare_20261015/sec_pfe_2017_ex21.metadata.json").read_bytes())
    if exhibit_meta["body_sha256"] != "61edde7790c9765d7108d7660eddd4a3ef8241abb1f855de42cbebf66beb831f":
        raise ValueError("Original already attempted EX21 pin changed")
    applicant_plan = json.loads((OLD / "purple_book_review_plan.json").read_bytes())
    company_plan = json.loads((OLD / "drugsfda_application_review_plan.json").read_bytes())
    app_request = deepcopy(applicant_plan["request"])
    company_request = deepcopy(company_plan["request"])
    app_request["source_key"] = "application"
    company_request["source_key"] = "company"
    descriptors = {
        "filing": descriptor(filing_meta, "evidence/batch7_healthcare_20261015/retained_sec_pfe_2017_10k.raw.html"),
        "exhibit": descriptor(exhibit_meta, "evidence/batch7_healthcare_20261015/sec_pfe_2017_ex21.body.html"),
        "application": applicant_plan["captures"]["page"], "company": company_plan["captures"]["page"],
        "issuer": applicant_plan["captures"]["issuer"],
    }
    captures = {}
    for key, item in descriptors.items():
        raw = (ROOT / item["body_ref"]["path"]).read_bytes()
        if sha(raw) != item["body_ref"]["sha256"] or len(raw) != item["body_ref"]["bytes"] or _has_sensitive_body(raw):
            raise ValueError("Original static role source pin changed")
        captures[key] = (item["metadata"], raw)
    request = {"scope": {"issuer_cik": "0000078003", "issuer_ticker": "PFE", "issuer_name": "PFIZER INC.",
        "application_type": "BLA", "application_number": "761081", "applicant_name": "Pfizer Ireland Pharmaceuticals Unlimited Company"},
        "filing_key": "filing", "exhibit_key": "exhibit", "roster_as_of": "2017-12-31",
        "selected_rows": [{"table_index": 5, "row_index": 6, "entity_name": "Pfizer Ireland Pharmaceuticals", "jurisdiction": "Ireland"}],
        "application_request": app_request, "company_request": company_request}
    plan = {"schema_version": "captured_identity_review_plan_v1", "kind": "FDA_LEGAL_APPLICANT_BRIDGE", "request": request, "captures": descriptors}
    return request, captures, plan


def route_audit(captures):
    meta, raw = captures["filing"]
    links = []
    for node in _Page(raw).nodes:
        if node.tag == "a" and node.text() in {"21", "21.1", "2.1"}:
            links.append({"literal_label": node.text(), "resolved_original_href": urljoin(meta["response_url"], node.attrs.get("href", "")),
                          "eligible_exhibit21_label": node.text() in {"21", "21.1"}})
    eligible = [entry for entry in links if entry["eligible_exhibit21_label"]]
    if len(eligible) != 1 or eligible[0]["resolved_original_href"] != EXHIBIT_URL:
        raise ValueError("Retained explicit linked route changed; no transport authorized by this replay")
    index_meta_path = ROOT / "evidence/batch3_healthcare_20261012/captures/sec_pfe_2017_index.metadata.json"
    index_meta = json.loads(index_meta_path.read_bytes())
    if index_meta["http_status"] != 403:
        raise ValueError("Original index failure changed")
    return {"retained_filing_links": links, "index_failure": {"metadata_path": index_meta_path.relative_to(ROOT).as_posix(),
              "metadata_sha256": sha(index_meta_path.read_bytes()), "http_status": 403,
              "request_url": index_meta["request_url"], "body_sha256": index_meta["body_sha256"],
              "failure_used_as_primary_index_proof": False},
            "only_explicit_EX21_route_already_attempted_and_captured": True,
            "prior_complete_EX21_observed_at": captures["exhibit"][0]["observed_at"],
            "new_SEC_research_GET_authorized_by_unattempted_link_condition": False,
            "new_source_requests": 0, "retry_redirect_fallback_or_alternate_route": False,
            "exhibit2_1_filename_is_not_exhibit21_label": True}


if __name__ == "__main__":
    request, captures, plan = inputs()
    audit = route_audit(captures)
    result = review_fda_legal_applicant_bridge(request, captures)
    report = {"schema_version": 1, "recorded_at": datetime.now(timezone.utc).isoformat(), "request": request,
              "route_audit": audit, "review": result, "actual_new_source_requests": 0,
              "registry_adoptions": 0, "healthcare_registry_count_unchanged": 10, "full_specialist_acceptance": "0/21",
              "historical_or_Windows_or_OOS_acceptance": False}
    for name, value in (("retained_review_plan.json", plan), ("source_review.json", report)):
        (HERE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "roster": result["roster_facts"],
                      "selected_rows": result["selected_roster_facts"], "gaps": result["gaps"], "new_HTTP": 0}))
