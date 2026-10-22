"""One reserved transport per selected, literal primary research URL; no database."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from market_checker_app.collectors.sec_edgar_client import _allowed_sec_url
from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body, _safe_header

HERE = Path(__file__).resolve().parent
WITHHELD = {
    "84b6ad89bee343819008e22babdf9a87dbfd3034a99022ea68320f0f17edf8b3",
    "deb5642d450c0fd60cd12066a42da8c1905f4fd47562afcef06cf8e428bcbeb1",
    "5130e6c90a0770d32e40f5eafcc87cd980d963934ae6c125963d8b1f2c20eac1",
    "27f03d6bb63f0590a82369556419c4eb8f5fb9ab3b3da44a9c8695d18d7b7532",
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def now():
    return datetime.now(timezone.utc).isoformat()


def expired(_signal, _frame):
    raise TimeoutError("Declared whole-response deadline exceeded")


def validate_url(url):
    parsed = urlsplit(url)
    assert parsed.scheme == "https" and not parsed.query and not parsed.fragment
    assert not parsed.username and not parsed.password and parsed.port is None
    if parsed.hostname == "www.sec.gov":
        _allowed_sec_url(url)
    else:
        assert parsed.hostname == "www.accessdata.fda.gov"
        assert parsed.path.startswith("/drugsatfda_docs/") and parsed.path.endswith(".pdf")


def capture(row, plan_bytes):
    key, url = row["key"], row["url"]
    validate_url(url)
    directory = HERE / "primary"
    directory.mkdir(exist_ok=True)
    reservation = {"key": key, "requested_url": url, "method": "GET", "started_at": now(),
        "request_plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "max_response_bytes": row["max_response_bytes"], "timeout_seconds": 25,
        "research_only": True, "collector_api_used": False, "retry": False, "redirect": False,
        "fallback": False, "quota_database_created_or_modified": False, "status": "REQUEST_RESERVED"}
    with (directory / f"{key}.reserved.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(reservation, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    result = dict(reservation)
    raw = None
    started = time.monotonic()
    previous = signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, 25)
        request = Request(url, headers={
            "User-Agent": "JOHNY-SKORE primary-evidence-research/1.0 (public repository: littleleg198602/JOHNY-SKORE)",
            "Accept-Encoding": "identity"})
        with build_opener(NoRedirect()).open(request, timeout=25) as response:
            result.update(http_status=response.status, final_url=response.geturl(), response_headers={
                name: _safe_header(response.headers.get(name)) for name in
                ("Date", "Last-Modified", "Content-Type", "Content-Length", "Content-Encoding")})
            raw = response.read(row["max_response_bytes"] + 1)
            encoding = response.headers.get("Content-Encoding", "identity")
            length = response.headers.get("Content-Length")
        signal.setitimer(signal.ITIMER_REAL, 0)
        result.update(observed_at=now(), bytes=len(raw), raw_sha256=hashlib.sha256(raw).hexdigest(),
            transport_elapsed_seconds=time.monotonic() - started)
        if (result["http_status"] != 200 or result["final_url"] != url or not raw
                or len(raw) > row["max_response_bytes"] or time.monotonic() - started > 25
                or length is not None and int(length) != len(raw) or encoding.casefold() != "identity"):
            result["status"] = "WITHHELD_INCOMPLETE_OR_UNSUPPORTED_RESPONSE"
        elif result["raw_sha256"] in WITHHELD or _has_sensitive_body(raw):
            result["status"] = "WITHHELD_WHOLE_SENSITIVE_BODY"
        else:
            if row["suffix"] == ".pdf":
                assert raw.startswith(b"%PDF-")
                decoded = subprocess.run(["pdftotext", "-layout", "-", "-"], input=raw,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=15).stdout
                result.update(derived_pdf_text_bytes=len(decoded),
                    derived_pdf_text_sha256=hashlib.sha256(decoded).hexdigest(),
                    decoded_text_is_not_pixel_authentication=True)
                if not decoded.strip() or _has_sensitive_body(decoded):
                    result["status"] = "WITHHELD_WHOLE_PDF_UNREVIEWABLE_OR_SENSITIVE"
                    return
            name = key + row["suffix"]
            with (directory / name).open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            result.update(status="PUBLIC_ORIGINAL_BYTES_CAPTURED", body_path=name,
                retention="ORIGINAL_BYTES", body_transformed=False, public_raw_available=True,
                original_sensitive_body_gate_passed=True, known_at=now(), source_publication_time=None)
    except Exception as error:
        result.update(status="CAPTURE_FAILED", error_type=type(error).__name__, observed_at=now())
        if isinstance(error, HTTPError):
            result["http_status"] = error.code
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        raw = None
        result.setdefault("observed_at", now())
        result["elapsed_seconds"] = time.monotonic() - started
        with (directory / f"{key}.metadata.json").open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        print(json.dumps({k: result.get(k) for k in
            ("key", "status", "http_status", "bytes", "raw_sha256", "observed_at")}))


if __name__ == "__main__":
    plan_bytes = (HERE / "request_plan.json").read_bytes()
    plan = json.loads(plan_bytes)
    assert plan["max_http_transports"] == len(plan["urls"]) == 5
    assert sum(row["max_response_bytes"] for row in plan["urls"]) == 10_256_000
    assert plan["retry"] is plan["redirect"] is plan["fallback"] is False
    assert len({row["url"] for row in plan["urls"]}) == 5
    for row in plan["urls"]:
        capture(row, plan_bytes)
