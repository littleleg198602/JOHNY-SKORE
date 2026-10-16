"""Review exact published Git blobs; never request a source or open a task DB."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from market_checker_app.services.usaspending_prime_archive_recovery import (
    CAPTURE_SCHEMA, recover_prime_awards_from_archives,
)
from market_checker_app.services.usaspending_scout_service import API_URL


BASE = "cbc6c4a3f023f4f32778456ae4f73d6d2c91fcc6"
ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = Path(__file__).resolve().parent
MAXIMUM = 4_000_000
NOC_BODY_SHA256 = "1ba1103c08171924457ba2a8f4786079a9d01cb4fd92dc525f418b9eac510da1"


def _git(*args, input=None):
    return subprocess.run(["git", "-C", str(ROOT), *args], input=input,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout


def _dicts(value, pointer=""):
    if isinstance(value, dict):
        yield pointer, value
        for key, child in value.items():
            if key != "results":
                yield from _dicts(child, pointer + "/" + key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _dicts(child, pointer + "/" + str(index))


def main():
    started = datetime.now(timezone.utc)
    entries, skipped = [], []
    for line in _git("ls-tree", "-rlz", BASE).split(b"\0"):
        if not line:
            continue
        header, name = line.split(b"\t", 1)
        mode, kind, blob, size = header.split()
        path = name.decode("utf-8")
        if kind != b"blob" or Path(path).suffix not in {".json", ".response", ".jsonl"}:
            continue
        row = {"path": path, "git_blob": blob.decode(), "bytes": int(size)}
        if int(size) > MAXIMUM:
            skipped.append(row)
        else:
            entries.append(row)
    unique = list(dict.fromkeys(row["git_blob"] for row in entries))
    batch = _git("cat-file", "--batch", input=("\n".join(unique) + "\n").encode())
    bodies, position = {}, 0
    for blob in unique:
        end = batch.index(b"\n", position)
        reported_blob, kind, size = batch[position:end].split()
        assert reported_blob.decode() == blob and kind == b"blob"
        position = end + 1
        bodies[blob] = batch[position:position + int(size)]
        position += int(size) + 1
    assert position == len(batch)
    candidates, prime_pages, original_prime_manifests, noc_body_locations = [], [], [], []
    for entry in entries:
        body = bodies[entry["git_blob"]]
        digest = hashlib.sha256(body).hexdigest()
        if digest == NOC_BODY_SHA256:
            noc_body_locations.append(entry["path"])
        if not any(marker in body for marker in (b"usaspending", b"Recipient UEI", b"spending_level", CAPTURE_SCHEMA.encode())):
            continue
        try:
            obj = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(obj, (dict, list)):
            continue
        source_scopes = set()
        for pointer, value in _dicts(obj):
            for key in ("source", "source_url", "source_url_final"):
                reference = value.get(key)
                if isinstance(reference, str) and reference.startswith("https://api.usaspending.gov/"):
                    source_scopes.add(reference)
            request = value.get("request")
            if (value.get("schema_version") == CAPTURE_SCHEMA
                    or (value.get("source_url") == API_URL and isinstance(request, dict)
                        and isinstance(request.get("filters"), dict)
                        and isinstance(value.get("body_file"), str) and isinstance(value.get("response_sha256"), str))):
                original_prime_manifests.append({**entry, "sha256": digest, "json_pointer": pointer})
            if value.get("spending_level") == "awards" and isinstance(value.get("results"), list):
                rows, paging = value["results"], value.get("page_metadata", {})
                prime_pages.append({**entry, "sha256": digest, "json_pointer": pointer,
                                    "reported_rows": len(rows), "page": paging.get("page"),
                                    "has_next": paging.get("hasNext"),
                                    "reported_ueis": sorted({row["Recipient UEI"] for row in rows
                                                             if isinstance(row, dict) and isinstance(row.get("Recipient UEI"), str)}),
                                    "original_http_response_bytes_and_request_manifest_present": False,
                                    "usable_for_recovery": False})
        candidates.append({**entry, "sha256": digest, "official_api_scopes": sorted(source_scopes)})
    rejected = []
    for name in ("evidence/usaspending_live_20261001.json", "evidence/usaspending_noc_live_20261002.json",
                 "evidence/batch3_contracts_live_20261012/capture-9cfe102b3b3a421292a3e20e0b0c39b2.json",
                 "evidence/batch5_contracts_20261014/capture-probe-import.json"):
        entry = next(row for row in entries if row["path"] == name)
        encoded = bodies[entry["git_blob"]]
        path = ROOT / name
        assert path.read_bytes() == encoded, "The tested source must match its published Git blob"
        report = recover_prime_awards_from_archives(None, manifests=[path],
                  expected_manifest_sha256={str(path): hashlib.sha256(encoded).hexdigest()},
                  subject_id="LMT", as_of=datetime.now(timezone.utc), validate_only=True)
        assert report["status"] == "REJECTED" and report["new_findings"] == 0
        assert path.read_bytes() == encoded
        rejected.append({**entry, "sha256": hashlib.sha256(encoded).hexdigest(), "report": report})
    result = {"schema_version": 1, "kind": "published_original_git_blob_corpus_review",
              "started_at": started.isoformat(), "completed_at": datetime.now(timezone.utc).isoformat(),
              "public_base_commit": BASE, "public_base_tree": _git("rev-parse", BASE + "^{tree}").decode().strip(),
              "scope": "Every JSON/JSONL/response blob <=4 MiB in the published base tree; no scratch DB as source evidence",
              "examined_blob_paths": len(entries), "examined_unique_blobs": len(unique),
              "examined_bytes_with_duplicate_paths": sum(row["bytes"] for row in entries),
              "oversized_blob_paths_not_decoded": skipped, "candidate_usaspending_documents": candidates,
              "embedded_prime_pages": prime_pages, "original_prime_capture_manifests": original_prime_manifests,
              "noc_reported_response_sha256": NOC_BODY_SHA256, "noc_exact_body_locations_in_reviewed_corpus": noc_body_locations,
              "actual_available_archive_rejections": rejected, "actual_recovered_prime_awards": 0,
              "new_source_http_attempts": 0, "quota_reconstructed": False, "task_database_opened": False,
              "limitations": ["LMT original positive is one parsed 100-row page with hasNext=true, no terminal continuation or original HTTP body/request manifest",
                              "NOC positive summary says hasNext=true; its declared response hash has no exact body in this reviewed tree",
                              "Copied prime rows and 14-row transaction bodies cannot stand in for complete original prime pages",
                              "The terminal empty control is not a verified recipient positive and lacks original body/request provenance",
                              "This review covers the stated published tree; absent external artifacts or disappeared uncommitted DBs are not inferred",
                              "Fresh USAspending collection remains blocked by missing original durable quota history; no quota state reconstructed"]}
    assert not original_prime_manifests and not noc_body_locations
    (DIRECTORY / "corpus_review.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"reviewed_paths": len(entries), "candidate_documents": len(candidates),
                      "embedded_prime_pages": len(prime_pages), "original_prime_manifests": len(original_prime_manifests),
                      "actual_recovered": 0, "new_http": 0}))


if __name__ == "__main__":
    main()
