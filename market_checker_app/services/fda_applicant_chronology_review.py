"""Bounded original-source point comparison, never an ownership interval.

Uses the existing legal-roster and static FDA parsers. A listed row is retained
even in an incomplete roster, while a universe presence/absence state needs an
explicit source-local all-subsidiaries declaration. Missing endpoints, spelling
variants and same-day revisions remain separate observations.
"""
from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
import re

from market_checker_app.collectors import fda_legal_applicant_bridge_review as bridge
from market_checker_app.services.fda_application_product_review import review_fda_application_product, _Page, _within
from market_checker_app.services.fda_structured_product_archive import _clock
from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body

VERSION = "fda-exact-applicant-dated-points-v1"


def _physical_clock():
    return datetime.now(timezone.utc)


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 300:
        raise ValueError("EXACT_BOUNDED_CHRONOLOGY_LITERAL_REQUIRED")
    return value


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("EXPLICIT_CHRONOLOGY_QUERY_DAY_REQUIRED")
    return date.fromisoformat(value)


def _keys(request, roster):
    if roster:
        try:
            keys = [request["filing_key"], request["exhibit_key"], request["application_request"]["source_key"],
                    request["company_request"]["source_key"], request["application_request"]["issuer_key"]]
            if request["company_request"]["issuer_key"] != keys[-1]:
                raise ValueError("INDEPENDENT_COMPANY_ISSUER_KEY_CONFLICT")
        except (KeyError, TypeError) as error:
            raise ValueError("EXPLICIT_ROSTER_SOURCE_KEYS_REQUIRED") from error
    else:
        keys = [request.get("source_key"), request.get("issuer_key")]
    if any(not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", key) for key in keys) or len(set(keys)) != len(keys):
        raise ValueError("EXACT_DISTINCT_CHRONOLOGY_CAPTURE_KEYS_REQUIRED")
    return keys


def _preflight(pair, cutoff):
    if not isinstance(pair, (tuple, list)) or len(pair) != 2 or not isinstance(pair[0], Mapping):
        return "ORIGINAL_CAPTURE_DESCRIPTOR_REQUIRED"
    meta, body = pair
    if (body is None or meta.get("body_sha256") in bridge.WITHHELD
            or meta.get("raw_publication_permitted") is False or meta.get("public_raw_available") is False
            or any(str(meta.get(k, "")).startswith(("WITHHELD", "MISSING", "NOT_CAPTURED"))
                   for k in ("retention", "status", "archive_status", "publication_status"))):
        return "WHOLE_ORIGINAL_UNAVAILABLE_NO_SUBSTITUTE"
    try:
        encoded = _encode(dict(meta))
        if len(encoded) > 64000 or _has_sensitive_body(encoded):
            return "WHOLE_METADATA_UNSAFE_OR_OVER_BOUND"
        if (not isinstance(body, bytes) or not 0 < len(body) <= 4_000_000
                or type(meta.get("body_bytes")) is not int or len(body) != meta["body_bytes"]
                or type(meta.get("byte_budget")) is not int or not len(body) <= meta["byte_budget"] <= 4_000_000
                or hashlib.sha256(body).hexdigest() != meta.get("body_sha256") or _has_sensitive_body(body)):
            return "WHOLE_BODY_HASH_LENGTH_CAP_OR_SAFETY_CONFLICT"
        if not _clock(meta.get("requested_at")) <= _clock(meta.get("observed_at")) <= _clock(meta.get("known_at", meta.get("observed_at"))) <= cutoff:
            return "ORIGINAL_SOURCE_KNOWLEDGE_CLOCK_CONFLICT"
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return "INVALID_WHOLE_ORIGINAL_METADATA_OR_CLOCK"
    return None


def _universe(statement, page, roster, day):
    if statement is None:
        return {"status": "UNKNOWN", "complete": False, "reason": "NO_EXPLICIT_ALL_SUBSIDIARIES_DECLARATION"}
    if (not isinstance(statement, dict) or set(statement) != {"table_index", "row_index", "exact_text"}
            or type(statement["table_index"]) is not int or type(statement["row_index"]) is not int
            or not 0 <= statement["table_index"] < 64 or not 0 <= statement["row_index"] < 3000):
        raise ValueError("EXACT_BOUNDED_UNIVERSE_STATEMENT_LOCATOR_REQUIRED")
    literal = _text(statement["exact_text"])
    expected = f"SUBSIDIARIES OF THE COMPANY: This is a complete list of all subsidiaries of the Company as of {day.strftime('%B')} {day.day}, {day.year}."
    if literal != expected:
        return {"status": "UNKNOWN", "complete": False, "reason": "UNSUPPORTED_OR_DIFFERENT_POINT_UNIVERSE_DECLARATION"}
    tables = [n for n in page.nodes if n.tag == "table"]
    if statement["table_index"] >= len(tables):
        return {"status": "UNKNOWN", "complete": False, "reason": "UNIVERSE_DECLARATION_NOT_IN_ORIGINAL_ROW"}
    rows = [n for n in page.nodes if n.tag == "tr" and _within(n, tables[statement["table_index"]])]
    matching = [row for row in rows if row.closed and not row.hidden and row.text() == literal]
    if (statement["row_index"] >= len(rows) or len(matching) != 1 or rows[statement["row_index"]] is not matching[0]
            or len([cell for cell in matching[0].children if cell.tag == "td"]) != 1):
        return {"status": "UNKNOWN", "complete": False, "reason": "UNIVERSE_DECLARATION_NOT_IN_ORIGINAL_ROW"}
    if (roster["roster_omits_some_subsidiaries"] or re.search(
            r"\b(?:omitting|excluding|except|selected|incomplete)\b|not (?:a )?complete|not exhaustive|(?:certain|principal|major) subsidiaries",
            page.root.text(), re.I)):
        return {"status": "CONFLICT", "complete": False, "reason": "UNIVERSE_DECLARATION_CONFLICTS_WITH_SOURCE_LIMITATION"}
    # The positive declaration is necessary, but cannot license arbitrary
    # surrounding qualification prose merely because a blacklist missed it.
    allowed = {"SUBSIDIARIES OF THE COMPANY", literal,
               f"The following is a list of subsidiaries of the Company as of {day.strftime('%B')} {day.day}, {day.year}."}
    for row in (node for node in page.nodes if node.tag == "tr"):
        cells = [cell for cell in row.children if cell.tag == "td"]
        if len(cells) == 1 and cells[0].text() and cells[0].text() not in allowed:
            return {"status": "UNKNOWN", "complete": False, "reason": "UNSUPPORTED_QUALIFICATION_IN_COMPLETE_UNIVERSE_CONTEXT"}
    return {"status": "DECLARED_COMPLETE_SUBSIDIARY_ROSTER_AT_POINT", "complete": True,
            "statement": deepcopy(statement), "as_of": day.isoformat(),
            "scope": "PUBLISHED_SUBSIDIARY_ROSTER_ONLY", "proves_ultimate_ownership_or_product_assignment": False}


def _entity_state(entity, rows, universe):
    same_name = [row for row in rows if row["legal_entity_name_as_displayed"] == entity["legal_name"]]
    exact = [row for row in same_name if row["jurisdiction_as_displayed"] == entity["jurisdiction"]]
    state = "UNKNOWN_IN_INCOMPLETE_ROSTER"
    if len(same_name) > 1:
        state = "AMBIGUOUS_EXACT_LEGAL_NAME_ROWS"
    elif same_name and not exact:
        state = "JURISDICTION_CONFLICT"
    elif universe["complete"]:
        state = "PRESENT_IN_DECLARED_COMPLETE_ROSTER" if len(exact) == 1 else "ABSENT_FROM_DECLARED_COMPLETE_ROSTER"
    return {"entity": deepcopy(entity), "universe_state": state, "literal_listed_rows": deepcopy(exact),
            "same_exact_name_jurisdictions": [row["jurisdiction_as_displayed"] for row in same_name],
            "literal_row_is_continuous_ownership": False, "spelling_variant_is_alias": False}


def _complete(report, started, children):
    completed = _clock(_physical_clock())
    if completed < started or any(_clock(child[field]) > completed for child in children
                                  for field in ("reviewed_at", "known_at", "ingested_at")):
        raise ValueError("CHRONOLOGY_COMPLETION_CLOCK_PRECEDES_START_OR_SUPPORTING_FACT")
    report.update(review_completed_at=completed.isoformat(), reviewed_at=completed.isoformat(),
                  known_at=completed.isoformat(), ingested_at=completed.isoformat())
    return report


def _application_comparisons(points):
    # A product row absent from one captured version is not an absent/licensed
    # product. Compare literal observed rows only, inside the same source role.
    fields = ("product_number", "proprietary_name", "proper_name", "strength", "dosage_form", "route",
              "dosage_form_route_text", "source_party_name", "source_party_role")
    groups = {}
    for point in points:
        if point.get("source_party_role"):
            groups.setdefault((point["source_kind"], point["source_party_role"]), []).append(point)
    comparisons = []
    for values in groups.values():
        ordered = sorted(values, key=lambda p: (_clock(p["source_observed_at"]), p["id"]))
        for left, right in zip(ordered, ordered[1:]):
            left_rows = {tuple(row[field] for field in fields): row for row in left["product_rows"]}
            right_rows = {tuple(row[field] for field in fields): row for row in right["product_rows"]}
            same = left_rows.keys() == right_rows.keys()
            outcome = "OBSERVED_LITERAL_ROWS_MATCH_ONLY" if same else "OBSERVED_LITERAL_ROW_DIFFERENCE_ONLY"
            if left["source_version_sha256"] == right["source_version_sha256"]:
                outcome = "REOBSERVED_IDENTICAL_APPLICATION_VERSION"
            elif _clock(left["source_observed_at"]) == _clock(right["source_observed_at"]):
                outcome = "SAME_OBSERVATION_CLOCK_DIFFERENT_APPLICATION_VERSIONS"
            comparisons.append({"from_id": left["id"], "to_id": right["id"], "source_kind": left["source_kind"],
                "source_party_role": left["source_party_role"], "outcome": outcome,
                "from_observed_at": left["source_observed_at"], "to_observed_at": right["source_observed_at"],
                "source_party_names": [left["source_party_name"], right["source_party_name"]],
                "only_in_from_capture": [deepcopy(row) for key, row in left_rows.items() if key not in right_rows],
                "only_in_to_capture": [deepcopy(row) for key, row in right_rows.items() if key not in left_rows],
                "from_original_container_closed": left["original_container_explicitly_closed"],
                "to_original_container_closed": right["original_container_explicitly_closed"],
                "product_universe_complete": False, "row_difference_proves_product_absence": False,
                "approval_or_capture_is_ownership_start": False, "ownership_change_or_continuity_verified": False})
    return comparisons


def review_fda_applicant_chronology(request, captures, *, reviewed_at=None):
    """Pure bounded original capture API; never fetch, adopt or fill an interval."""
    started = _clock(_physical_clock())
    if reviewed_at is not None and not isinstance(reviewed_at, datetime):
        raise ValueError("AWARE_CHRONOLOGY_SOURCE_READ_CUTOFF_REQUIRED")
    cutoff = started if reviewed_at is None else _clock(reviewed_at)
    if cutoff > started:
        raise ValueError("FUTURE_CHRONOLOGY_SOURCE_READ_CUTOFF")
    try:
        encoded = _encode(request)
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise ValueError("BOUNDED_EXPLICIT_CHRONOLOGY_REQUEST_REQUIRED") from error
    if (not isinstance(request, dict) or set(request) != {"scope", "entities", "roster_points", "application_points"}
            or len(encoded) > 200000 or _has_sensitive_body(encoded)):
        raise ValueError("BOUNDED_EXPLICIT_CHRONOLOGY_REQUEST_REQUIRED")
    scope = request["scope"]
    if (not isinstance(scope, dict) or set(scope) != {"issuer_cik", "issuer_ticker", "issuer_name", "application_type", "application_number", "applicant_name", "jurisdiction"}
            or not isinstance(scope["issuer_cik"], str) or not re.fullmatch(r"[0-9]{10}", scope["issuer_cik"]) or int(scope["issuer_cik"]) == 0
            or not isinstance(scope["issuer_ticker"], str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,12}", scope["issuer_ticker"])
            or scope["application_type"] != "BLA" or not isinstance(scope["application_number"], str) or not re.fullmatch(r"[0-9]{6}", scope["application_number"])):
        raise ValueError("EXACT_CHRONOLOGY_ISSUER_APPLICATION_SCOPE_REQUIRED")
    for key in ("issuer_name", "applicant_name", "jurisdiction"):
        _text(scope[key])
    entities = request["entities"]
    if (not isinstance(entities, list) or not 1 <= len(entities) <= 8
            or any(not isinstance(e, dict) or set(e) != {"legal_name", "jurisdiction"} for e in entities)):
        raise ValueError("BOUNDED_SEPARATE_EXACT_LEGAL_ENTITIES_REQUIRED")
    pairs = [(_text(e["legal_name"]), _text(e["jurisdiction"])) for e in entities]
    if len(pairs) != len(set(pairs)) or (scope["applicant_name"], scope["jurisdiction"]) not in pairs:
        raise ValueError("DISTINCT_ENTITIES_AND_EXACT_APPLICANT_PAIR_REQUIRED")
    roster_points, app_points = request["roster_points"], request["application_points"]
    if not isinstance(roster_points, list) or not 2 <= len(roster_points) <= 4 or not isinstance(app_points, list) or not 1 <= len(app_points) <= 4:
        raise ValueError("TWO_TO_FOUR_ROSTER_AND_ONE_TO_FOUR_APPLICATION_ENDPOINTS_REQUIRED")
    ids, required = set(), set()
    expected_scope = {key: value for key, value in scope.items() if key != "jurisdiction"}
    app_scope = {key: scope[key] for key in ("issuer_cik", "issuer_ticker", "application_type", "application_number")}
    for roster, points in ((True, roster_points), (False, app_points)):
        for point in points:
            fields = {"id", "requested_roster_as_of", "review_request", "universe_statement"} if roster else {"id", "review_request"}
            if (not isinstance(point, dict) or set(point) != fields or not isinstance(point["id"], str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", point["id"]) or point["id"] in ids):
                raise ValueError("DISTINCT_BOUNDED_CHRONOLOGY_ENDPOINT_IDS_REQUIRED")
            ids.add(point["id"])
            if roster:
                _day(point["requested_roster_as_of"])
            child_request = point["review_request"]
            if child_request is None:
                if roster and point["universe_statement"] is not None:
                    raise ValueError("UNIVERSE_STATEMENT_NEEDS_ITS_ORIGINAL_ROSTER")
                continue
            if (not isinstance(child_request, dict) or child_request.get("scope") != (expected_scope if roster else app_scope)
                    or roster and child_request.get("roster_as_of") != point["requested_roster_as_of"]):
                raise ValueError("EXACT_ENDPOINT_SCOPE_AND_SOURCE_POINT_REQUIRED")
            required.update(_keys(child_request, roster))
    if not isinstance(captures, Mapping) or set(captures) != required or len(captures) > 28:
        raise ValueError("EXPLICIT_UNION_OF_BOUNDED_ENDPOINT_CAPTURES_REQUIRED")
    if sum(len(pair[1]) for pair in captures.values() if isinstance(pair, (tuple, list)) and len(pair) == 2 and isinstance(pair[1], bytes)) > 32_000_000:
        raise ValueError("CHRONOLOGY_TOTAL_ORIGINAL_BYTE_CAP_EXCEEDED")
    issues = {key: reason for key, pair in captures.items() if (reason := _preflight(pair, cutoff))}
    report = {"schema_version": VERSION, "status": "PARTIAL", "data_status": "WAIT_DATA", "review_started_at": started.isoformat(),
        "source_read_cutoff": cutoff.isoformat(), "scope": deepcopy(scope), "source_issues": issues,
        "roster_points": [], "application_points": [], "comparisons": [], "application_comparisons": [], "gaps": [],
        "independent_roster_versions": 0, "independent_roster_dates": 0, "independent_application_versions": 0,
        "ownership_start_verified": False, "continuous_control_verified": False, "product_owner_verified": False,
        "issuer_attribution_allowed": False, "last_observation_carried_forward": False, "spelling_aliases_created": False,
        "approval_date_is_ownership_since": False, "historical_first_availability_inferred": False,
        "registry_adoptions": 0, "new_verified_identities": 0, "current_q3_verified_coverage": 0,
        "network_requests": 0, "DB_or_quota_modified": False, "score_or_model_modified": False, "trading_allowed": False}
    children, fingerprints, valid_dates, app_versions = [], set(), set(), set()
    for point in roster_points:
        out = {"id": point["id"], "requested_roster_as_of": point["requested_roster_as_of"], "source_roster_as_of": None,
            "status": "WAIT_DATA", "independent_source_version": False, "entities": [], "universe": None, "gaps": []}
        report["roster_points"].append(out)
        child_request = point["review_request"]
        if child_request is None:
            out["gaps"].append("ORIGINAL_DATED_ROSTER_ENDPOINT_MISSING")
            continue
        keys = _keys(child_request, True)
        if any(key in issues for key in keys):
            out["gaps"].append("ORIGINAL_ENDPOINT_CAPTURE_UNAVAILABLE_OR_REJECTED")
            continue
        child = bridge.review_fda_legal_applicant_bridge(child_request, {key: captures[key] for key in keys}, reviewed_at=cutoff)
        children.append(child)
        out["source_review"] = child
        if child["roster_facts"] is None:
            out["gaps"].append("DATED_ORIGINAL_ROSTER_SCOPE_NOT_VERIFIED")
            continue
        page = _Page(captures[child_request["exhibit_key"]][1])
        rows, roster = bridge._roster(page, expected_scope, _day(child["roster_facts"]["roster_as_of"]))
        universe = _universe(point["universe_statement"], page, roster, _day(roster["roster_as_of"]))
        fingerprint = tuple(child["source_captures"][key]["body_sha256"] for key in (child_request["filing_key"], child_request["exhibit_key"]))
        independent = fingerprint not in fingerprints
        fingerprints.add(fingerprint)
        valid_dates.add(roster["roster_as_of"])
        out.update(status="ORIGINAL_ROSTER_POINT_FACTS", source_roster_as_of=roster["roster_as_of"], universe=universe,
            independent_source_version=independent, entities=[_entity_state(entity, rows, universe) for entity in entities])
        if not independent:
            out["gaps"].append("DUPLICATE_ROSTER_SOURCE_VERSION_NOT_INDEPENDENT")
    for point in app_points:
        out = {"id": point["id"], "status": "WAIT_DATA", "gaps": [], "source_observed_at": None}
        report["application_points"].append(out)
        child_request = point["review_request"]
        if child_request is None:
            out["gaps"].append("ORIGINAL_APPLICATION_ENDPOINT_MISSING")
            continue
        keys = _keys(child_request, False)
        if any(key in issues for key in keys):
            out["gaps"].append("ORIGINAL_ENDPOINT_CAPTURE_UNAVAILABLE_OR_REJECTED")
            continue
        child = review_fda_application_product(child_request, {key: captures[key] for key in keys}, reviewed_at=cutoff)
        children.append(child)
        out["source_review"] = child
        if child["application_facts"]:
            source = child["source_captures"][child_request["source_key"]]
            version = (child_request["source_kind"], source["source_url"], source["body_sha256"])
            independent = version not in app_versions
            app_versions.add(version)
            out.update(status="ORIGINAL_APPLICATION_ROLE_SNAPSHOT", source_observed_at=source["observed_at"],
                source_kind=child_request["source_kind"], source_version_sha256=source["body_sha256"],
                source_party_role=child["application_facts"]["source_party_role"],
                source_party_name=child["application_facts"]["source_party_name"], product_rows=deepcopy(child["product_rows"]),
                independent_source_version=independent,
                exact_requested_applicant_name_match=child["application_facts"]["source_party_role"] == "BLA_APPLICANT" and child["application_facts"]["source_party_name"] == scope["applicant_name"],
                original_container_explicitly_closed=child["application_facts"].get("detail_container_explicitly_closed"),
                applicant_jurisdiction_verified=False, snapshot_is_roster_or_ownership_start=False)
            out["gaps"].extend(child["gaps"])
            if not independent:
                out["gaps"].append("REOBSERVED_IDENTICAL_APPLICATION_VERSION_NOT_NEW_VERSION")
    ordered = sorted(report["roster_points"], key=lambda p: (p["requested_roster_as_of"], p["id"]))
    for left, right in zip(ordered, ordered[1:]):
        complete_states = {"PRESENT_IN_DECLARED_COMPLETE_ROSTER", "ABSENT_FROM_DECLARED_COMPLETE_ROSTER"}
        for index, entity in enumerate(entities):
            before = left["entities"][index]["universe_state"] if left["entities"] else "NOT_OBSERVED_ENDPOINT"
            after = right["entities"][index]["universe_state"] if right["entities"] else "NOT_OBSERVED_ENDPOINT"
            comparable = before in complete_states and after in complete_states and left["independent_source_version"] and right["independent_source_version"]
            outcome = ("SOURCE_POINT_STATES_MATCH_ONLY" if before == after else "SOURCE_POINT_LISTING_DIFFERENCE_ONLY") if comparable else "WAIT_DATA"
            if comparable and left["source_roster_as_of"] == right["source_roster_as_of"] and before != after:
                outcome = "SAME_DAY_SOURCE_VERSION_CONFLICT"
            report["comparisons"].append({"from_id": left["id"], "to_id": right["id"], "entity": deepcopy(entity),
                "from_state": before, "to_state": after, "outcome": outcome,
                "ownership_change_or_continuity_verified": False, "intervening_days_filled": False})
    report["application_comparisons"] = _application_comparisons(report["application_points"])
    if not any(row["outcome"] != "REOBSERVED_IDENTICAL_APPLICATION_VERSION" for row in report["application_comparisons"]):
        report["gaps"].append("TWO_INDEPENDENT_SAME_ROLE_APPLICATION_ORIGINALS_REQUIRED")
    report.update(independent_roster_versions=len(fingerprints), independent_roster_dates=len(valid_dates), independent_application_versions=len(app_versions))
    if len(fingerprints) >= 2 and len(valid_dates) >= 2:
        report["data_status"] = "DATED_ORIGINAL_POINT_COMPARISON"
    else:
        report["gaps"].append("AT_LEAST_TWO_INDEPENDENT_DATED_ROSTER_ORIGINALS_REQUIRED")
    report["gaps"].extend(["POINT_LISTINGS_NEVER_PROVE_INTERVENING_OR_CURRENT_CONTROL", "PRODUCT_LICENSE_OR_LEGAL_OWNERSHIP_REQUIRES_ITS_OWN_DATED_BRIDGE"])
    return _complete(report, started, children)
