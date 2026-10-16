"""Recover prime-award observations from a complete, pinned original traversal.

This is an offline import, never a source request or quota reconstruction. All
pages are validated before one SQLite transaction can insert any findings. The
source capture clock is retained; first ingestion is the physical current time.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

from market_checker_app.services.usaspending_request_budget import MAX_RESPONSE_BYTES, bounded_positive
from market_checker_app.services.usaspending_scout_service import (
    API_URL, FIELDS, _award_details, _validate_identities, load_verified_identities,
)
from market_checker_app.storage.scout_store import ScoutStore, _key
from market_checker_app.utils.ticker_universe import load_canonical_tickers


CAPTURE_SCHEMA = "usaspending_prime_award_capture_v1"
RECOVERY_SCHEMA = "usaspending_prime_archive_recovery_v1"
MAX_MANIFEST_BYTES = 64_000
MAX_PAGES = 20
MAX_AWARDS = 2_000
MAX_ROW_BYTES = 32_000
_HASH = re.compile(r"[a-f0-9]{64}")
_SENSITIVE_KEY = re.compile(
    r"(?i)(?:authorization|password|credential|api[_-]?key|subscription[_-]?key|"
    r"client[_-]?secret|access[_-]?token|refresh[_-]?token|private[_-]?key)")


class _Rejected(ValueError):
    pass


def _reject(reason: str) -> None:
    raise _Rejected(reason)


def _physical_clock() -> datetime:
    return datetime.now(timezone.utc)


def _clock(value: object) -> datetime:
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            _reject("INVALID_CLOCK")
        return result.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError, OverflowError):
        _reject("INVALID_CLOCK")


def _read(path: Path, maximum: int) -> bytes:
    # No unbounded read/stat trust: even a file growing during the read is capped.
    if path.is_symlink() or not path.is_file():
        _reject("MISSING_OR_UNSAFE_ARCHIVE_FILE")
    with path.open("rb") as handle:
        encoded = handle.read(maximum + 1)
    if len(encoded) > maximum:
        _reject("ARCHIVE_BYTE_BOUND_EXCEEDED")
    return encoded


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            _reject("DUPLICATE_JSON_KEY")
        if _SENSITIVE_KEY.search(key) and value not in (None, ""):
            _reject("UNSAFE_SOURCE_BODY")
        result[key] = value
    return result


def _json(encoded: bytes) -> object:
    # An unsafe whole body is rejected, never sanitized or re-encoded for import.
    if any(re.search(pattern, encoded, re.I) for pattern in (
        rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        rb"\bAKIA[A-Z0-9]{16}\b",
        rb"\bbearer\s+[A-Za-z0-9._~+/=-]{12,}",
        rb"[?&](?:sig|signature|x-amz-signature|x-goog-signature|access_token)=[^\s\"'<>]{8,}",
    )):
        _reject("UNSAFE_SOURCE_BODY")
    try:
        return json.loads(encoded, object_pairs_hook=_pairs,
                          parse_constant=lambda _: _reject("NONFINITE_JSON_VALUE"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        _reject("INVALID_ARCHIVE_JSON")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _request(value: object) -> tuple[dict, str]:
    if not isinstance(value, dict) or set(value) != {"filters", "fields", "limit", "page", "sort", "order"}:
        _reject("INVALID_PRIME_REQUEST")
    if value["fields"] != FIELDS or value["sort"] != "Last Modified Date" or value["order"] != "desc":
        _reject("INVALID_PRIME_REQUEST")
    bounded_positive(value["limit"], 100, "Prime archive page limit")
    bounded_positive(value["page"], MAX_PAGES, "Prime archive page")
    filters = value["filters"]
    if not isinstance(filters, dict) or set(filters) != {"award_type_codes", "recipient_search_text", "time_period"}:
        _reject("INVALID_PRIME_QUERY_SCOPE")
    recipients, periods = filters["recipient_search_text"], filters["time_period"]
    if (filters["award_type_codes"] != ["A", "B", "C", "D"] or not isinstance(recipients, list)
            or len(recipients) != 1 or not isinstance(recipients[0], str)
            or not re.fullmatch(r"[A-Z0-9]{12}", recipients[0])
            or not isinstance(periods, list) or len(periods) != 1 or not isinstance(periods[0], dict)
            or set(periods[0]) != {"start_date", "end_date"}):
        _reject("INVALID_PRIME_QUERY_SCOPE")
    try:
        start, end = (date.fromisoformat(periods[0][key]) for key in ("start_date", "end_date"))
        if (start.isoformat() != periods[0]["start_date"] or end.isoformat() != periods[0]["end_date"]
                or not 0 <= (end - start).days <= 730):
            _reject("INVALID_PRIME_QUERY_WINDOW")
    except (ValueError, TypeError):
        _reject("INVALID_PRIME_QUERY_WINDOW")
    return value, recipients[0]


def _capture(path: Path, pin: str, *, cutoff: datetime, physical: datetime) -> tuple[dict, dict, dict]:
    encoded = _read(path, MAX_MANIFEST_BYTES)
    if not isinstance(pin, str) or not _HASH.fullmatch(pin) or hashlib.sha256(encoded).hexdigest() != pin:
        _reject("MANIFEST_HASH_MISMATCH")
    meta = _json(encoded)
    if not isinstance(meta, dict):
        _reject("INVALID_PRIME_CAPTURE_MANIFEST")
    if (meta.get("body_withheld") is True or meta.get("withheld") is True
            or any(isinstance(meta.get(key), str) and meta[key].startswith("WITHHELD")
                   for key in ("status", "archive_status", "publication_status"))):
        _reject("WITHHELD_BODY_UNAVAILABLE")
    if meta.get("schema_version") != CAPTURE_SCHEMA or meta.get("capture_mode") != "LIVE":
        _reject("NOT_ORIGINAL_PRIME_CAPTURE")
    if (meta.get("source_url") != API_URL or meta.get("source_url_final") != API_URL
            or meta.get("method") != "POST" or type(meta.get("http_status")) is not int
            or meta["http_status"] != 200 or meta.get("complete_response") is not True
            or meta.get("error_type") or ("body_transformed" in meta and meta["body_transformed"] is not False)
            or meta.get("content_encoding") not in (None, "identity")):
        _reject("UNSUCCESSFUL_OR_REPLACED_PRIME_BODY")
    request, uei = _request(meta.get("request"))
    started, completed = _clock(meta.get("request_started_at")), _clock(meta.get("response_completed_at"))
    if started > completed or completed > cutoff or completed > physical:
        _reject("CAPTURE_CLOCK_OUTSIDE_CUTOFF")
    if date.fromisoformat(request["filters"]["time_period"][0]["end_date"]) > started.date():
        _reject("PRIME_QUERY_WINDOW_AFTER_CAPTURE")
    digest, name = meta.get("response_sha256"), meta.get("body_file")
    if not isinstance(digest, str) or not _HASH.fullmatch(digest) or name != f"body-{digest}.response":
        _reject("INVALID_PRIME_BODY_REFERENCE")
    body = _read(path.parent / name, MAX_RESPONSE_BYTES)
    if (type(meta.get("response_bytes")) is not int or meta["response_bytes"] != len(body)
            or hashlib.sha256(body).hexdigest() != digest):
        _reject("BODY_HASH_OR_LENGTH_MISMATCH")
    headers = meta.get("response_headers", {})
    if not isinstance(headers, dict) or len({key.lower() for key in headers}) != len(headers):
        _reject("INVALID_CAPTURE_RESPONSE_HEADERS")
    headers = {key.lower(): value for key, value in headers.items()}
    length = headers.get("content-length")
    if length is not None and (not isinstance(length, str) or not re.fullmatch(r"[0-9]{1,8}", length)
                               or int(length) != len(body)):
        _reject("SOURCE_CONTENT_LENGTH_MISMATCH")
    if headers.get("content-encoding") not in (None, "identity"):
        _reject("UNSUCCESSFUL_OR_REPLACED_PRIME_BODY")
    payload = _json(body)
    if not isinstance(payload, dict) or payload.get("spending_level") != "awards":
        _reject("NOT_PRIME_AWARD_RESPONSE")
    if type(payload.get("limit")) is not int or payload["limit"] != request["limit"]:
        _reject("RESPONSE_PAGE_LIMIT_MISMATCH")
    rows, paging = payload.get("results"), payload.get("page_metadata")
    if (not isinstance(rows, list) or len(rows) > request["limit"] or not isinstance(paging, dict)
            or type(paging.get("page")) is not int or paging["page"] != request["page"]
            or type(paging.get("hasNext")) is not bool or (paging["hasNext"] and not rows)):
        _reject("INVALID_PRIME_PAGINATION")
    expected_next = request["page"] + 1 if paging["hasNext"] else None
    expected_previous = request["page"] - 1 if request["page"] > 1 else None
    if (("next" in paging and (paging["next"] != expected_next or type(paging["next"]) is bool))
            or ("previous" in paging and (paging["previous"] != expected_previous or type(paging["previous"]) is bool))
            or ("hasPrevious" in paging and paging["hasPrevious"] is not (request["page"] > 1))
            or ("last_record_unique_id" in paging and rows and "internal_id" in rows[-1]
                and (type(paging["last_record_unique_id"]) is not int
                     or paging["last_record_unique_id"] != rows[-1]["internal_id"]))):
        _reject("INVALID_PRIME_PAGINATION")
    reference = {"manifest_name": path.name, "manifest_sha256": pin,
                 "body_file": name, "body_sha256": digest, "response_bytes": len(body),
                 "request": request, "request_started_at": meta["request_started_at"],
                 "response_completed_at": meta["response_completed_at"], "reported_rows": len(rows),
                 "has_next": paging["hasNext"]}
    return meta, payload, reference


def _row(raw: object, identity: dict, *, completed: datetime, query_end: str) -> dict:
    if not isinstance(raw, dict) or len(_canonical(raw).encode()) > MAX_ROW_BYTES:
        _reject("INVALID_PRIME_ROW")
    award, generated = raw.get("Award ID"), raw.get("generated_internal_id")
    if (not isinstance(award, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{0,199}", award)
            or not isinstance(generated, str) or not re.fullmatch(r"CONT_AWD_[A-Za-z0-9_-]{1,990}", generated)
            or not generated.startswith(f"CONT_AWD_{award}_")
            or any(key not in raw for key in FIELDS)
            or any(not isinstance(raw[key], str) or not raw[key].strip()
                   for key in ("Awarding Agency", "Contract Award Type"))
            or ("internal_id" in raw and (type(raw["internal_id"]) is not int or raw["internal_id"] <= 0))):
        _reject("INVALID_PRIME_SOURCE_ID_OR_FIELDS")
    if isinstance(raw.get("Last Modified Date"), str) and len(raw["Last Modified Date"]) > 64:
        _reject("INVALID_PRIME_MODIFIED_CLOCK")
    details = _award_details(raw, identity, clock=completed, state={"query_end": query_end})
    if details is None:
        _reject("PRIME_ROW_IDENTITY_DATE_OR_AMOUNT_MISMATCH")
    if identity.get("effective_to") and details["award_start"] > identity["effective_to"]:
        _reject("PRIME_ROW_OUTSIDE_RELATIONSHIP")
    return details


def _modified_clock(raw: object, completed: datetime) -> dict:
    audit = {"raw": raw, "publisher_revision_time_verified": False, "utc": None,
             "status": "UNKNOWN"}
    if raw is None:
        return audit
    if not isinstance(raw, str):
        _reject("INVALID_PRIME_MODIFIED_CLOCK")
    try:
        if len(raw) == 10:
            day = date.fromisoformat(raw)
            if day > completed.date():
                _reject("PRIME_MODIFIED_CLOCK_AFTER_CAPTURE")
            return {**audit, "status": "DATE_ONLY"}
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            if stamp.date() > completed.date():
                _reject("PRIME_MODIFIED_CLOCK_AFTER_CAPTURE")
            return {**audit, "status": "TIMEZONE_UNKNOWN"}
        stamp = stamp.astimezone(timezone.utc)
        if stamp > completed:
            _reject("PRIME_MODIFIED_CLOCK_AFTER_CAPTURE")
        return {**audit, "status": "SOURCE_TIMESTAMP", "utc": stamp.isoformat()}
    except _Rejected:
        raise
    except (ValueError, OverflowError):
        _reject("INVALID_PRIME_MODIFIED_CLOCK")


def _insert(store: ScoutStore, prepared: list[dict], *, physical: datetime) -> tuple[list[str], int]:
    if not isinstance(store, ScoutStore) or not store.db_path.is_file():
        _reject("EXISTING_SCOUT_STORE_REQUIRED")
    identifiers, inserted = [], 0
    # No constructor, schema, checkpoints, leases or quota writes. All finding
    # inserts and conflict checks share one transaction and are rolled back on
    # any invalid existing row. The store's closing connection owns cleanup.
    with store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for item in prepared:
            identifier = item["finding_id"]
            existing = conn.execute("SELECT * FROM scout_findings WHERE finding_id=?", (identifier,)).fetchone()
            if existing is not None:
                previous = dict(existing)
                try:
                    detail = json.loads(previous["details_json"])
                    core = {key: detail.get(key) for key in item["source_details"]}
                    valid = (all(previous[key] == item[key] for key in
                                 ("source", "subject_id", "source_object_id", "content_hash", "source_url"))
                             and previous["verification_status"] == "SOURCE_VERIFIED"
                             and core == item["source_details"]
                             and _clock(previous["published_at"]) <= _clock(previous["available_at"])
                             <= _clock(previous["first_observed_at"]) <= physical)
                except (TypeError, ValueError, AttributeError):
                    valid = False
                if not valid:
                    _reject("EXISTING_FINDING_CONFLICT")
                # Exact replay preserves even the existing retrieved heartbeat.
                identifiers.append(identifier)
                continue
            conn.execute("""
                INSERT INTO scout_findings(finding_id, source, subject_id, source_object_id,
                    content_hash, title, source_url, locator, published_at, available_at,
                    first_observed_at, retrieved_at, verification_status, details_json)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'SOURCE_VERIFIED', ?)
            """, (identifier, item["source"], item["subject_id"], item["source_object_id"],
                  item["content_hash"], item["title"], item["source_url"], item["locator"],
                  item["capture_completed_at"], item["capture_completed_at"], physical.isoformat(),
                  physical.isoformat(), _canonical(item["details"])))
            identifiers.append(identifier)
            inserted += 1
    return identifiers, inserted


def recover_prime_awards_from_archives(
    store: ScoutStore | None, *, manifests: Sequence[Path],
    expected_manifest_sha256: Mapping[str, str], subject_id: str, as_of: datetime,
    identities: list[dict] | None = None, maximum_pages: int = MAX_PAGES,
    maximum_awards: int = MAX_AWARDS, validate_only: bool = False,
) -> dict:
    """Validate 1–20 pinned original prime pages and atomically recover findings.

    Hash-map keys are ``str(manifest_path)``. ``as_of`` only bounds source capture
    and identity knowledge. It cannot set ingestion, which always uses now UTC.
    Fixtures/replayed/projected bodies are not accepted as original captures.
    ``validate_only`` does not access or initialize the optional database.
    """
    physical = _clock(_physical_clock())
    report = {"schema_version": RECOVERY_SCHEMA, "status": "REJECTED",
              "recovery_recorded_at": physical.isoformat(), "as_of": None,
              "subject_id": subject_id if isinstance(subject_id, str) and re.fullmatch(r"[A-Z][A-Z0-9.-]{0,12}", subject_id) else None,
              "acquisition_mode": "OFFLINE_ARCHIVE_RECOVERY",
              "http_attempts": 0, "quota_reconstructed": False, "new_findings": 0,
              "recovered_awards": 0, "finding_ids": [], "pages": [], "query_scope": None,
              "traversal_complete": False, "pagination_snapshot_guaranteed": False,
              "current_live_collection": False, "source_manifest_is_publisher_signed": False,
              "publisher_publication_time_verified": False, "publisher_revision_time_verified": False,
              "historical_availability_reconstructed": False, "issuer_coverage_complete": False,
              "modification_history_complete": False, "revenue_inferred": False,
              "scoring_applied": False, "fixtures_count_as_live": False, "database_changed": False,
              "body_bytes_retained_or_replaced": False}
    try:
        cutoff = _clock(as_of)
        report["as_of"] = cutoff.isoformat()
        bounded_positive(maximum_pages, MAX_PAGES, "Prime archive page budget")
        bounded_positive(maximum_awards, MAX_AWARDS, "Prime archive award budget")
        if type(validate_only) is not bool or not isinstance(subject_id, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,12}", subject_id):
            _reject("INVALID_RECOVERY_ARGUMENT")
        if subject_id not in load_canonical_tickers():
            _reject("SUBJECT_OUTSIDE_CANONICAL_UNIVERSE")
        if (not isinstance(manifests, Sequence) or isinstance(manifests, (str, bytes))
                or not 1 <= len(manifests) <= maximum_pages or not isinstance(expected_manifest_sha256, Mapping)):
            _reject("INVALID_MANIFEST_BOUND_OR_PINS")
        paths = [Path(path) for path in manifests]
        if (len(expected_manifest_sha256) != len(paths) or len({str(path.resolve()) for path in paths}) != len(paths)
                or set(expected_manifest_sha256) != {str(path) for path in paths}):
            _reject("DUPLICATE_MANIFEST_OR_MISSING_PIN")
        entries = identities if identities is not None else load_verified_identities()
        if not isinstance(entries, list) or len(entries) > 1_000:
            _reject("INVALID_VERIFIED_IDENTITY_BOUND")
        _json(_canonical(entries).encode())
        known = _validate_identities(entries)
        pages = [_capture(path, expected_manifest_sha256[str(path)], cutoff=cutoff, physical=physical) for path in paths]
        pages.sort(key=lambda value: value[0]["request"]["page"])
        report["pages"] = [reference for _, _, reference in pages]
        base = {key: value for key, value in pages[0][0]["request"].items() if key != "page"}
        uei = base["filters"]["recipient_search_text"][0]
        choices = [entry for entry in known if entry["ticker"] == subject_id and entry["uei"] == uei]
        if len(choices) != 1:
            _reject("VERIFIED_UEI_IDENTITY_REQUIRED")
        identity = choices[0]
        if any(urlsplit(identity[key]).query or urlsplit(identity[key]).fragment
               for key in ("uei_evidence_url", "relationship_evidence_url", "continuity_evidence_url")
               if identity.get(key)):
            _reject("UNSAFE_IDENTITY_REFERENCE")
        window = base["filters"]["time_period"][0]
        if (window["start_date"] < identity["effective_from"]
                or (identity.get("effective_to") and window["end_date"] > identity["effective_to"])):
            _reject("QUERY_WINDOW_OUTSIDE_VERIFIED_RELATIONSHIP")
        report.update(query_scope=base, verified_identity_sha256=hashlib.sha256(_canonical(identity).encode()).hexdigest())
        prepared, seen, last_completed = [], set(), None
        query_end = base["filters"]["time_period"][0]["end_date"]
        for index, (meta, payload, reference) in enumerate(pages):
            request = meta["request"]
            if (request["page"] != index + 1 or {key: value for key, value in request.items() if key != "page"} != base):
                _reject("PAGE_SEQUENCE_OR_QUERY_SCOPE_MISMATCH")
            if reference["has_next"] is not (index < len(pages) - 1):
                _reject("INCOMPLETE_OR_EXTRA_TERMINAL_PAGES")
            started, completed = _clock(meta["request_started_at"]), _clock(meta["response_completed_at"])
            if last_completed is not None and started < last_completed:
                _reject("OVERLAPPING_OR_REORDERED_CAPTURE_CLOCKS")
            if _clock(identity["known_at"]) > started or date.fromisoformat(identity["effective_from"]) > completed.date():
                _reject("IDENTITY_NOT_KNOWN_AT_ORIGINAL_CAPTURE")
            last_completed = completed
            for row_index, raw in enumerate(payload["results"]):
                if len(prepared) >= maximum_awards:
                    _reject("AWARD_BOUND_EXCEEDED")
                details = _row(raw, identity, completed=completed, query_end=query_end)
                modified_clock = _modified_clock(raw["Last Modified Date"], completed)
                generated = details["generated_internal_id"]
                if generated in seen:
                    _reject("DUPLICATE_OR_CONFLICTING_AWARD_SOURCE_ID")
                seen.add(generated)
                content_hash = hashlib.sha256(_canonical(details).encode()).hexdigest()
                source_object_id = f"{uei}:{generated}"
                finding_id = "finding:" + _key("usaspending", subject_id, source_object_id, content_hash)[:32]
                source_details = dict(details)
                details["archive_recovery"] = {
                    "schema_version": RECOVERY_SCHEMA, "acquisition_mode": "OFFLINE_ARCHIVE_RECOVERY",
                    "source_row_sha256": hashlib.sha256(_canonical(raw).encode()).hexdigest(),
                    "source_manifest_sha256": reference["manifest_sha256"], "source_body_sha256": reference["body_sha256"],
                    "request_started_at": meta["request_started_at"], "response_completed_at": meta["response_completed_at"],
                    "ingested_at": physical.isoformat(), "page": request["page"], "row_index": row_index,
                    "traversal_complete": True, "pagination_snapshot_guaranteed": False,
                    "original_modified_date": raw["Last Modified Date"],
                    "modified_clock_audit": modified_clock,
                    "publisher_publication_time_verified": False, "publisher_revision_time_verified": False,
                    "historical_availability_reconstructed": False, "current_live_collection": False}
                prepared.append({"finding_id": finding_id, "source": "usaspending", "subject_id": subject_id,
                                 "source_object_id": source_object_id, "content_hash": content_hash,
                                 "source_url": API_URL, "locator": f"generated_internal_id:{generated}",
                                 "title": f"USAspending {details['award_id']} – {identity['recipient_name']}",
                                 "source_details": source_details, "details": details,
                                 "capture_completed_at": completed.isoformat()})
        report.update(traversal_complete=True, validated_awards=len(prepared),
                      capture_started_at=pages[0][0]["request_started_at"],
                      capture_completed_at=pages[-1][0]["response_completed_at"])
        if validate_only:
            return {**report, "status": "VALIDATED", "validate_only": True}
        if not prepared:
            return {**report, "status": "COMPLETE_EMPTY", "validate_only": False}
        identifiers, new = _insert(store, prepared, physical=physical)
        return {**report, "status": "RECOVERED" if new else "UNCHANGED", "new_findings": new,
                "recovered_awards": len(identifiers), "finding_ids": identifiers,
                "database_changed": bool(new), "validate_only": False}
    except _Rejected as error:
        return {**report, "reason": str(error)}
    except (OSError, ValueError, KeyError, TypeError, OverflowError, sqlite3.Error) as error:
        # Error messages can contain source text, paths or access values.
        return {**report, "reason": "INVALID_OR_UNAVAILABLE_RECOVERY_INPUT", "error_type": type(error).__name__}


__all__ = ["CAPTURE_SCHEMA", "RECOVERY_SCHEMA", "recover_prime_awards_from_archives"]
