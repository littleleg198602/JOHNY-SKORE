"""Pinned procedural case events, separate from merits, liability and identity.

An opposition is a party filing. An opinion denying a dismissal motion is a
procedural judicial decision, not the accompanying order or a final judgment.
No URL, caption spelling, source date or page update supplies legal continuity.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from html import unescape
import json
import re
from urllib.parse import urlsplit

from market_checker_app.services.doj_enforcement_scope_review import (
    _Unavailable, _clock, _day, _html, _key, _pdf_pages, _registry, _SENSITIVE, _text,
)

CONTRACT_VERSION = "doj-court-procedural-posture-v1"
MAX_CAPTURES = 6
MAX_BODY_BYTES = 4_000_000
_KINDS = {"COURT_OPINION", "COURT_ORDER", "PARTY_OPPOSITION", "DOJ_REPORT"}
_POSTURES = {"MOTION_TO_DISMISS_DENIED", "MOTION_TO_DISMISS_GRANTED", "OPPOSITION_FILED"}
_PURPOSES = {"COURT_CAPTION", "PLAINTIFF", "CASE_ENTITY", "DOCUMENT_TYPE",
             "PROCEDURAL_ACTION", "JUDGE", "DOCUMENT_DATE"}
_ACCESS = re.compile(r'(?:passcode|meeting password)\s*[:=]\s*\S+|'
                     r'[?&](?:pwd|password|api_key|access_token)=[^\s"\'<>]+', re.I)


def _official(value):
    value = _text(value, 2048)
    p = urlsplit(value)
    if (p.scheme != "https" or p.hostname not in {"www.justice.gov", "www.govinfo.gov", "www.njd.uscourts.gov", "ecf.njd.uscourts.gov"}
            or p.port not in {None, 443} or p.username or p.password or p.fragment
            or ".." in p.path.split("/") or "\\" in p.path or _ACCESS.search(value)):
        raise ValueError("Case review requires an exact safe primary reference")
    if p.query and not (p.hostname == "www.justice.gov" and p.query in {"inline", "inline="}
            or p.hostname == "ecf.njd.uscourts.gov" and p.path == "/cgi-bin/show_public_doc"
            and re.fullmatch(r"[0-9]{4}cv[0-9]{5}-[0-9]{1,7}", p.query)):
        raise ValueError("Case review reference has unsupported query scope")
    return value


def _request(raw):
    if not isinstance(raw, dict):
        raise ValueError("Case posture review requires one request object")
    request = {key: _text(raw.get(key)) for key in ("issuer_ticker", "issuer_cik", "issuer_legal_name",
        "case_entity_name", "plaintiff_name", "case_entity_role", "docket", "court", "originating_complaint_filed_on")}
    if (not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,12}", request["issuer_ticker"])
            or not re.fullmatch(r"[0-9]{10}", request["issuer_cik"]) or int(request["issuer_cik"]) == 0
            or not re.fullmatch(r"[0-9]{1,2}:[0-9]{2}-cv-[0-9]{4,7}", request["docket"])
            or request["case_entity_role"] != "DEFENDANT"):
        raise ValueError("Case posture requires exact issuer/docket/party selectors")
    origin = _day(request["originating_complaint_filed_on"])
    events = raw.get("events")
    if not isinstance(events, list) or not 1 <= len(events) <= 6:
        raise ValueError("Case review requires one to six selected events")
    request["events"] = []
    for item in events:
        if not isinstance(item, dict):
            raise ValueError("Case event must be an object")
        event = {key: _text(item.get(key)) for key in ("event_id", "capture_id", "document_kind", "filed_on", "document_number", "posture")}
        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", event["event_id"])
                or not re.fullmatch(r"[0-9]{1,7}", event["document_number"])
                or event["document_kind"] not in _KINDS or event["posture"] not in _POSTURES
                or _day(event["filed_on"]) <= origin):
            raise ValueError("Only exact later dated procedural events are supported")
        for field in ("motion_document_number", "document_dated_on", "judge_name", "caption_entity_name", "caption_plaintiff_name"):
            if field in item:
                event[field] = _text(item[field])
        if event.get("motion_document_number") and not re.fullmatch(r"[0-9]{1,7}", event["motion_document_number"]):
            raise ValueError("Motion document number must be exact")
        if event.get("document_dated_on"):
            if not origin < _day(event["document_dated_on"]) <= _day(event["filed_on"]):
                raise ValueError("A selected order/opinion date cannot follow its filing day")
        pins = item.get("selected_passages")
        if not isinstance(pins, list) or not 1 <= len(pins) <= 12:
            raise ValueError("Case event requires bounded page/excerpt pins")
        event["selected_passages"] = []
        for pin in pins:
            if (not isinstance(pin, dict) or pin.get("purpose") not in _PURPOSES
                    or type(pin.get("page")) is not int or not 1 <= pin["page"] <= 120):
                raise ValueError("Case passage needs an exact one-based page and purpose")
            event["selected_passages"].append({"purpose": pin["purpose"], "page": pin["page"],
                                                "excerpt": _text(pin.get("excerpt"), 2000)})
        request["events"].append(event)
    if len({e["event_id"] for e in request["events"]}) != len(events):
        raise ValueError("Case event IDs must be unique")
    return request


def _capture(identifier, packet, ingested):
    info = {"capture_id": identifier, "original_body_pin_verified": False}
    meta, body = packet.get("metadata"), packet.get("body")
    if not isinstance(meta, dict) or meta.get("capture_id") != identifier:
        raise ValueError("Case capture map key must equal metadata capture_id")
    try:
        info["source_url"] = _official(meta.get("requested_url"))
        for field in ("raw_response_sha256", "raw_response_bytes", "http_status", "started_at", "observed_at"):
            value = meta.get(field)
            if field == "raw_response_sha256" and isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value):
                info[field] = value
            elif field in {"raw_response_bytes", "http_status"} and type(value) is int:
                info[field] = value
            elif field in {"started_at", "observed_at"} and value is not None:
                info[field] = _clock(value).isoformat()
        if (body is None or meta.get("public_raw_available") is not True
                or str(meta.get("status", "")).startswith("WITHHELD")):
            return info, None, "MISSING_OR_WHOLE_WITHHELD_ORIGINAL"
        if (type(meta.get("http_status")) is not int or meta["http_status"] != 200
                or meta.get("final_url") != info["source_url"] or meta.get("automatic_redirects") is not False
                or meta.get("status") != "ORIGINAL_BYTES_CAPTURED"
                or meta.get("archive_encoding") not in {None, "raw"}):
            return info, None, "UNSUCCESSFUL_OR_CHANGED_PRIMARY_SCOPE"
        if not _clock(meta["started_at"]) <= _clock(meta["observed_at"]) <= ingested:
            return info, None, "INVALID_SOURCE_CAPTURE_CLOCK"
        if meta.get("known_at") is not None and _clock(meta["known_at"]) > ingested:
            return info, None, "INVALID_SOURCE_CAPTURE_CLOCK"
        if (not isinstance(body, bytes) or not 1 <= len(body) <= MAX_BODY_BYTES
                or type(meta.get("raw_response_bytes")) is not int or len(body) != meta["raw_response_bytes"]
                or hashlib.sha256(body).hexdigest() != meta.get("raw_response_sha256")):
            return info, None, "ORIGINAL_BYTE_PIN_OR_SIZE_BOUND"
        text = unescape(body.decode("utf-8", errors="replace"))
        if _SENSITIVE.search(text.encode()) or _ACCESS.search(text):
            return info, None, "WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE"
        mime = str(meta.get("content_type", "")).split(";", 1)[0].strip().lower()
        if mime not in {"application/pdf", "text/html"}:
            return info, None, "UNSUPPORTED_PRIMARY_CONTENT_TYPE"
        info.update(content_type=mime, original_body_pin_verified=True)
        return info, body, None
    except (ValueError, TypeError, KeyError):
        return info, None, "INVALID_CAPTURE_METADATA"


def _line(value, text):
    # Only a terminal caption comma is a delimiter. Internal legal-name
    # punctuation stays literal and never becomes an issuer alias.
    return any(_key(line).removesuffix(",") == _key(value).removesuffix(",") for line in text.splitlines())


def _event(request, selection, info, pages, count, records, cutoff):
    entity = selection.get("caption_entity_name", request["case_entity_name"])
    plaintiff_name = selection.get("caption_plaintiff_name", request["plaintiff_name"])
    result = {"event_id": selection["event_id"], "capture_id": selection["capture_id"],
        "selected_document_kind": selection["document_kind"], "selected_document_number": selection["document_number"],
        "selected_filed_on": selection["filed_on"], "caption_entity_name": entity, "caption_plaintiff_name": plaintiff_name,
        "caption_literal_matches_issuer": _key(entity) == _key(request["issuer_legal_name"]),
        "caption_variant_adopted_as_alias": False, "status": "PARTIAL", "passage_reviews": [],
        "case_docket_and_filing_supported": False, "exact_parties_supported": False,
        "document_kind_supported": False, "judicial_authority_supported": False,
        "document_date_supported": False, "procedural_event_supported": False,
        "court_procedural_decision_supported": False, "original_court_order_supported": False,
        "party_opposition_filing_supported": False, "doj_reported_procedure_only": False,
        "procedural_posture": "UNKNOWN", "merits_adjudication_verified": False,
        "final_judgment_verified": False, "liability_verified": False,
        "issuer_attribution_verified": False, "missing_evidence": []}
    matched = {}
    for pin in selection["selected_passages"]:
        okay = _key(pin["excerpt"]) in _key(pages.get(pin["page"], ""))
        result["passage_reviews"].append({"purpose": pin["purpose"], "page": pin["page"], "supported": okay})
        if okay:
            matched.setdefault(pin["purpose"], []).append(pin)
    early = "\n".join(pages.get(i, "") for i in (1, 2, 3))
    stamp = re.search(r"\bCase\s+([0-9]{1,2}:[0-9]{2}-cv-[0-9]{4,7})(?:-[A-Z0-9]+)*\s+Document\s+([0-9]+)\s+Filed\s+([0-9]{2}/[0-9]{2}/[0-9]{2})\s+Page\s+1\s+of\s+([0-9]+)\b", pages.get(1, ""), re.I)
    try:
        filed = datetime.strptime(stamp[3], "%m/%d/%y").date() if stamp else None
    except ValueError:
        filed = None
    caption_pins = [p for p in matched.get("COURT_CAPTION", []) if p["page"] <= 3]
    def court_text(value):
        return re.sub(r"\s", "", re.sub(r"\b(?:IN THE|FOR THE)\b", "", value, flags=re.I)).casefold()
    case = bool(info.get("original_body_pin_verified") and stamp and caption_pins
        and stamp[1] == request["docket"] and stamp[2] == selection["document_number"]
        and filed == _day(selection["filed_on"]) and filed <= _clock(info["observed_at"]).date()
        and (count is None or int(stamp[4]) == count)
        and court_text(request["court"]) in court_text(early))
    result["case_docket_and_filing_supported"] = case
    defendant = any(p["page"] <= 3 and _line(entity, pages[p["page"]]) for p in matched.get("CASE_ENTITY", []))
    # The publisher's OCR layer can spell et al. as ef at. Preserve that layer;
    # it cannot supply an alias, a state-plaintiff list or issuer identity.
    plaintiff = any(p["page"] <= 3 and re.fullmatch(re.escape(_key(plaintiff_name))
        + r"[,\s]*(?:(?:et\s+al|ef\s+at)[.,\s]*)?", _key(line))
        for p in matched.get("PLAINTIFF", []) for line in pages[p["page"]].splitlines())
    result["exact_parties_supported"] = bool(case and defendant and plaintiff and re.search(r"\bDefendant\.?", early))
    kind = selection["document_kind"]
    label = {"COURT_OPINION": "OPINION", "COURT_ORDER": "ORDER"}.get(kind)
    proposed = bool(re.search(r"(?:\[?\s*PROPOSED\s*\]?|DRAFT)\s+(?:ORDER|OPINION|JUDGMENT)", early[:6000], re.I))
    if label:
        source = urlsplit(info.get("source_url", ""))
        court_document_source = (info.get("content_type") == "application/pdf" and (
            source.hostname in {"www.govinfo.gov", "www.njd.uscourts.gov", "ecf.njd.uscourts.gov"}
            or source.hostname == "www.justice.gov" and source.path.startswith(("/atr/media/", "/d9/", "/atr/case-document/file/"))))
        typed = bool(court_document_source and matched.get("DOCUMENT_TYPE") and _line(label, early) and not proposed)
    elif kind == "PARTY_OPPOSITION":
        typed = bool(matched.get("DOCUMENT_TYPE") and re.search(r"\b(?:PLAINTIFFS[’']?\s+)?BRIEF\s+IN\s+OPPOSITION\b", early, re.I))
    else:
        typed = bool(matched.get("DOCUMENT_TYPE") and info.get("content_type") == "text/html"
                     and urlsplit(info.get("source_url", "")).hostname == "www.justice.gov")
    result["document_kind_supported"] = typed
    judge_pins = matched.get("JUDGE", [])
    judge = bool(selection.get("judge_name") and any(
        re.search(r"(?<!\w)" + re.escape(_key(selection["judge_name"])) + r"(?!\w)", _key(p["excerpt"]))
        and re.search(r"(?:District Judge|U\.S\.D\.J\.)", p["excerpt"], re.I)
        and (p["page"] <= 3 or p["page"] == count) for p in judge_pins))
    result["judicial_authority_supported"] = bool(label and typed and judge)
    date_pins = matched.get("DOCUMENT_DATE", [])
    if selection.get("document_dated_on"):
        day = _day(selection["document_dated_on"])
        variants = {day.isoformat(), day.strftime("%m/%d/%Y"), f"{day.month}/{day.day}/{day.year}", day.strftime("%B %d, %Y"), f"{day.strftime('%B')} {day.day}, {day.year}"}
        result["document_date_supported"] = any((p["page"] == count or count is None and p["page"] == 1)
            and any(re.search(r"(?<![0-9])" + re.escape(value) + r"(?![0-9])", p["excerpt"]) for value in variants)
            for p in date_pins)
    action_pins = matched.get("PROCEDURAL_ACTION", [])
    decision = selection["posture"].rsplit("_", 1)[-1]
    motion = selection.get("motion_document_number")
    action = bool(motion and decision in {"DENIED", "GRANTED"} and any(re.search(
        r"\bmotion\s+to\s+dismiss\s*\(ECF\s+No\.\s*" + re.escape(motion) + r"\)\s+is\s+" + decision + r"\b",
        " ".join(p["excerpt"].split()), re.I) for p in action_pins))
    all_pins = all(p["supported"] for p in result["passage_reviews"])
    common = bool(case and result["exact_parties_supported"] and typed and all_pins)
    if kind in {"COURT_OPINION", "COURT_ORDER"}:
        supported = bool(common and judge and action and result["document_date_supported"])
        result.update(procedural_event_supported=supported, court_procedural_decision_supported=supported,
                      original_court_order_supported=supported and kind == "COURT_ORDER")
    elif kind == "PARTY_OPPOSITION":
        supported = bool(common and selection["posture"] == "OPPOSITION_FILED")
        result.update(procedural_event_supported=supported, party_opposition_filing_supported=supported)
    else:
        result["doj_reported_procedure_only"] = bool(typed and action_pins)
        supported = False
    if result["procedural_event_supported"]:
        result.update(status="SOURCE_PROCEDURAL_EVENT_REVIEWABLE", procedural_posture=selection["posture"],
                      documentary_filed_on=selection["filed_on"])
        if result["document_date_supported"]:
            result["document_dated_on"] = selection["document_dated_on"]
        if result["original_court_order_supported"]:
            result["order_issued_on"] = selection["document_dated_on"]
    reg = _registry({**request, "filed_on": selection["filed_on"], "case_entity_name": entity}, records, cutoff, info["source_url"]) if info.get("source_url") else {
        "resolution": "SOURCE_UNAVAILABLE", "case_date_supported": False, "known_before_case_day": False, "legal_continuity_supported": False}
    result["registry_identity"] = reg
    required = {"MISSING_EXACT_CASE_DOCKET_FILED_DOCUMENT": case, "MISSING_EXACT_PARTY_CAPTION": result["exact_parties_supported"],
        "MISSING_OR_MISMATCHED_DOCUMENT_KIND": typed, "MISSING_SELECTED_PASSAGE": all_pins,
        "MISSING_SUPPORTED_PROCEDURAL_EVENT": result["procedural_event_supported"],
        "MISSING_CASE_DATE_REGISTERED_IDENTITY": reg["case_date_supported"],
        "MISSING_IDENTITY_KNOWN_BEFORE_EVENT": reg["known_before_case_day"],
        "MISSING_INDEPENDENT_LEGAL_CONTINUITY": reg["legal_continuity_supported"]}
    if label:
        required.update(MISSING_JUDICIAL_AUTHORITY=judge, MISSING_DOCUMENT_DATE=result["document_date_supported"])
    result["missing_evidence"] = [code for code, okay in required.items() if not okay]
    return result


def review_doj_case_posture(request, captures, *, as_of=None, ingested_at, legal_records=()):
    """Pure selected-event review; default cutoff is actual now, past is strict."""
    request = _request(request)
    ingested = _clock(ingested_at)
    cutoff = _clock(as_of) if as_of is not None else datetime.now(timezone.utc)
    if (not isinstance(captures, dict) or not 1 <= len(captures) <= MAX_CAPTURES
            or any(not isinstance(k, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", k)
                   or not isinstance(v, dict) for k, v in captures.items())):
        raise ValueError("Case posture requires at most six exact ID-keyed captures")
    if not isinstance(legal_records, (list, tuple)) or len(legal_records) > 1000:
        raise ValueError("Case posture requires bounded supplied legal records")
    result = {"schema_version": 1, "review_kind": "DOJ_CASE_POSTURE", "contract_version": CONTRACT_VERSION,
        "status": "PARTIAL", "scope": {k: v for k, v in request.items() if k != "events"},
        "as_of": cutoff.isoformat(), "ingested_at": ingested.isoformat(), "known_at": ingested.isoformat(),
        "report_available_at_cutoff": ingested <= cutoff, "source_reviews": [], "event_reviews": [],
        "supported_procedural_events": 0, "original_court_orders_supported": 0, "court_opinions_supported": 0,
        "latest_supported_documentary_event_day": None, "current_complete_docket_verified": False,
        "original_complaint_rereviewed": False, "merits_adjudication_verified": False,
        "final_judgment_verified": False, "issuer_attribution_verified": False,
        "company_wide_liability_verified": False, "issuer_fine_verified": False, "issuer_conviction_verified": False,
        "registry_adoption_allowed": False, "historical_availability_reconstructed": False,
        "publication_time_verified": False, "live_acceptance_verified": False, "scoring_applied": False,
        "prediction_input": False, "http_requests": 0, "missing_evidence": []}
    if ingested > cutoff:
        result["missing_evidence"] = ["REPORT_NOT_KNOWN_AT_CUTOFF"]
        return result
    decoded = {}
    for identifier, packet in captures.items():
        info, body, reason = _capture(identifier, packet, ingested)
        result["source_reviews"].append(info)
        pages, count = {}, None
        if body is not None:
            try:
                if info["content_type"] == "application/pdf":
                    selected = {p["page"] for e in request["events"] if e["capture_id"] == identifier for p in e["selected_passages"]}
                    pages, count = _pdf_pages(body, selected)
                    if any(_ACCESS.search(unescape(text)) for text in pages.values()):
                        raise _Unavailable("WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE")
                else:
                    pages = {1: _html(body).lines_text}
            except _Unavailable as error:
                reason, pages = str(error), {}
        info["review_reason"] = reason or "ORIGINAL_PIN_AND_SELECTED_CONTENT_REVIEWED"
        decoded[identifier] = (info, pages, count)
    for event in request["events"]:
        info, pages, count = decoded.get(event["capture_id"], ({}, {}, None))
        result["event_reviews"].append(_event(request, event, info, pages, count, legal_records, cutoff))
    supported = [r for r in result["event_reviews"] if r["procedural_event_supported"]]
    result["supported_procedural_events"] = len(supported)
    result["original_court_orders_supported"] = sum(r["original_court_order_supported"] for r in supported)
    result["court_opinions_supported"] = sum(r["selected_document_kind"] == "COURT_OPINION" for r in supported)
    if supported:
        result["latest_supported_documentary_event_day"] = max(r["documentary_filed_on"] for r in supported)
    result["missing_evidence"] = sorted({code for r in result["event_reviews"] for code in r["missing_evidence"]})
    if not result["original_court_orders_supported"]:
        result["missing_evidence"].append("NO_SELECTED_ORIGINAL_COURT_ORDER_SUPPORTED")
    result["missing_evidence"].append("CURRENT_COMPLETE_DOCKET_AND_MERITS_NOT_ESTABLISHED")
    return result


__all__ = ["CONTRACT_VERSION", "review_doj_case_posture"]
