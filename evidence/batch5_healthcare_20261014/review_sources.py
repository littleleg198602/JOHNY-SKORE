"""Read-only real-byte snapshot review: no HTTP, quota or registry mutation."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from evidence.batch4_healthcare_20261013.replay import capture
from market_checker_app.services.healthcare_identity_service import (
    DEFAULT_HEALTHCARE_IDENTITIES, build_clinical_trial_snapshot_record, clinical_trial_version,
)


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path(__file__).resolve().parent
SOURCE_KEYS = ("trial_surpass2_complete_record", "trial_summit_complete_record", "sec_lly_submissions")


def build_review_report(*, as_of: datetime) -> dict:
    if not isinstance(as_of, datetime) or as_of.utcoffset() is None:
        raise ValueError("Snapshot review requires its actual UTC knowledge clock")
    clock = as_of.astimezone(timezone.utc)
    issuer_meta, issuer_raw = capture("sec_lly_submissions", ROOT / "evidence/batch4_healthcare_20261013")
    issuer = json.loads(issuer_raw)
    captures, candidates, records = [], [], []
    for key in SOURCE_KEYS:
        meta, raw = capture(key, ROOT / "evidence/batch4_healthcare_20261013")
        meta_path = ROOT / "evidence/batch4_healthcare_20261013/captures" / f"{key}.metadata.json"
        captures.append({"capture_key": key,
                         "body_path": (meta_path.parent / meta["body_path"]).relative_to(ROOT).as_posix(),
                         "body_sha256": hashlib.sha256(raw).hexdigest(), "body_bytes": len(raw),
                         "metadata_path": meta_path.relative_to(ROOT).as_posix(),
                         "metadata_sha256": hashlib.sha256(meta_path.read_bytes()).hexdigest(),
                         "source_url": meta["response_url"], "requested_at": meta["requested_at"],
                         "observed_at": meta["observed_at"], "http_status": meta["http_status"]})
    for key in SOURCE_KEYS[:2]:
        meta, raw = capture(key, ROOT / "evidence/batch4_healthcare_20261013")
        payload = json.loads(raw)
        version = clinical_trial_version(payload)
        reasons = []
        try:
            record = build_clinical_trial_snapshot_record(
                ticker="LLY", cik="0000059478", intervention="Tirzepatide", role="LEAD_SPONSOR",
                trial_metadata=meta, trial_body=raw, issuer_metadata=issuer_meta,
                issuer_body=issuer_raw, reviewed_at=clock)
        except ValueError as error:
            reasons.append(str(error))
            record = None
        captured_day = datetime.fromisoformat(meta["observed_at"]).astimezone(timezone.utc).date()
        if datetime.fromisoformat(issuer_meta["observed_at"]) > datetime.fromisoformat(meta["observed_at"]):
            reasons.append("ISSUER_EVIDENCE_NOT_AVAILABLE_AT_SOURCE_OBSERVATION")
        if captured_day != clock.date():
            reasons.append("NO_CURRENT_OBSERVED_CLINICAL_PAGE")
        if record is not None:
            records.append(record)
        candidates.append({"nct_id": version["nct_id"], "source_capture_key": key,
                           "candidate_kind": "clinical_trial_sponsor_snapshot", "candidate_time_basis": "OBSERVED_SNAPSHOT",
                           "source_body_sha256": meta["body_sha256"], "source_observed_at": meta["observed_at"],
                           "publisher_posting_day": version["record_version_date"],
                           "publisher_posting_day_is_snapshot_or_knowledge_day": False,
                           "exact_source_sponsor_name": version["lead_sponsor"],
                           "independent_issuer_snapshot_name": issuer["name"],
                           "issuer_cik": str(issuer["cik"]).zfill(10), "issuer_tickers": issuer["tickers"],
                           "issuer_body_sha256": issuer_meta["body_sha256"],
                           "issuer_observed_at": issuer_meta["observed_at"],
                           "known_at": clock.isoformat(), "selected_intervention": "Tirzepatide",
                           "unattributed_interventions": [name for name in version["intervention_names"] if name != "Tirzepatide"],
                           "captured_arm_roles": [{"label": group["label"], "type": group["type"],
                                                   "intervention_names": group["interventionNames"]}
                                                  for group in payload["protocolSection"]["armsInterventionsModule"]["armGroups"]],
                           "decision": "PROPOSE_FOR_OWNER_REVIEW" if record else "WITHHOLD",
                           "adopted": False, "reasons": reasons})
    prior_path = ROOT / "evidence/batch4_identity_import_20261013/healthcare_review.json"
    prior = json.loads(prior_path.read_bytes())
    return {"schema_version": 1, "reviewed_at": clock.isoformat(), "baseline_commit": "5c5ea0ba6d5101dc67cfc3a7ad4c333f8ad39d40",
            "contract": "EXACT_CAPTURED_CLINICAL_EXPERIMENTAL_ARM_SPONSOR_WITH_DIRECT_SAME_DAY_ISSUER_SNAPSHOT",
            "source_requests": 0, "clinical_live_requests": 0, "live_current_capture_obtained": False,
            "source_history_status": "WAIT_QUOTA_HISTORY", "new_quota_store_created": False,
            "source_history_gap": "The old ClinicalTrials capture driver used a temporary reservation store; its ledger was not recovered. An empty current table does not recover those historical attempts.",
            "public_raw_captures_reused": captures, "candidates": candidates,
            "proposed_records": records, "proposal_count": len(records),
            "historical_event_proposals": {"path": prior_path.relative_to(ROOT).as_posix(),
                                            "sha256": hashlib.sha256(prior_path.read_bytes()).hexdigest(),
                                            "withheld_count": prior["withheld_records"],
                                            "owner_review_recorded_at": prior["owner_review_recorded_at"],
                                            "rewritten": False},
            "production_registry_sha256": hashlib.sha256(DEFAULT_HEALTHCARE_IDENTITIES.read_bytes()).hexdigest(),
            "production_registry_modified": False, "issuer_alias_normalized": False,
            "historical_ownership_inferred": False, "comparators_attributed": False,
            "full_specialist_acceptance": False, "OOS_verified": False, "actual_user_windows_verified": False,
            "remaining_gaps": ["Independent exact-name current issuer bridge, not an & Co normalization or old DEI-period extension",
                               "Recoverable durable clinical reservation history before another LIVE operation",
                               "Actual current-day clinical object capture when its durable quota permits",
                               "Broader product/sponsor/CMS exposure denominators and point-in-time source/price histories"]}


def main():
    path = EVIDENCE / "source_review.json"
    clock = (datetime.fromisoformat(json.loads(path.read_text())["reviewed_at"])
             if path.exists() else datetime.now(timezone.utc))
    report = build_review_report(as_of=clock)
    path.write_text(json.dumps(report, indent=2) + "\n")
    (EVIDENCE / "identity_proposal.json").write_text(json.dumps({
        "schema_version": 1, "record_count": report["proposal_count"], "reviewed_at": report["reviewed_at"],
        "records": report["proposed_records"], "review_status": "WITHHELD_PENDING_EXACT_PROSPECTIVE_ISSUER_BRIDGE_AND_CURRENT_SOURCE",
        "production_registry_modified": False, "positive_unit_fixture_is_live_evidence": False,
    }, indent=2) + "\n")
    print(json.dumps({"proposal_count": report["proposal_count"], "source_requests": 0,
                      "decisions": [{"nct_id": row["nct_id"], "reasons": row["reasons"]}
                                    for row in report["candidates"]]}, indent=2))


if __name__ == "__main__":
    main()
