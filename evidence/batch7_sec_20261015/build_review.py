"""Replay two reserved SEC originals and the retained original owner packet."""
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath

from market_checker_app.services.sec_person_kind_review import PersonKindCapture, review_sec_person_kind


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
ACCOUNT_ROOT = ROOT.parent / "batch6_sec_20261014"
PINNED = {
    "primary/msft_2025_proxy_index.html": (33931, "5658d63f9d7b98027f2028c744ecc1a63b51df83a38328a7e647ceaa41e42a0a"),
    "primary/msft_2025_proxy.htm": (2430522, "6394df905079e616174cf599672e60c839b3993af3231e43420d4fb6423532ac"),
}
ACCOUNT_PIN = (5836, "9123343610f3b95c84a0be6d172fea451ccc45139ad1d9d88f5ad91a343a5053")
FACT = {"person_name": "Satya Nadella", "context_locator": "table:163",
        "context_quote": "Satya Nadella Age: 58 | Director Since: 2014 | Birthplace: India",
        "age_quote": "Age: 58", "birthplace_quote": "Birthplace: India"}
EVENT_DAYS = ("2014-02-04", "2025-10-21", "2026-09-01")


def _capture(row, root, record_id, expected):
    if (row["size_bytes"], row["sha256"]) != expected or row["retention"] != "ORIGINAL_BYTES":
        raise ValueError("Person kind original metadata pin changed")
    ref = PurePosixPath(row["path"])
    if ref.is_absolute() or ".." in ref.parts or "\\" in row["path"] or ":" in row["path"]:
        raise ValueError("Person kind original path escaped its retained root")
    path = root.joinpath(*ref.parts)
    if path.is_symlink() or path.stat().st_size != row["size_bytes"] or row["size_bytes"] > 4_000_000:
        raise ValueError("Person kind whole original body exceeds its pin or cap")
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != row["sha256"]:
        raise ValueError("Person kind whole original byte hash changed")
    return PersonKindCapture(record_id, row["url"], body, row["sha256"], row["size_bytes"],
                             datetime.fromisoformat(row["observed_at"]))


def load_inputs():
    receipts, captures = [], []
    reservations = sorted(ROOT.glob("attempt-*-reserved.json"))
    if [path.name for path in reservations] != ["attempt-01-reserved.json", "attempt-02-reserved.json"]:
        raise ValueError("The exact two-request original research packet changed")
    for sequence, reservation_path in enumerate(reservations, 1):
        reservation = json.loads(reservation_path.read_bytes())
        result = json.loads((ROOT / f"attempt-{sequence:02d}-result.json").read_bytes())
        if (any(result.get(key) != value for key, value in reservation.items())
                or result["sequence"] != sequence or result["http_status"] != 200
                or result["maximum_original_body_bytes"] != 4_000_000
                or result["redirects_or_retries_allowed"]
                or result["application_daily_quota_claim"]
                or result["daily_history_reset_or_reconstructed"]
                or not result["original_retained_sec_lease_used"]
                or datetime.fromisoformat(result["reserved_at"]) >= datetime.fromisoformat(result["observed_at"])):
            raise ValueError("Person kind request reservation or physical capture clocks changed")
        captures.append(_capture(result, ROOT, f"actual-msft-proxy-2025-{sequence}", PINNED[result["path"]]))
        receipts.append(result)
    account_rows = json.loads((ACCOUNT_ROOT / "captures.json").read_bytes())["captures"]
    account_row = next(row for row in account_rows if row["path"] == "primary/nadella_2014_form3.txt")
    account = _capture(account_row, ACCOUNT_ROOT, "actual-nadella-form3-account-2014", ACCOUNT_PIN)
    descriptor = {"owner_cik": "0001513142", "owner_name": "Nadella Satya",
                  "source_url": account.source_url, "sha256": account.sha256}
    index, primary = captures
    return descriptor, account, dict(FACT), primary, index, receipts


