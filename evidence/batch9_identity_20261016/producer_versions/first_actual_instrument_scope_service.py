"""Original SEC registered-security rows and separate dated CUSIP/class facts.

A registration disclosure, a Schedule13 subject, a certificate specimen and an
issued instrument's validity interval are different scopes. No identity alias,
historical interval, registry, score or source collection is created here.
"""
from __future__ import annotations

from array import array
from datetime import date, datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import re
from urllib.parse import parse_qs, urljoin, urlsplit
from xml.etree import ElementTree as ET

from market_checker_app.services.sec13f_identity_bulk import _clock, _name, _valid_cusip, parse_ticker_filing
from market_checker_app.services.sec_corporate_action_bridge import class_terms, issuer_names
from market_checker_app.services.sec_person_kind_review import _Index
from market_checker_app.services.sec_symbol_action_scope_review import (
    _captures, _encoded, _filing, _snapshot, symbol_action_passages,
)
from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body

VERSION = "sec-original-registered-instrument-class-scope-v1"
MAX_BODY_BYTES, MAX_REQUEST_BYTES = 4_000_000, 48_000
_VOID = {"br","hr","meta","link","img","input","area","base","col","embed","param","source","track","wbr"}
_HEADER = ("Title of each class", "Trading symbol(s)", "Name of each exchange on which registered")


def _physical_clock():
    return datetime.now(timezone.utc)


class _Tables(HTMLParser):
    """Whole original row pins, physical column spans and local inline facts."""
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.body, self.text = body, body.decode("utf-8",errors="strict")
        self.lines=[0]+[match.end() for match in re.finditer("\n",self.text)]
        self.offsets,position=array("I",[0]),0
        for character in self.text:
            position+=len(character.encode());self.offsets.append(position)
        self.stack,self.tables,self.active=[],[],[]
        self.namespaces={}
        self.nodes,self.table_count=0,0
        self.feed(self.text);self.close()
        if self.active:raise ValueError("Incomplete original security table")

    def position(self):
        line,column=self.getpos();return self.lines[line-1]+column

    def handle_starttag(self,tag,attrs):
        self.nodes+=1
        if self.nodes>100_000 or len(self.stack)>128:raise ValueError("Registered table structure budget exceeded")
        attrs=dict(attrs);compact=str(attrs.get("style","")).replace(" ","").lower()
        for key,value in attrs.items():
            if key.startswith("xmlns:"):
                prefix=key[6:]
                if prefix in self.namespaces and self.namespaces[prefix]!=value:raise ValueError("Rebound registered inline namespace")
                self.namespaces[prefix]=value
        hidden=bool(self.stack and self.stack[-1][1]) or tag in {"script","style","noscript","ix:hidden","ix:header"}
        hidden|="hidden" in attrs or "display:none" in compact or "visibility:hidden" in compact
        if hidden and self.active:self.active[-1]["hidden_content"]=True
        if tag=="table":
            self.table_count+=1
            if self.table_count>128:raise ValueError("Registered table count budget exceeded")
            self.active.append({"table_number":self.table_count,"start":self.position(),"rows":[],"row":None,"cell":None,"hidden_content":hidden})
        if self.active:
            table=self.active[-1]
            if tag=="tr":
                if table["row"] is not None or len(table["rows"])>=2_000:raise ValueError("Ambiguous or excessive original rows")
                table["row"]={"row_number":len(table["rows"])+1,"start":self.position(),"cells":[],"column":0}
            if tag in {"td","th"} and table["row"] is not None:
                if table["cell"] is not None:raise ValueError("Nested original security cell")
                colspan,rowspan=int(attrs.get("colspan",1)),int(attrs.get("rowspan",1))
                if not 1<=colspan<=32 or not 1<=rowspan<=32:raise ValueError("Unbounded original column/row span")
                table["cell"]={"column_start":table["row"]["column"],"column_end":table["row"]["column"]+colspan,
                               "rowspan":rowspan,"parts":[],"facts":[]}
                table["row"]["column"]+=colspan
            if tag=="ix:nonnumeric" and table["cell"] is not None:
                name=str(attrs.get("name","")).lower()
                if name in {"dei:security12btitle","dei:tradingsymbol","dei:securityexchangename"}:
                    transform=attrs.get("format")
                    supported_transform=(transform is None or name=="dei:securityexchangename" and transform=="ixt-sec:exchnameen"
                                         and self.namespaces.get("ixt-sec")=="http://www.sec.gov/inlineXBRL/transformation/2015-08-31")
                    if (not attrs.get("contextref") or attrs.get("continuedat") or attrs.get("escape") or not supported_transform
                            or str(attrs.get("xsi:nil","")).lower() in {"1","true"}):
                        raise ValueError("Unsupported registered-security inline fact")
                    table["cell"]["facts"].append({"name":name,"context_ref":attrs["contextref"],"fact_id":attrs.get("id"),"format":transform})
        if tag not in _VOID:self.stack.append((tag,hidden))

    def handle_data(self,data):
        if self.active and self.active[-1]["cell"] is not None:
            cell=self.active[-1]["cell"];cell["parts"].append(data)
            if sum(map(len,cell["parts"]))>8_000:raise ValueError("Original security cell text bound exceeded")

    def handle_endtag(self,tag):
        if self.active:
            table=self.active[-1]
            if tag in {"td","th"} and table["cell"] is not None:
                cell=table["cell"];cell["text"]=" ".join("".join(cell.pop("parts")).split())
                table["row"]["cells"].append(cell);table["cell"]=None
            if tag=="tr" and table["row"] is not None:
                if table["cell"] is not None:raise ValueError("Unclosed original security cell")
                row=table["row"];stop=self.text.find(">",self.position())+1
                if stop<=0:raise ValueError("Incomplete original security row")
                left,right=self.offsets[row.pop("start")],self.offsets[stop]
                row.pop("column");row.update(byte_start=left,byte_end=right,sha256=hashlib.sha256(self.body[left:right]).hexdigest())
                table["rows"].append(row);table["row"]=None
            if tag=="table":
                if table["row"] is not None or table["cell"] is not None:raise ValueError("Unclosed original security table row")
                self.active.pop();stop=self.text.find(">",self.position())+1
                table.update(byte_start=self.offsets[table.pop("start")],byte_end=self.offsets[stop])
                table.pop("row");table.pop("cell");self.tables.append(table)
        for index in range(len(self.stack)-1,-1,-1):
            if self.stack[index][0]==tag:del self.stack[index:];break


