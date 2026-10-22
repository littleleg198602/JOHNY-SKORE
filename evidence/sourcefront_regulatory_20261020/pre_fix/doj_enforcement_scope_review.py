"""Pure review of pinned DOJ case documents; no HTTP, store or adoption.

A complaint records allegations against its named defendant. A prayer for a
judgment is not a judgment. A separately dated SEC legal-entity fact does not
prove continuity, historical availability, parent liability or a trading input.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timezone
import hashlib
from html import unescape
from html.parser import HTMLParser
from io import BytesIO
import json
import re
from types import SimpleNamespace
from urllib.parse import parse_qsl, urljoin, urlsplit

CONTRACT_VERSION = "doj-case-entity-original-bytes-v1"
MAX_CAPTURES = 4
MAX_CAPTURE_BYTES = 4_000_000
MAX_PDF_PAGES = 120
MAX_SELECTED_PAGES = 12
MAX_DECODED_TEXT = 2_000_000
MAX_PASSAGES = 12
_KINDS = {
    "COMPLAINT": "ALLEGATIONS_ONLY",
    "AMENDED_COMPLAINT": "ALLEGATIONS_ONLY",
    "PROPOSED_FINAL_JUDGMENT": "PROPOSED_RELIEF_NOT_ADJUDICATION",
    "FINAL_JUDGMENT": "COURT_DISPOSITION_DOCUMENT_REQUIRES_OUTCOME_REVIEW",
}
_PURPOSES = {"CAPTION", "CASE_ENTITY", "DOCUMENT_STAGE", "SOURCE_PUBLICATION", "LEGAL_ENTITY"}
_SENSITIVE = re.compile(
    rb'["\'](?:azure_map_key|azureMapsKey|subscriptionKey|api[_-]?key|accessToken|'
    rb'client_secret|password)["\']\s*[:=]|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|'
    rb'\bAKIA[A-Z0-9]{16}\b|\bbearer\s+[A-Za-z0-9._~+/=-]{12,}|'
    rb'(?:api[_-]?key|subscription[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|password)'
    rb'["\']?\s*[:=]\s*["\']?[A-Za-z0-9._~+/=-]{8,}|'
    rb'[?&](?:sig|signature|x-amz-signature|x-goog-signature|access_token)=[^\s"\'<>]{8,}', re.I)


class _Unavailable(ValueError):
    """Only fixed reason codes cross the review boundary."""


def _clock(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("DOJ review clocks require an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _text(value, limit=300):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or _SENSITIVE.search(unescape(value).encode())):
        raise ValueError("DOJ review requires bounded exact public text")
    return value.strip()


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("DOJ fact dates require YYYY-MM-DD")
    return date.fromisoformat(value)


def _key(value):
    # Retain every word and punctuation mark. A brand/parent is not an alias.
    return " ".join(value.split()).casefold()


def _url(value, host=None):
    value = _text(value, 2048)
    p = urlsplit(value)
    if (p.scheme != "https" or p.hostname not in {"www.justice.gov", "www.sec.gov"}
            or host is not None and p.hostname != host or p.port not in {None, 443}
            or p.username or p.password or p.query or p.fragment or "%" in p.path
            or ".." in p.path.split("/") or "\\" in p.path):
        raise ValueError("DOJ review needs an unchanged official HTTPS document")
    return value


class _VisibleHTML(HTMLParser):
    """Bound visible publisher text, links and explicit date attributes."""
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    _BLOCK = {"br", "div", "p", "table", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "li", "section", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self.datetimes, self.stack = [], [], [], []
        self.elements = self.characters = 0

    def handle_starttag(self, tag, attrs):
        self.elements += 1
        if self.elements > 120_000 or len(self.stack) > 200:
            raise _Unavailable("HTML_ELEMENT_OR_DEPTH_BOUND")
        attrs = dict(attrs)
        hidden = (bool(self.stack and self.stack[-1][1]) or tag in {"head", "script", "style", "template", "noscript"}
                  or "hidden" in attrs or str(attrs.get("aria-hidden", "")).lower() == "true"
                  or bool(re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", attrs.get("style", ""), re.I)))
        if not hidden:
            if tag == "a" and isinstance(attrs.get("href"), str):
                self.links.append(attrs["href"])
            if tag == "time" and isinstance(attrs.get("datetime"), str):
                self.datetimes.append(attrs["datetime"])
        if tag not in self._VOID:
            self.stack.append((tag, hidden))
        self.parts.append("\n" if tag in self._BLOCK else " ")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        # Publisher navigation can omit optional closes. Closing a hidden
        # ancestor cannot make its descendants supply a visible passage.
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        self.parts.append("\n" if tag in self._BLOCK else " ")

    def handle_data(self, data):
        if not self.stack or not self.stack[-1][1]:
            self.characters += len(data)
            if self.characters > MAX_DECODED_TEXT:
                raise _Unavailable("DECODED_TEXT_BOUND")
            self.parts.append(data)

    @property
    def text(self):
        return " ".join("".join(self.parts).split())

    @property
    def lines_text(self):
        return "\n".join(" ".join(line.split()) for line in "".join(self.parts).splitlines() if line.strip())


def _html(body):
    parser = _VisibleHTML()
    try:
        parser.feed(body.decode("utf-8-sig"))
        parser.close()
    except (UnicodeError, RecursionError) as error:
        raise _Unavailable("INVALID_PRIMARY_HTML") from error
    return parser


def _linked_filing(index_url, links, issuer_url):
    """Recognize an exact archive link, including SEC's non-I/O ix wrapper."""
    for link in links:
        joined = urljoin(index_url, link)
        if joined == issuer_url:
            return True
        parsed = urlsplit(joined)
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        if (parsed.scheme == "https" and parsed.hostname == "www.sec.gov"
                and parsed.port in {None, 443} and not parsed.username and not parsed.password
                and parsed.path == "/ix" and not parsed.fragment and len(pairs) == 1
                and pairs[0] == ("doc", urlsplit(issuer_url).path)):
            return True
    return False


