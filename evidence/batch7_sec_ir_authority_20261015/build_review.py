"""Pin retained safe SEC originals; replay an issuer-disclosed IR URI fact."""
from datetime import datetime
import hashlib
from html import unescape
import json
from pathlib import Path

from market_checker_app.services.sec_ir_disclosure_authority_review import (
    SECIRDisclosureCapture, review_sec_ir_disclosure_authority,
)


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
RETAINED = ROOT.parent / "batch3_sec_20261012"
ORIGINALS = {
    "primary/nvda_2026_10k.htm": (1967927, "0f874c4c57490fd24579499830a24b2853f3273c8dbfcc5ffd8e0563383fc6a5"),
    "primary/nvda_2026_index.html": (13551, "ba36b0aa8f66dd5e5a4de1b0ecc67335df014b0149a0350c9806cf9bdd366075"),
}
URL_LITERAL = "http://investor.nvidia.com"
QUOTE = ("A discussion regarding our financial condition and results of operations for fiscal year 2026 compared to fiscal year 2025 is presented below. "
    "A discussion regarding our financial condition and results of operations for fiscal year 2025 compared to fiscal year 2024 can be found under Item 7 "
    "in our Annual Report on Form 10-K for the fiscal year ended January 26, 2025, filed with the SEC on February 26, 2025, which is available free of charge "
    "on the SEC’s website at http://www.sec.gov and at our investor relations website, http://investor.nvidia.com.")


def load_inputs():
    manifest = json.loads((RETAINED / "captures.json").read_bytes())
    captures, receipts = {}, {}
    for row in manifest["captures"]:
        if row.get("path") not in ORIGINALS: continue
        expected_size, expected_hash = ORIGINALS[row["path"]]
        path = RETAINED / row["path"]
        if (row["size_bytes"] != expected_size or row["sha256"] != expected_hash
                or row["http_status"] != 200 or row["url"] != row["resolved_url"]
                or row["capture_kind"] != "RAW_HTTP_RESPONSE_BODY" or path.is_symlink()
                or path.stat().st_size != expected_size or expected_size > 4_000_000):
            raise ValueError("Retained whole original SEC IR-disclosure capture pin changed")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != expected_hash:
            raise ValueError("Retained SEC IR-disclosure whole original byte hash changed")
        key = "source" if row["path"].endswith("10k.htm") else "index"
        captures[key] = SECIRDisclosureCapture("actual-retained-nvda-2026-" + key,
            row["url"], body, expected_hash, expected_size, datetime.fromisoformat(row["observed_at"]))
        receipts[key] = dict(row)
    if set(captures) != {"source", "index"}:
        raise ValueError("Both complete original SEC filing and index must remain retained")
    body = captures["source"].body
    needle = b"A discussion regarding our financial condition and results of operations for fiscal year 2026 compared to fiscal year 2025 is presented below."
    if body.count(needle) != 1: raise ValueError("Pinned whole original IR context is ambiguous")
    start = body.index(needle); end = body.index(b"</span>", start)
    original = body[start:end]
    if " ".join(unescape(original.decode("utf-8")).split()) != QUOTE:
        raise ValueError("Pinned exact IR paragraph quote changed")
    selection = {"issuer_context_id": "c-1", "issuer_name": "NVIDIA CORP", "issuer_cik": "0001045810",
        "document_type": "10-K", "document_period_end": "2026-01-25", "ir_url_literal": URL_LITERAL,
        "ir_context_locator": "div:818", "ir_context_quote": QUOTE,
        "ir_context_span": {"byte_start": start, "byte_end": end, "sha256": hashlib.sha256(original).hexdigest()}}
    return captures["source"], captures["index"], selection, receipts


def build_review_artifact():
    source, index, selection, receipts = load_inputs()
    clock = max(capture.available_at for capture in (source, index))
    review = review_sec_ir_disclosure_authority(source_capture=source, index_capture=index,
                                               selection=selection, knowledge_at=clock)
    return {"schema_version": 1, "result": "INDEPENDENT_SEC_DISCLOSED_IR_WEBSITE_FACT_PUBLISHER_AUTHORITY_WITHHELD",
        "actual_new_http_requests": 0, "existing_http200_original_bodies_used": 2,
        "new_disclosures_collected": 0, "underlying_independent_sec_disclosures": 1,
        "old_originals_copied_modified_or_replaced": False, "whole_withheld_bodies_restored_or_substituted": False,
        "source_receipts_unchanged": receipts, "input_knowledge_cutoff": clock.isoformat(), "review": review,
        "root_must_record_physical_review_ingestion": True, "registry_or_feed_activation": False,
        "issuer_attribution_allowed": False, "scoring_applied": False, "full_specialist_acceptance": False,
        "historical_machine_availability_or_oos_proven": False, "windows_claim": False,
        "limits": ["The literal HTTP URI is an issuer-disclosed website annotation, not present publisher/domain authority",
            "The selected disclosure paragraph refers to prior fiscal reports without proving IR website validity on those report days",
            "Exact DEI context pairs NVIDIA CORP/0001045810; no hostname-only identity or cross-issuer attribution",
            "Index/body are one SEC disclosure; the index's 111-byte served-primary size conflict stays explicit",
            "No current publisher page, RSS feed, redirect chain, issuer continuity or website ownership was fetched or verified",
            "Previously withheld whole IR/NHTSA bodies remain absent and were not used to produce this fact",
            "Retained original October12 capture knowledge does not backdate this newly ingested component review"]}


def build_review_plan():
    source, index, selection, _ = load_inputs()
    captures = {}
    for key, capture, filename in (("source", source, "nvda_2026_10k.htm"), ("index", index, "nvda_2026_index.html")):
        captures[key] = {"metadata": capture.summary(), "body_available": True,
            "body_ref": {"path": "evidence/batch3_sec_20261012/primary/" + filename,
                         "sha256": capture.sha256, "bytes": capture.size_bytes}}
    return {"schema_version": "captured_identity_review_plan_v1", "kind": "SEC_IR_DISCLOSURE_AUTHORITY",
            "request": {"selection": selection}, "captures": captures}


if __name__ == "__main__":
    for name, result in (("ir_disclosure_review.json", build_review_artifact()), ("actual_review_plan.json", build_review_plan())):
        with (ROOT / name).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(name)
