"""Pure dated subsidiary-roster review beside original FDA role objects.

A reporting issuer's subsidiary roster is a point disclosure, not a direct
legal-parent chain, ownership start, current control or product assignment.
Exact applicant names are never replaced with a similar subsidiary name.
"""
from collections import Counter
from collections.abc import Mapping
from datetime import date, datetime, timezone
import hashlib
import json
import re
from urllib.parse import urljoin, urlsplit

from market_checker_app.services.fda_application_product_review import _Page, _within, review_fda_application_product
from market_checker_app.services.fda_structured_product_archive import _clock
from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body

VERSION = "fda-exact-legal-applicant-roster-point-v1"
MAX_BODY = 4_000_000
WITHHELD = frozenset(("84b6ad89bee343819008e22babdf9a87dbfd3034a99022ea68320f0f17edf8b3",
    "deb5642d450c0fd60cd12066a42da8c1905f4fd47562afcef06cf8e428bcbeb1",
    "5130e6c90a0770d32e40f5eafcc87cd980d963934ae6c125963d8b1f2c20eac1",
    "27f03d6bb63f0590a82369556419c4eb8f5fb9ab3b3da44a9c8695d18d7b7532"))


def _physical_clock():
    return datetime.now(timezone.utc)


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()


def _literal(value, maximum=300):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("EXACT_BOUNDED_LEGAL_SOURCE_LITERAL_REQUIRED")
    return value


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("EXACT_ROSTER_POINT_DAY_REQUIRED")
    return date.fromisoformat(value)


