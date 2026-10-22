"""Read-only dated control review alongside independent instrument evidence.

Direct and indirect ownership, Merger Sub and the surviving issuer, and
planned and completed events are separate claims. A matching name or a
current catalog supplies none of their missing dated identity bridges.
"""
from copy import deepcopy
from datetime import date
import hashlib
import json
import re
from urllib.parse import urlsplit

from market_checker_app.services.sec13f_identity_bulk import (
    _ActionPassageParser, _clock, _dated_action_text, _literal_in, _name, _ticker,
    official_url, parse_schedule13, parse_ticker_filing, share_class,
)
from market_checker_app.services.sec_corporate_action_bridge import (
    action_text, class_terms, issuer_names, review_stock_merger_bridge,
)

MAX_CONTROL_DISCLOSURES = 24
MAX_INSTRUMENT_DISCLOSURES = 8


def control_text(snapshot):
    """Parse whole retained bytes, including an exact single EX-2.1 envelope.

    This extends no existing binder and writes no replacement publisher body.
    The original filename, envelope and visible HTML must all agree.
    """
    if snapshot.kind != "corporate_action_filing":
        raise ValueError("Control evidence requires an original corporate-action filing")
    official_url(snapshot.kind, snapshot.url)
    if not isinstance(snapshot.payload, bytes) or not 0 < len(snapshot.payload) <= 4_000_000:
        raise ValueError("Control evidence exceeds its original-byte budget")
    try:
        return action_text(snapshot)
    except ValueError:
        payload = snapshot.payload.decode("utf-8-sig")
        match = re.fullmatch(
            r"\s*<DOCUMENT>\s*<TYPE>EX-(?:2\.1|99\.[12])\s*<SEQUENCE>[0-9]+\s*"
            r"<FILENAME>([A-Za-z0-9_.-]+)\s*(?:<DESCRIPTION>[^\r\n<]*\r?\n)?"
            r"<TEXT>\s*(<HTML(?:\s[^>]*)?>.*</HTML>)\s*</TEXT>\s*</DOCUMENT>\s*",
            payload, re.I | re.S,
        )
        if match is None or match[1] != urlsplit(snapshot.url).path.rsplit("/", 1)[-1]:
            raise ValueError("Unsupported or mismatched original control envelope")
        parser = _ActionPassageParser()
        parser.feed(match[2])
        parser.close()
        if not parser.has_html or parser.stack:
            raise ValueError("Incomplete original control HTML")
        return " ".join(" ".join(parser.parts).split())


def _source(service, reference, kind):
    if not isinstance(reference, str) or not re.fullmatch(r"[a-f0-9]{64}", reference):
        raise ValueError("An exact pinned source snapshot ID is required")
    source = service.snapshots.get(reference)
    if source is None or source.kind != kind:
        raise ValueError("Missing or incorrectly typed pinned review source")
    official_url(kind, source.url)
    if source.observed_at > service.as_of or source.published_at is None or source.published_at > source.observed_at:
        raise ValueError("Review evidence has missing or future physical/publication clocks")
    return source


def _filing_key(source):
    parts = urlsplit(source.url).path.split("/")
    return (parts[4].zfill(10), parts[5])


def _text(value, *, limit=300):
    if not isinstance(value, str) or not 1 <= len(value) <= limit:
        raise ValueError("An exact bounded source text field is required")
    return " ".join(value.split())


def _quote(source, excerpt):
    text = _text(excerpt, limit=4_000)
    if len(text) < 30 or text not in control_text(source):
        raise ValueError("Control passage is absent from the original visible source")
    return text


def _date_pair(value, text):
    if value is None and text is None:
        return None
    if not isinstance(value, str) or value != date.fromisoformat(value).isoformat():
        raise ValueError("An exact ISO event day or explicit UNKNOWN is required")
    result = date.fromisoformat(value)
    if not isinstance(text, str) or not _dated_action_text(text, result):
        raise ValueError("The source date text does not identify the exact event day")
    return result


