"""One-off offline inspection of two exact root-captured SEC originals.

No source collection, database access, registry updates or runtime adoption.
The source root is explicit; output refuses replacement of an earlier review.
"""
from argparse import ArgumentParser
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE_PACKAGE_COMMIT = "8313e09da8db533a2c75c2da279b10458db4643c"
PREFIX = Path("evidence/sourcefront21_primary_20261021/primary")
SPECS = {
    "p_name_amendment": (9238, "d2e7e86faf3fe1fd2b7701629dc0325a600ebd0d9f1305e0c2f38bb99a33847e"),
    "p_securities_description": (28199, "a87ab222a1958a5df3368b9cfb2c538cd3d63f14eb18e3d9b7102b38d8e1714e"),
}


def pin(path):
    raw = Path(path).read_bytes()
    return {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def visible(raw):
    return " ".join(BeautifulSoup(raw, "html.parser").get_text(" ", strip=True).split())


def inspect(source_root):
    started = datetime.now(timezone.utc)
    source_root = Path(source_root).resolve()
    sources, bodies, texts, passages = {}, {}, {}, {}
    for key, (size, digest) in SPECS.items():
        body_path = source_root / PREFIX / (key + ".htm")
        metadata_path = source_root / PREFIX / (key + ".metadata.json")
        meta = json.loads(metadata_path.read_bytes())
        if (meta["status"] != "PUBLIC_ORIGINAL_BYTES_CAPTURED" or meta["http_status"] != 200
                or meta["requested_url"] != meta["final_url"] or meta["raw_sha256"] != digest
                or meta["bytes"] != size or meta["body_path"] != body_path.name
                or meta["body_transformed"] is not False or meta["original_sensitive_body_gate_passed"] is not True):
            raise ValueError("The exact approved original metadata is unavailable")
        raw = body_path.read_bytes()
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Whole original byte pin failed")
        body_text = visible(raw)
        sources[key] = {
            "body_path": (PREFIX / body_path.name).as_posix(), "body_sha256": digest, "body_bytes": size,
            "metadata_path": (PREFIX / metadata_path.name).as_posix(), "metadata_pin": pin(metadata_path),
            "metadata_original": meta, "whole_original_inspected": True,
            "normalized_visible_text_sha256": hashlib.sha256(body_text.encode()).hexdigest(),
            "normalization_is_derived_not_a_replacement_publisher_body": True,
            "archive_issuer_cik_scope": "0001474432",
            "standalone_body_explicit_issuer_cik": None,
            "source_publication_time": meta["source_publication_time"],
            "http_last_modified_is_publication_or_legal_effectiveness": False,
            "CUSIP_label_raw_count": len(re.findall(rb"CUSIP", raw, re.I)),
            "CUSIP_label_visible_count": len(re.findall(r"CUSIP", body_text, re.I)),
            "old_specimen_CUSIP_literal_occurs_in_new_body": "74624M102" in body_text or "74624M 10 2" in body_text,
            "CUSIP": None, "CUSIP_status": "UNKNOWN_EXPLICIT_IDENTIFIER_NOT_FOUND_IN_WHOLE_ORIGINAL",
            "new_symbol_P_explicitly_bound": bool(re.search(r"(?:trading|ticker) symbol [\"“]?P\b", body_text)),
        }
        if sources[key]["CUSIP_label_raw_count"] or sources[key]["CUSIP_label_visible_count"] or sources[key]["old_specimen_CUSIP_literal_occurs_in_new_body"]:
            raise ValueError("CUSIP absence observation changed; inspect rather than infer")
        if b"0001474432" in raw:
            raise ValueError("Standalone body CIK observation changed")
        bodies[key], texts[key] = raw, body_text

    def span(identifier, source, first, last=None):
        raw = bodies[source]
        begin = raw.index(first.encode())
        end = begin + len(first.encode()) if last is None else raw.index(last.encode(), begin) + len(last.encode())
        excerpt = raw[begin:end]
        if not 1 <= len(excerpt) <= 1800:
            raise ValueError("Source-local excerpt exceeds its explicit bounded span")
        text = visible(excerpt)
        if text not in texts[source]:
            raise ValueError("Source-local text does not match the inspected whole original")
        passages[identifier] = {
            "source_key": source, "source_body_sha256": sources[source]["body_sha256"],
            "offset_basis": "Original whole-body bytes, zero-based, end-exclusive",
            "byte_start": begin, "byte_end_exclusive": end,
            "raw_excerpt_utf8": excerpt.decode("utf-8"), "raw_excerpt_sha256": hashlib.sha256(excerpt).hexdigest(),
            "normalized_visible_passage": text,
        }

    span("AMENDMENT_OLD_LEGAL_NAME", "p_name_amendment", "Pure Storage, Inc.")
    span("AMENDMENT_HISTORIC_FILING", "p_name_amendment", "The original Certificate", "Os76, Inc.")
    span("AMENDMENT_ARTICLE_I", "p_name_amendment", "Article I of", "Everpure, Inc.")
    span("AMENDMENT_EFFECTIVE_POINT", "p_name_amendment", "The terms and provisions", "February 23, 2026.")
    span("AMENDMENT_EXECUTION_DAY", "p_name_amendment", "IN WITNESS WHEREOF", "day of February, 2026.")
    span("DESCRIPTION_ISSUER_CLASS", "p_securities_description", "Everpure, Inc.", "&#8220;common stock.&#8221;")
    span("DESCRIPTION_REGISTRATION_SYMBOL", "p_securities_description", "Our common stock is registered", "&#8220;PSTG.&#8221;")
    span("DESCRIPTION_COMMON_AUTHORITY_PAR", "p_securities_description", "Our certificate of incorporation authorizes", "shares are designated as Class B common stock)")
    span("DESCRIPTION_CLASS_B_LIMIT", "p_securities_description", "While our certificate", "may be issued in the future.")
    span("DESCRIPTION_CONDITIONAL_DIVIDENDS", "p_securities_description", "Subject to any outstanding series", "from time to time by the board of directors.")
    span("DESCRIPTION_VOTE", "p_securities_description", "Holders of our common stock are entitled", "a vote of our stockholders.")
    span("DESCRIPTION_NONPREEMPTIVE_RIGHTS", "p_securities_description", "Our common stock is not entitled", "sinking fund provisions.")
    span("DESCRIPTION_TRANSFER_AGENT", "p_securities_description", "The transfer agent and registrar", "Equiniti Trust Company LLC.")

    old_handoff_path = ROOT / "evidence/batch10_identity_20261018/handoff.json"
    old_handoff = json.loads(old_handoff_path.read_bytes())
    clock_handoff_path = ROOT / "evidence/batch10_certificate_clock_fix_20261018/handoff.json"
    clock_handoff = json.loads(clock_handoff_path.read_bytes())
    plan_path = HERE / "source_plan.json"
    report = {
        "schema_version": "sourcefront21_primary_identity_review_v1",
        "review_kind": "OFFLINE_SOURCE_LOCAL_FACT_REVIEW_NO_ADOPTION",
        "source_package_commit": SOURCE_PACKAGE_COMMIT, "source_root_read_only": str(source_root),
        "preserved_source_plan_commit": "26eb46460e77b53d9f6c31257395c7af4e5a6bd6",
        "source_plan": {"path": "evidence/sourcefront21_identity_20261021/source_plan.json", **pin(plan_path)},
        "extractor": {"path": "evidence/sourcefront21_identity_20261021/extract_primary.py", **pin(__file__)},
        "review_started_at": started.isoformat(), "sources": sources, "source_local_passages": passages,
        "name_amendment_facts": {
            "old_legal_name_literal": "Pure Storage, Inc.", "new_legal_name_literal": "Everpure, Inc.",
            "amended_article": "Article I", "documentary_name_amendment_supported": True,
            "execution_day_literal": "20th day of February, 2026", "execution_on": "2026-02-20",
            "execution_time": None, "execution_authenticity_or_person_identity_verified": False,
            "effective_clause_literal": passages["AMENDMENT_EFFECTIVE_POINT"]["normalized_visible_passage"],
            "effective_local_date": "2026-02-23", "effective_local_time": "00:01",
            "effective_timezone_literal": "Eastern Time", "effective_utc_timestamp": None,
            "utc_conversion_or_timezone_offset_inferred": False,
            "execution_is_effective_or_capture_time": False,
            "historic_original_filing_on": "2009-10-02", "historic_name_literal": "Os76, Inc.",
            "historic_filing_date_is_action_or_instrument_day": False,
            "class_symbol_or_CUSIP_continuity_proven_by_name_amendment": False,
            "evidence_passage_ids": ["AMENDMENT_OLD_LEGAL_NAME", "AMENDMENT_ARTICLE_I", "AMENDMENT_EFFECTIVE_POINT", "AMENDMENT_EXECUTION_DAY", "AMENDMENT_HISTORIC_FILING"],
        },
        "standalone_securities_description_facts": {
            "issuer_name_literal": "Everpure, Inc.", "outstanding_class_title_literal": "Class A common stock",
            "source_says_one_class_outstanding": True, "registered_under_literal": "Section 12 of the Securities Exchange Act of 1934, as amended",
            "exchange_literal": "New York Stock Exchange (NYSE)", "trading_symbol_literal": "PSTG",
            "common_stock_reference_is_defined_as_Class_A_in_this_document": True,
            "common_par_value_usd": "0.0001", "common_par_scope": "Authorized common shares; ClassA and ClassB designations appear in the same source clause.",
            "authorized_common_shares": 2250000000, "authorized_Class_A_shares": 2000000000,
            "authorized_Class_B_shares": 250000000, "Class_A_outstanding_share_count": None,
            "Class_B_outstanding_share_count_as_source_stated": 0, "no_new_Class_B_shares_source_statement": True,
            "authorized_quantities_are_outstanding_quantities": False,
            "votes_per_common_share_as_source_stated": 1,
            "dividends_are_conditional_on_board_declaration_legal_assets_and_preferred_rights": True,
            "preemptive_rights_as_source_stated": False,
            "conversion_redemption_or_sinking_fund_rights_for_common_as_source_stated": False,
            "transfer_agent_and_registrar_literal": "Equiniti Trust Company LLC",
            "standalone_original_class_registration_symbol_fact_supported": True,
            "source_version_anchor": "SEC accession000147443226000027, original EX-4.3 attachment",
            "own_explicit_class_as_of_date": None, "own_explicit_class_effective_on": None,
            "historical_publication_or_availability_verified": False,
            "source_version_is_same_day_independent_instrument_anchor": False,
            "point_in_time_Class_A_CUSIP_P_symbol_bridge_supported": False,
            "CUSIP": None, "P_symbol_effective_on": None,
            "evidence_passage_ids": ["DESCRIPTION_ISSUER_CLASS", "DESCRIPTION_REGISTRATION_SYMBOL", "DESCRIPTION_COMMON_AUTHORITY_PAR", "DESCRIPTION_CLASS_B_LIMIT", "DESCRIPTION_VOTE", "DESCRIPTION_CONDITIONAL_DIVIDENDS", "DESCRIPTION_NONPREEMPTIVE_RIGHTS", "DESCRIPTION_TRANSFER_AGENT"],
        },
        "comparison_with_preserved_specimen": {
            "earlier_handoff": {"path": old_handoff_path.relative_to(ROOT).as_posix(), **pin(old_handoff_path)},
            "current_clock_corrected_handoff": {"path": clock_handoff_path.relative_to(ROOT).as_posix(), **pin(clock_handoff_path)},
            "current_clock_corrected_report_path": clock_handoff["actual_current_report_path"],
            "earlier_image_pin_from_preserved_handoff": old_handoff["actual_sources"]["image"]["body_pin"],
            "earlier_visual_measurement_type": old_handoff["literal_source_facts"]["annotation_type"],
            "earlier_image_literal_CUSIP": old_handoff["literal_source_facts"]["cusip_literal"],
            "earlier_image_canonical_CUSIP": old_handoff["literal_source_facts"]["cusip_canonical"],
            "earlier_image_is_SPECIMEN_with_blank_Dated_holder_number_and_shares": True,
            "image_or_OCR_repeated_this_review": False,
            "new_original_description_is_independent_of_pixel_transcription": True,
            "description_and_specimen_share_same_SEC_accession": True,
            "same_accession_or_matching_Class_A_par_supplies_issued_validity_day": False,
            "old_image_CUSIP_transferred_to_new_class_fact": False,
            "old_image_CUSIP_plus_name_effective_day_is_independent_dated_instrument_proof": False,
        },
        "remaining_blockers": [
            "Neither new whole original explicitly binds a CUSIP to the described registeredClassA instrument.",
            "The description states PSTG, not a completed PSTG-to-P action or exact P-effective day.",
            "The description has no own explicit class/instrument as-of or effective day; publisher publication metadata remainsnull. HTTP LastModified is not substituted.",
            "The name amendment supplies an exact legal-name effective point, not the instrument/symbol transition or issued-validity interval.",
            "The old SPECIMEN image CUSIP remains an undated derived visual observation. Matching issuer/class/par or same accession cannot supply its missing date.",
            "Existing parent10-K/index declared-versus-served byte and calendar-day conflicts are not repaired by either new body.",
        ],
        "unacquired_candidate": {"candidate_id": "P_HISTORICAL_CLASS_A_CHARTER", "status": "NOT_ACQUIRED_BY_THIS_REVIEW_OR_THE_TWO_SELECTED_CAPTURES", "fallback_or_request_attempted": False},
        "work_counts": {"new_HTTP_search_connector_browser_or_collector_calls": 0, "SQL_or_DB_operations": 0,
                        "protected_journal_opens_hashes_or_copies": 0, "new_original_bodies_inspected_read_only": 2,
                        "new_capture_metadata_inspected_read_only": 2, "image_or_remote_OCR_calls": 0,
                        "test_runs": 0, "source_bytes_rewritten": 0, "withheld_bodies_read_restored_or_substituted": 0},
        "acceptance": {"registry_adoptions": 0, "new_verified_instruments": 0, "current_Q3_coverage": 0,
                       "full_specialist_acceptance": "0/21", "production_registry_changed": False,
                       "automatic_alias_allowed": False, "interval_extension_allowed": False,
                       "canonical687_and_historicalQ2_150_preserved": True,
                       "code_model_score_fitter_budget_or_trading_changed": False},
    }
    completed = datetime.now(timezone.utc)
    latest = max(datetime.fromisoformat(row["metadata_original"]["known_at"]) for row in sources.values())
    if completed < started or started < latest:
        raise ValueError("The physical interpretation clock cannot precede source knowledge or move backwards")
    report.update(review_recorded_at=completed.isoformat(), knowledge_at=completed.isoformat(),
                  source_knowledge_at=latest.isoformat(), historical_knowledge_reconstructed=False)
    report["review_sha256"] = hashlib.sha256(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return report


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    destination = Path(args.output).resolve()
    if destination.parent != HERE or destination.exists():
        raise ValueError("Keep this review in its owned evidence folder and preserve prior output")
    report = inspect(args.source_root)
    raw = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)+"\n").encode()
    destination.write_bytes(raw)
    print(json.dumps({"path": str(destination), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                      "source_knowledge_at": report["source_knowledge_at"], "review_started_at": report["review_started_at"],
                      "review_recorded_at": report["review_recorded_at"], "passages": len(report["source_local_passages"]),
                      "registry_adoptions": 0, "new_source_requests": 0}, indent=2))
