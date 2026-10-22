"""Original FDA application objects plus a bounded, dated SEC legal-entity row.

CRL company fields identify an addressee. A drug mentioned in letter text is
not a product-ownership record, and a subsidiary list is not an application,
license or product assignment. This review preserves those separate scopes.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import re
from urllib.parse import parse_qsl, urlsplit
from xml.etree.ElementTree import ParseError

from market_checker_app.services.healthcare_identity_service import application_values, normalize_application
from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body
from market_checker_app.services.sec_operational_metrics_review import _dei_and_contexts, _field
from market_checker_app.utils.source_validation import public_https_reference


REVIEW_VERSION = "healthcare-product-application-legal-scope-v1"
MAX_BODY_BYTES = 4_000_000
MAX_METADATA_BYTES = 64_000
_ISSUERS = {"PFE": "0000078003", "MRK": "0000310158", "ABBV": "0001551152"}


@dataclass(frozen=True, slots=True)
class CapturedHealthcareSource:
    metadata: Mapping
    body: bytes | None


@dataclass(frozen=True, slots=True)
class FdaProductApplicationScope:
    ticker: str
    issuer_cik: str
    application_number: str
    file_name: str
    letter_date: str
    company_name: str
    product_markers: tuple[str, ...] = ()

    def __post_init__(self):
        if isinstance(self.product_markers, list):
            object.__setattr__(self, "product_markers", tuple(self.product_markers))

    def validate(self):
        if (not isinstance(self.ticker, str) or _ISSUERS.get(self.ticker) != self.issuer_cik
                or not isinstance(self.application_number, str)
                or normalize_application(self.application_number) != self.application_number
                or not isinstance(self.file_name, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,180}\.pdf", self.file_name)
                or not isinstance(self.company_name, str) or not 1 <= len(self.company_name) <= 200
                or not isinstance(self.product_markers, tuple) or len(self.product_markers) > 5
                or any(not isinstance(value, str) or not 1 <= len(value) <= 120 for value in self.product_markers)
                or len(set(self.product_markers)) != len(self.product_markers)):
            raise ValueError("Select one exact canonical FDA application/file/date/addressee scope")
        _day(self.letter_date)
        if _has_sensitive_body(json.dumps(self.__dict__ if hasattr(self, "__dict__") else {
                "company_name": self.company_name, "product_markers": self.product_markers}).encode()):
            raise ValueError("Unsafe whole scope is withheld")


@dataclass(frozen=True, slots=True)
class SecOwnershipSelection:
    table_index: int
    row_index: int
    entity_name: str
    parent_context_id: str
    as_of_date: str

    def validate(self):
        if (type(self.table_index) is not int or not 0 <= self.table_index < 100
                or type(self.row_index) is not int or not 0 <= self.row_index < 3000
                or not isinstance(self.entity_name, str) or not 1 <= len(self.entity_name) <= 200
                or not isinstance(self.parent_context_id, str)
                or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", self.parent_context_id)):
            raise ValueError("Select one bounded SEC ownership row and parent context")
        _day(self.as_of_date)
        if _has_sensitive_body(json.dumps({"entity_name": self.entity_name}).encode()):
            raise ValueError("Unsafe whole ownership selection is withheld")


class _Rejected(ValueError):
    pass


def _aware(value: object) -> datetime:
    try:
        clock = datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError as error:
        raise _Rejected("INVALID_EXPLICIT_SOURCE_CLOCK") from error
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise _Rejected("INVALID_EXPLICIT_SOURCE_CLOCK")
    return clock.astimezone(timezone.utc)


def _day(value: object) -> date:
    try:
        result = date.fromisoformat(value)
        if result.isoformat() != value:
            raise ValueError("Noncanonical day")
    except (ValueError, TypeError) as error:
        raise _Rejected("EXACT_ISO_SOURCE_DAY_REQUIRED") from error
    return result


def _name(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise _Rejected("EXACT_LEGAL_NAME_NOT_CAPTURED")
    return " ".join(value.split()).casefold()


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise _Rejected("DUPLICATE_ORIGINAL_JSON_FIELD")
        result[key] = value
    return result


def _constant(value):
    raise _Rejected("NONFINITE_ORIGINAL_JSON_VALUE")


def _source(capture: CapturedHealthcareSource | None, *, kind: str,
            scope: FdaProductApplicationScope, clock: datetime) -> tuple[bytes, dict]:
    if capture is None:
        raise _Rejected(f"{kind}_ORIGINAL_CAPTURE_MISSING")
    if not isinstance(capture, CapturedHealthcareSource) or not isinstance(capture.metadata, Mapping):
        raise _Rejected(f"{kind}_CAPTURE_METADATA_INVALID")
    metadata = capture.metadata
    if (capture.body is None or metadata.get("raw_publication_permitted") is False
            or metadata.get("public_raw_available") is False
            or metadata.get("retention") in {"WITHHELD_SENSITIVE_BODY", "MISSING_BODY"}
            or any(str(metadata.get(key, "")).startswith("WITHHELD") for key in
                   ("status", "publication_status", "archive_status"))):
        raise _Rejected(f"{kind}_WHOLE_BODY_WITHHELD_OR_UNAVAILABLE")
    try:
        metadata_bytes = json.dumps(dict(metadata), ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError) as error:
        raise _Rejected(f"{kind}_CAPTURE_METADATA_INVALID") from error
    if len(metadata_bytes) > MAX_METADATA_BYTES or _has_sensitive_body(metadata_bytes):
        raise _Rejected(f"{kind}_UNSAFE_OR_UNBOUNDED_WHOLE_METADATA")
    body = capture.body
    if (metadata.get("capture_form") != "ORIGINAL_HTTP_RESPONSE"
            and metadata.get("acquisition_method") != "urllib.request raw HTTP body"):
        raise _Rejected(f"{kind}_NONORIGINAL_OR_PROJECTED_SOURCE")
    if (type(metadata.get("http_status")) is not int or metadata["http_status"] != 200
            or metadata.get("body_complete_within_byte_budget") is not True):
        raise _Rejected(f"{kind}_COMPLETE_HTTP_200_BODY_REQUIRED")
    if (not isinstance(body, bytes) or not 1 <= len(body) <= MAX_BODY_BYTES
            or type(metadata.get("body_bytes")) is not int or metadata["body_bytes"] != len(body)
            or type(metadata.get("byte_budget")) is not int or len(body) > metadata["byte_budget"]
            or not isinstance(metadata.get("body_sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", metadata["body_sha256"])
            or hashlib.sha256(body).hexdigest() != metadata["body_sha256"]):
        raise _Rejected(f"{kind}_ORIGINAL_BODY_HASH_LENGTH_OR_BOUND_MISMATCH")
    if _has_sensitive_body(body):
        raise _Rejected(f"{kind}_UNSAFE_WHOLE_BODY_WITHHELD")
    request, response = metadata.get("request_url"), metadata.get("response_url")
    try:
        if not isinstance(request, str) or len(request) > 2048 or request != response:
            raise ValueError("Source redirect or reference conflict")
        public_https_reference(request)
        parsed = urlsplit(request)
        if parsed.port not in {None, 443} or parsed.fragment:
            raise ValueError("Source reference outside bounds")
        if kind == "FDA":
            query_items = parse_qsl(parsed.query, keep_blank_values=True)
            query = dict(query_items)
            expected = f'company_name:"{scope.company_name}"'
            allowed = {expected, expected + f' AND letter_year:"{scope.letter_date[:4]}"'}
            if (parsed.hostname != "api.fda.gov" or parsed.path != "/transparency/crl.json"
                    or len(query) != len(query_items) or set(query) != {"search", "limit", "skip"}
                    or query["search"] not in allowed or query["skip"] != "0"
                    or not re.fullmatch(r"[1-9][0-9]{0,2}", query["limit"])
                    or not 1 <= int(query["limit"]) <= 100):
                raise ValueError("Unsupported or unbounded FDA query")
            reference = {"query_search": query["search"], "query_limit": int(query["limit"]), "query_skip": 0}
        else:
            path = re.fullmatch(r"/Archives/edgar/data/([0-9]{1,10})/([0-9]{18})/([A-Za-z0-9_.-]{1,180}\.html?)", parsed.path)
            if (parsed.hostname != "www.sec.gov" or parsed.query or path is None
                    or str(int(path[1])).zfill(10) != scope.issuer_cik):
                raise ValueError("Unsupported SEC parent source reference")
            reference = {"archive_routing_cik": scope.issuer_cik, "accession_digits": path[2]}
    except ValueError as error:
        raise _Rejected(f"{kind}_EXACT_SAFE_PRIMARY_REFERENCE_REQUIRED") from error
    requested, observed = _aware(metadata.get("requested_at")), _aware(metadata.get("observed_at"))
    if not requested <= observed <= clock:
        raise _Rejected(f"{kind}_CAPTURE_NOT_AVAILABLE_AT_ACTUAL_REVIEW")
    return body, {"source_url": request, "body_sha256": metadata["body_sha256"], "body_bytes": len(body),
                  "requested_at": requested.isoformat(), "observed_at": observed.isoformat(), **reference}


def _fda_object(body: bytes, pin: dict, scope: FdaProductApplicationScope) -> dict:
    try:
        payload = json.loads(body.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise _Rejected("FDA_ORIGINAL_JSON_INVALID") from error
    if _has_sensitive_body(json.dumps(payload, ensure_ascii=False).encode()):
        raise _Rejected("FDA_UNSAFE_DECODED_WHOLE_BODY_WITHHELD")
    if (not isinstance(payload, dict) or not isinstance(payload.get("meta"), dict)
            or not isinstance(payload.get("results"), list) or len(payload["results"]) > pin["query_limit"]
            or any(not isinstance(row, dict) for row in payload["results"])):
        raise _Rejected("FDA_ORIGINAL_OBJECT_SCHEMA_INVALID")
    version = payload["meta"].get("last_updated")
    _day(version)
    totals = payload["meta"].get("results", {})
    if (not isinstance(totals, dict) or any(type(totals.get(key)) is not int for key in ("skip", "limit", "total"))
            or totals["skip"] != 0 or totals["limit"] != pin["query_limit"]
            or totals["total"] < len(payload["results"])):
        raise _Rejected("FDA_QUERY_VERSION_TOTAL_CONFLICT")
    matches = []
    for index, row in enumerate(payload["results"]):
        try:
            applications = application_values(row.get("application_number"))
            normalized = [normalize_application(value) for value in applications]
            letter_day = datetime.strptime(row.get("letter_date", ""), "%m/%d/%Y").date().isoformat()
        except (ValueError, TypeError) as error:
            raise _Rejected("FDA_APPLICATION_OR_LETTER_DAY_MALFORMED") from error
        if any(value is None for value in normalized):
            raise _Rejected("FDA_APPLICATION_IDENTIFIER_MALFORMED")
        if (scope.application_number in normalized and row.get("file_name") == scope.file_name
                and letter_day == scope.letter_date and _name(row.get("company_name")) == _name(scope.company_name)):
            matches.append((index, row, applications, normalized))
    if len(matches) != 1:
        raise _Rejected("FDA_EXACT_EVENT_OBJECT_MISSING_OR_AMBIGUOUS")
    index, row, applications, normalized = matches[0]
    if len(set(normalized)) != 1:
        raise _Rejected("FDA_MULTI_APPLICATION_OBJECT_SCOPE_WITHHELD")
    if (row.get("letter_type") != "COMPLETE RESPONSE" or str(row.get("letter_year")) != scope.letter_date[:4]
            or _day(scope.letter_date) > _day(version)
            or _day(version) > _aware(pin["observed_at"]).date()):
        raise _Rejected("FDA_LETTER_TYPE_OR_PUBLISHER_CLOCK_CONFLICT")
    text = row.get("text")
    if not isinstance(text, str) or not text:
        raise _Rejected("FDA_ORIGINAL_LETTER_TEXT_NOT_CAPTURED")
    mentions = []
    for marker in scope.product_markers:
        matches = list(re.finditer(r"(?<!\w)" + re.escape(marker) + r"(?!\w)", text, flags=re.I))
        if not matches:
            raise _Rejected("EXACT_PRODUCT_TEXT_MARKER_NOT_IN_FDA_OBJECT")
        mentions.append({"requested_marker": marker, "literal_source_text": matches[0][0],
                         "first_text_offset": matches[0].start(), "mentions": len(matches),
                         "role": "LETTER_TEXT_MENTION_ONLY"})
    row_hash = hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return {"application_number": scope.application_number, "original_application_values": list(applications),
            "file_name": row["file_name"], "letter_date": scope.letter_date,
            "original_letter_date": row["letter_date"], "company_name": row["company_name"],
            "company_field_role": "CRL_ADDRESSEE_ONLY", "approval_status_as_displayed": row.get("approval_status"),
            "approval_status_is_event_day_state": False, "product_mentions": mentions,
            "structured_product_identity_available": bool(row.get("product_name") or row.get("drug_name")),
            "product_ownership_proven_by_letter_text": False, "application_holder_role_inferred": False,
            "publisher_last_updated": version, "publisher_version_is_first_observation": False,
            "original_capture_observed_at": pin["observed_at"], "source_object_locator": f"/results/{index}",
            "source_object_sha256": row_hash, "letter_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "query_total": totals["total"], "returned_rows": len(payload["results"]),
            "exact_query_complete": totals["total"] == len(payload["results"]) < pin["query_limit"],
            "filename_does_not_define_event_date": True, "issuer_wide_scope": False}


class _OwnershipTables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.stack, self.text, self.hidden = [], [], [], 0
        self.recent_text = ""

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if self.hidden:
            return
        if tag == "table":
            if len(self.tables) >= 100:
                raise _Rejected("OWNERSHIP_TABLE_BOUND_EXCEEDED")
            table = {"rows": [], "row": None, "cell": None,
                     "scope_prefix": " ".join(self.recent_text.split())}
            self.tables.append(table)
            self.stack.append(table)
        elif self.stack and tag == "tr":
            self.stack[-1]["row"] = []
        elif self.stack and tag in {"td", "th"}:
            self.stack[-1]["cell"] = []

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
            return
        if self.hidden or not self.stack:
            return
        table = self.stack[-1]
        if tag in {"td", "th"} and table["cell"] is not None:
            value = " ".join("".join(table["cell"]).split())
            if len(value) > 1000 or table["row"] is None or len(table["row"]) >= 50:
                raise _Rejected("OWNERSHIP_CELL_BOUND_OR_STRUCTURE_INVALID")
            table["row"].append(value)
            table["cell"] = None
        elif tag == "tr" and table["row"] is not None:
            if len(table["rows"]) >= 3000:
                raise _Rejected("OWNERSHIP_ROW_BOUND_EXCEEDED")
            table["rows"].append(table["row"])
            table["row"] = None
        elif tag == "table":
            self.stack.pop()

    def handle_data(self, value):
        if not self.hidden:
            self.text.append(value)
            self.recent_text = (self.recent_text + " " + value)[-4000:]
            if self.stack and self.stack[-1]["cell"] is not None:
                self.stack[-1]["cell"].append(value)


def _ownership(body: bytes, issuer_body: bytes, pins: dict, selection: SecOwnershipSelection,
               scope: FdaProductApplicationScope) -> dict:
    if (pins["ownership"]["accession_digits"] != pins["issuer"]["accession_digits"]
            or pins["ownership"]["source_url"] == pins["issuer"]["source_url"]
            or pins["ownership"]["body_sha256"] == pins["issuer"]["body_sha256"]):
        raise _Rejected("OWNERSHIP_AND_PARENT_DOCUMENT_DISCLOSURE_PAIR_CONFLICT")
    if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", issuer_body, re.I):
        raise _Rejected("UNSUPPORTED_PARENT_XML_ENTITY_OR_DTD")
    try:
        facts, contexts = _dei_and_contexts(issuer_body)
        context = contexts.get(selection.parent_context_id)
        selected = facts.get(selection.parent_context_id, {})
        parent, _ = _field(selected, "EntityRegistrantName")
        cik, _ = _field(selected, "EntityCentralIndexKey")
        period, _ = _field(selected, "DocumentPeriodEndDate")
        form, _ = _field(selected, "DocumentType")
    except (ValueError, ParseError, RecursionError) as error:
        raise _Rejected("EXACT_PARENT_DEI_CONTEXT_NOT_PROVEN") from error
    if (cik != scope.issuer_cik or context is None or context["entity_identifier"] != cik
            or context["dimensions"] or context["has_segment_or_scenario"] or form != "10-K"
            or context["end_date"] != period or period != selection.as_of_date):
        raise _Rejected("PARENT_NAME_CIK_PERIOD_SAME_CONTEXT_CONFLICT")
    if _name(parent) == _name(selection.entity_name):
        raise _Rejected("DIRECT_ISSUER_NAME_IS_NOT_SUBSIDIARY_OWNERSHIP_PROOF")
    parser = _OwnershipTables()
    try:
        parser.feed(body.decode("utf-8"))
    except (UnicodeError, RecursionError) as error:
        raise _Rejected("ORIGINAL_OWNERSHIP_HTML_INVALID") from error
    text = " ".join(" ".join(parser.text).split())
    day = _day(selection.as_of_date)
    display_day = day.strftime("%B") + f" {day.day}, {day.year}"
    if (not re.search(r"\bEXHIBIT\s+21(?:\.1)?\b", text, re.I)
            or not re.search(r"\bSUBSIDIARIES\s+OF\s+" + re.escape(parent) + r"(?!\w)", text, re.I)
            or not re.search(r"\bas\s+of\s+(?:" + re.escape(display_day) + "|" + re.escape(day.isoformat()) + r")\b", text, re.I)):
        raise _Rejected("EXHIBIT21_EXACT_PARENT_AND_POINT_DATE_NOT_CAPTURED")
    try:
        selected_table = parser.tables[selection.table_index]
        rows = selected_table["rows"]
        row = rows[selection.row_index]
    except IndexError as error:
        raise _Rejected("SELECTED_OWNERSHIP_ROW_NOT_CAPTURED") from error
    prefix = selected_table["scope_prefix"]
    named_heads = list(re.finditer(r"\bSUBSIDIARIES\s+OF\s+" + re.escape(parent) + r"(?!\w)", prefix, re.I))
    parent_heads = list(re.finditer(r"\bSUBSIDIARIES\s+OF\s+", prefix, re.I))
    point_days = list(re.finditer(r"\bas\s+of\s+(?:" + re.escape(display_day) + "|" + re.escape(day.isoformat()) + r")\b", prefix, re.I))
    any_points = list(re.finditer(r"\bas\s+of\s+", prefix, re.I))
    if (not named_heads or not parent_heads or named_heads[-1].start() != parent_heads[-1].start()
            or not point_days or not any_points or point_days[-1].start() != any_points[-1].start()):
        raise _Rejected("SELECTED_TABLE_PARENT_AND_POINT_DAY_SCOPE_NOT_PROVEN")
    matching_rows = [cells for table in parser.tables for cells in table["rows"]
                     if any(_name(cell) == _name(selection.entity_name) for cell in cells if cell)]
    if (len(matching_rows) != 1 or not row or _name(row[0]) != _name(selection.entity_name)
            or _name(selection.entity_name) != _name(scope.company_name) or len(row) < 2 or not row[1].strip()):
        raise _Rejected("EXACT_LEGAL_ENTITY_OWNERSHIP_ROW_MISSING_OR_AMBIGUOUS")
    if selection.as_of_date != scope.letter_date:
        raise _Rejected("OWNERSHIP_POINT_DAY_DOES_NOT_PROVE_FDA_EVENT_DAY")
    return {"relationship": "LISTED_SUBSIDIARY_ONLY", "legal_entity_name": row[0], "jurisdiction_as_displayed": row[1],
            "parent_name": parent, "parent_cik": cik, "parent_context_id": selection.parent_context_id,
            "as_of_date": selection.as_of_date, "table_index": selection.table_index, "row_index": selection.row_index,
            "ownership_percentage_verified": False, "direct_or_indirect_control_verified": False,
            "application_or_product_ownership_verified": False}


def review_healthcare_product_scope(*, scope: FdaProductApplicationScope,
                                    fda_capture: CapturedHealthcareSource | None,
                                    ownership_capture: CapturedHealthcareSource | None,
                                    issuer_capture: CapturedHealthcareSource | None,
                                    ownership_selection: SecOwnershipSelection | None,
                                    reviewed_at: datetime) -> dict:
    """Pure bounded source/identity review; current ingestion never backdates bytes."""
    if not isinstance(scope, FdaProductApplicationScope):
        raise ValueError("Explicit FDA scope required")
    scope.validate()
    if ownership_selection is not None:
        if not isinstance(ownership_selection, SecOwnershipSelection):
            raise ValueError("Explicit SEC ownership selection required")
        ownership_selection.validate()
    clock = _aware(reviewed_at)
    result = {"schema_version": REVIEW_VERSION, "decision": "WITHHOLD", "identity_status": "UNVERIFIED",
              "reviewed_at": clock.isoformat(), "ingested_at": clock.isoformat(),
              "source_object_facts": None, "original_capture_pins": {}, "ownership_facts": None,
              "proposed_record": None, "reasons": [], "candidate_legal_bridge_complete": False,
              "issuer_attribution_allowed": False, "product_ownership_verified": False,
              "application_holder_role_inferred": False, "historical_ownership_attribution_allowed": False,
              "historical_knowledge_reconstructed": False, "issuer_wide_scope": False,
              "negative_issuer_completeness_verified": False, "live_acceptance_verified": False,
              "source_http_requests": 0, "quota_modified": False, "registry_modified": False,
              "score_changes": False, "prediction_input": False}
    bodies = {}
    for label, capture in (("FDA", fda_capture), ("OWNERSHIP", ownership_capture), ("ISSUER", issuer_capture)):
        try:
            body, pin = _source(capture, kind=label, scope=scope, clock=clock)
            bodies[label] = body
            result["original_capture_pins"][label.lower()] = pin
        except _Rejected as error:
            result["reasons"].append(str(error))
    if "FDA" in bodies:
        try:
            result["source_object_facts"] = _fda_object(bodies["FDA"], result["original_capture_pins"]["fda"], scope)
        except _Rejected as error:
            result["reasons"].append(str(error))
    if ownership_selection is None:
        result["reasons"].append("EXACT_DATED_OWNERSHIP_SELECTION_MISSING")
    pins = result["original_capture_pins"]
    if "fda" in pins and any(key in pins and _aware(pins[key]["observed_at"]) > _aware(pins["fda"]["requested_at"])
                             for key in ("ownership", "issuer")):
        result["reasons"].append("LEGAL_OWNER_EVIDENCE_NOT_AVAILABLE_BEFORE_ORIGINAL_FDA_OBJECT")
    if result["reasons"]:
        return result
    try:
        facts, pins = result["source_object_facts"], result["original_capture_pins"]
        if not facts["exact_query_complete"]:
            raise _Rejected("FDA_QUERY_SCOPE_NOT_PROVEN_COMPLETE")
        if any(_aware(pins[key]["observed_at"]) > _aware(pins["fda"]["requested_at"])
               for key in ("ownership", "issuer")):
            raise _Rejected("LEGAL_OWNER_EVIDENCE_NOT_AVAILABLE_BEFORE_ORIGINAL_FDA_OBJECT")
        ownership = _ownership(bodies["OWNERSHIP"], bodies["ISSUER"], pins, ownership_selection, scope)
        result["ownership_facts"] = ownership
        digest = hashlib.sha256(json.dumps({"application": facts, "ownership": ownership, "captures": pins},
                                           sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        # This is a narrow legal-entity observation, never a product/IP/license assignment.
        result.update(decision="PROPOSE_NARROW_LEGAL_ENTITY_FOR_OWNER_REVIEW",
                      identity_status="PROPOSED_SOURCE_LEGAL_ENTITY_ONLY", candidate_legal_bridge_complete=True,
                      proposed_record={"schema_version": 1, "record_id": f"fda-legal-scope-{digest[:24]}",
                                       "ticker": scope.ticker, "issuer_cik": scope.issuer_cik,
                                       "application_object": facts, "legal_entity_relationship": ownership,
                                       "known_at": clock.isoformat(), "ingested_at": clock.isoformat(),
                                       "source_captures": pins, "snapshot_sha256": digest, "adopted": False,
                                       "scope": "CRL_ADDRESSEE_IDENTITY_AND_POINT_DATED_LISTED_SUBSIDIARY_ONLY",
                                       "product_or_application_ownership_verified": False,
                                       "historical_prediction_knowledge_available": False})
    except _Rejected as error:
        result["reasons"].append(str(error))
    return result
