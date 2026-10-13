"""Reproduce the narrow Form 4 review from pinned, hash-checked captures."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PurePath
import re

from market_checker_app.collectors.sec_edgar_client import SecFiling
from market_checker_app.services.sec_document_extraction import readable_sec_text
from market_checker_app.services.sec_form4_service import extract_form4_observation
from market_checker_app.services.sec_form4_identity_review import review_form4_identities


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
ACCESSION = "0000789019-26-000161"
TEN_K_ACCESSION = "0001193125-26-323660"


def repository_reference(path: PurePath, *, root: PurePath = REPO) -> str:
    """Serialize a contained repository reference independently of host paths."""
    relative = path.relative_to(root)
    if ".." in relative.parts:
        raise ValueError("Repository reference cannot traverse outside its root")
    return relative.as_posix()


def build_review_artifact():
    manifest = json.loads((ROOT / "captures.json").read_bytes())
    captures = {Path(row["path"]).name: row for row in manifest["captures"] if row.get("http_status") == 200}
    documents = {}
    for row in captures.values():
        body = (ROOT / row["path"]).read_bytes()
        if len(body) != row["size_bytes"] or hashlib.sha256(body).hexdigest() != row["sha256"]:
            raise ValueError("Pinned SEC capture bytes/hash mismatch")
        documents[row["sha256"]] = body
    xml_capture, index_capture = (captures[key] for key in ("msft_form4.xml", "msft_form4_index.html"))
    index = documents[index_capture["sha256"]].decode("utf-8")
    filing_date = re.search(r'Filing Date</div>\s*<div class="info">([^<]+)', index)[1]
    accepted_raw = re.search(r'Accepted</div>\s*<div class="info">([^<]+)', index)[1]
    filed_at = datetime.combine(date.fromisoformat(filing_date), datetime.min.time(), tzinfo=timezone.utc)
    filing = SecFiling(ACCESSION, "4", filed_at, None, "form4.xml", xml_capture["url"], index_capture["url"])
    observation = extract_form4_observation(
        documents[xml_capture["sha256"]], filing=filing, issuer_cik="0000789019",
        observed_at=datetime.fromisoformat(xml_capture["observed_at"]),
    )
    knowledge_at = max(datetime.fromisoformat(row["observed_at"]) for row in captures.values())
    owner_capture = captures["nadella_submissions.json"]
    owner = json.loads(documents[owner_capture["sha256"]])
    owner_snapshot_day = datetime.fromisoformat(owner_capture["observed_at"]).date()
    issuer_capture = captures["msft_submissions.json"]
    issuer = json.loads(documents[issuer_capture["sha256"]])
    recent = issuer["filings"]["recent"]
    form4_index, ten_k_index = (recent["accessionNumber"].index(key) for key in (ACCESSION, TEN_K_ACCESSION))
    ten_k_filing_day = date.fromisoformat(recent["filingDate"][ten_k_index])
    instrument_capture = captures["msft_2026_R1.htm"]
    instrument_text = readable_sec_text(documents[instrument_capture["sha256"]])
    title = "Common stock, $0.00000625 par value per share"
    quote = "Trading Symbol MSFT Title of 12(b) Security " + title
    if quote not in instrument_text:
        raise ValueError("Pinned 10-K cover title/trading-symbol table differs")
    candidates = [
        {"record_id": "msft-nadella-cik-profile-capture-day-only", "kind": "OWNER_CIK_PROFILE",
         "owner_cik": owner["cik"], "owner_name": owner["name"],
         "quote": '"name":"Nadella Satya"', "source_url": owner_capture["url"],
         "sha256": owner_capture["sha256"], "observed_at": owner_capture["observed_at"],
         "known_at": owner_capture["observed_at"], "published_at": None,
         "effective_from": owner_snapshot_day.isoformat(),
         "effective_to": (owner_snapshot_day + timedelta(days=1)).isoformat()},
        {"record_id": "msft-10k-cover-instrument-filing-day-only", "kind": "INSTRUMENT_IDENTITY",
         "issuer_cik": "0000789019", "ticker": "MSFT", "security_title": title, "derivative": False,
         "instrument_identifier": None, "quote": quote, "source_url": instrument_capture["url"],
         "sha256": instrument_capture["sha256"], "observed_at": instrument_capture["observed_at"],
         "known_at": instrument_capture["observed_at"], "published_at": None,
         "effective_from": ten_k_filing_day.isoformat(),
         "effective_to": (ten_k_filing_day + timedelta(days=1)).isoformat()},
    ]
    review = review_form4_identities(
        observation, knowledge_at=knowledge_at, evidence=candidates, captured_documents=documents,
        source_document_sha256=xml_capture["sha256"],
    )
    old_cover_path = REPO / "evidence/batch3_sec_20261012/primary/msft_2026_R1.htm"
    old_cover = old_cover_path.read_bytes()
    cover_visible_unchanged = readable_sec_text(old_cover) == instrument_text
    return {
        "schema_version": 1, "result": "SOURCE_CAPTURE_AND_EXACT_IDENTITY_GAP_REVIEW",
        "source_observation": observation, "identity_review": review, "candidate_input": candidates,
        "source_clocks": {
            "filing_date_raw": filing_date, "index_acceptance_datetime_unqualified_raw": accepted_raw,
            "submissions_acceptance_datetime_raw": recent["acceptanceDateTime"][form4_index],
            "filing_date_normalized_to_midnight_utc_is_not_publication_timestamp": True,
            "first_form4_bytes_observed_at": xml_capture["observed_at"],
            "review_knowledge_at": knowledge_at.isoformat(),
            "http_and_last_modified_clocks_retained_in": "captures.json",
            "acceptance_clock_timezone_not_inferred_by_review": True,
        },
        "raw_cover_revision": {
            "prior_path": repository_reference(old_cover_path),
            "prior_sha256": hashlib.sha256(old_cover).hexdigest(),
            "current_sha256": instrument_capture["sha256"],
            "raw_bytes_changed": old_cover != documents[instrument_capture["sha256"]],
            "visible_text_unchanged": cover_visible_unchanged,
            "interpretation": "Observed publisher script URL changed; both raw snapshots retained. No corporate-action or identity change inferred.",
        },
        "limits": [
            "one actual issuer/accession; eight as-filed rows, not relevant-universe coverage",
            "owner CIK/name exact profile match does not establish a legal person or historical director role",
            "current owner profile is a capture-day candidate, outside the September report date",
            "10-K cover and Form 4 titles differ; no invented alias and no exact instrument identifier",
            "declared one-day candidate intervals are scope restrictions, not verified effective dates",
            "no person or instrument records adopted; no production registry writes",
            "source clocks/raw revisions retained; today's capture is not historical availability",
            "no scoring, trade execution, OOS, full acceptance or Windows end-to-end claim",
        ],
        "scoring_applied": False, "completion_verified": False,
    }


if __name__ == "__main__":
    output = ROOT / "identity_review.json"
    output.write_text(json.dumps(build_review_artifact(), indent=2) + "\n", encoding="utf-8")
    print(repository_reference(output))
