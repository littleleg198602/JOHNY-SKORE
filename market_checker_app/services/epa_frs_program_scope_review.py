"""Captured-only EPA facility/program/source-field observations, never ownership.

The supported Results/Facilities layout is deliberately narrow. Caller-selected
interest/date fields remain literal observations within their own program row;
their legal meaning and date semantics are not established by a JSON pointer.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
from html import unescape
import json
import re
from urllib.parse import parse_qsl, urlsplit

from market_checker_app.services.epa_frs_ownership_review import EPA_HOSTS, _SENSITIVE

CONTRACT_VERSION = "epa-frs-program-source-field-scope-v2-interest-array-bound"
MAX_BODY_BYTES = 4_000_000
MAX_CAPTURES = 4
MAX_FACILITIES = 100
MAX_PROGRAMS = 100
MAX_SELECTED_PROGRAMS = 25
MAX_INTEREST_SELECTIONS = 100
MAX_INTEREST_ROWS = 100
MAX_JSON_NODES = 100_000
MAX_JSON_DEPTH = 32
_FIELD = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,79}")
_FACILITY = re.compile(r"/Results/Facilities/(?:0|[1-9][0-9]?)")
_DATE_ROLES = {"start", "end", "last_reported"}


class _Partial(ValueError):
    pass


def _clock(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("FRS program review clocks require an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _physical_clock():
    return datetime.now(timezone.utc)


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _literal(value, limit=500):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("An exact bounded source literal is required")
    return value


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or set(value) - set(required) - set(optional):
        raise ValueError("Unexpected or missing FRS program selection fields")


def _request(raw):
    _keys(raw, {"capture_id", "frs_registry_id", "facility_name", "facility_pointer", "programs"})
    identifier, registry, name, pointer = (raw[key] for key in
        ("capture_id", "frs_registry_id", "facility_name", "facility_pointer"))
    if (not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identifier)
            or not isinstance(registry, str) or not re.fullmatch(r"[0-9]{12}", registry)
            or not isinstance(pointer, str) or not _FACILITY.fullmatch(pointer)):
        raise ValueError("Exact capture ID, twelve-digit FRS ID and facility-list pointer required")
    result = {"capture_id": identifier, "frs_registry_id": registry, "facility_name": _literal(name),
              "facility_pointer": pointer, "programs": []}
    if not isinstance(raw["programs"], list) or len(raw["programs"]) > MAX_SELECTED_PROGRAMS:
        raise ValueError("Bounded explicit program selections required")
    seen_programs, selected_interests = set(), 0
    for program in raw["programs"]:
        _keys(program, {"program_pointer", "program_system_acronym", "program_system_id", "interests"})
        pp = program["program_pointer"]
        if (not isinstance(pp, str) or not re.fullmatch(re.escape(pointer)+r"/ProgramFacilities/(?:0|[1-9][0-9]?)", pp)
                or pp in seen_programs):
            raise ValueError("Program pointer must name a unique direct child of the selected facility")
        seen_programs.add(pp)
        row = {"program_pointer": pp, "program_system_acronym": _literal(program["program_system_acronym"], 100),
               "program_system_id": _literal(program["program_system_id"], 200), "interests": []}
        if not isinstance(program["interests"], list):
            raise ValueError("Explicit interest selections required")
        seen_interests = set()
        for interest in program["interests"]:
            _keys(interest, {"interest_pointer", "role_field", "date_fields"})
            ip, role, fields = (interest[key] for key in ("interest_pointer", "role_field", "date_fields"))
            if (not isinstance(ip, str) or ip in seen_interests
                    or not (ip == pp or re.fullmatch(re.escape(pp)+r"/[A-Za-z][A-Za-z0-9_]{0,79}/(?:0|[1-9][0-9]?)", ip))
                    or not isinstance(role, str) or not _FIELD.fullmatch(role)
                    or not isinstance(fields, dict) or set(fields)-_DATE_ROLES
                    or any(not isinstance(v, str) or not _FIELD.fullmatch(v) for v in fields.values())
                    or len(set(fields.values())) != len(fields) or role in fields.values()):
                raise ValueError("Interest fields must stay in one program object or one of its own child rows")
            seen_interests.add(ip)
            row["interests"].append({"interest_pointer": ip, "role_field": role, "date_fields": dict(fields)})
            selected_interests += 1
        result["programs"].append(row)
    if selected_interests > MAX_INTEREST_SELECTIONS:
        raise ValueError("FRS interest selection bound exceeded")
    return result


def _primary_url(value):
    if not isinstance(value, str) or len(value) > 2_048:
        raise _Partial("INVALID_PRIMARY_EPA_REFERENCE")
    try:
        parsed = urlsplit(value)
        fields = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True) if parsed.query else []
        if (parsed.scheme != "https" or parsed.hostname not in EPA_HOSTS or parsed.port not in {None, 443}
                or parsed.username or parsed.password or parsed.fragment or "\\" in parsed.path
                or ".." in parsed.path.split("/")
                or any(re.search(r"(?:key|token|password|passcode|signature|secret|^sig$)", key, re.I) for key, _ in fields)
                or not (parsed.path == "/echo/echo_rest_services.get_facility_info"
                        or parsed.path.endswith("/frs_rest_services.get_facilities"))):
            raise _Partial("INVALID_PRIMARY_EPA_REFERENCE")
    except ValueError as error:
        raise _Partial("INVALID_PRIMARY_EPA_REFERENCE") from error
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _Partial("DUPLICATE_PRIMARY_JSON_KEY")
        result[key] = value
    return result


def _constant(_):
    raise _Partial("NONFINITE_PRIMARY_JSON_CONSTANT")


def _json(body):
    try:
        payload = json.loads(body.decode("utf-8-sig"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise _Partial("INVALID_OR_INCOMPLETE_PRIMARY_JSON") from error
    pending, nodes = [(payload, 0)], 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise _Partial("PRIMARY_JSON_STRUCTURE_BOUND")
        if isinstance(item, dict):
            pending.extend((value, depth+1) for value in item.values())
        elif isinstance(item, list):
            pending.extend((value, depth+1) for value in item)
    return payload


def _source(identifier, packet, cutoff):
    if isinstance(packet, tuple) and len(packet) == 2:
        meta, body = packet
    elif isinstance(packet, dict):
        meta, body = packet.get("metadata"), packet.get("body")
    else:
        raise ValueError("FRS capture requires metadata and original bytes")
    if not isinstance(meta, dict) or meta.get("capture_id") != identifier:
        raise ValueError("FRS capture key must equal its metadata capture_id")
    info = {"capture_id": identifier, "status": "PARTIAL", "original_body_pin_verified": False}
    # A withheld/missing record is never inspected, even if a body is supplied.
    if (body is None or meta.get("public_raw_available") is not True
            or any(str(meta.get(k, "")).startswith("WITHHELD") for k in ("status", "publication_status"))):
        return info, None, "MISSING_OR_WHOLE_WITHHELD_ORIGINAL"
    try:
        url = _primary_url(meta.get("requested_url"))
        started, observed = _clock(meta["started_at"]), _clock(meta["observed_at"])
        known = _clock(meta.get("known_at", meta["observed_at"]))
        if not started <= observed <= known <= cutoff:
            raise _Partial("SOURCE_NOT_KNOWN_AT_SOURCE_READ_CUTOFF")
        if (type(meta.get("http_status")) is not int or meta["http_status"] != 200
                or meta.get("final_url") != url or meta.get("automatic_redirects") is not False
                or meta.get("status") not in {"CAPTURED", "ORIGINAL_BYTES_CAPTURED"}
                or meta.get("content_type", "").split(";", 1)[0].strip().lower() != "application/json"
                or meta.get("content_encoding") not in {None, "", "identity"}
                or meta.get("archive_encoding") not in {None, "raw"}):
            raise _Partial("UNSUCCESSFUL_OR_CHANGED_ORIGINAL_TRANSPORT")
        if (not isinstance(body, bytes) or not 1 <= len(body) <= MAX_BODY_BYTES
                or type(meta.get("raw_response_bytes")) is not int or len(body) != meta["raw_response_bytes"]
                or _sha(body) != meta.get("raw_response_sha256")):
            raise _Partial("ORIGINAL_BODY_HASH_SIZE_OR_BYTE_BOUND")
        if _SENSITIVE.search(unescape(body.decode("utf-8", errors="replace")).encode()):
            raise _Partial("WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE")
        payload = _json(body)
        if _SENSITIVE.search(unescape(json.dumps(payload, ensure_ascii=False)).encode()):
            raise _Partial("WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE")
        info.update(status="ORIGINAL_JSON_PINNED", original_body_pin_verified=True,
            source_url=url, raw_response_sha256=_sha(body), raw_response_bytes=len(body),
            source_started_at=started.isoformat(), source_observed_at=observed.isoformat(), source_known_at=known.isoformat(),
            synthetic_contract_only=str(meta.get("origin", "")).startswith("SYNTHETIC"))
        return info, payload, None
    except _Partial as error:
        return info, None, str(error)
    except (KeyError, TypeError, ValueError, AttributeError):
        return info, None, "INVALID_OR_UNAVAILABLE_ORIGINAL_METADATA"


def _pointer(payload, pointer):
    try:
        for key in pointer[1:].split("/"):
            payload = payload[int(key)] if isinstance(payload, list) else payload[key]
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise _Partial("MISSING_SELECTED_SOURCE_ROW") from error
    if not isinstance(payload, dict):
        raise _Partial("SELECTED_SOURCE_ROW_IS_NOT_OBJECT")
    return payload


def _alias(row, keys):
    present = [key for key in keys if key in row]
    if len(present) != 1:
        raise _Partial("MISSING_OR_AMBIGUOUS_SOURCE_IDENTITY_FIELD")
    key = present[0]
    try:
        return key, _literal(row[key])
    except ValueError as error:
        raise _Partial("INVALID_SOURCE_IDENTITY_LITERAL") from error


def _fact(row, field, pointer, body_hash):
    value = row[field]
    return {"source_field": field, "source_value": value, "json_pointer": pointer+"/"+field,
        "source_value_json_sha256": _sha(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()),
        "original_body_sha256": body_hash, "binding": "EXACT_SAME_SOURCE_OBJECT"}


def _identity_scope(row, request, program=None):
    for aliases, expected in ((["RegistryId", "RegistryID"], request["frs_registry_id"]),
                              (["ProgramSystemAcronym"], program[0] if program else None),
                              (["ProgramSystemId"], program[1] if program else None)):
        if any(key in row for key in aliases):
            _, actual = _alias(row, aliases)
            if expected is None or actual != expected:
                raise _Partial("CONTRADICTORY_RECORD_LOCAL_FACILITY_OR_PROGRAM_ID")


def _date_field(row, field, pointer, digest):
    result = {"source_field": field, "literal": None, "iso_date": None,
        "status": "NOT_SELECTED" if field is None else "SOURCE_FIELD_NOT_PRESENT",
        "semantic_date_role_verified": False, "event_or_validity_verified": False, "evidence": None}
    if field is None or field not in row:
        return result
    value = row[field]
    if value is None:
        result["status"] = "NULL_SOURCE_VALUE"
    elif not isinstance(value, str) or len(value) > 80:
        result["status"] = "UNSUPPORTED_SOURCE_DATE_VALUE"
        return result
    else:
        result["literal"] = value
        result["status"] = "EMPTY_SOURCE_VALUE" if not value.strip() else "UNRECOGNIZED_DATE_LITERAL"
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            try:
                result.update(iso_date=date.fromisoformat(value).isoformat(), status="EXACT_ISO_DATE_LITERAL")
            except ValueError:
                pass
    result["evidence"] = _fact(row, field, pointer, digest)
    return result


def _interest(selection, payload, request, program_key, program_pointer, digest):
    pointer = selection["interest_pointer"]
    result = {"interest_pointer": pointer, "status": "PARTIAL", "role_literal_supported": False,
        "role_literal": None, "role_evidence": None, "dates": {},
        "meaning": "CALLER_SELECTED_PROGRAM_RECORD_SOURCE_FIELDS_ONLY",
        "program_interest_role_semantics_verified": False, "dated_program_interest_verified": False,
        "source_record_has_role_and_parseable_date": False, "lexical_start_end_order_coherent": None,
        "legal_owner_verified": False, "permitted_operator_verified": False, "missing_evidence": []}
    try:
        if pointer != program_pointer:
            collection, _ = pointer[len(program_pointer)+1:].split("/")
            parent = _pointer(payload, program_pointer)
            children = parent.get(collection)
            if not isinstance(children, list) or len(children) > MAX_INTEREST_ROWS:
                raise _Partial("MISSING_OR_UNBOUNDED_PROGRAM_LOCAL_INTEREST_ARRAY")
        row = _pointer(payload, pointer)
        _identity_scope(row, request, program_key)
        role = selection["role_field"]
        if role in row:
            try:
                result.update(role_literal=_literal(row[role], 300), role_literal_supported=True,
                              role_evidence=_fact(row, role, pointer, digest))
            except ValueError:
                result["missing_evidence"].append("MISSING_OR_INVALID_RECORD_LOCAL_ROLE_LITERAL")
        else:
            result["missing_evidence"].append("MISSING_OR_INVALID_RECORD_LOCAL_ROLE_LITERAL")
        result["dates"] = {key: _date_field(row, selection["date_fields"].get(key), pointer, digest)
                           for key in sorted(_DATE_ROLES)}
        result["source_record_has_role_and_parseable_date"] = result["role_literal_supported"] and any(
            item["iso_date"] is not None for item in result["dates"].values())
        start, end = (result["dates"][key]["iso_date"] for key in ("start", "end"))
        if start is not None and end is not None:
            result["lexical_start_end_order_coherent"] = start <= end
        result["missing_evidence"].extend(["INDEPENDENT_PUBLISHER_FIELD_SEMANTICS_NOT_ESTABLISHED",
            "PROGRAM_REGISTRATION_IS_NOT_LEGAL_OWNERSHIP_OR_PERMISSION"])
    except _Partial as error:
        result["missing_evidence"].append(str(error))
    return result


def _review_rows(result, request, payload, digest):
    if not isinstance(payload, dict) or not isinstance(payload.get("Results"), dict):
        raise _Partial("UNSUPPORTED_PRIMARY_FRS_JSON_ENVELOPE")
    envelope = payload["Results"]
    if (envelope.get("Message") != "Success"
            or any(key.lower() in {"error", "errors"} for key in payload.keys() | envelope.keys())):
        raise _Partial("UNSUCCESSFUL_PRIMARY_FRS_ENVELOPE")
    rows, count = envelope.get("Facilities"), envelope.get("QueryRows")
    if (not isinstance(rows, list) or len(rows) > MAX_FACILITIES or any(not isinstance(row, dict) for row in rows)
            or isinstance(count, bool) or not re.fullmatch(r"[0-9]{1,10}", str(count)) or int(count) < len(rows)):
        raise _Partial("UNBOUNDED_OR_INCONSISTENT_FACILITY_INVENTORY")
    result["response_row_inventory_complete"] = int(count) == len(rows)
    result["publisher_query_rows"] = int(count)
    result["returned_facility_rows"] = len(rows)
    if not result["response_row_inventory_complete"]:
        result["missing_evidence"].append("INCOMPLETE_FACILITY_QUERY")
    identities = [_alias(row, ("RegistryId", "RegistryID"))[1] for row in rows]
    if identities.count(request["frs_registry_id"]) != 1:
        raise _Partial("MISSING_OR_DUPLICATE_EXACT_FRS_REGISTRY_ID")
    facility = _pointer(payload, request["facility_pointer"])
    id_field, registry = _alias(facility, ("RegistryId", "RegistryID"))
    name_field, name = _alias(facility, ("FacilityName", "FacName"))
    if registry != request["frs_registry_id"] or name != request["facility_name"]:
        raise _Partial("SELECTED_FACILITY_ID_OR_NAME_MISMATCH")
    result.update(facility_identity_supported=True, facility_field_evidence=[
        _fact(facility, id_field, request["facility_pointer"], digest),
        _fact(facility, name_field, request["facility_pointer"], digest)])
    programs = facility.get("ProgramFacilities")
    if not isinstance(programs, list) or len(programs) > MAX_PROGRAMS or any(not isinstance(row, dict) for row in programs):
        raise _Partial("MISSING_OR_UNBOUNDED_FACILITY_LOCAL_PROGRAM_COLLECTION")
    inventory, program_keys = [], []
    for index, row in enumerate(programs):
        try:
            acronym, identifier = _literal(row["ProgramSystemAcronym"], 100), _literal(row["ProgramSystemId"], 200)
            _identity_scope(row, request, (acronym, identifier))
        except (KeyError, ValueError) as error:
            raise _Partial("INVALID_PROGRAM_LOCAL_IDENTITY") from error
        program_keys.append((acronym, identifier))
        pointer = request["facility_pointer"]+"/ProgramFacilities/"+str(index)
        fields = ["ProgramSystemAcronym", "ProgramSystemId"]
        if "ProgramFacilityName" in row:
            try:
                _literal(row["ProgramFacilityName"])
            except ValueError as error:
                raise _Partial("INVALID_PROGRAM_LOCAL_NAME") from error
            fields.append("ProgramFacilityName")
        inventory.append({"program_pointer": pointer, "program_system_acronym": acronym, "program_system_id": identifier,
            "program_facility_name": row.get("ProgramFacilityName"), "field_evidence": [_fact(row, key, pointer, digest) for key in fields]})
    if len(set(program_keys)) != len(program_keys):
        raise _Partial("DUPLICATE_FACILITY_LOCAL_PROGRAM_KEY")
    result.update(program_inventory_supported=True, program_inventory=inventory, observed_program_rows=len(programs))
    for selected in request["programs"]:
        row = {"program_pointer": selected["program_pointer"], "program_identity_supported": False,
               "program_system_acronym": selected["program_system_acronym"], "program_system_id": selected["program_system_id"],
               "interest_reviews": [], "missing_evidence": []}
        try:
            program = _pointer(payload, selected["program_pointer"])
            key = (selected["program_system_acronym"], selected["program_system_id"])
            if (program.get("ProgramSystemAcronym"), program.get("ProgramSystemId")) != key:
                raise _Partial("SELECTED_PROGRAM_ACRONYM_OR_SYSTEM_ID_MISMATCH")
            row["program_identity_supported"] = True
            row["interest_reviews"] = [_interest(item, payload, request, key, selected["program_pointer"], digest)
                                       for item in selected["interests"]]
            if not selected["interests"]:
                row["missing_evidence"].append("NO_EXPLICIT_INTEREST_FIELDS_SELECTED")
        except _Partial as error:
            row["missing_evidence"].append(str(error))
        result["selected_program_reviews"].append(row)


def _finish(result, started):
    bounds, pending = [started], [result]
    keys = {"review_started_at", "source_read_cutoff", "reviewed_at", "known_at", "ingested_at",
            "review_completed_at", "source_started_at", "source_observed_at", "source_known_at"}
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for key, item in value.items():
                if key in keys and item is not None:
                    bounds.append(_clock(item))
                elif isinstance(item, (dict, list, tuple)):
                    pending.append(item)
        elif isinstance(value, (list, tuple)):
            pending.extend(value)
    finished = _clock(_physical_clock())
    if finished < started:
        raise ValueError("FRS_PROGRAM_PHYSICAL_CLOCK_ROLLBACK")
    if finished < max(bounds):
        raise ValueError("FRS_PROGRAM_COMPLETION_PRECEDES_SUPPORTING_CLOCK")
    result.update(known_at=finished.isoformat(), review_completed_at=finished.isoformat(), completion_clock_verified=True)
    return result


def review_epa_frs_program_scope(request, captures, *, reviewed_at=None):
    """Pure bounded source-field review; no file, HTTP, DB, quota or registry I/O."""
    started = _clock(_physical_clock())
    selected = _request(request)
    cutoff = _clock(reviewed_at) if reviewed_at is not None else started
    if cutoff > started:
        raise ValueError("FUTURE_FRS_PROGRAM_SOURCE_READ_CUTOFF_UNAVAILABLE")
    if (not isinstance(captures, dict) or not 1 <= len(captures) <= MAX_CAPTURES
            or any(not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", key) for key in captures)):
        raise ValueError("One to four exact FRS original packets required")
    result = {"schema_version": 1, "review_kind": "EPA_FRS_PROGRAM_SCOPE", "contract_version": CONTRACT_VERSION,
        "status": "PARTIAL", "scope": {key: selected[key] for key in
            ("capture_id", "frs_registry_id", "facility_name", "facility_pointer")},
        "review_started_at": started.isoformat(), "source_read_cutoff": cutoff.isoformat(), "reviewed_at": cutoff.isoformat(),
        "source_read_cutoff_is_output_availability": False, "known_at": None,
        "source_review": None, "response_row_inventory_complete": False, "publisher_query_rows": None,
        "returned_facility_rows": None, "facility_identity_supported": False, "facility_field_evidence": [],
        "program_inventory_supported": False, "program_inventory": [], "observed_program_rows": None,
        "selected_program_reviews": [], "all_program_interests_complete": False,
        "negative_facility_or_program_absence_verified": False, "legal_owner_verified": False,
        "permitted_operator_verified": False, "issuer_identity_verified": False,
        "continuous_issuer_exposure_verified": False, "environmental_liability_verified": False,
        "registry_adoption_allowed": False, "historical_availability_reconstructed": False,
        "live_acceptance_verified": False, "scoring_applied": False, "prediction_input": False,
        "http_requests": 0, "database_actions": 0, "quota_or_lease_actions": 0, "missing_evidence": []}
    identifier = selected["capture_id"]
    if identifier not in captures:
        info, payload, reason = {"capture_id": identifier, "original_body_pin_verified": False, "status": "PARTIAL"}, None, "MISSING_SELECTED_ORIGINAL"
    else:
        info, payload, reason = _source(identifier, captures[identifier], cutoff)
    info["reason"] = reason or "WHOLE_ORIGINAL_JSON_PINNED"
    result["source_review"] = info
    if payload is not None:
        try:
            _review_rows(result, selected, payload, info["raw_response_sha256"])
        except _Partial as error:
            result["missing_evidence"].append(str(error))
    else:
        result["missing_evidence"].append(reason)
    result["missing_evidence"].extend(["FACILITY_PROGRAM_RECORD_IS_NOT_LEGAL_OWNERSHIP_OR_OPERATING_PERMISSION",
        "DATED_PROGRAM_INTEREST_MEANING_AND_CONTINUOUS_ISSUER_EXPOSURE_NOT_ESTABLISHED"])
    if not selected["programs"]:
        result["missing_evidence"].append("NO_EXPLICIT_PROGRAM_INTEREST_SELECTION")
    return _finish(result, started)


__all__ = ["CONTRACT_VERSION", "review_epa_frs_program_scope"]
