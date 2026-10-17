"""Original static FDA application/product facts, with distinct source roles.

Drugs@FDA's displayed Company is not renamed Applicant or legal owner. Purple
Book Applicant is a different role/dataset. Neither supplies parent ownership,
clinical sponsorship, historical availability or an approval-holder interval.
"""
from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import re
from urllib.parse import parse_qsl, urlsplit

from market_checker_app.services.fda_structured_product_archive import _clock, _json as _strict_json
from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body

VERSION = "fda-original-static-application-product-v1"
KINDS = {"DRUGSFDA_APPLICATION_PAGE", "PURPLE_BOOK_PRODUCT_DETAIL"}
MAX_BODY = 4_000_000
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
FDA_HEADERS = ("Drug Name", "Active Ingredients", "Strength", "Dosage Form/Route", "Marketing Status", "TE Code", "RLD", "RS")
PURPLE_HEADERS = ("Product Number", "Dosage Form", "Route of Administration", "Strength", "Product Presentation", "License Type", "Proprietary Name", "Marketing Status")
SELECTED_FIELDS = {"source_row_index", "product_number", "proprietary_name", "proper_name", "strength",
                   "dosage_form", "route", "dosage_form_route_text", "source_party_name"}


def _physical_clock():
    return datetime.now(timezone.utc)


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()


def _text(value, maximum=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("BOUNDED_EXACT_SOURCE_LITERAL_REQUIRED")
    return value


def _source_url(url, kind, scope):
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 or ord(c) == 127 for c in url):
        raise ValueError("SAFE_EXACT_OFFICIAL_SOURCE_URL_REQUIRED")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment
            or parsed.port not in {None, 443} or "%" in parsed.path):
        raise ValueError("SAFE_EXACT_OFFICIAL_SOURCE_URL_REQUIRED")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = dict(pairs)
    if len(pairs) != len(query):
        raise ValueError("DUPLICATE_SOURCE_QUERY_FIELD")
    if kind == "ISSUER":
        if url != f'https://data.sec.gov/submissions/CIK{scope["issuer_cik"]}.json':
            raise ValueError("EXACT_DIRECT_SEC_ISSUER_SOURCE_REQUIRED")
    elif kind == "DRUGSFDA_APPLICATION_PAGE":
        if (parsed.netloc != "www.accessdata.fda.gov" or parsed.path != "/scripts/cder/daf/index.cfm"
                or query != {"event": "overview.process", "ApplNo": scope["application_number"]}):
            raise ValueError("EXACT_STATIC_DRUGSFDA_APPLICATION_SOURCE_REQUIRED")
    elif (parsed.netloc != "purplebooksearch.fda.gov" or parsed.path != "/index.cfm"
            or scope["application_type"] != "BLA"
            or query != {"blaNo": scope["application_number"], "event": "productdetails"}):
        raise ValueError("EXACT_STATIC_PURPLE_BOOK_BLA_SOURCE_REQUIRED")


