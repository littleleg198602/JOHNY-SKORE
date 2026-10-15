"""One-off bounded original-byte SEC research; not an app collector ledger."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from market_checker_app.collectors.sec_edgar_client import _allowed_sec_url
from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body, _safe_header

HERE = Path(__file__).resolve().parent
MAX_REQUESTS, MAX_BYTES = 6, 4_000_000
PROJECT = "JOHNY-SKORE primary-evidence-research/1.0 (public repository: littleleg198602/JOHNY-SKORE)"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise HTTPError(request.full_url, code, "Uncharged redirects are forbidden", headers, fp)


def now():
    return datetime.now(timezone.utc).isoformat()


def capture(url):
    _allowed_sec_url(url)
    if re.fullmatch(r"https://www\.sec\.gov/Archives/edgar/data/(?:58492|1206264)/[0-9]{18}/[A-Za-z0-9_.-]+", url) is None:
        raise ValueError("Only exact assigned LEG/SGI primary filing endpoints are allowed")
    directory = HERE / "captures"
    directory.mkdir(parents=True, exist_ok=True)
    if len(list(directory.glob("*.metadata.json"))) >= MAX_REQUESTS:
        raise ValueError("The six-attempt research budget is exhausted")
    output = directory / (hashlib.sha256(url.encode()).hexdigest() + ".metadata.json")
    metadata = {"requested_url": url, "started_at": now(), "status": "REQUEST_RESERVED", "method": "GET",
                "attempts": 1, "max_bytes": MAX_BYTES, "redirects_allowed": False, "access_values_exported": False,
                "research_only": True, "app_quota_managed_live_claimed": False,
                "historical_request_ledger_reconstructed": False}
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(metadata, sort_keys=True, indent=2) + "\n")
    try:
        with build_opener(NoRedirect()).open(Request(url, headers={"User-Agent": PROJECT, "Accept-Encoding": "identity"}),
                                            timeout=30) as response:
            metadata.update(http_status=response.status, final_url=response.geturl(),
                            response_headers={key: _safe_header(response.headers.get(key)) for key in
                                              ("Date", "Last-Modified", "Content-Type", "Content-Length", "Content-Encoding")})
            raw = response.read(MAX_BYTES + 1)
        metadata.update(observed_at=now(), bytes=len(raw), raw_sha256=hashlib.sha256(raw).hexdigest())
        if metadata["final_url"] != url or metadata["http_status"] != 200 or not raw or len(raw) > MAX_BYTES:
            metadata["status"] = "WITHHELD_UNSUPPORTED_OR_INCOMPLETE_RESPONSE"
        elif response.headers.get("Content-Encoding", "identity").casefold() != "identity":
            metadata["status"] = "WITHHELD_ENCODED_BODY"
        elif _has_sensitive_body(raw):
            metadata["status"] = "WITHHELD_SENSITIVE_BODY"
        else:
            path = directory / (metadata["raw_sha256"] + ".bin")
            if path.exists():
                if path.read_bytes() != raw:
                    raise ValueError("Original-byte digest collision")
            else:
                with path.open("xb") as stream:
                    stream.write(raw)
            metadata.update(status="PUBLIC_ORIGINAL_BYTES_CAPTURED", body_path=path.name)
    except Exception as error:
        metadata.update(status="CAPTURE_FAILED", observed_at=now(), error_type=type(error).__name__)
        if isinstance(error, HTTPError):
            metadata["http_status"] = error.code
    finally:
        metadata.setdefault("observed_at", now())
        output.write_text(json.dumps(metadata, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": metadata["status"], "http_status": metadata.get("http_status"),
                          "bytes": metadata.get("bytes"), "metadata": output.name}))


if __name__ == "__main__":
    if not 1 <= len(sys.argv[1:]) <= MAX_REQUESTS:
        raise SystemExit("Supply one to six genuinely new exact assigned primary URLs")
    for url in sys.argv[1:]:
        capture(url)
