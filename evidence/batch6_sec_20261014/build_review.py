"""Hash-check original old/new captures and reproduce the exact owner bridge."""
from datetime import datetime
import hashlib
import json
from pathlib import Path

from evidence.batch4_sec_20261013.build_review import build_review_artifact as source_review
from market_checker_app.services.sec_form4_person_bridge import SECPersonCapture, review_form4_person_bridge


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
OLD_ROOT = ROOT.parent / "batch4_sec_20261013"


def _capture(row, root, purpose, record_id):
    body = (root / row["path"]).read_bytes()
    if len(body) != row["size_bytes"] or hashlib.sha256(body).hexdigest() != row["sha256"]:
        raise ValueError("Pinned person bridge original body/hash conflict")
    return SECPersonCapture(record_id, purpose, row["url"], body, row["sha256"], row["size_bytes"],
                            datetime.fromisoformat(row["observed_at"]))


def load_inputs():
    old = json.loads((OLD_ROOT / "captures.json").read_bytes())
    old_rows = {Path(row["path"]).name: row for row in old["captures"] if row.get("http_status") == 200}
    new = json.loads((ROOT / "captures.json").read_bytes())
    if (new["request_count"] != 3 or len(new["captures"]) != 3 or not new["lease_acquired"]
            or not new["lease_released"] or new["daily_quota_history_reset_or_reconstructed"]):
        raise ValueError("Original bounded SEC research/lease proof changed")
    new_captures = {}
    for row in new["captures"]:
        if row["http_status"] != 200 or row["retention"] != "ORIGINAL_BYTES":
            raise ValueError("Person bridge needs safe original source captures")
        body = (ROOT / row["path"]).read_bytes()
        if len(body) != row["size_bytes"] or hashlib.sha256(body).hexdigest() != row["sha256"]:
            raise ValueError("New person bridge capture full-byte/hash conflict")
        new_captures[Path(row["path"]).name] = body
    source = _capture(old_rows["msft_form4.xml"], OLD_ROOT, "FORM4_SOURCE", "actual-msft-form4-2026")
    profile = _capture(old_rows["nadella_submissions.json"], OLD_ROOT, "OWNER_PROFILE", "actual-nadella-profile-oct13")
    packet = _capture(new["captures"][0], ROOT, "DATED_OWNERSHIP_PACKET", "actual-nadella-form3-2014")
    # The raw XML is separately pinned but remains the same disclosure. It
    # confirms the primary document bytes embedded inside the original SGML.
    embedded = packet.body.split(b"<XML>", 1)[1].split(b"</XML>", 1)[0].strip()
    if embedded != new_captures["nadella_2014_form3.xml"].strip():
        raise ValueError("Original SGML embedded XML differs from original primary XML")
    return source_review()["source_observation"], source, (profile, packet), new


def build_review_artifact():
    observation, source, evidence, manifest = load_inputs()
    clock = max(capture.available_at for capture in (source, *evidence))
    event = review_form4_person_bridge(observation, source_capture=source, evidence=evidence, knowledge_at=clock)
    snapshot = review_form4_person_bridge(observation, source_capture=source, evidence=evidence,
                                         knowledge_at=clock, scope_mode="OBSERVED_SNAPSHOT")
    owner = json.loads((OLD_ROOT / "primary/nadella_submissions.json").read_bytes())
    recent = owner["filings"]["recent"]
    position = recent["accessionNumber"].index(manifest["requested_accession"])
    return {"schema_version": 1, "result": "INDEPENDENT_DATED_SEC_ACCOUNT_NAME_CORROBORATION_PERSON_WITHHELD",
            "actual_new_primary_http_requests": 3, "original_http200_bodies": 3,
            "new_disclosure_count": 1, "same_accession_documents_not_independent_sources": True,
            "review_knowledge_at": clock.isoformat(), "event_date_review": event,
            "observed_account_snapshot_review": snapshot,
            "discovery_pointer_only": {"accession": manifest["requested_accession"],
                                       "profile_filing_day_raw": recent["filingDate"][position],
                                       "profile_acceptance_datetime_raw": recent["acceptanceDateTime"][position],
                                       "current_profile_is_historical_person_identity": False},
            "acceptance_timezone_reconciled_or_inferred": False,
            "source_rows_preserved": 8, "person_verified_rows": 0, "instrument_verified_rows": 0,
            "production_registries_modified": False, "scoring_applied": False, "full_specialist_acceptance": False,
            "historical_machine_availability_or_oos_proven": False, "windows_end_to_end_verified": False,
            "quota_managed_application_live_claim": False,
            "research_sources": [
                {"url": "https://www.sec.gov/search-filings/cik-lookup",
                 "relevance": "CIKs identify both corporations and individuals; name order may differ. This does not approve an alias or prove account kind."},
                {"url": "https://www.sec.gov/Archives/edgar/data/1305473/000089914026000482/xslF345X06/form3.xml",
                 "relevance": "A Form3 may include LLC/LP reporting owners. Individual/group filing scope is not natural-person identity evidence."}],
            "limits": ["Only one exact reporting-owner CIK and eight source rows, not wider relevant-universe coverage",
                       "2014 Form3 header/XML independently corroborates the SEC account/name but does not explicitly establish natural-person legal identity",
                       "The current entityType=other profile and attorney signature do not establish natural-person kind or approved legal-name alias",
                       "2014 report day and Oct13 profile snapshot cannot be extended to the Sep1 2026 event day",
                       "Capture clocks are actual October knowledge; a dated older filing is not historical machine availability",
                       "Three one-off primary research requests honor retained SEC lease/cooldown/cadence but do not reconstruct a daily quota ledger"]}


if __name__ == "__main__":
    with (ROOT / "person_bridge_review.json").open("x", encoding="utf-8", newline="\n") as out:
        out.write(json.dumps(build_review_artifact(), indent=2, sort_keys=True) + "\n")
    print("person_bridge_review.json")