def registered_security_tables(body:bytes)->list[dict]:
    """Return original table/row pins; choose an explicit Section12(b) scope."""
    if not isinstance(body,bytes) or not 0<len(body)<=MAX_BODY_BYTES or _has_sensitive_body(body):
        raise ValueError("Safe bounded whole original bytes are required")
    return _Tables(body).tables


class _SubjectIndex(_Index):
    def __init__(self,body):
        self.companies=[];self.parts=None;self.links=[]
        super().__init__(body)

    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=="span" and attrs.get("class")=="companyName":
            if self.parts is not None:raise ValueError("Nested original subject/filer context")
            self.parts=[];self.links=[]
        if tag=="a" and self.parts is not None:self.links.append(attrs.get("href",""))
        super().handle_starttag(tag,list(attrs.items()))

    def handle_data(self,data):
        if self.parts is not None:self.parts.append(data)
        super().handle_data(data)

    def handle_endtag(self,tag):
        if tag=="span" and self.company_depth==1 and self.parts is not None:
            text=" ".join("".join(self.parts).split())
            match=re.fullmatch(r"(.{1,250}) \((Filed by|Subject)\) CIK\s*:\s*([0-9]{10}) \(see all company filings\)",text)
            if match is None:raise ValueError("Explicit original Filed-by/Subject roles are required")
            name,role,cik=match.groups()
            links=[]
            for href in self.links:
                parsed=urlsplit(href);query=parse_qs(parsed.query)
                if parsed.path=="/cgi-bin/browse-edgar":links.append((query.get("CIK"),query.get("action")))
            if links!=[([cik],["getcompany"])]:raise ValueError("Subject/filer role link conflicts with its exact CIK")
            self.companies.append({"name":name,"role":role,"cik":cik,"literal_context":text});self.parts=None
        super().handle_endtag(tag)


