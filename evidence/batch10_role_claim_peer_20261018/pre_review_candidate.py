"""Offline, source-bound named-person role assertions with literal precision.

The filing's assertions are not legal role intervals, reporting-account aliases,
or permission to attribute a Form4 account to the named biography subject.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import re

from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body
from market_checker_app.services.sec_complete_packet_review import parse_complete_sec_packet
from market_checker_app.services.sec_form4_person_bridge import _xml
from market_checker_app.services.sec_owner_instrument_scope_review import (
    OwnerInstrumentCapture as PersonRoleClaimCapture, ROLE_FIELDS, _scalar,
)
from market_checker_app.services.sec_person_kind_review import _clock, _index_proof, _norm, _url

VERSION = "sec-named-person-literal-role-claims-v1"
MAX_SELECTED_TEXT = 16_000
MAX_CLAIMS = 12


class PersonRoleClaimReviewError(ValueError):
    pass


def _physical_clock():
    return datetime.now(timezone.utc)


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise PersonRoleClaimReviewError("Role claims require exact source calendar days")
    return date.fromisoformat(value)


class _RoleDocument(HTMLParser):
    """Bounded visible contexts, their structural relation, and original DEI scope."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, body, selections):
        super().__init__(convert_charrefs=True)
        self.text = body.decode("utf-8", errors="strict")
        self.line_starts = [0]
        self.line_starts.extend(match.end() for match in re.finditer("\n", self.text))
        self.wanted = set(selections)
        self.stack = []; self.tables = []; self.active = {}; self.contexts = {}
        self.table_count = 0; self.nodes = 0; self.all_text = []
        self.facts = []; self.fact_active = []; self.xcontexts = {}; self.xactive = []
        self.feed(self.text)
        if self.active or self.fact_active or self.xactive:
            raise PersonRoleClaimReviewError("Original role disclosure has unclosed selected contexts")

    def _position(self):
        line, column = self.getpos()
        return self.line_starts[line - 1] + column

    def _open(self, key, tag):
        if key not in self.wanted:
            return
        if key in self.active or key in self.contexts:
            raise PersonRoleClaimReviewError("Role context locator is ambiguous")
        if any(frame["hidden"] for frame in self.stack):
            raise PersonRoleClaimReviewError("Hidden text cannot be the selected biography role clause")
        table = self.tables[-1]
        self.active[key] = {"tag": tag, "frame": self.stack[-1]["id"], "table": table["id"],
            "table_parent": table["parent"], "table_char_start": table["char_start"],
            "parts": [], "size": 0, "char_start": self._position()}

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 200_000 or len(self.stack) > 128:
            raise PersonRoleClaimReviewError("Role disclosure exceeds bounded HTML complexity")
        attrs = dict(attrs)
        style = re.sub(r"\s+", "", attrs.get("style", "").lower())
        hidden = (tag in {"script", "style", "noscript", "ix:header", "ix:hidden"}
                  or "hidden" in attrs or attrs.get("aria-hidden", "").lower() == "true"
                  or "display:none" in style or "visibility:hidden" in style)
        parent = self.stack[-1]["id"] if self.stack else None
        frame = {"id": self.nodes, "tag": tag, "hidden": hidden}
        self.stack.append(frame)
        if tag == "table":
            self.table_count += 1
            self.tables.append({"id": self.table_count, "frame": frame["id"], "parent": parent,
                                "char_start": self._position(), "row": 0, "cell": 0})
            self._open(f"table:{self.table_count}", tag)
        elif tag == "tr" and self.tables:
            self.tables[-1]["row"] += 1; self.tables[-1]["cell"] = 0
        elif tag in {"td", "th"} and self.tables:
            table = self.tables[-1]; table["cell"] += 1
            self._open(f'table:{table["id"]}/row:{table["row"]}/cell:{table["cell"]}', tag)
        if tag == "ix:nonnumeric" and attrs.get("name") in {"dei:DocumentType", "dei:EntityCentralIndexKey", "dei:EntityRegistrantName"}:
            self.fact_active.append({"frame": frame["id"], "name": attrs["name"], "context_ref": attrs.get("contextref"), "parts": []})
        if tag == "xbrli:context":
            key = attrs.get("id")
            if not key or key in self.xcontexts or self.xactive:
                raise PersonRoleClaimReviewError("Issuer DEI context is duplicated or nested")
            self.xactive.append({"frame": frame["id"], "id": key, "identifier": [], "start": [], "end": [], "segment": False})
        if self.xactive:
            if tag in {"xbrli:segment", "xbrli:scenario"}: self.xactive[-1]["segment"] = True
            if tag == "xbrli:identifier" and attrs.get("scheme") != "http://www.sec.gov/CIK":
                raise PersonRoleClaimReviewError("Issuer role context uses a different identifier scheme")
        if not any(item["hidden"] for item in self.stack):
            for context in self.active.values(): context["parts"].append(" ")
        if tag in self.VOID:
            self._close(tag, self._position() + len(self.get_starttag_text()))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID: self.handle_endtag(tag)

    def _close(self, tag, end):
        positions = [at for at, frame in enumerate(self.stack) if frame["tag"] == tag]
        if not positions:
            return
        at = positions[-1]; closed = self.stack[at:]; ids = {frame["id"] for frame in closed}
        for key, context in list(self.active.items()):
            if context["frame"] in ids:
                if context["frame"] != closed[0]["id"]:
                    raise PersonRoleClaimReviewError("Selected role context is not explicitly closed")
                context["text"] = _norm("".join(context.pop("parts")))
                context["char_end"] = end
                self.contexts[key] = context; del self.active[key]
        for fact in list(self.fact_active):
            if fact["frame"] in ids:
                fact["value"] = _norm("".join(fact.pop("parts")))
                self.facts.append(fact); self.fact_active.remove(fact)
        for context in list(self.xactive):
            if context["frame"] in ids:
                self.xcontexts[context["id"]] = context; self.xactive.remove(context)
        while self.tables and self.tables[-1]["frame"] in ids:
            self.tables.pop()
        del self.stack[at:]

    def handle_endtag(self, tag):
        end = self.text.find(">", self._position())
        if end < 0: raise PersonRoleClaimReviewError("Original role markup closing tag is incomplete")
        self._close(tag, end + 1)

    def handle_data(self, data):
        if not any(frame["hidden"] for frame in self.stack):
            self.all_text.append(data)
            for context in self.active.values():
                context["size"] += len(data)
                if context["size"] > MAX_SELECTED_TEXT:
                    raise PersonRoleClaimReviewError("Role clause exceeds selected text cap")
                context["parts"].append(data)
        for fact in self.fact_active:
            if sum(map(len, fact["parts"])) + len(data) > 8_000:
                raise PersonRoleClaimReviewError("Issuer fact exceeds text cap")
            fact["parts"].append(data)
        if self.xactive and self.stack:
            key = {"xbrli:identifier": "identifier", "xbrli:startdate": "start", "xbrli:enddate": "end"}.get(self.stack[-1]["tag"])
            if key:
                self.xactive[-1][key].append(data)

    def selected(self, locator, quote):
        context = self.contexts.get(locator)
        if context is None or context["text"] != quote:
            raise PersonRoleClaimReviewError("Exact selected original person/role quote changed")
        start, end = context["char_start"], context["char_end"]
        raw = self.text[start:end].encode("utf-8")
        return {"locator": locator, "quote": quote, "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
                "original_context_byte_start": len(self.text[:start].encode("utf-8")),
                "original_context_byte_end": len(self.text[:end].encode("utf-8")),
                "original_context_sha256": hashlib.sha256(raw).hexdigest(),
                "original_table_byte_start": len(self.text[:context["table_char_start"]].encode("utf-8")),
                "table_number": context["table"], "table_parent": context["table_parent"]}

    def issuer_scope(self, issuer_cik, issuer_name):
        wanted = {"dei:DocumentType": "DEF 14A", "dei:EntityCentralIndexKey": issuer_cik,
                  "dei:EntityRegistrantName": issuer_name}
        facts = {}
        for name, value in wanted.items():
            found = [fact for fact in self.facts if fact["name"] == name]
            if len(found) != 1 or found[0]["value"] != value:
                raise PersonRoleClaimReviewError("Original DEI issuer/document fact conflicts or is duplicated")
            facts[name] = found[0]
        refs = {item["context_ref"] for item in facts.values()}
        if len(refs) != 1 or None in refs:
            raise PersonRoleClaimReviewError("Issuer facts do not share an exact source context")
        context_ref = refs.pop(); context = self.xcontexts.get(context_ref)
        if (context is None or context["segment"] or _norm("".join(context["identifier"])) != issuer_cik
                or len(context["start"]) != 1 or len(context["end"]) != 1):
            raise PersonRoleClaimReviewError("Original issuer reporting context is incomplete or dimensional")
        start, end = _day(context["start"][0]), _day(context["end"][0])
        if start > end: raise PersonRoleClaimReviewError("Issuer reporting period is reversed")
        return {"issuer_cik": issuer_cik, "issuer_name_literal": issuer_name, "form": "DEF 14A",
                "dei_context_ref": context_ref, "report_period_start_day": start.isoformat(),
                "report_period_end_day": end.isoformat(), "report_period_is_biography_role_interval": False}