def _capture(pair, kind, scope, cutoff):
    if pair is None or not isinstance(pair, (tuple, list)) or len(pair) != 2:
        raise ValueError("ORIGINAL_CAPTURE_DESCRIPTOR_REQUIRED")
    metadata, body = pair
    if not isinstance(metadata, Mapping):
        raise ValueError("ORIGINAL_CAPTURE_METADATA_REQUIRED")
    if (body is None or metadata.get("raw_publication_permitted") is False or metadata.get("public_raw_available") is False
            or metadata.get("retention") in ("MISSING_BODY", "WITHHELD_SENSITIVE_BODY", "NOT_CAPTURED_COMPLETE_BODY")
            or str(metadata.get("retention", "")).startswith(("WITHHELD", "MISSING", "NOT_CAPTURED"))
            or any(str(metadata.get(k, "")).startswith("WITHHELD") for k in ("status", "archive_status", "publication_status"))):
        raise ValueError("WHOLE_ORIGINAL_SOURCE_UNAVAILABLE")
    try:
        encoded = _encoded(dict(metadata))
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ValueError("BOUNDED_SAFE_WHOLE_METADATA_REQUIRED") from error
    if len(encoded) > 64000 or _has_sensitive_body(encoded):
        raise ValueError("BOUNDED_SAFE_WHOLE_METADATA_REQUIRED")
    if (metadata.get("capture_form") != "ORIGINAL_HTTP_RESPONSE"
            or metadata.get("body_transformed", False) is not False
            or type(metadata.get("http_status")) is not int or metadata["http_status"] != 200
            or metadata.get("body_complete_within_byte_budget") is not True):
        raise ValueError("COMPLETE_ORIGINAL_HTTP200_REQUIRED")
    if (not isinstance(body, bytes) or not 0 < len(body) <= MAX_BODY
            or type(metadata.get("body_bytes")) is not int or len(body) != metadata["body_bytes"]
            or type(metadata.get("byte_budget")) is not int or not len(body) <= metadata["byte_budget"] <= MAX_BODY
            or not isinstance(metadata.get("body_sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", metadata["body_sha256"])
            or hashlib.sha256(body).hexdigest() != metadata["body_sha256"]):
        raise ValueError("ORIGINAL_WHOLE_HASH_LENGTH_OR_CAP_CONFLICT")
    if _has_sensitive_body(body):
        raise ValueError("WHOLE_UNSAFE_SOURCE_WITHHELD")
    url = metadata.get("request_url")
    if url != metadata.get("response_url"):
        raise ValueError("ORIGINAL_URL_REDIRECT_OR_VERSION_CONFLICT")
    _source_url(url, kind, scope)
    requested, observed = _clock(metadata.get("requested_at")), _clock(metadata.get("observed_at"))
    known = _clock(metadata.get("known_at", metadata.get("observed_at")))
    if not requested <= observed <= known <= cutoff:
        raise ValueError("ORIGINAL_CAPTURE_OR_KNOWLEDGE_CLOCK_CONFLICT")
    headers = metadata.get("response_headers", {})
    if not isinstance(headers, Mapping) or headers.get("Content-Encoding") not in (None, "", "identity"):
        raise ValueError("ORIGINAL_UNENCODED_RESPONSE_REQUIRED")
    return body, {"source_url": url, "body_sha256": metadata["body_sha256"], "body_bytes": len(body),
                  "requested_at": requested.isoformat(), "observed_at": observed.isoformat(), "known_at": known.isoformat(),
                  "publisher_last_modified_raw": headers.get("Last-Modified"), "publisher_etag_raw": headers.get("ETag"),
                  "first_historical_availability_proven": False}


class _Node:
    def __init__(self, tag, attrs, parent, ordinal, hidden=False):
        self.tag, self.attrs, self.parent, self.ordinal = tag, attrs, parent, ordinal
        self.parts, self.children, self.closed, self.hidden = [], [], False, hidden

    def text(self):
        if self.hidden:
            return ""
        return " ".join(" ".join(part.text() if isinstance(part, _Node) else part for part in self.parts).split())

    def has_class(self, name):
        return name in (self.attrs.get("class") or "").split()


class _Page(HTMLParser):
    def __init__(self, raw):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {}, None, 0)
        self.stack, self.nodes = [self.root], []
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeError as error:
            raise ValueError("ORIGINAL_UTF8_HTML_REQUIRED") from error
        if "\x00" in text or re.search(r"<!\s*ENTITY\b", text, re.I):
            raise ValueError("UNSUPPORTED_HTML_ENCODING_OR_ENTITY")
        self.feed(text); self.close()

    def handle_starttag(self, tag, attrs):
        if len(self.nodes) >= 100000 or len(self.stack) > 128 or len(attrs) > 100:
            raise ValueError("ORIGINAL_HTML_COMPLEXITY_CAP_EXCEEDED")
        values = dict(attrs)
        if len(values) != len(attrs):
            raise ValueError("DUPLICATE_HTML_ATTRIBUTE_AMBIGUOUS")
        if any(not isinstance(value, (str, type(None))) or isinstance(value, str) and len(value) > 8000 for value in values.values()):
            raise ValueError("BOUNDED_HTML_ATTRIBUTE_REQUIRED")
        parent = self.stack[-1]
        style = re.sub(r"\s+", "", values.get("style") or "").lower()
        hidden = (parent.hidden or tag in {"script", "style", "noscript", "template", "ix:hidden"}
                  or "hidden" in values or (values.get("aria-hidden") or "").lower() == "true"
                  or "display:none" in style or "visibility:hidden" in style)
        node = _Node(tag, values, parent, len(self.nodes), hidden)
        parent.parts.append(node); parent.children.append(node); self.nodes.append(node)
        if tag in VOID:
            node.closed = True
            if tag == "br": node.parts.append(" ")
        else:
            self.stack.append(node)

    def handle_endtag(self, tag):
        found = next((n for n in range(len(self.stack) - 1, 0, -1) if self.stack[n].tag == tag), None)
        if found is not None:
            self.stack[found].closed = True
            del self.stack[found:]

    def handle_data(self, value):
        self.stack[-1].parts.append(value)

    def unique(self, predicate, *, require_closed=True):
        rows = [node for node in self.nodes if predicate(node)]
        if len(rows) != 1 or rows[0].hidden or require_closed and not rows[0].closed:
            raise ValueError("EXACT_VISIBLE_SOURCE_OBJECT_MISSING_OR_AMBIGUOUS")
        return rows[0]