def _schedule_index(source,index,form,scope):
    accession=source["accession"];expected=f"{accession[:10]}-{accession[10:12]}-{accession[12:]}-index"
    if index["accession"]!=accession or index["filename"] not in {expected+".html",expected+".htm"}:
        raise ValueError("Schedule source/index exact accession conflict")
    parsed=_SubjectIndex(index["body"])
    subject=[row for row in parsed.companies if row["role"]=="Subject"]
    filed_by=[row for row in parsed.companies if row["role"]=="Filed by"]
    if len(subject)!=len(filed_by) or len(subject)!=1 or subject[0]["cik"]!=scope["issuer_cik"]:
        raise ValueError("Schedule index needs one exact separate subject and reporting filer")
    rows=[]
    for row in parsed.rows:
        for href in row["hrefs"]:
            url=urljoin(index["url"],href);parts=urlsplit(url)
            if parts.path.endswith("/"+source["filename"]) and "/xsl" not in parts.path.lower():rows.append((row,url))
    if len(rows)!=1 or len(rows[0][0]["cells"])<5 or rows[0][0]["cells"][-2]!=form:
        raise ValueError("Schedule index does not identify one exact raw untransformed XML row")
    row,url=rows[0];parts=urlsplit(url).path.split("/")
    if len(parts)!=7 or parts[5]!=accession or parts[4].zfill(10)!=filed_by[0]["cik"]:
        raise ValueError("Schedule raw XML route conflicts with its reporting-filer accession scope")
    filed=date.fromisoformat(parsed.labels["Filing Date"]);declared=int(row["cells"][-1])
    if declared<=0 or filed>min(source["observed"],index["observed"]).date():raise ValueError("Schedule index filing day or declared length conflict")
    gaps=[]
    if url!=source["url"]:gaps.append("CAPTURED_SUBJECT_ROUTE_DIFFERS_FROM_INDEX_FILER_ROUTE")
    if declared!=source["bytes"]:gaps.append("INDEX_DECLARED_SERVED_BYTE_CONFLICT")
    return {"source_url":source["url"],"accession":accession,"form":form,"filing_on":filed.isoformat(),"publication_at":None,
            "acceptance_unqualified_raw":parsed.labels.get("Accepted"),"acceptance_timezone_inferred":False,
            "index_companies":parsed.companies,"index_raw_xml_url":url,"index_declared_bytes":declared,
            "publisher_size_conflict":declared!=source["bytes"],"as_filed_body_version_verified":False,"blockers":gaps}


def _schedule_fact(source,filing):
    body=source["body"]
    if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b",body,re.I):raise ValueError("Schedule XML DTD/entity declarations are forbidden")
    root=ET.fromstring(body);namespace=root.tag.split("}")[0]+"}"
    if root.tag not in {"{http://www.sec.gov/edgar/schedule13g}edgarSubmission","{http://www.sec.gov/edgar/schedule13D}edgarSubmission"} or sum(1 for _ in root.iter())>30_000:
        raise ValueError("Unrecognized or overbudget original Schedule XML")
    def one(parent,name):
        matches=[child for child in parent if child.tag==namespace+name]
        if len(matches)!=1:raise ValueError("Missing or duplicated exact Schedule field: "+name)
        return matches[0]
    def value(parent,name):
        node=one(parent,name)
        if len(node) or not (node.text or "").strip():raise ValueError("Malformed original Schedule scalar")
        return node.text.strip()
    form=value(one(root,"headerData"),"submissionType");cover=one(one(root,"formData"),"coverPageHeader");issuer=one(cover,"issuerInfo")
    if form!=filing["form"] or ("13G" in form)!=("schedule13g" in namespace):raise ValueError("Schedule namespace/index form conflict")
    cik=value(issuer,"issuerCik");name=value(issuer,"issuerName");title=value(cover,"securitiesClassTitle")
    singular=issuer.findall(namespace+"issuerCusip");plural=issuer.findall(namespace+"issuerCusips")
    if len(singular)==1 and not plural:cusip=value(issuer,"issuerCusip")
    elif len(plural)==1 and not singular and len(plural[0])==1:cusip=value(plural[0],"issuerCusipNumber")
    else:raise ValueError("Original Schedule requires one exact instrument CUSIP")
    if not re.fullmatch(r"[0-9]{1,10}",cik) or int(cik)==0 or not _valid_cusip(cusip):raise ValueError("Invalid exact Schedule issuer/CUSIP")
    event=value(cover,"eventDateRequiresFilingThisStatement" if "13G" in form else "dateOfEvent")
    day=datetime.strptime(event,"%m/%d/%Y").date()
    if day>date.fromisoformat(filing["filing_on"]):raise ValueError("Schedule event follows its original filing day")
    return {"issuer_cik":cik.zfill(10),"issuer_name":name,"class_title":title,"cusip":cusip,"event_on":day.isoformat()}


def _pin(row):
    return {key:row[key] for key in ("byte_start","byte_end","sha256")}


