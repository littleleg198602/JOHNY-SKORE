"""Read-only audit of the five newly captured research originals and EX31.1."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import time


CAPTURE_COMMIT = "8313e09da8db533a2c75c2da279b10458db4643c"
CAPTURE_DIRECTORY = "evidence/sourcefront21_primary_20261021"
PRIOR_AUDIT = "evidence/sourcefront21_evaluation_20261021/candidate_plan_audit.json"
PRIOR_AUDIT_SHA = "fecc40c808f8abdc9ce25d81de3c5f56330d70b0591edf753396de900654af83"
PROPOSAL_SHA = "60c86f690de7a7bc8be143455317d21b37053548a1eae771b3565c2bbaea87d4"
KEYS = ("p_name_amendment", "p_securities_description", "fda_trazimera_approval_2019", "fda_trazimera_label_2025", "msft_ex31_1")
EXPECTED_BYTES = (9238, 28199, 137546, 2495404, 12762)
EXPECTED_SHA = (
    "d2e7e86faf3fe1fd2b7701629dc0325a600ebd0d9f1305e0c2f38bb99a33847e",
    "a87ab222a1958a5df3368b9cfb2c538cd3d63f14eb18e3d9b7102b38d8e1714e",
    "2daf7bfa938057c13186e49ba716e87f9d819e2d9bdf5ee6c12f08bf0d6ee6c7",
    "ee258f711faf29cdd88ad2cf78bccc802d1d766bb9898447a68e00f713a5afb2",
    "5001124e4c5e57d8aafd6fbbe98845acd11397dd5ddcc853857c6377a452872a",
)
HEADERS = {"Date", "Last-Modified", "Content-Type", "Content-Length", "Content-Encoding"}
WITHHELD_SHA = {
    "84b6ad89bee343819008e22babdf9a87dbfd3034a99022ea68320f0f17edf8b3",
    "deb5642d450c0fd60cd12066a42da8c1905f4fd47562afcef06cf8e428bcbeb1",
    "5130e6c90a0770d32e40f5eafcc87cd980d963934ae6c125963d8b1f2c20eac1",
    "27f03d6bb63f0590a82369556419c4eb8f5fb9ab3b3da44a9c8695d18d7b7532",
}
SENSITIVE = re.compile(r"(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bAKIA[A-Z0-9]{16}\b|"
    r"\bbearer\s+[A-Za-z0-9._~+/=-]{12,}|(?:api[_-]?key|subscription[_-]?key|client[_-]?secret|"
    r"access[_-]?token|refresh[_-]?token|password)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._~+/=-]{8,}|"
    r"[?&](?:sig|signature|x-amz-signature|x-goog-signature|access_token)=[^\s\"'<>]{8,})", re.I)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def stamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert result.tzinfo is not None and result.utcoffset().total_seconds() == 0
    return result


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, value):
        if value.strip():
            self.parts.append(" ".join(value.split()))


def audit(capture_root, proposal_path):
    started_at, started_mono = datetime.now(timezone.utc), time.monotonic_ns()
    capture_root = capture_root.resolve()
    entries, cache, pins = {}, {}, {}
    index_path = "evidence/batch3_sec_20261012/primary/msft_2026_index.html"
    listing = subprocess.check_output(["git", "-C", str(capture_root), "ls-tree", "-r", "-z", CAPTURE_COMMIT,
                                       "--", CAPTURE_DIRECTORY, PRIOR_AUDIT, index_path])
    for row in listing.split(b"\0"):
        if row:
            header, path = row.split(b"\t", 1)
            mode, kind, blob = header.decode().split()
            entries[path.decode()] = (mode, kind, blob)

    def read(relative):
        assert relative in entries and relative.startswith((CAPTURE_DIRECTORY + "/", PRIOR_AUDIT, index_path))
        assert Path(relative).suffix not in {".db", ".sqlite", ".sqlite3", ".wal", ".journal"}
        if relative not in cache:
            mode, kind, blob = entries[relative]
            assert mode != "120000" and kind == "blob"
            p = capture_root / relative
            assert not p.is_symlink() and all(not part.is_symlink() for part in p.parents if part.is_relative_to(capture_root))
            raw = p.read_bytes()
            assert hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == blob
            cache[relative] = raw
        raw = cache[relative]
        pins[relative] = {"path": relative, "bytes": len(raw), "sha256": digest(raw)}
        return raw

    def load(relative):
        return json.loads(read(relative))

    prior = load(PRIOR_AUDIT)
    assert pins[PRIOR_AUDIT]["sha256"] == PRIOR_AUDIT_SHA
    assert prior["status"] == "FIVE_SELECTED_LITERAL_RESEARCH_CANDIDATES_VERIFIED_NOT_AUTHORIZED"
    assert not proposal_path.is_symlink()
    proposal_raw = proposal_path.read_bytes()
    assert digest(proposal_raw) == PROPOSAL_SHA
    proposal = json.loads(proposal_raw)
    proposal_pin = {"path": str(proposal_path), "bytes": len(proposal_raw), "sha256": digest(proposal_raw)}
    plan_path = CAPTURE_DIRECTORY + "/request_plan.json"
    plan = load(plan_path)
    assert [row["key"] for row in plan["urls"]] == list(KEYS)
    assert plan["base_public_head"] == prior["base_commit"] == proposal["base_public_head"]
    assert plan["max_http_transports"] == len(plan["urls"]) == proposal["max_http_transports"] == 5
    assert plan["aggregate_max_response_bytes"] == proposal["aggregate_max_response_bytes"] == 10_256_000
    assert plan["retry"] is plan["redirect"] is plan["fallback"] is False
    assert plan["independent_candidate_audit"]["sha256"] == PRIOR_AUDIT_SHA
    assert plan["maximum_PDF_decode_seconds_after_transport"] == 15
    assert plan["sourcefront21_collector_API_SQL_quotaDB_actions"] == 0
    assert plan["source_publish_or_registry_adoption_authorized"] is False
    for row, original in zip(plan["urls"], proposal["urls"]):
        assert row == {k: v for k, v in original.items() if k != "http_attempted"}
        assert original["http_attempted"] is False
        selected = next(c for c in prior["candidates"] if c["candidate_id"] == row["candidate_id"])
        assert selected["root_selected_for_separate_research_review"] and row["url"] == selected["exact_URL"]
        assert selected["bounds"]["maximum_response_bytes"] == row["max_response_bytes"]
        assert not selected["tracked_history"]["prior_attempt_or_reservation_matches"]

    launch = load(CAPTURE_DIRECTORY + "/primary_capture_launch.json")
    exit_record = load(CAPTURE_DIRECTORY + "/primary_capture_exit.json")
    process = load(CAPTURE_DIRECTORY + "/primary_capture_process.json")
    helper_path = CAPTURE_DIRECTORY + "/capture_primary.py"
    helper = read(helper_path)
    supervisor_path = CAPTURE_DIRECTORY + "/supervise_primary.py"
    read(supervisor_path)
    assert launch["plan_sha256"] == pins[plan_path]["sha256"]
    assert launch["helper_sha256"] == digest(helper)
    assert launch["maximum_transports"] == 5 and launch["http_attempts_before"] == 0
    assert launch["retry"] is launch["redirect"] is launch["fallback"] is False
    assert exit_record["exit_code"] == 0
    assert stamp(prior["audited_at"]) <= stamp(plan["root_selected_at"]) <= stamp(launch["started_at"])
    assert stamp(launch["started_at"]) <= stamp(process["observed_at"]) < stamp(exit_record["completed_at"])
    assert stamp(exit_record["completed_at"]) <= started_at
    stdout_path, stderr_path = (CAPTURE_DIRECTORY + "/primary_capture_" + s + ".log" for s in ("stdout", "stderr"))
    stdout, stderr = read(stdout_path), read(stderr_path)
    assert not stderr and not SENSITIVE.search(unescape(stdout.decode()))
    stdout_rows = [json.loads(line) for line in stdout.splitlines()]
    assert [row["key"] for row in stdout_rows] == list(KEYS)

    originals, previous_known, expected_files = [], stamp(launch["started_at"]), set()
    for row, expected_size, expected_sha, out in zip(plan["urls"], EXPECTED_BYTES, EXPECTED_SHA, stdout_rows):
        key = row["key"]
        metadata_path = CAPTURE_DIRECTORY + "/primary/" + key + ".metadata.json"
        reservation_path = CAPTURE_DIRECTORY + "/primary/" + key + ".reserved.json"
        body_path = CAPTURE_DIRECTORY + "/primary/" + key + row["suffix"]
        metadata, reserved = load(metadata_path), load(reservation_path)
        assert reserved["status"] == "REQUEST_RESERVED"
        assert all(metadata[k] == v for k, v in reserved.items() if k != "status")
        assert metadata["request_plan_sha256"] == pins[plan_path]["sha256"]
        assert metadata["key"] == key and metadata["method"] == "GET"
        assert metadata["requested_url"] == metadata["final_url"] == row["url"]
        assert metadata["http_status"] == 200 and metadata["status"] == "PUBLIC_ORIGINAL_BYTES_CAPTURED"
        assert metadata["research_only"] is True and metadata["collector_api_used"] is metadata["quota_database_created_or_modified"] is False
        assert metadata["retry"] is metadata["redirect"] is metadata["fallback"] is False
        assert metadata["timeout_seconds"] == 25 and metadata["max_response_bytes"] == row["max_response_bytes"]
        assert metadata["body_transformed"] is False and metadata["public_raw_available"] is metadata["original_sensitive_body_gate_passed"] is True
        assert metadata["source_publication_time"] is None and metadata["retention"] == "ORIGINAL_BYTES"
        assert previous_known <= stamp(metadata["started_at"]) <= stamp(metadata["observed_at"]) <= stamp(metadata["known_at"]) <= stamp(exit_record["completed_at"])
        assert 0 <= metadata["transport_elapsed_seconds"] <= 25 and metadata["elapsed_seconds"] <= 40
        previous_known = stamp(metadata["known_at"])
        headers = metadata["response_headers"]
        assert set(headers) == HEADERS
        assert all(v is None or isinstance(v, str) and len(v) <= 1024 and "\r" not in v and "\n" not in v and not SENSITIVE.search(unescape(v)) for v in headers.values())
        assert headers["Content-Encoding"] in (None, "identity")
        raw = read(body_path)
        assert metadata["body_path"] == key + row["suffix"]
        assert len(raw) == expected_size == metadata["bytes"] == int(headers["Content-Length"]) <= row["max_response_bytes"]
        assert digest(raw) == expected_sha == metadata["raw_sha256"] and expected_sha not in WITHHELD_SHA
        assert not SENSITIVE.search(unescape(raw.decode("utf-8", errors="replace")))
        assert all(out[k] == metadata[k] for k in ("key", "status", "http_status", "bytes", "raw_sha256", "observed_at"))
        item = {"key": key, "candidate_id": row["candidate_id"], "requested_URL": row["url"], "body": pins[body_path],
            "metadata": pins[metadata_path], "reservation": pins[reservation_path], "HTTP_status": 200,
            "physical_started_at": metadata["started_at"], "physical_observed_at": metadata["observed_at"], "physical_known_at": metadata["known_at"],
            "safe_preserved_headers": headers, "source_publication_time": None, "body_whole_hash_and_bytes_match": True,
            "reservation_plan_and_metadata_match": True, "request_bound_bytes": row["max_response_bytes"], "transport_elapsed_seconds": metadata["transport_elapsed_seconds"],
            "retry_redirect_fallback": False, "research_only": True}
        if row["suffix"] == ".pdf":
            assert raw.startswith(b"%PDF-")
            decoded = subprocess.run(["pdftotext", "-layout", "-", "-"], input=raw, capture_output=True, check=True, timeout=15).stdout
            assert len(decoded) == metadata["derived_pdf_text_bytes"] and digest(decoded) == metadata["derived_pdf_text_sha256"]
            assert decoded.strip() and not SENSITIVE.search(unescape(decoded.decode("utf-8", errors="replace")))
            item["PDF_text_gate"] = {"derived_bytes": len(decoded), "derived_sha256": digest(decoded), "matches_capture_metadata": True,
                "original_text_not_published_or_used_as_body_substitute": True, "not_pixel_or_publisher_authentication": True}
        originals.append(item)
        expected_files.update((metadata_path, reservation_path, body_path))
    observed_files = {p for p in entries if p.startswith(CAPTURE_DIRECTORY + "/primary/")}
    assert expected_files == observed_files and sum(i["body"]["bytes"] for i in originals) == 2_683_149

    msft = cache[CAPTURE_DIRECTORY + "/primary/msft_ex31_1.htm"]
    parser = VisibleText()
    parser.feed(msft.decode("utf-8")); parser.close()
    visible = "\n".join(parser.parts)
    literal_spans = {}
    for literal in ("I, Satya Nadella, certify that:", "Microsoft Corporation", "Satya Nadella", "Chief Executive Officer", "July 29, 2026"):
        matches = list(re.finditer(re.escape(literal.encode()), msft))
        assert len(matches) == (2 if literal == "Satya Nadella" else 1)
        literal_spans[literal] = [{"byte_start": m.start(), "byte_end_exclusive": m.end(), "sha256": digest(m.group())} for m in matches]
    assert "annual report on Form 10-K of Microsoft Corporation" in visible and "Chief Executive Officer" in visible
    assert not SENSITIVE.search(unescape(visible))
    prior_msft = next(c for c in prior["candidates"] if c["candidate_id"] == "MSFT_2026_EX31_1")
    ref = prior_msft["verified_literal_references"][0]
    index = read(index_path)
    assert pins[index_path] == ref["parent"]
    sp = ref["enclosing_row_span"]
    index_row = index[sp["byte_start"]:sp["byte_end_exclusive"]]
    assert digest(index_row) == sp["sha256"]
    cells = re.findall(rb"<td\b[^>]*>(.*?)</td>", index_row, re.I | re.S)
    declared = int(re.sub(rb"<[^>]+>", b"", cells[-1]).strip())
    assert declared == 12647 and len(msft) == 12762 and len(msft) - declared == 115
    scripts = [{"byte_start": m.start(), "byte_end_exclusive": m.end(), "bytes": len(m.group()), "sha256": digest(m.group())}
               for m in re.finditer(rb"<script\b[^>]*>.*?</script>", msft, re.I | re.S)]
    assert len(scripts) == 1 and scripts[0]["bytes"] == 115
    verification_path = CAPTURE_DIRECTORY + "/capture_verification.json"
    verification = load(verification_path)
    assert verification["total_originals"] == verification["research_HTTP_attempts"] == verification["research_HTTP200_whole_originals"] == 5
    assert verification["total_original_bytes"] == 2_683_149
    for item, metadata in zip(originals, verification["originals"]):
        assert item["key"] == metadata["key"] and item["body"]["bytes"] == metadata["bytes"] and item["body"]["sha256"] == metadata["sha256"]
    for row in verification["copies"]:
        relative = CAPTURE_DIRECTORY + "/" + row["path"]
        assert len(read(relative)) == row["bytes"] and pins[relative]["sha256"] == row["sha256"]
    for path, raw in cache.items():
        assert (capture_root / path).read_bytes() == raw, "ROOT_INPUT_CHANGED_DURING_READ_ONLY_AUDIT"
    completed, completed_mono = datetime.now(timezone.utc), time.monotonic_ns()
    assert completed >= started_at and completed_mono >= started_mono
    own_raw = Path(__file__).read_bytes()
    return {"schema_version": "sourcefront21_independent_primary_capture_audit_v1", "scope": "ACTUAL_NEW_RESEARCH_CAPTURE_CONTENT_ONLY",
        "status": "FIVE_WHOLE_ORIGINALS_AND_DATED_EX31_POINT_VERIFIED_WITH_LIMITS", "started_at": started_at.isoformat(), "completed_at": completed.isoformat(),
        "elapsed_monotonic_ns": completed_mono - started_mono, "capture_source_commit": CAPTURE_COMMIT,
        "auditor_producer": {"path": "evidence/sourcefront21_evaluation_20261021/audit_primary_captures.py", "bytes": len(own_raw), "sha256": digest(own_raw)},
        "frozen_prior_audit_pin": pins[PRIOR_AUDIT], "frozen_proposal_pin": proposal_pin, "actual_request_plan_pin": pins[plan_path],
        "originals": originals, "totals": {"distinct_research_HTTP200_originals": 5, "whole_original_bytes": 2_683_149,
            "maximum_authorized_response_bytes": 10_256_000, "retries_redirects_fallbacks": 0},
        "capture_process": {"launch": pins[CAPTURE_DIRECTORY + "/primary_capture_launch.json"], "process": pins[CAPTURE_DIRECTORY + "/primary_capture_process.json"],
            "exit": pins[CAPTURE_DIRECTORY + "/primary_capture_exit.json"], "exit_code": 0, "stdout": pins[stdout_path], "stderr": pins[stderr_path],
            "helper": pins[helper_path], "supervisor": pins[supervisor_path], "helper_or_supervisor_executed_by_this_audit": False},
        "MSFT_EX31_1": {"body": originals[-1]["body"], "whole_visible_text_read": True, "visible_text_sha256": digest(visible.encode()),
            "source_stated_issuer": "Microsoft Corporation", "source_stated_signer": "Satya Nadella", "source_stated_role": "Chief Executive Officer",
            "source_stated_signature_day": "2026-07-29", "date_precision": "DAY", "scope": "FORM10K_CERTIFICATION_ROLE_POINT_ONLY",
            "original_literal_spans": literal_spans, "index": pins[index_path], "index_original_row_span": sp,
            "index_declared_bytes": declared, "actual_served_bytes": len(msft), "served_minus_index_bytes": 115,
            "served_script_element_pins_without_opaque_resource_path_export": scripts, "script_length_equals_numeric_size_difference": True,
            "difference_cause_or_historical_as_filed_body_established": False, "response_normalized_trimmed_or_reconstructed": False,
            "script_executed": False, "cryptographic_signature_or_natural_person_authenticated": False, "account_person_alias_or_transaction_bridge": False,
            "continuous_2026_role_or_role_at_2026_08_31_proven": False, "historical_machine_availability_at_signature_day_proven": False},
        "all_root_input_pins": list(pins.values()), "root_input_bytes_revalidated_unchanged_at_completion": True,
        "actions_by_this_audit": {"new_HTTP_search_connector_browser_collector_calls": 0, "SQL_DB_or_quota_operations": 0, "protected_journal_DB_touched": False,
            "candidate_auditor_or_prior_four_original_audit_replayed": False, "test_CI_or_application_runs": 0, "root_source_or_evidence_mutations": 0,
            "withheld_bodies_opened_recovered_or_substituted": 0, "source_body_copies_or_sanitized_replacements_created": 0,
            "forecasts_registrations_labels_registry_model_score_or_trading_changes": 0},
        "limits": ["Five new research original response bodies, not app collector/API or recovered durable history, native userWindows, historical/OOS or full21 acceptance.",
            "Whole bytes remain exactly as physically served; source dates, HTTPDate and Last-Modified never backdate machine knowledge or establish historical as-filed byte identity.",
            "The EX31.1 named signer/CEO/July29 point does not prove natural-person/account/alias identity, tenure, Aug31 role, a transaction or sale execution.",
            "The115-byte script element is observed in this whole response. Equal size difference does not prove cause, publisher version or a valid historical reconstruction.",
            "Local PDF text hashes match the capture metadata; decoded text is not pixel/publisher authentication and no decoded body substitute is published.",
            "All request plans are consumed; no failed, unsafe, over-cap or later request gains retry, alternate endpoint or fallback authorization."]}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--capture-root", type=Path, required=True)
    p.add_argument("--proposal", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    assert args.output.parent.resolve() == Path(__file__).resolve().parent and not args.output.is_symlink()
    result = audit(args.capture_root, args.proposal)
    with args.output.open("x", encoding="utf-8") as out:
        out.write(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": result["status"], "originals": 5, "bytes": 2_683_149,
        "MSFT_signature_day": "2026-07-29", "MSFT_served_minus_index_bytes": 115, "HTTP_SQL_actions_by_auditor": 0}))
