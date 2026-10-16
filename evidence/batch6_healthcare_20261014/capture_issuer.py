"""At most one explicit SEC primary research request, never a CMS operation.

An existing metadata file is immutable and prevents a second request. No quota
store is constructed, copied or reset. An unsafe whole body is never saved.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from market_checker_app.services.ir_primary_scout_service import (
    IRPrimaryHTTPClient, _has_sensitive_body, _safe_header,
)


ROOT = Path(__file__).resolve().parent
URL = "https://data.sec.gov/submissions/CIK0000860730.json"
METADATA_PATH = ROOT / "sec_hca_submissions.metadata.json"
BODY_PATH = ROOT / "sec_hca_submissions.body.json"


def main():
    if METADATA_PATH.exists():
        meta = json.loads(METADATA_PATH.read_bytes())
        print(json.dumps({"reused_original_capture": True, "status": meta["status"],
                          "body_sha256": meta.get("body_sha256"), "new_source_requests": 0}))
        return
    requested = datetime.now(timezone.utc)
    meta = {"schema_version": 1, "capture_form": "ORIGINAL_HTTP_RESPONSE",
            "requested_url": URL, "response_url": URL, "requested_at": requested.isoformat(),
            "research_request_allowance": 4, "attempts_in_this_capture": 1,
            "request_purpose": "BOUNDED_INDEPENDENT_SEC_PRIMARY_ISSUER_RESEARCH",
            "cms_or_clinical_request": False, "new_quota_store_created": False}
    try:
        response = IRPrimaryHTTPClient().fetch(URL, timeout=20, max_bytes=8 * 1024 * 1024)
        body = response.body
        safe = not _has_sensitive_body(body)
        meta.update(http_status=response.status, body_bytes=len(body),
                    body_sha256=hashlib.sha256(body).hexdigest(),
                    observed_at=response.observed_at.isoformat(), complete_response_body=True,
                    http_date=_safe_header(response.http_date),
                    last_modified=_safe_header(response.last_modified),
                    content_type=_safe_header(response.content_type),
                    raw_publication_permitted=safe,
                    status=("HTTP_200_COMPLETE" if response.status == 200 else "HTTP_NON200_COMPLETE")
                           if safe else "WITHHELD_SENSITIVE_WHOLE_BODY",
                    body_path=BODY_PATH.name if safe else None)
        if safe:
            BODY_PATH.write_bytes(body)
    except Exception as error:
        # The exception body/message may contain access values. Retain type only.
        meta.update(observed_at=datetime.now(timezone.utc).isoformat(), http_status=None,
                    complete_response_body=False, raw_publication_permitted=False,
                    body_path=None, status="SOURCE_CAPTURE_UNAVAILABLE", error_type=type(error).__name__)
    METADATA_PATH.write_text(json.dumps(meta, indent=2) + "\n")
    print(json.dumps({"status": meta["status"], "http_status": meta["http_status"],
                      "body_sha256": meta.get("body_sha256"), "body_bytes": meta.get("body_bytes"),
                      "new_source_requests": 1}))


if __name__ == "__main__":
    main()
