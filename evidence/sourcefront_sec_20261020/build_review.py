"""Offline, byte-pinned Form144/account comparison; never fetches a source."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from market_checker_app.services.sec_form4_person_bridge import (
    SECPersonCapture, review_form144_account_notice,
)

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
NOTICE_PATH = "evidence/sourcefront_20261020/primary/nadella_form144_packet.txt"
ACCOUNT_PATH = "evidence/batch9_sec_20261016/primary/nadella_2026_form4_complete_packet.txt"
NOTICE_SHA = "69fd38a01da8b03a7b05ebe13d1fdd73e2996cfb3a1581af8971493b979ab40e"
ACCOUNT_SHA = "d2facdeb8d8935143f3b5fbc53fbe0140a28e69e5bd7915913d4ef573a769ec8"
SELECTION = {
    "notice_accession": "0001950047-26-008927", "notice_primary_filename": "primary_doc.xml",
    "account_accession": "0000789019-26-000157", "account_primary_filename": "form4.xml",
    "issuer_cik": "0000789019", "issuer_header_name": "MICROSOFT CORP",
    "owner_cik": "0001513142", "owner_header_name": "Nadella Satya", "transaction_day": "2026-08-31",
}


def pin(path):
    raw = (ROOT / path).read_bytes()
    return {"path": path, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def build_plan():
    receipt = json.loads((ROOT / "evidence/sourcefront_20261020/primary/nadella_form144_packet.metadata.json").read_bytes())
    account_receipt = json.loads((ROOT / "evidence/batch9_sec_20261016/attempt-01-result.json").read_bytes())
    captures = {}
    for key, path, digest, size, purpose, source in (
        ("notice", NOTICE_PATH, NOTICE_SHA, 4770, "FORM144_NOTICE_PACKET", receipt),
        ("account_packet", ACCOUNT_PATH, ACCOUNT_SHA, 9665, "DATED_OWNERSHIP_PACKET", account_receipt),
    ):
        reference = pin(path)
        if reference != {"path": path, "bytes": size, "sha256": digest}:
            raise ValueError("Pinned whole original body differs; no evidence rewrite is permitted")
        captures[key] = {"body_available": True, "body_ref": reference,
                         "metadata": {"record_id": "sourcefront-sec-" + key, "purpose": purpose,
                             "source_url": source.get("requested_url") or source["url"],
                             "sha256": digest, "size_bytes": size,
                             "observed_at": source["observed_at"], "known_at": source["observed_at"],
                             "retention": "ORIGINAL_BYTES"}}
    return {"schema_version": "captured_identity_review_plan_v1", "kind": "SEC_FORM144_ACCOUNT_NOTICE",
            "captures": captures, "request": {"selection": SELECTION.copy()}}


def load_inputs():
    plan = build_plan()
    captures = {}
    for key, row in plan["captures"].items():
        metadata = row["metadata"].copy()
        for field in ("observed_at", "known_at"):
            metadata[field] = datetime.fromisoformat(metadata[field])
        captures[key] = SECPersonCapture(body=(ROOT / row["body_ref"]["path"]).read_bytes(), **metadata)
    return plan["request"]["selection"].copy(), captures["notice"], captures["account_packet"], plan


def review_actual():
    selection, notice, account, _ = load_inputs()
    return review_form144_account_notice(notice_capture=notice, account_packet_capture=account, selection=selection)


def _array_token(raw, key, row):
    """Exact UTF-8 token spans in the retained original, not JSON reserialization."""
    text = raw.decode("utf-8")
    matches = list(re.finditer('"' + re.escape(key) + r'"\s*:\s*\[', text))
    if len(matches) != 1:
        raise ValueError("Discovery array locator is ambiguous")
    cursor = matches[0].end(); decoder = json.JSONDecoder()
    for at in range(row + 1):
        cursor = re.compile(r"\s*").match(text, cursor).end()
        start = cursor
        value, cursor = decoder.raw_decode(text, cursor)
        if at == row:
            a, z = len(text[:start].encode()), len(text[:cursor].encode())
            token = raw[a:z]
            return {"pointer": f"/filings/recent/{key}/{row}", "value": value,
                    "byte_start": a, "byte_end": z, "quote": token.decode(),
                    "sha256": hashlib.sha256(token).hexdigest()}
        cursor = re.compile(r"\s*,\s*").match(text, cursor).end()


def build_discovery():
    profiles = []
    for path, row, digest in (
        ("evidence/batch3_sec_20261012/primary/msft_submissions.json", 42,
         "2484a2a3b5677d892a1b25af573ce4eda7b6f449f5eda41bac6b3f106dc3139c"),
        ("evidence/batch8_sec_20261015/primary/nadella_current_submissions.json", 2,
         "90b5bc75b82e4d027368b07e2adcdf829eb232d0d88034476e0910e164a1133d"),
    ):
        reference = pin(path)
        if reference["sha256"] != digest:
            raise ValueError("Retained discovery original changed")
        raw = (ROOT / path).read_bytes(); parsed = json.loads(raw)
        profiles.append({"whole_original": reference, "root_cik": parsed["cik"], "root_name": parsed["name"],
                         "tokens": [_array_token(raw, key, row) for key in
                                    ("accessionNumber", "filingDate", "form", "primaryDocument", "size")]})
    return {"schema_version": "sourcefront_sec_discovery_v1", "created_at": datetime.now(timezone.utc).isoformat(),
            "baseline_commit": "96c4e90a8e72dcd11087e6a08371ee0c924fac83",
            "common_originals_commit": "f2aa76e", "retained_referring_originals": profiles,
            "grounded_primary_url": build_plan()["captures"]["notice"]["metadata"]["source_url"],
            "url_grounding": "Constructed canonical SEC complete-packet URL from exact issuer CIK and accession; not a literal profile URL",
            "prior_attempt_audit_scope": "Before the new root GET, this accession matched four retained submissions JSON bodies and no retained capture/attempt receipt; no global never-fetched claim",
            "actual_new_primary_capture": build_plan()["captures"]["notice"],
            "new_request_receipt": pin("evidence/sourcefront_20261020/primary/nadella_form144_packet.metadata.json"),
            "new_request_reservation": pin("evidence/sourcefront_20261020/primary/nadella_form144_packet.reserved.json"),
            "worker_http_requests": 0, "coordinator_new_primary_http_requests": 1,
            "new_source_claim": "Separate dated Form144 header and original XML bind same reporting account and issuer CIK; acquired-day overlap with Aug31 Form4 is not transaction identity",
            "exact_missing_bridge": ["Independent dated natural-person/legal-name-to-account binding",
                                     "Exact instrument/class/CUSIP validity on transaction day",
                                     "Legal role interval covering Aug31 independently of reported-account labels",
                                     "Exact transaction identity: notice86525/Common differs from Form4 178622 or70465.844/Common Stock"],
            "safe_unattempted_alternative": {
                "source": "evidence/batch3_sec_20261012/primary/msft_2026_index.html",
                "url": "https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm",
                "declared_bytes": 8585501, "not_requested": True,
                "limit": "Exceeds current4MB research limit; no assumption about unstored content and no request authorization used"},
            "no_continuity_identity_adoption_or_execution": True, "database_opened_or_modified": False,
            "withheld_bodies_read_recovered_or_substituted": False}


def write_new(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    write_new(HERE / "captured_review_plan.json", build_plan())
    write_new(HERE / "form144_account_notice_review_v1.json", review_actual())
    write_new(HERE / "source_discovery.json", build_discovery())
