"""Reproduce actual original-byte FDA product/application scope reviews, no HTTP."""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from evidence.batch3_healthcare_20261012.replay import capture
from market_checker_app.services.healthcare_product_scope_review import (
    CapturedHealthcareSource, FdaProductApplicationScope, _OwnershipTables, review_healthcare_product_scope,
)
from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body
from market_checker_app.utils.ticker_universe import CANONICAL_CSV_SHA256, load_canonical_tickers


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path(__file__).resolve().parent
PRIOR = ROOT / "evidence/batch3_healthcare_20261012"
CASES = (
    ("fda_crl_pfizer_exact_phrase", "sec_pfe_2017_10k", "PFE", "0000078003", "BLA761081", "2018-04-20", "Pfizer Inc.", ("trastuzumab",), 0),
    ("fda_crl_merck_exact_phrase", "sec_mrk_2012_10k", "MRK", "0000310158", "NDA204569", "2013-06-28", "Merck & Co., Inc.", ("suvorexant",), 0),
    ("fda_crl_abbvie_exact_phrase", "sec_abbv_2023_10q", "ABBV", "0001551152", "NDA216962", "2023-03-17", "AbbVie Inc.", ("foscarbidopa", "foslevodopa"), 0),
    ("fda_crl_abbvie_exact_phrase", "sec_abbv_2024_10q", "ABBV", "0001551152", "NDA216962", "2024-06-18", "AbbVie Inc.", ("foscarbidopa", "foslevodopa"), 1),
    ("fda_crl_abbvie_exact_phrase", "sec_abbv_2026_10q", "ABBV", "0001551152", "BLA761459", "2026-04-22", "AbbVie Inc.", (), 2),
)


def _pin(key, metadata, raw):
    metadata_path = PRIOR / "captures" / f"{key}.metadata.json"
    archive_path = metadata_path.parent / metadata["body_path"]
    return {"capture_key": key, "metadata_path": metadata_path.relative_to(ROOT).as_posix(),
            "metadata_sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
            "archive_path": archive_path.relative_to(ROOT).as_posix(),
            "archive_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            "original_body_sha256": hashlib.sha256(raw).hexdigest(), "original_body_bytes": len(raw),
            "source_url": metadata["response_url"], "requested_at": metadata["requested_at"],
            "observed_at": metadata["observed_at"], "original_metadata_unchanged": True,
            "archived_byte_hash_is_http_body_hash": metadata["storage_encoding"] == "identity"}


