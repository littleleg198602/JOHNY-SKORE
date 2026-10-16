"""Bounded offline inventory/replay of explicit existing original sources.

No capture is fetched, rewritten, projected into a fake FDA response or given
a new historical clock. This file writes only new batch8 review artifacts.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from evidence.batch3_healthcare_20261012.replay import capture
from market_checker_app.services.fda_structured_product_archive import (
    FdaProductArchiveCapture, FdaStructuredProductSelection, review_fda_structured_product_archive,
)
from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body
from market_checker_app.utils.ticker_universe import CANONICAL_CSV_SHA256, load_canonical_tickers


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PRIOR = ROOT / "evidence/batch3_healthcare_20261012"
BASE = "e252ec62796b0b02e733c55ebef713e0f29d4b44"
FDA_CASES = (
    ("fda_crl_pfizer_exact_phrase", "BLA", "761081", "Pfizer Inc.", "PFE", "0000078003"),
    ("fda_crl_merck_exact_phrase", "NDA", "204569", "Merck & Co., Inc.", "MRK", "0000310158"),
    ("fda_crl_abbvie_exact_phrase", "NDA", "216962", "AbbVie Inc.", "ABBV", "0001551152"),
)
SEC_KEYS = ("sec_pfe_2017_10k", "sec_mrk_2012_10k", "sec_abbv_2023_10q",
            "sec_abbv_2024_10q", "sec_abbv_2026_10q")
REGISTRY_PATHS = tuple("market_checker_app/data/" + name for name in (
    "verified_usaspending_uei.json", "verified_healthcare_identities.json", "verified_13f_securities.json",
    "verified_nhtsa_models.json", "verified_fdic_banks.json", "verified_counterparty_identities.json",
    "european_authority_registry.json", "market_checker_687_tickers.csv"))


def sha(body):
    return hashlib.sha256(body).hexdigest()


def write(name, value):
    (HERE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def pin(key, meta, raw, *, metadata_path, archive_path):
    if len(raw) > 4_000_000 or _has_sensitive_body(raw):
        raise ValueError("Original source unavailable; do not substitute a rewritten body")
    return {"capture_key": key, "metadata_path": metadata_path.relative_to(ROOT).as_posix(),
            "metadata_sha256": sha(metadata_path.read_bytes()),
            "archive_path": archive_path.relative_to(ROOT).as_posix(),
            "archive_sha256": sha(archive_path.read_bytes()), "original_body_sha256": sha(raw),
            "original_body_bytes": len(raw), "source_url": meta["response_url"],
            "requested_at": meta["requested_at"], "observed_at": meta["observed_at"],
            "source_first_historical_availability_proven": False, "new_source_request": False}


def capture_descriptor(metadata, path):
    raw = (ROOT / path).read_bytes()
    if sha(raw) != metadata["body_sha256"] or len(raw) != metadata["body_bytes"] or _has_sensitive_body(raw):
        raise ValueError("Exact retained raw pin unavailable")
    return {"metadata": metadata, "body_available": True,
            "body_ref": {"path": path, "sha256": sha(raw), "bytes": len(raw)}}


def replay(*, reviewed_at):
    if not isinstance(reviewed_at, datetime) or reviewed_at.utcoffset() is None:
        raise ValueError("Actual physical aware review time required")
    if len(load_canonical_tickers()) != 687:
        raise ValueError("Canonical scope changed")
    registry_hashes = {path: sha((ROOT / path).read_bytes()) for path in REGISTRY_PATHS}
    inventory, cases, event_count, apps = [], [], 0, set()
    for key, kind, number, name, ticker, cik in FDA_CASES:
        meta, raw = capture(key, PRIOR)
        entry = pin(key, meta, raw, metadata_path=PRIOR / "captures" / f"{key}.metadata.json",
                    archive_path=PRIOR / "captures" / meta["body_path"])
        # Dataset diagnostics only. These are CRL objects, never imported as
        # structured product/applicant records by the production adapter.
        source = json.loads(raw)
        rows = source["results"]
        event_count += len(rows)
        for row in rows:
            apps.update(row["application_number"])
        entry.update(source_dataset="FDA_TRANSPARENCY_CRL", original_event_rows=len(rows),
                     structured_products_arrays=sum(isinstance(row.get("products"), list) for row in rows),
                     structured_sponsor_fields=sum("sponsor_name" in row for row in rows),
                     crl_publisher_version=source["meta"]["last_updated"],
                     crl_version_is_drugsfda_version=False)
        inventory.append(entry)
        selection = FdaStructuredProductSelection(kind, number, name, ticker, cik)
        result = review_fda_structured_product_archive(selection=selection,
                    structured_capture=FdaProductArchiveCapture(meta, raw), issuer_capture=None, reviewed_at=reviewed_at)
        if result["structured_rows"] or result["applicant_product_candidate"] is not None:
            raise ValueError("CRL text was incorrectly promoted to structured products")
        cases.append({"capture_key": key, "selection": asdict(selection), "result": result})
    for key in SEC_KEYS:
        meta, raw = capture(key, PRIOR)
        entry = pin(key, meta, raw, metadata_path=PRIOR / "captures" / f"{key}.metadata.json",
                    archive_path=PRIOR / "captures" / meta["body_path"])
        entry.update(source_dataset="SEC_ISSUER_PERIODIC_FILING", structured_fda_product_source=False,
                     same_day_direct_submissions_bridge=False,
                     historical_period_is_original_source_availability=False)
        inventory.append(entry)
    owner_dir = ROOT / "evidence/batch7_healthcare_20261015"
    owner_path = owner_dir / "sec_pfe_2017_ex21.metadata.json"
    owner_meta = json.loads(owner_path.read_bytes())
    if owner_meta.get("raw_publication_permitted") is not True or owner_meta.get("status") != "HTTP_200_COMPLETE":
        raise ValueError("Previously safe original ownership source is now unavailable")
    owner_raw = (owner_dir / "sec_pfe_2017_ex21.body.html").read_bytes()
    if len(owner_raw) != owner_meta["body_bytes"] or sha(owner_raw) != owner_meta["body_sha256"]:
        raise ValueError("Original ownership byte pin changed")
    owner_entry = pin("sec_pfe_2017_ex21", owner_meta, owner_raw, metadata_path=owner_path,
                      archive_path=owner_dir / "sec_pfe_2017_ex21.body.html")
    owner_entry.update(source_dataset="SEC_EXHIBIT_21", structured_fda_product_source=False,
                       known_after_all_original_crl_observations=True,
                       product_application_assignment_proven=False,
                       today_capture_is_historical_knowledge=False)
    inventory.append(owner_entry)
    pfe_meta, _ = capture("fda_crl_pfizer_exact_phrase", PRIOR)
    issuer_meta, _ = capture("sec_pfe_2017_10k", PRIOR)
    issuer = capture_descriptor(issuer_meta, "evidence/batch7_healthcare_20261015/retained_sec_pfe_2017_10k.raw.html")
    selection = asdict(FdaStructuredProductSelection("BLA", "761081", "Pfizer Inc.", "PFE", "0000078003"))
    plan = {"schema_version": "captured_identity_review_plan_v1", "kind": "FDA_STRUCTURED_PRODUCT",
            "request": {"selection": selection}, "captures": {
                "structured": {"metadata": {"capture_form": "UNAVAILABLE_ORIGINAL_STRUCTURED_SOURCE",
                               "retention": "MISSING_BODY", "status": "WAIT_DATA",
                               "raw_publication_permitted": False,
                               "reason": "No retained original DrugsFDA structured application/product response in reviewed corpus"},
                               "body_available": False, "body_ref": None}, "issuer": issuer}}
    diagnostic = json.loads(json.dumps(plan))
    diagnostic["captures"]["structured"] = capture_descriptor(pfe_meta,
        "evidence/batch3_healthcare_20261012/captures/fda_crl_pfizer_exact_phrase.body.json")
    result = review_fda_structured_product_archive(selection=FdaStructuredProductSelection(**selection),
        structured_capture=FdaProductArchiveCapture(plan["captures"]["structured"]["metadata"], None),
        issuer_capture=FdaProductArchiveCapture(issuer["metadata"], (ROOT / issuer["body_ref"]["path"]).read_bytes()),
        reviewed_at=reviewed_at)
    report = {"schema_version": 1, "base_commit": BASE, "reviewed_at": reviewed_at.isoformat(),
              "ingested_at": reviewed_at.isoformat(), "scope": "Only explicit retained three-issuer five-event/four-application corpus",
              "original_captures_checked": len(inventory), "original_crl_bodies": 3,
              "original_crl_events": event_count, "original_distinct_applications": len(apps),
              "original_distinct_application_keys": sorted(apps), "structured_originals_available": 0,
              "structured_product_rows_imported": 0, "candidate_records": [], "adopted_records": 0,
              "verified_instruments_added": 0, "relevant_current_structured_issuer_coverage": 0,
              "canonical_ticker_count": 687, "canonical_csv_sha256": CANONICAL_CSV_SHA256,
              "status": "PARTIAL", "data_status": "WAIT_DATA", "full_specialist_acceptance": "0/21",
              "actual_new_http_requests": 0, "collector_http_requests": 0, "quota_database_opened": False,
              "http_count_scope": "Source-data capture/collector transports; read-only format-documentation web lookup is not a data capture",
              "format_documentation_read_only": True, "new_data_endpoint_requested": False,
              "known_withheld_bodies_read": 0, "source_archive_rewritten": False,
              "production_registries_written": False, "score_changes": False, "oos_evidence": False,
              "positive_contract_examples_are_synthetic_tests_only": True,
              "existing_registry_and_universe_hashes": registry_hashes,
              "blockers": ["Original FDA DrugsFDA structured application/product rows are absent; CRL text is a different dataset",
                           "No exact product number, dosage form or full active ingredient/strength selection may be inferred from CRL mentions",
                           "Issuer periodic filings and Exhibit21 are not direct same-day earlier SEC submissions sponsor-name snapshots",
                           "Original durable collector history is unproven; no new FDA/CMS/Clinical request or reconstructed quota store",
                           "Applicant/sponsor role never establishes legal product owner, parent, IP or historical interval"],
              "inventory": inventory, "actual_wrong_dataset_reviews": cases, "actual_missing_source_review": result}
    if event_count != 5 or len(apps) != 4:
        raise ValueError("Original case corpus changed")
    for path, digest in registry_hashes.items():
        if sha((ROOT / path).read_bytes()) != digest:
            raise ValueError("Registry or universe changed during offline replay")
    return report, plan, diagnostic


if __name__ == "__main__":
    report, plan, diagnostic = replay(reviewed_at=datetime.now(timezone.utc))
    write("source_review.json", report)
    write("retained_pfe_review_plan.json", plan)
    write("retained_crl_diagnostic_plan.json", diagnostic)
    print(json.dumps({"status": report["status"], "data_status": report["data_status"],
                      "original_captures_checked": report["original_captures_checked"],
                      "crl_events": report["original_crl_events"],
                      "distinct_applications": report["original_distinct_applications"],
                      "structured_products": report["structured_product_rows_imported"],
                      "new_requests": report["actual_new_http_requests"]}))
