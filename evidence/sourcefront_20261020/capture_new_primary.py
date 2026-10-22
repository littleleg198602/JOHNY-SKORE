"""Three reserved, genuinely new SEC research GETs; no collector or database."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from market_checker_app.collectors.sec_edgar_client import _allowed_sec_url
from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body, _safe_header

HERE = Path(__file__).resolve().parent


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def now():
    return datetime.now(timezone.utc).isoformat()


def capture(row, plan_bytes):
    key, url = row["key"], row["url"]
    _allowed_sec_url(url)
    directory = HERE / "primary"
    directory.mkdir(exist_ok=True)
    reservation = {"key": key, "requested_url": url, "method": "GET", "started_at": now(),
                   "request_plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
                   "max_response_bytes": row["max_response_bytes"], "timeout_seconds": 25,
                   "research_only": True, "collector_api_used": False,
                   "app_quota_managed_live_claimed": False, "retry": False, "redirect": False,
                   "quota_database_created_or_modified": False, "status": "REQUEST_RESERVED"}
    with (directory / f"{key}.reserved.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(reservation, indent=2) + "\n")
    result = dict(reservation)
    started = time.monotonic()
    raw = None
    try:
        request = Request(url, headers={
            "User-Agent": "JOHNY-SKORE primary-evidence-research/1.0 (public repository: littleleg198602/JOHNY-SKORE)",
            "Accept-Encoding": "identity"})
        with build_opener(NoRedirect()).open(request, timeout=25) as response:
            result.update(http_status=response.status, final_url=response.geturl(), response_headers={
                key: _safe_header(response.headers.get(key)) for key in
                ("Date", "Last-Modified", "Content-Type", "Content-Length", "Content-Encoding")})
            raw = response.read(row["max_response_bytes"] + 1)
            encoding = response.headers.get("Content-Encoding", "identity")
            length = response.headers.get("Content-Length")
        result.update(observed_at=now(), bytes=len(raw), raw_sha256=hashlib.sha256(raw).hexdigest())
        if (result["http_status"] != 200 or result["final_url"] != url or not raw
                or len(raw) > row["max_response_bytes"] or time.monotonic() - started > 25
                or length is not None and int(length) != len(raw) or encoding.casefold() != "identity"):
            result["status"] = "WITHHELD_INCOMPLETE_OR_UNSUPPORTED_RESPONSE"
        elif _has_sensitive_body(raw):
            result["status"] = "WITHHELD_WHOLE_SENSITIVE_BODY"
        else:
            suffix = ".txt" if url.endswith(".txt") else ".htm"
            name = key + suffix
            with (directory / name).open("xb") as stream:
                stream.write(raw)
            result.update(status="PUBLIC_ORIGINAL_BYTES_CAPTURED", body_path=name,
                          body_transformed=False, public_raw_available=True)
    except Exception as error:
        result.update(status="CAPTURE_FAILED", error_type=type(error).__name__, observed_at=now())
        if isinstance(error, HTTPError):
            result["http_status"] = error.code
    finally:
        raw = None
        result.setdefault("observed_at", now())
        result["elapsed_seconds"] = time.monotonic() - started
        with (directory / f"{key}.metadata.json").open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, indent=2) + "\n")
        print(json.dumps({k: result.get(k) for k in
                          ("key", "status", "http_status", "bytes", "raw_sha256", "observed_at")}))


if __name__ == "__main__":
    plan_bytes = (HERE / "request_plan.json").read_bytes()
    plan = json.loads(plan_bytes)
    assert len(plan["urls"]) == plan["max_http_transports"] == 3
    assert plan["retry"] is False and plan["redirect"] is False
    for row in plan["urls"]:
        capture(row, plan_bytes)
