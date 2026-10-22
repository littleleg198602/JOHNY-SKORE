"""Bounded primary capture driver; never rewrites an existing capture.

Run from the repository root. These exact-study diagnostics consume the
ClinicalTrials provider's lease/request reservations in one temporary store.
They do not certify or write a completed issuer-wide source check.
"""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from market_checker_app.services.healthcare_scout_service import ClinicalTrialsClient
from market_checker_app.services.public_source_budget import PublicSourceBudget
from market_checker_app.storage.scout_store import ScoutStore


ROOT = Path(__file__).resolve().parent
STUDY_IDS = ("NCT03987919", "NCT04847557")
MAX_REQUESTS = 12
MAX_BYTES_PER_REQUEST = 4_000_000
SEC_DOCUMENT_BYTES = 8_000_000
SAFE_HEADERS = {"date", "last-modified", "etag", "content-type", "content-length"}


class Response(BytesIO):
    def __init__(self, raw, url):
        super().__init__(raw)
        self.url = url

    def geturl(self):
        return self.url


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true", help="Verify and reuse immutable captures")
    options = parser.parse_args()
    captures = ROOT / "captures"
    captures.mkdir(parents=True, exist_ok=True)
    if not options.resume and ((ROOT / "capture_index.json").exists() or any(captures.iterdir())):
        raise ValueError("The dated capture directory already exists; preserve it")
    index = [json.loads(path.read_text()) for path in sorted(captures.glob("*.metadata.json"))]
    reused = []

    def write_index(**details):
        (ROOT / "capture_index.json").write_text(json.dumps({
            "schema_version": 1, "request_count": len(index), "max_request_count": MAX_REQUESTS,
            "captures": index, "reused_capture_keys": reused,
            "scope": "Two exact NCT IDs, their displayed sponsor/interventions and independent Lilly annual filings",
            "source_response_is_historical_availability_proof": False, **details,
        }, indent=2) + "\n")

    def capture(key, request, timeout=20):
        request_url = request.full_url
        expected = urlsplit(request_url).hostname
        if expected not in {"clinicaltrials.gov", "www.sec.gov", "data.sec.gov"}:
            raise ValueError("Capture is limited to its official primary publishers")
        byte_budget = SEC_DOCUMENT_BYTES if expected == "www.sec.gov" else MAX_BYTES_PER_REQUEST
        existing = next((meta for meta in index if meta["capture_key"] == key), None)
        if existing:
            archive = (captures / existing["body_path"]).read_bytes()
            raw = gzip.decompress(archive) if existing["storage_encoding"] == "gzip" else archive
            if (existing["request_url"] != request_url
                    or hashlib.sha256(raw).hexdigest() != existing["body_sha256"]
                    or hashlib.sha256(archive).hexdigest() != existing["archive_sha256"]
                    or len(raw) != existing["body_bytes"]):
                raise ValueError("Existing capture integrity or request scope mismatch")
            if existing["http_status"] != 200:
                raise HTTPError(existing["response_url"], existing["http_status"],
                                "Preserved primary denial", {}, BytesIO(raw))
            reused.append(key)
            return Response(raw, existing["response_url"])
        if len(index) >= MAX_REQUESTS:
            raise ValueError("Capture request budget exhausted")
        requested = datetime.now(timezone.utc)
        try:
            response = urlopen(request, timeout=timeout)
        except HTTPError as error:
            response = error
        with response:
            final_url = response.geturl()
            status = response.code
            headers = {key.lower(): value for key, value in response.headers.items()
                       if key.lower() in SAFE_HEADERS}
            raw = response.read(byte_budget + 1)
        observed = datetime.now(timezone.utc)
        if urlsplit(final_url).hostname != expected:
            raise ValueError("Capture left its official publisher")
        is_html = expected == "www.sec.gov"
        body_path = f"{key}.body.html.gz" if is_html else f"{key}.body.json"
        archived = gzip.compress(raw, mtime=0) if is_html else raw
        meta = {"capture_key": key, "request_url": request_url, "response_url": final_url,
                "requested_at": requested.isoformat(), "observed_at": observed.isoformat(),
                "http_status": status, "response_headers": headers,
                "body_path": body_path, "storage_encoding": "gzip" if is_html else "identity",
                "body_sha256": hashlib.sha256(raw).hexdigest(), "body_bytes": len(raw),
                "archive_sha256": hashlib.sha256(archived).hexdigest(), "archived_bytes": len(archived),
                "byte_budget": byte_budget,
                "body_complete_within_byte_budget": len(raw) <= byte_budget}
        (captures / body_path).write_bytes(archived)
        (captures / f"{key}.metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
        index.append(meta)
        write_index(status="IN_PROGRESS")
        if len(raw) > byte_budget:
            raise ValueError("Preserved incomplete capture exceeds byte budget")
        if status != 200:
            raise HTTPError(final_url, status, "Preserved primary denial", {}, BytesIO(raw))
        return Response(raw, final_url)

    client = ClinicalTrialsClient(study_ids=STUDY_IDS)
    current_key = None

    def transport(request, *, timeout):
        return capture(current_key, request, timeout)

    queries = []
    with TemporaryDirectory() as temporary:
        store = ScoutStore(Path(temporary) / "bounded_capture.db")
        budget = PublicSourceBudget(store, "clinicaltrials", as_of=datetime.now(timezone.utc),
                                    max_run_seconds=180)
        if not budget.acquire():
            raise ValueError("ClinicalTrials provider lease unavailable")
        try:
            with patch("market_checker_app.services.healthcare_scout_service.urlopen", transport):
                cursor = None
                for page in range(1, 5):
                    current_key = f"trial_lilly_positive_page_{page}"
                    rows, next_token = budget.call(
                        "LLY", "0000059478:Eli Lilly and Company",
                        lambda: client.page("Eli Lilly and Company", size=1, cursor=cursor))
                    queries.append({"capture_key": current_key, "returned_count": len(rows),
                                    "has_next_page": next_token is not None})
                    if next_token is None:
                        break
                    cursor = next_token
                else:
                    raise ValueError("Exact-study traversal exceeded its page budget")
                current_key = "trial_pfizer_exact_study_negative"
                rows, next_token = budget.call(
                    "LLY", "0000059478:exact-study negative sponsor control",
                    lambda: client.page("Pfizer", size=2, cursor=None))
                queries.append({"capture_key": current_key, "returned_count": len(rows),
                                "has_next_page": next_token is not None})
            usage = budget.annotate({"status": "OK", "checked_issuers": 0,
                                     "queries": queries, "issuer_wide_check_written": False})
        finally:
            budget.release()

    urls = {
        "trial_surpass2_complete_record": "https://clinicaltrials.gov/api/v2/studies/NCT03987919",
        "trial_summit_complete_record": "https://clinicaltrials.gov/api/v2/studies/NCT04847557",
        "sec_lly_2021_10k": "https://www.sec.gov/Archives/edgar/data/59478/000005947822000068/lly-20211231.htm",
        "sec_lly_2024_10k": "https://www.sec.gov/Archives/edgar/data/59478/000005947825000067/lly-20241231.htm",
        "sec_lly_2025_10k": "https://www.sec.gov/Archives/edgar/data/59478/000005947826000013/lly-20251231.htm",
        "sec_lly_submissions": "https://data.sec.gov/submissions/CIK0000059478.json",
    }
    for key, url in urls.items():
        capture(key, Request(url, headers={"Accept": "application/json,text/html",
                                          "User-Agent": "JohnySkoreScout/1.0"}))
    write_index(status="COMPLETE", clinicaltrials_budget=usage,
                resume_reservations_include_verified_cached_captures=True)
    print(json.dumps({"capture_count": len(index), "query_pages": queries,
                      "clinicaltrials_daily_usage": usage}, indent=2))


if __name__ == "__main__":
    main()