def _registered(claim,source,filing,scope):
    if filing["publication_at"] is None:raise ValueError("Registered context parser requires its declared original SEC timestamp, not a substituted clock")
    snapshot=_snapshot(source,filing,"ticker_filing")
    names=[row for row in issuer_names(snapshot) if row["context_ref"]==claim.get("context_ref")]
    securities=[row for row in parse_ticker_filing(snapshot) if row["context_ref"]==claim.get("context_ref")]
    if len(names)!=1 or len(securities)!=1:raise ValueError("Exact same-context original issuer/security facts are missing or ambiguous")
    name,security=names[0],securities[0]
    if (name["issuer_cik"]!=scope["issuer_cik"] or security["issuer_cik"]!=scope["issuer_cik"] or name["issuer_name"]!=claim.get("issuer_name")
            or name["anchor_date"]!=security["anchor_date"] or security["ticker"]!=scope["symbol"]
            or class_terms(security["class_title"])!=class_terms(scope["class_title"])):
        raise ValueError("Exact registered CIK/name/symbol/class/anchor tuple conflicts")
    section=claim.get("registration_section")
    if section not in symbol_action_passages(source["body"]) or not section["text"].startswith("Securities registered pursuant to Section 12(b) of the Act:"):
        raise ValueError("Select the whole original visible Section12(b) declaration")
    tables=[table for table in registered_security_tables(source["body"]) if table["table_number"]==claim.get("table_number")]
    if len(tables)!=1:raise ValueError("Select one exact original registered-security table")
    table=tables[0]
    if (table["hidden_content"] or not section["byte_start"]<=table["byte_start"]<table["byte_end"]<=section["byte_end"]<=64_000):
        raise ValueError("Registered table is hidden, outside its declaration or outside the bounded cover")
    header=[row for row in table["rows"] if row["row_number"]==claim.get("header_row")]
    security_rows=[row for row in table["rows"] if row["row_number"]==claim.get("security_row")]
    if len(header)!=1 or len(security_rows)!=1 or _pin(header[0])!=claim.get("header_pin") or _pin(security_rows[0])!=claim.get("security_pin"):
        raise ValueError("Original registered header/data row pin conflict")
    headers=[cell for cell in header[0]["cells"] if cell["text"]]
    if tuple(cell["text"] for cell in headers[:3])!=_HEADER or len(headers) not in {3,4}:
        raise ValueError("Registered table does not have exact class/symbol/exchange headers")
    if len(headers)==4 and headers[3]["text"] not in {"CUSIP","CUSIP number"}:raise ValueError("Unrecognized registered CUSIP column")
    values=[]
    for cell in headers:
        matches=[data for data in security_rows[0]["cells"] if (data["column_start"],data["column_end"])==(cell["column_start"],cell["column_end"])]
        if cell["rowspan"]!=1 or len(matches)!=1 or matches[0]["rowspan"]!=1:raise ValueError("Registered header/data columns are misaligned or span other rows")
        values.append(matches[0])
    for cell,field in zip(values,("dei:security12btitle","dei:tradingsymbol","dei:securityexchangename")):
        if (len(cell["facts"])!=1 or cell["facts"][0]["name"]!=field or cell["facts"][0]["context_ref"]!=security["context_ref"]):
            raise ValueError("Registered class/symbol/exchange must share their original local inline context")
    if values[0]["text"]!=security["class_title"] or values[1]["text"]!=security["ticker"] or not values[2]["text"]:
        raise ValueError("Visible registered row disagrees with its same-context inline facts")
    cusip=values[3]["text"] if len(values)==4 else None
    if cusip is not None and not _valid_cusip(cusip):raise ValueError("Malformed exact same-row registered CUSIP")
    return {"source_key":claim["source_key"],"issuer_cik":name["issuer_cik"],"issuer_name":name["issuer_name"],
            "context_ref":security["context_ref"],"context_anchor_on":security["anchor_date"],"symbol":security["ticker"],"class_title":security["class_title"],
            "exchange_name":values[2]["text"],"same_row_cusip":cusip,"registration_section":dict(section),"header_pin":_pin(header[0]),"security_pin":_pin(security_rows[0]),
            "registered_class_symbol_fact_supported":True,"context_anchor_proves_legal_name_effective_day":False,"issued_instrument_validity_verified":False}