def _year(value, source_year):
    if not re.fullmatch(r"[0-9]{4}", value) or not 1900 <= int(value) <= source_year:
        raise PersonRoleClaimReviewError("Role year is outside the original disclosure scope")
    return {"literal": value, "precision": "YEAR", "year": int(value), "exact_day": None}


def _claims(person_quote, experience_quote, person_name, issuer_name, filing_day):
    header = re.fullmatch(re.escape(person_name) + r" Age: [0-9]{1,3} \| Director Since: ([0-9]{4}) \| Birthplace: .{1,150}", person_quote)
    if not header: raise PersonRoleClaimReviewError("Selected person context needs its explicit named director-since clause")
    source_year = _day(filing_day).year
    claims = [{"kind": "DIRECTOR_SINCE_ASSERTION", "title_literal": "Director", "source_clause": "Director Since: " + header[1],
               "start_assertion": _year(header[1], source_year), "end_assertion": None}]
    parts = experience_quote.split(" • ")
    employer = re.fullmatch(r"Experience: " + re.escape(issuer_name) + r" \(([0-9]{4})-present\)", parts[0])
    if not employer or not 2 <= len(parts) <= MAX_CLAIMS:
        raise PersonRoleClaimReviewError("Selected career context needs exact issuer experience scope")
    present = {"literal": "present", "precision": "SOURCE_RELATIVE_PRESENT", "anchor_filing_day": filing_day,
               "exact_end_day": None, "continues_through_capture_or_current_form4": False}
    experience = {"issuer_name_literal": issuer_name, "source_clause": parts[0],
                  "start_assertion": _year(employer[1], source_year), "end_assertion": dict(present),
                  "employment_or_legal_continuity_verified": False}
    seen = set()
    for part in parts[1:]:
        match = re.fullmatch(r"([^()]{1,200}) \(([0-9]{4})-([0-9]{4}|present)\)", part)
        if not match or match[1] in seen:
            raise PersonRoleClaimReviewError("Role title/range is missing, repeated, or outside literal year scope")
        seen.add(match[1]); start = _year(match[2], source_year)
        end = dict(present) if match[3] == "present" else _year(match[3], source_year)
        if end.get("year", source_year) < start["year"]:
            raise PersonRoleClaimReviewError("Original role assertion has a reversed year range")
        kind = "GENERIC_CAREER_POSITION_RANGE_ASSERTION" if match[1]=="Various positions of increasing authority" else "COMBINED_ROLE_RANGE_ASSERTION"
        claims.append({"kind": kind, "title_literal": match[1], "source_clause": part,
                       "start_assertion": start, "end_assertion": end})
    for claim in claims:
        claim.update(person_name_literal=person_name, issuer_name_literal=issuer_name,
                     source_assertion_only=True, legal_interval_verified=False,
                     exact_appointment_or_cessation_day_verified=False, continuous_role_verified=False)
    return experience, claims


