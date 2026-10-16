"""Six one-off bounded primary SEC transports; no application collector ledger."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from market_checker_app.collectors.sec_edgar_client import _allowed_sec_url
from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body, _safe_header

HERE = Path(__file__).resolve().parent
PROJECT = "JOHNY-SKORE primary-evidence-research/1.0 (public repository: littleleg198602/JOHNY-SKORE)"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise HTTPError(request.full_url, code, "Reserved transport cannot follow a redirect", headers, fp)


def now():
    return datetime.now(timezone.utc).isoformat()


def capture(url):
    plan_body = (HERE / "request_plan.json").read_bytes()
    plan = json.loads(plan_body)
    _allowed_sec_url(url)
    if not (url == plan["first_url"] or re.fullmatch(
            r"https://www\.sec\.gov/Archives/edgar/data/1474432/[0-9]{18}/[A-Za-z0-9_.-]+", url)):
        raise ValueError("Only the assigned primary issuer endpoints are allowed")
    directory = HERE / "captures"
    directory.mkdir(parents=True, exist_ok=True)
    reservations = tuple(directory.glob("attempt-*.reserved.json"))
    if len(reservations) >= plan["max_http_transports"]:
        raise ValueError("Six-transport research cap is exhausted")
    attempt = len(reservations) + 1
    reservation = {"attempt": attempt, "requested_url": url, "started_at": now(), "status": "REQUEST_RESERVED",
                   "method": "GET", "timeout_seconds": plan["timeout_seconds"], "max_bytes": plan["max_response_bytes"],
                   "redirects_allowed": False, "request_plan_sha256": hashlib.sha256(plan_body).hexdigest(),
                   "research_only": True, "app_quota_managed_live_claimed": False,
                   "historical_request_ledger_reconstructed": False, "access_values_exported": False}
    prefix = directory / f"attempt-{attempt:02d}"
    with prefix.with_suffix(".reserved.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(reservation, sort_keys=True, indent=2) + "\n")
    result = dict(reservation)
    raw = None
    began = time.monotonic()
    try:
        response = build_opener(NoRedirect()).open(Request(url, headers={"User-Agent": PROJECT, "Accept-Encoding": "identity"}),
                                                  timeout=plan["timeout_seconds"])
        with response:
            result.update(http_status=response.status, final_url=response.geturl(), response_headers={
                key: _safe_header(response.headers.get(key)) for key in ("Date", "Last-Modified", "Content-Type", "Content-Length", "Content-Encoding")})
            raw = response.read(plan["max_response_bytes"] + 1)
            content_encoding = response.headers.get("Content-Encoding", "identity")
        result.update(observed_at=now(), bytes=len(raw), raw_sha256=hashlib.sha256(raw).hexdigest())
        if (result["http_status"] != 200 or result["final_url"] != url or not raw
                or len(raw) > plan["max_response_bytes"] or time.monotonic()-began > plan["timeout_seconds"]):
            result["status"] = "WITHHELD_UNSUPPORTED_OR_INCOMPLETE_RESPONSE"
        elif content_encoding.casefold() != "identity":
            result["status"] = "WITHHELD_ENCODED_BODY"
        elif _has_sensitive_body(raw):
            result["status"] = "WITHHELD_SENSITIVE_BODY"
        else:
            path = directory / (result["raw_sha256"] + ".bin")
            if path.exists():
                if path.read_bytes() != raw:
                    raise ValueError("Original-byte digest collision")
            else:
                with path.open("xb") as stream:
                    stream.write(raw)
            result.update(status="PUBLIC_ORIGINAL_BYTES_CAPTURED", body_path=path.name)
    except Exception as error:
        result.update(status="CAPTURE_FAILED", observed_at=now(), error_type=type(error).__name__)
        if isinstance(error, HTTPError):
            result["http_status"] = error.code
    finally:
        raw = None
        result.setdefault("observed_at", now())
        result["elapsed_seconds"] = time.monotonic()-began
        with prefix.with_suffix(".metadata.json").open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, sort_keys=True, indent=2) + "\n")
        print(json.dumps({"attempt": attempt, "status": result["status"], "http_status": result.get("http_status"),
                          "bytes": result.get("bytes"), "metadata": prefix.name+".metadata.json"}))


if __name__ == "__main__":
    if not 1 <= len(sys.argv[1:]) <= 6:
        raise SystemExit("Select at most six exact primary URLs")
    for selected in sys.argv[1:]:
        capture(selected)
