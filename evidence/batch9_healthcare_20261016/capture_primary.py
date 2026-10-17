"""Explicit one-attempt public static-source research; never a collector API.

Every transport is reserved on disk before requesting. URLs cannot be supplied
by callers, redirects/retries are disabled, whole original bodies are capped,
and unsafe originals are withheld whole. A resumed invocation never repeats a
previous slot. This ledger is not reconstructed collector quota history.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body

HERE = Path(__file__).resolve().parent
MAX_BYTES = 4_000_000
MAX_ATTEMPTS = 6
TIMEOUT_SECONDS = 25
URLS = {
    1: ("sec_pfe_current_submissions", "https://data.sec.gov/submissions/CIK0000078003.json"),
    2: ("fda_bla761081_application", "https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo=761081"),
    3: ("fda_purple_book_761081", "https://purplebooksearch.fda.gov/index.cfm?blaNo=761081&event=productdetails"),
    4: ("fda_bla761081_approval_package", "https://www.accessdata.fda.gov/drugsatfda_docs/nda/2019/761081Orig1s000TOC.cfm"),
    5: ("fda_bla761081_quality_review", "https://www.accessdata.fda.gov/drugsatfda_docs/nda/2019/761081Orig1s000ChemR.pdf"),
}


def clock():
    return datetime.now(timezone.utc).isoformat()


def write_new(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def capture(sequence):
    if sequence not in URLS or not 1 <= sequence <= MAX_ATTEMPTS:
        raise ValueError("Select one predeclared research slot, never an arbitrary endpoint")
    plan_path = HERE / "research_plan.json"
    if not plan_path.exists():
        write_new(plan_path, {"schema_version": 1, "recorded_at": clock(),
            "maximum_research_transports": MAX_ATTEMPTS, "maximum_original_body_bytes": MAX_BYTES,
            "timeout_seconds": TIMEOUT_SECONDS, "redirects_allowed": False, "retries_allowed": False,
            "source_scope": "One PFE BLA761081 application/product object and earlier direct SEC issuer snapshot; FDA static pages only",
            "FDA_CMS_Clinical_API_collectors_allowed": False, "collector_quota_database_created_or_reconstructed": False,
            "original_durable_collector_history_claimed": False,
            "predeclared_urls": {str(key): value[1] for key, value in URLS.items()},
            "unused_sixth_slot_is_not_permission_to_raise_the_cap": True})
    plan_raw = plan_path.read_bytes()
    plan = json.loads(plan_raw)
    if (plan["maximum_research_transports"] != MAX_ATTEMPTS or plan["maximum_original_body_bytes"] != MAX_BYTES
            or plan["predeclared_urls"].get(str(sequence)) != URLS[sequence][1]):
        raise ValueError("Original research plan does not authorize this transport")
    # Explicit bounded inventory of slots, not a recreated application budget.
    for slot in range(1, MAX_ATTEMPTS + 1):
        if (HERE / f"attempt-{slot:02d}-reserved.json").exists() and slot == sequence:
            raise ValueError("Research slot already reserved; do not retry or reset")
    key, url = URLS[sequence]
    record = {"schema_version": 1, "capture_form": "ORIGINAL_HTTP_RESPONSE", "sequence": sequence,
              "capture_key": key, "request_url": url, "reserved_at": clock(),
              "research_plan_sha256": hashlib.sha256(plan_raw).hexdigest(), "reserved_before_transport": True,
              "research_attempt_cap": MAX_ATTEMPTS, "byte_budget": MAX_BYTES, "timeout_seconds": TIMEOUT_SECONDS,
              "redirects_allowed": False, "retries_allowed": False, "research_only": True,
              "collector_API_or_quota_DB_used": False, "new_accounts_or_credentials": False}
    write_new(HERE / f"attempt-{sequence:02d}-reserved.json", record)
    result = dict(record, requested_at=clock(), http_status=None, response_url=None,
                  body_complete_within_byte_budget=False, raw_publication_permitted=False,
                  body_sha256=None, body_bytes=None, body_path=None)
    started = time.monotonic()
    response = None
    try:
        request = Request(url, headers={"User-Agent": "JohnySkorePrimaryEvidence/1.0", "Accept": "application/json,text/html,application/pdf", "Accept-Encoding": "identity"})
        try:
            response = build_opener(NoRedirect()).open(request, timeout=TIMEOUT_SECONDS)
        except HTTPError as error:
            response = error
        with response:
            result.update(http_status=response.code, response_url=response.geturl(), response_headers={
                key: response.headers.get(key) for key in ("Date", "Content-Type", "Content-Length", "Last-Modified", "ETag", "Content-Encoding")})
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            result.update(status="INCOMPLETE_BODY_EXCEEDS_CAP", received_prefix_bytes=len(raw),
                          prefix_sha256=hashlib.sha256(raw).hexdigest(), retention="NOT_CAPTURED_COMPLETE_BODY")
        else:
            result.update(body_complete_within_byte_budget=True, body_sha256=hashlib.sha256(raw).hexdigest(), body_bytes=len(raw))
            if _has_sensitive_body(raw):
                result.update(status="WITHHELD_SENSITIVE_BODY", retention="WITHHELD_SENSITIVE_BODY")
            elif result["response_url"] != url:
                result.update(status="SOURCE_URL_CONFLICT", retention="WITHHELD_SOURCE_CONFLICT")
            else:
                filename = key + ".body.bin"
                with (HERE / filename).open("xb") as stream:
                    stream.write(raw)
                result.update(body_path=filename, raw_publication_permitted=True, retention="ORIGINAL_BYTES",
                              status="HTTP_200_COMPLETE" if result["http_status"] == 200 else "HTTP_ERROR_ORIGINAL_COMPLETE")
    except Exception as error:
        # Whole transport exception bodies/URLs may contain access values. Store
        # only a class and a bounded generic reason, never a cleaned substitute.
        result.update(status="TRANSPORT_FAILURE", failure_type=type(error).__name__, retention="NOT_CAPTURED_COMPLETE_BODY")
    result.update(observed_at=clock(), elapsed_seconds=time.monotonic() - started)
    write_new(HERE / f"attempt-{sequence:02d}-result.json", result)
    print(json.dumps({key: result.get(key) for key in ("sequence", "capture_key", "status", "http_status", "body_bytes", "body_sha256", "observed_at")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sequence", type=int)
    args = parser.parse_args()
    capture(args.sequence)