def build_review_report(*, ingested_at: datetime) -> dict:
    if not isinstance(ingested_at, datetime) or ingested_at.utcoffset() is None:
        raise ValueError("Actual aware ingestion clock required")
    clock = ingested_at.astimezone(timezone.utc)
    tickers = load_canonical_tickers()
    if not all(ticker in tickers for ticker in ("PFE", "MRK", "ABBV")):
        raise ValueError("Exact reviewed issuers must belong to unchanged canonical 687")
    owner_metadata_path = EVIDENCE / "sec_pfe_2017_ex21.metadata.json"
    owner_meta = json.loads(owner_metadata_path.read_bytes())
    owner_raw = None
    if (owner_meta.get("raw_publication_permitted") is True
            and not str(owner_meta.get("status", "")).startswith("WITHHELD")):
        if owner_meta["body_path"] != "sec_pfe_2017_ex21.body.html":
            raise ValueError("The bounded original ownership body path changed")
        owner_raw = (EVIDENCE / owner_meta["body_path"]).read_bytes()
    owner = CapturedHealthcareSource(owner_meta, owner_raw)
    owner_review = {"capture_status": owner_meta["status"], "http_status": owner_meta["http_status"],
                    "observed_at": owner_meta["observed_at"], "source_url": owner_meta["response_url"],
                    "body_sha256": owner_meta.get("body_sha256"), "body_bytes": owner_meta.get("body_bytes"),
                    "metadata_sha256": hashlib.sha256(owner_metadata_path.read_bytes()).hexdigest(),
                    "parent_ownership_point_date_as_displayed": None, "parent_title_as_displayed": None,
                    "exact_fda_addressee_subsidiary_rows": None, "product_application_assignment_available": False,
                    "original_source_http_time_is_historical_publication_time": False}
    if (owner_raw is not None and not _has_sensitive_body(owner_raw)
            and hashlib.sha256(owner_raw).hexdigest() == owner_meta["body_sha256"]
            and len(owner_raw) == owner_meta["body_bytes"] and owner_meta["http_status"] == 200):
        parser = _OwnershipTables()
        parser.feed(owner_raw.decode("utf-8"))
        text = " ".join(" ".join(parser.text).split())
        if "subsidiaries of the Company as of December 31, 2017" not in text:
            raise ValueError("Original Exhibit21 date/title qualification differs")
        owner_review.update(parent_ownership_point_date_as_displayed="2017-12-31",
                            parent_title_as_displayed="SUBSIDIARIES OF THE COMPANY",
                            exact_fda_addressee_subsidiary_rows=sum(1 for table in parser.tables for row in table["rows"]
                                                                   if row and row[0] == "Pfizer Inc."),
                            table_count=len(parser.tables),
                            candidate_row_count=sum(len(table["rows"]) for table in parser.tables),
                            date_is_point_observation_not_continuous_ownership_interval=True)
    entries, pins = [], {}
    for fda_key, issuer_key, ticker, cik, application, event_day, company, markers, index in CASES:
        fda_meta, fda_raw = capture(fda_key, PRIOR)
        issuer_meta, issuer_raw = capture(issuer_key, PRIOR)
        row = json.loads(fda_raw)["results"][index]
        scope = FdaProductApplicationScope(ticker, cik, application, row["file_name"], event_day, company, markers)
        result = review_healthcare_product_scope(scope=scope, fda_capture=CapturedHealthcareSource(fda_meta, fda_raw),
            ownership_capture=owner if ticker == "PFE" else None,
            issuer_capture=CapturedHealthcareSource(issuer_meta, issuer_raw), ownership_selection=None,
            reviewed_at=clock)
        for key, meta, raw in ((fda_key, fda_meta, fda_raw), (issuer_key, issuer_meta, issuer_raw)):
            pins[key] = _pin(key, meta, raw)
        entries.append({"scope": {**asdict(scope), "product_markers": list(scope.product_markers)},
                        "fda_capture_key": fda_key, "issuer_capture_key": issuer_key,
                        "ownership_capture_key": "sec_pfe_2017_ex21" if ticker == "PFE" else None,
                        "ownership_selection": None, "review": result,
                        "limitations": ["FDA company field is a CRL addressee, not a legal product/application assignment",
                                        "Product names in letter text are scoped source mentions, not ownership or license records",
                                        "Existing main10K/10Q captures identify issuers only; no dated application-owner bridge is inferred",
                                        "No exact independently dated legal-owner row available before the original FDA observation"]})
    registry = ROOT / "market_checker_app/data/verified_healthcare_identities.json"
    return {"schema_version": 1, "baseline_commit": "089ce4dbe03952d84daac5cfe47760b09020954b",
            "reviewed_at": clock.isoformat(), "ingested_at": clock.isoformat(),
            "canonical_subject_count": len(tickers), "canonical_csv_sha256": CANONICAL_CSV_SHA256,
            "original_prior_captures": list(pins.values()), "independent_ownership_capture_review": owner_review,
            "cases": entries, "proposed_records": [], "proposal_count": 0,
            "coverage": {"selected_original_fda_query_bodies": 3, "selected_issuers": 3, "selected_letter_events": 5,
                         "distinct_applications": 4, "source_object_facts_preserved": sum(x["review"]["source_object_facts"] is not None for x in entries),
                         "events_with_product_text_markers": sum(bool(x["scope"]["product_markers"]) for x in entries),
                         "structured_product_identity_events": 0, "verified_legal_owner_event_links": 0,
                         "product_or_license_assignments_verified": 0, "issuer_group_coverage_verified": 0,
                         "current_live_fda_query_coverage_verified": 0, "coverage_is_selected_archive_only": True},
            "production_healthcare_registry_sha256": hashlib.sha256(registry.read_bytes()).hexdigest(),
            "production_registry_modified": False, "new_verified_identity_count": 0,
            "bounded_sec_research_requests": 1, "authorized_sec_research_attempt_cap": 4,
            "new_fda_cms_or_clinical_collector_api_requests": 0, "source_http_requests_in_replay": 0,
            "source_history_status": "WAIT_QUOTA_HISTORY", "new_or_reconstructed_quota_database": False,
            "exact_original_byte_rearchive": {"path": (EVIDENCE / "retained_sec_pfe_2017_10k.raw.html").relative_to(ROOT).as_posix(),
                                              "sha256": pins["sec_pfe_2017_10k"]["original_body_sha256"],
                                              "bytes": pins["sec_pfe_2017_10k"]["original_body_bytes"],
                                              "new_capture": False, "original_observed_at_unchanged": True},
            "score_changes": False, "product_ownership_verified": False, "issuer_attribution_allowed": False,
            "historical_prediction_knowledge_available": False, "out_of_sample_verified": False,
            "actual_user_windows_verified": False, "full_specialist_acceptance": False,
            "remaining_work": ["Original durable FDA/CMS/Clinical request history before any corresponding LIVE operation",
                               "Exact independent legal/application/product ownership evidence that was available before the original object",
                               "Point-date ownership/issuer identities rather than extending annual-period subsidiary names",
                               "Actual broader product/issuer groups, complete negatives and historical source/price evaluation"]}