def _account(capture, descriptor):
    required = {"accession", "form", "issuer_cik", "issuer_name", "owner_cik", "owner_name", "officer_title"}
    if not isinstance(descriptor, dict) or set(descriptor) != required or descriptor.get("form") != "4":
        raise PersonRoleClaimReviewError("Role comparison requires one exact Form4 account descriptor")
    issuer, key, filename = _url(capture.source_url)
    accession = descriptor["accession"]
    if (not isinstance(accession, str) or not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", accession)
            or key != accession.replace("-", "") or filename != accession + ".txt"
            or issuer.zfill(10) != descriptor["issuer_cik"]):
        raise PersonRoleClaimReviewError("Account packet source URL and descriptor accession/issuer conflict")
    parsed = parse_complete_sec_packet(capture.body, expected_accession=accession)
    header = parsed["header"]
    documents = [item for item in parsed["documents"] if item["document_type"] == "4"]
    if len(documents) != 1 or header["submission_type"] != "4" or header["clock_conflicts"]:
        raise PersonRoleClaimReviewError("Comparison packet lacks one unconflicted original Form4 document")
    if max(_day(header[key]) for key in ("filed_day", "period_day", "change_day")) > _clock(capture.observed_at).date():
        raise PersonRoleClaimReviewError("Account filing dates exceed original capture")
    span = documents[0]["ownership_xml_span"]
    root = _xml(capture.body[span["byte_start"]:span["byte_end"]])
    issuer_fact = {"cik": _scalar(root,"issuer/issuerCik"), "name": _scalar(root,"issuer/issuerName")}
    owners = [{"cik": _scalar(node,"reportingOwnerId/rptOwnerCik"), "name": _scalar(node,"reportingOwnerId/rptOwnerName")}
              for node in root.findall("reportingOwner")]
    if (issuer_fact != {"cik":descriptor["issuer_cik"],"name":descriptor["issuer_name"]}
            or owners != [{"cik":descriptor["owner_cik"],"name":descriptor["owner_name"]}]
            or [{"cik":x["cik"],"name":x["name"]} for x in header["account_roles"] if x["role"]=="ISSUER"] != [issuer_fact]
            or [{"cik":x["cik"],"name":x["name"]} for x in header["account_roles"] if x["role"]=="REPORTING-OWNER"] != owners
            or _scalar(root,"periodOfReport") != header["period_day"] or _scalar(root,"documentType") != "4"):
        raise PersonRoleClaimReviewError("Original comparison header/XML account fields conflict with descriptor")
    role = {key: _scalar(root, "reportingOwner/reportingOwnerRelationship/" + key) for key in ROLE_FIELDS}
    if role["officerTitle"] != descriptor["officer_title"] or role["isOfficer"] != "1":
        raise PersonRoleClaimReviewError("Original comparison officer role conflicts with descriptor")
    return {"accession": accession, "form": "4", "issuer": issuer_fact, "reporting_account": owners[0],
            "role_as_filed": role, "period_day": header["period_day"], "filing_day": header["filed_day"],
            "acceptance_datetime_unqualified_raw": header["acceptance_datetime_unqualified_raw"],
            "publication_timezone": "UNKNOWN", "source_capture_at": capture.observed_at.isoformat(),
            "original_packet_sha256": capture.sha256, "natural_person_cik_binding_verified": False}


