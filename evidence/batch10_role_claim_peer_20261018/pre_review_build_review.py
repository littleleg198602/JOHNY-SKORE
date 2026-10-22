"""Offline replay of literal named-person role claims from retained originals."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from market_checker_app.collectors.sec_person_role_claim_review import (
    PersonRoleClaimCapture, review_sec_person_role_claims,
)

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
BIO = ROOT.parent / "batch7_sec_20261015"
ACCOUNT = ROOT.parent / "batch9_sec_20261016"
PINS = {
    "primary": (2_430_522, "6394df905079e616174cf599672e60c839b3993af3231e43420d4fb6423532ac"),
    "index": (33_931, "5658d63f9d7b98027f2028c744ecc1a63b51df83a38328a7e647ceaa41e42a0a"),
    "account_packet": (9_665, "d2facdeb8d8935143f3b5fbc53fbe0140a28e69e5bd7915913d4ef573a769ec8"),
}
BODY_PATHS = {
    "primary": "evidence/batch7_sec_20261015/primary/msft_2025_proxy.htm",
    "index": "evidence/batch7_sec_20261015/primary/msft_2025_proxy_index.html",
    "account_packet": "evidence/batch9_sec_20261016/primary/nadella_2026_form4_complete_packet.txt",
}
SELECTION = {
    "issuer_cik": "0000789019", "issuer_name": "Microsoft Corporation", "person_name": "Satya Nadella",
    "person_context_locator": "table:163",
    "person_context_quote": "Satya Nadella Age: 58 | Director Since: 2014 | Birthplace: India",
    "role_context_locator": "table:164/row:2/cell:5",
    "role_context_quote": "Experience: Microsoft Corporation (1992-present) • Chairman and Chief Executive Officer (2021-present) • Chief Executive Officer and Director (2014-2021) • Executive Vice President, Cloud and Enterprise (2013-2014) • President, Server and Tools (2011-2013) • Senior Vice President, Online Services Division (2009-2011) • Senior Vice President, Search, Portal, and Advertising (2008-2009) • Various positions of increasing authority (1992-2008)",
    "account_descriptor": {"accession": "0000789019-26-000157", "form": "4", "issuer_cik": "0000789019",
        "issuer_name": "MICROSOFT CORP", "owner_cik": "0001513142", "owner_name": "Nadella Satya",
        "officer_title": "Chief Executive Officer"},
}
SERVICE = "market_checker_app/collectors/sec_person_role_claim_review.py"


def pin(path):
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def load_inputs():
    receipts = {
        "primary": json.loads((BIO / "attempt-02-result.json").read_bytes()),
        "index": json.loads((BIO / "attempt-01-result.json").read_bytes()),
        "account_packet": json.loads((ACCOUNT / "attempt-01-result.json").read_bytes()),
    }
    captures = {}
    for key, row in receipts.items():
        expected = PINS[key]; path = REPO / BODY_PATHS[key]
        if ((row["size_bytes"], row["sha256"]) != expected or row["retention"] != "ORIGINAL_BYTES"
                or row["http_status"] != 200 or path.is_symlink() or path.stat().st_size != expected[0]):
            raise ValueError("Retained whole-original role source metadata changed")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected[1]:
            raise ValueError("Retained whole-original role source bytes changed")
        captures[key] = PersonRoleClaimCapture("retained-role-source-" + key, row["url"], raw,
            expected[1], expected[0], datetime.fromisoformat(row["observed_at"]))
    return captures, json.loads(json.dumps(SELECTION)), receipts


def build_review_artifact(*, replay_clocks=None):
    captures, selection, receipts = load_inputs()
    def review():
        return review_sec_person_role_claims(primary_capture=captures["primary"],
            index_capture=captures["index"], account_packet_capture=captures["account_packet"], selection=selection)
    if replay_clocks is None:
        result = review()
    else:
        with patch("market_checker_app.collectors.sec_person_role_claim_review._physical_clock",
                   side_effect=[datetime.fromisoformat(value) for value in replay_clocks]):
            result = review()
    return {"schema_version": 1, "result": "SEVEN_LITERAL_ROLE_CLAIMS_ONE_GENERIC_CAREER_ASSERTION_ACCOUNT_IDENTITY_UNVERIFIED",
        "actual_new_http_requests": 0, "source_collection_performed": False,
        "application_collector_live_or_quota_history_claim": False,
        "retained_original_capture_receipts": receipts,
        "source_service_pin": pin(REPO / SERVICE), "review": result,
        "new_verified_adoptions": 0, "full_specialist_acceptance": False,
        "windows_user_execution_or_oos_claim": False,
        "limits": ["One independently dated named biography disclosure and one separate Form4 account disclosure",
            "Seven literal role claims, one generic career assertion and one company-experience range are not nine independent sources",
            "Director Since and year ranges remain source assertions with YEAR precision",
            "The proxy report period is financial context, not a proven biography role-validity interval",
            "Present is relative to the 2025 filing, not the 2026 capture or current Form4",
            "Original proxy SGML header is not retained; DEI inline header and dated original index are validated",
            "The known 110-byte original index/served-primary discrepancy is retained",
            "Name order and issuer spelling do not establish approved account/person or legal-issuer aliases"]}


def build_review_plan():
    captures, selection, _ = load_inputs()
    return {"schema_version": "captured_identity_review_plan_v1", "kind": "SEC_PERSON_ROLE_CLAIM",
        "request": {"selection": selection},
        "captures": {key: {"metadata": capture.summary(), "body_available": True,
            "body_ref": {"path": BODY_PATHS[key], "sha256": capture.sha256, "bytes": capture.size_bytes}}
            for key, capture in captures.items()}}


if __name__ == "__main__":
    for name, report in (("person_role_claim_review_v1.json", build_review_artifact()),
                         ("actual_review_plan.json", build_review_plan())):
        with (ROOT / name).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(name)