def _within(node, parent):
    while node.parent is not None:
        if node.parent is parent:
            return True
        node = node.parent
    return False


def _cells(row, tag):
    cells = [node for node in row.children if node.tag == tag]
    if not row.closed or row.hidden or any(not node.closed or node.hidden for node in cells):
        raise ValueError("COMPLETE_VISIBLE_STRUCTURED_ROW_REQUIRED")
    if any(node.tag in {"table", "div"} for cell in cells for node in cell.children):
        raise ValueError("NESTED_PRODUCT_CELL_SCOPE_UNSUPPORTED")
    return [_text(node.text()) for node in cells]


def _base_row(index, number, name, proper, strength, form, route, combined, party, role):
    return {"source_row_index": index, "product_number": number, "proprietary_name": name,
            "proper_name": proper, "strength": strength, "dosage_form": form, "route": route,
            "dosage_form_route_text": combined, "source_party_name": party, "source_party_role": role,
            "ingredient_strength_pairs_inferred": False, "row_index_is_FDA_product_number": False,
            "sponsor_role_proven": False, "legal_owner_verified": False, "corporate_parent_verified": False}


def _drugs_page(page, scope):
    candidates = []
    for parent in page.nodes:
        values = [n for n in parent.children if n.has_class("appl-details-top")]
        labels = [n for n in parent.children if n.has_class("prodBoldText")]
        if len(values) == len(labels) == 2:
            candidates.append((parent, values, labels))
    if len(candidates) != 1:
        raise ValueError("EXACT_APPLICATION_COMPANY_HEADER_REQUIRED")
    parent, values, labels = candidates[0]
    expected = {"BLA": "Biologic License Application (BLA)", "NDA": "New Drug Application (NDA)"}
    if (parent.hidden or not parent.closed or any(n.hidden or not n.closed for n in values + labels)
            or labels[0].text().rstrip(" :") != expected[scope["application_type"]]
            or labels[1].text().rstrip(" :") != "Company" or values[0].text() != scope["application_number"]):
        raise ValueError("APPLICATION_TYPE_NUMBER_OR_COMPANY_LABEL_CONFLICT")
    party = _text(values[1].text())
    heading = page.unique(lambda n: n.tag == "a" and n.attrs.get("href") == "#collapseProduct")
    if heading.text() != f'Products on {scope["application_type"]} {scope["application_number"]}':
        raise ValueError("PRODUCT_PANEL_APPLICATION_SCOPE_CONFLICT")
    panel = page.unique(lambda n: n.attrs.get("id") == "collapseProduct")
    table = page.unique(lambda n: n.tag == "table" and n.attrs.get("id") == "exampleProd")
    if not _within(table, panel) or table.attrs.get("summary") != "FDA Approved Drug Products":
        raise ValueError("ORIGINAL_APPLICATION_PRODUCT_TABLE_REQUIRED")
    rows = [n for n in page.nodes if n.tag == "tr" and _within(n, table)]
    if not 2 <= len(rows) <= 1001 or tuple(_cells(rows[0], "th")) != FDA_HEADERS:
        raise ValueError("EXACT_DRUGSFDA_PRODUCT_COLUMN_SCHEMA_REQUIRED")
    products = []
    for index, row in enumerate(rows[1:]):
        values = _cells(row, "td")
        if len(values) != len(FDA_HEADERS):
            raise ValueError("COMPLETE_DRUGSFDA_PRODUCT_ROW_REQUIRED")
        product = _base_row(index, None, values[0], values[1], values[2], None, None, values[3], party, "COMPANY_AS_DISPLAYED")
        product.update(marketing_status=values[4], source_locator=f"table#exampleProd/tbody/tr:{index + 1}",
                       complete_product_number_form_route_identity=False)
        products.append(product)
    return {"application_type": scope["application_type"], "application_number": scope["application_number"],
            "source_party_name": party, "source_party_role": "COMPANY_AS_DISPLAYED", "source_label": "Company",
            "original_approval_date_as_displayed": None}, products


