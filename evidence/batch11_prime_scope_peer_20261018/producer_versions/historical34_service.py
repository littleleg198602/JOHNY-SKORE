"""Read-only original prime-page lineage, missing pins and completeness limits.

A terminal offset traversal is an observation of pages, not a publisher snapshot,
revision history, negative issuer case or verified recipient relationship. This
module performs no file, network, database, quota or registry operation.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
import hashlib
import re

from market_checker_app.services.usaspending_prime_archive_recovery import (
    CAPTURE_SCHEMA, _Rejected, _canonical, _clock, _json, _modified_clock, _request,
)
from market_checker_app.services.usaspending_scout_service import API_URL, FIELDS


REVIEW_VERSION = "usaspending-original-prime-scope-v1"
MAX_BODY_BYTES = 4_000_000
MAX_MANIFEST_BYTES = 64_000
MAX_TOTAL_BYTES = 16_000_000
MAX_REQUEST_BYTES = 48_000
MAX_PAGES = 5
MAX_ROWS = 500
_HASH = re.compile(r"[a-f0-9]{64}")
_KEY = re.compile(r"[A-Za-z0-9_-]{1,80}")


def _fail(reason):
    raise _Rejected(reason)


def _physical_clock():
    return datetime.now(timezone.utc)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _arguments(request, captures, physical):
    if (not isinstance(request, Mapping) or set(request) != {"query", "recipient", "selected_award_ids", "pages", "legacy_payloads"}
            or len(_canonical(dict(request)).encode()) > MAX_REQUEST_BYTES):
        _fail("INVALID_BOUNDED_PRIME_SCOPE_REQUEST")
    _json(_canonical(dict(request)).encode())
    recipient = request["recipient"]
    if (not isinstance(recipient, dict) or set(recipient) != {"uei", "recipient_name"}
            or not isinstance(recipient["uei"], str) or re.fullmatch(r"[A-Z0-9]{12}",recipient["uei"]) is None
            or not isinstance(recipient["recipient_name"], str) or not 1 <= len(recipient["recipient_name"]) <= 300):
        _fail("INVALID_EXPLICIT_RECIPIENT_SCOPE")
    query = request["query"]
    if query is not None:
        if not isinstance(query, dict) or "page" in query:
            _fail("EXACT_PAGE_INDEPENDENT_PRIME_QUERY_REQUIRED")
        _,uei = _request({**query,"page":1})
        if uei != recipient["uei"]: _fail("QUERY_RECIPIENT_UEI_CONFLICT")
    awards = request["selected_award_ids"]
    if (not isinstance(awards,list) or len(awards)>20 or len(set(awards))!=len(awards)
            or any(not isinstance(value,str) or re.fullmatch(r"CONT_AWD_[A-Za-z0-9_-]{1,990}",value) is None for value in awards)):
        _fail("INVALID_EXPLICIT_AWARD_SELECTION")
    if (not isinstance(request["pages"],list) or not 1<=len(request["pages"])<=MAX_PAGES
            or not isinstance(request["legacy_payloads"],list) or len(request["legacy_payloads"])>2
            or not isinstance(captures,Mapping) or len(captures)>12):
        _fail("PRIME_SCOPE_PAGE_OR_CAPTURE_BOUND")
    referenced, pages = set(), set()
    for item in request["pages"]:
        if (not isinstance(item,dict) or set(item)!={"page","manifest_capture","manifest_sha256","body_capture","body_sha256"}
                or type(item["page"]) is not int or not 1<=item["page"]<=20 or item["page"] in pages):
            _fail("INVALID_OR_DUPLICATE_EXPECTED_PRIME_PAGE")
        pages.add(item["page"])
        for kind in ("manifest","body"):
            key,pin=item[kind+"_capture"],item[kind+"_sha256"]
            if not isinstance(key,str) or not _KEY.fullmatch(key) or key in referenced or pin is not None and (not isinstance(pin,str) or not _HASH.fullmatch(pin)):
                _fail("INVALID_OR_REUSED_PRIME_CAPTURE_PIN")
            referenced.add(key)
    for item in request["legacy_payloads"]:
        if (not isinstance(item,dict) or set(item)!={"capture","sha256","json_pointer"}
                or not isinstance(item["capture"],str) or not _KEY.fullmatch(item["capture"]) or item["capture"] in referenced
                or not isinstance(item["sha256"],str) or not _HASH.fullmatch(item["sha256"])
                or not isinstance(item["json_pointer"],str) or re.fullmatch(r"(?:/[A-Za-z0-9_-]{1,80}){1,8}",item["json_pointer"]) is None):
            _fail("INVALID_LEGACY_REPORT_PIN_OR_POINTER")
        referenced.add(item["capture"])
    if set(captures)-referenced: _fail("UNSELECTED_CAPTURE_CANNOT_SUPPLY_PRIME_SCOPE")
    total=0
    for key,pair in captures.items():
        if not isinstance(pair,(tuple,list)) or len(pair)!=2 or not isinstance(pair[0],Mapping):
            _fail("INVALID_PRIME_CAPTURE_DESCRIPTOR")
        encoded=_canonical(dict(pair[0])).encode()
        if len(encoded)>MAX_MANIFEST_BYTES: _fail("PRIME_CAPTURE_METADATA_BOUND")
        _json(encoded)
        clocks={field:_clock(pair[0][field]) for field in ("observed_at","known_at","available_at","ingested_at","first_ingested_at")
                if pair[0].get(field) is not None}
        if any(value>physical for value in clocks.values()): _fail("RETAINED_ARTIFACT_METADATA_AFTER_REVIEW")
        if clocks.get("known_at",physical)<clocks.get("observed_at",clocks.get("known_at",physical)):
            _fail("RETAINED_ARTIFACT_METADATA_CLOCK_REVERSED")
        if pair[1] is not None:
            if not isinstance(pair[1],bytes) or len(pair[1])>MAX_BODY_BYTES: _fail("PRIME_WHOLE_BODY_BOUND")
            total+=len(pair[1])
        if total>MAX_TOTAL_BYTES: _fail("PRIME_TOTAL_INPUT_BYTE_BOUND")
    return query


def _missing(page, kind, key, pin, query, reason):
    return {"page":page,"kind":kind,"capture":key,"expected_sha256":pin,
        "hash_pin_known":pin is not None,"expected_request_sha256":_digest({**query,"page":page}) if query else None,
        "reason":reason}


def _whole(captures,key,pin,maximum):
    pair=captures.get(key)
    if pair is None: return None
    meta,body=pair
    withheld=(meta.get("body_withheld") is True or meta.get("withheld") is True
        or meta.get("public_raw_available") is False or meta.get("retention") in {"WITHHELD_SENSITIVE_BODY","MISSING_BODY"}
        or any(str(meta.get(field,"")).startswith("WITHHELD") for field in ("status","archive_status","publication_status")))
    if withheld:
        if body is not None: _fail("WITHHELD_WHOLE_BODY_CANNOT_HAVE_A_SUBSTITUTE")
        return None
    if body is None: return None
    if pin is None: _fail("AVAILABLE_PRIME_ARTIFACT_REQUIRES_EXACT_HASH_PIN")
    if len(body)>maximum or hashlib.sha256(body).hexdigest()!=pin:
        _fail("ORIGINAL_PRIME_ARTIFACT_HASH_OR_BOUND_MISMATCH")
    return body


def _legacy(item,captures,physical):
    raw=_whole(captures,item["capture"],item["sha256"],MAX_BODY_BYTES)
    if raw is None: return {"capture":item["capture"],"available":False,"original_http_body":False}
    value=_json(raw);parent=None
    for part in item["json_pointer"].split("/")[1:]:
        parent=value;value=value[int(part)] if isinstance(value,list) else value[part]
    if not isinstance(value,dict) or value.get("spending_level")!="awards" or not isinstance(value.get("results"),list):
        _fail("LEGACY_POINTER_IS_NOT_AN_EMBEDDED_PRIME_PAGE")
    paging=value.get("page_metadata",{})
    if type(paging.get("page")) is not int or type(paging.get("hasNext")) is not bool:
        _fail("INVALID_LEGACY_PAGE_DESCRIPTION")
    observed=parent.get("observed_at") if isinstance(parent,dict) else None
    if observed is not None and _clock(observed)>physical: _fail("LEGACY_REPORT_CLOCK_AFTER_REVIEW")
    return {"capture":item["capture"],"whole_report_sha256":item["sha256"],"json_pointer":item["json_pointer"],
        "available":True,"reported_page":paging["page"],"reported_has_next":paging["hasNext"],"reported_rows":len(value["results"]),
        "embedded_case_observed_at":observed,"original_http_body":False,"whole_original_request_present":False,
        "publisher_revision_semantics_verified":False,"may_supply_missing_prime_pages":False}


def _page(item,captures,query,physical):
    manifest=_whole(captures,item["manifest_capture"],item["manifest_sha256"],MAX_MANIFEST_BYTES)
    body=_whole(captures,item["body_capture"],item["body_sha256"],MAX_BODY_BYTES)
    missing=[]
    for kind,raw in (("manifest",manifest),("body",body)):
        if raw is None: missing.append(_missing(item["page"],kind.upper(),item[kind+"_capture"],item[kind+"_sha256"],query,"WHOLE_ORIGINAL_PRIME_ARTIFACT_UNAVAILABLE"))
    if missing: return None,missing
    meta=_json(manifest)
    if not isinstance(meta,dict): _fail("INVALID_ORIGINAL_PRIME_MANIFEST")
    if (meta.get("body_withheld") is True or meta.get("withheld") is True
            or any(str(meta.get(key,"")).startswith("WITHHELD") for key in ("status","archive_status","publication_status"))):
        _fail("WITHHELD_PRIME_MANIFEST_CANNOT_RESTORE_BODY")
    mode=meta.get("capture_mode")
    if meta.get("schema_version")!=CAPTURE_SCHEMA or mode not in {"LIVE","FIXTURE"}:
        _fail("TRANSACTION_REPLAY_OR_STORE_ROWS_ARE_NOT_ORIGINAL_PRIME_PAGES")
    if (meta.get("source_url")!=API_URL or meta.get("source_url_final")!=API_URL or meta.get("method")!="POST"
            or type(meta.get("http_status")) is not int or meta["http_status"]!=200 or meta.get("complete_response") is not True
            or meta.get("error_type") or meta.get("body_transformed",False) is not False or meta.get("content_encoding") not in (None,"identity")):
        _fail("UNSUCCESSFUL_OR_TRANSFORMED_ORIGINAL_PRIME_PAGE")
    actual,uei=_request(meta.get("request"))
    if actual["page"]!=item["page"] or query is not None and actual!={**query,"page":item["page"]}:
        _fail("ORIGINAL_REQUEST_FILTER_PAGE_LINEAGE_CONFLICT")
    if (meta.get("response_sha256")!=item["body_sha256"] or type(meta.get("response_bytes")) is not int
            or meta["response_bytes"]!=len(body) or meta.get("body_file")!=f"body-{item['body_sha256']}.response"):
        _fail("PRIME_MANIFEST_BODY_PIN_OR_LENGTH_CONFLICT")
    started,completed=_clock(meta.get("request_started_at")),_clock(meta.get("response_completed_at"))
    known=_clock(meta.get("known_at",completed))
    if not started<=completed<=known<=physical: _fail("ORIGINAL_PRIME_CAPTURE_OR_KNOWLEDGE_CLOCK_CONFLICT")
    if date.fromisoformat(actual["filters"]["time_period"][0]["end_date"])>started.date(): _fail("PRIME_QUERY_WINDOW_AFTER_CAPTURE")
    headers=meta.get("response_headers",{})
    if not isinstance(headers,dict) or len({key.lower() for key in headers})!=len(headers): _fail("INVALID_PRIME_RESPONSE_HEADERS")
    headers={key.lower():value for key,value in headers.items()}
    if headers.get("content-encoding") not in (None,"identity"): _fail("TRANSFORMED_PRIME_RESPONSE_ENCODING")
    if "content-length" in headers and headers["content-length"]!=str(len(body)): _fail("PRIME_CONTENT_LENGTH_CONFLICT")
    payload=_json(body)
    if (not isinstance(payload,dict) or payload.get("spending_level")!="awards" or type(payload.get("limit")) is not int
            or payload["limit"]!=actual["limit"] or not isinstance(payload.get("results"),list) or len(payload["results"])>actual["limit"]):
        _fail("NOT_A_BOUNDED_PRIME_AWARD_RESPONSE")
    paging=payload.get("page_metadata")
    if (not isinstance(paging,dict) or type(paging.get("page")) is not int or paging["page"]!=actual["page"]
            or type(paging.get("hasNext")) is not bool or paging["hasNext"] and not payload["results"]):
        _fail("INVALID_PRIME_RESPONSE_PAGINATION")
    for field,value in (("next",actual["page"]+1 if paging["hasNext"] else None),("previous",actual["page"]-1 if actual["page"]>1 else None),("hasPrevious",actual["page"]>1)):
        if field in paging and (paging[field]!=value or type(paging[field]) is not type(value)):
            _fail("PRIME_NEXT_PREVIOUS_LINEAGE_CONFLICT")
    total=paging.get("total")
    if total is not None and (type(total) is not int or not 0<=total<=MAX_ROWS): _fail("INVALID_REPORTED_PRIME_TOTAL")
    retained_clocks=[_clock(captures[key][0][field]) for key in (item["manifest_capture"],item["body_capture"])
                     for field in ("observed_at","known_at","available_at","ingested_at","first_ingested_at") if captures[key][0].get(field) is not None]
    retained_known=max(retained_clocks) if retained_clocks else None
    return {"reference":{**item,"request":actual,"request_sha256":_digest(actual),"request_started_at":started.isoformat(),
        "response_completed_at":completed.isoformat(),"original_response_known_at":known.isoformat(),
        "retained_artifact_known_at":retained_known.isoformat() if retained_known else None,
        "source_known_at":max(known,retained_known or known).isoformat(),"body_bytes":len(body),"capture_origin":mode,
        "reported_rows":len(payload["results"]),"has_next":paging["hasNext"],"reported_total":total},
        "rows":payload["results"],"started":started,"completed":completed,"uei":uei},[]


def _observations(page,recipient):
    result=[]
    for position,raw in enumerate(page["rows"]):
        if not isinstance(raw,dict) or len(_canonical(raw).encode())>32_000 or any(key not in raw for key in FIELDS):
            _fail("INVALID_OR_PROJECTED_PRIME_ROW")
        generated,award=raw["generated_internal_id"],raw["Award ID"]
        if (not isinstance(generated,str) or re.fullmatch(r"CONT_AWD_[A-Za-z0-9_-]{1,990}",generated) is None
                or not isinstance(award,str) or re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{0,199}",award) is None
                or not generated.startswith(f"CONT_AWD_{award}_") or raw["Recipient UEI"]!=recipient["uei"]
                or raw["Recipient Name"]!=recipient["recipient_name"]): _fail("PRIME_ROW_AWARD_OR_RECIPIENT_SCOPE_CONFLICT")
        result.append({"generated_internal_id":generated,"award_id":award,"recipient_uei":raw["Recipient UEI"],
            "source_page":page["reference"]["page"],"row_index":position,"source_row_sha256":_digest(raw),
            "modified_clock":_modified_clock(raw["Last Modified Date"],page["completed"]),
            "publisher_revision_order_verified":False})
    return result


def review_usaspending_prime_scope(request: Mapping, captures: Mapping, *, as_of: datetime | None = None) -> dict:
    """Audit one explicit original page chain; never import, fetch or infer absence.

    Captures map selected names to (metadata, whole bytes or None). Manifest and
    response bodies each have independent pins. ``query=None`` records a missing
    original request, never reconstructs it from a parsed historical report.
    """
    started=_clock(_physical_clock())
    report={"schema_version":REVIEW_VERSION,"status":"REJECTED","reason":"","review_started_at":started.isoformat(),
        "review_ingested_at":None,"review_available_at":None,"knowledge_at":None,"as_of":None,
        "pages":[],"legacy_observations":[],"missing_evidence":[],"award_observations":[],
        "observed_page_chain_complete":False,"synthetic_page_chain_complete":False,"query_complete":False,
        "reported_totals_stable":False,"total_count_semantics_verified":False,"publisher_revision_semantics_verified":False,
        "source_sort_order_verified":False,
        "pagination_snapshot_guaranteed":False,"source_revision_history_complete":False,"first_or_last_revision_verified":False,
        "genuine_negative_verified":False,"selected_award_absence_verified":False,"issuer_coverage_complete":False,
        "earlier_uei_relationship_inferred":False,"recipient_legal_identity_verified":False,
        "historical_availability_reconstructed":False,"source_manifest_is_publisher_signed":False,
        "source_http_requests":0,"collector_requests":0,"db_operations":0,"quota_operations":0,"registry_adoptions":0,
        "scoring_applied":False,"trading_allowed":False,"revenue_or_cumulative_difference_inferred":False}
    cutoff=None
    try:
        cutoff=_clock(as_of) if as_of is not None else None
        report["source_read_cutoff"]=(cutoff or started).isoformat()
        if cutoff is not None and cutoff>started: _fail("FUTURE_REVIEW_CUTOFF_UNAVAILABLE")
        if cutoff is not None and cutoff<started: _fail("AFTER_PHYSICAL_REVIEW_CUTOFF")
        query=_arguments(request,captures,cutoff or started)
        report.update(query_scope=query,recipient_scope=dict(request["recipient"]),selected_award_ids=list(request["selected_award_ids"]))
        report["legacy_observations"]=[_legacy(item,captures,cutoff or started) for item in request["legacy_payloads"]]
        if query is None:
            report["missing_evidence"].append({"kind":"WHOLE_ORIGINAL_REQUEST","expected_sha256":None,"hash_pin_known":False,
                "reason":"EXACT_FILTER_WINDOW_SORT_AND_REQUEST_LINEAGE_NOT_RETAINED"})
        pages=[]
        for item in sorted(request["pages"],key=lambda value:value["page"]):
            page,missing=_page(item,captures,query,cutoff or started)
            report["missing_evidence"].extend(missing)
            if page is not None: pages.append(page)
        observations=[];seen=set();last=None;terminal=False;totals=[];origins=set();expected=1
        for page in pages:
            ref=page["reference"];report["pages"].append(ref);origins.add(ref["capture_origin"])
            if len(origins)>1: _fail("FIXTURE_AND_ORIGINAL_PRIME_ORIGINS_CANNOT_BE_MIXED")
            if query is None or {key:value for key,value in ref["request"].items() if key!="page"}!=query:
                _fail("WHOLE_ORIGINAL_REQUEST_SCOPE_NOT_EXPLICITLY_PINNED")
            if page["uei"]!=request["recipient"]["uei"]: _fail("PAGE_RECIPIENT_QUERY_SCOPE_CONFLICT")
            if terminal: _fail("EXTRA_PAGES_AFTER_ORIGINAL_TERMINAL_RESPONSE")
            if last is not None and page["started"]<last: _fail("OVERLAPPING_OR_REORDERED_PRIME_CAPTURE_CLOCKS")
            last=page["completed"]
            if ref["page"]!=expected:
                for missing_page in range(expected,ref["page"]):
                    report["missing_evidence"].append(_missing(missing_page,"PAGE",None,None,query,"WHOLE_PRIOR_PAGE_LINEAGE_MISSING"))
            expected=ref["page"]+1;terminal=not ref["has_next"];totals.append(ref["reported_total"])
            for observation in _observations(page,request["recipient"]):
                if observation["generated_internal_id"] in seen: _fail("DUPLICATE_OR_CHANGED_AWARD_ACROSS_OFFSET_PAGES")
                if len(observations)>=MAX_ROWS: _fail("PRIME_REVIEW_ROW_BOUND")
                seen.add(observation["generated_internal_id"]);observations.append(observation)
        if pages and not terminal:
            report["missing_evidence"].append(_missing(expected,"PAGE",None,None,query,"WHOLE_NEXT_PAGE_REQUIRED_BY_HAS_NEXT"))
        if pages and any(total is not None for total in totals):
            if len(set(value for value in totals if value is not None))>1: _fail("REPORTED_TOTAL_CHANGED_DURING_OFFSET_TRAVERSAL")
            if all(total is not None for total in totals):
                report["reported_totals_stable"]=True
                if terminal and not report["missing_evidence"] and totals[0]!=len(observations): _fail("REPORTED_TOTAL_DOES_NOT_MATCH_ENUMERATED_UNIQUE_AWARDS")
        if pages and not report["reported_totals_stable"]:
            report["missing_evidence"].append({"kind":"STABLE_ORIGINAL_TOTAL","expected_sha256":None,"hash_pin_known":False,
                "reason":"TOTAL_IS_ABSENT_OR_NOT_REPORTED_ON_EVERY_ORIGINAL_PAGE"})
        chain=bool(pages) and terminal and not report["missing_evidence"]
        fixture=origins=={"FIXTURE"}
        report.update(award_observations=observations,reported_unique_awards=len(observations) if pages else None,
            observed_page_chain_complete=chain and not fixture,synthetic_page_chain_complete=chain and fixture,
            selected_award_presence=[{"generated_internal_id":value,"observation":"PRESENT_IN_RETAINED_PAGE" if value in seen else "NOT_OBSERVED_IN_AVAILABLE_PAGES" if pages else "UNKNOWN_NO_ORIGINAL_PRIME_PAGES",
                "absence_verified":False} for value in request["selected_award_ids"]],
            source_origin="SYNTHETIC_CONTROL" if fixture else "ORIGINAL_CAPTURE_RECORDS" if pages else "NO_ORIGINAL_PRIME_PAGES",
            source_inputs_known_at=max((page["reference"]["source_known_at"] for page in pages),default=None),
            query_completeness_blockers=["NO_PUBLISHER_PINNED_SNAPSHOT_OR_TOTAL_REVISION_SEMANTICS",
                "OFFSET_TERMINAL_PAGE_IS_NOT_FULL_HISTORY_OR_GENUINE_NEGATIVE_EVIDENCE"])
        report["status"]="SYNTHETIC_COMPLETE_CONTROL" if chain and fixture else "TRAVERSAL_REVIEWED" if chain else "WAIT"
        report["reason"]="" if chain else "ORIGINAL_PRIME_SCOPE_EVIDENCE_INCOMPLETE"
    except _Rejected as error:
        report.update(status="WAIT" if str(error)=="AFTER_PHYSICAL_REVIEW_CUTOFF" else "REJECTED",reason=str(error),
                      award_observations=[],observed_page_chain_complete=False,synthetic_page_chain_complete=False)
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError,RecursionError) as error:
        report.update(status="REJECTED",reason="INVALID_PRIME_SCOPE_REVIEW_INPUT",error_type=type(error).__name__,
                      award_observations=[],observed_page_chain_complete=False,synthetic_page_chain_complete=False)
    completed=_clock(_physical_clock())
    if completed<started or cutoff is not None and completed>cutoff:
        report.update(status="WAIT",reason="PHYSICAL_REVIEW_CLOCK_MOVED_BACKWARDS" if completed<started else "AFTER_PHYSICAL_REVIEW_CUTOFF",
                      award_observations=[],pages=[],legacy_observations=[],selected_award_presence=[],reported_unique_awards=None,
                      source_inputs_known_at=None,reported_totals_stable=False,
                      observed_page_chain_complete=False,synthetic_page_chain_complete=False)
    if completed<started:
        return {**report,"raw_completion_clock":completed.isoformat(),"as_of":cutoff.isoformat() if cutoff else None}
    report.update(review_ingested_at=completed.isoformat(),review_available_at=completed.isoformat(),knowledge_at=completed.isoformat(),
                  as_of=(cutoff or completed).isoformat())
    return report


__all__=["REVIEW_VERSION","review_usaspending_prime_scope"]
