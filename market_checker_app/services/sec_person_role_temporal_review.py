"""Whole-source role/transaction temporal compatibility, never legal attribution."""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import re
from xml.parsers import expat

from market_checker_app.collectors.sec_person_role_claim_review import (
    PersonRoleClaimCapture as PersonRoleTemporalCapture, review_sec_person_role_claims,
)
from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body
from market_checker_app.services.sec_complete_packet_review import parse_complete_sec_packet
from market_checker_app.services.sec_form4_person_bridge import _xml
from market_checker_app.services.sec_owner_instrument_scope_review import _scalar
from market_checker_app.services.sec_person_kind_review import _clock

VERSION = "sec-whole-source-role-transaction-temporal-v1"
MAX_TRANSACTIONS = 128
KINDS = {"nonDerivativeTransaction": "nonDerivativeTable", "derivativeTransaction": "derivativeTable"}


class PersonRoleTemporalReviewError(ValueError):
    pass


def _physical_clock():
    return datetime.now(timezone.utc)


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise PersonRoleTemporalReviewError("Temporal review needs exact ISO source/request days")
    return date.fromisoformat(value)


def _time(value):
    if not isinstance(value, str):
        raise PersonRoleTemporalReviewError("Nested source review needs physical clock strings")
    return _clock(datetime.fromisoformat(value))


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _original_transaction_spans(raw):
    """Collect original byte positions without serializing or normalizing XML."""
    parser = expat.ParserCreate()
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.ExternalEntityRefHandler = lambda *args: 0
    stack = []; rows = []; active = None; nodes = 0
    def start(name, attributes):
        nonlocal active, nodes
        nodes += 1; stack.append(name)
        if nodes > 20_000 or len(stack) > 64:
            raise PersonRoleTemporalReviewError("Original transaction XML exceeds bounded complexity")
        if len(stack)==3 and stack[0]=="ownershipDocument" and name in KINDS and stack[1]==KINDS[name]:
            if active is not None or len(rows)>=MAX_TRANSACTIONS:
                raise PersonRoleTemporalReviewError("Original transaction scope is nested or exceeds its row bound")
            active = {"kind":name,"byte_start":parser.CurrentByteIndex,"date_span":None}
        if active is not None and len(stack)==4 and name=="transactionDate":
            if active["date_span"] is not None:
                raise PersonRoleTemporalReviewError("Original transaction date element is duplicated")
            active["date_span"] = {"byte_start":parser.CurrentByteIndex}
    def end(name):
        nonlocal active
        if active is not None and ((len(stack)==4 and name=="transactionDate") or (len(stack)==3 and name==active["kind"])):
            offset = parser.CurrentByteIndex
            # Empty/self-closing date/transaction elements have no complete
            # original closing context and cannot stand in for a dated row.
            if not raw[offset:].startswith(("</"+name).encode()):
                raise PersonRoleTemporalReviewError("Original transaction/date context has no explicit closing tag")
            closing = raw.find(b">", offset)
            if closing<0: raise PersonRoleTemporalReviewError("Original transaction context is truncated")
            if len(stack)==4:
                active["date_span"]["byte_end"] = closing+1
            else:
                active["byte_end"] = closing+1
                rows.append(active); active = None
        stack.pop()
    parser.StartElementHandler = start; parser.EndElementHandler = end
    parser.Parse(raw, True)
    return rows


