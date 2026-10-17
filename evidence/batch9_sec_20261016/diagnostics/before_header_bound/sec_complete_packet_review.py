"""Strict original SGML packet/document binding; no legal identity adoption."""
from __future__ import annotations

from datetime import date,datetime,timezone
import hashlib
import json
import re
from urllib.parse import urljoin

from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body
from market_checker_app.services.sec_form4_person_bridge import _field,_xml
from market_checker_app.services.sec_owner_instrument_scope_review import (
    OwnerInstrumentCapture as CompletePacketCapture,ROLE_FIELDS,_index_scope,_scalar,
)
from market_checker_app.services.sec_person_kind_review import _clock,_url

VERSION="sec-complete-original-sgml-packet-v1"
MAX_BYTES=4_000_000
MAX_DOCUMENTS=32
MAX_LINES=100_000


class CompletePacketReviewError(ValueError):
    pass


def _physical_clock():
    return datetime.now(timezone.utc)


def _date8(value):
    if not isinstance(value,str) or not re.fullmatch(r"[0-9]{8}",value):
        raise CompletePacketReviewError("Packet header needs an exact original calendar date")
    return datetime.strptime(value,"%Y%m%d").date()


def _day(value):
    if not isinstance(value,str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}",value):
        raise CompletePacketReviewError("Packet scope requires an exact ISO calendar day")
    return date.fromisoformat(value)


