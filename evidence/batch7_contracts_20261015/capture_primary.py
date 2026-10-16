"""At most four pre-reserved SEC research GETs, never USAspending collection."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from market_checker_app.services.sec_operational_metrics_review import _sensitive


DIRECTORY = Path(__file__).resolve().parent
MAX_ATTEMPTS = 4
MAX_BYTES = 4_000_000
USER_AGENT = "JOHNY-SKORE primary-evidence-research/1.0 (public repository: littleleg198602/JOHNY-SKORE)"


def _now():
    return datetime.now(timezone.utc).isoformat()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "Redirect blocked before a second source operation", headers, fp)


def capture(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in {"www.sec.gov", "data.sec.gov"}
            or parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not (re.fullmatch(r"/submissions/CIK[0-9]{10}\.json", parsed.path)
                    or re.fullmatch(r"/Archives/edgar/data/[0-9]+/[0-9]{18}/[A-Za-z0-9_.-]+", parsed.path))):
        raise ValueError("Exact bounded primary SEC reference required")
    requests, raw = DIRECTORY / "requests", DIRECTORY / "raw"
    requests.mkdir(exist_ok=True)
    raw.mkdir(exist_ok=True)
    if len(list(requests.glob("*.reserved.json"))) >= MAX_ATTEMPTS:
        raise ValueError("Explicit four-attempt SEC research budget exhausted")
    key = hashlib.sha256(url.encode()).hexdigest()
    reserved = requests / f"{key}.reserved.json"
    completed = requests / f"{key}.metadata.json"
    meta = {"requested_url": url, "method": "GET", "started_at": _now(), "max_bytes": MAX_BYTES,
            "max_attempts": MAX_ATTEMPTS, "automatic_redirects": False, "status": "RESERVED",
            "origin": "LIVE_PRIMARY_RESEARCH_HTTP", "quota_scope": "EXPLICIT_BATCH7_SEC_RESEARCH_ONLY",
            "usaspending_requests": 0, "public_raw_available": False}
    with reserved.open("x", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    body = b""
    try:
        deadline = time.monotonic() + 30
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}, method="GET")
        with build_opener(_NoRedirect()).open(request, timeout=30) as response:
            meta.update(http_status=response.status, final_url=response.geturl(),
                        response_headers={key: response.headers.get(key) for key in
                                          ("Date", "Last-Modified", "Content-Type", "Content-Length", "Content-Encoding")})
            if response.geturl() != url:
                raise ValueError("Exact primary source URL changed")
            parts, size = [], 0
            while size <= MAX_BYTES:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Whole response deadline exhausted")
                chunk = response.read(min(65536, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                parts.append(chunk)
                size += len(chunk)
            body = b"".join(parts)
        meta["observed_at"] = _now()
        length = meta["response_headers"].get("Content-Length")
        if len(body) > MAX_BYTES:
            meta.update(status="WITHHELD_OVERSIZED_RESPONSE", received_prefix_bytes=len(body),
                        received_prefix_sha256=hashlib.sha256(body).hexdigest())
        elif meta["response_headers"].get("Content-Encoding") not in (None, "identity"):
            meta.update(status="WITHHELD_UNSUPPORTED_RESPONSE_ENCODING")
        elif response.status != 200 or not body or (length is not None and int(length) != len(body)):
            meta.update(status="INCOMPLETE_OR_UNSUCCESSFUL_RESPONSE")
        else:
            digest = hashlib.sha256(body).hexdigest()
            meta.update(raw_response_bytes=len(body), raw_response_sha256=digest, complete_response=True)
            if _sensitive(body):
                meta.update(status="WITHHELD_ACCESS_VALUE_WHOLE_BODY")
            else:
                path = raw / f"body-{digest}.response"
                if path.exists():
                    assert path.read_bytes() == body
                else:
                    with path.open("xb") as handle:
                        handle.write(body)
                meta.update(status="CAPTURED", public_raw_available=True,
                            archive_path=str(path.relative_to(DIRECTORY.parents[1])))
    except Exception as error:
        meta.update(status="CAPTURE_FAILED", observed_at=_now(), error_type=type(error).__name__)
        if isinstance(error, HTTPError):
            meta["http_status"] = error.code
            if 300 <= error.code < 400:
                meta["status"] = "REDIRECT_BLOCKED"
    finally:
        meta.setdefault("observed_at", _now())
        with completed.open("x", encoding="utf-8") as handle:
            json.dump(meta, handle, indent=2, sort_keys=True)
            handle.write("\n")
        print(json.dumps({"metadata": str(completed.relative_to(DIRECTORY.parents[1])), "status": meta["status"],
                          "http_status": meta.get("http_status"), "bytes": meta.get("raw_response_bytes")}))
    return meta


if __name__ == "__main__":
    if not 1 <= len(sys.argv[1:]) <= MAX_ATTEMPTS:
        raise SystemExit("One to four explicit primary SEC references")
    for url in sys.argv[1:]:
        capture(url)