def _detail_field(page, details, label):
    matches = []
    for node in page.nodes:
        if node.tag == "div" and _within(node, details):
            labels = [child for child in node.children if child.tag == "strong"]
            if len(labels) == 1 and labels[0].text() == label:
                if node.hidden or not node.closed or not labels[0].closed:
                    raise ValueError("VISIBLE_EXACT_PRODUCT_DETAIL_FIELD_REQUIRED")
                after = node.parts[node.parts.index(labels[0]) + 1:]
                matches.append(" ".join(" ".join(p.text() if isinstance(p, _Node) else p for p in after).split()))
    if len(matches) != 1:
        raise ValueError("EXACT_PRODUCT_DETAIL_LABEL_MISSING_OR_AMBIGUOUS")
    return _text(matches[0])


def _purple_page(page, scope):
    # The genuine current page omits this outer div's closing tag. Recover only
    # explicitly closed labelled fields and rows within the same closed section,
    # record the defect, and keep the review partial rather than repair bytes.
    details = page.unique(lambda n: n.has_class("product-details"), require_closed=False)
    if _detail_field(page, details, "BLA Number") != scope["application_number"]:
        raise ValueError("PURPLE_BOOK_BLA_NUMBER_CONFLICT")
    party = _detail_field(page, details, "Applicant")
    proper = _detail_field(page, details, "Proper Name")
    approval = _detail_field(page, details, "Original Approval Date")
    try:
        approval_day = datetime.strptime(approval, "%B %d, %Y").date().isoformat()
    except ValueError:
        approval_day = None
    grid = page.unique(lambda n: n.has_class("product-grid"))
    if (details.parent is not grid.parent or details.parent.tag != "section"
            or not details.parent.closed or not details.parent.has_class("main-content")):
        raise ValueError("PURPLE_BOOK_GRID_AND_APPLICATION_DETAIL_SCOPE_CONFLICT")
    rows = [n for n in grid.children if n.has_class("grid-row")]
    if (not 2 <= len(rows) <= 1001 or not rows[0].has_class("grid-header")
            or tuple(_cells(rows[0], "div")) != PURPLE_HEADERS):
        raise ValueError("EXACT_PURPLE_BOOK_PRODUCT_COLUMN_SCHEMA_REQUIRED")
    products, numbers = [], set()
    for index, row in enumerate(rows[1:]):
        values = _cells(row, "div")
        if len(values) != len(PURPLE_HEADERS) or not re.fullmatch(r"[0-9]{3}", values[0]) or values[0] in numbers:
            raise ValueError("EXACT_UNIQUE_PURPLE_BOOK_PRODUCT_NUMBER_REQUIRED")
        numbers.add(values[0])
        product = _base_row(index, values[0], values[6], proper, values[3], values[1], values[2], None, party, "BLA_APPLICANT")
        product.update(product_presentation=values[4], license_type=values[5], marketing_status=values[7],
                       source_locator=f"div.product-grid/div.grid-row:{index + 1}",
                       complete_product_number_form_route_identity=True)
        products.append(product)
    return {"application_type": "BLA", "application_number": scope["application_number"],
            "source_party_name": party, "source_party_role": "BLA_APPLICANT", "source_label": "Applicant",
            "original_approval_date_as_displayed": approval, "approval_date_is_applicant_since": False,
            "original_approval_day": approval_day, "detail_container_explicitly_closed": details.closed}, products