def build_review_artifact():
    descriptor, account, fact, primary, index, receipts = load_inputs()
    clock = max(capture.available_at for capture in (account, primary, index))
    result = review_sec_person_kind(account_descriptor=descriptor, account_capture=account,
        person_fact=fact, primary_capture=primary, index_capture=index, knowledge_at=clock,
        requested_event_days=EVENT_DAYS)
    old_profile = json.loads((ROOT.parent / "batch4_sec_20261013/primary/msft_submissions.json").read_bytes())
    recent = old_profile["filings"]["recent"]
    accession = "0001193125-25-245150"
    position = recent["accessionNumber"].index(accession)
    return {"schema_version": 1, "result": "DATED_NAMED_PERSON_BIO_ACCOUNT_CIK_BRIDGE_WITHHELD",
        "actual_new_primary_http_requests": len(receipts), "original_http200_bodies": len(receipts),
        "maximum_new_primary_requests": 4, "new_person_disclosure_count": 1,
        "index_and_primary_are_same_disclosure": True, "research_capture_receipts": receipts,
        "review_knowledge_at": clock.isoformat(), "review": result,
        "discovery_pointer_only": {"profile_sha256": hashlib.sha256((ROOT.parent / "batch4_sec_20261013/primary/msft_submissions.json").read_bytes()).hexdigest(),
            "accession": accession, "form": recent["form"][position],
            "primary_document": recent["primaryDocument"][position],
            "filing_day_raw": recent["filingDate"][position],
            "acceptance_datetime_raw": recent["acceptanceDateTime"][position],
            "profile_clock_is_proxy_body_publication_clock": False,
            "profile_clock_reconciled_to_unqualified_index_clock": False},
        "source_rows_preserved": 8, "person_cik_verified_rows": 0, "instrument_verified_rows": 0,
        "issuer_attribution_allowed": False, "production_registries_modified": False,
        "scoring_applied": False, "full_specialist_acceptance": False,
        "historical_machine_availability_or_oos_proven": False, "windows_end_to_end_verified": False,
        "quota_managed_application_live_claim": False, "daily_quota_history_reset_or_reconstructed": False,
        "limits": ["One independently dated named-person bio only; no approved legal-person/account-CIK alias",
            "Ages and director labels are quoted as served, never present age, birthday or an event validity interval",
            "The index declares 110 fewer primary bytes; exact historical as-filed body version is not established",
            "Index and body are one disclosure, not two independent confirmations",
            "Actual capture knowledge is October 14, 2026; the dated filing is not historical machine availability",
            "The source account remains exact Nadella Satya; the bio name Satya Nadella is not an approved name-order alias",
            "One-off reserved primary research uses original retained lease/cadence without claiming an application LIVE daily-quota collector"]}


def build_review_plan():
    """Exact loader inputs for root's new SEC_PERSON_KIND dispatch."""
    descriptor, account, fact, primary, index, _ = load_inputs()
    captures = {}
    for key, capture, path in (
        ("account", account, "evidence/batch6_sec_20261014/primary/nadella_2014_form3.txt"),
        ("primary", primary, "evidence/batch7_sec_20261015/primary/msft_2025_proxy.htm"),
        ("index", index, "evidence/batch7_sec_20261015/primary/msft_2025_proxy_index.html"),
    ):
        captures[key] = {"metadata": capture.summary(), "body_available": True,
                        "body_ref": {"path": path, "sha256": capture.sha256, "bytes": capture.size_bytes}}
    return {"schema_version": "captured_identity_review_plan_v1", "kind": "SEC_PERSON_KIND",
            "request": {"account_descriptor": descriptor, "person_fact": fact,
                        "requested_event_days": list(EVENT_DAYS)}, "captures": captures}


if __name__ == "__main__":
    for name, artifact in (("person_kind_review.json", build_review_artifact()),
                           ("actual_review_plan.json", build_review_plan())):
        with (ROOT / name).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
        print(name)
