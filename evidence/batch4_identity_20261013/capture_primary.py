"""Bounded ordinary capture for the three transition-review investigations.

No contact email is invented; the application's configured-contact guard is
unchanged. This research capture identifies the existing public project,
archives complete raw public responses, and stops on access/rate rejection.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.request import Request, build_opener

from market_checker_app.collectors.sec_edgar_client import _SecRedirectPolicy, _allowed_sec_url
from market_checker_app.services.sec13f_identity_bulk import MAX_SOURCE_BYTES

DIRECTORY = Path(__file__).resolve().parent
PROJECT_IDENTIFIER = (
    "JOHNY-SKORE primary-evidence-research/1.0 "
    "(public repository: littleleg198602/JOHNY-SKORE)"
)


def now():
    return datetime.now(timezone.utc).isoformat()


def capture(url):
    _allowed_sec_url(url)
    key = hashlib.sha256(url.encode()).hexdigest()
    receipt = DIRECTORY / "captures" / f"{key}.json"
    if receipt.exists():
        raise ValueError("Preserve the prior capture; no repeat request for this URL")
    receipt.parent.mkdir(parents=True, exist_ok=True)
    record = {"requested_url": url, "started_at": now(), "max_bytes": MAX_SOURCE_BYTES,
              "max_attempts": 1, "publisher_publication_time": None,
              "last_modified_is_publication_time": False}
    try:
        with build_opener(_SecRedirectPolicy()).open(Request(url, headers={
            "User-Agent": PROJECT_IDENTIFIER, "Accept-Encoding": "identity",
        }), timeout=30) as response:
            if response.geturl() != url:
                raise ValueError("Source changed the exact requested URL")
            record.update(http_status=response.status, final_url=response.geturl(),
                          response_headers={key: response.headers.get(key) for key in
                                            ("Date", "Last-Modified", "Content-Type", "Content-Length")})
            payload = response.read(MAX_SOURCE_BYTES + 1)
        if not payload or len(payload) > MAX_SOURCE_BYTES:
            raise ValueError("Empty or oversized capture")
        digest = hashlib.sha256(payload).hexdigest()
        raw_path = receipt.with_name(f"{digest}.bin")
        if raw_path.exists():
            if raw_path.read_bytes() != payload:
                raise ValueError("Immutable raw digest collision")
        else:
            with raw_path.open("xb") as stream:
                stream.write(payload)
        record.update(status="RAW_CAPTURED", bytes=len(payload), sha256=digest,
                      raw_file=raw_path.name)
    except Exception as error:
        record.update(status="CAPTURE_FAILED", error_type=type(error).__name__)
        if isinstance(error, HTTPError):
            record["http_status"] = error.code
        raise
    finally:
        record["observed_at"] = now()
        with receipt.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
    print(json.dumps({"receipt": str(receipt.relative_to(DIRECTORY)), "url": url,
                      "bytes": record["bytes"], "sha256": record["sha256"]}))


if __name__ == "__main__":
    if not 1 <= len(sys.argv[1:]) <= 4:
        raise SystemExit("One to four specific SEC URLs per invocation")
    for url in sys.argv[1:]:
        capture(url)
