"""Bounded primary research hop journal, not a collector or quota database."""
from datetime import datetime, timezone
import hashlib
from html import unescape
from io import BytesIO
import json
from pathlib import Path
import re
import sys
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from market_checker_app.services.doj_enforcement_scope_review import _SENSITIVE

FOLDER = Path(__file__).resolve().parent
LIMIT = 4_000_000


def sensitive(body):
    text = unescape(body.decode("utf-8", errors="replace"))
    return (_SENSITIVE.search(text.encode()) or re.search(
        r'(?:passcode|meeting password)\s*[:=]\s*\S+|[?&](?:pwd|password|api_key|access_token)=[^\s"\'<>]+|'
        r'["\'](?:csrf_token|licenseKey|subscriptionKey|sitekey)["\']\s*:\s*["\'][^"\']{8,}', text, re.I))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def capture(identifier, url):
    plan_path = FOLDER / "research_plan.json"
    plan = json.loads(plan_path.read_text())
    parsed = urlsplit(url)
    if (not re.fullmatch(r"[a-z0-9_]{1,70}", identifier) or parsed.scheme != "https"
            or parsed.hostname not in plan["permitted_primary_hosts"]
            or parsed.username or parsed.password or parsed.port not in {None, 443}
            or parsed.fragment or sensitive(url.encode())):
        raise ValueError("Exact safe permitted primary document URL required")
    reservations, raw = FOLDER / "reservations", FOLDER / "raw"
    reservations.mkdir(exist_ok=True)
    raw.mkdir(exist_ok=True)
    if (raw / (identifier + ".metadata.json")).exists():
        raise ValueError("An earlier capture exists; no retry or replacement")
    existing = list(reservations.glob("*.json"))
    if len(existing) >= plan["maximum_primary_capture_transports"]:
        raise ValueError("Precommitted six-hop research cap exhausted")
    started = datetime.now(timezone.utc).isoformat()
    reservation = {"sequence": len(existing) + 1, "capture_id": identifier,
        "requested_url": url, "reserved_at": started, "maximum_bytes": LIMIT, "timeout_seconds": 25,
        "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(), "automatic_redirects": False}
    with (reservations / f"{len(existing)+1:02d}_{identifier}.json").open("x") as stream:
        json.dump(reservation, stream, indent=2, sort_keys=True)
        stream.write("\n")
    meta = {**reservation, "started_at": started, "origin": "LIVE_PRIMARY_RESEARCH_HTTP",
            "public_raw_available": False, "archive_path": None, "raw_response_sha256": None,
            "raw_response_bytes": None}
    try:
        try:
            response = build_opener(NoRedirect()).open(Request(url, headers={
                "Accept": "application/pdf,text/html;q=0.9", "Accept-Encoding": "identity",
                "User-Agent": "JOHNY-SKORE bounded public primary review/1.0"}), timeout=25)
        except HTTPError as error:
            response = error
        with response:
            body = response.read(LIMIT + 1)
            meta.update(http_status=response.getcode(), final_url=response.geturl(),
                        content_type=response.headers.get("Content-Type", ""),
                        content_encoding=response.headers.get("Content-Encoding", "identity"))
        meta["observed_at"] = datetime.now(timezone.utc).isoformat()
        if len(body) > LIMIT:
            meta.update(status="WITHHELD_INCOMPLETE_OVERSIZE_RESPONSE", captured_prefix_sha256=hashlib.sha256(body).hexdigest())
        else:
            meta.update(raw_response_sha256=hashlib.sha256(body).hexdigest(), raw_response_bytes=len(body))
            reason = "WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE" if sensitive(body) else None
            if meta["content_encoding"].lower() not in {"", "identity"}:
                reason = reason or "WITHHELD_UNINSPECTED_CONTENT_ENCODING"
            if body.startswith(b"%PDF-") and reason is None:
                try:
                    from pypdf import PdfReader
                    reader = PdfReader(BytesIO(body), strict=True)
                    if reader.is_encrypted or not 1 <= len(reader.pages) <= 120:
                        raise ValueError("PDF inspection bound")
                    total = 0
                    for page in reader.pages:
                        text = page.extract_text() or ""
                        total += len(text)
                        if total > 2_000_000:
                            raise ValueError("PDF text inspection bound")
                        if sensitive(text.encode()):
                            reason = "WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE"
                            break
                    meta.update(pdf_pages_inspected=len(reader.pages), pdf_decoded_characters=total)
                except Exception:
                    reason = "WITHHELD_UNINSPECTED_PDF"
            if reason:
                meta["status"] = reason
            else:
                suffix = ".pdf" if body.startswith(b"%PDF-") else ".html"
                archive = raw / (identifier + suffix)
                with archive.open("xb") as stream:
                    stream.write(body)
                meta.update(status="ORIGINAL_BYTES_CAPTURED", public_raw_available=True,
                            archive_path=archive.relative_to(FOLDER.parents[1]).as_posix())
    except (OSError, ValueError) as error:
        meta.update(observed_at=datetime.now(timezone.utc).isoformat(), status="TRANSPORT_OR_CAPTURE_ERROR",
                    error_type=type(error).__name__)
    with (raw / (identifier + ".metadata.json")).open("x") as stream:
        json.dump(meta, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({k: meta.get(k) for k in ("sequence", "capture_id", "http_status", "status",
                     "raw_response_bytes", "raw_response_sha256", "observed_at")}))


if __name__ == "__main__":
    capture(*sys.argv[1:])
