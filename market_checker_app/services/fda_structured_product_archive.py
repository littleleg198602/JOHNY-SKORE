"""Pure bounded import of original Drugs@FDA application/product snapshots.

The structured sponsor_name is the application applicant/sponsor role. Neither
it nor product/ingredient fields establish a legal owner, parent, manufacturer,
IP right or historical role interval. Text mentions are never a substitute.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import re
from urllib.parse import parse_qsl, urlsplit

from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body


ADAPTER_VERSION = "fda-original-structured-application-product-v1"
MAX_BODY_BYTES = 4_000_000
MAX_METADATA_BYTES = 64_000
MAX_PRODUCTS = 1000
MAX_INGREDIENTS = 50
MAX_JSON_DEPTH = 64
FDA_ENDPOINT = "https://api.fda.gov/drug/drugsfda.json"


@dataclass(frozen=True, slots=True)
class FdaProductArchiveCapture:
    metadata: Mapping
    body: bytes | None


@dataclass(frozen=True, slots=True)
class FdaStructuredProductSelection:
    application_type: str
    application_number: str
    sponsor_name: str
    issuer_ticker: str
    issuer_cik: str
    product_number: str | None = None
    dosage_form: str | None = None
    active_ingredients: tuple[tuple[str, str], ...] = ()
    source_version: str | None = None

    def __post_init__(self):
        if isinstance(self.active_ingredients, (list, tuple)):
            object.__setattr__(self, "active_ingredients", tuple(
                tuple(value) if isinstance(value, (tuple, list)) else value for value in self.active_ingredients))

    @property
    def application_key(self):
        return self.application_type + self.application_number

    def validate(self):
        if (not isinstance(self.application_type, str) or self.application_type not in {"NDA", "BLA"}
                or not isinstance(self.application_number, str)
                or not re.fullmatch(r"[0-9]{6}", self.application_number)
                or not isinstance(self.issuer_ticker, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,12}", self.issuer_ticker)
                or not isinstance(self.issuer_cik, str) or not re.fullmatch(r"[0-9]{10}", self.issuer_cik)
                or int(self.issuer_cik) == 0
                or (self.product_number is not None and (not isinstance(self.product_number, str)
                    or not re.fullmatch(r"[0-9]{3}", self.product_number)))):
            raise ValueError("Select exact NDA/BLA, six-digit application, product number and issuer identifiers")
        _text(self.sponsor_name)
        if self.dosage_form is not None:
            _text(self.dosage_form)
        if (not isinstance(self.active_ingredients, tuple) or len(self.active_ingredients) > MAX_INGREDIENTS
                or any(not isinstance(pair, tuple) or len(pair) != 2 for pair in self.active_ingredients)):
            raise ValueError("Select bounded exact ingredient/strength pairs")
        for name, strength in self.active_ingredients:
            _text(name); _text(strength)
        if len({pair[0] for pair in self.active_ingredients}) != len(self.active_ingredients):
            raise ValueError("Select each exact active ingredient once")
        if self.source_version is not None:
            _day(self.source_version)
        if _has_sensitive_body(json.dumps({"sponsor": self.sponsor_name, "dosage_form": self.dosage_form,
                                          "active_ingredients": self.active_ingredients}, ensure_ascii=False).encode()):
            raise ValueError("Whole unsafe product selection is withheld")


class _Rejected(ValueError):
    pass


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise _Rejected("EXACT_BOUNDED_STRUCTURED_TEXT_REQUIRED")
    return value


def _day(value):
    try:
        result = date.fromisoformat(value)
        if result.isoformat() != value:
            raise ValueError("Noncanonical day")
    except (ValueError, TypeError) as error:
        raise _Rejected("EXACT_PUBLISHER_VERSION_DAY_REQUIRED") from error
    return result


def _clock(value):
    try:
        result = datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError as error:
        raise _Rejected("AWARE_CAPTURE_KNOWLEDGE_CLOCK_REQUIRED") from error
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise _Rejected("AWARE_CAPTURE_KNOWLEDGE_CLOCK_REQUIRED")
    return result.astimezone(timezone.utc)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise _Rejected("DUPLICATE_ORIGINAL_JSON_FIELD")
        result[key] = value
    return result


def _constant(value):
    raise _Rejected("NONFINITE_ORIGINAL_JSON_VALUE")


def _float(value):
    number = float(value)
    if not math.isfinite(number):
        raise _Rejected("NONFINITE_ORIGINAL_JSON_VALUE")
    return number


def _capture(value, *, label, clock):
    if value is None:
        raise _Rejected(f"{label}_ORIGINAL_CAPTURE_MISSING")
    if not isinstance(value, FdaProductArchiveCapture) or not isinstance(value.metadata, Mapping):
        raise _Rejected(f"{label}_CAPTURE_DESCRIPTOR_INVALID")
    metadata = value.metadata
    if (value.body is None or metadata.get("raw_publication_permitted") is False
            or metadata.get("public_raw_available") is False
            or metadata.get("retention") in ("MISSING_BODY", "WITHHELD_SENSITIVE_BODY")
            or any(str(metadata.get(key, "")).startswith("WITHHELD") for key in
                   ("status", "publication_status", "archive_status"))):
        raise _Rejected(f"{label}_WHOLE_BODY_UNAVAILABLE")
    try:
        encoded = json.dumps(dict(metadata), ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise _Rejected(f"{label}_METADATA_INVALID") from error
    if len(encoded) > MAX_METADATA_BYTES or _has_sensitive_body(encoded):
        raise _Rejected(f"{label}_UNSAFE_OR_UNBOUNDED_WHOLE_METADATA")
    if (metadata.get("capture_form") != "ORIGINAL_HTTP_RESPONSE"
            and metadata.get("acquisition_method") != "urllib.request raw HTTP body"):
        raise _Rejected(f"{label}_ORIGINAL_UNPROJECTED_CAPTURE_REQUIRED")
    body = value.body
    if (type(metadata.get("http_status")) is not int or metadata["http_status"] != 200
            or metadata.get("body_complete_within_byte_budget") is not True):
        raise _Rejected(f"{label}_COMPLETE_HTTP200_REQUIRED")
    if (not isinstance(body, bytes) or not 0 < len(body) <= MAX_BODY_BYTES
            or type(metadata.get("body_bytes")) is not int or len(body) != metadata["body_bytes"]
            or type(metadata.get("byte_budget")) is not int
            or not len(body) <= metadata["byte_budget"] <= MAX_BODY_BYTES
            or not isinstance(metadata.get("body_sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", metadata["body_sha256"])
            or hashlib.sha256(body).hexdigest() != metadata["body_sha256"]):
        raise _Rejected(f"{label}_ORIGINAL_HASH_LENGTH_OR_BOUND_MISMATCH")
    if _has_sensitive_body(body):
        raise _Rejected(f"{label}_UNSAFE_WHOLE_BODY_WITHHELD")
    url = metadata.get("request_url")
    if (not isinstance(url, str) or len(url) > 2048 or url != metadata.get("response_url")):
        raise _Rejected(f"{label}_ORIGINAL_SOURCE_URL_CONFLICT")
    try:
        parsed = urlsplit(url)
        if (any(ord(char) < 33 or ord(char) == 127 for char in url)
                or parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment
                or parsed.port not in {None, 443} or parsed.hostname not in {"api.fda.gov", "data.sec.gov", "www.sec.gov"}):
            raise ValueError("Unsafe official reference")
        query_items = parse_qsl(parsed.query, keep_blank_values=True)
        query = dict(query_items)
        if (len(query_items) != len(query) or set(query) - {"search", "limit", "skip"}
                or any(len(v) > 500 or "\n" in v or "\r" in v for v in query.values())):
            raise ValueError("Unsupported source query")
    except ValueError as error:
        raise _Rejected(f"{label}_SAFE_EXPLICIT_SOURCE_REFERENCE_REQUIRED") from error
    requested, observed = _clock(metadata.get("requested_at")), _clock(metadata.get("observed_at"))
    available = _clock(metadata.get("known_at", metadata.get("observed_at")))
    if not requested <= observed <= available <= clock:
        raise _Rejected(f"{label}_CAPTURE_OR_KNOWLEDGE_CLOCK_CONFLICT")
    return body, {"source_url": url, "body_sha256": metadata["body_sha256"], "body_bytes": len(body),
                  "requested_at": requested.isoformat(), "observed_at": observed.isoformat(),
                  "original_available_at": available.isoformat()}


def _json(body):
    # Reject deeply nested original structures before the JSON decoder. Quoted
    # brackets and escaped quotes are string data, not container boundaries.
    depth, quoted, escaped = 0, False, False
    for byte in body:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise _Rejected("ORIGINAL_STRUCTURED_JSON_NESTING_EXCEEDED")
        elif byte in (93, 125):
            depth -= 1
    try:
        payload = json.loads(body.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_float)
    except _Rejected:
        raise
    except (ValueError, UnicodeError, RecursionError) as error:
        raise _Rejected("ORIGINAL_STRUCTURED_JSON_INVALID") from error
    try:
        if _has_sensitive_body(json.dumps(payload, ensure_ascii=False).encode()):
            raise _Rejected("UNSAFE_DECODED_WHOLE_BODY_WITHHELD")
    except (UnicodeError, RecursionError) as error:
        raise _Rejected("ORIGINAL_STRUCTURED_JSON_INVALID") from error
    if not isinstance(payload, dict):
        raise _Rejected("ORIGINAL_STRUCTURED_OBJECT_REQUIRED")
    return payload


def _structured(body, pin, selection):
    parsed = urlsplit(pin["source_url"])
    if parsed._replace(query="").geturl() != FDA_ENDPOINT:
        raise _Rejected("RETAINED_SOURCE_IS_NOT_STRUCTURED_DRUGSFDA_DATASET")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if (set(query) - {"search", "limit", "skip"} or not {"search", "limit"} <= set(query)
            or query["search"] != f'application_number:"{selection.application_key}"'
            or query.get("skip", "0") != "0" or not re.fullmatch(r"[1-9][0-9]{0,2}", query["limit"])
            or not 1 <= int(query["limit"]) <= 100):
        raise _Rejected("EXACT_BOUNDED_APPLICATION_QUERY_REQUIRED")
    payload = _json(body)
    if (not isinstance(payload.get("meta"), dict) or not isinstance(payload.get("results"), list)
            or any(not isinstance(row, dict) for row in payload["results"])):
        raise _Rejected("ORIGINAL_DRUGSFDA_SCHEMA_REQUIRED")
    meta, rows = payload["meta"], payload["results"]
    version, totals = meta.get("last_updated"), meta.get("results")
    if _day(version) > _clock(pin["observed_at"]).date():
        raise _Rejected("PUBLISHER_VERSION_AFTER_ORIGINAL_OBSERVATION")
    if (not isinstance(totals, dict) or any(type(totals.get(key)) is not int for key in ("skip", "limit", "total"))
            or totals["skip"] != 0 or totals["limit"] != int(query["limit"])
            or not 0 <= len(rows) <= totals["limit"] or totals["total"] < len(rows)):
        raise _Rejected("PUBLISHER_QUERY_TOTAL_OR_LIMIT_CONFLICT")
    if not rows:
        raise _Rejected("NO_STRUCTURED_APPLICATION_PRODUCT_ROWS")
    if any(row.get("application_number") != selection.application_key for row in rows):
        raise _Rejected("APPLICATION_TYPE_NUMBER_OR_QUERY_SCOPE_CONFLICT")
    if len(rows) != 1:
        raise _Rejected("DUPLICATE_APPLICATION_ROWS_AMBIGUOUS")
    application = rows[0]
    sponsor = _text(application.get("sponsor_name"))
    products = application.get("products")
    if not isinstance(products, list) or not 1 <= len(products) <= MAX_PRODUCTS:
        raise _Rejected("ORIGINAL_STRUCTURED_PRODUCT_ROWS_MISSING_OR_UNBOUNDED")
    imported, product_numbers = [], set()
    for index, product in enumerate(products):
        if not isinstance(product, dict):
            raise _Rejected("MALFORMED_STRUCTURED_PRODUCT_ROW")
        number = product.get("product_number")
        if not isinstance(number, str) or not re.fullmatch(r"[0-9]{3}", number):
            raise _Rejected("EXACT_PRODUCT_NUMBER_NOT_CAPTURED")
        if number in product_numbers:
            raise _Rejected("DUPLICATE_PRODUCT_NUMBER_AMBIGUOUS")
        product_numbers.add(number)
        form = _text(product.get("dosage_form"))
        ingredients = product.get("active_ingredients")
        if not isinstance(ingredients, list) or not 1 <= len(ingredients) <= MAX_INGREDIENTS:
            raise _Rejected("STRUCTURED_INGREDIENT_STRENGTH_ROWS_REQUIRED")
        pairs, names = [], set()
        for ingredient in ingredients:
            if not isinstance(ingredient, dict):
                raise _Rejected("STRUCTURED_INGREDIENT_STRENGTH_ROWS_REQUIRED")
            name, strength = _text(ingredient.get("name")), _text(ingredient.get("strength"))
            if name in names:
                raise _Rejected("DUPLICATE_ACTIVE_INGREDIENT_AMBIGUOUS")
            names.add(name); pairs.append((name, strength))
        imported.append({"application_type": selection.application_type,
                         "application_number": selection.application_number,
                         "source_application_key": application["application_number"],
                         "product_number": number, "dosage_form": form,
                         "active_ingredients": [{"name": name, "strength": strength} for name, strength in pairs],
                         "source_sponsor_name": sponsor, "source_role": "APPLICATION_SPONSOR_APPLICANT",
                         "source_locator": f"/results/0/products/{index}",
                         "product_row_sha256": hashlib.sha256(json.dumps(product, sort_keys=True, ensure_ascii=False,
                                                                          separators=(",", ":")).encode()).hexdigest(),
                         "publisher_version": version, "original_body_sha256": pin["body_sha256"],
                         "source_observed_at": pin["observed_at"],
                         "legal_owner_verified": False, "corporate_parent_verified": False,
                         "manufacturer_annotation_used_as_applicant": False, "product_from_text_mention": False})
    return imported, {"publisher_version": version, "version_clock_is_first_availability": False,
                      "publisher_version_id_available": False, "exact_original_body_version_sha256": pin["body_sha256"],
                      "returned_application_rows": len(rows), "query_total": totals["total"],
                      "exact_query_complete": totals["total"] == len(rows) < totals["limit"]}


def review_fda_structured_product_archive(*, selection: FdaStructuredProductSelection,
                                         structured_capture: FdaProductArchiveCapture | None,
                                         issuer_capture: FdaProductArchiveCapture | None,
                                         reviewed_at: datetime) -> dict:
    """Import exact source roles, with a strictly earlier direct named issuer.

    Missing structured evidence is WAIT_DATA. A legal-owner/parent bridge is
    never inferred from FDA applicant/manufacturer fields or a brand mention.
    A later issuer snapshot cannot retrospectively authenticate an old source.
    """
    if not isinstance(selection, FdaStructuredProductSelection):
        raise ValueError("Explicit immutable FDA product selection required")
    selection.validate()
    if not isinstance(reviewed_at, datetime):
        raise ValueError("Review ingestion requires an actual aware datetime")
    clock = _clock(reviewed_at)
    result = {"schema_version": ADAPTER_VERSION, "status": "PARTIAL", "data_status": "WAIT_DATA",
              "reviewed_at": clock.isoformat(), "ingested_at": clock.isoformat(), "known_at": clock.isoformat(),
              "structured_rows": [], "source_version": None, "source_captures": {}, "reasons": [],
              "applicant_product_candidate": None, "exact_applicant_issuer_bridge_supported": False,
              "issuer_attribution_allowed": False, "legal_owner_verified": False, "corporate_parent_verified": False,
              "application_applicant_is_legal_owner": False, "manufacturer_annotation_is_applicant": False,
              "product_from_text_mention": False, "historical_role_or_knowledge_reconstructed": False,
              "issuer_wide_scope": False, "negative_issuer_completeness_verified": False,
              "live_acceptance_verified": False, "registry_modified": False, "quota_modified": False,
              "http_requests": 0, "score_changes": False, "prediction_input": False}
    bodies = {}
    for label, source in (("STRUCTURED", structured_capture), ("ISSUER", issuer_capture)):
        try:
            body, pin = _capture(source, label=label, clock=clock)
            bodies[label] = body
            result["source_captures"][label.lower()] = pin
        except _Rejected as error:
            result["reasons"].append(str(error))
    if "STRUCTURED" in bodies:
        try:
            rows, version = _structured(bodies["STRUCTURED"], result["source_captures"]["structured"], selection)
            result.update(structured_rows=rows, source_version=version)
        except _Rejected as error:
            result["reasons"].append(str(error))
    if result["reasons"]:
        return result
    try:
        pins, rows, version = result["source_captures"], result["structured_rows"], result["source_version"]
        if not version["exact_query_complete"]:
            raise _Rejected("APPLICATION_QUERY_TRAVERSAL_NOT_PROVEN_COMPLETE")
        if (selection.product_number is None or selection.dosage_form is None or not selection.active_ingredients
                or selection.source_version is None):
            raise _Rejected("EXACT_PRODUCT_FORM_STRENGTH_VERSION_SELECTION_MISSING")
        if selection.source_version != version["publisher_version"]:
            raise _Rejected("SELECTED_SOURCE_VERSION_CONFLICT")
        matches = [row for row in rows if row["product_number"] == selection.product_number]
        if len(matches) != 1:
            raise _Rejected("SELECTED_PRODUCT_NUMBER_NOT_IN_ORIGINAL_APPLICATION")
        row = matches[0]
        pairs = tuple((item["name"], item["strength"]) for item in row["active_ingredients"])
        if (row["dosage_form"] != selection.dosage_form or pairs != selection.active_ingredients
                or row["source_sponsor_name"] != selection.sponsor_name):
            raise _Rejected("EXACT_APPLICANT_FORM_OR_INGREDIENT_STRENGTH_CONFLICT")
        official = f"https://data.sec.gov/submissions/CIK{selection.issuer_cik}.json"
        if pins["issuer"]["source_url"] != official:
            raise _Rejected("INDEPENDENT_DIRECT_NAMED_ISSUER_SNAPSHOT_REQUIRED")
        issuer = _json(bodies["ISSUER"])
        if (type(issuer.get("cik")) not in {str, int} or not re.fullmatch(r"[0-9]{1,10}", str(issuer["cik"]))
                or str(issuer["cik"]).zfill(10) != selection.issuer_cik
                or not isinstance(issuer.get("tickers"), list) or not 1 <= len(issuer["tickers"]) <= 100
                or any(not isinstance(ticker, str) for ticker in issuer["tickers"])
                or issuer["tickers"].count(selection.issuer_ticker) != 1
                or _text(issuer.get("name")).strip().casefold() != selection.sponsor_name.strip().casefold()):
            raise _Rejected("EXACT_APPLICANT_LEGAL_ISSUER_NAME_CIK_TICKER_BRIDGE_MISSING")
        source_clock, issuer_clock = _clock(pins["structured"]["observed_at"]), _clock(pins["issuer"]["observed_at"])
        if (issuer_clock.date() != source_clock.date()
                or _clock(pins["issuer"]["original_available_at"]) >= _clock(pins["structured"]["requested_at"])):
            raise _Rejected("ISSUER_BRIDGE_NOT_ACTUALLY_KNOWN_BEFORE_ORIGINAL_FDA_SOURCE")
        digest = hashlib.sha256(json.dumps({"row": row, "sources": pins}, sort_keys=True,
                                           separators=(",", ":")).encode()).hexdigest()
        result.update(status="REVIEWABLE_APPLICANT_PRODUCT_SNAPSHOT", data_status="EXACT_ORIGINAL_SNAPSHOT",
                      exact_applicant_issuer_bridge_supported=True,
                      applicant_product_candidate={"record_id": f"fda-structured-product-{digest[:24]}",
                          "role": "APPLICATION_SPONSOR_APPLICANT", "structured_product": row,
                          "ticker": selection.issuer_ticker, "issuer_cik": selection.issuer_cik,
                          "time_basis": "OBSERVED_SNAPSHOT", "source_observed_at": source_clock.isoformat(),
                          "effective_from": source_clock.date().isoformat(),
                          "effective_to": (source_clock.date() + timedelta(days=1)).isoformat(),
                          "known_at": clock.isoformat(), "ingested_at": clock.isoformat(), "source_captures": pins,
                          "adopted": False, "legal_owner_verified": False, "corporate_parent_verified": False,
                          "historical_prediction_knowledge_available": False})
    except _Rejected as error:
        result["reasons"].append(str(error))
    return result