def _sec_source(pair, scope, cutoff):
    if not isinstance(pair, (tuple, list)) or len(pair) != 2 or not isinstance(pair[0], Mapping):
        raise ValueError("ORIGINAL_SEC_SOURCE_DESCRIPTOR_REQUIRED")
    meta, body = pair
    if (meta.get("body_sha256") in WITHHELD or body is None or meta.get("raw_publication_permitted") is False
            or meta.get("public_raw_available") is False
            or any(str(meta.get(k, "")).startswith(("WITHHELD", "MISSING", "NOT_CAPTURED"))
                   for k in ("retention", "status", "publication_status", "archive_status"))):
        raise ValueError("WHOLE_SEC_SOURCE_UNAVAILABLE_NO_SUBSTITUTE")
    if len(_encoded(dict(meta))) > 64000 or _has_sensitive_body(_encoded(dict(meta))):
        raise ValueError("SAFE_BOUNDED_WHOLE_SEC_METADATA_REQUIRED")
    if (meta.get("capture_form") != "ORIGINAL_HTTP_RESPONSE" and meta.get("acquisition_method") != "urllib.request raw HTTP body"
            or type(meta.get("http_status")) is not int or meta["http_status"] != 200
            or meta.get("body_complete_within_byte_budget") is not True or meta.get("body_transformed", False) is not False):
        raise ValueError("COMPLETE_UNTRANSFORMED_ORIGINAL_SEC_HTTP200_REQUIRED")
    if (not isinstance(body, bytes) or not 0 < len(body) <= MAX_BODY
            or type(meta.get("body_bytes")) is not int or len(body) != meta["body_bytes"]
            or type(meta.get("byte_budget")) is not int or not len(body) <= meta["byte_budget"] <= MAX_BODY
            or not isinstance(meta.get("body_sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", meta["body_sha256"])
            or hashlib.sha256(body).hexdigest() != meta["body_sha256"] or _has_sensitive_body(body)):
        raise ValueError("WHOLE_SEC_HASH_LENGTH_CAP_OR_SAFETY_CONFLICT")
    url = meta.get("request_url")
    if not isinstance(url, str) or url != meta.get("response_url") or len(url) > 2048 or any(ord(c) < 33 for c in url):
        raise ValueError("EXACT_ORIGINAL_SEC_REFERENCE_REQUIRED")
    parsed = urlsplit(url)
    path = re.fullmatch(r"/Archives/edgar/data/([0-9]{1,10})/([0-9]{18})/([A-Za-z0-9_.-]{1,180}\.html?)", parsed.path)
    if (parsed.scheme != "https" or parsed.netloc != "www.sec.gov" or parsed.query or parsed.fragment
            or path is None or path[1].zfill(10) != scope["issuer_cik"]):
        raise ValueError("EXACT_SEC_ISSUER_ROUTING_ACCESSION_REQUIRED")
    requested, observed = _clock(meta.get("requested_at")), _clock(meta.get("observed_at"))
    known = _clock(meta.get("known_at", meta.get("observed_at")))
    if not requested <= observed <= known <= cutoff:
        raise ValueError("SEC_ORIGINAL_KNOWLEDGE_CLOCK_CONFLICT")
    return body, {"source_url": url, "body_sha256": meta["body_sha256"], "body_bytes": len(body),
                  "accession_digits": path[2], "filename": path[3], "archive_routing_cik": path[1].zfill(10),
                  "requested_at": requested.isoformat(), "observed_at": observed.isoformat(), "known_at": known.isoformat(),
                  "capture_is_historical_first_availability": False}


def _wrapper(body, pin, kind):
    prefix = body[:2000].decode("utf-8", errors="strict")
    form = re.findall(r"(?m)^<TYPE>([^\r\n<]+)", prefix)
    filename = re.findall(r"(?m)^<FILENAME>([^\r\n<]+)", prefix)
    if form != [kind] or filename != [pin["filename"]]:
        raise ValueError("SEC_ORIGINAL_DOCUMENT_TYPE_OR_FILENAME_CONFLICT")


def _cover(page, scope, day):
    # Explicit adjacent visible cover blocks bind the literal registrant name.
    # A routing CIK remains routing evidence; no missing DEI CIK is invented.
    markers = [n for n in page.nodes if n.tag == "div" and n.text() == "(Exact name of registrant as specified in its charter)"]
    matches = []
    for marker in markers:
        siblings = [n for n in marker.parent.children if n.tag == "div"]
        if marker in siblings and siblings.index(marker) > 0:
            previous = siblings[siblings.index(marker) - 1]
            if previous.text() == scope["issuer_name"] and previous.closed and marker.closed and not previous.hidden:
                matches.append(previous)
    if len(matches) != 1:
        raise ValueError("EXACT_ADJACENT_LEGACY_COVER_REGISTRANT_REQUIRED")
    period = f"For the fiscal year ended {day.strftime('%B')} {day.day}, {day.year}"
    periods = [n for n in page.nodes if n.tag == "div" and n.text() == period]
    if len(periods) != 1 or periods[0].hidden or not periods[0].closed or abs(periods[0].ordinal - matches[0].ordinal) > 300:
        raise ValueError("EXACT_NEARBY_LEGACY_COVER_FISCAL_POINT_REQUIRED")
    return {"registrant_name_as_displayed": scope["issuer_name"], "registrant_name_node": matches[0].ordinal,
            "fiscal_point_as_displayed": day.isoformat(), "fiscal_point_node": periods[0].ordinal,
            "CIK_source": "SEC_ARCHIVE_ROUTING_ONLY", "independent_legal_CIK_body_fact_verified": False}


def _text_outside(node, excluded_tag):
    if node.hidden or node.tag == excluded_tag:
        return ""
    return " ".join(" ".join(_text_outside(part, excluded_tag) if not isinstance(part, str) else part
                             for part in node.parts).split())


def _roster_table_scope(page, tables, day):
    # A global heading cannot turn a later independent table into subsidiaries.
    # Accept only this explicit roster-only document layout and contiguous table
    # continuations; any new visible section/caption needs its own adapter.
    bodies = [node for node in page.nodes if node.tag == "body"]
    if (len(bodies) != 1 or bodies[0].hidden or not bodies[0].closed
            or any(not _within(table, bodies[0]) for table in tables)):
        raise ValueError("SINGLE_COMPLETE_ROSTER_DOCUMENT_BODY_REQUIRED")
    point = re.escape(f"{day.strftime('%B')} {day.day}, {day.year}")
    introduction = (rf"The following is a list of subsidiaries of the Company as of {point}"
                    r"(?:, omitting some subsidiaries(?: which, considered in the aggregate, would not constitute a significant subsidiary)?)?\.")
    ambient = _text_outside(bodies[0], "table")
    if not re.fullmatch(rf"EXHIBIT 21(?: SUBSIDIARIES OF THE COMPANY)?(?: {introduction})?", ambient):
        raise ValueError("INDEPENDENT_SECTION_OUTSIDE_EXACT_ROSTER_TABLE_SCOPE")
    if any(_text_outside(table, "tr") for table in tables):
        raise ValueError("UNSCOPED_CAPTION_OR_TEXT_INSIDE_ROSTER_TABLE")


def _roster(page, scope, day):
    text = page.root.text()
    if (not re.search(r"\bEXHIBIT\s+21\b", text) or "SUBSIDIARIES OF THE COMPANY" not in text
            or any(not text[m.end():].startswith("the Company") and not text[m.end():].startswith("THE COMPANY")
                   for m in re.finditer(r"\bsubsidiaries of\s+", text, re.I))):
        raise ValueError("EXACT_SINGLE_REPORTING_COMPANY_ROSTER_SCOPE_REQUIRED")
    days = re.findall(r"\bas of ([A-Z][a-z]+ [0-9]{1,2}, [0-9]{4})", text)
    if not days or len(days) > 16 or any(datetime.strptime(value, "%B %d, %Y").date() != day for value in days):
        raise ValueError("ROSTER_POINT_DATE_CONFLICT_OR_UNAVAILABLE")
    if len(re.findall(r"\bas of\b", text, re.I)) != len(days):
        raise ValueError("UNRESOLVED_ROSTER_POINT_DATE_SCOPE")
    tables = [n for n in page.nodes if n.tag == "table"]
    if not 1 <= len(tables) <= 64 or any(n.hidden or not n.closed or any(p.tag == "table" and _within(n, p) for p in tables if p is not n) for n in tables):
        raise ValueError("BOUNDED_COMPLETE_NONNESTED_ROSTER_TABLES_REQUIRED")
    _roster_table_scope(page, tables, day)
    records, header = [], None
    for ti, table in enumerate(tables):
        rows = [n for n in page.nodes if n.tag == "tr" and _within(n, table)]
        if len(rows) > 3000:
            raise ValueError("ROSTER_ROW_BOUND_EXCEEDED")
        for ri, row in enumerate(rows):
            cells = [n for n in row.children if n.tag == "td"]
            values = [n.text() for n in cells]
            if not cells or not any(values):
                continue
            if any(n.hidden or not n.closed for n in cells) or not row.closed or row.hidden:
                raise ValueError("COMPLETE_VISIBLE_ROSTER_ROW_REQUIRED")
            if values[:2] == ["Company", "Where Incorporated or Organized"]:
                if len(values) not in {2, 3} or len(values) == 3 and values[2] != "Direct or Indirect":
                    raise ValueError("SUPPORTED_EXACT_ROSTER_COLUMN_SCHEMA_REQUIRED")
                if header is not None and header != values:
                    raise ValueError("COMPETING_ROSTER_COLUMN_SCHEMA")
                header = values
                continue
            if len(values) == 1 and ("SUBSIDIARIES OF THE COMPANY" in values[0] or "list of subsidiaries of the Company as of" in values[0]):
                continue
            if header is None or len(values) != len(header) or any(not value.strip() or len(value) > 500 for value in values):
                raise ValueError("ROSTER_ENTITY_JURISDICTION_SCOPE_AMBIGUOUS")
            directness = values[2] if len(values) == 3 else None
            if directness is not None and directness not in {"Direct", "Indirect"}:
                raise ValueError("EXPLICIT_PER_ENTITY_DIRECTNESS_UNSUPPORTED")
            records.append({"table_index": ti, "row_index": ri, "legal_entity_name_as_displayed": values[0],
                "jurisdiction_as_displayed": values[1], "jurisdiction_column_label": header[1], "country_or_ISO_code_inferred": False,
                "issuer_group_name_as_displayed": scope["issuer_name"], "relationship": "LISTED_SUBSIDIARY_OF_REPORTING_ISSUER_GROUP",
                "roster_as_of": day.isoformat(), "directness_as_stated": directness,
                "direct_legal_parent_as_stated": scope["issuer_name"] if directness == "Direct" else None,
                "roster_date_is_ownership_start": False, "continuous_or_current_control_verified": False,
                "product_application_or_IP_owner_verified": False})
    if not records or len(records) > 10000:
        raise ValueError("BOUNDED_ROSTER_ENTITY_ROWS_REQUIRED")
    names = [row["legal_entity_name_as_displayed"] for row in records]
    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
    for row in records:
        row["legal_entity_name_ambiguous_in_roster"] = row["legal_entity_name_as_displayed"] in duplicates
    return records, {"roster_as_of": day.isoformat(), "relationship_source_title": "SUBSIDIARIES OF THE COMPANY",
        "roster_omits_some_subsidiaries": "omitting some subsidiaries" in text,
        "absence_proves_not_a_subsidiary": False, "table_count": len(tables), "entity_row_count": len(records),
        "duplicate_legal_entity_names": duplicates, "table_scope": "EXACT_ROSTER_ONLY_DOCUMENT_AND_CONTIGUOUS_TABLES"}


def _complete_review(result, started):
    completed = _clock(_physical_clock())
    if completed < started:
        raise ValueError("REVIEW_COMPLETION_CLOCK_PRECEDES_START")
    for label in ("applicant_source", "company_source"):
        for field in ("reviewed_at", "ingested_at", "known_at"):
            child_clock = _clock(result[label].get(field))
            if completed < child_clock:
                raise ValueError("REVIEW_COMPLETION_PRECEDES_SUPPORTING_CHILD_AVAILABILITY")
    # These are newly derived facts, available only when the entire review ends.
    # Never replace an impossible physical clock with a future child's clock.
    result.update(review_completed_at=completed.isoformat(), reviewed_at=completed.isoformat(),
                  ingested_at=completed.isoformat(), known_at=completed.isoformat())
    return result


def review_fda_legal_applicant_bridge(request, captures, *, reviewed_at=None):
    """Review originals; source cutoff is distinct from completion availability."""
    physical = _clock(_physical_clock())
    if reviewed_at is not None and not isinstance(reviewed_at, datetime):
        raise ValueError("AWARE_ORIGINAL_READ_CUTOFF_REQUIRED")
    cutoff = physical if reviewed_at is None else _clock(reviewed_at)
    if cutoff > physical:
        raise ValueError("FUTURE_REVIEW_CUTOFF_UNAVAILABLE")
    try:
        encoded_request = _encoded(request)
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise ValueError("EXACT_BOUNDED_LEGAL_APPLICANT_REVIEW_REQUEST_REQUIRED") from error
    if (not isinstance(request, dict) or set(request) != {"scope", "filing_key", "exhibit_key", "roster_as_of", "selected_rows", "application_request", "company_request"}
            or len(encoded_request) > 32000 or _has_sensitive_body(encoded_request)):
        raise ValueError("EXACT_BOUNDED_LEGAL_APPLICANT_REVIEW_REQUEST_REQUIRED")
    scope = request["scope"]
    if (not isinstance(scope, dict) or set(scope) != {"issuer_cik", "issuer_ticker", "issuer_name", "application_type", "application_number", "applicant_name"}
            or not isinstance(scope["issuer_cik"], str) or not re.fullmatch(r"[0-9]{10}", scope["issuer_cik"]) or int(scope["issuer_cik"]) == 0
            or not isinstance(scope["issuer_ticker"], str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,12}", scope["issuer_ticker"])
            or scope["application_type"] != "BLA" or not isinstance(scope["application_number"], str) or not re.fullmatch(r"[0-9]{6}", scope["application_number"])):
        raise ValueError("EXACT_LEGAL_APPLICANT_ISSUER_APPLICATION_SCOPE_REQUIRED")
    _literal(scope["issuer_name"]); _literal(scope["applicant_name"])
    day = _day(request["roster_as_of"])
    selected_rows = request["selected_rows"]
    if not isinstance(selected_rows, list) or len(selected_rows) > 8:
        raise ValueError("BOUNDED_EXACT_ROSTER_ROW_SELECTION_REQUIRED")
    for row in selected_rows:
        if (not isinstance(row, dict) or set(row) != {"table_index", "row_index", "entity_name", "jurisdiction"}
                or type(row["table_index"]) is not int or not 0 <= row["table_index"] < 64
                or type(row["row_index"]) is not int or not 0 <= row["row_index"] < 3000):
            raise ValueError("EXACT_ROSTER_ROW_LOCATOR_REQUIRED")
        _literal(row["entity_name"]); _literal(row["jurisdiction"])
    app_request, company_request = request["application_request"], request["company_request"]
    exact_scope = {key: scope[key] for key in ("application_type", "application_number", "issuer_cik", "issuer_ticker")}
    if (not isinstance(app_request, dict) or not isinstance(company_request, dict)
            or app_request.get("scope") != exact_scope or company_request.get("scope") != exact_scope
            or app_request.get("source_kind") != "PURPLE_BOOK_PRODUCT_DETAIL" or company_request.get("source_kind") != "DRUGSFDA_APPLICATION_PAGE"):
        raise ValueError("EXACT_SEPARATE_APPLICANT_AND_COMPANY_REQUESTS_REQUIRED")
    keys = (request["filing_key"], request["exhibit_key"], app_request.get("source_key"), company_request.get("source_key"), app_request.get("issuer_key"))
    if (any(not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", key) for key in keys)
            or len(set(keys)) != 5 or company_request.get("issuer_key") != app_request.get("issuer_key")
            or not isinstance(captures, Mapping) or set(captures) != set(keys)):
        raise ValueError("FIVE_EXPLICIT_ORIGINAL_SOURCE_KEYS_REQUIRED")
    result = {"schema_version": VERSION, "status": "PARTIAL", "decision": "WITHHOLD_CURRENT_APPLICANT_ISSUER_BRIDGE",
        "review_started_at": physical.isoformat(),
        "source_read_cutoff": cutoff.isoformat(), "source_captures": {}, "gaps": [], "roster_facts": None,
        "selected_roster_facts": [], "exact_applicant_roster_rows": [], "exact_applicant_name_match_supported": False,
        "current_applicant_issuer_control_verified": False, "direct_legal_parent_verified": False,
        "product_owner_verified": False, "approval_date_is_ownership_start": False, "continuous_control_inferred": False,
        "similar_name_is_approved_alias": False, "issuer_attribution_allowed": False,
        "registry_adoptions": 0, "new_verified_identities": 0, "current_q3_verified_coverage": 0,
        "source_HTTP_requests": 0, "DB_or_quota_modified": False, "score_or_model_changed": False, "trading_allowed": False}
    for label, source_request in (("applicant_source", app_request), ("company_source", company_request)):
        selected = {key: captures[key] for key in (source_request["source_key"], source_request["issuer_key"])}
        result[label] = review_fda_application_product(source_request, selected, reviewed_at=cutoff)
    applicant = result["applicant_source"]["application_facts"]
    if not applicant or applicant["source_party_name"] != scope["applicant_name"] or applicant["source_party_role"] != "BLA_APPLICANT":
        result["gaps"].append("EXACT_ORIGINAL_LEGAL_APPLICANT_OBJECT_UNAVAILABLE")
    # Preserve the original incomplete container and its gaps; never replace it
    # with a healed document or a company-name/product association.
    result["gaps"].extend(result["applicant_source"]["gaps"])
    bodies = {}
    for key in (request["filing_key"], request["exhibit_key"]):
        try:
            bodies[key], result["source_captures"][key] = _sec_source(captures[key], scope, cutoff)
        except (ValueError, TypeError, UnicodeError, RecursionError) as error:
            result["gaps"].append(str(error) if isinstance(error, ValueError) else "INVALID_BOUNDED_ORIGINAL_SEC_SOURCE")
    if len(bodies) != 2:
        return _complete_review(result, physical)
    try:
        filing_pin, exhibit_pin = (result["source_captures"][key] for key in (request["filing_key"], request["exhibit_key"]))
        if (filing_pin["accession_digits"] != exhibit_pin["accession_digits"] or filing_pin["source_url"] == exhibit_pin["source_url"]
                or filing_pin["body_sha256"] == exhibit_pin["body_sha256"]):
            raise ValueError("INDEPENDENT_SAME_ACCESSION_FILING_EXHIBIT_PAIR_REQUIRED")
        _wrapper(bodies[request["filing_key"]], filing_pin, "10-K")
        _wrapper(bodies[request["exhibit_key"]], exhibit_pin, "EX-21")
        filing = _Page(bodies[request["filing_key"]])
        linked = [n for n in filing.nodes if n.tag == "a" and n.text() in {"21", "21.1"}
                  and urljoin(filing_pin["source_url"], n.attrs.get("href", "")) == exhibit_pin["source_url"] and not n.hidden and n.closed]
        if len(linked) != 1:
            raise ValueError("EXACT_EXHIBIT21_LABEL_AND_ORIGINAL_LINK_REQUIRED")
        result["issuer_group_cover"] = _cover(filing, scope, day)
        if day > _clock(exhibit_pin["observed_at"]).date() or day > _clock(filing_pin["observed_at"]).date():
            raise ValueError("ROSTER_FISCAL_POINT_FOLLOWS_ORIGINAL_CAPTURE")
        rows, roster = _roster(_Page(bodies[request["exhibit_key"]]), scope, day)
        result["roster_facts"] = roster
        for selected in selected_rows:
            matches = [row for row in rows if (row["table_index"], row["row_index"], row["legal_entity_name_as_displayed"], row["jurisdiction_as_displayed"])
                       == (selected["table_index"], selected["row_index"], selected["entity_name"], selected["jurisdiction"])]
            if len(matches) != 1:
                raise ValueError("SELECTED_EXACT_ROSTER_ENTITY_JURISDICTION_ROW_MISSING")
            result["selected_roster_facts"].extend(matches)
            if matches[0]["legal_entity_name_ambiguous_in_roster"]:
                result["gaps"].append("SELECTED_LEGAL_ENTITY_NAME_DUPLICATED_IN_ROSTER")
        exact = [row for row in rows if row["legal_entity_name_as_displayed"] == scope["applicant_name"]]
        result.update(exact_applicant_roster_rows=exact, exact_applicant_name_match_supported=len(exact) == 1)
        if not exact:
            result["gaps"].append("EXACT_CURRENT_APPLICANT_LEGAL_NAME_NOT_IN_POINT_ROSTER")
        elif len(exact) > 1:
            result["gaps"].append("EXACT_APPLICANT_LEGAL_ENTITY_ROSTER_ROWS_AMBIGUOUS")
        application_pin = result["applicant_source"]["source_captures"].get(app_request["source_key"])
        if application_pin:
            if day.isoformat() != _clock(application_pin["observed_at"]).date().isoformat():
                result["gaps"].append("OLD_ROSTER_POINT_DOES_NOT_PROVE_CURRENT_APPLICANT_CONTROL")
            if any(_clock(pin["known_at"]) >= _clock(application_pin["requested_at"]) for pin in (filing_pin, exhibit_pin)):
                result["gaps"].append("LEGAL_GROUP_EVIDENCE_NOT_KNOWN_BEFORE_ORIGINAL_APPLICANT_OBJECT")
        result["gaps"].extend(["SUBSIDIARY_GROUP_ROSTER_IS_NOT_CONTINUOUS_OWNERSHIP_OR_PRODUCT_ASSIGNMENT",
                                "DIRECT_LEGAL_PARENT_AND_CURRENT_CONTROL_REQUIRE_INDEPENDENT_DATED_EVIDENCE"])
    except (ValueError, UnicodeError, RecursionError) as error:
        result["gaps"].append(str(error) if isinstance(error, ValueError) else "BOUNDED_ORIGINAL_ROSTER_PARSE_REQUIRED")
    return _complete_review(result, physical)
