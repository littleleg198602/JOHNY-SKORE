"""Offline evidence reproducer for two exact, already captured FDA PDFs.

This is evidence tooling, not an application collector. It performs no HTTP or
SQL. Input bytes and capture metadata must match the two predeclared originals.
Derived text never authenticates pixels, legal ownership, or a digital signature.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess


DOCUMENTS = {
    "approval_2019": {
        "filename": "fda_trazimera_approval_2019.pdf", "bytes": 137546,
        "sha256": "2daf7bfa938057c13186e49ba716e87f9d819e2d9bdf5ee6c12f08bf0d6ee6c7",
        "pages": 10, "text_bytes": 19840,
        "text_sha256": "bfdd962c120f6f41fca44df5a3ea775f44de9c7f6a8870a0085937278bf23a66",
    },
    "label_2025": {
        "filename": "fda_trazimera_label_2025.pdf", "bytes": 2495404,
        "sha256": "ee258f711faf29cdd88ad2cf78bccc802d1d766bb9898447a68e00f713a5afb2",
        "pages": 36, "text_bytes": 142311,
        "text_sha256": "a68e47d19e58114e6f788b6cd100dd97cc6f271ce20920a2a87a04ce49d04059",
    },
}
TERMS = {
    "exact_unlimited_applicant": r"Pfizer\s+Ireland\s+Pharmaceuticals\s+Unlimited\s+Company",
    "any_unlimited": r"unlimited",
    "short_name": r"Pfizer\s+Ireland\s+Pharmaceuticals",
    "issuer_cik": r"\b(?:0000078003|78003)\b",
    "ownership_words": r"\b(?:parent|subsidiar(?:y|ies)|owned|ownership|affiliate)\b",
    "license_2060": r"(?:U\.?S\.?\s+License\s+No\.\s*2060)",
    "manufacturer_role": r"Manufactured\s+by",
    "distributor_role": r"Distributed\s+by",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    source_root, output = args.source_root.resolve(strict=True), args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    records = []
    for key, spec in DOCUMENTS.items():
        source = source_root / spec["filename"]
        metadata_path = source.with_suffix(".metadata.json")
        metadata_bytes = metadata_path.read_bytes()
        metadata = json.loads(metadata_bytes)
        if (metadata.get("public_raw_available") is not True
                or metadata.get("original_sensitive_body_gate_passed") is not True
                or metadata.get("status") != "PUBLIC_ORIGINAL_BYTES_CAPTURED"
                or metadata.get("http_status") != 200):
            raise ValueError("Original unavailable or unsafe; do not read/substitute its body")
        body = source.read_bytes()
        if (len(body) != spec["bytes"] or sha(body) != spec["sha256"]
                or metadata.get("bytes") != len(body)
                or metadata.get("raw_sha256") != sha(body)
                or not 0 < len(body) <= 4000000):
            raise ValueError("Original whole-body size/hash mismatch")
        text_path = output / f"{key}.layout.txt"
        command = ["pdftotext", "-layout", "-enc", "UTF-8", str(source), str(text_path)]
        cached = text_path.exists()
        stderr = None
        if not cached:
            result = subprocess.run(command, capture_output=True, check=False)
            stderr = result.stderr.decode("utf-8", errors="strict")
            if result.returncode:
                raise ValueError("PDF extraction failed; source left unchanged")
        text_bytes = text_path.read_bytes()
        if (len(text_bytes) != spec["text_bytes"] or sha(text_bytes) != spec["text_sha256"]
                or metadata.get("derived_pdf_text_bytes") != len(text_bytes)
                or metadata.get("derived_pdf_text_sha256") != sha(text_bytes)):
            raise ValueError("Derived text differs from the exact retained extraction")
        text = text_bytes.decode("utf-8")
        pages = text.split("\f")
        if len(pages) != spec["pages"] + 1 or pages[-1].strip():
            raise ValueError("Physical PDF page inventory is incomplete")
        pages = pages[:-1]
        indexed = []
        offset = 0
        for number, page in enumerate(pages, 1):
            if not page.strip():
                raise ValueError("Empty extracted page requires separate review")
            page_bytes = page.encode("utf-8")
            global_line = text[:offset].count("\n") + 1
            indexed.append({
                "physical_page": number,
                "derived_byte_start": len(text[:offset].encode("utf-8")),
                "derived_byte_end_exclusive": len(text[:offset].encode("utf-8")) + len(page_bytes),
                "global_line_start": global_line,
                "global_line_end": global_line + page.count("\n"),
                "page_line_count": len(page.splitlines()),
                "page_text_bytes": len(page_bytes), "page_text_sha256": sha(page_bytes),
                "complete_page_text_identity_search_counts": {
                    name: len(re.findall(pattern, page, flags=re.I))
                    for name, pattern in TERMS.items()
                },
            })
            offset += len(page) + 1
        info = subprocess.run(["pdfinfo", str(source)], capture_output=True, check=False)
        if info.returncode:
            raise ValueError("PDF page metadata unavailable")
        records.append({
            "key": key, "source_absolute_path": str(source),
            "source_repo_path": "evidence/sourcefront21_primary_20261021/primary/" + spec["filename"],
            "whole_pdf_bytes": len(body), "whole_pdf_sha256": sha(body),
            "capture_metadata_absolute_path": str(metadata_path),
            "capture_metadata_bytes": len(metadata_bytes), "capture_metadata_sha256": sha(metadata_bytes),
            "source_requested_at": metadata["started_at"], "source_observed_at": metadata["observed_at"],
            "source_capture_known_at": metadata["known_at"],
            "source_publication_time": metadata["source_publication_time"],
            "derived_text_filename": text_path.name, "derived_text_bytes": len(text_bytes),
            "derived_text_sha256": sha(text_bytes), "extraction_command": command,
            "reused_already_extracted_exact_text": cached,
            "extraction_stderr_this_invocation": stderr,
            "pdfinfo_stdout": info.stdout.decode("utf-8"),
            "pdfinfo_stderr": info.stderr.decode("utf-8"),
            "page_count": len(pages), "pages": indexed,
            "whole_text_search_counts": {
                name: len(re.findall(pattern, text, flags=re.I))
                for name, pattern in TERMS.items()
            },
            "text_coordinates_are_derived_not_original_PDF_byte_offsets": True,
            "whole_text_search_is_not_full_pixel_or_universe_absence_proof": True,
            "all_source_bytes_rechecked_unchanged_after_extraction": source.read_bytes() == body,
        })
    result = {"schema_version": "two_actual_fda_pdf_page_index_v1",
              "review_started_at": started.isoformat(), "documents": records,
              "network_HTTP_collector_SQL_operations": 0,
              "known_at": datetime.now(timezone.utc).isoformat()}
    result["ingested_at"] = result["known_at"]
    encoded = (json.dumps(result, indent=2) + "\n").encode()
    (output / "page_text_index.json").write_bytes(encoded)
    print(json.dumps({"documents": len(records), "physical_pages": sum(r["page_count"] for r in records),
                      "index_bytes": len(encoded), "index_sha256": sha(encoded)}, indent=2))


if __name__ == "__main__":
    main()