def parse_complete_sec_packet(body: bytes,*,expected_accession: str) -> dict:
    """Consume a complete original packet, retaining exact document byte spans.

    SEC envelope/header/document tags are parsed only as entire original lines.
    Every document is consumed; unmatched controls or trailing text cannot be
    silently dropped. XML is bound within its own declared document block.
    """
    if (not isinstance(body,bytes) or not 0<len(body)<=MAX_BYTES or b"\x00" in body
            or not isinstance(expected_accession,str)
            or not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}",expected_accession)):
        raise CompletePacketReviewError("Packet requires bounded complete UTF-8 original bytes and exact accession")
    if _has_sensitive_body(body): raise CompletePacketReviewError("Whole access-bearing packet is withheld before parsing")
    body.decode("utf-8",errors="strict")
    lines=body.splitlines(keepends=True)
    if not 5<=len(lines)<=MAX_LINES: raise CompletePacketReviewError("Packet original line count is unsupported")
    offsets=[];offset=0
    for line in lines:offsets.append(offset);offset+=len(line)
    def value(index):return lines[index].rstrip(b"\r\n")
    def fail(message):raise CompletePacketReviewError(message)
    opening=re.fullmatch(br"<SEC-DOCUMENT>([0-9]{10}-[0-9]{2}-[0-9]{6})\.txt : ([0-9]{8})",value(0))
    header_open=re.fullmatch(br"<SEC-HEADER>([0-9]{10}-[0-9]{2}-[0-9]{6})\.hdr\.sgml : ([0-9]{8})",value(1))
    if (not opening or not header_open or opening[1].decode()!=expected_accession
            or header_open[1].decode()!=expected_accession):fail("Original SEC document/header filename accession conflicts")
    cursor=2
    while cursor<len(lines) and value(cursor)!=b"</SEC-HEADER>":
        if value(cursor).startswith((b"<DOCUMENT",b"</SEC-DOCUMENT",b"<SEC-HEADER",b"<SEC-DOCUMENT",b"<TEXT",b"</TEXT")):
            fail("Unexpected structural control inside original SEC header")
        if offsets[cursor]>200_000:fail("Original SEC header exceeds its byte bound")
        cursor+=1
    if cursor==len(lines):fail("Complete original SEC header closing tag is absent")
    header_text=body[offsets[2]:offsets[cursor]].decode("utf-8")
    sections=re.split(r"(?m)^[ \t]*(ISSUER|REPORTING-OWNER|FILER|SUBJECT COMPANY):[ \t]*\r?\n",header_text)
    top=sections[0]
    required=("ACCESSION NUMBER","CONFORMED SUBMISSION TYPE","PUBLIC DOCUMENT COUNT",
              "CONFORMED PERIOD OF REPORT","FILED AS OF DATE","DATE AS OF CHANGE")
    fields={key:_field(top,key) for key in required}
    if any(_field(header_text,key)!=fields[key] for key in required):fail("SEC header field is outside its top-level scope")
    if fields["ACCESSION NUMBER"]!=expected_accession:fail("Original SEC header accession field conflicts")
    form=fields["CONFORMED SUBMISSION TYPE"]
    if form not in {"3","3/A","4","4/A","5","5/A"}:fail("Packet supports bounded original ownership forms only")
    if not re.fullmatch(r"[1-9][0-9]?",fields["PUBLIC DOCUMENT COUNT"]):fail("Original public document count is not explicit")
    count=int(fields["PUBLIC DOCUMENT COUNT"])
    if count>MAX_DOCUMENTS:fail("Original public document count exceeds packet scope")
    filed=_date8(fields["FILED AS OF DATE"]);period=_date8(fields["CONFORMED PERIOD OF REPORT"])
    changed=_date8(fields["DATE AS OF CHANGE"])
    if _date8(opening[2].decode())!=filed or _date8(header_open[2].decode())!=filed:
        fail("SEC envelope/header original dates differ from filed day")
    accepted=re.findall(r"(?m)^<ACCEPTANCE-DATETIME>([0-9]{14})\r?$",top)
    if len(accepted)!=1 or header_text.count("<ACCEPTANCE-DATETIME>")!=1:
        fail("Original header requires exactly one unqualified acceptance clock")
    accepted_naive=datetime.strptime(accepted[0],"%Y%m%d%H%M%S")
    roles=[]
    for at in range(1,len(sections),2):
        kind,section=sections[at:at+2]
        if kind not in {"ISSUER","REPORTING-OWNER"}:fail("Unsupported ownership header account role")
        name=_field(section,"COMPANY CONFORMED NAME");cik=_field(section,"CENTRAL INDEX KEY")
        if not 1<=len(name)<=250 or not re.fullmatch(r"[0-9]{10}",cik) or int(cik)==0:
            fail("Header account needs an exact bounded raw name and CIK")
        if kind=="REPORTING-OWNER" and _field(section,"FORM TYPE")!=form:
            fail("Reporting-owner filing form differs from submission type")
        roles.append({"role":kind,"name":name,"cik":cik})
    if (not 2<=len(roles)<=17 or sum(item["role"]=="ISSUER" for item in roles)!=1
            or len({(item["role"],item["cik"]) for item in roles})!=len(roles)):
        fail("Original issuer/reporting-owner role set is missing or duplicated")
    documents=[];seen_files=set();cursor+=1;closed=False
    controls=(b"<SEC-DOCUMENT",b"</SEC-DOCUMENT",b"<SEC-HEADER",b"</SEC-HEADER",b"<DOCUMENT",b"</DOCUMENT",b"<TEXT",b"</TEXT")
    while cursor<len(lines):
        if not value(cursor).strip():cursor+=1;continue
        if value(cursor)==b"</SEC-DOCUMENT>":
            closed=True;cursor+=1
            if any(value(at).strip() for at in range(cursor,len(lines))):fail("Extra data follows complete original packet")
            break
        if value(cursor)!=b"<DOCUMENT>":fail("Unconsumed data occurs outside an original document block")
        if len(documents)>=MAX_DOCUMENTS:fail("Original document blocks exceed packet bound")
        block_start=offsets[cursor];cursor+=1;metadata={};ordered=[];metadata_bytes=0
        while cursor<len(lines) and value(cursor)!=b"<TEXT>":
            line=value(cursor);metadata_bytes+=len(lines[cursor])
            match=re.fullmatch(br"<(TYPE|SEQUENCE|FILENAME|DESCRIPTION)>([^<>\r\n]*)",line)
            if not match or metadata_bytes>8_000:fail("Document metadata is missing, nested or unbounded")
            key=match[1].decode();literal=match[2].decode("utf-8")
            if key in metadata:fail("Document metadata field is duplicated")
            metadata[key]=literal;ordered.append(key);cursor+=1
        if cursor==len(lines) or ordered not in (["TYPE","SEQUENCE","FILENAME"],["TYPE","SEQUENCE","FILENAME","DESCRIPTION"]):
            fail("Document identity metadata or TEXT opening is incomplete")
        if (not re.fullmatch(r"[A-Z0-9][A-Z0-9./-]{0,49}",metadata["TYPE"])
                or metadata["SEQUENCE"]!=str(len(documents)+1)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}",metadata["FILENAME"])
                or metadata["FILENAME"] in seen_files or len(metadata.get("DESCRIPTION",""))>1_000):
            fail("Document type, sequence or filename identity is conflicting")
        seen_files.add(metadata["FILENAME"]);cursor+=1
        if cursor==len(lines):fail("Original TEXT body is truncated")
        payload_first=cursor
        while cursor<len(lines) and value(cursor)!=b"</TEXT>":
            if value(cursor).startswith(controls):fail("Nested or unmatched document control inside original TEXT")
            cursor+=1
        if cursor==len(lines):fail("Original TEXT closing tag is absent")
        payload_end=offsets[cursor];payload_start=offsets[payload_first]
        document={"document_type":metadata["TYPE"],"sequence_raw":metadata["SEQUENCE"],
            "filename":metadata["FILENAME"],"description_raw":metadata.get("DESCRIPTION"),
            "text_span":{"byte_start":payload_start,"byte_end":payload_end,"bytes":payload_end-payload_start,
                         "sha256":hashlib.sha256(body[payload_start:payload_end]).hexdigest()}}
        if metadata["TYPE"] in {"3","3/A","4","4/A","5","5/A"}:
            nonblank=[at for at in range(payload_first,cursor) if value(at).strip()]
            if (len(nonblank)<3 or value(nonblank[0])!=b"<XML>" or value(nonblank[-1])!=b"</XML>"
                    or any(value(at) in {b"<XML>",b"</XML>"} for at in nonblank[1:-1])):
                fail("Ownership document requires its own complete unambiguous XML envelope")
            xml_start=offsets[nonblank[0]+1];xml_end=offsets[nonblank[-1]]
            xml_bytes=body[xml_start:xml_end]
            _xml(xml_bytes)
            document["ownership_xml_span"]={"byte_start":xml_start,"byte_end":xml_end,"bytes":len(xml_bytes),
                "sha256":hashlib.sha256(xml_bytes).hexdigest()}
            document["text_envelope"]="ORIGINAL_XML_WRAPPER"
        else:document["text_envelope"]="OPAQUE_OTHER_DOCUMENT_NOT_IDENTITY_EVIDENCE"
        cursor+=1
        while cursor<len(lines) and not value(cursor).strip():cursor+=1
        if cursor==len(lines) or value(cursor)!=b"</DOCUMENT>":fail("Original document closing tag is absent")
        document["document_span"]={"byte_start":block_start,"byte_end":offsets[cursor]+len(lines[cursor])}
        documents.append(document);cursor+=1
    if not closed or len(documents)!=count:fail("Complete original packet/document count does not match header")
    return {"parser_version":VERSION,"whole_original_consumed":True,"packet_sha256":hashlib.sha256(body).hexdigest(),
        "packet_bytes":len(body),"header":{"accession":expected_accession,"submission_type":form,
            "public_document_count":count,"period_day":period.isoformat(),"filed_day":filed.isoformat(),
            "change_day":changed.isoformat(),"acceptance_datetime_unqualified_raw":accepted[0],
            "acceptance_datetime_unqualified_display":accepted_naive.isoformat(sep=" "),"acceptance_timezone_inferred":False,
            "account_roles":roles,"clock_conflicts":(["REPORT_AFTER_FILED_DAY"] if period>filed else [])+
                (["CHANGE_BEFORE_FILED_DAY"] if changed<filed else [])+
                (["ACCEPTANCE_DATE_DIFFERS_FROM_FILED_DAY"] if accepted_naive.date()!=filed else [])},
        "documents":documents}


