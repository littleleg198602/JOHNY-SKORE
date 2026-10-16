"""Reproduce the strict two-scope HCA rejection without any publisher request."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from market_checker_app.services.cms_provider_ownership_review import (
    CapturedSource, CmsProviderOwnerScope, review_cms_provider_ownership,
)
from market_checker_app.utils.ticker_universe import CANONICAL_CSV_SHA256, load_canonical_tickers


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path(__file__).resolve().parent
PROJECTED = ROOT / "tests/fixtures/healthcare/hca_cms_captured_20261011.json"
PROJECTED_SHA256 = "5645620e60368e6f76bec5c13f84c7a5f88e4ca782c7f247b898b8fe25f9d3a2"
REGISTRY = ROOT / "market_checker_app/data/verified_healthcare_identities.json"


def build_review_report(*, ingested_at: datetime) -> dict:
    if not isinstance(ingested_at, datetime) or ingested_at.utcoffset() is None:
        raise ValueError("CMS review requires the actual aware ingestion/knowledge clock")
    clock = ingested_at.astimezone(timezone.utc)
    tickers = load_canonical_tickers()
    if "HCA" not in tickers:
        raise ValueError("The exact HCA review issuer is absent from the unchanged canonical universe")
    projection_bytes = PROJECTED.read_bytes()
    if hashlib.sha256(projection_bytes).hexdigest() != PROJECTED_SHA256:
        raise ValueError("Retained projected CMS fixture changed; original hash is required")
    legacy_manifest_path = ROOT / "evidence/parallel_healthcare_20261011.json"
    legacy_manifest = json.loads(legacy_manifest_path.read_bytes())
    if legacy_manifest["saved_primary_evidence"]["fixture_form"] != (
            "Projected publisher fields rather than complete primary HTTP responses or SEC filing copies."):
        raise ValueError("Original projection qualification changed")
    projection_meta = {"capture_form": "PROJECTED_PUBLISHER_FIELDS", "raw_publication_permitted": True,
                       "body_bytes": len(projection_bytes), "body_sha256": PROJECTED_SHA256}
    sec_meta_path = EVIDENCE / "sec_hca_submissions.metadata.json"
    sec_meta = json.loads(sec_meta_path.read_bytes())
    # Unavailable/withheld source metadata never enters a body parser or loader.
    sec_body = None
    if (sec_meta.get("raw_publication_permitted") is True
            and not str(sec_meta.get("status", "")).startswith("WITHHELD")):
        if sec_meta.get("body_path") != "sec_hca_submissions.body.json":
            raise ValueError("The original SEC archive path differs from this bounded review")
        sec_body = (EVIDENCE / sec_meta["body_path"]).read_bytes()
    issuer_capture = CapturedSource(sec_meta, sec_body)
    entries = []
    for row in json.loads(projection_bytes):
        scope = CmsProviderOwnerScope(row["ENROLLMENT ID"], row["ASSOCIATE ID - OWNER"], row["ROLE CODE - OWNER"])
        result = review_cms_provider_ownership(scope=scope,
            owner_capture=CapturedSource(projection_meta, projection_bytes), provider_capture=None,
            issuer_capture=issuer_capture, reviewed_at=clock)
        issuer_name = None
        if "issuer" in result["original_capture_pins"]:
            issuer_name = json.loads(sec_body)["name"]
        gaps = ["ORIGINAL_CMS_RESPONSE_AND_REQUEST_URL_NOT_RETAINED",
                "HOSPITAL_ENROLLMENTS_CCN_AND_PROVIDER_PAC_CAPTURE_MISSING",
                "OWNER_ROW_PROVIDER_PAC_NOT_PRESERVED",
                "CMS_PUBLISHER_DATASET_VERSION_AND_PUBLICATION_CLOCK_NOT_CAPTURED"]
        if issuer_name is not None and issuer_name.strip().casefold() != row["ORGANIZATION NAME - OWNER"].strip().casefold():
            gaps.append("EXACT_LEGAL_OWNER_ISSUER_NAME_BRIDGE_MISSING")
        if "issuer" in result["original_capture_pins"]:
            gaps.append("CURRENT_SEC_CAPTURE_CANNOT_BACKDATE_LEGACY_CMS_OBSERVATION")
        entries.append({"exact_legacy_scope": {"enrollment_id": scope.enrollment_id,
                                                "owner_associate_id": scope.owner_associate_id,
                                                "owner_role_code": scope.owner_role_code,
                                                "provider_name": row["ORGANIZATION NAME"],
                                                "legal_owner_name": row["ORGANIZATION NAME - OWNER"],
                                                "ccn": None, "provider_associate_id": None},
                        "source_association_date": row["ASSOCIATION DATE - OWNER"],
                        "source_association_date_is_publication_or_knowledge_time": False,
                        "legacy_capture_known_at_claim": legacy_manifest["saved_primary_evidence"]["capture_known_at"],
                        "legacy_original_http_capture_reproducible": False,
                        "original_projected_bytes_sha256": PROJECTED_SHA256,
                        "projected_byte_hash_is_original_http_body_hash": False,
                        "current_independent_sec_name": issuer_name, "punctuation_or_brand_alias_inferred": False,
                        "current_ingested_at": clock.isoformat(), "review": result,
                        "additional_exact_source_gaps": gaps})
    records = [entry["review"]["proposed_record"] for entry in entries
               if entry["review"]["proposed_record"] is not None]
    return {"schema_version": 1, "baseline_commit": "cbc6c4a3f023f4f32778456ae4f73d6d2c91fcc6",
            "ingested_at": clock.isoformat(), "reviewed_at": clock.isoformat(),
            "review_scope": "TWO_RETAINED_PROJECTED_HCA_OWNER_ENROLLMENTS; NO_CCN_OR_GROUP_INFERENCE",
            "projected_fixture": {"path": PROJECTED.relative_to(ROOT).as_posix(),
                                  "sha256": PROJECTED_SHA256, "bytes": len(projection_bytes),
                                  "capture_form": "PROJECTED_PUBLISHER_FIELDS", "unchanged": True},
            "legacy_manifest": {"path": legacy_manifest_path.relative_to(ROOT).as_posix(),
                                "sha256": hashlib.sha256(legacy_manifest_path.read_bytes()).hexdigest(), "unchanged": True},
            "independent_sec_capture": {"metadata_path": sec_meta_path.relative_to(ROOT).as_posix(),
                                        "metadata_sha256": hashlib.sha256(sec_meta_path.read_bytes()).hexdigest(),
                                        "source_url": sec_meta["response_url"], "requested_at": sec_meta["requested_at"],
                                        "observed_at": sec_meta["observed_at"], "http_status": sec_meta["http_status"],
                                        "status": sec_meta["status"], "body_bytes": sec_meta.get("body_bytes"),
                                        "body_sha256": sec_meta.get("body_sha256"),
                                        "body_path": (EVIDENCE / sec_meta["body_path"]).relative_to(ROOT).as_posix()
                                                     if sec_body is not None else None},
            "cases": entries, "proposed_records": records, "proposal_count": len(records),
            "production_healthcare_registry_sha256": hashlib.sha256(REGISTRY.read_bytes()).hexdigest(),
            "production_registry_modified": False, "canonical_subject_count": len(tickers),
            "canonical_csv_sha256": CANONICAL_CSV_SHA256,
            "source_http_requests_in_replay": 0, "new_cms_or_clinical_http_requests": 0,
            "http_count_scope": "Explicit collector API operations; read-only public schema/catalog discovery is recorded separately",
            "bounded_sec_research_requests": 1, "authorized_primary_capture_cap": 4,
            "source_history_status": "WAIT_QUOTA_HISTORY",
            "quota_gap": "Current-day original CMS quota history was not proven; no CMS call, store initialization, copy or reset was made.",
            "new_quota_store_created": False, "actual_complete_cms_positive_obtained": False,
            "actual_complete_cms_negative_obtained": False, "live_acceptance_verified": False,
            "historical_ownership_attribution_allowed": False, "issuer_wide_scope": False,
            "score_changes": False, "out_of_sample_verified": False, "actual_user_windows_verified": False,
            "remaining_work": ["Recover the original CMS durable request history before a live CMS operation",
                               "Complete exact-enrollment Hospital Enrollments and All Owners original responses",
                               "Independent exact legal-owner issuer bridge available in that captured cohort",
                               "Broader dated provider/product exposure denominators, genuine complete negatives and point-in-time history"]}


def main():
    path = EVIDENCE / "source_review.json"
    clock = (datetime.fromisoformat(json.loads(path.read_bytes())["ingested_at"])
             if path.exists() else datetime.now(timezone.utc))
    report = build_review_report(ingested_at=clock)
    path.write_text(json.dumps(report, indent=2) + "\n")
    (EVIDENCE / "identity_proposal.json").write_text(json.dumps({
        "schema_version": 1, "ingested_at": report["ingested_at"], "record_count": report["proposal_count"],
        "records": report["proposed_records"], "review_status": "WITHHELD_ORIGINAL_CMS_CCN_AND_EXACT_DATED_OWNER_BRIDGE_MISSING",
        "production_registry_modified": False, "positive_unit_fixture_is_live_evidence": False,
    }, indent=2) + "\n")
    print(json.dumps({"proposal_count": report["proposal_count"], "source_http_requests_in_replay": 0,
                      "cases": [{"enrollment_id": row["exact_legacy_scope"]["enrollment_id"],
                                 "decision": row["review"]["decision"], "reasons": row["review"]["reasons"],
                                 "additional_gaps": row["additional_exact_source_gaps"]} for row in report["cases"]]}, indent=2))


if __name__ == "__main__":
    main()
