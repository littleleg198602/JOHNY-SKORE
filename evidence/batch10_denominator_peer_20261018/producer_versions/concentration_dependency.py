"""Exact original customer concentration; reporting revenue is not customer revenue.

An inline XBRL generic customer member is retained verbatim. Only the complete
same-sentence narrative can associate its ordered percentages with a disclosed
name. A separate legal snapshot never supplies a historical validity interval.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
from io import BytesIO
import re
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from market_checker_app.services.sec_ir_disclosure_authority_review import _filing_index
from market_checker_app.services.sec_operational_metrics_review import _Rejected, _aware, _canonical, _dei_and_contexts, _period, _sensitive
from market_checker_app.services.sec_purchase_obligation_scope_review import _Blocks, _expanded, _index_original, _known, _VisibleIndex
from market_checker_app.services.sec_supply_chain_scope_review import _issuer, _legal_snapshot, _original
from market_checker_app.services.sec_person_kind_review import _Index


REVIEW_VERSION = "sec-original-supplier-concentration-v1"
MAX_BODY_BYTES = 4_000_000
MAX_REQUEST_BYTES = 32_000
_IX = "http://www.xbrl.org/2013/inlineXBRL"
_XBRLI = "http://www.xbrl.org/2003/instance"
_XBRLDI = "http://xbrl.org/2006/xbrldi"
_SELECTION_KEYS = {"issuer_ticker", "issuer_context_id", "issuer_name", "issuer_cik", "fiscal_period_end",
    "claim_role", "reporting_scope", "scope_passage", "fiscal_calendar_passage", "section_heading",
    "concentration_passage", "alias_passage", "counterparty_label", "counterparty_kind",
    "counterparty_legal_name_literal", "denominator_literal", "channel_qualifier_literal", "aggregation_qualifier_literal", "periods"}
_ANONYMOUS = re.compile(r"(?:customer|supplier|client|company)\s*(?:[A-Z]|[0-9]{1,2})|(?:one|two|three|a|a single|an? unnamed|an? unidentified|our largest|the largest)\s+(?:customer|supplier)s?", re.I)


def _fail(reason):
    raise _Rejected(reason)


def _physical_clock():
    return datetime.now(timezone.utc)


def _input(request, captures):
    if (not isinstance(request, Mapping) or set(request) - {"selection", "counterparty_selection"}
            or len(_canonical(dict(request))) > MAX_REQUEST_BYTES or _sensitive(_canonical(dict(request)))
            or not isinstance(request.get("selection"), Mapping)
            or set(request["selection"]) - _SELECTION_KEYS):
        _fail("INVALID_OR_CROSS_SCOPE_CONCENTRATION_REQUEST")
    if (not isinstance(captures, Mapping) or not {"issuer", "index"} <= set(captures)
            or set(captures) - {"issuer", "index", "counterparty"}):
        _fail("EXPLICIT_CONCENTRATION_CAPTURE_SCOPE_REQUIRED")
    for value in captures.values():
        if not isinstance(value, (tuple, list)) or len(value) != 2 or not isinstance(value[0], Mapping):
            _fail("INVALID_CONCENTRATION_CAPTURE_DESCRIPTOR")
        metadata, body = value
        if (len(_canonical(dict(metadata))) > 64_000 or _sensitive(_canonical(dict(metadata)))
                or body is not None and (not isinstance(body, bytes) or len(body) > MAX_BODY_BYTES)):
            _fail("INVALID_OR_UNSAFE_WHOLE_CONCENTRATION_CAPTURE")
    return request["selection"]


def _identity(selection, blocks):
    label, kind, legal = (selection.get(key) for key in ("counterparty_label", "counterparty_kind", "counterparty_legal_name_literal"))
    if not isinstance(label, str) or not 1 <= len(label) <= 160 or kind not in {"NAMED", "ANONYMOUS"}:
        _fail("INVALID_REPORTED_COUNTERPARTY_LABEL")
    anonymous = _ANONYMOUS.fullmatch(label) is not None
    if anonymous != (kind == "ANONYMOUS"):
        _fail("ANONYMOUS_LABEL_CANNOT_BE_RECLASSIFIED_AS_NAMED")
    if anonymous:
        if legal is not None or "alias_passage" in selection:
            _fail("ANONYMOUS_LABEL_CANNOT_ACQUIRE_A_LEGAL_NAME")
        return {"reported_label": label, "kind": kind, "reported_legal_name": None,
                "source_disclosed_alias_supported": False, "alias_evidence": None}
    evidence = None
    if legal is not None:
        if not isinstance(legal, str) or not 1 <= len(legal) <= 300:
            _fail("INVALID_REPORTED_LEGAL_NAME")
        if legal != label:
            evidence = blocks.get("alias_passage")
            text = evidence["literal_visible_passage"] if evidence else ""
            customers = text.partition(" Our competitors include ")[0]
            pattern = re.escape(legal) + r"\s*\([“\"]" + re.escape(label) + r"[”\"]\)"
            if (not customers.startswith("Our key customers include ") or len(re.findall(pattern, customers)) != 1
                    or re.search(r"\b(?:not|never|formerly|previously|example|no longer)\b", customers, re.I)):
                _fail("LEGAL_NAME_ALIAS_NOT_EXPLICIT_IN_CUSTOMER_SCOPE")
    return {"reported_label": label, "kind": kind, "reported_legal_name": legal,
            "source_disclosed_alias_supported": legal is not None and legal != label, "alias_evidence": evidence}


def _percentages(body, selected, paragraph, calendar, issuer):
    periods = selected.get("periods")
    if not isinstance(periods, list) or len(periods) != 3:
        _fail("THREE_ORDERED_ANNUAL_CONCENTRATION_PERIODS_REQUIRED")
    keys = {"fiscal_year", "period_start", "period_end", "value_literal", "fact_id", "context_id"}
    for index, period in enumerate(periods):
        if (not isinstance(period, Mapping) or set(period) != keys or type(period["fiscal_year"]) is not int
                or period["fiscal_year"] != int(issuer["fiscal_year_focus"]) - index
                or not isinstance(period["value_literal"], str)
                or not re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,6})?", period["value_literal"])
                or not 0 <= Decimal(period["value_literal"]) <= 100
                or any(not isinstance(period[key], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", period[key]) for key in ("fact_id", "context_id"))):
            _fail("INVALID_CONCENTRATION_PERIOD_OR_PERCENTAGE")
    identifiers = [item["fact_id"] for item in periods]
    if len(set(identifiers)) != 3 or len({item["context_id"] for item in periods}) != 3 or paragraph["fact_ids"] != identifiers:
        _fail("CONCENTRATION_FACT_ORDER_OR_SCOPE_AMBIGUOUS")
    facts, contexts, units, namespaces, pending, node_ns, count, depth = {}, {}, {}, [{}], {}, {}, 0, 0
    for event, node in ET.iterparse(BytesIO(body), events=("start-ns", "start", "end")):
        if event == "start-ns":
            pending[node[0]] = node[1]; continue
        if event == "start":
            count, depth = count + 1, depth + 1
            if count > 100_000 or depth > 128:
                _fail("CONCENTRATION_XML_STRUCTURE_BOUND")
            namespaces.append({**namespaces[-1], **pending}); pending = {}; continue
        ns = namespaces[-1]
        if node.tag in {f"{{{_XBRLDI}}}explicitMember", f"{{{_XBRLI}}}measure"}:
            node_ns[id(node)] = dict(ns)
        if node.tag == f"{{{_IX}}}nonFraction" and node.get("id") in identifiers:
            facts.setdefault(node.get("id"), []).append((node, dict(ns)))
        elif node.tag == f"{{{_XBRLI}}}context":
            contexts.setdefault(node.get("id"), []).append(node)
        elif node.tag == f"{{{_XBRLI}}}unit":
            units.setdefault(node.get("id"), []).append(node)
        namespaces.pop(); depth -= 1
    calendar_dates = {}
    for match in re.finditer(r"fiscal year ended on ([A-Z][a-z]+\s+[0-9]{1,2},\s+[0-9]{4})\s*\([“\"]fiscal ([0-9]{4})[”\"]\)", calendar["literal_visible_passage"], re.I):
        year = int(match[2])
        if year in calendar_dates:
            _fail("AMBIGUOUS_FISCAL_CALENDAR_DATE")
        calendar_dates[year] = _period(match[1]).isoformat()
    if set(calendar_dates) != {period["fiscal_year"] for period in periods}:
        _fail("FISCAL_CALENDAR_DOES_NOT_BIND_ALL_THREE_PERIODS")
    result = []
    for index, period in enumerate(periods):
        candidates, context = facts.get(period["fact_id"], []), contexts.get(period["context_id"], [])
        if len(candidates) != 1 or len(context) != 1:
            _fail("MISSING_OR_DUPLICATE_CONCENTRATION_FACT_CONTEXT")
        node, ns = candidates[0]; context = context[0]
        taxonomy, name = _expanded(node.get("name", ""), ns)
        if (not re.fullmatch(r"https?://fasb.org/us-gaap/20[0-9]{2}", taxonomy) or name != "ConcentrationRiskPercentage1"
                or node.get("contextRef") != period["context_id"] or node.get("scale") != "-2"
                or node.get("decimals") != "2" or node.get("sign") is not None or node.get("format") is not None
                or node.get("continuedAt") or node.get("footnoteRefs") or len(node)
                or any(key.endswith("}nil") and value != "false" for key, value in node.attrib.items())
                or node.text != period["value_literal"]):
            _fail("CONCENTRATION_FACT_CONCEPT_UNIT_FORMAT_OR_VALUE_MISMATCH")
        unit_id = node.get("unitRef"); unit = units.get(unit_id, [])
        if (len(unit) != 1 or len(unit[0]) != 1 or unit[0][0].tag != f"{{{_XBRLI}}}measure"
                or _expanded(unit[0][0].text or "", node_ns[id(unit[0][0])]) != (_XBRLI, "pure")):
            _fail("CONCENTRATION_PERCENTAGE_NEEDS_DIMENSIONLESS_UNIT")
        entities = context.findall(f"{{{_XBRLI}}}entity/{{{_XBRLI}}}identifier")
        starts = context.findall(f"{{{_XBRLI}}}period/{{{_XBRLI}}}startDate")
        ends = context.findall(f"{{{_XBRLI}}}period/{{{_XBRLI}}}endDate")
        members = list(context.iter(f"{{{_XBRLDI}}}explicitMember"))
        if (len(entities) != 1 or entities[0].get("scheme") != "http://www.sec.gov/CIK" or entities[0].text != issuer["cik"]
                or len(starts) != 1 or len(ends) != 1 or starts[0].text != period["period_start"] or ends[0].text != period["period_end"]
                or context.find(f"{{{_XBRLI}}}period/{{{_XBRLI}}}instant") is not None
                or context.find(f"{{{_XBRLI}}}scenario") is not None or list(context.iter(f"{{{_XBRLDI}}}typedMember"))
                or len(members) != 3 or calendar_dates[period["fiscal_year"]] != period["period_end"]
                or date.fromisoformat(period["period_end"]).year != period["fiscal_year"]
                or not 350 <= (date.fromisoformat(period["period_end"])-date.fromisoformat(period["period_start"])).days <= 371
                or index == 0 and (period["period_end"] != issuer["fiscal_period_end"] or period["period_start"] != issuer["fiscal_period_start"])):
            _fail("CONCENTRATION_PERIOD_ENTITY_OR_REPORTING_SCOPE_MISMATCH")
        dimensions = []
        for member in members:
            namespaces_for_member = node_ns[id(member)]
            axis = _expanded(member.get("dimension", ""), namespaces_for_member)
            value = _expanded((member.text or "").strip(), namespaces_for_member)
            dimensions.append({"axis": member.get("dimension"), "axis_namespace": axis[0], "axis_local": axis[1],
                "member": (member.text or "").strip(), "member_namespace": value[0], "member_local": value[1]})
        by_axis = {row["axis_local"]:row for row in dimensions}
        if (len(by_axis) != 3 or set(by_axis) != {"MajorCustomersAxis", "ConcentrationRiskByTypeAxis", "ConcentrationRiskByBenchmarkAxis"}
                or not re.fullmatch(r"https?://fasb.org/srt/20[0-9]{2}", by_axis["MajorCustomersAxis"]["axis_namespace"])
                or by_axis["MajorCustomersAxis"]["member_local"] != "CompanyMember"
                or not re.fullmatch(r"https?://[A-Za-z0-9.-]+/[0-9]{8}", by_axis["MajorCustomersAxis"]["member_namespace"])
                or any(by_axis[axis]["axis_namespace"] != taxonomy or by_axis[axis]["member_namespace"] != taxonomy
                       or by_axis[axis]["member_local"] != value for axis,value in (
                           ("ConcentrationRiskByTypeAxis","CustomerConcentrationRiskMember"),
                           ("ConcentrationRiskByBenchmarkAxis","SalesRevenueNetMember")))):
            _fail("CONCENTRATION_CUSTOMER_NET_REVENUE_DIMENSIONS_REQUIRED")
        result.append({**dict(period), "unit": "percent", "currency": None, "currency_applicable": False,
            "source_unit_id":unit_id,"source_unit_measure":unit[0][0].text,"source_scale":-2,"source_decimals":2,
            "source_fact_name":node.get("name"),"fact_taxonomy_namespace":taxonomy,
            "ratio_from_xbrl_scale_only":format((Decimal(period["value_literal"])/100).normalize(),"f"),
            "source_dimensions":dimensions,"generic_customer_member_renamed":False,
            "counterparty_identity_from_dimension_verified":False})
    return result


def review_sec_supplier_concentration(request: Mapping, captures: Mapping, *, as_of: datetime | None = None) -> dict:
    """Review one complete named/anonymous three-period customer-revenue statement.

    ``captures`` maps issuer/index and optional counterparty to (metadata, exact
    whole bytes or None). Defaults use actual physical ingestion; an explicit
    old cutoff cannot backdate the newly constructed review or legal snapshot.
    """
    started = _aware(_physical_clock())
    report = {"schema_version":REVIEW_VERSION,"status":"REJECTED","reason":"","claim":None,
        "review_started_at":started.isoformat(),"ingested_at":started.isoformat(),"knowledge_at":started.isoformat(),"as_of":None,
        "read_only":True,"source_http_requests":0,"usaspending_requests":0,"db_writes":0,"registry_writes":0,
        "scoring_applied":False,"model_modified":False,"trading_allowed":False,"activation_allowed":False,
        "counterparty_financials_attribution_allowed":False,"direct_customer_share_verified":False,
        "supplier_purchase_share_verified":False,"absolute_revenue_amount_inferred":False,
        "counterparty_identity_at_each_period_verified":False,"continuous_legal_identity_verified":False,
        "independent_sec_acceptance_clock_crosscheck_performed":False,"prior_source_clock_conflict_resolved":False,
        "current_instrument_identity_verified":False,"current_q3_identity_verified":False,
        "historical_availability_reconstructed":False,"out_of_sample_evaluation_verified":False}
    try:
        cutoff = _aware(as_of) if as_of is not None else None
        report["as_of"] = (cutoff or started).isoformat()
        if cutoff is not None and cutoff < started:
            _fail("AFTER_PHYSICAL_KNOWLEDGE_CUTOFF")
        selected = _input(request,captures)
        if selected.get("claim_role") != "CUSTOMER_REVENUE_CONCENTRATION" or selected.get("reporting_scope") != "CONSOLIDATED_REPORTING_GROUP":
            _fail("UNSUPPORTED_CONCENTRATION_MEASURE_OR_DENOMINATOR_SCOPE")
        metadata,body = captures["issuer"]; index_metadata,index_body = captures["index"]
        source = _known(_original(body,metadata,started),metadata,started)
        index = _index_original(index_body,index_metadata,started)
        issuer = _issuer(body,selected,source["capture_completed_utc"])
        facts,_ = _dei_and_contexts(body)
        if ({entry["value"] for context in facts.values() for entry in context.get("EntityCentralIndexKey",[])} != {issuer["cik"]}
                or issuer["cik"] != source["archive_routing_cik"] or issuer["document_type"] != "10-K"):
            _fail("COFILER_OR_ISSUER_SOURCE_SCOPE_CONFLICT")
        try:
            filing = _filing_index(SimpleNamespace(body=index_body,source_url=index["source_url"],observed_at=_aware(index["capture_completed_utc"])),
                SimpleNamespace(source_url=source["source_url"],observed_at=_aware(source["capture_completed_utc"]),size_bytes=len(body)),"10-K")
        except ValueError:
            _fail("INDEX_PRIMARY_ACCESSION_FORM_OR_DATE_CONFLICT")
        original_index,visible_index = _Index(index_body),_VisibleIndex(index_body)
        if (filing["report_period_end"] != issuer["fiscal_period_end"]
                or any(getattr(original_index,key)!=getattr(visible_index,key) for key in ("rows","labels","filer_ciks"))):
            _fail("INDEX_PERIOD_OR_VISIBLE_PRIMARY_SCOPE_CONFLICT")
        block_keys = ["scope_passage","fiscal_calendar_passage","section_heading","concentration_passage"]
        if "alias_passage" in selected: block_keys.append("alias_passage")
        blocks = _Blocks(body,{key:selected.get(key) for key in block_keys}).parse()
        identity = _identity(selected,blocks)
        scope,calendar,heading,paragraph = (blocks[key] for key in block_keys[:4])
        scope_text = scope["literal_visible_passage"]
        if (re.fullmatch(r"All [A-Za-z0-9 ,.&'’-]{1,200} subsidiaries are included in the Company[’']s consolidated financial statements and all intercompany balances are eliminated in consolidation\.",scope_text) is None
                or not re.fullmatch(r"[0-9]{1,2}\. SEGMENT INFORMATION AND CONCENTRATIONS",heading["literal_visible_passage"])
                or not scope["byte_end"] <= heading["byte_start"] < heading["byte_end"] <= paragraph["byte_start"]
                or paragraph["byte_end"]-heading["byte_start"]>100_000):
            _fail("CONSOLIDATED_NOTE_AND_CONCENTRATION_SCOPE_NOT_EXPLICIT")
        quantities = _percentages(body,selected,paragraph,calendar,issuer)
        denominator,channels,aggregate = (selected.get(key) for key in ("denominator_literal","channel_qualifier_literal","aggregation_qualifier_literal"))
        prefix = "through sales to multiple distributors, contract manufacturers, and direct sales for multiple applications including "
        if (denominator not in {"the Company’s net revenue","the Company's net revenue"}
                or aggregate != "in the aggregate" or not isinstance(channels,str) or not channels.startswith(prefix)
                or not 1 <= len(channels[len(prefix):]) <= 400
                or re.search(r"\b(?:not|no|never|except|unrelated|another|other customer)\b",channels,re.I)):
            _fail("CUSTOMER_CHANNEL_AGGREGATION_OR_NET_REVENUE_DENOMINATOR_MISMATCH")
        years,values = [row["fiscal_year"] for row in quantities],[row["value_literal"] for row in quantities]
        expected = (f"During fiscal {years[0]}, fiscal {years[1]}, and fiscal {years[2]}, {identity['reported_label']}, "
            f"{channels}, {aggregate} accounted for {values[0]}%, {values[1]}%, and {values[2]}% of {denominator}, respectively.")
        if paragraph["literal_visible_passage"] != expected:
            _fail("COUNTERPARTY_PERCENTAGES_NOT_THE_SAME_COMPLETE_SCOPED_SENTENCE")
        legal = {"status":"NAMED_ONLY" if identity["kind"]=="NAMED" else "ANONYMOUS_ONLY",
            "independent_source_snapshot_supported":False,"reason":"NO_INDEPENDENT_LEGAL_SOURCE",
            "relationship_date_identity_verified":False,"continuous_legal_validity_verified":False,
            "registry_identity_adopted":False,"financial_attribution_allowed":False}
        if identity["kind"]=="ANONYMOUS" and ("counterparty" in captures or request.get("counterparty_selection") is not None):
            _fail("ANONYMOUS_CUSTOMER_CANNOT_ACQUIRE_A_NAMED_LEGAL_IDENTITY")
        if "counterparty" in captures:
            if identity["reported_legal_name"] is None:
                _fail("INDEPENDENT_IDENTITY_REQUIRES_AN_EXPLICIT_SOURCE_LEGAL_NAME")
            counter_metadata,counter_body = captures["counterparty"]
            base_legal = dict(legal)
            try:
                legal = _legal_snapshot(counter_body,counter_metadata,request.get("counterparty_selection"),identity["reported_legal_name"],started)
                if legal.get("source"):
                    legal["source"] = _known(legal["source"],counter_metadata,started)
                    if legal["source"]["accession_number"]==source["accession_number"] or legal["source"]["body_sha256"]==source["body_sha256"]:
                        _fail("SAME_DISCLOSURE_NOT_INDEPENDENT")
            except (ValueError,KeyError,TypeError,ET.ParseError):
                legal = {**base_legal,"reason":"INVALID_OR_NOT_INDEPENDENT_LEGAL_SNAPSHOT"}
        completed = _aware(_physical_clock())
        if completed < started: _fail("PHYSICAL_REVIEW_CLOCK_MOVED_BACKWARDS")
        if cutoff is not None and completed > cutoff: _fail("AFTER_PHYSICAL_KNOWLEDGE_CUTOFF")
        report.update(ingested_at=completed.isoformat(),knowledge_at=completed.isoformat(),as_of=(cutoff or completed).isoformat())
        claim = {"issuer_anchor":issuer,"primary_source":source,"filing_index_source":index,"filing":filing,
            "claim_role":"CUSTOMER_REVENUE_CONCENTRATION","reporting_scope":"CONSOLIDATED_REPORTING_GROUP",
            "reporting_group_scope_literal":scope_text,"counterparty":identity,"quantities":quantities,
            "denominator":{"literal":denominator,"measure":"REPORTING_GROUP_NET_REVENUE","owner_cik":issuer["cik"],
                "owner_name":issuer["reported_dei_name"],"counterparty_own_revenue":False,"absolute_amount":None},
            "channel_qualifier_literal":channels,"aggregation_qualifier_literal":aggregate,
            "independent_counterparty_legal_snapshot":legal,"source_format":"COMPLETE_PARAGRAPH_WITH_THREE_ORDERED_INLINE_FACTS",
            "table_row_column_applicable":False,"footnote_refs":[],"evidence":blocks,
            "selected_source_count":len(captures),"selection_sha256":hashlib.sha256(_canonical(dict(request))).hexdigest(),
            "source_inputs_known_at":max(_aware(item["original_source_known_at"]) for item in (source,index,*([legal["source"]] if legal.get("source") else []))).isoformat(),
            "missing_facts":["FULL_PERIOD_COUNTERPARTY_LEGAL_CONTINUITY_UNVERIFIED","DIRECT_SALES_ONLY_SHARE_NOT_SEPARATED",
                "CUSTOMER_OWN_REVENUE_AND_SUPPLIER_PURCHASE_SHARE_NOT_REPORTED","GENERIC_XBRL_CUSTOMER_MEMBER_IS_NOT_A_LEGAL_IDENTIFIER",
                "CURRENT_INSTRUMENT_Q3_AND_RELEVANT_687_HISTORY_UNVERIFIED","PUBLISHER_PUBLICATION_INSTANT_UNKNOWN"]}
        if filing["publisher_size_conflict"]: claim["missing_facts"].append("INDEX_SIZE_DIFFERS_FROM_RETAINED_SERVED_ORIGINAL")
        if identity["kind"]=="ANONYMOUS": claim["missing_facts"].append("ANONYMOUS_LABEL_ATTRIBUTION_BLOCKED")
        claim["candidate_id"] = "supplier-concentration:"+hashlib.sha256(_canonical(claim)).hexdigest()
        return {**report,"status":"CANDIDATE_REVIEWED","claim":claim}
    except _Rejected as error:
        return {**report,"reason":str(error)}
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError,UnicodeError,ET.ParseError,RecursionError) as error:
        return {**report,"reason":"INVALID_SUPPLIER_CONCENTRATION_INPUT","error_type":type(error).__name__}


__all__ = ["REVIEW_VERSION","review_sec_supplier_concentration"]