def main():
    path = EVIDENCE / "source_review.json"
    clock = datetime.fromisoformat(json.loads(path.read_bytes())["ingested_at"]) if path.exists() else datetime.now(timezone.utc)
    report = build_review_report(ingested_at=clock)
    path.write_text(json.dumps(report, indent=2) + "\n")
    (EVIDENCE / "identity_proposal.json").write_text(json.dumps({
        "schema_version": 1, "known_at": report["ingested_at"], "record_count": 0, "records": [],
        "review_status": "WITHHELD_DATED_EXACT_APPLICATION_OR_PRODUCT_LEGAL_OWNERSHIP_NOT_PROVEN",
        "production_registry_modified": False, "synthetic_unit_candidate_is_live_acceptance": False,
    }, indent=2) + "\n")
    scope = report["cases"][0]["scope"]
    fda_meta, fda_raw = capture("fda_crl_pfizer_exact_phrase", PRIOR)
    issuer_meta, issuer_raw = capture("sec_pfe_2017_10k", PRIOR)
    owner_meta = json.loads((EVIDENCE / "sec_pfe_2017_ex21.metadata.json").read_bytes())
    def descriptor(meta, path, body):
        return {"metadata": meta, "body_available": body is not None,
                "body_ref": {"path": path, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)} if body is not None else None}
    owner_path = EVIDENCE / owner_meta["body_path"] if owner_meta.get("body_path") else None
    owner_raw = owner_path.read_bytes() if owner_path else None
    plan = {"schema_version": "captured_identity_review_plan_v1", "kind": "HEALTHCARE_PRODUCT_SCOPE",
            "request": {"scope": scope, "ownership_selection": None}, "captures": {
                "fda": descriptor(fda_meta, "evidence/batch3_healthcare_20261012/captures/fda_crl_pfizer_exact_phrase.body.json", fda_raw),
                "ownership": descriptor(owner_meta, owner_path.relative_to(ROOT).as_posix() if owner_path else None, owner_raw),
                "issuer": descriptor(issuer_meta, "evidence/batch7_healthcare_20261015/retained_sec_pfe_2017_10k.raw.html", issuer_raw)}}
    (EVIDENCE / "retained_pfe_review_plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    print(json.dumps({"coverage": report["coverage"], "proposal_count": 0, "source_http_requests_in_replay": 0,
                      "decisions": [{"scope": x["scope"]["application_number"], "letter_date": x["scope"]["letter_date"],
                                     "decision": x["review"]["decision"], "reasons": x["review"]["reasons"]} for x in report["cases"]]}, indent=2))


if __name__ == "__main__":
    main()
