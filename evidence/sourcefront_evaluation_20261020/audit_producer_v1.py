"""Offline content audit of four explicit research originals; never recollects."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
from hashlib import sha256
from html import unescape
from io import BytesIO
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urljoin

from pypdf import PdfReader


BASE = "96c4e90a8e72dcd11087e6a08371ee0c924fac83"
COMMON = "39e5958b20b66338ad2029e99908609834d67db3"
HERE = Path(__file__).resolve().parent
KEYS = ("leg_final_articles", "xom_successor_formation", "nadella_form144_packet", "doj_first_amended_complaint")
BODY_SUFFIX = (".htm", ".htm", ".txt", ".pdf")
EXPECTED_BYTES = (14256, 25866, 4770, 3404671)
EXPECTED_SHA = (
    "6cd32d55f62c8d5b0a157b63527a07be489d94b8effe67ef4f0f3d892769d5ba",
    "ef4d1a0c0c71bc3ea536ca3ee333c91db561e99c57448d851225e8b3dcf920ac",
    "69fd38a01da8b03a7b05ebe13d1fdd73e2996cfb3a1581af8971493b979ab40e",
    "3c8eaebc6ebc5106ac3dfbe1751023cf4b16d297ca5e54c65ed9a75bc80fc2a6",
)
LANE_NAMES = ("identity_bulk.json", "identity_bulk_charters.json", "contracts.json", "regulatory.json", "evaluation.json", "sec.json", "healthcare.json")
WITHHELD_PATHS = (
    "evidence/batch4_regulatory_20261013/raw/nhtsa_api_docs.html",
    "evidence/batch4_ir_20261013/primary/ir_authority.html",
    "evidence/batch5_ir_20261014/primary/ir_authority.html",
    "evidence/batch8_regulatory_20261015/raw/njd_case_page.html",
)


def stamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert result.tzinfo is not None, "Naive physical capture clock"
    return result.astimezone(timezone.utc)


def audit(repo: Path, handoffs: Path | None):
    repo = repo.resolve()
    pins = {}

    def read(relative):
        path = repo / relative
        assert path.is_relative_to(repo) and not path.is_symlink()
        assert all(not part.is_symlink() for part in path.parents if part.is_relative_to(repo))
        assert path.suffix not in {".db", ".sqlite", ".sqlite3"}
        raw = path.read_bytes()
        assert len(raw) <= 4_000_000
        pins[relative] = {"path": relative, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}
        return raw

    def load(relative):
        return json.loads(read(relative))

    common = "evidence/sourcefront_20261020/"
    sec_plan = load(common + "request_plan.json")
    doj_plan = load(common + "amended_complaint_plan.json")
    plans = {r["key"]: (sec_plan, r, common + "request_plan.json") for r in sec_plan["urls"]}
    plans[KEYS[-1]] = (doj_plan, {"url": doj_plan["url"], "max_response_bytes": doj_plan["max_response_bytes"]}, common + "amended_complaint_plan.json")
    assert tuple(plans) == KEYS and sec_plan["max_http_transports"] == 3 and doj_plan["max_http_transports"] == 1
    assert sec_plan["max_total_bytes"] == 6_000_000

    # Isolate only the real production pure-body gate, without app imports/init.
    gate_path = "market_checker_app/services/ir_primary_scout_service.py"
    gate_source = read(gate_path).decode()
    node = next(n for n in ast.parse(gate_source).body if isinstance(n, ast.FunctionDef) and n.name == "_has_sensitive_body")
    namespace = {"re": re, "unescape": unescape}
    exec(compile(ast.Module(body=[node], type_ignores=[]), gate_path, "exec"), namespace)
    sensitive = namespace["_has_sensitive_body"]

    now = datetime.now(timezone.utc)
    originals, bodies = [], {}
    for key, suffix, size, digest in zip(KEYS, BODY_SUFFIX, EXPECTED_BYTES, EXPECTED_SHA):
        plan, row, plan_path = plans[key]
        metadata_path = common + "primary/" + key + ".metadata.json"
        reservation_path = common + "primary/" + key + ".reserved.json"
        metadata, reserved = load(metadata_path), load(reservation_path)
        assert reserved["status"] == "REQUEST_RESERVED"
        assert all(metadata[k] == v for k, v in reserved.items() if k != "status")
        assert metadata["key"] == key and metadata["requested_url"] == row["url"] == metadata["final_url"]
        assert metadata["method"] == "GET" and metadata["http_status"] == 200
        assert metadata["request_plan_sha256"] == pins[plan_path]["sha256"]
        assert metadata["retry"] is False and metadata["redirect"] is False
        assert metadata["collector_api_used"] is False and metadata["app_quota_managed_live_claimed"] is False
        assert metadata["quota_database_created_or_modified"] is False and metadata["research_only"] is True
        assert stamp(plan["prepared_at"]) <= stamp(metadata["started_at"]) <= stamp(metadata["observed_at"]) <= now
        assert metadata["status"] == "PUBLIC_ORIGINAL_BYTES_CAPTURED"
        assert metadata["body_transformed"] is False and metadata["public_raw_available"] is True
        body_path = common + "primary/" + key + suffix
        raw = read(body_path)
        assert metadata["body_path"] == key + suffix
        assert len(raw) == size == metadata["bytes"] == int(metadata["response_headers"]["Content-Length"])
        assert sha256(raw).hexdigest() == digest == metadata["raw_sha256"]
        assert size <= row["max_response_bytes"] and metadata["elapsed_seconds"] <= metadata["timeout_seconds"] == 25
        assert metadata["response_headers"]["Content-Encoding"] in (None, "identity")
        assert not sensitive(raw), "Whole original body gate failed; never export a substitute"
        bodies[key] = raw
        originals.append({"key": key, "body": pins[body_path], "metadata": pins[metadata_path], "reservation": pins[reservation_path],
            "request_plan": pins[plan_path], "requested_url": metadata["requested_url"], "HTTP_status_in_original_metadata": 200,
            "started_at": metadata["started_at"], "observed_at": metadata["observed_at"], "complete_original_hash_bytes_verified": True,
            "whole_body_gate_passed": True, "reservation_plan_and_capture_fields_match": True,
            "research_only": True, "app_collector_live_or_historical_availability_claimed": False})
    assert len({r["requested_url"] for r in originals}) == len({r["body"]["sha256"] for r in originals}) == 4

    sec_summary = load(common + "original_verification.json")
    doj_summary = load(common + "amended_complaint_verification.json")
    assert sec_summary["http200_originals"] == 3 and sec_summary["total_original_bytes"] == sum(EXPECTED_BYTES[:3]) == 44892
    assert doj_summary["bytes"] == EXPECTED_BYTES[-1] and doj_summary["sha256"] == EXPECTED_SHA[-1]
    for row in sec_summary["items"]:
        original = next(r for r in originals if r["key"] == row["key"])
        assert row["bytes"] == original["body"]["bytes"] and row["sha256"] == original["body"]["sha256"]

    # Verify exact original closing-filing hrefs; no URL discovery or requests.
    discovery = []
    for key, meta_path, href in (
        ("leg_final_articles", "evidence/batch4_identity_20261013/captures/0727cd2189a797d82f066ded90209aca3f94a15d7480061b0b1c916da146b5a6.json", "d152590dex31.htm"),
        ("xom_successor_formation", "evidence/batch4_identity_20261013/captures/8efa2c0f68db6520ddaf11e5d3f8dee0c3abfa0b14c3f3f79230e2d93b549021.json", "d71068dex3i.htm"),
    ):
        m = load(meta_path)
        source_path = str(Path(meta_path).parent / m["raw_file"])
        raw = read(source_path)
        assert len(raw) == m["bytes"] and sha256(raw).hexdigest() == m["sha256"]
        pattern = rb"<a\b[^>]*href=[\"']" + re.escape(href.encode()) + rb"[\"'][^>]*>.*?</a>"
        matches = list(re.finditer(pattern, raw, re.I | re.S))
        assert len(matches) == 1 and urljoin(m["final_url"], href) == plans[key][1]["url"]
        a = matches[0]
        discovery.append({"key": key, "source": pins[source_path], "source_metadata": pins[meta_path], "source_observed_at": m["observed_at"],
            "original_anchor_start": a.start(), "original_anchor_end_exclusive": a.end(), "original_anchor_sha256": sha256(a.group()).hexdigest(),
            "literal_href": href, "resolved_exact_request_matches": True})

    captures_path = "evidence/batch4_sec_20261013/captures.json"
    captures = load(captures_path)["captures"]
    accession = "0001950047-26-008927"
    for filename in ("nadella_submissions.json", "msft_submissions.json"):
        p = "evidence/batch4_sec_20261013/primary/" + filename
        d = load(p)
        capture = next(r for r in captures if r.get("path") == "primary/" + filename)
        assert capture["sha256"] == pins[p]["sha256"] and capture["size_bytes"] == pins[p]["bytes"]
        recent = d["filings"]["recent"]; i = recent["accessionNumber"].index(accession)
        row = {k: recent[k][i] for k in ("accessionNumber", "form", "filingDate", "acceptanceDateTime", "primaryDocument")}
        assert row["form"] == "144" and row["filingDate"] == "2026-09-01" and row["primaryDocument"] == "xsl144X01/primary_doc.xml"
        discovery.append({"key": "nadella_form144_packet", "source": pins[p], "capture_index": pins[captures_path],
            "source_cik": d["cik"], "source_observed_at": capture["observed_at"], "row_index": i, "accession_row": row,
            "limit": "Accession/account/issuer context; not independent executed-sale, natural-person ownership or legal-role continuity."})
    packet = bodies["nadella_form144_packet"]
    assert accession.encode() in packet
    ccc = list(re.finditer(rb"<ccc>\s*([^<\r\n]*)", packet, re.I))
    assert len(ccc) == 1 and re.fullmatch(rb"[xX]{8}", ccc[0].group(1).strip())
    placeholder = {"exact_packet_bytes": 4770, "exact_packet_sha256": EXPECTED_SHA[2], "ccc_field_count": 1,
        "ccc_field_original_byte_offset": ccc[0].start(), "ccc_value_bytes": 8, "classification": "REPEATED_X_PLACEHOLDER",
        "access_value_present_in_this_field": False, "access_value_exported": False, "original_bytes_modified_or_sanitized": False}

    d = doj_plan["discovery"]; raw = read(d["path"])
    assert pins[d["path"]]["bytes"] == d["bytes"] and pins[d["path"]]["sha256"] == d["sha256"]
    anchor = raw[d["anchor_byte_start"]:d["anchor_byte_end"]]
    assert sha256(anchor).hexdigest() == d["anchor_sha256"] and b'First Amended Complaint' in anchor
    assert b'href="/atr/media/1358786/dl"' in anchor
    discovery.append({"key": "doj_first_amended_complaint", "source": pins[d["path"]],
        "original_anchor_start": d["anchor_byte_start"], "original_anchor_end_exclusive": d["anchor_byte_end"],
        "original_anchor_sha256": d["anchor_sha256"], "literal_href": "/atr/media/1358786/dl", "exact_request_matches": True,
        "label": "First Amended Complaint", "is_separate_Order_or_current_docket": False})
    pdf = PdfReader(BytesIO(bodies["doj_first_amended_complaint"]))
    assert not pdf.is_encrypted and len(pdf.pages) == 91 == doj_summary["original_page_count"]
    text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert len(text) <= 2_000_000 and not sensitive(text.encode())

    # This is bounded public Git history inspection, not remote or collector I/O.
    command = ["git", "-C", str(repo), "grep", "-l", "-F"]
    for item in originals:
        command += ["-e", item["requested_url"]]
    previous = subprocess.run(command + [BASE, "--", "evidence/**/*.json"], capture_output=True, text=True, check=False)
    assert previous.returncode == 1 and not previous.stdout
    tracked = subprocess.check_output(["git", "-C", str(repo), "ls-tree", "-r", "--name-only", "HEAD"], text=True).splitlines()
    assert all(p not in tracked for p in WITHHELD_PATHS)

    lane_rows = []
    for name in LANE_NAMES:
        p = handoffs / name if handoffs is not None else None
        if p is None or not p.is_file():
            lane_rows.append({"name": name, "available_at_audit": False, "reviewed": False})
            continue
        assert not p.is_symlink() and p.parent == handoffs and p.stat().st_size <= 2_000_000
        raw = p.read_bytes(); d = json.loads(raw)
        row = {"name": name, "available_at_audit": True, "bytes": len(raw), "sha256": sha256(raw).hexdigest(),
            "schema_version": d.get("schema_version"), "reviewed": True, "source_availability_created_by_review": False}
        if name == "identity_bulk.json":
            row["candidate_urls"] = [v["url"] for v in d["new_original_candidates"]]
            row["proposals_now_have_preserved_originals"] = all(v in [x["requested_url"] for x in originals] for v in row["candidate_urls"])
        elif name == "identity_bulk_charters.json":
            row["supported_role_specific_facts"] = d["new_supported_facts"]
            row["unchanged_missing_claims"] = d["unchanged_missing_claims"]
            row["new_verified_adoptions"] = d["new_verified_adoptions"]
        elif name == "contracts.json":
            row["result"] = d["result"]
            row["narrow_integration_proposal"] = d["possible_parser_delta_before_any_edit"]
        elif name == "regulatory.json":
            row["status"] = d["status"]
            row["discovered_url"] = d["admissible_next_action"]["url"]
            row["proposal_transport_now_preserved"] = row["discovered_url"] == doj_plan["url"]
            row["original_proposal_was_not_itself_a_capture"] = True
        elif name == "evaluation.json":
            row["lane_status"] = d["lane_status"]
            row["market_or_forecast_inputs_supplied_by_four_new_nonmarket_originals"] = False
        lane_rows.append(row)
    for path, original in list(pins.items()):
        assert sha256((repo / path).read_bytes()).hexdigest() == original["sha256"], "Pinned input changed during audit"
    return {"schema_version": "sourcefront_four_originals_independent_audit_v1", "audited_at": datetime.now(timezone.utc).isoformat(),
        "public_base": BASE, "common_originals_commit": COMMON, "status": "FOUR_ORIGINAL_CONTENT_AND_CHAIN_BINDINGS_VERIFIED",
        "originals": originals, "totals": {"distinct_research_HTTP200_records": 4, "SEC_original_bytes": 44892, "DOJ_original_bytes": 3404671, "total_original_bytes": 3449563},
        "discovery_pins": discovery, "form144_CCC": placeholder, "DOJ_original_pdf": {"pages": len(pdf.pages), "whole_raw_and_extracted_text_gate_passed": True, "extracted_text_not_published": True},
        "prior_public_JSON_exact_target_URL_hits": [], "prior_search_limit": "Available public baseline evidence JSON only; does not reconstruct unavailable transport history.",
        "lane_handoffs": lane_rows, "explicit_safe_input_pins": list(pins.values()),
        "scope_limits": ["Research originals only; not app collector/live, dated machine historical availability, adoption, native userWindows, OOS or full21 acceptance.",
            "Charter authorized capital is a separate role from premerger exchanged/traded class, CUSIP continuity and direct/indirect control.",
            "Form144 is a proposed-sale notice, not proof of an executed sale or natural-person/account/legal-role continuity.",
            "Amended Complaint is a party pleading, not separate Order, complete current docket, adjudicated merits, financial CIK or issuer continuity.",
            "Four nonmarket originals cannot supply missing66-session market data, dated publisher price revisions, genuine forecasts/labels or nativeWindows runtime."],
        "actions": {"new_HTTP_or_search_requests": 0, "collector_calls": 0, "market_retries_or_new_plans": 0, "SQL_or_DB_file_operations": 0,
            "retained_DB_or_journal_touched": False, "withheld_body_paths_opened_or_recovered": 0, "new_forecasts_registrations_labels": 0,
            "tests_CI_or_readiness_reruns": 0, "registry_model_fitter_score_trading_changes": False},
        "withheld_paths_absent_from_Git_catalog_only": list(WITHHELD_PATHS), "no_body_copy_or_substitute_created": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=HERE.parents[1])
    parser.add_argument("--lane-handoffs-root", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = audit(args.repo_root, args.lane_handoffs_root)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": result["status"], "output": str(args.output), "original_count": len(result["originals"]), "total_original_bytes": result["totals"]["total_original_bytes"]}))