def review_sec_complete_packet(*,packet_capture: CompletePacketCapture,xml_capture: CompletePacketCapture,
        index_capture: CompletePacketCapture,selection: dict,knowledge_at: datetime|None=None) -> dict:
    physical=_clock(_physical_clock());cutoff=physical if knowledge_at is None else _clock(knowledge_at)
    if cutoff>physical:raise CompletePacketReviewError("Future complete-packet knowledge is unavailable")
    report={"review_version":VERSION,"status":"PARTIAL","requested_knowledge_at":None if knowledge_at is None else cutoff.isoformat(),
        "review_ingested_at":physical.isoformat(),"original_complete_packet_supported":False,
        "original_header_scope_supported":False,"original_primary_xml_exact_binding_supported":False,
        "standalone_xml_index_scope_supported":False,"natural_person_identity_verified":False,
        "person_cik_binding_verified":False,"approved_name_alias":False,"instrument_identity_verified":False,
        "cusip_class_validity_verified":False,"role_continuity_verified":False,"account_continuity_verified":False,
        "execution_verified":False,"issuer_attribution_allowed":False,"historical_machine_availability_inferred":False,
        "scoring_applied":False,"registry_modified":False,"trading_allowed":False,"network_requests_performed":0,
        "quota_or_lease_modified":False,"gaps":[]}
    def finish():
        completed=_clock(_physical_clock())
        if completed<physical:raise CompletePacketReviewError("Physical complete-packet review clock rolled back")
        report["review_available_at"]=completed.isoformat();return report
    if cutoff<physical:report["gaps"]=["AFTER_PHYSICAL_REVIEW_KNOWLEDGE_CUTOFF"];return finish()
    captures={"packet":packet_capture,"xml":xml_capture,"index":index_capture}
    for capture in captures.values():
        if not isinstance(capture,CompletePacketCapture):raise CompletePacketReviewError("Complete packet review requires original captures")
        capture.validate()
    if len({capture.record_id for capture in captures.values()})!=3:raise CompletePacketReviewError("Packet capture record IDs are duplicated")
    if any(capture.available_at>cutoff for capture in captures.values()):report["gaps"]=["ORIGINAL_INPUT_NOT_YET_KNOWN"];return finish()
    summaries={key:capture.summary() for key,capture in captures.items()};unavailable=set()
    for key,capture in captures.items():
        if capture.retention!="ORIGINAL_BYTES" or _has_sensitive_body(capture.body):
            if capture.retention=="ORIGINAL_BYTES":summaries[key]["retention"]="WITHHELD_SENSITIVE_BODY"
            unavailable.add(key)
    report["captures"]=summaries
    if unavailable-{"packet"}:report["gaps"]=["RETAINED_ORIGINAL_XML_OR_INDEX_UNAVAILABLE"];return finish()
    if not isinstance(selection,dict):raise CompletePacketReviewError("Packet review needs one exact bounded selection")
    try:encoded=json.dumps(selection,allow_nan=False).encode()
    except (TypeError,ValueError,RecursionError) as error:raise CompletePacketReviewError("Packet selection is not bounded JSON") from error
    keys={"accession","form","issuer_cik","issuer_name","owner_cik","owner_name","primary_filename","period_day"}
    if (set(selection)!=keys or len(encoded)>8_000 or _has_sensitive_body(encoded)
            or not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}",str(selection.get("accession"))) or selection.get("form")!="4"
            or any(not isinstance(selection[key],str) or not re.fullmatch(r"[0-9]{10}",selection[key]) or int(selection[key])==0 for key in ("issuer_cik","owner_cik"))
            or any(not isinstance(selection[key],str) or not 1<=len(selection[key])<=250 for key in ("issuer_name","owner_name"))
            or not isinstance(selection["primary_filename"],str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,195}\.xml",selection["primary_filename"])):
        raise CompletePacketReviewError("Packet exact account/form/filename selection is malformed")
    period=_day(selection["period_day"])
    if _url(xml_capture.source_url)[2]!=selection["primary_filename"]:raise CompletePacketReviewError("Primary URL and selected filename differ")
    index=_index_scope(index_capture,xml_capture,packet_capture,selection)
    if b"\x00" in xml_capture.body:
        raise CompletePacketReviewError("Original ownership XML must be unencoded UTF-8 bytes")
    xml_capture.body.decode("utf-8",errors="strict")
    root=_xml(xml_capture.body)
    issuer={"cik":_scalar(root,"issuer/issuerCik"),"name":_scalar(root,"issuer/issuerName")}
    owners=[{"cik":_scalar(node,"reportingOwnerId/rptOwnerCik"),"name":_scalar(node,"reportingOwnerId/rptOwnerName")} for node in root.findall("reportingOwner")]
    if (issuer!={"cik":selection["issuer_cik"],"name":selection["issuer_name"]}
            or owners!=[{"cik":selection["owner_cik"],"name":selection["owner_name"]}]
            or _scalar(root,"documentType")!=selection["form"] or _scalar(root,"periodOfReport")!=period.isoformat()
            or index["period_day"]!=period.isoformat()):raise CompletePacketReviewError("Original primary XML/index exact ownership scope conflicts")
    report.update(standalone_xml_index_scope_supported=not index["xml_size_conflict"] and not index["clock_conflicts"],index_scope=index)
    if "packet" in unavailable:report["gaps"]=["ORIGINAL_COMPLETE_PACKET_UNAVAILABLE_NO_HEADER_SUBSTITUTE"];return finish()
    parsed=parse_complete_sec_packet(packet_capture.body,expected_accession=selection["accession"])
    header=parsed["header"]
    if (header["submission_type"]!=selection["form"] or header["period_day"]!=period.isoformat()
            or header["filed_day"]!=index["filing_day"]
            or [{"cik":item["cik"],"name":item["name"]} for item in header["account_roles"] if item["role"]=="ISSUER"]!=[issuer]
            or [{"cik":item["cik"],"name":item["name"]} for item in header["account_roles"] if item["role"]=="REPORTING-OWNER"]!=owners):
        raise CompletePacketReviewError("Original SEC header and primary/index exact account/date/form scope conflicts")
    upper=min(_clock(capture.observed_at).date() for capture in captures.values())
    if max(_day(header[key]) for key in ("filed_day","period_day","change_day"))>upper:
        raise CompletePacketReviewError("Original SEC header dates exceed physical source observation scope")
    candidates=[document for document in parsed["documents"] if document["document_type"]==selection["form"]]
    if len(candidates)!=1 or candidates[0]["filename"]!=selection["primary_filename"]:
        raise CompletePacketReviewError("Primary ownership XML is absent, duplicated or in another document identity")
    primary=candidates[0];index_parser,index_count=_index_primary_sequence(index_capture,xml_capture)
    if primary["sequence_raw"]!=index_parser:raise CompletePacketReviewError("Packet document sequence conflicts with original index")
    if header["public_document_count"]!=index_count:
        raise CompletePacketReviewError("Complete original document count conflicts with original index")
    span=primary["ownership_xml_span"];packet_xml=packet_capture.body[span["byte_start"]:span["byte_end"]]
    exact=packet_xml==xml_capture.body and span["sha256"]==xml_capture.sha256 and span["bytes"]==xml_capture.size_bytes
    clocks=header["clock_conflicts"]+index["clock_conflicts"]
    if header["acceptance_datetime_unqualified_display"]!=index["acceptance_datetime_unqualified_raw"]:clocks.append("HEADER_INDEX_UNQUALIFIED_ACCEPTANCE_CONFLICT")
    packet_size_conflict=index["index_declared_packet_bytes"]!=packet_capture.size_bytes
    if packet_size_conflict:report["gaps"].append("INDEX_COMPLETE_PACKET_SIZE_CONFLICT")
    if not exact:report["gaps"].append("ORIGINAL_PACKET_PRIMARY_XML_BYTE_BINDING_CONFLICT")
    if clocks:report["gaps"].extend(clocks)
    if index["xml_size_conflict"]:report["gaps"].append("INDEX_RAW_XML_SIZE_CONFLICT")
    transaction_days=[_scalar(node,"transactionDate/value") for tag in ("nonDerivativeTransaction","derivativeTransaction") for node in root.iter(tag)]
    signatures=[{"name":_scalar(node,"signatureName"),"date":_scalar(node,"signatureDate")} for node in root.findall("ownerSignature")]
    if not signatures or any(not item["name"] for item in signatures):
        raise CompletePacketReviewError("Primary XML has no complete original signature")
    for day in transaction_days+[item["date"] for item in signatures]:
        if _day(day)>_day(header["filed_day"]):raise CompletePacketReviewError("Primary transaction/signature day is missing or after filed day")
    supported=exact and not clocks and not packet_size_conflict and report["standalone_xml_index_scope_supported"]
    report.update(original_complete_packet_supported=bool(supported),original_header_scope_supported=not clocks and not packet_size_conflict,
        original_primary_xml_exact_binding_supported=exact,complete_original_packet=parsed,
        primary_document_binding={"document_type":primary["document_type"],"sequence_raw":primary["sequence_raw"],"filename":primary["filename"],
            "index_sequence_raw":index_parser,"packet_xml_original_sha256":span["sha256"],"retained_raw_xml_sha256":xml_capture.sha256,
            "index_public_document_count":index_count,
            "exact_original_bytes_equal":exact,"extracted_excerpt_is_not_a_whole_packet_substitute":True},
        issuer_as_filed=issuer,reporting_accounts_as_filed=owners,
        roles_as_filed=[{key:_scalar(node,"reportingOwnerRelationship/"+key) for key in ROLE_FIELDS} for node in root.findall("reportingOwner")],
        signatures_as_filed=signatures,signature_or_poa_is_approved_person_alias=False,
        transaction_row_count=len(transaction_days),holding_row_count=sum(1 for tag in ("nonDerivativeHolding","derivativeHolding") for node in root.iter(tag)),
        security_titles_as_filed=sorted({(_scalar(node,"securityTitle/value") or "") for table in root if table.tag in {"nonDerivativeTable","derivativeTable"} for node in table}),
        one_accession_documents_are_one_disclosure=True,source_inputs_available_at=max(capture.available_at for capture in captures.values()).isoformat())
    report["gaps"].extend(["NATURAL_PERSON_LEGAL_ACCOUNT_BINDING_UNVERIFIED","NO_APPROVED_NAME_ORDER_SIGNATURE_OR_POA_ALIAS",
        "DATED_CUSIP_CLASS_VALIDITY_UNVERIFIED","ACCOUNT_AND_ROLE_CONTINUITY_UNPROVEN","NO_EXECUTION_TRADING_OR_HISTORICAL_OOS_ACCEPTANCE"])
    return finish()


def _index_primary_sequence(index_capture,xml_capture):
    from market_checker_app.services.sec_owner_instrument_scope_review import _OwnershipIndex
    parser=_OwnershipIndex(index_capture.body)
    rows=[row for row in parser.rows if xml_capture.source_url in [urljoin(index_capture.source_url,href) for href in row["hrefs"]]]
    if len(rows)!=1 or not rows[0]["cells"]:raise CompletePacketReviewError("Original index primary sequence is absent or ambiguous")
    count=parser.labels.get("Documents","")
    if not re.fullmatch(r"[1-9][0-9]?",count) or int(count)>MAX_DOCUMENTS:
        raise CompletePacketReviewError("Original index document count is absent or unsupported")
    return rows[0]["cells"][0],int(count)