def review_fda_application_product(request, captures, *, reviewed_at=None):
    """Pure captured-plan API; supplied reviewed_at is a source-read cutoff.

    New review availability/knowledge is always the actual physical invocation,
    never a historical cutoff. The coordinator rejects historical as_of claims.
    """
    physical = _clock(_physical_clock())
    if reviewed_at is not None and not isinstance(reviewed_at, datetime):
        raise ValueError("AWARE_SOURCE_READ_CUTOFF_REQUIRED")
    cutoff = physical if reviewed_at is None else _clock(reviewed_at)
    if cutoff > physical:
        raise ValueError("FUTURE_REVIEW_CUTOFF_UNAVAILABLE")
    required = {"scope", "source_kind", "source_key", "issuer_key", "source_version_sha256", "product_selection"}
    try:
        encoded_request = _encoded(request)
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ValueError("BOUNDED_EXPLICIT_STATIC_FDA_REVIEW_REQUEST_REQUIRED") from error
    if not isinstance(request, dict) or set(request) != required or len(encoded_request) > 16000 or _has_sensitive_body(encoded_request):
        raise ValueError("BOUNDED_EXPLICIT_STATIC_FDA_REVIEW_REQUEST_REQUIRED")
    scope = request["scope"]
    if (not isinstance(scope, dict) or set(scope) != {"application_type", "application_number", "issuer_cik", "issuer_ticker"}
            or not isinstance(scope["application_type"], str) or scope["application_type"] not in {"NDA", "BLA"}
            or not isinstance(scope["application_number"], str) or not re.fullmatch(r"[0-9]{6}", scope["application_number"])
            or not isinstance(scope["issuer_cik"], str) or not re.fullmatch(r"[0-9]{10}", scope["issuer_cik"]) or int(scope["issuer_cik"]) == 0
            or not isinstance(scope["issuer_ticker"], str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,12}", scope["issuer_ticker"])):
        raise ValueError("EXACT_APPLICATION_TYPE_NUMBER_AND_ISSUER_SCOPE_REQUIRED")
    if (not isinstance(request["source_kind"], str) or request["source_kind"] not in KINDS or any(not isinstance(request[k], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", request[k]) for k in ("source_key", "issuer_key"))
            or request["source_key"] == request["issuer_key"] or not isinstance(request["source_version_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", request["source_version_sha256"])
            or not isinstance(captures, Mapping) or set(captures) - {request["source_key"], request["issuer_key"]}):
        raise ValueError("EXACT_SOURCE_KIND_KEYS_AND_ORIGINAL_VERSION_REQUIRED")
    selected = request["product_selection"]
    if selected is not None and (not isinstance(selected, dict) or set(selected) != SELECTED_FIELDS
            or type(selected["source_row_index"]) is not int or not 0 <= selected["source_row_index"] < 1000
            or any(value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 1000)
                   for key, value in selected.items() if key != "source_row_index")):
        raise ValueError("EXACT_BOUNDED_PRODUCT_SELECTION_REQUIRED")
    report = {"schema_version": VERSION, "status": "PARTIAL", "data_status": "WAIT_DATA", "source_kind": request["source_kind"],
              "source_read_cutoff": cutoff.isoformat(), "reviewed_at": physical.isoformat(), "ingested_at": physical.isoformat(), "known_at": physical.isoformat(),
              "source_captures": {}, "application_facts": None, "product_rows": [], "gaps": [],
              "exact_named_issuer_bridge_supported": False, "applicant_product_candidate": None,
              "issuer_attribution_allowed": False, "company_label_is_applicant": False, "sponsor_role_proven": False,
              "legal_owner_verified": False, "corporate_parent_verified": False, "approval_date_is_ownership_since": False,
              "historical_machine_availability_inferred": False, "issuer_wide_scope": False,
              "registry_adoptions": 0, "new_verified_identities": 0, "current_q3_verified_coverage": 0,
              "live_acceptance_verified": False, "network_requests_performed": 0, "quota_or_DB_modified": False,
              "score_changes": False, "trading_allowed": False}
    bodies = {}
    for key, kind in ((request["source_key"], request["source_kind"]), (request["issuer_key"], "ISSUER")):
        try:
            bodies[key], report["source_captures"][key] = _capture(captures.get(key), kind, scope, cutoff)
        except ValueError as error:
            report["gaps"].append(str(error))
    source_key, issuer_key = request["source_key"], request["issuer_key"]
    if source_key not in bodies:
        return report
    source = report["source_captures"][source_key]
    if source["body_sha256"] != request["source_version_sha256"]:
        report["gaps"].append("SELECTED_ORIGINAL_SOURCE_VERSION_CONFLICT")
        return report
    try:
        page = _Page(bodies[source_key])
        app, products = (_drugs_page(page, scope) if request["source_kind"] == "DRUGSFDA_APPLICATION_PAGE" else _purple_page(page, scope))
        signatures = [tuple((k, row[k]) for k in SELECTED_FIELDS if k != "source_row_index") for row in products]
        if len(set(signatures)) != len(signatures):
            raise ValueError("DUPLICATE_STRUCTURED_PRODUCT_ROWS_AMBIGUOUS")
        report.update(application_facts=app, product_rows=products, data_status="ORIGINAL_STATIC_OBJECT_FACTS")
        if app.get("detail_container_explicitly_closed") is False:
            report["gaps"].append("PRODUCT_DETAIL_OUTER_CONTAINER_IMPLICITLY_CLOSED_SOURCE_DEFECT")
        if app["source_party_role"] == "BLA_APPLICANT":
            if app["original_approval_day"] is None:
                report["gaps"].append("ORIGINAL_APPROVAL_CALENDAR_DAY_UNVERIFIED")
            elif app["original_approval_day"] > _clock(source["observed_at"]).date().isoformat():
                report["gaps"].append("ORIGINAL_APPROVAL_AFTER_SOURCE_OBSERVATION")
        report["source_version"] = {"body_sha256": source["body_sha256"], "publisher_unique_version_id": None,
                                     "publisher_first_availability": None, "capture_date_is_historical_publication": False}
    except (ValueError, UnicodeError, RecursionError) as error:
        report["gaps"].append(str(error) if isinstance(error, ValueError) else "BOUNDED_ORIGINAL_HTML_REQUIRED")
        return report
    if issuer_key in bodies:
        try:
            issuer = _strict_json(bodies[issuer_key])
            issuer_pin = report["source_captures"][issuer_key]
            if (type(issuer.get("cik")) not in {str, int} or not re.fullmatch(r"[0-9]{1,10}", str(issuer["cik"]))
                    or str(issuer["cik"]).zfill(10) != scope["issuer_cik"] or not isinstance(issuer.get("tickers"), list)
                    or not 1 <= len(issuer["tickers"]) <= 100 or any(not isinstance(t, str) for t in issuer["tickers"])
                    or issuer["tickers"].count(scope["issuer_ticker"]) != 1
                    or _text(issuer.get("name")).strip().casefold() != app["source_party_name"].strip().casefold()):
                raise ValueError("EXACT_SOURCE_PARTY_NAME_CIK_TICKER_ISSUER_BRIDGE_MISSING")
            if (_clock(issuer_pin["observed_at"]).date() != _clock(source["observed_at"]).date()
                    or _clock(issuer_pin["known_at"]) >= _clock(source["requested_at"])):
                raise ValueError("ISSUER_BRIDGE_NOT_KNOWN_STRICTLY_BEFORE_ORIGINAL_SOURCE")
            report["exact_named_issuer_bridge_supported"] = True
        except ValueError as error:
            report["gaps"].append(str(error))
    if app["source_party_role"] == "COMPANY_AS_DISPLAYED":
        report["gaps"].extend(["COMPANY_LABEL_IS_NOT_EXPLICIT_APPLICANT_OR_SPONSOR", "PRODUCT_NUMBER_AND_SEPARATE_FORM_ROUTE_NOT_PUBLISHED"])
    if selected is None:
        report["gaps"].append("EXACT_PRODUCT_SELECTION_NOT_PROVIDED")
    else:
        matches = [row for row in products if {k: row[k] for k in SELECTED_FIELDS} == selected]
        if len(matches) != 1:
            report["gaps"].append("EXACT_PRODUCT_TUPLE_NOT_IN_SELECTED_ORIGINAL_VERSION")
        elif (report["exact_named_issuer_bridge_supported"] and app["source_party_role"] == "BLA_APPLICANT"
                and matches[0]["complete_product_number_form_route_identity"] and not report["gaps"]):
            row = matches[0]
            identity = hashlib.sha256(_encoded({"row": row, "sources": report["source_captures"]})).hexdigest()
            report.update(status="REVIEWABLE_APPLICATION_APPLICANT_PRODUCT_SNAPSHOT", data_status="EXACT_ORIGINAL_STATIC_OBJECT",
                          applicant_product_candidate={"record_id": "fda-static-product-" + identity[:24], "role": "BLA_APPLICANT",
                              "product": dict(row), "issuer_cik": scope["issuer_cik"], "ticker": scope["issuer_ticker"],
                              "source_observed_at": source["observed_at"], "known_at": physical.isoformat(),
                              "time_basis": "OBSERVED_SNAPSHOT", "adopted": False, "legal_owner_verified": False,
                              "corporate_parent_verified": False, "historical_prediction_knowledge_available": False})
    return report