def _reference_bound(name, reference, passage):
    """Accept the exact name or its explicit local quoted definition only."""
    if not _literal_in(name, passage):
        return False
    stripped = re.sub(r"^the\s+", "", reference, flags=re.I)
    if _name(stripped) == _name(name):
        return True
    definition = (re.escape(name) + r"(?:,?\s+(?:a|an)\s+[^.;()]{1,100})?\s*"
                  r"\(\s*(?:the\s+)?[\"“‘']\s*" + re.escape(stripped) + r"\s*[\"”’']\s*\)")
    return re.search(definition, passage, re.I) is not None


def _control_edge(service, stock, disclosure):
    if not isinstance(disclosure, dict):
        raise ValueError("Control disclosures must be explicit structured proposals")
    source = _source(service, disclosure.get("source_snapshot_id"), "corporate_action_filing")
    role, scope, relation = (disclosure.get(key) for key in ("subject_role", "scope", "relation"))
    if role not in {"SURVIVING_ISSUER", "MERGER_SUB"} or scope not in {"PLANNED", "COMPLETED"} or relation not in {
            "DIRECT", "INDIRECT", "UNSPECIFIED"}:
        raise ValueError("Control subject, event scope and directness must be explicit")
    passage = _quote(source, disclosure.get("excerpt"))
    subject = _text(disclosure.get("child_reference_text"))
    parent = _text(disclosure.get("parent_reference_text"))
    child_name = _text(disclosure.get("child_name_text"))
    parent_name = _text(disclosure.get("parent_name_text"))
    relation_quote = _text(disclosure.get("relation_excerpt"), limit=900)
    day = _date_pair(disclosure.get("effective_on"), disclosure.get("effective_date_text"))
    blockers = []
    if relation_quote not in passage:
        raise ValueError("The relation must be an exact subset of its original local passage")
    if (_name(parent_name) != _name(stock["to_action_name_text"])
            or role == "SURVIVING_ISSUER" and _name(child_name) != _name(stock["from_action_name_text"])):
        raise ValueError("Control names do not describe the exact proposed merger parties")
    if role == "MERGER_SUB" and _name(child_name) in {_name(stock["from_action_name_text"]), _name(parent_name)}:
        raise ValueError("Merger Sub cannot be silently relabeled as either issuer")
    if not _reference_bound(child_name, subject, passage) or not _reference_bound(parent_name, parent, passage):
        blockers.append("LOCAL_NAMED_CONTROL_ROLES_UNPROVEN")
    archive_cik = _filing_key(source)[0]
    if archive_cik not in {stock["from_identity"]["issuer_cik"], stock["to_identity"]["issuer_cik"]}:
        raise ValueError("An unrelated archive CIK cannot supply merger-party control evidence")
    # This small grammar is deliberately literal and directed. 'Indirect' is
    # never a 'direct' match; the full subject must precede the subsidiary fact.
    directness = {"DIRECT": r"direct(?:,)?\s+(?:wholly[ -]owned\s+)?|(?:wholly[ -]owned\s+)?direct\s+",
                  "INDIRECT": r"indirect(?:,)?\s+(?:wholly[ -]owned\s+)?|(?:wholly[ -]owned\s+)?indirect\s+",
                  "UNSPECIFIED": r"wholly[ -]owned\s+"}[relation]
    verb = (r"(?:became|has become|becoming|is|was)\s+(?:a\s+)?" if scope == "COMPLETED" else
            r"(?:will become|would become|shall become|is|was|surviving the merger as|"
            r"continuing as the surviving corporation in the merger\s+\(\s*(?:the\s+)?"
            r"[\"“‘']\s*Surviving Corporation\s*[\"”’']\s*\)\s+and as)\s+(?:a\s+)?")
    pattern = (r"(?<![A-Za-z0-9])" + re.escape(subject) + r"\s+(?:surviving\s+and\s+)?" + verb
               + r"(?:" + directness + r")subsidiary\s+of\s+" + re.escape(parent) + r"(?![A-Za-z0-9])")
    matched = re.search(pattern, relation_quote, re.I) is not None
    if not matched:
        blockers.append("DIRECTED_CONTROL_RELATION_TEXT_UNPROVEN")
    if _name(re.sub(r"^the\s+", "", subject, flags=re.I)) == _name(re.sub(r"^the\s+", "", parent, flags=re.I)):
        blockers.append("LOCAL_CONTROL_PARTY_REFERENCE_COLLISION")
    if re.search(r"\b(?:not|no longer|neither|may|might|expected|proposed|subject to)\b", relation_quote, re.I):
        blockers.append("NEGATED_OR_CONDITIONAL_CONTROL_RELATION")
    if day is None:
        blockers.append("CONTROL_EFFECTIVE_DAY_UNKNOWN")
    elif day > source.published_at.date() or day > service.as_of.date():
        blockers.append("CONTROL_EFFECTIVE_DAY_CLOCK_CONFLICT")
    elif not _literal_in(disclosure["effective_date_text"], passage):
        blockers.append("CONTROL_SOURCE_DATE_ABSENT")
    if scope == "COMPLETED":
        completed = re.search(r"\b(?:merged with and into|consummated|completed (?:the )?(?:acquisition|merger)|"
                              r"merger (?:was|has been) (?:completed|consummated))\b", passage, re.I)
        if not completed or re.search(r"\b(?:will|would|shall|proposed|expected)\b", relation_quote, re.I):
            blockers.append("COMPLETED_CONTROL_EVENT_UNPROVEN")
        if day is not None and disclosure["effective_on"] != stock.get("effective_on"):
            blockers.append("CONTROL_AND_MERGER_DAY_CONFLICT")
    declarant_names = []
    try:
        declared_role = "from" if archive_cik == stock["from_identity"]["issuer_cik"] else "to"
        declarant_names = [fact for fact in issuer_names(source) if fact["issuer_cik"] == archive_cik
                          and _name(fact["issuer_name"]) == _name(stock[f"{declared_role}_identity"]["issuer_name"])]
    except ValueError:
        blockers.append("CONTROL_DECLARANT_LEGAL_NAME_UNSUPPORTED")
    if len(declarant_names) != 1:
        blockers.append("CONTROL_DECLARANT_LEGAL_NAME_MISSING")
    elif day is not None and declarant_names[0]["anchor_date"] != day.isoformat():
        blockers.append("CONTROL_DECLARANT_IDENTITY_DATE_CONFLICT")
    result = {"subject_role": role, "scope": scope, "relation": relation,
              "parent_cik_proposal": stock["to_identity"]["issuer_cik"],
              "child_cik_proposal": stock["from_identity"]["issuer_cik"] if role == "SURVIVING_ISSUER" else None,
              "merger_sub_cik_status": "UNKNOWN" if role == "MERGER_SUB" else "NOT_APPLICABLE",
              "parent_name_text": parent_name, "child_name_text": child_name,
              "parent_reference_text": parent, "child_reference_text": subject,
              "proposed_effective_on": day.isoformat() if day else None,
              "effective_on": day.isoformat() if scope == "COMPLETED" and not blockers else None,
              "control_text_matched": matched, "relation_excerpt": relation_quote, "excerpt": passage,
              "declarant_issuer_names": declarant_names, "source": {**source.metadata(), "snapshot_id": source.snapshot_id},
              "independent_disclosure_key": list(_filing_key(source)),
              "status": "PRIMARY_CONTROL_CANDIDATE" if not blockers else "PRIMARY_CONTROL_PARTIAL",
              "identity_binding_status": "UNVERIFIED_MERGER_PARTY_PROPOSALS",
              "ownership_percentage": None, "blockers": list(dict.fromkeys(blockers))}
    return result