def _transaction(packet, account, request):
    parsed = parse_complete_sec_packet(packet.body, expected_accession=account["accession"])
    docs = [item for item in parsed["documents"] if item["document_type"]=="4"]
    if len(docs)!=1: raise PersonRoleTemporalReviewError("Temporal review requires one original Form4 XML document")
    span = docs[0]["ownership_xml_span"]
    raw = packet.body[span["byte_start"]:span["byte_end"]]
    root = _xml(raw)
    spans = _original_transaction_spans(raw)
    counts = {}; all_nodes = {}
    for kind,table in KINDS.items():
        if len(root.findall(table))>1:
            raise PersonRoleTemporalReviewError("Original transaction table is duplicated")
        nodes = root.findall(table+"/"+kind)
        if len(nodes)!=sum(1 for _ in root.iter(kind)):
            raise PersonRoleTemporalReviewError("Transaction elements outside their direct original table cannot be used")
        counts[kind]=len(nodes); all_nodes[kind]=nodes
        if len(nodes)!=sum(item["kind"]==kind for item in spans):
            raise PersonRoleTemporalReviewError("Original transaction byte spans and parsed rows conflict")
    if sum(counts.values())>MAX_TRANSACTIONS:
        raise PersonRoleTemporalReviewError("Original transaction count exceeds bounded review scope")
    kind,ordinal = request["transaction_kind"],request["transaction_ordinal"]
    if ordinal>counts[kind]:
        return None, ["SELECTED_ORIGINAL_TRANSACTION_ROW_ABSENT"], counts
    node = all_nodes[kind][ordinal-1]
    original = [item for item in spans if item["kind"]==kind][ordinal-1]
    date_value = _scalar(node,"transactionDate/value")
    gaps = []
    if date_value is None:
        gaps.append("ORIGINAL_TRANSACTION_DATE_MISSING_NO_PERIOD_OR_REQUEST_SUBSTITUTE")
        transaction_day = None
    else:
        try: transaction_day = _day(date_value)
        except ValueError:
            transaction_day = None; gaps.append("ORIGINAL_TRANSACTION_DATE_INVALID")
    period = _day(_scalar(root,"periodOfReport"))
    filed = _day(parsed["header"]["filed_day"])
    if transaction_day is not None:
        if transaction_day<period: gaps.append("ORIGINAL_TRANSACTION_DATE_BEFORE_REPORT_PERIOD")
        if transaction_day>filed: gaps.append("ORIGINAL_TRANSACTION_DATE_AFTER_FILING_DAY")
    references = [item.get("id") for item in node.findall("transactionDate/footnoteId")]
    if len(references)>16 or any(not isinstance(value,str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,119}",value) for value in references):
        raise PersonRoleTemporalReviewError("Original date qualification references exceed their scope")
    if references: gaps.append("TRANSACTION_DATE_FOOTNOTE_QUALIFICATION_NOT_REVIEWED")
    date_span = original["date_span"]
    if date_span is not None:
        date_raw = raw[date_span["byte_start"]:date_span["byte_end"]]
        if len(date_raw)>8_000: raise PersonRoleTemporalReviewError("Original date context exceeds its quote bound")
    else: date_raw = None
    row_raw = raw[original["byte_start"]:original["byte_end"]]
    match = transaction_day is not None and transaction_day.isoformat()==request["requested_transaction_day"]
    if not match: gaps.append("REQUESTED_DAY_DOES_NOT_MATCH_ORIGINAL_XML_TRANSACTION")
    return {"kind":kind,"ordinal":ordinal,"date_literal":date_value,"date_precision":"DAY" if transaction_day is not None else "UNRESOLVED",
        "requested_transaction_day":request["requested_transaction_day"],"requested_day_matches_original_xml":match,
        "report_period_day":period.isoformat(),"filing_day":filed.isoformat(),
        "original_xml_sha256":span["sha256"],"original_xml_bytes":span["bytes"],
        "original_packet_xml_byte_start":span["byte_start"],"original_packet_xml_byte_end":span["byte_end"],
        "original_packet_row_byte_start":span["byte_start"]+original["byte_start"],
        "original_packet_row_byte_end":span["byte_start"]+original["byte_end"],
        "original_row_sha256":hashlib.sha256(row_raw).hexdigest(),
        "original_date_element_quote":None if date_raw is None else date_raw.decode("utf-8"),
        "original_date_element_sha256":None if date_raw is None else hashlib.sha256(date_raw).hexdigest(),
        "date_qualification_footnote_ids":references,"security_title_as_filed":_scalar(node,"securityTitle/value"),
        "transaction_code_as_filed":_scalar(node,"transactionCoding/transactionCode"),
        "date_is_execution_or_person_identity_proof":False},gaps,counts


def _bounds(claim, anchor, target):
    """Calendar envelopes of literal precision; never effective legal intervals."""
    start = claim["start_assertion"]
    if start["precision"]!="YEAR" or start.get("exact_day") is not None:
        raise PersonRoleTemporalReviewError("Current source-role adapter requires literal YEAR start precision")
    year = start["year"]
    earliest,latest = date(year,1,1),date(year,12,31)
    end = claim["end_assertion"]
    end_bounds = None
    if end is not None and end["precision"]=="YEAR":
        end_bounds = {"earliest_end_day_in_stated_year":date(end["year"],1,1).isoformat(),
            "latest_end_day_in_stated_year":date(end["year"],12,31).isoformat(),
            "precision":"YEAR","boundary_inclusivity":"UNKNOWN","legal_cessation_verified":False}
    elif end is not None and (end["precision"]!="SOURCE_RELATIVE_PRESENT" or end["anchor_filing_day"]!=anchor.isoformat()):
        raise PersonRoleTemporalReviewError("Source-relative present has a conflicting disclosure anchor")
    reasons=[]
    if target<earliest: reasons.append("REQUESTED_DAY_BEFORE_ASSERTED_START_YEAR")
    if target>anchor: reasons.append("REQUESTED_DAY_AFTER_PROXY_ASSERTION_ANCHOR_NO_CONTINUITY")
    if end_bounds is not None and target>_day(end_bounds["latest_end_day_in_stated_year"]):
        reasons.append("REQUESTED_DAY_AFTER_ASSERTED_END_YEAR")
    uncertainty=[]
    if earliest<=target<=latest: uncertainty.append("START_YEAR_APPOINTMENT_DAY_UNRESOLVED")
    if end_bounds is not None and _day(end_bounds["earliest_end_day_in_stated_year"])<=target<=_day(end_bounds["latest_end_day_in_stated_year"]):
        uncertainty.append("END_YEAR_CESSATION_DAY_AND_INCLUSIVITY_UNRESOLVED")
    if reasons:
        fit="OUTSIDE_SOURCE_ASSERTION_SCOPE"; compatible=False
    elif uncertainty:
        fit="YEAR_BOUNDARY_DAY_UNCERTAIN"; compatible=None
    elif target==anchor and end_bounds is None:
        fit="SOURCE_DISCLOSURE_POINT_ASSERTION_ONLY";compatible=True
    else:
        fit="COARSE_LITERAL_RANGE_COMPATIBLE_NO_LEGAL_INTERVAL";compatible=True
    return {"literal_title":claim["title_literal"],"literal_source_clause":claim["source_clause"],
        "assertion_kind":claim["kind"],"start_day_bounds":{"earliest_start_day_in_stated_year":earliest.isoformat(),
            "latest_start_day_in_stated_year":latest.isoformat(),"precision":"YEAR","exact_appointment_day":None,
            "legal_appointment_verified":False},"stated_end_day_bounds":end_bounds,
        "bounds_are_precision_envelopes_not_effective_role_dates":True,
        "source_extent_upper_day":anchor.isoformat(),"source_extent_upper_is_cessation_date":False,
        "present_or_since_does_not_extend_through_capture_or_current_form4":True,
        "source_temporal_fit":fit,"source_assertion_temporally_compatible":compatible,
        "exclusion_reasons":reasons,"precision_uncertainties":uncertainty,
        "continuous_legal_interval_established":False,"exact_role_at_day_established":False}


def review_sec_person_role_temporal(*, primary_capture: PersonRoleTemporalCapture,
        index_capture: PersonRoleTemporalCapture, account_packet_capture: PersonRoleTemporalCapture,
        selection: dict, temporal_request: dict, knowledge_at: datetime | None = None) -> dict:
    """Rebuild pinned role/account facts and assess one original transaction day."""
    started=_clock(_physical_clock());cutoff=started if knowledge_at is None else _clock(knowledge_at)
    if cutoff>started: raise PersonRoleTemporalReviewError("Future temporal interpretation knowledge is unavailable")
    report={"review_version":VERSION,"status":"PARTIAL","role_at_transaction_day_status":"UNVERIFIED",
        "source_temporal_assessment_supported":False,"requested_transaction_binding_supported":False,
        "requested_knowledge_at":None if knowledge_at is None else cutoff.isoformat(),"review_ingested_at":started.isoformat(),
        "natural_person_account_binding_verified":False,"approved_name_alias":False,"exact_role_interval_verified":False,
        "continuous_role_verified":False,"instrument_identity_verified":False,"cusip_class_validity_verified":False,
        "historical_machine_availability_inferred":False,"execution_verified":False,"issuer_attribution_allowed":False,
        "registry_modified":False,"scoring_applied":False,"trading_allowed":False,"full_specialist_acceptance":False,
        "network_requests_performed":0,"quota_or_lease_modified":False,"gaps":[]}
    base_fields=set(report);nested_available=None
    def finish():
        ended=_clock(_physical_clock())
        if ended<started: raise PersonRoleTemporalReviewError("Physical temporal interpretation clock rolled back")
        if nested_available is not None and nested_available>ended:
            raise PersonRoleTemporalReviewError("Source-role interpretation completes after outer physical completion")
        if knowledge_at is not None and cutoff>=started and ended>cutoff:
            for key in set(report)-base_fields-{"captures"}:del report[key]
            report.update(source_temporal_assessment_supported=False,requested_transaction_binding_supported=False,
                gaps=["INTERPRETATION_COMPLETED_AFTER_REQUESTED_KNOWLEDGE_CUTOFF"])
        report["review_available_at"]=ended.isoformat();report["known_at"]=ended.isoformat()
        return report
    if cutoff<started:
        report["gaps"]=["AFTER_PHYSICAL_REVIEW_KNOWLEDGE_CUTOFF"];return finish()
    captures={"primary":primary_capture,"index":index_capture,"account_packet":account_packet_capture}
    for capture in captures.values():
        if not isinstance(capture,PersonRoleTemporalCapture):raise PersonRoleTemporalReviewError("Temporal review requires original captures, never a supplied report")
        capture.validate()
    if len({capture.record_id for capture in captures.values()})!=3:
        raise PersonRoleTemporalReviewError("Original temporal capture IDs are duplicated")
    if any(capture.available_at>cutoff for capture in captures.values()):
        report["gaps"]=["ORIGINAL_INPUT_NOT_YET_KNOWN"];return finish()
    report["captures"]={key:capture.summary() for key,capture in captures.items()}
    for key,capture in captures.items():
        if capture.retention!="ORIGINAL_BYTES" or _has_sensitive_body(capture.body):
            if capture.retention=="ORIGINAL_BYTES":report["captures"][key]["retention"]="WITHHELD_SENSITIVE_BODY"
            report["gaps"]=["REQUIRED_WHOLE_ORIGINAL_UNAVAILABLE_NO_SUBSTITUTE"];return finish()
    keys={"role_title_literal","transaction_kind","transaction_ordinal","requested_transaction_day"}
    if (not isinstance(temporal_request,dict) or set(temporal_request)!=keys
            or not isinstance(temporal_request["role_title_literal"],str) or not 1<=len(temporal_request["role_title_literal"])<=250
            or temporal_request["role_title_literal"]!=" ".join(temporal_request["role_title_literal"].split())
            or not isinstance(temporal_request["transaction_kind"],str) or temporal_request["transaction_kind"] not in KINDS
            or type(temporal_request["transaction_ordinal"]) is not int
            or not 1<=temporal_request["transaction_ordinal"]<=MAX_TRANSACTIONS):
        raise PersonRoleTemporalReviewError("Choose one bounded literal role and original transaction row")
    target=_day(temporal_request["requested_transaction_day"])
    if _has_sensitive_body(json.dumps(temporal_request).encode()):
        raise PersonRoleTemporalReviewError("Unsafe temporal request is withheld")
    # This is a fresh whole-byte review, never a caller-supplied claim report.
    source=review_sec_person_role_claims(primary_capture=primary_capture,index_capture=index_capture,
        account_packet_capture=account_packet_capture,selection=selection)
    nested_started=_time(source["review_ingested_at"]);nested_available=_time(source["review_available_at"])
    if not started<=nested_started<=nested_available:
        raise PersonRoleTemporalReviewError("Source-role physical interpretation clock precedes outer start or rolls back")
    report["source_role_review"]={"review_version":source["review_version"],"review_report_sha256":_digest(source),
        "review_ingested_at":source["review_ingested_at"],"review_available_at":source["review_available_at"],
        "rebuilt_from_whole_originals":True,"caller_report_accepted":False}
    if not source["source_role_claims_supported"] or not source.get("dated_role_source_clock_consistent",False):
        report["gaps"]=["DATED_SOURCE_ROLE_ASSERTION_UNAVAILABLE",*source["gaps"]];return finish()
    claims=[claim for claim in source["role_claims"] if claim["title_literal"]==temporal_request["role_title_literal"]
            and claim["kind"]!="GENERIC_CAREER_POSITION_RANGE_ASSERTION"]
    if len(claims)!=1:raise PersonRoleTemporalReviewError("Selected literal named-role assertion is absent or ambiguous")
    transaction,gaps,counts=_transaction(account_packet_capture,source["comparison_account_facts"],temporal_request)
    anchor=_day(source["source_document"]["filing_day"])
    bounds=_bounds(claims[0],anchor,target)
    report.update(selected_role_temporal_assertion=bounds,transaction=transaction,original_transaction_row_counts=counts,
        requested_transaction_binding_supported=transaction is not None and transaction["requested_day_matches_original_xml"] and not gaps,
        source_temporal_assessment_supported=transaction is not None and not gaps,
        source_document=source["source_document"],source_issuer_scope=source["source_issuer_scope"],
        source_account=source["comparison_account_facts"],source_comparison=source["source_comparison"],
        role_assertion_provenance={"person_clause":source["named_person_clause"],
            "career_context_sha256":source["issuer_career_clause"]["original_context_sha256"],
            "career_locator":source["issuer_career_clause"]["locator"],
            "career_original_byte_start":source["issuer_career_clause"]["original_context_byte_start"],
            "career_original_byte_end":source["issuer_career_clause"]["original_context_byte_end"]},
        source_inputs_available_at=max(capture.available_at for capture in captures.values()).isoformat(),
        as_filed_account_role_is_transaction_day_role_proof=False,
        proxy_financial_period_used_as_role_validity_interval=False)
    report["gaps"]=[*gaps,*bounds["exclusion_reasons"],*bounds["precision_uncertainties"]]
    if source["source_document"]["publisher_size_conflict"]:
        report["gaps"].append("PUBLISHER_INDEX_SERVED_BODY_SIZE_CONFLICT_PRESERVED")
    report["gaps"].extend(["INDEPENDENT_EXACT_PERSON_ACCOUNT_IDENTITY_NOT_SUPPLIED",
        "INDEPENDENT_EXACT_ROLE_INTERVAL_NOT_SUPPLIED","ACCOUNT_ROLE_IS_AS_FILED_NOT_ROLE_AT_TRANSACTION_PROOF",
        "YEAR_ENDPOINTS_AND_ROLE_CONTINUITY_UNVERIFIED","SOURCE_CAPTURE_DOES_NOT_ESTABLISH_HISTORICAL_AVAILABILITY",
        "DECLARED_TRANSACTION_DATE_IS_NOT_EXECUTION_PROOF"])
    return finish()
