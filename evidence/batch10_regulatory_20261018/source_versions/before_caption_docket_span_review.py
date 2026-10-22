"""Document-local case/caption facts from complete pinned primary court PDFs.

This captured-only reader opens no path, URL, store or registry. A stamped case
identifier and a named caption role are case-document facts, not financial
issuer identity, legal continuity, a separate Order or a current disposition.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from html import unescape
from io import BytesIO
import re
from urllib.parse import urlsplit

from market_checker_app.services.doj_enforcement_scope_review import _SENSITIVE

CONTRACT_VERSION = "doj-document-local-caption-identity-v1"
MAX_DOCUMENTS = 3
MAX_BODY_BYTES = 4_000_000
MAX_PAGES = 120
MAX_TEXT_CHARACTERS = 2_000_000
_ACCESS = re.compile(r"(?:passcode|meeting password)\s*[:=]\s*\S+|[?&](?:pwd|password|api_key|access_token)=[^\s\"'<>]+", re.I)
_STAMP = re.compile(
    r"\bCase\s+(?P<docket>(?P<base>[0-9]{1,2}:[0-9]{2}-cv-[0-9]{4,7})(?P<suffix>(?:-[A-Z0-9]+)*))"
    r"\s+Document\s+(?P<document>[0-9]{1,7})\s+Filed\s+(?P<filed>[0-9]{2}/[0-9]{2}/[0-9]{2})"
    r"\s+Page\s+(?P<page>[0-9]{1,3})\s+of\s+(?P<pages>[0-9]{1,3})\b", re.I)
_COURT = re.compile(
    r"(?im)^[ \t]*(?:IN[ \t]+THE[ \t]+)?UNITED[ \t]+STATES[ \t]+DISTRICT[ \t]+COURT[ \t]*\r?\n"
    r"[ \t]*(?:FOR[ \t]+THE[ \t]+)?DISTRICT[ \t]+OF[ \t]+[A-Z][A-Z \t]{1,80}(?=\r?$)")
_PARTIES = re.compile(
    r"\A(?P<plaintiff>[\s\S]{1,600}?)\n[ \t]*(?P<plaintiff_role>Plaintiffs?)[,.]?[ \t]*"
    r"(?:\r?\n[ \t]*)+[vV]\.[ \t]*(?:\r?\n[ \t]*)+"
    r"(?P<defendant>[\s\S]{1,600}?)\n[ \t]*(?P<defendant_role>Defendants?)[,.]?[ \t]*(?:\r?\n|$)")
_CAPTION_DOCKET = re.compile(
    r"(?im)\b(?:Civil[ \t]+Action[ \t]+No\.|Case[ \t]+No\.)[ \t]*"
    r"(?P<docket>(?:(?P<division>[0-9]{1,2})[:-])?(?P<year>[0-9]{2})-cv-(?P<sequence>[0-9]{4,7})"
    r"(?P<suffix>(?:-[A-Z0-9]+)*))"
    r"(?:[ \t]*(?:\r?\n[ \t]*)?(?P<parenthetical>(?:\([A-Z0-9-]{1,20}\)[ \t]*){1,3}))?")
_KINDS = {"PARTY_OPPOSITION", "COURT_OPINION", "COMPLAINT"}


class _Unavailable(ValueError):
    pass


def _clock(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Case-caption clocks require an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _fold(value):
    return " ".join(value.split()).casefold()


def _court_key(value):
    return _fold(re.sub(r"\b(?:IN THE|FOR THE)\b", "", " ".join(value.split()), flags=re.I))


def _request(raw):
    if not isinstance(raw, dict):
        raise ValueError("One exact case-caption request is required")
    docket, court, documents = raw.get("case_docket"), raw.get("court"), raw.get("documents")
    if (not isinstance(docket, str) or not re.fullmatch(r"[0-9]{1,2}:[0-9]{2}-cv-[0-9]{4,7}", docket)
            or not isinstance(court, str) or not 20 <= len(court) <= 200
            or not isinstance(documents, list) or not 1 <= len(documents) <= MAX_DOCUMENTS):
        raise ValueError("Case caption needs exact base docket/court and one to three documents")
    result = {"case_docket": docket, "court": court, "documents": []}
    for row in documents:
        if not isinstance(row, dict):
            raise ValueError("Case document selector must be an object")
        identifier, number, filed, kind = (row.get(k) for k in ("capture_id", "document_number", "filed_on", "document_kind"))
        if (not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identifier)
                or not isinstance(number, str) or not re.fullmatch(r"[0-9]{1,7}", number)
                or not isinstance(filed, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", filed)
                or kind not in _KINDS):
            raise ValueError("Case document requires exact ID, number, date and type")
        filed_day = datetime.strptime(filed, "%Y-%m-%d")
        if not 2000 <= filed_day.year <= 2099:
            raise ValueError("This modern court-stamp contract does not infer another century")
        result["documents"].append({"capture_id": identifier, "document_number": number, "filed_on": filed, "document_kind": kind})
    if len({r["capture_id"] for r in result["documents"]}) != len(documents):
        raise ValueError("Case document captures must be unique")
    return result


def _primary(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise _Unavailable("INVALID_PRIMARY_REFERENCE")
    p = urlsplit(value)
    if (p.scheme != "https" or p.hostname not in {"www.govinfo.gov", "www.justice.gov"}
            or p.port not in {None, 443} or p.username or p.password or p.fragment
            or p.query not in {"", "inline", "inline="} or ".." in p.path.split("/")
            or "\\" in p.path or _ACCESS.search(value)):
        raise _Unavailable("INVALID_PRIMARY_REFERENCE")
    if not (p.hostname == "www.govinfo.gov" and p.path.startswith("/content/pkg/USCOURTS-") and p.path.endswith(".pdf")
            or p.hostname == "www.justice.gov" and p.path.startswith(("/atr/media/", "/d9/", "/atr/case-document/file/"))):
        raise _Unavailable("UNSUPPORTED_ORIGINAL_DOCUMENT_REFERENCE")
    return value


def _decode(body):
    # Lazy decoder; every page is bounded and inspected before facts are returned.
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise _Unavailable("PDF_DECODER_UNAVAILABLE") from error
    try:
        reader = PdfReader(BytesIO(body), strict=True)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= MAX_PAGES:
            raise _Unavailable("PDF_ENCRYPTION_OR_PAGE_BOUND")
        pages, total = [], 0
        for page in reader.pages:
            text = page.extract_text() or ""
            total += len(text)
            if total > MAX_TEXT_CHARACTERS:
                raise _Unavailable("PDF_TEXT_BOUND")
            if _SENSITIVE.search(unescape(text).encode()) or _ACCESS.search(unescape(text)):
                raise _Unavailable("WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE")
            pages.append(text)
        return pages
    except _Unavailable:
        raise
    except Exception as error:
        raise _Unavailable("INVALID_OR_UNREADABLE_PDF") from error


def _source(identifier, packet, clock):
    if isinstance(packet, dict):
        meta, body = packet.get("metadata"), packet.get("body")
    elif isinstance(packet, tuple) and len(packet) == 2:
        meta, body = packet
    else:
        raise ValueError("Case-caption capture requires metadata and original bytes")
    info = {"capture_id": identifier, "original_pdf_pin_verified": False, "status": "PARTIAL"}
    if not isinstance(meta, dict) or meta.get("capture_id") != identifier:
        raise ValueError("Capture key must equal metadata capture_id")
    if (body is None or meta.get("public_raw_available") is not True
            or str(meta.get("status", "")).startswith("WITHHELD")):
        return info, None, "MISSING_OR_WHOLE_WITHHELD_ORIGINAL"
    try:
        url = _primary(meta.get("requested_url"))
        observed, started = _clock(meta["observed_at"]), _clock(meta["started_at"])
        known = _clock(meta.get("known_at", meta["observed_at"]))
        if not started <= observed <= known <= clock:
            raise _Unavailable("SOURCE_NOT_KNOWN_AT_REVIEW_CLOCK")
        if (type(meta.get("http_status")) is not int or meta["http_status"] != 200
                or meta.get("final_url") != url or meta.get("automatic_redirects") is not False
                or meta.get("status") != "ORIGINAL_BYTES_CAPTURED"
                or meta.get("archive_encoding") not in {None, "raw"}
                or meta.get("content_encoding") not in {None, "", "identity"}
                or meta.get("content_type", "").split(";", 1)[0].strip().lower() != "application/pdf"):
            raise _Unavailable("UNSUPPORTED_ORIGINAL_TRANSPORT")
        if (not isinstance(body, bytes) or not 1 <= len(body) <= MAX_BODY_BYTES
                or not body.startswith(b"%PDF-") or type(meta.get("raw_response_bytes")) is not int
                or len(body) != meta["raw_response_bytes"] or _sha(body) != meta.get("raw_response_sha256")):
            raise _Unavailable("ORIGINAL_PDF_PIN_OR_SIZE_BOUND")
        if _SENSITIVE.search(unescape(body.decode("utf-8", errors="replace")).encode()) or _ACCESS.search(body.decode("utf-8", errors="replace")):
            raise _Unavailable("WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE")
        pages = _decode(body)
        info.update(status="ORIGINAL_PDF_REVIEWED", original_pdf_pin_verified=True, source_url=url,
            raw_response_sha256=_sha(body), raw_response_bytes=len(body),
            source_started_at=started.isoformat(), source_observed_at=observed.isoformat(), source_known_at=known.isoformat(),
            pdf_pages=len(pages), decoded_text_characters=sum(map(len, pages)),
            whole_original_and_all_pdf_pages_access_gate_passed=True)
        return info, pages, None
    except _Unavailable as error:
        return info, None, str(error)
    except (ValueError, TypeError, KeyError, AttributeError):
        return info, None, "INVALID_ORIGINAL_CAPTURE_METADATA"


def _fact(name, value, text, span, page, page_sha, pdf_sha, *, basis="SAME_PAGE_DOCUMENT"):
    start, end = span
    literal = text[start:end]
    return {"field": name, "value": value, "source_literal": literal,
        "page": page, "start_character": start, "end_character": end,
        "source_literal_sha256": _sha(literal.encode()), "page_text_sha256": page_sha,
        "original_pdf_sha256": pdf_sha, "binding": basis}


def _trim_span(text, start, end):
    raw = text[start:end]
    left = len(raw)-len(raw.lstrip())
    right = len(raw)-len(raw.rstrip())
    return start+left, end-right


def _caption(text, court, stamp, page, page_sha, pdf_sha, requested_court):
    # Names and roles come from this one court/v./role block on this page.
    block = text[court.end():court.end()+1800]
    parties = _PARTIES.match(block)
    if not parties:
        return None
    fields, names = [], {}
    for side in ("plaintiff", "defendant"):
        start, end = parties.span(side)
        span = _trim_span(text, court.end()+start, court.end()+end)
        literal = text[slice(*span)]
        if (len(literal) > 240 or len([s for s in literal.splitlines() if s.strip()]) > 4
                or re.search(r"\b(?:Case|Document|Filed|COURT|OPINION|COMPLAINT|ORDER)\b", literal, re.I)):
            return None
        names[side] = " ".join(literal.split())
        fields.append(_fact("caption."+side+".name", names[side], text, span, page, page_sha, pdf_sha))
        role_start, role_end = parties.span(side+"_role")
        fields.append(_fact("caption."+side+".role", side.upper(), text,
            (court.end()+role_start, court.end()+role_end), page, page_sha, pdf_sha))
    fields.append(_fact("court", " ".join(court[0].split()), text, court.span(), page, page_sha, pdf_sha))
    scope = text[court.end():court.end()+1800]
    docket_matches = list(_CAPTION_DOCKET.finditer(scope))
    abbreviated = []
    compatible = bool(stamp) and _court_key(court[0]) == _court_key(requested_court)
    base = stamp["base"] if stamp else None
    for match in docket_matches:
        division = match["division"]
        value = {"literal_docket": match["docket"], "division_literal": division,
            "year_literal": match["year"], "sequence_literal": match["sequence"],
            "suffix_literal": match["suffix"] or None,
            "parenthetical_literal": match["parenthetical"].strip() if match["parenthetical"] else None,
            "omitted_components_filled": False}
        if base:
            division_expected, year_expected, number_expected = re.fullmatch(r"([0-9]+):([0-9]+)-cv-([0-9]+)", base).groups()
            same = (division is None or division == division_expected) and match["year"] == year_expected and int(match["sequence"]) == int(number_expected)
        else:
            same = False
        value["compatible_with_same_page_stamped_base"] = same
        compatible = compatible and same
        span = match.span("docket")
        field = _fact("caption.docket", value, text, (court.end()+span[0], court.end()+span[1]), page, page_sha, pdf_sha)
        abbreviated.append(field)
    fields.extend(abbreviated)
    return {"page": page, "court_literal_matches_selected_court": _court_key(court[0]) == _court_key(requested_court),
        "party_labels": names, "named_party_list_complete": not any(re.search(r"\bet\s+al\b|\bef\s+at\b", name, re.I) for name in names.values()),
        "case_identity_binding_supported": compatible, "caption_docket_fields": abbreviated,
        "caption_names_adopted_as_issuer_aliases": False, "field_facts": fields,
        "parties_and_roles_from_same_page_caption_block": True}


def _document(request, selected, info, pages):
    result = {"capture_id": selected["capture_id"], "selected_document_number": selected["document_number"],
        "selected_filed_on": selected["filed_on"], "selected_document_kind": selected["document_kind"],
        "status": "PARTIAL", "source_case_identity_supported": False, "case_date_party_facts_supported": False,
        "document_kind_supported": False, "page_reviews": [], "captions": [], "separate_order_references": [],
        "original_separate_order_present": False, "issuer_identity_verified": False, "legal_continuity_verified": False,
        "merits_adjudication_verified": False, "current_disposition_verified": False, "missing_evidence": []}
    if pages is None:
        return result
    stamp_ok, full_dockets = [], []
    first_text = pages[0]
    headings = {"COURT_OPINION": r"(?im)^\s*OPINION\s*$", "PARTY_OPPOSITION": r"(?is)BRIEF\s+IN\s+OPPOSITION\s+TO\s+DEFENDANT[’']S\s+MOTION\s+TO\s+DISMISS",
                "COMPLAINT": r"(?im)^\s*(?:AMENDED\s+)?COMPLAINT\s*$"}
    title = re.search(headings[selected["document_kind"]], first_text)
    proposed = bool(re.search(r"\b(?:PROPOSED|DRAFT)\s+(?:OPINION|ORDER|JUDGMENT)\b", first_text, re.I))
    result["document_kind_supported"] = bool(title and not proposed)
    result["source_authority"] = "COURT_OPINION_CASE_FACTS" if selected["document_kind"] == "COURT_OPINION" else "PARTY_FILING_CASE_FACTS"
    for number, text in enumerate(pages, 1):
        page_sha, pdf_sha = _sha(text.encode()), info["raw_response_sha256"]
        row = {"page": number, "page_text_sha256": page_sha, "page_text_characters": len(text), "field_facts": [],
               "stamp_matches_selected_document": False, "caption_present": False}
        stamps = list(_STAMP.finditer(text))
        stamp = stamps[0] if len(stamps) == 1 else None
        okay = bool(stamp and stamp["base"] == request["case_docket"] and stamp["document"] == selected["document_number"]
            and stamp["filed"] == datetime.strptime(selected["filed_on"], "%Y-%m-%d").strftime("%m/%d/%y")
            and int(stamp["page"]) == number and int(stamp["pages"]) == len(pages)
            and selected["filed_on"] <= info["source_observed_at"][:10])
        row["stamp_matches_selected_document"] = okay
        stamp_ok.append(okay)
        if stamp:
            full_dockets.append(stamp["docket"])
            values = {"case.docket_full_literal": stamp["docket"], "case.docket_base_literal": stamp["base"],
                "case.docket_suffix_literal": stamp["suffix"] or None, "document.number": stamp["document"],
                "document.filed_date_literal": stamp["filed"], "document.page_number": int(stamp["page"]),
                "document.page_count": int(stamp["pages"])}
            groups = ("docket", "base", "suffix", "document", "filed", "page", "pages")
            row["field_facts"] = [_fact(key, value, text, stamp.span(group), number, page_sha, pdf_sha)
                                  for (key, value), group in zip(values.items(), groups)]
            row["filed_iso_selector_matches_printed_two_digit_date"] = okay
        courts = list(_COURT.finditer(text))
        if len(courts) == 1:
            caption = _caption(text, courts[0], stamp, number, page_sha, pdf_sha, request["court"])
            if caption:
                caption["case_identity_binding_supported"] = caption["case_identity_binding_supported"] and okay
                result["captions"].append(caption)
                row["caption_present"] = True
        if title and number == 1:
            row["field_facts"].append(_fact("document.type_title", selected["document_kind"], text, title.span(), number, page_sha, pdf_sha))
        for reference in re.finditer(r"An\s+appropriate\s+Order\s+accompanies\s+this\s+Opinion\.", text, re.I):
            result["separate_order_references"].append(_fact("separate_order_reference_only", True, text,
                reference.span(), number, page_sha, pdf_sha))
        result["page_reviews"].append(row)
    supported_captions = [c for c in result["captions"] if c["case_identity_binding_supported"]]
    coherent_stamps = all(stamp_ok) and len(set(full_dockets)) == 1
    case_supported = bool(coherent_stamps and supported_captions and result["document_kind_supported"])
    result.update(source_case_identity_supported=case_supported, case_date_party_facts_supported=case_supported,
        status="CASE_DOCUMENT_IDENTITY_FACTS_REVIEWABLE" if case_supported else "PARTIAL",
        caption_party_label_variants_observed=len({tuple(c["party_labels"].items()) for c in result["captions"]}) > 1,
        caption_variants_unified_as_aliases=False, all_document_pages_stamp_consistent=coherent_stamps,
        full_docket_literals_observed=sorted(set(full_dockets)))
    if not coherent_stamps:
        result["missing_evidence"].append("MISSING_OR_CONFLICTING_PAGE_DOCUMENT_STAMP")
    if not supported_captions:
        result["missing_evidence"].append("MISSING_DOCUMENT_LOCAL_COURT_CASE_PARTY_CAPTION")
    if not result["document_kind_supported"]:
        result["missing_evidence"].append("MISSING_OR_CONTRADICTORY_DOCUMENT_TITLE")
    return result


def review_doj_case_caption(request, captures, *, reviewed_at=None):
    """Extract case identity facts from whole pinned PDFs, without external I/O."""
    request = _request(request)
    physical = datetime.now(timezone.utc)
    cutoff = min(_clock(reviewed_at), physical) if reviewed_at is not None else physical
    if (not isinstance(captures, dict) or not 1 <= len(captures) <= MAX_DOCUMENTS
            or any(not isinstance(k, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", k) for k in captures)):
        raise ValueError("Case-caption review requires one to three exact captures")
    result = {"schema_version": 1, "review_kind": "DOJ_CASE_CAPTION", "contract_version": CONTRACT_VERSION,
        "status": "PARTIAL", "case_scope": {k: v for k, v in request.items() if k != "documents"},
        "reviewed_at": cutoff.isoformat(), "known_at": physical.isoformat(), "source_reviews": [], "document_reviews": [],
        "supported_case_documents": 0, "supported_caption_blocks": 0, "same_case_corroborated_by_distinct_originals": False,
        "issuer_identity_verified": False, "financial_cik_identity_verified": False, "legal_continuity_verified": False,
        "original_separate_order_present": False, "complete_current_docket_verified": False,
        "merits_adjudication_verified": False, "final_judgment_verified": False, "current_disposition_verified": False,
        "party_assertions_are_court_findings": False, "registry_adoption_allowed": False,
        "historical_availability_reconstructed": False, "scoring_applied": False, "prediction_input": False,
        "http_requests": 0, "database_actions": 0, "missing_evidence": []}
    for selected in request["documents"]:
        identifier = selected["capture_id"]
        if identifier in captures:
            info, pages, reason = _source(identifier, captures[identifier], cutoff)
        else:
            info, pages, reason = {"capture_id": identifier, "original_pdf_pin_verified": False}, None, "MISSING_SELECTED_ORIGINAL"
        info["reason"] = reason or "ORIGINAL_BYTES_AND_ALL_PAGE_TEXT_REVIEWED"
        result["source_reviews"].append(info)
        document = _document(request, selected, info, pages)
        if reason:
            document["missing_evidence"].append(reason)
        result["document_reviews"].append(document)
    supported = [row for row in result["document_reviews"] if row["source_case_identity_supported"]]
    result["supported_case_documents"] = len(supported)
    result["supported_caption_blocks"] = sum(sum(c["case_identity_binding_supported"] for c in row["captions"]) for row in supported)
    hashes = {row.get("raw_response_sha256") for row in result["source_reviews"] if row["original_pdf_pin_verified"]}
    result["same_case_corroborated_by_distinct_originals"] = len(supported) >= 2 and len(hashes) == len(supported)
    result["missing_evidence"] = sorted({code for row in result["document_reviews"] for code in row["missing_evidence"]}
        | {"INDEPENDENT_DATED_FINANCIAL_ISSUER_IDENTITY_NOT_ESTABLISHED", "LEGAL_ISSUER_CONTINUITY_NOT_ESTABLISHED",
           "SEPARATE_WHOLE_COURT_ORDER_NOT_PROVIDED", "COMPLETE_CURRENT_DOCKET_AND_DISPOSITION_NOT_ESTABLISHED"})
    return result


__all__ = ["CONTRACT_VERSION", "review_doj_case_caption"]