class _CertificateMedia(HTMLParser):
    def __init__(self,body):
        super().__init__(convert_charrefs=True);self.images=[];self.parts=[];self.hidden=0
        self.feed(body.decode("utf-8",errors="strict"));self.close()
    def handle_starttag(self,tag,attrs):
        if tag in {"script","style","ix:hidden"}:self.hidden+=1
        if tag=="img":self.images.append(dict(attrs).get("src"))
    def handle_endtag(self,tag):
        if tag in {"script","style","ix:hidden"} and self.hidden:self.hidden-=1
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)


def review_sec_instrument_class_scope(request:dict,captures:dict,*,as_of:datetime|None=None)->dict:
    """Pure original-byte review; all missing scope remains explicit and unused."""
    required={"scope","filings","registered_claims","schedule_claims","certificate_claims"}
    if not isinstance(request,dict) or set(request)!=required or len(_encoded(request))>MAX_REQUEST_BYTES or _has_sensitive_body(_encoded(request)):
        raise ValueError("Select an exact bounded safe instrument/class review request")
    scope=request["scope"]
    if (not isinstance(scope,dict) or set(scope)!={"issuer_cik","symbol","class_title","cusip"}
            or not isinstance(scope["issuer_cik"],str) or not re.fullmatch(r"[0-9]{10}",scope["issuer_cik"]) or int(scope["issuer_cik"])==0
            or not isinstance(scope["symbol"],str) or not re.fullmatch(r"[A-Z0-9./-]{1,15}",scope["symbol"])
            or not isinstance(scope["cusip"],str) or not _valid_cusip(scope["cusip"]) or class_terms(scope["class_title"]) is None
            or class_terms(scope["class_title"])["par_term"]=="UNSPECIFIED" or class_terms(scope["class_title"])["share_class"]=="EQUITY:UNCLASSIFIED"):
        raise ValueError("Exact independent issuer/symbol/class/par/CUSIP scope is required")
    for key in required-{"scope"}:
        if not isinstance(request[key],list) or len(request[key])>16 or any(not isinstance(row,dict) for row in request[key]):raise ValueError("Review claims exceed their explicit bounded scope")
    physical=_clock(_physical_clock());cutoff=physical if as_of is None else _clock(as_of)
    if cutoff<physical:raise ValueError("AFTER_PHYSICAL_REVIEW_KNOWLEDGE_CUTOFF")
    if cutoff>physical:raise ValueError("Future review cutoff is unavailable")
    sources=_captures(captures,scope,cutoff,physical);filings={}
    for row in request["filings"]:
        key,proof=row.get("source_key"),row.get("proof_key")
        if key in filings or key not in sources or proof not in sources:raise ValueError("Each source needs one exact original publication proof")
        source,index=sources[key],sources[proof]
        if row.get("proof_kind")=="SCHEDULE13_INDEX" and source["body"] is not None and index["body"] is not None:
            filings[key]=_schedule_index(source,index,row.get("form"),scope)
        else:filings[key]=_filing(source,index,row)
        entry=filings[key];accepted=entry.get("acceptance_unqualified_raw")
        entry["acceptance_calendar_day_conflict"]=bool(accepted and entry["filing_on"] and accepted[:10]!=entry["filing_on"])
        if entry["acceptance_calendar_day_conflict"]:entry["blockers"].append("INDEX_ACCEPTANCE_CALENDAR_DAY_DIFFERS_FROM_FILING_DAY")
    registered,schedules,certificates,gaps=[],[],[],[]
    for claim in request["registered_claims"]:
        key=claim.get("source_key")
        if key not in filings:raise ValueError("Registered claim lacks its exact filing descriptor")
        if sources[key]["body"] is None or filings[key]["filing_on"] is None:gaps.append("REGISTERED_WHOLE_SOURCE_UNAVAILABLE");continue
        registered.append(_registered(claim,sources[key],filings[key],scope))
    for claim in request["schedule_claims"]:
        key=claim.get("source_key")
        if key not in filings:raise ValueError("Schedule claim lacks its exact filing descriptor")
        if sources[key]["body"] is None or filings[key]["filing_on"] is None:gaps.append("SCHEDULE_WHOLE_SOURCE_UNAVAILABLE");continue
        fact=_schedule_fact(sources[key],filings[key]);fact_gaps=[]
        if fact["issuer_cik"]!=scope["issuer_cik"] or fact["cusip"]!=scope["cusip"]:fact_gaps.append("EXACT_SCHEDULE_ISSUER_CUSIP_CONFLICT")
        if class_terms(fact["class_title"])!=class_terms(scope["class_title"]):fact_gaps.append("SCHEDULE_CLASS_OR_PAR_TERM_UNRESOLVED")
        source_fact_supported=not fact_gaps
        paired=[row for row in registered if row["context_anchor_on"]==fact["event_on"] and _name(row["issuer_name"])==_name(fact["issuer_name"])
                and sources[row["source_key"]]["accession"]!=sources[key]["accession"]]
        if not paired:fact_gaps.append("INDEPENDENT_SAME_DAY_REGISTERED_SYMBOL_CLASS_BINDING_MISSING")
        schedules.append({"source_key":key,**fact,"exact_class_cusip_source_fact_supported":source_fact_supported,
                          "same_day_registered_symbol_bridge_supported":not fact_gaps,
                          "event_day_cannot_be_replaced_by_later_cover_day":True,"blockers":fact_gaps})
    for claim in request["certificate_claims"]:
        key=claim.get("source_key")
        if key not in filings:raise ValueError("Certificate wrapper lacks its exact original index")
        source=sources[key]
        if source["body"] is None:certificates.append({"source_key":key,"status":"WHOLE_BODY_UNAVAILABLE","class_cusip_supported":False});gaps.append("CERTIFICATE_WHOLE_SOURCE_UNAVAILABLE");continue
        media=_CertificateMedia(source["body"])
        refs=[]
        for reference in media.images:
            if not isinstance(reference,str) or not re.fullmatch(r"[A-Za-z0-9_.-]+\.(?:jpg|jpeg|png)",reference,re.I):raise ValueError("Unsupported certificate media reference")
            refs.append(urljoin(source["url"],reference))
        certificates.append({"source_key":key,"status":"IMAGE_WRAPPER_ONLY" if refs else "UNREVIEWED_CERTIFICATE_TEXT",
                             "original_image_references":refs,"image_bodies_available":False,"class_cusip_supported":False,
                             "filename_or_form_label_is_instrument_identity":False,"specimen_is_issued_instrument":False})
        gaps.append("CERTIFICATE_IMAGE_BYTES_UNAVAILABLE" if refs else "EXACT_ORIGINAL_CERTIFICATE_FIELD_PROOF_MISSING")
    for filing in filings.values():gaps.extend(filing["blockers"])
    for fact in schedules:gaps.extend(fact["blockers"])
    if not registered:gaps.append("EXACT_REGISTERED_CLASS_SYMBOL_SOURCE_MISSING")
    if not any(fact["same_day_registered_symbol_bridge_supported"] for fact in schedules) and not any(row["same_row_cusip"]==scope["cusip"] for row in registered):
        gaps.append("EXACT_DATED_CLASS_CUSIP_SYMBOL_BRIDGE_UNRESOLVED")
    # This review records declarations, not a completed action or an interval.
    gaps.extend(["ISSUED_INSTRUMENT_VALIDITY_INTERVAL_UNPROVEN","SYMBOL_ACTION_EXACT_EFFECTIVE_DAY_NOT_ESTABLISHED_BY_REGISTERED_TABLE"])
    report={"schema_version":VERSION,"status":"PRIMARY_INSTRUMENT_SCOPE_PARTIAL","scope":dict(scope),"review_recorded_at":physical.isoformat(),"knowledge_at":physical.isoformat(),
            "source_knowledge_at":max(row["known"] for row in sources.values()).isoformat(),"as_of":cutoff.isoformat(),"filings":filings,
            "registered_security_facts":registered,"schedule_subject_facts":schedules,"certificate_scopes":certificates,"blockers":sorted(set(gaps)),
            "registered_source_anchor_count":len({(row["source_key"],row["context_anchor_on"]) for row in registered}),
            "new_verified_identities":0,"current_q3_verified_instrument_coverage":0,"production_registry_changed":False,"automatic_alias_allowed":False,
            "interval_extension_allowed":False,"historical_machine_availability_inferred":False,"score_changed":False,"trading_allowed":False,"network_requests_performed":0,
            "capture_pins":{key:{"url":row["url"],"sha256":row["sha256"],"bytes":row["bytes"],"metadata_sha256":row["metadata_sha256"],
                                 "observed_at":row["observed"].isoformat(),"known_at":row["known"].isoformat(),"body_available":row["body"] is not None} for key,row in sources.items()}}
    report["review_sha256"]=hashlib.sha256(_encoded(report)).hexdigest();return report