def review_sec_person_role_claims(*, primary_capture: PersonRoleClaimCapture,
        index_capture: PersonRoleClaimCapture, account_packet_capture: PersonRoleClaimCapture,
        selection: dict, knowledge_at: datetime | None = None) -> dict:
    """Read literal biography role claims and compare bounded source facts only."""
    physical = _clock(_physical_clock())
    cutoff = physical if knowledge_at is None else _clock(knowledge_at)
    if cutoff > physical: raise PersonRoleClaimReviewError("Future role review knowledge is unavailable")
    report = {"review_version": VERSION, "status": "PARTIAL", "requested_knowledge_at": None if knowledge_at is None else cutoff.isoformat(),
        "review_ingested_at": physical.isoformat(), "source_role_claims_supported": False,
        "natural_person_account_binding_verified": False, "approved_name_alias": False,
        "legal_role_interval_verified": False, "continuous_role_verified": False,
        "instrument_identity_verified": False, "cusip_class_validity_verified": False,
        "historical_machine_availability_inferred": False, "issuer_attribution_allowed": False,
        "registry_modified": False, "scoring_applied": False, "trading_allowed": False,
        "quota_or_lease_modified": False, "network_requests_performed": 0, "gaps": []}
    def finish():
        completed = _clock(_physical_clock())
        if completed < physical: raise PersonRoleClaimReviewError("Physical role review clock rolled back")
        report["review_available_at"] = completed.isoformat()
        return report
    if cutoff < physical:
        report["gaps"] = ["AFTER_PHYSICAL_REVIEW_KNOWLEDGE_CUTOFF"]
        return finish()
    captures = {"primary":primary_capture, "index":index_capture, "account_packet":account_packet_capture}
    for capture in captures.values():
        if not isinstance(capture, PersonRoleClaimCapture): raise PersonRoleClaimReviewError("Role review needs pinned original captures")
        capture.validate()
    if len({capture.record_id for capture in captures.values()}) != 3:
        raise PersonRoleClaimReviewError("Role review capture IDs are duplicated")
    if any(capture.available_at > cutoff for capture in captures.values()):
        report["gaps"] = ["ORIGINAL_INPUT_NOT_YET_KNOWN"]
        return finish()
    summaries = {key:capture.summary() for key,capture in captures.items()}
    unavailable = False
    for key,capture in captures.items():
        if capture.retention != "ORIGINAL_BYTES" or _has_sensitive_body(capture.body):
            unavailable = True
            if capture.retention == "ORIGINAL_BYTES": summaries[key]["retention"] = "WITHHELD_SENSITIVE_BODY"
    report["captures"] = summaries
    if unavailable:
        report["gaps"] = ["REQUIRED_WHOLE_ORIGINAL_UNAVAILABLE_NO_SUBSTITUTE"]
        return finish()
    keys = {"issuer_cik", "issuer_name", "person_name", "person_context_locator", "person_context_quote",
            "role_context_locator", "role_context_quote", "account_descriptor"}
    try: encoded = json.dumps(selection, allow_nan=False).encode("utf-8")
    except (TypeError,ValueError,RecursionError) as error: raise PersonRoleClaimReviewError("Role selection is not bounded JSON") from error
    if (not isinstance(selection,dict) or set(selection)!=keys or len(encoded)>40_000 or _has_sensitive_body(encoded)
            or any(not isinstance(selection[key],str) or not 1<=len(selection[key])<=250 for key in ("issuer_name","person_name"))
            or not isinstance(selection["issuer_cik"],str) or not re.fullmatch(r"[0-9]{10}",selection["issuer_cik"])):
        raise PersonRoleClaimReviewError("Role review selection fields are malformed or unbounded")
    person_locator = selection["person_context_locator"]; role_locator = selection["role_context_locator"]
    first = re.fullmatch(r"table:([1-9][0-9]{0,3})", str(person_locator))
    second = re.fullmatch(r"table:([1-9][0-9]{0,3})/row:([1-9][0-9]{0,2})/cell:([1-9][0-9]{0,2})", str(role_locator))
    if (not first or not second or int(second[1])!=int(first[1])+1
            or any(not isinstance(selection[key],str) or not 1<=len(selection[key])<=MAX_SELECTED_TEXT
                   or selection[key]!=_norm(selection[key]) for key in ("person_context_quote","role_context_quote"))):
        raise PersonRoleClaimReviewError("Role selection needs exact neighboring biography/career contexts")
    for capture in (primary_capture,index_capture):
        if b"\x00" in capture.body: raise PersonRoleClaimReviewError("Encoded replacement markup is unsupported")
    index = _index_proof(index_capture, primary_capture)
    if index["issuer_cik"] != selection["issuer_cik"]:
        raise PersonRoleClaimReviewError("Role issuer selection conflicts with original index")
    accepted = index["acceptance_datetime_unqualified_raw"]
    if not isinstance(accepted,str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}",accepted):
        raise PersonRoleClaimReviewError("Role source needs its literal unqualified acceptance display")
    accepted_day = datetime.strptime(accepted,"%Y-%m-%d %H:%M:%S").date()
    clock_conflicts = ["INDEX_ACCEPTANCE_DATE_DIFFERS_FROM_FILING_DAY"] if accepted_day!=_day(index["filing_day"]) else []
    document = _RoleDocument(primary_capture.body, (person_locator,role_locator))
    person = document.selected(person_locator, selection["person_context_quote"])
    role = document.selected(role_locator, selection["role_context_quote"])
    if person["table_parent"]!=role["table_parent"] or person["original_context_byte_end"]>=role["original_context_byte_start"]:
        raise PersonRoleClaimReviewError("Selected biography and career clauses do not share ordered sibling table scope")
    between = primary_capture.body[person["original_context_byte_end"]:role["original_table_byte_start"]]
    if between.strip():
        raise PersonRoleClaimReviewError("Intervening content breaks named biography/career table adjacency")
    issuer = document.issuer_scope(selection["issuer_cik"],selection["issuer_name"])
    if _day(issuer["report_period_end_day"])>_day(index["filing_day"]):
        raise PersonRoleClaimReviewError("Issuer report period follows source filing day")
    experience,claims = _claims(person["quote"],role["quote"],selection["person_name"],selection["issuer_name"],index["filing_day"])
    account = _account(account_packet_capture,selection["account_descriptor"])
    if account["issuer"]["cik"]!=issuer["issuer_cik"]:
        raise PersonRoleClaimReviewError("Comparison account belongs to another issuer CIK scope")
    biography_accession = f'{index["accession_key"][:10]}-{index["accession_key"][10:12]}-{index["accession_key"][12:]}'
    distinct = biography_accession!=account["accession"]
    title = account["role_as_filed"]["officerTitle"]
    matching = [at for at,claim in enumerate(claims) if re.search(r"(?<![A-Za-z])"+re.escape(title)+r"(?![A-Za-z])",claim["title_literal"])]
    report.update(source_role_claims_supported=True, dated_role_source_clock_consistent=not clock_conflicts,
        source_issuer_scope=issuer,
        named_person_clause=person, issuer_career_clause=role, issuer_experience_assertion=experience,
        role_claims=claims, role_claim_count=sum(claim["kind"]!="GENERIC_CAREER_POSITION_RANGE_ASSERTION" for claim in claims),
        career_assertion_count=len(claims), comparison_account_facts=account,
        source_document={**index,"accession":biography_accession,"source_url":primary_capture.source_url,
            "source_sha256":primary_capture.sha256,"publication_timezone":"UNKNOWN",
            "clock_conflicts":clock_conflicts,
            "original_biography_sgml_header_available":False,"original_inline_dei_header_validated":True},
        source_comparison={"distinct_accessions":distinct,"underlying_disclosure_count":2 if distinct else 1,
            "named_person_disclosure_count":1,"account_packet_document_count_is_independent_disclosure_count":False,
            "distinct_disclosures_are_independent_legal_identity_proof":False,
            "literal_officer_title_component_agreement":bool(matching),"matching_bio_claim_indices":matching,
            "role_title_component_agreement_is_identity_or_continuity_proof":False,
            "bio_person_name_equals_raw_account_name":selection["person_name"]==account["reporting_account"]["name"],
            "issuer_cik_scope_equal":True,"issuer_raw_names_equal":selection["issuer_name"]==account["issuer"]["name"],
            "approved_person_or_issuer_name_alias":False,"independent_natural_person_account_binding":False},
        source_inputs_available_at=max(capture.available_at for capture in captures.values()).isoformat())
    if index["publisher_size_conflict"]: report["gaps"].append("PUBLISHER_INDEX_SERVED_BODY_SIZE_CONFLICT_PRESERVED")
    report["gaps"].extend(clock_conflicts)
    report["gaps"].extend(["YEAR_PRECISION_IS_NOT_AN_EXACT_APPOINTMENT_OR_CESSATION_DAY",
        "PRESENT_IS_SOURCE_RELATIVE_NOT_CONTINUITY_THROUGH_CAPTURE_OR_FORM4",
        "NAMED_BIOGRAPHY_TO_REPORTING_ACCOUNT_CIK_UNVERIFIED","PERSON_AND_ISSUER_NAME_ALIASES_UNAPPROVED",
        "LEGAL_ROLE_CONTINUITY_AND_CUSIP_CLASS_IDENTITY_UNVERIFIED","NO_HISTORICAL_MACHINE_AVAILABILITY_OR_OOS_ACCEPTANCE"])
    return finish()
