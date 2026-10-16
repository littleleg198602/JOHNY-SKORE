"""Read-only, bounded peer audit of the three batch8 primary capture packets.

Only selected public original bodies and safe capture metadata are read. Known
whole-withheld bodies are never opened. Their absence uses canonical paths and
Git/worktree size inventories, not a search through private or unrelated bodies.
This is a content/pin audit, not source acceptance or universal secret detection.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from html import unescape
from io import BytesIO
import json
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

MAX_BODY = 4_000_000
MAX_PDF_PAGES = 120
MAX_PDF_TEXT = 2_000_000
KNOWN_WITHHELD = (
    ("evidence/batch4_regulatory_20261013/raw/nhtsa_api_docs.html", 147164,
     "84b6ad89bee343819008e22babdf9a87dbfd3034a99022ea68320f0f17edf8b3"),
    ("evidence/batch4_ir_20261013/primary/ir_authority.html", 286802,
     "deb5642d450c0fd60cd12066a42da8c1905f4fd47562afcef06cf8e428bcbeb1"),
    ("evidence/batch5_ir_20261014/primary/ir_authority.html", 287254,
     "5130e6c90a0770d32e40f5eafcc87cd980d963934ae6c125963d8b1f2c20eac1"),
    ("evidence/batch8_regulatory_20261015/raw/njd_case_page.html", 28995,
     "27f03d6bb63f0590a82369556419c4eb8f5fb9ab3b3da44a9c8695d18d7b7532"),
)
PATTERNS = {
    "PRIVATE_KEY_BLOCK": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "AWS_ACCESS_KEY_IDENTIFIER": r"\bAKIA[A-Z0-9]{16}\b",
    "BEARER_ACCESS_VALUE": r"\bbearer\s+[A-Za-z0-9._~+/=-]{12,}",
    "CREDENTIAL_FIELD_ASSIGNMENT": r"(?:api[_-]?key|subscription[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|password)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._~+/=-]{8,}",
    "AZURE_MAPS_ACCESS_ASSIGNMENT": r"(?:MICROSOFT_AZURE_MAPS_KEY|azureMapsKey|azure_map_key)[\"']?\s*[:=]\s*[\"']?[^\s\"'<>;,]{8,}",
    "ACCESS_QUERY_VALUE": r"[?&](?:sig|signature|x-amz-signature|x-goog-signature|access_token|pwd|password|api_key)=[^\s\"'<>]{3,}",
    "MEETING_ACCESS_VALUE": r"(?:passcode|meeting password)\s*[:=]\s*\S+",
    "EMBEDDED_ACCESS_FIELD": r"[\"'](?:csrf_token|licenseKey|subscriptionKey|sitekey)[\"']\s*:\s*[\"'][^\"']{8,}",
}
COMPILED = {name: re.compile(pattern, re.I) for name, pattern in PATTERNS.items()}


def digest(body):
    return hashlib.sha256(body).hexdigest()


def utc(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert result.tzinfo is not None
    return result.astimezone(timezone.utc)


def git(root, *args, input=None):
    return subprocess.check_output(["git", "-C", str(root), *args], input=input)


class Audit:
    def __init__(self):
        self.pins = {}
        self.failures = []

    def require(self, condition, code):
        if not condition:
            self.failures.append(code)

    def read(self, root, relative):
        root = root.resolve()
        path = root / relative
        assert path.resolve().is_relative_to(root) and not path.is_symlink()
        body = path.read_bytes()
        row = {"root": str(root), "path": relative, "bytes": len(body), "sha256": digest(body)}
        key = (str(root), relative)
        assert key not in self.pins or self.pins[key] == row, "Selected file changed during audit"
        self.pins[key] = row
        return body

    def load(self, root, relative):
        return json.loads(self.read(root, relative))

    def sensitive(self, text):
        # Detection only; decoded content and matched values are never exported.
        views = (text, unescape(text), unquote(unescape(text)))
        return sorted({name for name, pattern in COMPILED.items() if any(pattern.search(v) for v in views)})

    def original(self, root, relative, expected_size, expected_sha, content_type):
        assert isinstance(relative, str) and ".." not in Path(relative).parts
        assert relative not in {item[0] for item in KNOWN_WITHHELD}
        assert expected_sha not in {item[2] for item in KNOWN_WITHHELD}
        body = self.read(root, relative)
        row = {"root": str(root), "path": relative, "bytes": len(body), "sha256": digest(body),
               "content_type": content_type, "publisher_bytes_modified": False}
        self.require(type(expected_size) is int and len(body) == expected_size <= MAX_BODY, "ORIGINAL_SIZE_PIN")
        self.require(digest(body) == expected_sha, "ORIGINAL_SHA256_PIN")
        self.require(bool(body), "EMPTY_PUBLIC_ORIGINAL")
        cues = self.sensitive(body.decode("utf-8", errors="replace"))
        if body.startswith(b"%PDF-"):
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(body), strict=True)
            self.require(not reader.is_encrypted, "ENCRYPTED_PDF")
            count = len(reader.pages)
            self.require(1 <= count <= MAX_PDF_PAGES, "PDF_PAGE_BOUND")
            if count > MAX_PDF_PAGES or reader.is_encrypted:
                return row
            characters = 0
            for page in reader.pages:
                text = page.extract_text() or ""
                characters += len(text)
                self.require(characters <= MAX_PDF_TEXT, "PDF_DECODED_TEXT_BOUND")
                if characters > MAX_PDF_TEXT:
                    break
                cues.extend(self.sensitive(text))
            row.update(pdf_pages_inspected=count, pdf_decoded_characters=characters,
                       all_pdf_pages_inspected=characters <= MAX_PDF_TEXT)
        row.update(sensitive_fixed_cue_ids=sorted(set(cues)), whole_raw_and_decoded_access_gate_passed=not cues)
        self.require(not cues, "PUBLIC_ORIGINAL_ACCESS_CUE")
        return row

    def source(self, kind, root):
        folder = {"identity": "evidence/batch8_identity_20261015",
                  "doj": "evidence/batch8_regulatory_20261015",
                  "sec": "evidence/batch8_sec_20261015"}[kind]
        plan_name = "request_plan.json" if kind == "identity" else "research_plan.json"
        plan_raw = self.read(root, folder + "/" + plan_name)
        plan_hash = digest(plan_raw)
        plan = json.loads(plan_raw)
        if kind == "identity":
            self.require(plan["max_http_transports"] == 6 and plan["max_response_bytes"] == MAX_BODY
                and plan["timeout_seconds"] == 25 and plan["redirects_allowed"] is False,
                "IDENTITY_PRECOMMITTED_BOUNDS")
        elif kind == "doj":
            self.require(plan["maximum_primary_capture_transports"] == 6
                and plan["maximum_original_bytes_per_response"] == MAX_BODY
                and plan["timeout_seconds_per_hop"] == 25 and plan["automatic_redirects"] is False,
                "DOJ_PRECOMMITTED_BOUNDS")
        else:
            self.require(plan["maximum_transports"] == 6 and plan["per_transport_maximum_body_bytes"] == MAX_BODY
                and plan["per_transport_absolute_timeout_seconds"] == 25 and plan["redirects_or_retries_allowed"] is False,
                "SEC_PRECOMMITTED_BOUNDS")
        producer_pin = digest(self.read(root, folder + "/capture_primary.py"))
        rows = []
        expected = {"identity": 6, "doj": 5, "sec": 4}[kind]
        if kind == "identity":
            reservations = sorted((root / folder / "captures").glob("attempt-*.reserved.json"))
        elif kind == "doj":
            reservations = sorted((root / folder / "reservations").glob("*.json"))
        else:
            reservations = sorted((root / folder).glob("attempt-*-reserved.json"))
        self.require(len(reservations) == expected, "UNEXPECTED_" + kind.upper() + "_ATTEMPTS")
        for path in reservations:
            reservation_path = path.relative_to(root).as_posix()
            reserved = self.load(root, reservation_path)
            if kind == "identity":
                result_path = reservation_path.replace(".reserved.json", ".metadata.json")
                result = self.load(root, result_path)
                stable = set(reserved) - {"status"}
                url, started = reserved["requested_url"], reserved["started_at"]
                self.require(result["request_plan_sha256"] == plan_hash and result["redirects_allowed"] is False,
                             "IDENTITY_PLAN_OR_REDIRECT_SCOPE")
                self.require(result["research_only"] is True and result["app_quota_managed_live_claimed"] is False
                             and result["historical_request_ledger_reconstructed"] is False, "IDENTITY_RESEARCH_SCOPE")
                complete = result["status"] == "PUBLIC_ORIGINAL_BYTES_CAPTURED"
                raw_path = folder + "/captures/" + result["body_path"] if complete else None
                size, sha = result.get("bytes"), result.get("raw_sha256")
                mime = result.get("response_headers", {}).get("Content-Type")
                self.require(result.get("final_url") == url, "IDENTITY_FINAL_URL")
                self.require(result.get("response_headers", {}).get("Content-Encoding") in {None, "", "identity"},
                             "IDENTITY_ENCODED_SUBSTITUTE")
            elif kind == "doj":
                result_path = folder + "/raw/" + reserved["capture_id"] + ".metadata.json"
                result = self.load(root, result_path)
                stable = set(reserved)
                url, started = reserved["requested_url"], reserved["started_at"] if "started_at" in reserved else reserved["reserved_at"]
                self.require(result["plan_sha256"] == plan_hash and result["automatic_redirects"] is False,
                             "DOJ_PLAN_OR_REDIRECT_SCOPE")
                complete = result["status"] == "ORIGINAL_BYTES_CAPTURED"
                raw_path = result.get("archive_path") if complete else None
                size, sha = result.get("raw_response_bytes"), result.get("raw_response_sha256")
                mime = result.get("content_type")
                if complete:
                    self.require(result.get("final_url") == url, "DOJ_FINAL_URL")
                    self.require(result.get("content_encoding") in {None, "", "identity"}, "DOJ_ENCODED_SUBSTITUTE")
            else:
                result_path = reservation_path.replace("-reserved.json", "-result.json")
                result = self.load(root, result_path)
                stable = set(reserved)
                url, started = reserved["url"], reserved["reserved_at"]
                self.require(result["research_plan_sha256"] == plan_hash and result["redirects_or_retries_allowed"] is False,
                             "SEC_PLAN_OR_REDIRECT_SCOPE")
                self.require(result["application_collector_or_quota_db_used"] is False, "SEC_RESEARCH_SCOPE")
                complete = result["retention"] == "ORIGINAL_BYTES"
                raw_path = folder + "/" + result["path"] if complete else None
                size, sha = result.get("size_bytes"), result.get("sha256")
                mime = result.get("content_type")
                self.require(complete == result["complete_original_body_received"], "SEC_COMPLETE_BODY_SCOPE")
            self.require(all(result.get(key) == reserved[key] for key in stable), "RESERVATION_MUTATED")
            self.require(utc(started) <= utc(result["observed_at"]), "SOURCE_CLOCK_ORDER")
            parsed = urlsplit(url)
            self.require(parsed.scheme == "https" and parsed.hostname in {"data.sec.gov", "www.sec.gov", "www.justice.gov",
                "www.govinfo.gov", "www.njd.uscourts.gov", "ecf.njd.uscourts.gov"}, "UNEXPECTED_PRIMARY_HOST")
            self.require(not self.sensitive(url), "UNSAFE_METADATA_URL")
            row = {"reservation_path": reservation_path, "metadata_path": result_path,
                   "source_url": url, "started_at": started, "observed_at": result["observed_at"],
                   "http_status": result.get("http_status"), "public_original": complete,
                   "original_sha256": sha, "original_bytes": size}
            if complete:
                self.require(result.get("http_status") == 200, "PUBLIC_NON200_BODY")
                assert raw_path.startswith(folder + "/")
                row["original_review"] = self.original(root, raw_path, size, sha, mime)
            elif kind == "doj" and result.get("http_status") == 200:
                self.require(result["status"] == "WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE"
                    and result["public_raw_available"] is False and result["archive_path"] is None,
                    "WHOLE_WITHHELD_CONTROL")
                self.require((size, sha) == KNOWN_WITHHELD[3][1:], "NJD_WITHHELD_HASH_OR_SIZE")
                row["whole_withheld"] = True
            else:
                self.require(result.get("http_status") is None and raw_path is None, "FAILED_TRANSPORT_NOT_RESPONSE")
                row["transport_failed_without_response"] = True
            rows.append(row)
        return {"kind": kind, "root": str(root), "folder": folder, "plan_sha256": plan_hash,
                "capture_producer_sha256": producer_pin, "attempt_count": len(rows), "attempts": rows}


def absence_inventory(root):
    """Stat/index object sizes only; no unrelated or unavailable body reads."""
    target_sizes = {item[1] for item in KNOWN_WITHHELD}
    names = {v.decode() for v in git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0") if v}
    working_candidates = []
    for name in names:
        path = root / name
        if path.is_file() and path.stat().st_size in target_sizes:
            working_candidates.append({"path": name, "bytes": path.stat().st_size})
    tree_candidates = []
    for entry in git(root, "ls-tree", "-r", "-l", "-z", "HEAD").split(b"\0"):
        if entry:
            head, name = entry.split(b"\t", 1)
            fields = head.decode().split()
            if fields[1] == "blob" and int(fields[3]) in target_sizes:
                tree_candidates.append({"path": name.decode(), "bytes": int(fields[3])})
    index = [entry.split(b"\t", 1)[0].decode().split()[1] for entry in git(root, "ls-files", "--stage", "-z").split(b"\0") if entry]
    index_candidates = []
    if index:
        checked = git(root, "cat-file", "--batch-check", input=("\n".join(index)+"\n").encode()).decode().splitlines()
        for line in checked:
            oid, kind, size = line.split()
            if kind == "blob" and int(size) in target_sizes:
                index_candidates.append({"git_blob": oid, "bytes": int(size)})
    paths_absent = all(not (root / path).exists() for path, _, _ in KNOWN_WITHHELD)
    return {"root": str(root), "canonical_paths_absent": paths_absent,
            "known_withheld_originals": [{"path": p, "bytes": n, "sha256": h} for p, n, h in KNOWN_WITHHELD],
            "publishable_worktree_path_count": len(names), "head_tree_size_candidates": tree_candidates,
            "index_blob_size_candidates": index_candidates, "worktree_size_candidates": working_candidates,
            "exact_original_absence_proven_without_body_reads": paths_absent and not (tree_candidates or index_candidates or working_candidates),
            "method": "Absent canonical paths and no HEAD/index/nonignored-worktree file with any original byte length",
            "unrelated_or_private_body_contents_read": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--integration-root", type=Path, required=True)
    parser.add_argument("--identity-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    roots = {"integration": args.integration_root.resolve(), "identity": args.identity_root.resolve()}
    output = args.output.resolve()
    assert output.parent == Path(__file__).resolve().parent and not output.exists()
    started = datetime.now(timezone.utc).isoformat()
    before = {name: git(root, "rev-parse", "HEAD").decode().strip() for name, root in roots.items()}
    audit = Audit()
    with patch.object(socket, "create_connection", side_effect=AssertionError("No peer-audit HTTP")), \
         patch.object(sqlite3, "connect", side_effect=AssertionError("No peer-audit database")):
        scopes = [audit.source("identity", roots["identity"]), audit.source("doj", roots["integration"]),
                  audit.source("sec", roots["integration"])]
        absence = [absence_inventory(root) for root in dict.fromkeys(roots.values())]
        for row in absence:
            audit.require(row["exact_original_absence_proven_without_body_reads"], "WHOLE_WITHHELD_ABSENCE_NOT_PROVEN")
        # Pin the safe original metadata for the three historical withheld bodies.
        nhtsa = audit.load(roots["integration"], "evidence/batch4_regulatory_20261013/raw/nhtsa_api_docs.metadata.json")
        audit.require((nhtsa["raw_response_bytes"], nhtsa["raw_response_sha256"]) == KNOWN_WITHHELD[0][1:]
            and nhtsa["public_raw_available"] is False, "HISTORICAL_NHTSA_WITHHELD_METADATA")
        ir4 = audit.load(roots["integration"], "evidence/batch4_ir_20261013/captures.json")
        authority = [r for r in ir4["captures"] if r["path"] == "primary/ir_authority.html"]
        audit.require(len(authority) == 1 and (authority[0]["size_bytes"], authority[0]["sha256"]) == KNOWN_WITHHELD[1][1:],
                      "HISTORICAL_IR4_WITHHELD_METADATA")
        ir5 = audit.load(roots["integration"], "evidence/batch5_ir_20261014/captures/02_receipt.json")
        audit.require((ir5["size_bytes"], ir5["sha256"]) == KNOWN_WITHHELD[2][1:]
            and ir5["retention"] == "WITHHELD_SENSITIVE_BODY", "HISTORICAL_IR5_WITHHELD_METADATA")
        for (root, path), expected in tuple(audit.pins.items()):
            audit.require(digest((Path(root) / path).read_bytes()) == expected["sha256"], "PINNED_SOURCE_CHANGED_DURING_AUDIT")
    attempts = [row for scope in scopes for row in scope["attempts"]]
    public = [row["original_review"] for row in attempts if row["public_original"]]
    counts = {"primary_research_transport_attempts": len(attempts), "http200_responses": sum(r["http_status"] == 200 for r in attempts),
              "transport_failures_without_response": sum(r.get("transport_failed_without_response", False) for r in attempts),
              "public_original_bodies": len(public), "new_whole_withheld_responses": sum(r.get("whole_withheld", False) for r in attempts),
              "public_original_bytes": sum(r["bytes"] for r in public),
              "pdf_originals": sum("pdf_pages_inspected" in r for r in public),
              "pdf_pages_inspected": sum(r.get("pdf_pages_inspected", 0) for r in public),
              "fresh_collector_api_requests_in_declared_packet": 0, "peer_audit_http_requests": 0, "peer_audit_database_actions": 0}
    expected = {"primary_research_transport_attempts": 15, "http200_responses": 13, "transport_failures_without_response": 2,
                "public_original_bodies": 12, "new_whole_withheld_responses": 1, "pdf_originals": 2, "pdf_pages_inspected": 95}
    audit.require(all(counts[k] == v for k, v in expected.items()), "EXPECTED_PACKET_COUNTS_MISMATCH")
    after = {name: git(root, "rev-parse", "HEAD").decode().strip() for name, root in roots.items()}
    report = {"schema_version": 1, "kind": "BATCH8_PUBLIC_ORIGINAL_PEER_AUDIT", "started_at": started,
              "finished_at": datetime.now(timezone.utc).isoformat(), "status": "PASS" if not audit.failures else "FAIL",
              "script_sha256": digest(Path(__file__).read_bytes()), "source_heads_before": before, "source_heads_after": after,
              "heads_unchanged_during_snapshot": before == after, "scope_content_pins_rechecked_at_end": True,
              "counts": counts, "expected_counts": expected, "source_scopes": scopes, "whole_withheld_absence": absence,
              "input_pins": sorted(audit.pins.values(), key=lambda r: (r["root"], r["path"])),
              "fixed_failure_codes": dict(Counter(audit.failures)), "max_original_body_bytes": MAX_BODY,
              "max_pdf_pages_per_document": MAX_PDF_PAGES, "max_pdf_decoded_characters_per_document": MAX_PDF_TEXT,
              "sensitive_gate_pattern_ids": sorted(PATTERNS), "matched_access_values_exported": False,
              "known_whole_withheld_bodies_read_restored_sanitized_encoded_or_wrapped": False,
              "universal_secret_detection_claim": False, "current_complete_sources_or_live_acceptance_claimed": False,
              "limitation": "Read-only pinned capture packets, not a full repository/history security audit or proof of unrecorded network activity. Root must rerun after final integration."}
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "counts": counts, "fixed_failure_codes": report["fixed_failure_codes"],
                      "output_sha256": digest(output.read_bytes())}, sort_keys=True))
    return 0 if not audit.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
