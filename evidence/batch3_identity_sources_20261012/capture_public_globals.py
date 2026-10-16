"""One bounded ordinary download per official SEC public global input.

This evidence script does not supply a fictitious EDGAR email or alter the
application's configured-client guard. A declared public project identifier
is used only for these two public files. No credentials, cookies, mirrors,
rate-limit retries or access-block workarounds are used.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener

from market_checker_app.collectors.sec_edgar_client import (
    SecEdgarClient, _SecRedirectPolicy,
)
from market_checker_app.sec13f_identity_bulk import capture_source
from market_checker_app.services.sec13f_identity_bulk import (
    MAX_SOURCE_BYTES, TICKER_URL, list_url,
)


PROJECT_IDENTIFIER = (
    "JOHNY-SKORE primary-evidence-research/1.0 "
    "(public repository: littleleg198602/JOHNY-SKORE)"
)
WORKSPACE = Path("evidence/batch3_identity_bulk_20261012")
ATTEMPTS = []


def now():
    return datetime.now(timezone.utc).isoformat()


def public_transport(url, headers, timeout):
    record = {"requested_url": url, "started_at": now()}
    ATTEMPTS.append(record)
    headers = {**headers, "Accept-Encoding": "identity"}
    try:
        with build_opener(_SecRedirectPolicy()).open(
            Request(url, headers=headers), timeout=timeout,
        ) as response:
            record.update({
                "http_status": response.status,
                "final_url": response.geturl(),
                "response_headers": {
                    key: response.headers.get(key)
                    for key in ("Date", "Last-Modified", "Content-Type", "Content-Length")
                },
            })
            if response.geturl() != url:
                raise ValueError("Public global input changed its exact official URL")
            payload = response.read(MAX_SOURCE_BYTES + 1)
        record.update({"observed_at": now(), "bytes": len(payload),
                       "sha256": hashlib.sha256(payload).hexdigest()})
        if not payload or len(payload) > MAX_SOURCE_BYTES:
            raise ValueError("Public global input is empty or exceeds the 4 MB bound")
        return payload
    except Exception as error:
        record.update({"observed_at": now(), "error_type": type(error).__name__,
                       "error": str(error)})
        if isinstance(error, HTTPError):
            record["http_status"] = error.code
        raise


def immutable_json(path, value):
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)


if __name__ == "__main__":
    if (WORKSPACE / "public_capture.json").exists():
        raise SystemExit("Capture already recorded; preserve the original attempt and snapshots")
    client = SecEdgarClient(
        user_agent=PROJECT_IDENTIFIER, text_transport=public_transport,
        timeout_seconds=30, min_request_interval_seconds=1, max_attempts=1,
    )
    sources = []
    status = "COMPLETE_TWO_PUBLIC_GLOBAL_INPUTS"
    try:
        for kind, url in (("ticker_catalog", TICKER_URL), ("13f_list", list_url("2026q3"))):
            source = capture_source(workspace=WORKSPACE, kind=kind, url=url, client=client)
            sources.append({"snapshot_id": source.snapshot_id, **source.metadata()})
    except Exception:
        status = "STOPPED_WITH_CAPTURE_ERROR"
        raise
    finally:
        immutable_json(WORKSPACE / "public_capture.json", {
            "schema_version": 1, "status": status, "completed_at": now(),
            "transport": "ordinary bounded urllib HTTPS with official SEC redirect policy",
            "user_agent": PROJECT_IDENTIFIER, "configured_edgar_guard_unchanged": True,
            "max_request_attempts": 1, "max_source_bytes": MAX_SOURCE_BYTES,
            "publisher_publication_time_known": False,
            "last_modified_is_publication_time": False,
            "attempts": ATTEMPTS, "sources": sources,
        })
    print(json.dumps({"status": status, "sources": sources}, indent=2))
