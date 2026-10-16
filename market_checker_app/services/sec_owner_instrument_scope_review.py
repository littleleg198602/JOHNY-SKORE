"""Bounded original SEC account/instrument/role facts, never legal identity.

Matching dated reporting accounts do not prove a natural person, an approved
alias, a CUSIP/class interval, employment continuity, or transaction execution.
No HTTP, store, registry or model is touched by this review.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body
from market_checker_app.services.sec_form4_person_bridge import _field, _packet, _xml
from market_checker_app.services.sec_person_kind_review import _Index, _clock, _norm, _url

VERSION = "sec-original-owner-instrument-role-scope-v1"
MAX_BYTES = 4_000_000
ROLE_FIELDS = ("isDirector", "isOfficer", "isTenPercentOwner", "isOther", "officerTitle")


class OwnerScopeReviewError(ValueError):
    pass


def _physical_clock():
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class OwnerInstrumentCapture:
    record_id: str
    source_url: str
    body: bytes | None
    sha256: str | None
    size_bytes: int | None
    observed_at: datetime
    known_at: datetime | None = None
    retention: str = "ORIGINAL_BYTES"

    @property
    def available_at(self):
        observed = _clock(self.observed_at)
        known = _clock(self.observed_at if self.known_at is None else self.known_at)
        if known < observed: raise OwnerScopeReviewError("Owner scope cannot be known before its capture")
        return known

    def validate(self):
        _url(self.source_url); self.available_at
        if not isinstance(self.record_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", self.record_id):
            raise OwnerScopeReviewError("Owner scope needs a bounded original record ID")
        if self.retention in {"MISSING_BODY", "NOT_CAPTURED_COMPLETE_BODY"}:
            if self.body is not None or self.sha256 is not None or self.size_bytes is not None:
                raise OwnerScopeReviewError("An uncaptured whole body cannot have substitute bytes or a full-body hash")
            return
        if (self.retention not in {"ORIGINAL_BYTES", "WITHHELD_SENSITIVE_BODY", "WITHHELD_ENCODED_BODY"}
                or not isinstance(self.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.sha256)
                or type(self.size_bytes) is not int or not 0 <= self.size_bytes <= MAX_BYTES):
            raise OwnerScopeReviewError("Owner scope requires bounded complete original metadata")
        if self.retention != "ORIGINAL_BYTES":
            if self.body is not None: raise OwnerScopeReviewError("A withheld whole body cannot have replacement bytes")
        elif (not isinstance(self.body, bytes) or len(self.body) != self.size_bytes
                or hashlib.sha256(self.body).hexdigest() != self.sha256):
            raise OwnerScopeReviewError("Owner scope full original byte/hash conflict")

    def summary(self):
        return {"record_id":self.record_id,"source_url":self.source_url,"sha256":self.sha256,
            "size_bytes":self.size_bytes,"observed_at":self.observed_at.isoformat(),
            "known_at":self.available_at.isoformat(),"retention":self.retention}


class _OwnershipIndex(_Index):
    def __init__(self, body):
        self.companies = []; self.company_parts = None; self.company_links = []
        super().__init__(body)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "span" and attrs.get("class") == "companyName":
            if self.company_parts is not None: raise OwnerScopeReviewError("Ambiguous ownership index company scope")
            self.company_parts = []; self.company_links = []
        if tag == "a" and self.company_parts is not None: self.company_links.append(attrs.get("href", ""))
        super().handle_starttag(tag, list(attrs.items()))

    def handle_data(self, data):
        if self.company_parts is not None: self.company_parts.append(data)
        super().handle_data(data)

    def handle_endtag(self, tag):
        if tag == "span" and self.company_depth == 1 and self.company_parts is not None:
            text = _norm("".join(self.company_parts))
            match = re.fullmatch(r"(.{1,250}) \((Issuer|Reporting)\) CIK:\s*([0-9]{10}) \(see all company filings\)", text)
            if not match: raise OwnerScopeReviewError("Ownership index needs explicit issuer/reporting company labels")
            name, role, cik = match.groups()
            actions = []
            for href in self.company_links:
                parsed = urlsplit(href); query = parse_qs(parsed.query)
                if parsed.path == "/cgi-bin/own-disp": actions.append((query.get("CIK"),query.get("action")))
            expected = ([cik], ["getissuer" if role == "Issuer" else "getowner"])
            if actions != [expected]: raise OwnerScopeReviewError("Ownership index role/CIK link conflicts")
            self.companies.append({"name":name,"role":role,"cik":cik,"literal_company_context":text})
            self.company_parts = None
        super().handle_endtag(tag)


def _scalar(node, path):
    nodes = node.findall(path)
    if len(nodes) > 1: raise OwnerScopeReviewError("Owner scope scalar is duplicated")
    if not nodes or not (nodes[0].text or "").strip(): return None
    if len(nodes[0]) or len(nodes[0].text) > 8_000:
        raise OwnerScopeReviewError("Owner scope scalar is nested or exceeds its bound")
    return nodes[0].text.strip()


def _day(value):
    if not isinstance(value,str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}",value):
        raise OwnerScopeReviewError("Owner scope needs an exact ISO calendar day")
    return date.fromisoformat(value)


def _exact_packet(capture):
    result=_packet(capture)
    header=re.findall(r"<SEC-HEADER>(.*?)</SEC-HEADER>",capture.body.decode("utf-8"),re.S)[0]
    sections=re.split(r"(?m)^\s*(REPORTING-OWNER|ISSUER|SUBJECT COMPANY|FILER):\s*\n",header)
    owners=[]
    for offset in range(1,len(sections),2):
        if sections[offset]=="REPORTING-OWNER":
            owners.append({"cik":_field(sections[offset+1],"CENTRAL INDEX KEY"),
                           "name":_field(sections[offset+1],"COMPANY CONFORMED NAME")})
    if owners!=result["owners"]:
        raise OwnerScopeReviewError("Original header/XML reporting account raw names or CIKs differ")
    return result


def _index_scope(index, xml, packet, selection):
    issuer, accession, filename = _url(xml.source_url)
    index_issuer, index_accession, index_name = _url(index.source_url)
    expected = selection["accession"]
    if (issuer.zfill(10) != selection["issuer_cik"] or (issuer,accession) != (index_issuer,index_accession)
            or accession != expected.replace("-", "") or index_name not in {expected+"-index.html",expected+"-index.htm"}
            or packet.source_url != xml.source_url.rsplit("/",1)[0]+"/"+expected+".txt"):
        raise OwnerScopeReviewError("Owner index/primary/packet accession, basename or issuer scope conflict")
    parsed = _OwnershipIndex(index.body)
    expected_companies = [{"name":selection["issuer_name"],"role":"Issuer","cik":selection["issuer_cik"]},
                          {"name":selection["owner_name"],"role":"Reporting","cik":selection["owner_cik"]}]
    if [{k:item[k] for k in ("name","role","cik")} for item in parsed.companies] != expected_companies:
        raise OwnerScopeReviewError("Ownership index exact issuer/reporting account names or CIKs conflict")
    rows, packet_rows = [], []
    for row in parsed.rows:
        refs = [urljoin(index.source_url,ref) for ref in row["hrefs"]]
        if xml.source_url in refs: rows.append(row)
        if packet.source_url in refs: packet_rows.append(row)
    if len(rows)!=1 or len(rows[0]["cells"])<5 or rows[0]["cells"][-2]!=selection["form"]:
        raise OwnerScopeReviewError("Ownership index does not identify the exact original untransformed XML")
    try:
        declared=int(rows[0]["cells"][-1]); filed=_day(parsed.labels["Filing Date"])
        period=_day(parsed.labels["Period of Report"])
        accepted=datetime.strptime(parsed.labels["Accepted"],"%Y-%m-%d %H:%M:%S")
        packet_declared=int(packet_rows[0]["cells"][-1]) if len(packet_rows)==1 else None
    except (KeyError,TypeError,ValueError) as error:
        raise OwnerScopeReviewError("Ownership index needs explicit dates, acceptance and original sizes") from error
    if (declared<1 or packet_declared is not None and packet_declared<1 or period>filed
            or filed>min(_clock(index.observed_at),_clock(xml.observed_at)).date()):
        raise OwnerScopeReviewError("Ownership index filing/report day exceeds its original observation scope")
    return {"accession":expected,"form":selection["form"],"original_xml_filename":filename,
        "filing_day":filed.isoformat(),"period_day":period.isoformat(),
        "acceptance_datetime_unqualified_raw":parsed.labels["Accepted"],"acceptance_timezone_inferred":False,
        "index_company_contexts":parsed.companies,"index_declared_xml_bytes":declared,
        "captured_xml_bytes":xml.size_bytes,"xml_size_conflict":declared!=xml.size_bytes,
        "index_declared_packet_bytes":packet_declared,
        "clock_conflicts":["INDEX_ACCEPTANCE_DATE_DIFFERS_FROM_FILING_DAY"] if accepted.date()!=filed else [],
        "source_publication_instant_verified":False,"exact_historical_as_filed_version_verified":False}


def review_sec_owner_instrument_scope(*, prior_account_capture: OwnerInstrumentCapture,
        packet_capture: OwnerInstrumentCapture, xml_capture: OwnerInstrumentCapture,
        index_capture: OwnerInstrumentCapture, selection: dict, knowledge_at: datetime|None=None) -> dict:
    """Review one recent filing's exact as-filed account/role/instrument scope.

    None means current physical ingestion. Explicit past knowledge never makes
    a newly ingested review historical; future cutoffs are unavailable.
    """
    physical=_clock(_physical_clock())
    cutoff=physical if knowledge_at is None else _clock(knowledge_at)
    if cutoff>physical: raise OwnerScopeReviewError("Future owner-scope knowledge is unavailable")
    report={"review_version":VERSION,"status":"PARTIAL","requested_knowledge_at":None if knowledge_at is None else cutoff.isoformat(),
        "review_ingested_at":physical.isoformat(),"source_only_xml_scope_supported":False,
        "complete_primary_packet_scope_supported":False,"current_header_corroborated":False,
        "dated_reporting_account_match_supported":False,"natural_person_identity_verified":False,
        "person_cik_binding_verified":False,"approved_name_alias":False,"instrument_identity_verified":False,
        "cusip_class_validity_verified":False,"role_continuity_verified":False,"account_continuity_verified":False,
        "execution_verified":False,"issuer_attribution_allowed":False,"historical_machine_availability_inferred":False,
        "scoring_applied":False,"registry_modified":False,"trading_allowed":False,"network_requests_performed":0,
        "quota_or_lease_modified":False,"is_latest_owner_filing_proven":False,"gaps":[]}
    def finish():
        completed=_clock(_physical_clock())
        if completed<physical: raise OwnerScopeReviewError("Physical owner review clock rolled back")
        report["review_available_at"]=completed.isoformat()
        return report
    if cutoff<physical:
        report["gaps"]=["AFTER_PHYSICAL_REVIEW_KNOWLEDGE_CUTOFF"]
        return finish()
    captures={"prior_account":prior_account_capture,"packet":packet_capture,"xml":xml_capture,"index":index_capture}
    for capture in captures.values():
        if not isinstance(capture,OwnerInstrumentCapture): raise OwnerScopeReviewError("Owner scope needs exact whole original captures")
        capture.validate()
    if len({capture.record_id for capture in captures.values()})!=4:
        raise OwnerScopeReviewError("Owner scope original record IDs must be distinct")
    if any(capture.available_at>cutoff for capture in captures.values()):
        report["gaps"]=["ORIGINAL_INPUT_NOT_YET_KNOWN"]
        return finish()
    summaries={key:capture.summary() for key,capture in captures.items()}
    unavailable=set()
    for key,capture in captures.items():
        if capture.retention!="ORIGINAL_BYTES" or _has_sensitive_body(capture.body):
            if capture.retention=="ORIGINAL_BYTES": summaries[key]["retention"]="WITHHELD_SENSITIVE_BODY"
            unavailable.add(key)
    report["captures"]=summaries
    if unavailable-{"packet"}:
        report["gaps"]=["REQUIRED_ORIGINAL_PRIOR_ACCOUNT_XML_OR_INDEX_UNAVAILABLE"]
        return finish()
    if not isinstance(selection,dict): raise OwnerScopeReviewError("Owner scope needs one bounded exact selection")
    try: encoded=json.dumps(selection,allow_nan=False).encode()
    except (TypeError,ValueError,RecursionError) as error: raise OwnerScopeReviewError("Owner scope selection is not bounded JSON") from error
    if len(encoded)>16_000 or _has_sensitive_body(encoded): raise OwnerScopeReviewError("Owner scope selection is unavailable or unbounded")
    required={"issuer_cik","issuer_name","owner_cik","owner_name","accession","form","period_day","instrument_title","transaction_or_period_day","role","signature"}
    if (set(selection)!=required or any(not isinstance(selection[key],str) or not re.fullmatch(r"[0-9]{10}",selection[key]) or int(selection[key])==0 for key in ("issuer_cik","owner_cik"))
            or not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}",str(selection["accession"])) or selection["form"]!="4"
            or any(not isinstance(selection[key],str) or not 1<=len(selection[key])<=250 for key in ("issuer_name","owner_name","instrument_title"))
            or not isinstance(selection["role"],dict) or set(selection["role"])!=set(ROLE_FIELDS)
            or not isinstance(selection["signature"],dict) or set(selection["signature"])!={"name","date"}):
        raise OwnerScopeReviewError("Owner scope exact account, form, instrument, role or signature selection is malformed")
    for value in (*selection["role"].values(),*selection["signature"].values()):
        if value is not None and (not isinstance(value,str) or not 1<=len(value)<=1_000): raise OwnerScopeReviewError("Owner scope selected source literals are unbounded")
    selected_period=_day(selection["period_day"]); selected_row_day=_day(selection["transaction_or_period_day"])
    for capture in (prior_account_capture,xml_capture):
        if b"\x00" in capture.body: raise OwnerScopeReviewError("Owner scope original text must be UTF-8 without NUL encoding")
    prior=_exact_packet(prior_account_capture)
    prior_owner=[row for row in prior["owners"] if row=={"cik":selection["owner_cik"],"name":selection["owner_name"]}]
    if (len(prior_owner)!=1 or prior["issuer_as_filed"]!={"cik":selection["issuer_cik"],"name":selection["issuer_name"]}
            or prior["form"] not in {"3","3/A"}):
        raise OwnerScopeReviewError("Prior original Form3 account or issuer does not exactly match selection")
    index=_index_scope(index_capture,xml_capture,packet_capture,selection)
    if (index["accession"]==prior["accession"] or _day(index["filing_day"])<=_day(prior["filing_day"])
            or prior["clock_conflicts"] or _day(prior["filing_day"])>_clock(prior_account_capture.observed_at).date()):
        raise OwnerScopeReviewError("Current scope needs a separate later dated disclosure and consistent original prior account")
    xml=_xml(xml_capture.body)
    issuer={"cik":_scalar(xml,"issuer/issuerCik"),"name":_scalar(xml,"issuer/issuerName")}
    if (issuer!={"cik":selection["issuer_cik"],"name":selection["issuer_name"]}
            or _scalar(xml,"documentType")!=selection["form"] or _scalar(xml,"periodOfReport")!=selected_period.isoformat()
            or index["period_day"]!=selected_period.isoformat()):
        raise OwnerScopeReviewError("Current original XML issuer/form/report day conflicts with exact index/selection")
    owners=xml.findall("reportingOwner")
    if not 1<=len(owners)<=16: raise OwnerScopeReviewError("Current owner scope is unbounded or absent")
    owner_pairs=[{"cik":_scalar(owner,"reportingOwnerId/rptOwnerCik"),"name":_scalar(owner,"reportingOwnerId/rptOwnerName")} for owner in owners]
    if len(owners)!=1 or owner_pairs!=[{"cik":selection["owner_cik"],"name":selection["owner_name"]}]:
        raise OwnerScopeReviewError("Current exact reporting account is different, duplicated or jointly unallocated")
    role={key:_scalar(owners[0],"reportingOwnerRelationship/"+key) for key in ROLE_FIELDS}
    role_missing=[]
    for key,value in role.items():
        if value is None and (key!="officerTitle" or role["isOfficer"] in {"1","true"} or selection["role"][key] is not None): role_missing.append(key)
        elif value is not None and value!=selection["role"][key]: raise OwnerScopeReviewError("Selected role literal conflicts with original XML")
        if key!="officerTitle" and value is not None and value not in {"0","1","true","false"}:
            raise OwnerScopeReviewError("Original role checkbox is not an explicit supported boolean")
    signatures=[]
    for node in xml.findall("ownerSignature"):
        if len(signatures)>=16: raise OwnerScopeReviewError("Original signature scope is unbounded")
        item={"name":_scalar(node,"signatureName"),"date":_scalar(node,"signatureDate")}
        if item["date"] is not None and _day(item["date"])>_day(index["filing_day"]): raise OwnerScopeReviewError("Signature day follows original filing day")
        signatures.append(item)
    signature_supported=bool(signatures and selection["signature"] in signatures and all(all(item.values()) for item in signatures))
    if signatures and all(all(item.values()) for item in signatures) and selection["signature"] not in signatures:
        raise OwnerScopeReviewError("Selected signature literal conflicts with original XML")
    footnotes={}
    for node in xml.findall("footnotes/footnote"):
        key=node.get("id"); text="".join(node.itertext()).strip()
        if not key or key in footnotes or len(footnotes)>=512 or len(text)>8_000: raise OwnerScopeReviewError("Original footnote scope is ambiguous or unbounded")
        footnotes[key]=text
    rows=[]
    for table_name,transaction,holding,derivative in (("nonDerivativeTable","nonDerivativeTransaction","nonDerivativeHolding",False),("derivativeTable","derivativeTransaction","derivativeHolding",True)):
        tables=xml.findall(table_name)
        if len(tables)>1: raise OwnerScopeReviewError("Original instrument table is duplicated")
        for table in tables:
            for ordinal,node in enumerate(table,1):
                if len(rows)>=1_000 or node.tag not in {transaction,holding}: raise OwnerScopeReviewError("Original instrument row scope is unsupported or unbounded")
                title=_scalar(node,"securityTitle/value"); transaction_day=_scalar(node,"transactionDate/value") if node.tag==transaction else None
                if transaction_day is not None and _day(transaction_day)>_day(index["filing_day"]): raise OwnerScopeReviewError("Instrument transaction day follows filing day")
                refs=[ref.get("id","") for ref in node.iter("footnoteId")]
                missing=(["securityTitle/value"] if not title else [])+(["transactionDate/value"] if node.tag==transaction and not transaction_day else [])
                if any(ref not in footnotes for ref in refs): missing.append("referencedFootnote")
                rows.append({"row_locator":f"{table_name}/{node.tag}:{ordinal}","kind":"TRANSACTION" if node.tag==transaction else "HOLDING",
                    "security_title":title,"derivative":derivative,"transaction_day":transaction_day,
                    "report_period_day":selected_period.isoformat(),"holding_date_or_validity_inferred":False,
                    "direct_or_indirect_raw":_scalar(node,"ownershipNature/directOrIndirectOwnership/value"),
                    "footnote_ids":refs,"missing_fields":missing,"instrument_identity_verified":False,"execution_verified":False})
    if not rows: raise OwnerScopeReviewError("Original current filing contains no bounded instrument rows")
    selected=[row for row in rows if row["security_title"]==selection["instrument_title"] and (row["transaction_day"] if row["kind"]=="TRANSACTION" else row["report_period_day"])==selected_row_day.isoformat()]
    if not selected and not any(row["missing_fields"] for row in rows): raise OwnerScopeReviewError("Selected instrument title/day is absent from original rows")
    header_ok=False; header=None
    if "packet" in unavailable:
        report["gaps"].append("CURRENT_ORIGINAL_HEADER_PACKET_UNAVAILABLE")
    else:
        if b"\x00" in packet_capture.body: raise OwnerScopeReviewError("Original packet must be UTF-8 without NUL encoding")
        header=_exact_packet(packet_capture)
        if (header["accession"]!=selection["accession"] or header["form"]!=selection["form"]
                or header["owners"]!=owner_pairs or header["issuer_as_filed"]!=issuer
                or header["report_day"]!=selected_period.isoformat() or header["filing_day"]!=index["filing_day"]
                or _day(header["filing_day"])>_clock(packet_capture.observed_at).date()):
            raise OwnerScopeReviewError("Current original SGML header/XML/index exact scope conflict")
        embedded=re.findall(br"<XML>(.*?)</XML>",packet_capture.body,re.S)
        if len(embedded)!=1 or not any(embedded[0]==left+xml_capture.body+right for left in (b"",b"\n",b"\r\n") for right in (b"",b"\n",b"\r\n")):
            raise OwnerScopeReviewError("Current packet does not contain the exact separately pinned original XML bytes")
        accepted=datetime.strptime(header["acceptance_datetime_unqualified_raw"],"%Y%m%d%H%M%S")
        index_accepted=datetime.strptime(index["acceptance_datetime_unqualified_raw"],"%Y-%m-%d %H:%M:%S")
        header_ok=not header["clock_conflicts"] and accepted==index_accepted and (index["index_declared_packet_bytes"] in {None,packet_capture.size_bytes})
        if not header_ok: report["gaps"].append("CURRENT_HEADER_INDEX_CLOCK_OR_SIZE_CONFLICT")
    if role_missing: report["gaps"].append("MISSING_EXPLICIT_ROLE_FIELDS")
    if not signature_supported: report["gaps"].append("MISSING_COMPLETE_EXACT_SIGNATURE")
    if any(row["missing_fields"] for row in rows): report["gaps"].append("MISSING_ORIGINAL_INSTRUMENT_ROW_FIELDS")
    if index["xml_size_conflict"]: report["gaps"].append("INDEX_DECLARED_XML_SIZE_CONFLICT")
    if index["clock_conflicts"]: report["gaps"].extend(index["clock_conflicts"])
    source_supported=bool(selected and not role_missing and signature_supported and not any(row["missing_fields"] for row in rows) and not index["clock_conflicts"] and not index["xml_size_conflict"])
    report.update(source_only_xml_scope_supported=source_supported,complete_primary_packet_scope_supported=source_supported and header_ok,
        current_header_corroborated=header_ok,dated_reporting_account_match_supported=True,index_scope=index,
        source_account={"owner_cik":selection["owner_cik"],"account_name_as_filed":selection["owner_name"],"issuer_as_filed":issuer,
            "prior_accession":prior["accession"],"prior_report_day":prior["report_day"],"current_accession":selection["accession"],
            "current_report_day":selected_period.isoformat(),"same_account_at_two_disclosure_points_not_identity_or_continuity":True},
        as_filed_role={"raw_fields":role,"missing_fields":role_missing,"role_validity_interval_verified":False},
        as_filed_signatures=signatures,signature_name_is_approved_owner_alias=False,
        instrument_rows=rows,source_row_count=len(rows),selected_row_locators=[row["row_locator"] for row in selected],
        footnotes=footnotes,current_primary_disclosure_count=1,packet_xml_index_are_one_disclosure=True,
        source_inputs_available_at=max(capture.available_at for capture in captures.values()).isoformat())
    report["gaps"].extend(["NATURAL_PERSON_LEGAL_ACCOUNT_BRIDGE_UNVERIFIED","NO_APPROVED_NAME_ORDER_OR_ATTORNEY_ALIAS",
        "DATED_CUSIP_CLASS_VALIDITY_UNVERIFIED","NO_ROLE_OR_ACCOUNT_CONTINUITY_INTERVAL","AS_FILED_ROWS_ARE_NOT_EXECUTION_OR_TRADING_EVIDENCE"])
    return finish()
