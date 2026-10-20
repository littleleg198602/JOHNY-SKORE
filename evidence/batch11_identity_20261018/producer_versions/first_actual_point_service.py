"""Explicit, read-only joining of independent SEC declarations at one day.

A source-asserted point is not an active interval, corporate action, alias or
verified instrument. No catalog, specimen, quarter or registry fills a gap.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import re
from xml.etree import ElementTree as ET

from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body
from market_checker_app.services.sec13f_identity_bulk import _clock, _name, _valid_cusip, parse_ticker_filing
from market_checker_app.services.sec_corporate_action_bridge import class_terms, issuer_names
from market_checker_app.services.sec_instrument_class_scope_review import _SubjectIndex, _registered, _schedule_fact, _schedule_index
from market_checker_app.services.sec_symbol_action_scope_review import _captures, _encoded, _filing, _snapshot, _url

VERSION = "sec-dated-instrument-point-review-v1"
_KEY = re.compile(r"[A-Za-z0-9_-]{1,80}")
_FORMS = {"SCHEDULE 13G", "SCHEDULE 13G/A", "SCHEDULE 13D", "SCHEDULE 13D/A"}
_COVER_FORMS = {"8-K", "10-K", "10-Q", "20-F", "40-F", "6-K"}
_COVER_SELECTION = {"source_key", "context_ref", "issuer_name", "registration_section", "table_number",
                    "header_row", "security_row", "header_pin", "security_pin"}


def _physical_clock():
    return datetime.now(timezone.utc)


def _request(request):
    if (not isinstance(request, dict) or set(request) != {"anchor_on", "identity", "instrument", "cover"}
            or len(_encoded(request)) > 48_000 or _has_sensitive_body(_encoded(request))):
        raise ValueError("Select an exact bounded safe dated-point request")
    anchor = request["anchor_on"]
    if anchor is not None and (not isinstance(anchor, str) or anchor != date.fromisoformat(anchor).isoformat()):
        raise ValueError("Select one exact ISO day or explicitly UNKNOWN; a quarter is not a point")
    identity = request["identity"]
    if (not isinstance(identity, dict) or set(identity) != {"issuer_cik", "issuer_name", "ticker", "class_title", "cusip"}
            or not isinstance(identity["issuer_cik"], str) or not re.fullmatch(r"[0-9]{10}", identity["issuer_cik"])
            or int(identity["issuer_cik"]) == 0 or not isinstance(identity["issuer_name"], str)
            or not 1 <= len(identity["issuer_name"]) <= 300 or not _name(identity["issuer_name"])
            or not isinstance(identity["ticker"], str) or not re.fullmatch(r"[A-Z0-9./-]{1,15}", identity["ticker"])
            or not isinstance(identity["cusip"], str) or not _valid_cusip(identity["cusip"])
            or class_terms(identity["class_title"]) is None
            or class_terms(identity["class_title"])["par_term"] == "UNSPECIFIED"
            or class_terms(identity["class_title"])["share_class"] == "EQUITY:UNCLASSIFIED"):
        raise ValueError("Expected point identity needs exact issuer/name/symbol/class/par/CUSIP fields")
    instrument, cover = request["instrument"], request["cover"]
    if (not isinstance(instrument, dict) or set(instrument) != {"source_key", "index_key", "form"}
            or instrument["form"] not in _FORMS
            or not isinstance(cover, dict) or set(cover) != {"source_key", "proof_key", "proof_kind", "form", "registered_claim"}
            or cover["proof_kind"] not in {"SUBMISSIONS", "INDEX"} or cover["form"] not in _COVER_FORMS
            or not isinstance(cover["registered_claim"], dict) or set(cover["registered_claim"]) != _COVER_SELECTION
            or cover["registered_claim"]["source_key"] != cover["source_key"]):
        raise ValueError("Select one original Schedule13/index and one separate registered cover/proof")
    keys = [instrument["source_key"], instrument["index_key"], cover["source_key"], cover["proof_key"]]
    if any(not isinstance(key, str) or not _KEY.fullmatch(key) for key in keys) or len(set(keys)) != 4:
        raise ValueError("Exactly four distinct original capture roles are required")
    return anchor, identity, instrument, cover, keys


def _publication_gap(filing):
    accepted, filed = filing.get("acceptance_unqualified_raw"), filing.get("filing_on")
    if accepted and filed and accepted[:10] != filed:
        filing["blockers"].append("INDEX_ACCEPTANCE_CALENDAR_DAY_DIFFERS_FROM_FILING_DAY")
    return filing


def _instrument(source, index, descriptor):
    if source["body"] is None or index["body"] is None:
        return None, None, ["ORIGINAL_SCHEDULE_OR_INDEX_UNAVAILABLE"], None
    # Subject identity and reporting-filer archive routing are separate original
    # facts. The caller's proposed issuer never rewrites either original role.
    parsed = _SubjectIndex(index["body"])
    subjects = [row for row in parsed.companies if row["role"] == "Subject"]
    if len(subjects) != 1:
        raise ValueError("Original Schedule index must have one exact subject")
    filing = _publication_gap(_schedule_index(source, index, descriptor["form"], {"issuer_cik": subjects[0]["cik"]}))
    try:
        fact = _schedule_fact(source, filing)
    except (ValueError, ET.ParseError):
        return None, filing, [*filing["blockers"], "ORIGINAL_SCHEDULE_FACT_FIELDS_UNRESOLVED"], subjects[0]
    gaps = list(filing["blockers"])
    if fact["issuer_cik"] != subjects[0]["cik"] or _name(fact["issuer_name"]) != _name(subjects[0]["name"]):
        gaps.append("SCHEDULE_SUBJECT_XML_AND_INDEX_IDENTITY_CONFLICT")
    return fact, filing, gaps, subjects[0]


def _cover(source, proof, descriptor, expected_cusip):
    filing = _publication_gap(_filing(source, proof, descriptor))
    if source["body"] is None or proof["body"] is None:
        return None, filing, [*filing["blockers"], "ORIGINAL_COVER_OR_PUBLICATION_PROOF_UNAVAILABLE"], [], []
    if filing["publication_at"] is None:
        # Existing strict cover parsers need a publisher timestamp. Never pass
        # the capture time or a made-up timezone in place of that missing fact.
        return None, filing, [*filing["blockers"], "COVER_PUBLICATION_TIMESTAMP_UNKNOWN"], [], []
    snapshot = _snapshot(source, filing, "ticker_filing")
    facts, names = parse_ticker_filing(snapshot), issuer_names(snapshot)
    claim = descriptor["registered_claim"]
    chosen = [row for row in facts if row["context_ref"] == claim["context_ref"]]
    if len(chosen) != 1:
        return None, filing, [*filing["blockers"], "EXACT_REGISTERED_COVER_CONTEXT_UNRESOLVED"], facts, names
    security = chosen[0]
    same_context_names = [row for row in names if row["context_ref"] == security["context_ref"]
                          and row["issuer_cik"] == security["issuer_cik"] and row["anchor_date"] == security["anchor_date"]]
    if len(same_context_names) != 1:
        return None, filing, [*filing["blockers"], "EXACT_SAME_CONTEXT_COVER_ISSUER_NAME_UNRESOLVED"], facts, names
    actual_scope = {"issuer_cik": security["issuer_cik"], "symbol": security["ticker"], "class_title": security["class_title"],
                    "cusip": expected_cusip}
    try:
        registered = _registered(claim, source, filing, actual_scope)
    except ValueError:
        return None, filing, [*filing["blockers"], "REGISTERED_COVER_LOCAL_ROW_BINDING_UNRESOLVED"], facts, names
    return registered, filing, [*filing["blockers"], *registered["blockers"]], facts, names


def review_sec_dated_instrument_point(request: dict, captures: dict, *, as_of: datetime | None = None) -> dict:
    """Join one source-asserted date; return no adopted identity or validity span.

    Captures use the standard key -> (metadata, original whole bytes | None)
    contract. Default source selection is the physical start; new interpretation
    knowledge is the physical end. An explicit cutoff is a completion deadline,
    never permission to select future sources or backdate a new interpretation.
    """
    anchor, identity, instrument, cover, keys = _request(request)
    started = _clock(_physical_clock())
    deadline = _clock(as_of) if as_of is not None else None
    if deadline is not None and deadline < started:
        raise ValueError("A new dated-point interpretation cannot be backdated")
    if not isinstance(captures, dict) or set(captures) != set(keys):
        raise ValueError("Select only the four explicit original capture roles")
    sources, total = {}, 0
    for key in keys:
        pair = captures[key]
        if not isinstance(pair, (tuple, list)) or len(pair) != 2 or not isinstance(pair[0], dict):
            raise ValueError("An exact original metadata/bytes pair is required")
        routing_cik, _, _ = _url(pair[0].get("requested_url", pair[0].get("url")))
        sources.update(_captures({key: pair}, {"issuer_cik": routing_cik}, started, started))
        total += sources[key]["bytes"] if sources[key]["body"] is not None else 0
    if total > 16_000_000:
        raise ValueError("Dated-point original-byte total exceeds its bound")
    schedule, schedule_index, cover_source, cover_proof = [sources[key] for key in keys]
    schedule_fact, schedule_filing, gaps, subject = _instrument(schedule, schedule_index, instrument)
    cover_fact, cover_filing, cover_gaps, all_contexts, cover_names = _cover(cover_source, cover_proof, cover, identity["cusip"])
    gaps.extend(cover_gaps)
    if cover_source["cik"] != identity["issuer_cik"]:
        gaps.append("COVER_ARCHIVE_CIK_DIFFERS_FROM_REQUESTED_ISSUER")
    if anchor is None:
        gaps.append("EXACT_REQUESTED_ANCHOR_UNKNOWN")
    elif date.fromisoformat(anchor) > started.date():
        gaps.append("REQUESTED_ANCHOR_FOLLOWS_PHYSICAL_REVIEW_DAY")
    if schedule["accession"] == cover_source["accession"] or schedule["sha256"] == cover_source["sha256"]:
        gaps.append("INDEPENDENT_SCHEDULE_AND_COVER_FILINGS_MISSING")
    expected_terms = class_terms(identity["class_title"])
    if schedule_fact is not None:
        if schedule_fact["issuer_cik"] != identity["issuer_cik"]:
            gaps.append("SCHEDULE_ISSUER_CIK_CONFLICT")
        if _name(schedule_fact["issuer_name"]) != _name(identity["issuer_name"]):
            gaps.append("SCHEDULE_ISSUER_NAME_CONFLICT")
        if schedule_fact["cusip"] != identity["cusip"]:
            gaps.append("SCHEDULE_CUSIP_CONFLICT")
        terms = class_terms(schedule_fact["class_title"])
        if terms is None or terms["share_class"] == "EQUITY:UNCLASSIFIED":
            gaps.append("SCHEDULE_EXACT_SHARE_CLASS_UNKNOWN")
        if terms is None or terms["par_term"] == "UNSPECIFIED":
            gaps.append("SCHEDULE_EXACT_PAR_TERM_UNKNOWN")
        if terms != expected_terms:
            gaps.append("SCHEDULE_CLASS_OR_PAR_CONFLICT")
        if anchor is None or schedule_fact["event_on"] != anchor:
            gaps.append("SCHEDULE_EVENT_DAY_DIFFERS_FROM_REQUESTED_ANCHOR")
    else:
        gaps.append("DATED_SCHEDULE_INSTRUMENT_FACT_UNRESOLVED")
    if cover_fact is not None:
        if cover_fact["issuer_cik"] != identity["issuer_cik"] or _name(cover_fact["issuer_name"]) != _name(identity["issuer_name"]):
            gaps.append("COVER_ISSUER_CIK_OR_NAME_CONFLICT")
        if cover_fact["symbol"] != identity["ticker"]:
            gaps.append("COVER_SYMBOL_CONFLICT")
        if class_terms(cover_fact["class_title"]) != expected_terms:
            gaps.append("COVER_CLASS_OR_PAR_CONFLICT")
        if cover_fact["same_row_cusip"] is not None and cover_fact["same_row_cusip"] != identity["cusip"]:
            gaps.append("COVER_SAME_ROW_CUSIP_CONFLICT")
        if anchor is None or cover_fact["context_anchor_on"] != anchor:
            gaps.append("COVER_CONTEXT_DAY_DIFFERS_FROM_REQUESTED_ANCHOR")
    else:
        gaps.append("DATED_COVER_SECURITY_FACT_UNRESOLVED")
    point_supported = not gaps
    report = {"schema_version": VERSION, "status": "DATED_INSTRUMENT_POINT_SUPPORTED" if point_supported else "DATED_INSTRUMENT_POINT_PARTIAL",
              "requested_anchor_on": anchor, "requested_identity": deepcopy(identity), "point_supported": point_supported,
              "point": {**deepcopy(identity), "anchor_on": anchor} if point_supported else None,
              "point_scope": "ONE_SOURCE_ASSERTED_DAY_ONLY", "review_started_at": started.isoformat(), "source_as_of": started.isoformat(),
              "source_knowledge_at": max(row["known"] for row in sources.values()).isoformat(),
              "schedule_instrument_fact": deepcopy(schedule_fact), "schedule_index_subject": deepcopy(subject),
              "schedule_publication_proof": deepcopy(schedule_filing), "cover_security_fact": deepcopy(cover_fact),
              "cover_publication_proof": deepcopy(cover_filing), "original_cover_contexts": deepcopy(all_contexts), "original_cover_issuer_names": deepcopy(cover_names),
              "blockers": sorted(set(gaps)), "acceptance_blockers": ["CORPORATE_ACTION_EFFECTIVE_DAY_NOT_PROVEN_BY_POINT_JOIN",
                  "ISSUED_INSTRUMENT_VALIDITY_INTERVAL_UNPROVEN", "CONTINUITY_BETWEEN_POINTS_UNPROVEN", "HISTORICAL_AVAILABILITY_AND_OOS_UNPROVEN"],
              "action_effective_on": None, "continuity_verified": False, "issued_validity_verified": False,
              "registry_adoptions": 0, "new_verified_identities": 0, "current_q3_verified_instrument_coverage": 0,
              "production_registry_changed": False, "automatic_alias_allowed": False, "interval_extension_allowed": False,
              "historical_availability_inferred": False, "scoring_allowed": False, "trading_allowed": False, "network_requests_performed": 0,
              "capture_pins": {key: {"url": row["url"], "sha256": row["sha256"], "bytes": row["bytes"],
                  "metadata_sha256": row["metadata_sha256"], "observed_at": row["observed"].isoformat(), "known_at": row["known"].isoformat(),
                  "body_available": row["body"] is not None} for key, row in sources.items()}}
    completed = _clock(_physical_clock())
    if completed < max(started, *[row["known"] for row in sources.values()]):
        raise ValueError("Dated-point physical interpretation clock moved backwards")
    if deadline is not None and completed > deadline:
        raise ValueError("Dated-point interpretation completed after the explicit cutoff")
    report.update(review_recorded_at=completed.isoformat(), knowledge_at=completed.isoformat(), as_of=(deadline or completed).isoformat())
    report["review_sha256"] = hashlib.sha256(_encoded(report)).hexdigest()
    return report
