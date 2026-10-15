"""One explicitly reserved SEC research hop; never a healthcare collector.

The URL comes from the original retained Pfizer 2017 Form10K link. The whole
body is bounded before transport, never redirected, and never rewritten.
Metadata is reserved before the request, so an interruption cannot reset it.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from market_checker_app.services.ir_primary_scout_service import IRPrimaryHTTPClient, _has_sensitive_body, _safe_header

ROOT = Path(__file__).resolve().parent
URL = "https://www.sec.gov/Archives/edgar/data/78003/000007800318000027/pfe-exhibit21x12312017.htm"
METADATA = ROOT / "sec_pfe_2017_ex21.metadata.json"
BODY = ROOT / "sec_pfe_2017_ex21.body.html"
BYTE_CAP = 4_000_000


def main():
    if METADATA.exists():
        print(json.dumps({"new_requests": 0, "reservation_already_exists": True,
                          "status": json.loads(METADATA.read_bytes())["status"]}))
        return
    started = datetime.now(timezone.utc)
    meta = {"schema_version": 1, "capture_form": "ORIGINAL_HTTP_RESPONSE",
            "acquisition_method": "urllib.request raw HTTP body", "request_url": URL, "response_url": URL,
            "requested_at": started.isoformat(), "status": "RESEARCH_ATTEMPT_RESERVED",
            "reserved_at": started.isoformat(), "reserved_before_transport": True,
            "research_attempt_number": 1, "authorized_sec_research_attempt_cap": 4,
            "byte_budget": BYTE_CAP, "raw_publication_permitted": False, "body_path": None,
            "healthcare_collector_request": False, "new_or_reconstructed_quota_database": False,
            "discovered_from": "evidence/batch3_healthcare_20261012/captures/sec_pfe_2017_10k.metadata.json"}
    # Durable local authorization reservation, not a reconstruction of old quota.
    with METADATA.open("x") as stream:
        stream.write(json.dumps(meta, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        response = IRPrimaryHTTPClient().fetch(URL, timeout=20, max_bytes=BYTE_CAP)
        safe = not _has_sensitive_body(response.body)
        meta.update(http_status=response.status, observed_at=response.observed_at.isoformat(),
                    body_bytes=len(response.body), body_sha256=hashlib.sha256(response.body).hexdigest(),
                    body_complete_within_byte_budget=True, raw_publication_permitted=safe,
                    http_date=_safe_header(response.http_date), last_modified=_safe_header(response.last_modified),
                    status=("HTTP_200_COMPLETE" if response.status == 200 else "HTTP_NON200_COMPLETE")
                           if safe else "WITHHELD_SENSITIVE_WHOLE_BODY",
                    body_path=BODY.name if safe else None)
        if safe:
            BODY.write_bytes(response.body)
    except Exception as error:
        # Access values can occur in error messages. Keep the type, never text/body.
        meta.update(http_status=None, observed_at=datetime.now(timezone.utc).isoformat(),
                    status="SOURCE_CAPTURE_UNAVAILABLE", body_complete_within_byte_budget=False,
                    error_type=type(error).__name__)
    METADATA.write_text(json.dumps(meta, indent=2) + "\n")
    print(json.dumps({"new_requests": 1, "status": meta["status"], "http_status": meta["http_status"],
                      "body_bytes": meta.get("body_bytes"), "body_sha256": meta.get("body_sha256")}))


if __name__ == "__main__":
    main()