def _instrument_evidence(service, stock, disclosure):
    if not isinstance(disclosure, dict) or disclosure.get("role") not in {"from", "to"}:
        raise ValueError("Dated tuple evidence requires one exact merger-party role")
    role = disclosure["role"]
    identity = stock[f"{role}_identity"]
    instrument = _source(service, disclosure.get("instrument_snapshot_id"), "schedule13")
    cover = _source(service, disclosure.get("ticker_snapshot_id"), "ticker_filing")
    day = _date_pair(disclosure.get("anchor_on"), disclosure.get("anchor_on"))
    if day is None:
        raise ValueError("An explicit dated tuple anchor is required")
    fact, covers, names, blockers = parse_schedule13(instrument), parse_ticker_filing(cover), [], []
    instrument_terms = class_terms(fact["class_title"])
    expected_terms = class_terms(stock[f"{role}_class_title"])
    exact = [item for item in covers if item["issuer_cik"] == identity["issuer_cik"]
             and _ticker(item["ticker"]) == _ticker(identity["ticker"])]
    if (fact["issuer_cik"] != identity["issuer_cik"] or fact["cusip"] != identity["cusip"]
            or _name(fact["issuer_name"]) != _name(identity["issuer_name"])
            or instrument_terms is None or instrument_terms["share_class"] != share_class(identity["class_description"])):
        blockers.append("EXACT_CIK_CUSIP_NAME_CLASS_CONFLICT")
    if (instrument_terms is not None and instrument_terms["par_term"] != "UNSPECIFIED"
            and instrument_terms != expected_terms):
        blockers.append("EXACT_INSTRUMENT_CLASS_OR_PAR_VALUE_CONFLICT")
    if len(exact) != 1:
        blockers.append("EXACT_CIK_SYMBOL_CLASS_CONTEXT_MISSING")
    try:
        all_names = issuer_names(cover)
        if len(exact) == 1:
            names = [name for name in all_names if name["issuer_cik"] == identity["issuer_cik"]
                     and name["context_ref"] == exact[0]["context_ref"] and name["anchor_date"] == exact[0]["anchor_date"]]
    except ValueError:
        blockers.append("COVER_LEGAL_NAME_UNSUPPORTED")
    if len(names) != 1 or _name(names[0]["issuer_name"]) != _name(identity["issuer_name"]):
        blockers.append("EXACT_DATED_COVER_LEGAL_NAME_MISSING")
    if len(exact) == 1:
        if class_terms(exact[0]["class_title"]) != expected_terms:
            blockers.append("EXACT_CLASS_OR_PAR_VALUE_CONFLICT")
        if exact[0]["anchor_date"] != day.isoformat():
            blockers.append("TICKER_CONTEXT_DATE_CONFLICT")
    if fact["event_date"] != day.isoformat():
        blockers.append("INSTRUMENT_EVENT_DATE_CONFLICT")
    if day > instrument.published_at.date() or day > cover.published_at.date():
        blockers.append("DATED_TUPLE_PUBLICATION_CLOCK_CONFLICT")
    if _filing_key(instrument) == _filing_key(cover):
        blockers.append("INDEPENDENT_DATED_TUPLE_DISCLOSURES_MISSING")
    in_quarter = service.period[0] <= day <= service.period[1]
    if not in_quarter:
        blockers.append("DATED_ANCHOR_OUTSIDE_SELECTED_QUARTER")
    if role == "from" and stock.get("effective_on") and day >= date.fromisoformat(stock["effective_on"]):
        blockers.append("OLD_INSTRUMENT_ANCHOR_AFTER_CONVERSION")
    return {"role": role, "identity_proposal": deepcopy(identity), "anchor_on": day.isoformat(),
            "instrument": {**fact, **instrument.metadata()}, "ticker_contexts": exact,
            "cover_source": {**cover.metadata(), "snapshot_id": cover.snapshot_id}, "issuer_names": names,
            "anchor_in_selected_quarter": in_quarter,
            "aligned_source_dates": bool(len(exact) == 1 and exact[0]["anchor_date"] == fact["event_date"] == day.isoformat()),
            "historical_validity_scope": "ONE_DATED_ANCHOR_ONLY",
            "status": "DATED_TUPLE_CANDIDATE" if not blockers else "DATED_TUPLE_PARTIAL",
            "blockers": blockers, "verified_identity": False, "interval_extension_allowed": False}


