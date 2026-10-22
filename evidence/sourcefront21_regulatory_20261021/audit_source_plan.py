"""Offline source-link and tracked-history audit. Never executes source requests.

This audits only retained public repository bytes. It does not invoke an app
collector, review parser, tests, a database, browser, connector or web search.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from html import unescape
from io import BytesIO
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urljoin, urlsplit

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from market_checker_app.services.doj_enforcement_scope_review import _SENSITIVE

BASE = "9d60cc650ff0d64b720f84fabfe1348e44c6bcf4"
BODY = "evidence/sourcefront_20261020/primary/doj_first_amended_complaint.pdf"
BODY_META = "evidence/sourcefront_20261020/primary/doj_first_amended_complaint.metadata.json"
INDEX = "evidence/batch8_regulatory_20261015/raw/doj_case_index.html"
INDEX_META = "evidence/batch8_regulatory_20261015/raw/doj_case_index.metadata.json"
INDEX_URL = "https://www.justice.gov/atr/case/us-and-plaintiff-states-v-apple-inc"
SUMMARY = "evidence/epa_echo_live_20261002.json"
EPA_ENDPOINT = "https://echodata.epa.gov/echo/echo_rest_services.get_facility_info"
EPA_DOCUMENTATION = "https://echo.epa.gov/tools/web-services"
OLD_ORDER = "https://ecf.njd.uscourts.gov/cgi-bin/show_public_doc?2024cv04055-284"
OLD_ORDER_META = "evidence/batch8_regulatory_20261015/raw/court_public_order.metadata.json"
PRIOR = "evidence/sourcefront_regulatory_20261020/initial_source_feasibility_snapshot.json"
TEXT_SUFFIXES = {".json", ".jsonl", ".ndjson", ".md", ".txt", ".log", ".yaml", ".yml", ".py"}
RECEIPT_KEYS = {"capture_id", "key", "requested_url", "final_url", "status", "http_status",
    "started_at", "observed_at", "reserved_at", "raw_response_sha256", "raw_sha256",
    "raw_response_bytes", "bytes", "error_type", "public_raw_available", "origin",
    "automatic_redirects", "redirect", "method"}
WITHHELD = (
    "evidence/batch4_regulatory_20261013/raw/nhtsa_api_docs.html",
    "evidence/batch4_ir_20261013/primary/ir_authority.html",
    "evidence/batch5_ir_20261014/primary/ir_authority.html",
    "evidence/batch8_regulatory_20261015/raw/njd_case_page.html",
)


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def digest(body):
    return hashlib.sha256(body).hexdigest()


def pin(path, body=None):
    body = (ROOT / path).read_bytes() if body is None else body
    return {"path": str(path), "bytes": len(body), "sha256": digest(body)}


def write_new(name, value):
    if "known_at" in value:
        finished = datetime.now(timezone.utc)
        assert finished >= datetime.fromisoformat(value["known_at"])
        value["known_at"] = finished.isoformat()
    with (FOLDER / name).open("xb") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n")


def pointer(token):
    return str(token).replace("~", "~0").replace("/", "~1")


def walk(value, path=""):
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from walk(child, path + "/" + pointer(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, path + "/" + str(index))


def main():
    started = datetime.now(timezone.utc)
    assert git("rev-parse", "HEAD").decode().strip() == BASE
    assert git("diff", "--name-only", BASE).decode() == ""
    assert all(not (ROOT / path).exists() for path in WITHHELD)
    body = (ROOT / BODY).read_bytes()
    assert (len(body), digest(body)) == (
        3_404_671, "3c8eaebc6ebc5106ac3dfbe1751023cf4b16d297ca5e54c65ed9a75bc80fc2a6")
    metadata = json.loads((ROOT / BODY_META).read_bytes())
    reader = PdfReader(BytesIO(body), strict=True)
    assert not reader.is_encrypted and len(reader.pages) == 91
    page_records, text_urls, uri_annotations = [], [], []
    characters = 0
    for number, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        characters += len(text)
        assert characters <= 2_000_000 and not _SENSITIVE.search(unescape(text).encode())
        links = list(re.finditer(r"https?://[^\s<>\"']+", text, re.I))
        for match in links:
            text_urls.append({"page": number, "character_span": list(match.span()), "literal": match[0]})
        annotations = 0
        for reference in page.get("/Annots", []):
            action = reference.get_object().get("/A")
            if action and action.get_object().get("/S") == "/URI":
                uri = str(action.get_object().get("/URI"))
                assert not _SENSITIVE.search(unescape(uri).encode())
                uri_annotations.append({"page": number, "literal_uri": uri})
                annotations += 1
        page_records.append({"page": number, "page_text_characters": len(text),
            "page_text_utf8_bytes": len(text.encode()), "page_text_sha256": digest(text.encode()),
            "literal_http_urls": len(links), "external_uri_annotations": annotations})
    assert not text_urls and not uri_annotations
    pdf_finished = datetime.now(timezone.utc)
    write_new("doc51_link_audit.json", {
        "started_at": started.isoformat(), "known_at": pdf_finished.isoformat(),
        "whole_original": pin(BODY, body), "source_metadata": pin(BODY_META),
        "source_observed_at": metadata["observed_at"], "page_count": 91,
        "decoded_characters": characters, "literal_http_urls": text_urls,
        "external_uri_annotations": uri_annotations, "pages": page_records,
        "purpose": "New route discovery only; no Doc51 case/entity interpretation or test replay.",
        "limit": "No claim that the publisher has no Order/docket or that uninspected PDF container features cannot exist.",
        "HTTP_requests": 0, "database_operations": 0,
    })

    index = (ROOT / INDEX).read_bytes()
    assert (len(index), digest(index)) == (
        107_793, "93e092c53234733cd13e4b6affc8cb49e4d5b1230920dc276b9c77c1c59491b3")
    assert not _SENSITIVE.search(unescape(index.decode()).encode())
    anchor_regex = re.compile(rb"<a\b[^>]*\bhref\s*=\s*([\"'])(?P<href>.*?)\1[^>]*>(?P<label>.*?)</a\s*>", re.I | re.S)
    anchors, document_links, court_epa_links = [], [], []
    for match in anchor_regex.finditer(index):
        href = unescape(match["href"].decode())
        absolute = urljoin(INDEX_URL, href)
        label = " ".join(re.sub(r"<[^>]+>", " ", unescape(match["label"].decode())).split())
        record = {"exact_href": href, "absolute_url": absolute, "label": label,
            "anchor_raw_byte_span": list(match.span()), "href_raw_byte_span": list(match.span("href")),
            "anchor_raw_sha256": digest(match[0])}
        anchors.append(record)
        if href.startswith("/atr/media/"):
            record = dict(record, exact_anchor_html=match[0].decode())
            paragraph_start = index.rfind(b"<p", 0, match.start())
            paragraph_end = index.find(b"</p>", match.end()) + 4
            if 0 <= match.start() - paragraph_start < 300 and 0 < paragraph_end - match.end() < 100:
                paragraph = index[paragraph_start:paragraph_end]
                record["publisher_paragraph"] = {"raw_byte_span": [paragraph_start, paragraph_end],
                    "raw_sha256": digest(paragraph), "exact_html": paragraph.decode()}
            document_links.append(record)
        hostname = urlsplit(absolute).hostname or ""
        if hostname.endswith(("uscourts.gov", "govinfo.gov", "epa.gov")):
            court_epa_links.append(record)
    assert len(document_links) == 8 and not court_epa_links
    assert {link["exact_href"].split("?", 1)[0] for link in document_links} == {
        "/atr/media/1368741/dl", "/atr/media/1358786/dl", "/atr/media/1358781/dl", "/atr/media/1344606/dl"}
    index_meta = json.loads((ROOT / INDEX_META).read_bytes())
    date_match = re.search(rb"September 17, 2024", index)
    assert date_match is not None
    write_new("doj_index_link_audit.json", {
        "known_at": datetime.now(timezone.utc).isoformat(), "whole_original": pin(INDEX, index),
        "source_metadata": pin(INDEX_META), "source_observed_at": index_meta["observed_at"],
        "source_url": INDEX_URL, "all_html_anchor_count": len(anchors),
        "case_document_link_occurrences": document_links, "distinct_case_document_families": 4,
        "literal_court_govinfo_or_epa_links": court_epa_links,
        "displayed_update_date": {"literal": date_match[0].decode(), "raw_byte_span": list(date_match.span()),
            "display_timezone": "UNKNOWN", "is_current_complete_docket": False},
        "source_scope": "Publisher case-index snapshot. The existing material includes party documents, not a separate Order/current complete docket.",
        "HTTP_requests": 0,
    })

    targets = sorted({link["absolute_url"] for link in document_links} | {
        INDEX_URL, EPA_ENDPOINT, EPA_DOCUMENTATION, OLD_ORDER})
    assert len(targets) == 11
    target_patterns = {url: re.compile(re.escape(url.encode()) + rb"(?![A-Za-z0-9/?#&=._%+-])") for url in targets}
    tracked = [path.decode() for path in git("ls-tree", "-r", "-z", "--name-only", BASE).split(b"\0") if path]
    inventory, history, unparsed = [], {url: {"references": [], "receipt_like_records": []} for url in targets}, []
    for relative in tracked:
        if Path(relative).suffix.lower() not in TEXT_SUFFIXES:
            continue
        # Only tracked repository text is opened; DB/journal/WAL/SHM, archives,
        # HTML bodies and PDFs are outside this metadata/history scan.
        content = (ROOT / relative).read_bytes()
        record = pin(relative, content)
        inventory.append(record)
        for url, pattern in target_patterns.items():
            offsets = [list(match.span()) for match in pattern.finditer(content)]
            if offsets:
                history[url]["references"].append({"file": record, "exact_url_raw_byte_spans": offsets,
                    "meaning": "Reference alone is not proof of an attempted request."})
        if Path(relative).suffix.lower() != ".json":
            continue
        try:
            parsed = json.loads(content.decode("utf-8-sig"))
        except (UnicodeError, ValueError, RecursionError):
            unparsed.append({"file": record, "reason": "JSON_METADATA_NOT_STRUCTURALLY_PARSEABLE"})
            continue
        for location, obj in walk(parsed):
            url = obj.get("requested_url")
            if isinstance(url, str) and url in history and any(key in obj for key in ("started_at", "observed_at", "reserved_at")):
                receipt = {key: value for key, value in obj.items() if key in RECEIPT_KEYS
                    and isinstance(value, (str, bool, int, float, type(None)))}
                history[url]["receipt_like_records"].append({"file": record, "json_pointer": location,
                    "record": receipt,
                    "meaning": "Retained receipt/reservation/reference; duplicates are not counted as new transports."})
    assert git("diff", "--name-only", BASE).decode() == ""
    for url in history:
        history[url]["history_scope"] = "All tracked text suffixes listed in inventory at the pinned base. Protected original DB/journal and outside-repository histories were not accessed."
        history[url]["outside_tracked_original_history"] = "UNOBSERVED"
    write_new("tracked_history_inventory.json", {
        "base_commit": BASE, "base_tree": git("rev-parse", BASE + "^{tree}").decode().strip(),
        "known_at": datetime.now(timezone.utc).isoformat(), "text_suffixes": sorted(TEXT_SUFFIXES),
        "tracked_text_files_scanned": len(inventory), "tracked_text_bytes_scanned": sum(item["bytes"] for item in inventory),
        "files": inventory, "unparsed_JSON": unparsed,
        "protected_database_or_journal_opened_or_hashed": False,
        "archives_HTML_PDFs_not_part_of_metadata_history_scan": True,
    })
    write_new("tracked_url_history.json", {
        "base_commit": BASE, "known_at": datetime.now(timezone.utc).isoformat(),
        "inventory": pin(str((FOLDER / "tracked_history_inventory.json").relative_to(ROOT))),
        "exact_targets": history, "HTTP_requests": 0, "database_operations": 0,
    })
    prior = json.loads((ROOT / PRIOR).read_bytes())
    order_reference = next(row for row in prior["source_grounded_crossreferences"]
                           if row["kind"] == "OPINION_REFERENCES_SEPARATE_ORDER")
    old_order = json.loads((ROOT / OLD_ORDER_META).read_bytes())
    assert old_order["status"] == "TRANSPORT_OR_CAPTURE_ERROR" and old_order["requested_url"] == OLD_ORDER
    assert old_order["public_raw_available"] is False and old_order["archive_path"] is None
    assert old_order["raw_response_bytes"] is None and old_order["raw_response_sha256"] is None
    summary = (ROOT / SUMMARY).read_bytes()
    parsed_summary = json.loads(summary)
    assert parsed_summary["positive"]["frs_registry_id"] == "110019986586"
    endpoint_span = [summary.index(EPA_ENDPOINT.encode()), summary.index(EPA_ENDPOINT.encode()) + len(EPA_ENDPOINT)]
    source_availability = json.loads((ROOT / "evidence/batch11_regulatory_20261018/source_availability.json").read_bytes())
    assert source_availability["whole_original_body_retained"] is False
    assert source_availability["actual_source_record"]["raw_response_bytes"] is None
    final = datetime.now(timezone.utc)
    plan = {
        "schema_version": "sourcefront21-regulatory-literal-link-source-plan-v1",
        "review_started_at": started.isoformat(), "known_at": final.isoformat(),
        "base_commit": BASE, "base_tree": git("rev-parse", BASE + "^{tree}").decode().strip(),
        "status": "BLOCKED", "candidate_limit": 3, "eligible_candidate_count": 0, "candidates": [],
        "source_requests_reserved": 0, "source_requests_performed": 0, "source_requests_allowed_by_this_plan": 0,
        "scope_sought": ["Separate original Order in Apple case2:24-cv-04055",
            "Current complete docket/disposition for the same exact case",
            "Whole original EPA FRS facility/program evidence for exact historical DOW facility110019986586"],
        "new_source_progress": {
            "newly_available_whole_Doc51_link_audit": pin(str((FOLDER / "doc51_link_audit.json").relative_to(ROOT))),
            "whole_original": pin(BODY, body), "original_source_observed_at": metadata["observed_at"],
            "literal_HTTP_links_in_91_decoded_pages": 0, "external_URI_annotations": 0,
            "does_not_establish_any_publisher_wide_absence": True,
            "completed_Doc51_case_review_and_test_suites_repeated": False},
        "route_assessments": [
            {"scope": "SEPARATE_ORIGINAL_ORDER", "status": "BLOCKED_NO_NEW_LITERAL_LINK",
             "retained_crossreference_proof": pin(PRIOR), "original_opinion_clause": order_reference,
             "exact_Order_link": None, "exact_Order_link_byte_offset": None,
             "prior_attempted_locator": OLD_ORDER, "prior_attempt_metadata": pin(OLD_ORDER_META),
             "prior_result": {"status": old_order["status"], "observed_at": old_order["observed_at"],
                 "error_type": old_order["error_type"], "http_status": old_order.get("http_status"),
                 "original_body_bytes": None, "original_body_sha256": None},
             "reason": "The retained opinion says a separate Order accompanies it but supplies no retrieval URI or Order document number. The previously attempted failed locator is not a new eligible route; changing its number would be invented."},
            {"scope": "CURRENT_COMPLETE_DOCKET_OR_DISPOSITION", "status": "BLOCKED_NO_NEW_LITERAL_LINK",
             "parent_primary": pin(INDEX, index), "parent_metadata": pin(INDEX_META),
             "link_audit": pin(str((FOLDER / "doj_index_link_audit.json").relative_to(ROOT))),
             "exact_current_docket_or_disposition_link": None, "exact_link_byte_offset": None,
             "parent_source_observed_at": index_meta["observed_at"],
             "reason": "The retained index links only opposition, First Amended Complaint, pre-motion letter and original complaint. Doc51 has no decoded HTTP link or external URI annotation. The index itself is already attempted and is not a current complete court docket.",
             "unattempted_out_of_scope_link": next(link for link in document_links
                 if link["exact_href"] == "/atr/media/1358781/dl"),
             "out_of_scope_reason": "The paired dated publisher paragraph lists May30,2024 pre-motion correspondence. Its label/date do not support obtaining the separate June2025 Order or a current docket/disposition; it is not proposed for a request."},
            {"scope": "WHOLE_ORIGINAL_FRS_FACILITY_PROGRAM_BODY", "status": "BLOCKED_SUMMARY_ONLY_AND_UNKNOWN_ORIGINAL_HISTORY",
             "derived_summary": pin(SUMMARY, summary), "summary_is_primary_original": False,
             "summary_endpoint_literal": EPA_ENDPOINT, "summary_endpoint_raw_byte_span": endpoint_span,
             "exact_primary_parent_FRS_link": None, "exact_primary_link_byte_offset": None,
             "facility_registry_id_from_historical_summary_only": "110019986586",
             "facility_name_from_historical_summary_only": "DOW INC",
             "historical_original_claimed_sha256": parsed_summary["positive"]["response_sha256"],
             "historical_original_bytes": None, "historical_original_body_available": False,
             "retained_availability_proof": pin("evidence/batch11_regulatory_20261018/source_availability.json"),
             "exact_original_request_parameters": None, "original_durable_history": "UNOBSERVED",
             "current_query_count": None, "current_program_count": None, "program_identity_or_dates_verified": False,
             "reason": "A derived summary's generic endpoint/documentation strings are not a whole primary-parent facility/program link. Original bytes, exact request scope and original history are missing; no replacement body or zero count is invented."},
        ],
        "recorded_attempt_history": pin(str((FOLDER / "tracked_url_history.json").relative_to(ROOT))),
        "history_inventory": pin(str((FOLDER / "tracked_history_inventory.json").relative_to(ROOT))),
        "bounds_for_any_separately_approved_future_exact_primary_request": {
            "coordinator_only_after_peer_audit_and_prior_reservation": True,
            "maximum_candidates": 3, "maximum_transport_per_reserved_candidate": 1,
            "timeout_seconds": 25, "maximum_original_bytes": 4_000_000,
            "redirects": False, "retries": False, "whole_sensitive_body_withheld_metadata_only": True},
        "next_action": "No HTTP is authorized by this blocked plan. A separately authorized discovery must first supply an unattempted exact official literal link, source-local scope and admissible original request history. Do not retry the failed locator, refresh an already attempted index, guess document numbers, use withheld pages or replace the FRS summary with fabricated bytes.",
        "NHTSA_live": "APPROVAL_BLOCKED_NO_RETRY_ALTERNATE_TRANSPORT_BYPASS_OR_STATE_REWRITE",
        "source_clock_is_historical_machine_availability": False,
        "case_name_is_financial_CIK_or_legal_continuity": False,
        "Doc51_filed_on": "2024-06-11", "Doc51_scope": "ALLEGATIONS_ONLY",
        "separate_Order_current_docket_merits_disposition_issuer_identity_verified": False,
        "four_whole_withheld_paths_absent_and_unopened": list(WITHHELD),
        "HTTP_search_browser_connector_collector_actions": 0, "SQL_database_or_quota_actions": 0,
        "protected_journal_database_opened_hashed_copied_replayed_or_reset": False,
        "production_shared_registry_config_status_or_existing_evidence_changes": False,
        "adoptions": 0, "full_specialist_acceptance": False,
        "verifier": pin(str(Path(__file__).relative_to(ROOT))),
    }
    assert final >= pdf_finished >= started
    write_new("source_plan.json", plan)
    print(json.dumps({"status": plan["status"], "eligible_candidates": 0,
        "source_plan": pin(str((FOLDER / "source_plan.json").relative_to(ROOT))),
        "history_files_scanned": len(inventory), "unparsed_JSON": len(unparsed),
        "HTTP": 0, "database": 0}, sort_keys=True))


if __name__ == "__main__":
    main()
