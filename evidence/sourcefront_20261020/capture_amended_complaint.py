"""One distinct DOJ primary research document, never a docket/Order retry."""
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pypdf import PdfReader
from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body, _safe_header

HERE = Path(__file__).resolve().parent
URL = "https://www.justice.gov/atr/media/1358786/dl"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def now():
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    plan_bytes = (HERE / "amended_complaint_plan.json").read_bytes()
    plan = json.loads(plan_bytes)
    assert plan["url"] == URL and plan["max_http_transports"] == 1
    directory = HERE / "primary"
    result = {"key": "doj_first_amended_complaint", "requested_url": URL,
              "started_at": now(), "status": "REQUEST_RESERVED", "method": "GET",
              "request_plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
              "research_only": True, "collector_api_used": False,
              "app_quota_managed_live_claimed": False, "quota_database_created_or_modified": False,
              "retry": False, "redirect": False, "max_response_bytes": 4_000_000, "timeout_seconds": 25}
    with (directory / "doj_first_amended_complaint.reserved.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, indent=2) + "\n")
    started, raw = time.monotonic(), None
    try:
        request = Request(URL, headers={
            "User-Agent": "JOHNY-SKORE primary-evidence-research/1.0 (public repository: littleleg198602/JOHNY-SKORE)",
            "Accept-Encoding": "identity"})
        with build_opener(NoRedirect()).open(request, timeout=25) as response:
            result.update(http_status=response.status, final_url=response.geturl(), response_headers={
                key: _safe_header(response.headers.get(key)) for key in
                ("Date", "Last-Modified", "Content-Type", "Content-Length", "Content-Encoding")})
            raw = response.read(4_000_001)
            encoding, length = response.headers.get("Content-Encoding", "identity"), response.headers.get("Content-Length")
        result.update(observed_at=now(), bytes=len(raw), raw_sha256=hashlib.sha256(raw).hexdigest())
        if (result["http_status"] != 200 or result["final_url"] != URL or not raw.startswith(b"%PDF-")
                or len(raw) > 4_000_000 or time.monotonic() - started > 25
                or length is not None and int(length) != len(raw) or encoding.casefold() != "identity"):
            result["status"] = "WITHHELD_INCOMPLETE_OR_UNSUPPORTED_RESPONSE"
        else:
            reader = PdfReader(BytesIO(raw))
            if reader.is_encrypted or not 1 <= len(reader.pages) <= 120:
                raise ValueError("Unsupported complete original PDF")
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            if len(text) > 2_000_000 or _has_sensitive_body(raw) or _has_sensitive_body(text.encode()):
                result["status"] = "WITHHELD_WHOLE_UNSUPPORTED_OR_SENSITIVE_BODY"
            else:
                with (directory / "doj_first_amended_complaint.pdf").open("xb") as stream:
                    stream.write(raw)
                result.update(status="PUBLIC_ORIGINAL_BYTES_CAPTURED", body_path="doj_first_amended_complaint.pdf",
                              body_transformed=False, public_raw_available=True, original_page_count=len(reader.pages))
            text, reader = None, None
    except Exception as error:
        result.update(status="CAPTURE_FAILED", observed_at=now(), error_type=type(error).__name__)
        if isinstance(error, HTTPError):
            result["http_status"] = error.code
    finally:
        raw = None
        result.setdefault("observed_at", now())
        result["elapsed_seconds"] = time.monotonic() - started
        with (directory / "doj_first_amended_complaint.metadata.json").open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, indent=2) + "\n")
        print(json.dumps({k: result.get(k) for k in ("status", "http_status", "bytes", "raw_sha256", "observed_at")}))
