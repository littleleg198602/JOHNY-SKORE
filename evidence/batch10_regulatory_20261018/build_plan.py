"""Prepare two retained original-PDF descriptors; no source or database I/O."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent
SOURCE = ROOT / "evidence/batch8_regulatory_20261015/captured_review_plan.json"


def main():
    old = json.loads(SOURCE.read_text())
    documents = [{"capture_id": "doj_opposition_pdf", "document_number": "106", "filed_on": "2024-09-12", "document_kind": "PARTY_OPPOSITION"},
                 {"capture_id": "govinfo_case_opinion", "document_number": "283", "filed_on": "2025-06-30", "document_kind": "COURT_OPINION"}]
    captures = {}
    for document in documents:
        identifier = document["capture_id"]
        descriptor = old["captures"][identifier]
        body = (ROOT / descriptor["body_ref"]["path"]).read_bytes()
        assert len(body) == descriptor["body_ref"]["bytes"]
        assert hashlib.sha256(body).hexdigest() == descriptor["body_ref"]["sha256"]
        assert descriptor["metadata"]["public_raw_available"] is True
        captures[identifier] = descriptor
    request = {"case_docket": "2:24-cv-04055", "court": "UNITED STATES DISTRICT COURT DISTRICT OF NEW JERSEY", "documents": documents}
    plan = {"schema_version": "captured_identity_review_plan_v1", "kind": "DOJ_CASE_CAPTION", "request": request, "captures": captures}
    for name, value in (("request.json", request), ("actual_review_plan.json", plan), ("input_scope.json", {
            "written_at": datetime.now(timezone.utc).isoformat(),
            "source_plan_path": SOURCE.relative_to(ROOT).as_posix(), "source_plan_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            "whole_original_documents": 2, "expected_whole_pdf_pages": 95, "new_http_requests": 0,
            "database_connections_or_writes": 0, "capture_clock_rewritten": False, "publisher_bytes_copied_or_modified": False,
            "financial_issuer_identity_or_registry_adoption_requested": False})):
        with (FOLDER / name).open("x") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
    print(json.dumps({"kind": "DOJ_CASE_CAPTION", "documents": 2, "new_source_requests": 0}))


if __name__ == "__main__":
    main()
