"""At most six specific ordinary SEC requests; preserve original publisher bytes.

Full bodies containing access values are withheld with their digest/metadata.
No encoding, stripping, compression or replacement body is used for withheld
responses. The application contact guard is unchanged; no contact is invented.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.error import HTTPError
from urllib.request import Request, build_opener

from market_checker_app.collectors.sec_edgar_client import _SecRedirectPolicy, _allowed_sec_url

HERE = Path(__file__).resolve().parent
MAX_REQUESTS = 6
MAX_BYTES = 4_000_000
PROJECT = "JOHNY-SKORE primary-evidence-research/1.0 (public repository: littleleg198602/JOHNY-SKORE)"


def now():
    return datetime.now(timezone.utc).isoformat()


def capture(url):
    _allowed_sec_url(url)
    directory = HERE / "captures"
    directory.mkdir(parents=True, exist_ok=True)
    if len(list(directory.glob("*.metadata.json"))) >= MAX_REQUESTS:
        raise ValueError("New primary request budget exhausted")
    key = hashlib.sha256(url.encode()).hexdigest()
    output = directory / f"{key}.metadata.json"
    if output.exists():
        raise ValueError("Do not repeat or replace an existing exact capture")
    metadata = {"requested_url": url, "started_at": now(), "max_attempts": 1,
                "max_bytes": MAX_BYTES, "method": "GET", "access_values_exported": False}
    try:
        with build_opener(_SecRedirectPolicy()).open(Request(url, headers={
            "User-Agent": PROJECT, "Accept-Encoding": "identity",
        }), timeout=30) as response:
            metadata.update(http_status=response.status, final_url=response.geturl(),
                            response_headers={key: response.headers.get(key) for key in
                                              ("Date", "Last-Modified", "Content-Type", "Content-Length")})
            if response.geturl() != url:
                raise ValueError("Exact requested SEC source changed its URL")
            raw = response.read(MAX_BYTES + 1)
        digest = hashlib.sha256(raw).hexdigest()
        metadata.update(observed_at=now(), bytes=len(raw), raw_sha256=digest)
        if not raw or len(raw) > MAX_BYTES:
            metadata["status"] = "WITHHELD_EMPTY_OR_OVERSIZED_BODY"
            return
        # Do not print matches or store an altered substitute body.
        has_access_value = bool(re.search(
            rb'(?i)(?:api[_-]?key|access[_-]?token|authorization|bearer)\s*[=:]\s*["\x27]?[^\s"\x27<>]{8,}', raw))
        if has_access_value:
            metadata["status"] = "WITHHELD_ACCESS_VALUE_BODY"
            return
        body_path = directory / f"{digest}.bin"
        if body_path.exists():
            if body_path.read_bytes() != raw:
                raise ValueError("Exact raw body digest collision")
        else:
            body_path.write_bytes(raw)
        metadata.update(status="PUBLIC_RAW_CAPTURED", body_path=body_path.name)
    except Exception as error:
        metadata.update(status="CAPTURE_FAILED", observed_at=now(), error_type=type(error).__name__)
        if isinstance(error, HTTPError):
            metadata["http_status"] = error.code
        raise
    finally:
        metadata.setdefault("observed_at", now())
        with output.open("x", encoding="utf-8") as stream:
            json.dump(metadata, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
        print(json.dumps({"metadata": output.name, "status": metadata["status"],
                          "http_status": metadata.get("http_status"), "bytes": metadata.get("bytes")}))


if __name__ == "__main__":
    if not 1 <= len(sys.argv[1:]) <= MAX_REQUESTS:
        raise SystemExit("One to six specific SEC URLs")
    for url in sys.argv[1:]:
        capture(url)