def _pdf_pages(body, selected):
    # Import is lazy: unavailable decoding produces PARTIAL without starting a
    # client. Byte/page/selection bounds precede any page-text extraction.
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise _Unavailable("PDF_DECODER_UNAVAILABLE") from error
    if not body.startswith(b"%PDF-"):
        raise _Unavailable("INVALID_PRIMARY_PDF")
    try:
        reader = PdfReader(BytesIO(body), strict=True)
        if reader.is_encrypted:
            raise _Unavailable("ENCRYPTED_PRIMARY_PDF")
        count = len(reader.pages)
        if not 1 <= count <= MAX_PDF_PAGES:
            raise _Unavailable("PDF_PAGE_BOUND")
        selected = selected | set(range(1, min(count, 3) + 1))
        if len(selected) > MAX_SELECTED_PAGES or any(not 1 <= page <= count for page in selected):
            raise _Unavailable("PDF_SELECTED_PAGE_BOUND")
        result, decoded = {}, 0
        for page in sorted(selected):
            text = reader.pages[page - 1].extract_text() or ""
            decoded += len(text)
            if decoded > MAX_DECODED_TEXT:
                raise _Unavailable("DECODED_TEXT_BOUND")
            if _SENSITIVE.search(unescape(text).encode()):
                raise _Unavailable("WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE")
            result[page] = text
        return result, count
    except _Unavailable:
        raise
    except Exception as error:
        # No publisher bytes, parser trace or access value enter an error.
        raise _Unavailable("PDF_DECODER_OR_CONTENT_REJECTED") from error


def _original(capture, ingested):
    meta, body = capture.get("metadata"), capture.get("body")
    if not isinstance(meta, dict):
        raise ValueError("DOJ captures require metadata objects")
    identifier = _text(meta.get("capture_id"), 80)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identifier):
        raise ValueError("DOJ capture IDs require exact bounded identifiers")
    info = {"capture_id": identifier, "original_body_pin_verified": False}
    if (meta.get("public_raw_available") is not True or body is None
            or str(meta.get("status", "")).startswith("WITHHELD")):
        return info, None, "MISSING_OR_WITHHELD_ORIGINAL_BODY"
    try:
        url = _url(meta.get("requested_url"))
        if (meta.get("final_url") != url or type(meta.get("http_status")) is not int
                or meta["http_status"] != 200 or meta.get("automatic_redirects") is not False
                or meta.get("archive_encoding") not in {None, "raw"}
                or meta.get("status") != "ORIGINAL_BYTES_CAPTURED"):
            return info, None, "UNSUCCESSFUL_OR_CHANGED_PRIMARY_SCOPE"
        started, observed = _clock(meta.get("started_at")), _clock(meta.get("observed_at"))
        if not started <= observed <= ingested:
            return info, None, "INVALID_SOURCE_CAPTURE_CLOCK"
        if (not isinstance(body, bytes) or not 1 <= len(body) <= MAX_CAPTURE_BYTES
                or type(meta.get("raw_response_bytes")) is not int
                or len(body) != meta["raw_response_bytes"]
                or meta.get("raw_response_sha256") != hashlib.sha256(body).hexdigest()):
            return info, None, "ORIGINAL_BODY_PIN_MISMATCH_OR_BYTE_BOUND"
        if _SENSITIVE.search(unescape(body.decode("utf-8", errors="replace")).encode()):
            return info, None, "WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE"
        mime = str(meta.get("content_type", "")).split(";", 1)[0].strip().lower()
        if mime not in {"application/pdf", "text/html"}:
            return info, None, "UNSUPPORTED_PRIMARY_CONTENT_TYPE"
        info.update(source_url=url, content_type=mime, started_at=started.isoformat(),
                    observed_at=observed.isoformat(), body_sha256=meta["raw_response_sha256"],
                    body_bytes=len(body), original_body_pin_verified=True,
                    actual_primary_capture=meta.get("origin") == "LIVE_PRIMARY_RESEARCH_HTTP")
        return info, body, None
    except (ValueError, TypeError, KeyError):
        return info, None, "INVALID_OR_UNAVAILABLE_CAPTURE_METADATA"


