"""Read-only, original-byte review of one HCA CMS enrollment/owner scope.

An All Owners row has no CCN. The separate Hospital Enrollments response must
join by enrollment ID, provider PAC ID and exact legal business name. A brand,
DBA, owner role or percentage does not establish the ultimate corporate group.
This module makes proposals; it never changes a registry or grants attribution.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Mapping
from urllib.parse import parse_qsl, urlsplit

from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body
from market_checker_app.utils.source_validation import public_https_reference


CONTRACT_VERSION = "cms-exact-ccn-owner-original-bytes-v1"
CMS_OWNERS_URL = "https://data.cms.gov/data-api/v1/dataset/029c119f-f79c-49be-9100-344d31d10344/data"
CMS_ENROLLMENTS_URL = "https://data.cms.gov/data-api/v1/dataset/f6f6505c-e8b0-4d57-b258-e2b94133aaf2/data"
MAX_CAPTURE_BYTES = 8 * 1024 * 1024
MAX_QUERY_ROWS = 100
_ROLE_TEXT = {"34": "5% OR GREATER DIRECT OWNERSHIP INTEREST",
              "35": "5% OR GREATER INDIRECT OWNERSHIP INTEREST"}


@dataclass(frozen=True, slots=True)
class CapturedSource:
    """Original HTTP body and its retained capture metadata; no filesystem I/O."""

    metadata: Mapping
    body: bytes | None


@dataclass(frozen=True, slots=True)
class CmsProviderOwnerScope:
    enrollment_id: str
    owner_associate_id: str
    owner_role_code: str
    ticker: str = "HCA"
    issuer_cik: str = "0000860730"
    expected_ccn: str | None = None

    def validate(self) -> None:
        if (not isinstance(self.enrollment_id, str)
                or not re.fullmatch(r"O[0-9]{14}", self.enrollment_id)
                or not isinstance(self.owner_associate_id, str)
                or not re.fullmatch(r"[0-9]{10}", self.owner_associate_id)
                or not isinstance(self.owner_role_code, str) or self.owner_role_code not in _ROLE_TEXT
                or (self.ticker, self.issuer_cik) != ("HCA", "0000860730")
                or (self.expected_ccn is not None and
                    (not isinstance(self.expected_ccn, str)
                     or not re.fullmatch(r"[0-9]{6}", self.expected_ccn)))):
            raise ValueError("CMS review requires the exact supported HCA enrollment/owner scope")


class _Rejected(ValueError):
    pass


def _aware(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError as error:
        raise _Rejected("INVALID_CAPTURE_CLOCK") from error
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _Rejected("INVALID_CAPTURE_CLOCK")
    return parsed.astimezone(timezone.utc)


def _text(value: object, *, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise _Rejected(code)
    return value.strip()


def _same_name(left: object, right: object) -> bool:
    return _text(left, code="MISSING_EXACT_LEGAL_NAME").casefold() == _text(
        right, code="MISSING_EXACT_LEGAL_NAME").casefold()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _Rejected("DUPLICATE_JSON_FIELD")
        result[key] = value
    return result


def _constant(value):
    raise _Rejected("NONFINITE_JSON_VALUE")


def _original(capture: CapturedSource | None, *, label: str, clock: datetime,
              endpoint: str, cms_query: bool) -> tuple[object, dict]:
    if capture is None:
        raise _Rejected(f"{label}_ORIGINAL_CAPTURE_MISSING")
    if not isinstance(capture, CapturedSource):
        raise _Rejected(f"{label}_INVALID_CAPTURE_METADATA")
    meta = capture.metadata
    if not isinstance(meta, Mapping):
        raise _Rejected(f"{label}_INVALID_CAPTURE_METADATA")
    # An unavailable body is rejected before decoding, parsing or exporting it.
    if (meta.get("raw_publication_permitted") is not True
            or str(meta.get("status", "")).startswith("WITHHELD") or capture.body is None):
        raise _Rejected(f"{label}_BODY_WITHHELD_OR_UNAVAILABLE")
    if meta.get("capture_form") != "ORIGINAL_HTTP_RESPONSE":
        raise _Rejected(f"{label}_PROJECTED_OR_NONORIGINAL_BYTES")
    if type(meta.get("http_status")) is not int or meta["http_status"] != 200:
        raise _Rejected(f"{label}_HTTP_200_REQUIRED")
    if meta.get("complete_response_body") is not True:
        raise _Rejected(f"{label}_INCOMPLETE_BODY")
    body = capture.body
    if (not isinstance(body, bytes) or not 1 <= len(body) <= MAX_CAPTURE_BYTES
            or type(meta.get("body_bytes")) is not int or meta["body_bytes"] != len(body)
            or not isinstance(meta.get("body_sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", meta["body_sha256"])
            or hashlib.sha256(body).hexdigest() != meta["body_sha256"]):
        raise _Rejected(f"{label}_ORIGINAL_BODY_HASH_OR_SIZE_MISMATCH")
    if _has_sensitive_body(body):
        raise _Rejected(f"{label}_UNSAFE_WHOLE_BODY_WITHHELD")
    requested, observed = _aware(meta.get("requested_at")), _aware(meta.get("observed_at"))
    if not requested <= observed <= clock:
        raise _Rejected(f"{label}_CAPTURE_CLOCK_ORDER_INVALID")
    url = meta.get("response_url")
    if (not isinstance(url, str) or len(url) > 2048
            or meta.get("requested_url", url) != url):
        raise _Rejected(f"{label}_SOURCE_REFERENCE_INVALID")
    try:
        public_https_reference(url)
        parsed = urlsplit(url)
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        query = dict(pairs)
        if (parsed.port not in {None, 443} or parsed.fragment
                or parsed._replace(query="").geturl() != endpoint
                or (not cms_query and parsed.query)
                or (cms_query and (len(pairs) != len(query)
                    or set(query) != {"size", "offset", "filter[ENROLLMENT ID]"}
                    or not re.fullmatch(r"[1-9][0-9]{0,2}", query.get("size", ""))
                    or not re.fullmatch(r"[0-9]{1,4}", query.get("offset", ""))
                    or not re.fullmatch(r"O[0-9]{14}", query.get("filter[ENROLLMENT ID]", ""))))):
            raise ValueError("Noncanonical source reference")
        payload = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object,
                             parse_constant=_constant)
    except _Rejected:
        raise
    except (ValueError, UnicodeError, RecursionError) as error:
        raise _Rejected(f"{label}_INVALID_ORIGINAL_JSON_OR_SOURCE_REFERENCE") from error
    return payload, {"source_url": url, "body_sha256": meta["body_sha256"],
                     "body_bytes": len(body), "requested_at": requested.isoformat(),
                     "observed_at": observed.isoformat()}


def _cms_page(payload: object, pin: dict, *, endpoint: str, scope: CmsProviderOwnerScope,
              label: str) -> list[dict]:
    parsed = urlsplit(pin["source_url"])
    base = parsed._replace(query="").geturl()
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = dict(pairs)
    if (base != endpoint or len(pairs) != len(query)
            or set(query) != {"size", "offset", "filter[ENROLLMENT ID]"}
            or query.get("offset") != "0"
            or query.get("filter[ENROLLMENT ID]") != scope.enrollment_id
            or not re.fullmatch(r"[1-9][0-9]{0,2}", query.get("size", ""))
            or not 1 <= int(query["size"]) <= MAX_QUERY_ROWS):
        raise _Rejected(f"{label}_EXACT_UNPROJECTED_ENROLLMENT_QUERY_REQUIRED")
    if (not isinstance(payload, list) or len(payload) > int(query["size"])
            or any(not isinstance(row, dict) for row in payload)):
        raise _Rejected(f"{label}_MALFORMED_RESPONSE_ROWS")
    # Full pages may hide a second mapping. No unsupported terminal assumption.
    if len(payload) == int(query["size"]):
        raise _Rejected(f"{label}_PAGING_NOT_PROVEN_COMPLETE")
    if any(row.get("ENROLLMENT ID") != scope.enrollment_id for row in payload):
        raise _Rejected(f"{label}_QUERY_RETURNED_OUT_OF_SCOPE_ENROLLMENT")
    return payload


def _provider(rows: list[dict], *, scope: CmsProviderOwnerScope) -> dict:
    if not rows:
        raise _Rejected("EXACT_PROVIDER_ENROLLMENT_NOT_IN_CAPTURE")
    if len(rows) != 1:
        raise _Rejected("AMBIGUOUS_PROVIDER_OR_CCN_MAPPING")
    row = rows[0]
    ccn, pac = row.get("CCN"), row.get("ASSOCIATE ID")
    if not isinstance(ccn, str) or not re.fullmatch(r"[0-9]{6}", ccn):
        raise _Rejected("EXACT_SIX_DIGIT_CCN_NOT_CAPTURED")
    if scope.expected_ccn is not None and ccn != scope.expected_ccn:
        raise _Rejected("REQUESTED_CCN_DIFFERS_FROM_CAPTURE")
    if not isinstance(pac, str) or not re.fullmatch(r"[0-9]{10}", pac):
        raise _Rejected("PROVIDER_PAC_ID_NOT_CAPTURED")
    if row.get("PROVIDER TYPE CODE") not in {"00-09", "00-85", "00-24"}:
        raise _Rejected("SUPPORTED_HOSPITAL_PROVIDER_TYPE_NOT_CAPTURED")
    return {"ccn": ccn, "provider_associate_id": pac,
            "provider_name": _text(row.get("ORGANIZATION NAME"), code="PROVIDER_LEGAL_NAME_NOT_CAPTURED"),
            "provider_type_code": row["PROVIDER TYPE CODE"]}


def _owner(rows: list[dict], *, scope: CmsProviderOwnerScope, provider: dict,
           observed: datetime) -> dict:
    # Every row must preserve the enrollment/provider join and owner key fields.
    for row in rows:
        if (row.get("ASSOCIATE ID") != provider["provider_associate_id"]
                or not _same_name(row.get("ORGANIZATION NAME"), provider["provider_name"])
                or not isinstance(row.get("ASSOCIATE ID - OWNER"), str)
                or not re.fullmatch(r"[0-9]{10}", row["ASSOCIATE ID - OWNER"])
                or row.get("TYPE - OWNER") not in {"O", "I"}
                or not isinstance(row.get("ROLE CODE - OWNER"), str)
                or not re.fullmatch(r"[0-9]{2}", row["ROLE CODE - OWNER"])):
            raise _Rejected("OWNER_PROVIDER_JOIN_OR_OWNER_KEYS_INVALID")
    matches = [row for row in rows if row["ASSOCIATE ID - OWNER"] == scope.owner_associate_id
               and row["ROLE CODE - OWNER"] == scope.owner_role_code]
    if not matches:
        raise _Rejected("EXACT_OWNER_ROLE_NOT_IN_CAPTURE")
    if len(matches) != 1:
        raise _Rejected("AMBIGUOUS_OWNER_ROLE_OR_ASSOCIATION_DATE")
    row = matches[0]
    if row["TYPE - OWNER"] != "O":
        raise _Rejected("ORGANIZATIONAL_LEGAL_OWNER_REQUIRED")
    if not _same_name(row.get("ROLE TEXT - OWNER"), _ROLE_TEXT[scope.owner_role_code]):
        raise _Rejected("OWNER_ROLE_CODE_TEXT_CONFLICT")
    associated = row.get("ASSOCIATION DATE - OWNER")
    try:
        if not isinstance(associated, str) or date.fromisoformat(associated).isoformat() != associated:
            raise ValueError("Noncanonical association date")
        if date.fromisoformat(associated) > observed.date():
            raise ValueError("Future association date")
    except ValueError as error:
        raise _Rejected("OWNER_ASSOCIATION_DATE_INVALID_OR_FUTURE") from error
    percent = row.get("PERCENTAGE OWNERSHIP")
    try:
        if not isinstance(percent, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,6})?", percent):
            raise InvalidOperation()
        value = Decimal(percent)
        if not value.is_finite() or not Decimal(5) <= value <= Decimal(100):
            raise InvalidOperation()
    except InvalidOperation as error:
        raise _Rejected("OWNER_PERCENTAGE_MISSING_OR_ROLE_CONFLICT") from error
    return {"owner_name": _text(row.get("ORGANIZATION NAME - OWNER"), code="OWNER_LEGAL_NAME_NOT_CAPTURED"),
            "owner_associate_id": scope.owner_associate_id, "owner_role_code": scope.owner_role_code,
            "owner_role_text": row["ROLE TEXT - OWNER"], "association_date": associated,
            "ownership_percentage": percent}


def review_cms_provider_ownership(*, scope: CmsProviderOwnerScope,
                                  owner_capture: CapturedSource | None,
                                  provider_capture: CapturedSource | None,
                                  issuer_capture: CapturedSource | None,
                                  reviewed_at: datetime) -> dict:
    """Return one detached proposal or explicit rejection, with zero side effects.

    Both CMS responses must be complete original, unprojected exact-enrollment
    pages smaller than their requested size. This bounded first-page contract
    refuses a full page rather than inventing traversal or negative coverage.
    SEC submissions must directly name the exact organizational owner before
    both CMS observations in the same UTC-day cohort. Today's SEC body cannot
    authenticate an older CMS association date or an older incomplete fixture.
    """
    if not isinstance(scope, CmsProviderOwnerScope):
        raise ValueError("CMS review requires an explicit immutable scope")
    scope.validate()
    clock = _aware(reviewed_at)
    result = {"schema_version": 1, "contract_version": CONTRACT_VERSION,
              "reviewed_at": clock.isoformat(), "ingested_at": clock.isoformat(),
              "scope": {"ticker": scope.ticker, "issuer_cik": scope.issuer_cik,
                        "enrollment_id": scope.enrollment_id,
                        "owner_associate_id": scope.owner_associate_id,
                        "owner_role_code": scope.owner_role_code, "expected_ccn": scope.expected_ccn},
              "decision": "WITHHOLD", "identity_status": "UNVERIFIED",
              "issuer_attribution_allowed": False, "candidate_bridge_complete": False,
              "proposed_record": None, "reasons": [], "original_capture_pins": {},
              "exact_enrollment_query_complete": False,
              "historical_ownership_attribution_allowed": False,
              "association_date_is_publication_or_knowledge_time": False,
              "issuer_wide_scope": False, "ultimate_parent_inferred": False,
              "product_ownership_inferred": False, "negative_issuer_completeness_verified": False,
              "live_acceptance_verified": False, "score_changes": False,
              "source_http_requests": 0, "registry_modified": False}
    parsed = {}
    for label, capture in (("OWNERS", owner_capture), ("PROVIDER", provider_capture), ("ISSUER", issuer_capture)):
        try:
            endpoint = (CMS_OWNERS_URL if label == "OWNERS" else CMS_ENROLLMENTS_URL if label == "PROVIDER"
                        else f"https://data.sec.gov/submissions/CIK{scope.issuer_cik}.json")
            payload, pin = _original(capture, label=label, clock=clock,
                                     endpoint=endpoint, cms_query=label != "ISSUER")
            parsed[label] = payload
            result["original_capture_pins"][label.lower()] = pin
        except _Rejected as error:
            result["reasons"].append(str(error))
    if result["reasons"]:
        return result
    pins = result["original_capture_pins"]
    try:
        owner_rows = _cms_page(parsed["OWNERS"], pins["owners"], endpoint=CMS_OWNERS_URL,
                               scope=scope, label="OWNERS")
        provider_rows = _cms_page(parsed["PROVIDER"], pins["provider"], endpoint=CMS_ENROLLMENTS_URL,
                                  scope=scope, label="PROVIDER")
        result["exact_enrollment_query_complete"] = True
        provider = _provider(provider_rows, scope=scope)
        owner_observed, provider_observed, issuer_observed = (
            _aware(pins[key]["observed_at"]) for key in ("owners", "provider", "issuer"))
        owner = _owner(owner_rows, scope=scope, provider=provider, observed=owner_observed)
        issuer = parsed["ISSUER"]
        if (pins["issuer"]["source_url"] != f"https://data.sec.gov/submissions/CIK{scope.issuer_cik}.json"
                or not isinstance(issuer, dict) or type(issuer.get("cik")) not in {str, int}
                or not re.fullmatch(r"[0-9]{1,10}", str(issuer["cik"]))
                or str(issuer["cik"]).zfill(10) != scope.issuer_cik
                or not isinstance(issuer.get("tickers"), list) or not 1 <= len(issuer["tickers"]) <= 100
                or any(not isinstance(ticker, str) or not ticker for ticker in issuer["tickers"])
                or scope.ticker not in issuer["tickers"]):
            raise _Rejected("DIRECT_SEC_ISSUER_CIK_TICKER_BRIDGE_MISSING")
        if not _same_name(issuer.get("name"), owner["owner_name"]):
            raise _Rejected("EXACT_LEGAL_OWNER_ISSUER_NAME_BRIDGE_MISSING")
        if not (owner_observed.date() == provider_observed.date() == issuer_observed.date()
                and issuer_observed <= min(owner_observed, provider_observed)):
            raise _Rejected("ISSUER_BRIDGE_NOT_AVAILABLE_IN_ORIGINAL_CMS_OBSERVED_COHORT")
        snapshot_day = owner_observed.date()
        identifiers = {"enrollment_id": scope.enrollment_id, **provider, **owner}
        fingerprint = hashlib.sha256(json.dumps({"identifiers": identifiers, "captures": pins},
                                                sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        proposal = {"schema_version": 1, "record_id": f"hca-cms-exact-snapshot-{fingerprint[:24]}",
                    "kind": "cms_provider_owner_snapshot", "source": "cms", "ticker": scope.ticker,
                    "issuer_cik": scope.issuer_cik, "identifiers": identifiers,
                    "relationship": "DIRECT_OWNER" if scope.owner_role_code == "34" else "INDIRECT_OWNER",
                    "scope": "CMS_REPORTED_SINGLE_CCN_ENROLLMENT_OWNER_RELATIONSHIP",
                    "time_basis": "OBSERVED_SNAPSHOT", "observed_snapshot_day": snapshot_day.isoformat(),
                    "effective_from": snapshot_day.isoformat(),
                    "effective_to": (snapshot_day + timedelta(days=1)).isoformat(),
                    "known_at": clock.isoformat(), "ingested_at": clock.isoformat(),
                    "source_captures": pins, "relationship_snapshot_sha256": fingerprint,
                    "issuer_bridge_kind": "SEC_SUBMISSIONS_DIRECT_NAMED_ISSUER",
                    "publisher_snapshot_date": None, "publisher_publication_at": None,
                    "publisher_snapshot_date_available": False,
                    "historical_ownership_attribution_allowed": False,
                    "association_date_is_publication_or_knowledge_time": False,
                    "issuer_wide_scope": False, "ultimate_parent_inferred": False,
                    "adopted": False}
        result.update(decision="PROPOSE_FOR_REGISTRY_OWNER_REVIEW",
                      identity_status="PROPOSED_EXACT_OBSERVED_SNAPSHOT", candidate_bridge_complete=True,
                      proposed_record=proposal, current_source_snapshot=snapshot_day == clock.date())
    except _Rejected as error:
        result["reasons"].append(str(error))
    return result
