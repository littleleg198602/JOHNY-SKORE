"""Pure original-byte NHTSA model-year completeness review, without collection.

An HTTP400 success-shaped empty body is partial. An HTTP200 empty body must
also pass exact documented scope and complete publisher metadata controls.
No replay proves issuer-wide absence, current production, ownership or LIVE
acceptance, and no URL, database or HTTP client is opened by this module.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from html import unescape
import json
import re

from market_checker_app.services.doj_enforcement_scope_review import _SENSITIVE
from market_checker_app.services.nhtsa_negative_review_service import (
    NhtsaResponseCapture, documented_model_scope, review_capture,
)
from market_checker_app.services.nhtsa_recall_scout_service import (
    MAX_RESPONSE_BYTES, SUCCESS_MESSAGE, _unique_json_object, _validated_rows,
    recall_url, validate_nhtsa_response_scope,
)

CONTRACT_VERSION = "nhtsa-exact-model-negative-completeness-v1"
_PARTIAL_FIELDS = {"hasMore", "HasMore", "has_more", "truncated", "Truncated", "IsTruncated", "partial", "Partial",
    "incomplete", "Incomplete", "next", "Next", "nextPage", "NextPage", "nextPageToken", "NextPageToken",
    "continuationToken", "ContinuationToken", "pagination", "Pagination"}
_PAGE_FIELDS = {"page", "Page", "pageNumber", "PageNumber", "pageSize", "PageSize", "offset", "Offset"}
_TOTAL_FIELDS = {"TotalCount", "totalCount", "TotalResults", "totalResults", "TotalRecords", "totalRecords"}
_SCOPE_FIELDS = {"make": "make", "Make": "make", "model": "model", "Model": "model",
                 "modelYear": "model_year", "ModelYear": "model_year"}
_ERROR_FIELDS = {"Error", "Errors", "error", "errors"}
_ALLOWED_FIELDS = ({"Count", "Message", "results", "SearchCriteria"} | _PARTIAL_FIELDS | _PAGE_FIELDS
                   | _TOTAL_FIELDS | set(_SCOPE_FIELDS) | _ERROR_FIELDS)


def _clock(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("NHTSA captured review requires timezone-aware clocks")
    return result.astimezone(timezone.utc)


def _request(value):
    if not isinstance(value, dict):
        raise ValueError("One exact NHTSA model-year request is required")
    request = {}
    for name in ("make", "model"):
        text = value.get(name)
        if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9., -]{2,80}", text):
            raise ValueError("NHTSA scope requires exact public make/model names")
        request[name] = text
    year = value.get("model_year")
    if type(year) is not int or not 1990 <= year <= 2100:
        raise ValueError("NHTSA scope requires an explicit model year")
    request["model_year"] = year
    for name in ("catalog_capture_id", "recall_capture_id"):
        identifier = value.get(name)
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identifier):
            raise ValueError("NHTSA review requires bounded exact capture selectors")
        request[name] = identifier
    if request["catalog_capture_id"] == request["recall_capture_id"]:
        raise ValueError("Independent catalog and recall captures are required")
    return request


def _original(identifier, packet, clock, *, require_no_redirect):
    if isinstance(packet, dict):
        metadata, body = packet.get("metadata"), packet.get("body")
    elif isinstance(packet, tuple) and len(packet) == 2:
        metadata, body = packet
    else:
        raise ValueError("A capture must be metadata/body or a two-item tuple")
    if not isinstance(metadata, dict) or metadata.get("capture_id") != identifier:
        raise ValueError("Capture key must equal exact metadata capture_id")
    info = {"capture_id": identifier, "original_byte_pin_verified": False,
            "source_observed_at": None, "reason": ""}
    if (body is None or metadata.get("public_raw_available") is not True
            or str(metadata.get("status", "")).startswith("WITHHELD")):
        return info, None, None, "MISSING_OR_WHOLE_WITHHELD_ORIGINAL"
    if (not isinstance(body, bytes) or not 1 <= len(body) <= MAX_RESPONSE_BYTES
            or type(metadata.get("raw_response_bytes")) is not int
            or len(body) != metadata["raw_response_bytes"]
            or hashlib.sha256(body).hexdigest() != metadata.get("raw_response_sha256")):
        return info, None, None, "ORIGINAL_BYTE_PIN_OR_SIZE_BOUND"
    if _SENSITIVE.search(unescape(body.decode("utf-8", errors="replace")).encode()):
        return info, None, None, "WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE"
    try:
        started, observed = _clock(metadata["started_at"]), _clock(metadata["observed_at"])
        known = _clock(metadata.get("known_at", metadata["observed_at"]))
        if not started <= observed <= known <= clock:
            return info, None, None, "SOURCE_NOT_KNOWN_AT_REVIEW_CLOCK"
        redirect_policy = metadata.get("automatic_redirects")
        redirect_invalid = (redirect_policy is not False if require_no_redirect
                            else redirect_policy is not None and redirect_policy is not False)
        if (redirect_invalid
                or metadata.get("archive_encoding") not in {None, "raw"}
                or metadata.get("content_encoding") not in {None, "", "identity"}
                or metadata.get("status") not in {"ORIGINAL_BYTES_CAPTURED", "PUBLIC_EXACT_RAW", "PUBLIC_ORIGINAL_BYTES_CAPTURED"}
                or type(metadata.get("http_status")) is not int
                or metadata.get("content_type", "").split(";", 1)[0].strip().casefold() != "application/json"):
            return info, None, None, "UNSUPPORTED_ORIGINAL_TRANSPORT_METADATA"
        info.update(original_byte_pin_verified=True, source_observed_at=observed.isoformat(),
            source_known_at=known.isoformat(), raw_response_sha256=hashlib.sha256(body).hexdigest(),
            raw_response_bytes=len(body), http_status=metadata["http_status"],
            automatic_redirects_disabled_verified=redirect_policy is False)
        return info, metadata, body, None
    except (ValueError, TypeError, KeyError, AttributeError):
        return info, None, None, "INVALID_SOURCE_CAPTURE_METADATA"


def _completeness(payload, request):
    """Extra server metadata cannot silently narrow an empty response."""
    reasons = []
    try:
        rows = _validated_rows(payload)
        if payload.get("Message") != SUCCESS_MESSAGE:
            raise ValueError("Missing exact publisher success envelope")
    except (ValueError, TypeError, KeyError):
        return {"complete_publisher_envelope": False, "publisher_count": None,
                "returned_rows": None, "reason_codes": ["INVALID_OR_INCOMPLETE_PUBLISHER_ENVELOPE"]}
    if set(payload) - _ALLOWED_FIELDS:
        reasons.append("UNREVIEWED_PUBLISHER_METADATA")
    if any(payload.get(key) not in (None, False, "", [], {}) for key in _PARTIAL_FIELDS):
        reasons.append("PARTIAL_TRUNCATED_OR_CONTINUATION_METADATA")
    if any(key in payload for key in _PAGE_FIELDS):
        reasons.append("PAGINATED_QUERY_NOT_PROVEN_COMPLETE")
    if any(type(payload[key]) is not int or payload[key] != len(rows) for key in _TOTAL_FIELDS if key in payload):
        reasons.append("TOTAL_COUNT_DOES_NOT_ESTABLISH_FULL_RESPONSE")
    for key, selector in _SCOPE_FIELDS.items():
        if key in payload:
            expected = request[selector]
            if type(payload[key]) is bool or str(payload[key]).casefold() != str(expected).casefold():
                reasons.append("CONTRADICTORY_PUBLISHER_SCOPE")
                break
    if "SearchCriteria" in payload and payload["SearchCriteria"] is not None:
        criteria = payload["SearchCriteria"]
        match = re.fullmatch(r"Make:([^|]+)\s*\|\s*Model:([^|]+)\s*\|\s*ModelYear:([0-9]{4})", criteria) if isinstance(criteria, str) else None
        if (not match or match[1].strip().casefold() != request["make"].casefold()
                or match[2].strip().casefold() != request["model"].casefold()
                or int(match[3]) != request["model_year"]):
            reasons.append("CONTRADICTORY_OR_UNSUPPORTED_SEARCH_CRITERIA")
    return {"complete_publisher_envelope": not reasons, "publisher_count": payload["Count"],
            "returned_rows": len(rows), "reason_codes": sorted(set(reasons))}


def review_nhtsa_negative_scope(request, captures, *, reviewed_at=None):
    """Review original model/year evidence only; never execute collection.

    ``reviewed_at`` is the source-information cutoff. The new report's known_at
    remains its physical creation time, even for an explicit earlier cutoff.
    Both tuple packets and central-loader metadata/body packets are accepted.
    """
    request = _request(request)
    physical = datetime.now(timezone.utc)
    clock = _clock(reviewed_at) if reviewed_at is not None else physical
    if (not isinstance(captures, dict) or not 1 <= len(captures) <= 8
            or any(not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", key) for key in captures)):
        raise ValueError("NHTSA review requires one to eight exact captures")
    result = {"schema_version": 1, "review_kind": "NHTSA_NEGATIVE_SCOPE", "contract_version": CONTRACT_VERSION,
        "scope": {key: request[key] for key in ("make", "model", "model_year")},
        "reviewed_at": clock.isoformat(), "known_at": physical.isoformat(),
        "physical_report_created_at": physical.isoformat(), "status": "PARTIAL", "reason": "",
        "source_reviews": [], "catalog_scope_supported": False, "publisher_completeness_audit": None,
        "complete_negative": False, "source_complete_negative": False,
        "live_complete_negative_verified": False, "http_requests": 0, "database_actions": 0,
        "scope_completeness": "EXACT_DOCUMENTED_MODEL_YEAR_QUERY_SNAPSHOT_ONLY",
        "complete_current_recall_history_verified": False, "issuer_wide_absence_verified": False,
        "issuer_attribution_verified": False, "manufacturer_ownership_inferred": False,
        "current_vehicle_production_verified": False, "historical_availability_reconstructed": False,
        "registry_adoption_allowed": False, "scoring_applied": False, "prediction_input": False,
        "missing_evidence": []}
    originals = {}
    for selector in ("catalog_capture_id", "recall_capture_id"):
        identifier = request[selector]
        if identifier not in captures:
            result["missing_evidence"].append("MISSING_SELECTED_CAPTURE")
            continue
        info, metadata, body, reason = _original(identifier, captures[identifier], min(clock, physical),
            require_no_redirect=selector == "recall_capture_id")
        info["reason"] = reason or "ORIGINAL_BYTES_PINNED"
        result["source_reviews"].append(info)
        if reason:
            result["missing_evidence"].append(reason)
        else:
            originals[selector] = (metadata, body)
    if result["missing_evidence"]:
        result["reason"] = result["missing_evidence"][0]
        return result
    catalog_meta, catalog_body = originals["catalog_capture_id"]
    recall_meta, recall_body = originals["recall_capture_id"]
    try:
        scope = documented_model_scope(make=request["make"], model=request["model"], model_year=request["model_year"],
            catalog_body=catalog_body, catalog_metadata=catalog_meta)
        result.update(catalog_scope_supported=True, catalog_scope=scope.record())
    except (ValueError, TypeError, KeyError):
        result.update(reason="MISSING_EXACT_INDEPENDENT_MODEL_YEAR_CATALOG", missing_evidence=["MISSING_EXACT_INDEPENDENT_MODEL_YEAR_CATALOG"])
        return result
    try:
        expected_url = recall_url(request["make"], request["model"], request["model_year"])
        validate_nhtsa_response_scope(recall_meta["requested_url"], expected_url)
        validate_nhtsa_response_scope(recall_meta["final_url"], expected_url)
        payload = json.loads(recall_body, object_pairs_hook=_unique_json_object)
        complete = _completeness(payload, request)
        result["publisher_completeness_audit"] = complete
        capture = NhtsaResponseCapture(recall_meta["requested_url"], recall_meta["final_url"], recall_meta["http_status"],
            recall_meta["content_type"], recall_body, _clock(recall_meta["started_at"]), _clock(recall_meta["observed_at"]),
            origin="ARCHIVED_RESPONSE_REPLAY")
        base = review_capture(scope, capture, reviewed_at=clock)
    except (ValueError, TypeError, KeyError):
        result.update(reason="INVALID_EXACT_QUERY_CLOCK_OR_PUBLISHER_BODY", missing_evidence=["INVALID_EXACT_QUERY_CLOCK_OR_PUBLISHER_BODY"])
        return result
    result.update(http_status=recall_meta["http_status"], publisher_count=base["publisher_count"],
                  matched_rows=base["matched_rows"], rejected_rows=base["rejected_rows"])
    if recall_meta["http_status"] != 200:
        result.update(reason=f"HTTP_{recall_meta['http_status']}_IS_NOT_COMPLETE_NEGATIVE",
                      missing_evidence=["SUCCESSFUL_HTTP200_RESPONSE_REQUIRED"])
    elif not complete["complete_publisher_envelope"]:
        result.update(reason="INCOMPLETE_OR_CONTRADICTORY_PUBLISHER_METADATA", missing_evidence=complete["reason_codes"])
    else:
        result.update(status=base["status"], reason=base["reason"], complete_negative=base["complete_negative"],
                      source_complete_negative=base["complete_negative"])
        if base["status"] == "PARTIAL":
            result["missing_evidence"] = [base["reason"]]
    return result


__all__ = ["CONTRACT_VERSION", "review_nhtsa_negative_scope"]
