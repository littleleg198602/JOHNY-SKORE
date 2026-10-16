"""Bounded one-off SEC research under the retained provider lease/cooldown.

Not a daily quota-managed application collector. No daily history is reset or
reconstructed. The referenced older accession comes from the pinned owner
submissions profile; all members of that accession are one disclosure.
"""
from datetime import datetime, timedelta, timezone
import argparse
import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from market_checker_app.collectors.sec_edgar_client import SecEdgarClient, _allowed_sec_url
from market_checker_app.services.ir_primary_scout_service import IRPrimaryHTTPClient, _has_sensitive_body
from market_checker_app.storage.scout_store import ScoutStore


ROOT = Path(__file__).resolve().parent
USER_AGENT = ("JOHNY-SKORE primary-evidence-research/1.0 "
              "(public project contact: github.com/littleleg198602/JOHNY-SKORE)")
ACCESSION = "0001582737-14-000015"
BASE = "https://www.sec.gov/Archives/edgar/data/1513142/000158273714000015/"
SOURCES = (("nadella_2014_form3.txt", BASE + ACCESSION + ".txt"),
           ("nadella_2014_form3.xml", BASE + "edgar.xml"),
           ("nadella_2014_form3_index.html", BASE + ACCESSION + "-index.html"))
MAX_BYTES = 2_000_000


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main(db_path):
    if not Path(db_path).is_file() or (ROOT / "captures.json").exists():
        raise ValueError("Needs the existing retained database and no prior capture manifest")
    store = ScoutStore(Path(db_path))
    now = datetime.now(timezone.utc)
    token = store.claim_provider("sec", as_of=now, seconds=180)
    manifest = {"schema_version": 1, "mode": "ONE_OFF_PRIMARY_RESEARCH_NOT_QUOTA_MANAGED_COLLECTOR_LIVE",
                "requested_accession": ACCESSION, "request_user_agent": USER_AGENT,
                "started_at": now.isoformat(), "maximum_requests": 3, "request_count": 0,
                "captures": [], "lease_acquired": token is not None,
                "daily_quota_history_reset_or_reconstructed": False, "http_requests_retry_or_redirect": 0}
    if token is None:
        manifest["status"] = "COOLDOWN_OR_BUSY"
        (ROOT / "captures.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        return 2
    pace = SecEdgarClient(user_agent=USER_AGENT, max_attempts=1, min_request_interval_seconds=.125)
    opener = build_opener(_NoRedirect())
    (ROOT / "primary").mkdir(exist_ok=False)
    try:
        for filename, url in SOURCES:
            _allowed_sec_url(url)
            now = datetime.now(timezone.utc)
            if store.provider_cooldown("sec", as_of=now) or not store.renew_provider("sec", token, as_of=now, seconds=180):
                manifest["status"] = "LEASE_LOST"
                break
            pace._wait_for_rate_slot()
            item = {"url": url, "requested_at": datetime.now(timezone.utc).isoformat()}
            manifest["request_count"] += 1
            try:
                request = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
                try:
                    response = opener.open(request, timeout=15)
                except HTTPError as error:
                    response = error
                with response:
                    body = response.read(MAX_BYTES + 1)
                    if len(body) > MAX_BYTES:
                        raise ValueError("SEC source exceeds complete-body capture cap")
                    length = response.headers.get("Content-Length", "")
                    if length.isdigit() and int(length) != len(body):
                        raise ValueError("SEC source is not a complete declared body")
                    item.update(http_status=response.code, observed_at=datetime.now(timezone.utc).isoformat(),
                                content_type=response.headers.get("Content-Type"), http_date=response.headers.get("Date"),
                                last_modified=response.headers.get("Last-Modified"), size_bytes=len(body),
                                sha256=hashlib.sha256(body).hexdigest())
                    encoded = response.headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}
                sensitive = _has_sensitive_body(body)
                item["retention"] = "WITHHELD_SENSITIVE_BODY" if sensitive else (
                    "WITHHELD_ENCODED_BODY" if encoded else "ORIGINAL_BYTES")
                if not sensitive and not encoded:
                    path = ROOT / "primary" / filename
                    with path.open("xb") as saved:
                        saved.write(body)
                    item["path"] = path.relative_to(ROOT).as_posix()
                if item["http_status"] in {401, 403, 429}:
                    store.defer_provider("sec", token, as_of=datetime.now(timezone.utc),
                                         retry_after=timedelta(hours=1 if item["http_status"] == 429 else 24),
                                         reason=f"SEC HTTP {item['http_status']}")
                manifest["captures"].append(item)
                manifest["status"] = "CAPTURED_SOURCE_ONLY" if item["http_status"] == 200 and item["retention"] == "ORIGINAL_BYTES" else "PARTIAL"
                if manifest["status"] == "PARTIAL":
                    break
            except Exception as error:
                item.update(observed_at=datetime.now(timezone.utc).isoformat(), error_type=type(error).__name__, retention="NO_COMPLETE_BODY")
                manifest["captures"].append(item)
                manifest["status"] = "PARTIAL"
                break
    finally:
        store.release_provider("sec", token)
        manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
        manifest["lease_released"] = True
        with (ROOT / "captures.json").open("x", encoding="utf-8", newline="\n") as out:
            out.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": manifest["status"], "request_count": manifest["request_count"],
                      "captures": [{key: row.get(key) for key in ("http_status", "sha256", "size_bytes", "retention")}
                                   for row in manifest["captures"]]}, indent=2))
    return 0 if manifest["status"] == "CAPTURED_SOURCE_ONLY" else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    raise SystemExit(main(parser.parse_args().db))