def _request(raw):
    if not isinstance(raw, dict):
        raise ValueError("DOJ review request requires one exact object")
    request = {}
    for key in ("issuer_ticker", "issuer_cik", "issuer_legal_name", "case_entity_name", "case_entity_role",
                "docket", "court", "filed_on", "document_kind", "court_capture_id", "issuer_capture_id", "issuer_anchor_date"):
        request[key] = _text(raw.get(key))
    if (not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,12}", request["issuer_ticker"])
            or not re.fullmatch(r"[0-9]{10}", request["issuer_cik"]) or int(request["issuer_cik"]) == 0
            or not re.fullmatch(r"[0-9]{1,2}:[0-9]{2}-[a-z]{2}-[0-9]{4,7}", request["docket"])
            or request["case_entity_role"] not in {"DEFENDANT", "RESPONDENT"}
            or request["document_kind"] not in _KINDS):
        raise ValueError("DOJ review requires exact supported case/issuer selectors")
    _day(request["filed_on"])
    _day(request["issuer_anchor_date"])
    for key in ("issuer_publication_capture_id", "issuer_published_on", "release_capture_id", "release_published_on"):
        if key in raw:
            request[key] = _text(raw[key])
            if key.endswith("_on"):
                _day(request[key])
    passages = raw.get("selected_passages")
    if not isinstance(passages, list) or not 1 <= len(passages) <= MAX_PASSAGES:
        raise ValueError("DOJ review requires bounded selected original passages")
    request["selected_passages"] = []
    for item in passages:
        if (not isinstance(item, dict) or item.get("purpose") not in _PURPOSES
                or type(item.get("page")) is not int or not 1 <= item["page"] <= MAX_PDF_PAGES):
            raise ValueError("DOJ passage requires a purpose and exact one-based page")
        request["selected_passages"].append({"purpose": item["purpose"], "page": item["page"],
            "capture_id": _text(item.get("capture_id"), 80), "excerpt": _text(item.get("excerpt"), 2000)})
    return request


def _registry(request, records, cutoff, url):
    # In-memory construction validates supplied rows; never load() a file or
    # infer an earlier interval from a newer legal registry snapshot.
    from market_checker_app.services.sec_counterparty_identity import DatedCounterpartyIdentityRegistry
    if not isinstance(records, (list, tuple)) or len(records) > 1000:
        raise ValueError("DOJ review legal records require a bounded supplied list")
    try:
        registry = DatedCounterpartyIdentityRegistry(records)
        published = datetime.combine(_day(request["filed_on"]), time.min, timezone.utc)
        result = registry.resolve(disclosed_name=request["case_entity_name"], published_at=published,
                                  as_of=cutoff, exposure_source_url=url)
        row = result.get("counterparty_identity_evidence")
        same = bool(row and row.get("relationship_role") == "DIRECT_ISSUER"
                    and (row.get("issuer_cik"), row.get("issuer_ticker")) ==
                        (request["issuer_cik"], request["issuer_ticker"])
                    and _key(row.get("issuer_legal_name", "")) == _key(request["issuer_legal_name"]))
        return {"resolution": result["counterparty_identity_resolution"], "case_date_supported": same,
                "known_before_case_day": bool(same and _clock(row["known_at"]) < published),
                "legal_continuity_supported": bool(same and row.get("legal_effective_interval_verified") is True),
                "record_id": row["record_id"] if same else None,
                "registry_fingerprint_sha256": registry.fingerprint(as_of=cutoff)}
    except (ValueError, TypeError, KeyError):
        return {"resolution": "INVALID_SUPPLIED_LEGAL_RECORDS", "case_date_supported": False,
                "known_before_case_day": False, "legal_continuity_supported": False, "record_id": None}