def review_control_instrument_bridge(service, request):
    """Review caller-supplied pinned snapshots; no files, HTTP or mutations.

    request={stock_merger: existing claim, control_disclosures: [...],
             instrument_disclosures: [...]}. Physical clocks come from the
    SourceSnapshot objects already constructed by the captured-byte dispatcher.
    """
    if not isinstance(request, dict) or set(request) != {"stock_merger", "control_disclosures", "instrument_disclosures"}:
        raise ValueError("Control/instrument review requires the exact explicit request schema")
    stock, controls, instruments = (request[key] for key in ("stock_merger", "control_disclosures", "instrument_disclosures"))
    if (not isinstance(controls, list) or len(controls) > MAX_CONTROL_DISCLOSURES
            or not isinstance(instruments, list) or len(instruments) > MAX_INSTRUMENT_DISCLOSURES):
        raise ValueError("Control/instrument review exceeds the bounded disclosure budget")
    baseline = review_stock_merger_bridge(service, stock)
    edges = [_control_edge(service, stock, item) for item in controls]
    tuples = [_instrument_evidence(service, stock, item) for item in instruments]
    blockers = list(baseline["blockers"])
    conflicts, by_edge = [], {}
    for index, edge in enumerate(edges):
        blockers.extend(f"CONTROL_{index}_{value}" for value in edge["blockers"])
        if (edge["subject_role"] == "SURVIVING_ISSUER" and edge["scope"] == "COMPLETED"
                and edge["control_text_matched"] and edge["proposed_effective_on"] == stock.get("effective_on")):
            key = (edge["parent_cik_proposal"], edge["child_cik_proposal"], edge["proposed_effective_on"])
            by_edge.setdefault(key, []).append(index)
    for key, indices in by_edge.items():
        relations = {edges[index]["relation"] for index in indices}
        if {"DIRECT", "INDIRECT"}.issubset(relations):
            conflicts.append({"parent_cik_proposal": key[0], "child_cik_proposal": key[1], "effective_on": key[2],
                              "edge_indices": indices, "relations": sorted(relations), "status": "UNRESOLVED"})
    if conflicts:
        blockers.append("EXACT_SURVIVING_ISSUER_DIRECT_INDIRECT_CONFLICT")
    completed = [edge for edge in edges if edge["subject_role"] == "SURVIVING_ISSUER" and edge["scope"] == "COMPLETED"
                 and edge["status"] == "PRIMARY_CONTROL_CANDIDATE"]
    independent = {tuple(edge["independent_disclosure_key"]) for edge in completed}
    if len(independent) < 2:
        blockers.append("TWO_INDEPENDENT_COMPLETED_SURVIVING_CONTROL_DISCLOSURES_MISSING")
    for index, item in enumerate(tuples):
        blockers.extend(f"TUPLE_{item['role'].upper()}_{index}_{value}" for value in item["blockers"])
    if {item["role"] for item in tuples} != {"from", "to"}:
        blockers.append("BOTH_EXPLICIT_DATED_TUPLE_ROLES_MISSING")
    blockers = list(dict.fromkeys(blockers))
    report = {"schema_version": 1, "review_kind": "SEC_CONTROL_INSTRUMENT", "ticker": stock["ticker"],
              "quarter": service.quarter, "as_of": service.as_of.isoformat(),
              "status": "PRIMARY_CONTROL_INSTRUMENT_PARTIAL" if blockers else "PRIMARY_READY_FOR_EXPLICIT_REVIEW",
              "stock_merger_review": baseline, "baseline_blockers_preserved": deepcopy(baseline["blockers"]),
              "control_edges": edges, "control_conflicts": conflicts, "dated_instrument_evidence": tuples,
              "independent_completed_surviving_disclosures": len(independent), "blockers": blockers,
              "evidence_known_at": max(_clock(source.observed_at) for source in service.snapshots.values()).isoformat(),
              "historical_knowledge_reconstructed": False, "merger_sub_is_surviving_issuer": False,
              "ownership_percentage_inferred": False, "identity_transition_approved": False,
              "production_registry_changed": False, "interval_extension_allowed": False,
              "automatic_alias_allowed": False, "scoring_allowed": False,
              "current_quarter_verified_coverage": 0, "new_verified_identities": 0}
    report["review_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return report