def review_doj_enforcement_scope(request, captures, *, as_of, ingested_at, legal_records=()):
    """Review at most four already-loaded original bodies; perform zero I/O.

    PDF pages are one-based; HTML uses page1. Dates are documentary fact dates.
    Only the caller's physical ingestion clock grants report availability.
    Even a reviewed judgment title cannot establish its outcome or liability.
    """
    request = _request(request)
    cutoff, ingested = _clock(as_of), _clock(ingested_at)
    if isinstance(captures, dict):
        if (len(captures) > MAX_CAPTURES or any(not isinstance(value, dict)
                or not isinstance(value.get("metadata"), dict)
                or key != value["metadata"].get("capture_id") for key, value in captures.items())):
            raise ValueError("DOJ capture map keys must equal their original metadata IDs")
        captures = list(captures.values())
    if (not isinstance(captures, (list, tuple)) or not 1 <= len(captures) <= MAX_CAPTURES
            or any(not isinstance(item, dict) for item in captures)):
        raise ValueError("DOJ review requires one to four supplied original captures")
    report = {"schema_version": 1, "review_kind": "DOJ_ENFORCEMENT_SCOPE", "contract_version": CONTRACT_VERSION,
        "status": "PARTIAL", "scope": {k: v for k, v in request.items() if k != "selected_passages"},
        "as_of": cutoff.isoformat(), "ingested_at": ingested.isoformat(), "known_at": ingested.isoformat(),
        "report_available_at_cutoff": ingested <= cutoff, "captures": [], "passage_reviews": [],
        "case_document_scope_supported": False, "exact_case_entity_supported": False,
        "document_stage_supported": False, "recorded_case_stage": "UNKNOWN",
        "independent_earlier_legal_identity_supported": False, "issuer_identity_fact": None,
        "release_publication_supported": False, "release_legal_description_supported": False,
        "adjudication_verified": False, "issuer_attribution_verified": False,
        "parent_liability_verified": False, "company_wide_liability_verified": False,
        "issuer_fine_verified": False, "issuer_conviction_verified": False,
        "current_case_status_verified": False, "complete_issuer_enforcement_coverage": False,
        "historical_availability_reconstructed": False, "publication_time_verified": False,
        "registry_adoption_allowed": False, "live_acceptance_verified": False,
        "scoring_applied": False, "prediction_input": False, "http_requests": 0,
        "registry_identity": {"resolution": "NOT_REVIEWED", "case_date_supported": False,
                              "known_before_case_day": False, "legal_continuity_supported": False, "record_id": None},
        "missing_evidence": []}
    if ingested > cutoff:
        report["missing_evidence"] = ["REPORT_NOT_KNOWN_AT_CUTOFF"]
        return report
    originals, pages, parsers = {}, {}, {}
    for capture in captures:
        info, body, reason = _original(capture, ingested)
        identifier = info["capture_id"]
        if identifier in originals:
            raise ValueError("DOJ review capture IDs must be unique")
        originals[identifier] = (info, body)
        report["captures"].append(info)
        if reason:
            info["review_reason"] = reason
            continue
        try:
            if info["content_type"] == "application/pdf":
                selected = {p["page"] for p in request["selected_passages"] if p["capture_id"] == identifier}
                pages[identifier], info["pdf_page_count"] = _pdf_pages(body, selected)
            else:
                parsers[identifier] = _html(body)
                pages[identifier] = {1: parsers[identifier].lines_text}
            info["review_reason"] = "ORIGINAL_PIN_AND_SELECTED_CONTENT_REVIEWED"
        except _Unavailable as error:
            info["review_reason"] = str(error)
    matched = {}
    for pin in request["selected_passages"]:
        supported = _key(pin["excerpt"]) in _key(pages.get(pin["capture_id"], {}).get(pin["page"], ""))
        report["passage_reviews"].append({"purpose": pin["purpose"], "capture_id": pin["capture_id"],
                                         "page": pin["page"], "supported": supported})
        if supported:
            matched.setdefault(pin["purpose"], []).append(pin)
    report["selected_passages_all_supported"] = all(p["supported"] for p in report["passage_reviews"])
    court_id = request["court_capture_id"]
    court_info, court_body = originals.get(court_id, ({}, None))
    court_pages = pages.get(court_id, {})
    early = " ".join(court_pages.get(page, "") for page in range(1, 4))
    stamp = re.search(r"\bCase\s+(\d{1,2}:\d{2}-[a-z]{2}-\d{4,7})\s+Document\s+(\d+)\s+Filed\s+(\d{2}/\d{2}/\d{2})\s+Page\s+1\s+of\s+(\d+)\b", court_pages.get(1, ""), re.I)
    try:
        stamped_day = datetime.strptime(stamp[3], "%m/%d/%y").date() if stamp else None
    except ValueError:
        stamped_day = None
    caption = [p for p in matched.get("CAPTION", []) if p["capture_id"] == court_id and p["page"] <= 3]
    court_match = re.sub(r"\s", "", request["court"]).casefold() in re.sub(r"\s", "", early).casefold()
    try:
        _url(court_info.get("source_url"), "www.justice.gov")
        court_primary = True
    except (ValueError, TypeError):
        court_primary = False
    scoped = bool(court_primary and caption and stamp and court_match
                  and stamp[1] == request["docket"]
                  and stamped_day == _day(request["filed_on"])
                  and _day(request["filed_on"]) <= _clock(court_info["observed_at"]).date()
                  and (court_info.get("pdf_page_count") is None or int(stamp[4]) == court_info["pdf_page_count"]))
    report["case_document_scope_supported"] = scoped
    entity_pins = [p for p in matched.get("CASE_ENTITY", []) if p["capture_id"] == court_id and p["page"] <= 3]
    role = request["case_entity_role"].lower()
    entity = any(_key(p["excerpt"]).startswith(_key(request["case_entity_name"]) + " ")
                 and any(_key(line) == _key(request["case_entity_name"])
                         for line in court_pages[p["page"]][:4000].splitlines())
                 and re.search(rf"\b{role}\.\s*$", _key(p["excerpt"])) for p in entity_pins)
    report["exact_case_entity_supported"] = bool(scoped and entity)
    # Classify the caption title following the party role, never words in a
    # complaint's relief section, press headline or metadata document label.
    titles = re.findall(rf"\b{role}\.?\s+(AMENDED COMPLAINT|COMPLAINT|PROPOSED FINAL JUDGMENT|FINAL JUDGMENT)\b", early, re.I)
    desired = request["document_kind"].replace("_", " ")
    stage_pins = [p for p in matched.get("DOCUMENT_STAGE", []) if p["capture_id"] == court_id and p["page"] <= 3]
    stage = bool(report["exact_case_entity_supported"] and stage_pins and len(titles) == 1 and titles[0].upper() == desired)
    report["document_stage_supported"] = stage
    if stage:
        report["recorded_case_stage"] = _KINDS[request["document_kind"]]
        report["court_document_number"] = stamp[2]
        report["documentary_filed_on"] = request["filed_on"]
    # The independent SEC index supplies a documentary publication day only;
    # its unzoned Accepted display and renderer size are not a raw-byte pin.
    issuer_id, index_id = request["issuer_capture_id"], request.get("issuer_publication_capture_id")
    issuer_info, issuer_body = originals.get(issuer_id, ({}, None))
    index_info, _ = originals.get(index_id, ({}, None))
    index_parser = parsers.get(index_id)
    try:
        issuer_url = _url(issuer_info.get("source_url"), "www.sec.gov")
        index_url = _url(index_info.get("source_url"), "www.sec.gov")
        prefix = f"/Archives/edgar/data/{int(request['issuer_cik'])}/"
        accession_path = urlsplit(issuer_url).path.rsplit("/", 1)[0]
        if (issuer_body is None or issuer_id not in pages or index_parser is None
                or not urlsplit(issuer_url).path.startswith(prefix)
                or not re.fullmatch(re.escape(prefix) + r"\d{18}/[^/]+", urlsplit(issuer_url).path)
                or urlsplit(index_url).path.rsplit("/", 1)[0] != accession_path
                or not _linked_filing(index_url, index_parser.links, issuer_url)):
            raise ValueError("Unavailable independent filing/index pair")
        pub = re.findall(r"\bFiling Date\s+(\d{4}-\d{2}-\d{2})\b", index_parser.text)
        period = re.findall(r"\bPeriod of Report\s+(\d{4}-\d{2}-\d{2})\b", index_parser.text)
        if (pub != [request.get("issuer_published_on")] or period != [request["issuer_anchor_date"]]
                or not _day(request["issuer_anchor_date"]) <= _day(pub[0]) < _day(request["filed_on"])):
            raise ValueError("Not an independently earlier dated legal source")
        from market_checker_app.services.sec_corporate_action_bridge import issuer_names
        facts = issuer_names(SimpleNamespace(payload=issuer_body, url=issuer_url, published_on=pub[0],
                                             observed_at=issuer_info["observed_at"]))
        exact = [fact for fact in facts if (fact["issuer_cik"], _key(fact["issuer_name"]), fact["anchor_date"]) ==
                 (request["issuer_cik"], _key(request["issuer_legal_name"]), request["issuer_anchor_date"])]
        if len(exact) != 1:
            raise ValueError("Ambiguous same-context legal entity")
        report.update(independent_earlier_legal_identity_supported=True, issuer_identity_fact=exact[0],
                      issuer_documentary_published_on=pub[0], issuer_documentary_anchor_date=period[0],
                      issuer_index_accepted_display=re.findall(r"\bAccepted\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\b", index_parser.text),
                      issuer_index_accepted_timezone_verified=False)
    except (ValueError, TypeError, KeyError, UnicodeError):
        pass
    release_id = request.get("release_capture_id")
    if release_id:
        release_info, _ = originals.get(release_id, ({}, None))
        parser = parsers.get(release_id)
        try:
            _url(release_info.get("source_url"), "www.justice.gov")
            displayed = [_clock(value).date().isoformat() for value in parser.datetimes]
            report["release_publication_supported"] = (displayed == [request.get("release_published_on")]
                and any(p["capture_id"] == release_id for p in matched.get("SOURCE_PUBLICATION", []))
                and _day(displayed[0]) == _day(request["filed_on"]))
            report["release_legal_description_supported"] = any(p["capture_id"] == release_id
                and _key(p["excerpt"]).startswith(_key(request["case_entity_name"]) + " is ")
                for p in matched.get("LEGAL_ENTITY", []))
            report["release_documentary_published_on"] = displayed[0] if report["release_publication_supported"] else None
            report["release_reported_datetime_attribute"] = parser.datetimes if report["release_publication_supported"] else []
            report["release_updated_display"] = re.findall(r"\bUpdated\s+([A-Za-z]+ \d{1,2}, \d{4})\b", parser.text)
        except (ValueError, TypeError, AttributeError, IndexError):
            pass
    if court_primary:
        report["registry_identity"] = _registry(request, legal_records, cutoff, court_info["source_url"])
    required = {
        "MISSING_EXACT_COURT_DOCKET_FILING_DAY": report["case_document_scope_supported"],
        "MISSING_SELECTED_ORIGINAL_PASSAGE": report["selected_passages_all_supported"],
        "MISSING_EXACT_CASE_ENTITY_ROLE": report["exact_case_entity_supported"],
        "UNSUPPORTED_DOCUMENT_STAGE_OR_HEADER": report["document_stage_supported"],
        "MISSING_INDEPENDENT_EARLIER_DATED_SEC_LEGAL_ENTITY": report["independent_earlier_legal_identity_supported"],
        "MISSING_CASE_DATE_REGISTERED_IDENTITY": report["registry_identity"]["case_date_supported"],
        "MISSING_IDENTITY_KNOWN_BEFORE_CASE_DAY": report["registry_identity"]["known_before_case_day"],
        "MISSING_INDEPENDENT_LEGAL_CONTINUITY": report["registry_identity"]["legal_continuity_supported"],
    }
    if release_id:
        required["MISSING_RELEASE_PUBLICATION_PIN"] = report["release_publication_supported"]
        required["MISSING_RELEASE_EXACT_LEGAL_DESCRIPTION"] = report["release_legal_description_supported"]
    report["missing_evidence"] = [code for code, supported in required.items() if not supported]
    # A name equality remains a candidate join, not a continuous legal bridge.
    report["case_entity_same_literal_as_issuer"] = _key(request["case_entity_name"]) == _key(request["issuer_legal_name"])
    report["review_fingerprint_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return deepcopy(report)


__all__ = ["CONTRACT_VERSION", "review_doj_enforcement_scope"]
