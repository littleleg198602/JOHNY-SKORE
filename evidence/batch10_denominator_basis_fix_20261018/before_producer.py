"""Offline original-byte review of a supplier's reported revenue denominator.

This consumer reuses the existing concentration review, then checks the actual
consolidated financial-statement row, its columns and duration XBRL facts. It
never multiplies a customer percentage by total revenue or fetches a source.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from html.parser import HTMLParser
from io import BytesIO
import re
from xml.etree import ElementTree as ET

from market_checker_app.services.sec_operational_metrics_review import _Rejected, _aware, _canonical, _period, _sensitive
from market_checker_app.services.sec_purchase_obligation_scope_review import _Blocks, _expanded
from market_checker_app.services.sec_supplier_concentration_review import review_sec_supplier_concentration


REVIEW_VERSION = "sec-supplier-revenue-denominator-v1"
MAX_BODY_BYTES = 4_000_000
MAX_REQUEST_BYTES = 48_000
MAX_TABLE_BYTES = 128_000
MAX_ELEMENTS = 100_000
MAX_DEPTH = 128
_IX = "http://www.xbrl.org/2013/inlineXBRL"
_XBRLI = "http://www.xbrl.org/2003/instance"
_TRANSFORM = "http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"
_CURRENCY = "http://www.xbrl.org/2003/iso4217"
_SELECT = {"measure", "reporting_scope", "statement_issuer_heading", "statement_heading", "unit_heading",
           "basis_passage", "statement_table", "period_header_row", "column_header_row", "revenue_row", "periods"}
_BLOCKS = ("statement_issuer_heading", "statement_heading", "unit_heading", "basis_passage")


def _fail(reason):
    raise _Rejected(reason)


def _physical_clock():
    return datetime.now(timezone.utc)


def _pin(body, selected, maximum):
    if not isinstance(selected, Mapping) or set(selected) != {"byte_start", "byte_end", "sha256"}:
        _fail("INVALID_DENOMINATOR_WHOLE_BYTE_PIN")
    start, end = selected["byte_start"], selected["byte_end"]
    if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(body)
            or end-start > maximum or hashlib.sha256(body[start:end]).hexdigest() != selected["sha256"]):
        _fail("DENOMINATOR_WHOLE_BYTE_PIN_MISMATCH")
    return dict(selected)


class _Statement(HTMLParser):
    """Parse one pinned table with original ancestors and unambiguous columns.

    Unrelated layout tables do not impose financial-column limits on this one.
    Hidden content, nested tables, footnotes and rowspans in the selected table
    are unsupported, so their qualifiers cannot disappear during extraction.
    """
    def __init__(self, body, selected):
        super().__init__(convert_charrefs=True)
        self.body, self.text = body, body.decode("utf-8", errors="strict")
        self.pin = _pin(body, selected, MAX_TABLE_BYTES)
        self.target = len(body[:self.pin["byte_start"]].decode("utf-8", errors="strict"))
        self.lines = [0]+[match.end() for match in re.finditer("\n", self.text)]
        self.stack, self.rows, self.row, self.cell = [], [], None, None
        self.active, self.found, self.depth, self.nodes = False, False, 0, 0

    def position(self):
        line, column = self.getpos()
        return self.lines[line-1]+column

    def byte_position(self):
        return len(self.text[:self.position()].encode())

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > MAX_ELEMENTS or len(self.stack) >= MAX_DEPTH:
            _fail("DENOMINATOR_HTML_STRUCTURE_BOUND")
        attrs = dict(attrs)
        compact = re.sub(r"\s+", "", attrs.get("style", "")).lower()
        hidden = bool(self.stack and self.stack[-1][1]) or tag in {"script", "style", "noscript", "ix:hidden", "ix:header"}
        hidden |= "hidden" in attrs or "display:none" in compact or "visibility:hidden" in compact
        if self.position() == self.target:
            if tag != "table" or hidden or self.found or any(name == "table" for name,_ in self.stack):
                _fail("DENOMINATOR_SELECTED_TABLE_NOT_VISIBLE_OR_TOP_LEVEL")
            self.active, self.found, self.depth = True, True, len(self.stack)
        elif self.active and tag == "table":
            _fail("DENOMINATOR_NESTED_TABLE_UNSUPPORTED")
        if self.active:
            if hidden or tag in {"sup", "a", "ix:continuation"} or attrs.get("footnoterefs") or attrs.get("continuedat"):
                _fail("DENOMINATOR_NONVISIBLE_OR_EXTERNAL_QUALIFIER")
            if tag == "tr":
                if self.row is not None or len(self.rows) >= 64:
                    _fail("DENOMINATOR_ROW_STRUCTURE_BOUND")
                self.row = {"cells": [], "byte_start": self.byte_position()}
            if tag in {"td", "th"}:
                if self.row is None or self.cell is not None or len(self.row["cells"]) >= 32:
                    _fail("DENOMINATOR_CELL_STRUCTURE_BOUND")
                span, rowspan = int(attrs.get("colspan", 1)), int(attrs.get("rowspan", 1))
                if not 1 <= span <= 32 or rowspan != 1:
                    _fail("DENOMINATOR_COLUMN_OR_ROWSPAN_UNSUPPORTED")
                self.cell = {"parts": [], "fact_ids": [], "colspan": span, "byte_start": self.byte_position()}
            if tag == "ix:nonfraction":
                if self.cell is None:
                    _fail("DENOMINATOR_FACT_OUTSIDE_A_TABLE_CELL")
                self.cell["fact_ids"].append(attrs.get("id"))
        if tag not in {"br", "hr", "meta", "link", "img", "input", "col"}:
            self.stack.append((tag, hidden))

    def handle_data(self, text):
        if self.active and self.cell is not None:
            self.cell["parts"].append(text)

    def handle_endtag(self, tag):
        if self.active:
            end = self.text.find(">", self.position())+1
            if not end:
                _fail("DENOMINATOR_INCOMPLETE_TABLE")
            if tag in {"td", "th"}:
                if self.cell is None:
                    _fail("DENOMINATOR_UNBALANCED_CELL")
                self.cell.update(byte_end=len(self.text[:end].encode()),
                                 literal=" ".join("".join(self.cell.pop("parts")).split()))
                self.row["cells"].append(self.cell); self.cell = None
            if tag == "tr":
                if self.row is None or self.cell is not None:
                    _fail("DENOMINATOR_UNBALANCED_ROW")
                grid = []
                for cell in self.row["cells"]:
                    grid.extend([cell]*cell["colspan"])
                if not 1 <= len(grid) <= 32:
                    _fail("DENOMINATOR_COLUMN_GRID_BOUND")
                self.row.update(byte_end=len(self.text[:end].encode()), grid=grid)
                self.rows.append(self.row); self.row = None
            if tag == "table":
                if (self.row is not None or self.cell is not None or len(self.stack) != self.depth+1
                        or len(self.text[:end].encode()) != self.pin["byte_end"]):
                    _fail("DENOMINATOR_TABLE_NOT_THE_COMPLETE_PINNED_ORIGINAL")
                self.active = False
        if tag not in {"br", "hr", "meta", "link", "img", "input", "col"}:
            if not self.stack or self.stack[-1][0] != tag:
                # Full SEC documents are XML. An HTML-parser void element may
                # have an explicit XML close; those were excluded above.
                _fail("DENOMINATOR_UNBALANCED_ORIGINAL_HTML")
            self.stack.pop()

    def parse(self):
        self.feed(self.text); self.close()
        if self.active or not self.found or not self.rows:
            _fail("DENOMINATOR_MISSING_OR_INCOMPLETE_TABLE")
        return self.rows


def _row(body, rows, selected):
    pin = _pin(body, selected, 16_000)
    chosen = [row for row in rows if row["byte_start"] == pin["byte_start"] and row["byte_end"] == pin["byte_end"]]
    if len(chosen) != 1:
        _fail("DENOMINATOR_ROW_NOT_IN_SELECTED_STATEMENT")
    row = chosen[0]
    evidence = {**pin, "literal_cells": [cell["literal"] for cell in row["grid"]],
                "fact_ids": [fact for cell in row["cells"] for fact in cell["fact_ids"]]}
    return row, evidence


def _xml(body):
    facts, contexts, units, node_namespaces = {}, {}, {}, {}
    namespaces, pending, count, depth = [{}], {}, 0, 0
    for event, node in ET.iterparse(BytesIO(body), events=("start-ns", "start", "end")):
        if event == "start-ns":
            pending[node[0]] = node[1]; continue
        if event == "start":
            count, depth = count+1, depth+1
            if count > MAX_ELEMENTS or depth > MAX_DEPTH:
                _fail("DENOMINATOR_XML_STRUCTURE_BOUND")
            namespaces.append({**namespaces[-1], **pending}); pending = {}; continue
        if node.tag == f"{{{_IX}}}nonFraction":
            facts.setdefault(node.get("id"), []).append(node)
            node_namespaces[id(node)] = dict(namespaces[-1])
        if node.tag == f"{{{_XBRLI}}}context": contexts.setdefault(node.get("id"), []).append(node)
        if node.tag == f"{{{_XBRLI}}}unit": units.setdefault(node.get("id"), []).append(node)
        if node.tag == f"{{{_XBRLI}}}measure": node_namespaces[id(node)] = dict(namespaces[-1])
        namespaces.pop(); depth -= 1
    return facts, contexts, units, node_namespaces


def _quantities(body, selected, claim, rows):
    periods = selected.get("periods")
    keys = {"fiscal_year", "period_start", "period_end", "fact_id", "context_id", "value_literal", "column"}
    if not isinstance(periods, list) or len(periods) != len(claim["quantities"]):
        _fail("DENOMINATOR_EXACT_CONCENTRATION_PERIODS_REQUIRED")
    revenue, revenue_evidence = _row(body, rows, selected.get("revenue_row"))
    dates, date_evidence = _row(body, rows, selected.get("column_header_row"))
    fiscal, fiscal_evidence = _row(body, rows, selected.get("period_header_row"))
    if (not fiscal["byte_end"] <= dates["byte_start"] < dates["byte_end"] <= revenue["byte_start"]
            or revenue["cells"][0]["literal"] != "Net revenue"
            or fiscal["grid"][0]["literal"] or dates["grid"][0]["literal"]):
        _fail("DENOMINATOR_STATEMENT_ROW_OR_HEADER_SCOPE_MISMATCH")
    identifiers = [item.get("fact_id") for item in periods if isinstance(item, Mapping)]
    if len(set(identifiers)) != len(periods) or revenue_evidence["fact_ids"] != identifiers:
        _fail("DENOMINATOR_ROW_FACTS_NOT_THE_EXACT_ORDERED_SELECTION")
    facts, contexts, units, namespaces = _xml(body)
    result, previous_column = [], -1
    for period, concentration in zip(periods, claim["quantities"]):
        if (not isinstance(period, Mapping) or set(period) != keys
                or any(period.get(key) != concentration[key] for key in ("fiscal_year", "period_start", "period_end"))
                or any(not isinstance(period[key], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", period[key]) for key in ("fact_id", "context_id"))
                or not isinstance(period["value_literal"], str) or len(period["value_literal"]) > 24
                or not re.fullmatch(r"(?:0|[1-9][0-9]{0,2}(?:,[0-9]{3})*)\.[0-9]", period["value_literal"])):
            _fail("DENOMINATOR_PERIOD_OR_LITERAL_DOES_NOT_MATCH_CONCENTRATION")
        column = period["column"]
        if (type(column) is not int or not previous_column < column < len(revenue["grid"])
                or column >= len(dates["grid"]) or column >= len(fiscal["grid"])):
            _fail("DENOMINATOR_INVALID_OR_UNORDERED_PERIOD_COLUMN")
        previous_column = column
        cell = revenue["grid"][column]
        if (cell["fact_ids"] != [period["fact_id"]] or cell["literal"] != period["value_literal"]
                or fiscal["grid"][column]["literal"] != "Fiscal Years Ended"
                or _period(dates["grid"][column]["literal"]).isoformat() != period["period_end"]):
            _fail("DENOMINATOR_VISIBLE_COLUMN_DATE_FACT_OR_VALUE_MISMATCH")
        matches, context = facts.get(period["fact_id"], []), contexts.get(period["context_id"], [])
        if len(matches) != 1 or len(context) != 1:
            _fail("DENOMINATOR_DUPLICATE_OR_MISSING_FACT_CONTEXT")
        node, context = matches[0], context[0]
        ns = namespaces[id(node)]
        taxonomy, concept = _expanded(node.get("name", ""), ns)
        if (not re.fullmatch(r"https?://fasb.org/us-gaap/20[0-9]{2}", taxonomy)
                or concept != "RevenueFromContractWithCustomerExcludingAssessedTax"
                or node.get("contextRef") != period["context_id"] or node.get("scale") != "6" or node.get("decimals") != "-5"
                or _expanded(node.get("format", ""), ns) != (_TRANSFORM, "num-dot-decimal")
                or node.get("sign") is not None or node.get("continuedAt") or node.get("footnoteRefs") or len(node)
                or any(key.endswith("}nil") and value != "false" for key,value in node.attrib.items())
                or node.text != period["value_literal"]):
            _fail("DENOMINATOR_FACT_CONCEPT_SCALE_SIGN_FORMAT_OR_VALUE_MISMATCH")
        unit_id = node.get("unitRef"); unit = units.get(unit_id, [])
        if (len(unit) != 1 or len(unit[0]) != 1 or unit[0][0].tag != f"{{{_XBRLI}}}measure"
                or _expanded(unit[0][0].text or "", namespaces[id(unit[0][0])]) != (_CURRENCY, "USD")):
            _fail("DENOMINATOR_EXACT_USD_UNIT_REQUIRED")
        entity = context.findall(f"{{{_XBRLI}}}entity/{{{_XBRLI}}}identifier")
        entity_nodes = context.findall(f"{{{_XBRLI}}}entity")
        period_nodes = context.findall(f"{{{_XBRLI}}}period")
        starts = context.findall(f"{{{_XBRLI}}}period/{{{_XBRLI}}}startDate")
        ends = context.findall(f"{{{_XBRLI}}}period/{{{_XBRLI}}}endDate")
        if (len(entity) != 1 or entity[0].text != claim["issuer_anchor"]["cik"] or entity[0].get("scheme") != "http://www.sec.gov/CIK"
                or len(context) != 2 or len(entity_nodes) != 1 or len(entity_nodes[0]) != 1
                or len(period_nodes) != 1 or len(period_nodes[0]) != 2
                or len(starts) != 1 or len(ends) != 1 or starts[0].text != period["period_start"] or ends[0].text != period["period_end"]
                or context.find(f"{{{_XBRLI}}}entity/{{{_XBRLI}}}segment") is not None
                or context.find(f"{{{_XBRLI}}}scenario") is not None or context.find(f"{{{_XBRLI}}}period/{{{_XBRLI}}}instant") is not None):
            _fail("DENOMINATOR_DURATION_ENTITY_OR_CONSOLIDATED_CONTEXT_MISMATCH")
        amount = Decimal(period["value_literal"].replace(",", ""))*Decimal(10)**6
        if amount < 0:
            _fail("DENOMINATOR_NEGATIVE_NET_REVENUE_UNSUPPORTED")
        raw = body[cell["byte_start"]:cell["byte_end"]]
        result.append({**dict(period), "currency":"USD", "display_unit":"millions", "scale":6, "decimals":-5,
            "source_sign":None, "source_format":node.get("format"), "source_fact_name":node.get("name"),
            "taxonomy_namespace":taxonomy, "source_unit_id":unit_id, "source_unit_measure":unit[0][0].text,
            "scaled_total_net_revenue_usd":format(amount.normalize(),"f"), "context_dimensions":[],
            "reporting_scope":"CONSOLIDATED_REPORTING_GROUP", "comparative_in_current_filing":period["fiscal_year"] != periods[0]["fiscal_year"],
            "cell_evidence":{"byte_start":cell["byte_start"],"byte_end":cell["byte_end"],"sha256":hashlib.sha256(raw).hexdigest()},
            "concentration_fact_id":concentration["fact_id"], "concentration_context_id":concentration["context_id"],
            "same_original_period_and_denominator_basis_supported":True,
            "customer_amount_calculated":False, "customer_amount":None})
    return result, {"revenue_row":revenue_evidence,"column_header_row":date_evidence,"period_header_row":fiscal_evidence}


def review_sec_supplier_revenue_denominator(request: Mapping, captures: Mapping, *, as_of: datetime | None = None) -> dict:
    """Review exact denominators; no transport, persistence or customer estimate.

    ``request`` has a new financial-statement ``selection`` and the existing
    ``concentration_request``. ``captures`` maps issuer/index and optionally
    counterparty to (metadata, original whole bytes). No earlier ``as_of`` may
    backdate a newly created physical review.
    """
    started = _aware(_physical_clock())
    report = {"schema_version":REVIEW_VERSION,"status":"REJECTED","reason":"","claim":None,
        "review_started_at":started.isoformat(),"ingested_at":started.isoformat(),"knowledge_at":started.isoformat(),"as_of":None,
        "source_http_requests":0,"collector_requests":0,"usaspending_requests":0,"db_writes":0,"registry_adoptions":0,
        "read_only":True,"scoring_applied":False,"model_modified":False,"fitter_modified":False,
        "trading_allowed":False,"activation_allowed":False,"customer_amount_calculated":False,
        "counterparty_purchases_verified":False,"direct_purchase_share_verified":False,"physical_quantity_denominator_verified":False,
        "counterparty_identity_continuity_verified":False,"current_instrument_or_q3_verified":False,
        "historical_as_filed_comparison_verified":False,"historical_availability_reconstructed":False,"out_of_sample_verified":False}
    try:
        cutoff = _aware(as_of) if as_of is not None else None
        report["as_of"] = (cutoff or started).isoformat()
        if cutoff is not None and cutoff < started: _fail("AFTER_PHYSICAL_KNOWLEDGE_CUTOFF")
        if (not isinstance(request, Mapping) or set(request) != {"selection","concentration_request"}
                or len(_canonical(dict(request))) > MAX_REQUEST_BYTES or _sensitive(_canonical(dict(request)))
                or not isinstance(request.get("selection"), Mapping) or set(request["selection"]) != _SELECT):
            _fail("INVALID_OR_UNSAFE_DENOMINATOR_REQUEST")
        selected = request["selection"]
        if selected["measure"] != "NET_REVENUE" or selected["reporting_scope"] != "CONSOLIDATED_REPORTING_GROUP":
            _fail("UNSUPPORTED_DENOMINATOR_MEASURE_OR_SCOPE")
        concentration = review_sec_supplier_concentration(request["concentration_request"],captures,as_of=cutoff)
        if concentration["status"] != "CANDIDATE_REVIEWED":
            _fail("ORIGINAL_CONCENTRATION_REVIEW_NOT_SUPPORTED")
        claim = concentration["claim"]
        metadata, body = captures["issuer"]
        if (not isinstance(body,bytes) or len(body)>MAX_BODY_BYTES
                or hashlib.sha256(body).hexdigest() != claim["primary_source"]["body_sha256"]):
            _fail("DENOMINATOR_NOT_THE_CONCENTRATION_ORIGINAL")
        blocks = _Blocks(body,{key:selected[key] for key in _BLOCKS}).parse()
        if (blocks["statement_issuer_heading"]["literal_visible_passage"].casefold() != claim["issuer_anchor"]["reported_dei_name"].casefold()
                or blocks["statement_heading"]["literal_visible_passage"] != "CONSOLIDATED STATEMENTS OF OPERATIONS"
                or blocks["unit_heading"]["literal_visible_passage"] != "(In millions, except per share amounts)"
                or not blocks["statement_issuer_heading"]["byte_end"] <= blocks["statement_heading"]["byte_start"]
                < blocks["statement_heading"]["byte_end"] <= blocks["unit_heading"]["byte_start"]
                < blocks["unit_heading"]["byte_end"] <= selected["statement_table"]["byte_start"]
                or selected["statement_table"]["byte_start"]-blocks["statement_issuer_heading"]["byte_start"] > 3000):
            _fail("DENOMINATOR_STATEMENT_GROUP_OR_DISPLAY_UNIT_NOT_EXPLICIT")
        basis = blocks["basis_passage"]["literal_visible_passage"]
        if not re.match(r"Revenue Recognition\. We recognize revenue in accordance with .*ASC.*606.*Revenue from Contracts with Customers net of estimated reserves\.",basis):
            _fail("DENOMINATOR_NET_REVENUE_POLICY_BASIS_NOT_EXPLICIT")
        rows = _Statement(body,selected["statement_table"]).parse()
        quantities,table_evidence = _quantities(body,selected,claim,rows)
        changes = sorted(set(re.findall(r"\b(?:restated|restatement|recast|reclassified|reclassification)\b|\b(?:changed|revised)\s+(?:revenue|definition|basis)\b",basis,re.I)))
        if changes:
            for quantity in quantities:
                quantity["same_original_period_and_denominator_basis_supported"] = False
        completed = _aware(_physical_clock())
        if completed < started or completed < _aware(concentration["knowledge_at"]): _fail("PHYSICAL_REVIEW_CLOCK_MOVED_BACKWARDS")
        if cutoff is not None and completed > cutoff: _fail("AFTER_PHYSICAL_KNOWLEDGE_CUTOFF")
        report.update(ingested_at=completed.isoformat(),knowledge_at=completed.isoformat(),as_of=(cutoff or completed).isoformat())
        result = {"issuer_anchor":claim["issuer_anchor"],"primary_source":claim["primary_source"],
            "filing_index_source":claim["filing_index_source"],"filing":claim["filing"],"measure":"NET_REVENUE",
            "reporting_scope":"CONSOLIDATED_REPORTING_GROUP","quantities":quantities,
            "denominator_owner":claim["denominator"],"reported_customer":claim["counterparty"],
            "concentration_candidate_id":claim["candidate_id"],"concentration_selection_sha256":claim["selection_sha256"],
            "source_inputs_known_at":claim["source_inputs_known_at"],"source_format":"CONSOLIDATED_STATEMENT_THREE_ANNUAL_DURATION_FACTS",
            "definition_policy_literal":basis,"definition_change_terms_in_selected_policy":changes,
            "concentration_join_status":"WITHHELD_BASIS_CHANGE_REVIEW_REQUIRED" if changes else "SAME_ORIGINAL_SCOPE_PERIOD_AND_BASIS_SUPPORTED",
            "definition_comparability_across_prior_filings":"UNKNOWN_NOT_RECONSTRUCTED",
            "comparative_version_scope":"COMPARATIVES_AS_REPORTED_IN_THIS_CURRENT_FILING",
            "revenue_concept_scope":"CUSTOMER_CONTRACT_REVENUE_EXCLUDING_ASSESSED_TAX_NET_OF_ESTIMATED_RESERVES",
            "evidence":{**blocks,**table_evidence,"statement_table":dict(selected["statement_table"])},
            "generic_customer_member_renamed":False,"customer_percentage_times_amount_performed":False,
            "broader_award_uei_or_durable_history_verified":False,"registry_identity_adopted":False,
            "missing_facts":["PRIOR_AS_FILED_COMPARATIVE_VERSIONS_AND_RESTATEMENT_HISTORY_UNVERIFIED",
                "APPLE_PURCHASES_DIRECT_CHANNEL_SHARE_AND_PHYSICAL_QUANTITY_NOT_REPORTED_BY_THIS_DENOMINATOR",
                "CUSTOMER_LEGAL_CONTINUITY_AND_CURRENT_INSTRUMENT_Q3_UNVERIFIED",
                "BROADER_AWARD_UEI_AND_ORIGINAL_DURABLE_QUOTA_HISTORY_UNAVAILABLE",
                "PUBLISHER_PUBLICATION_INSTANT_UNKNOWN","RELEVANT_687_COVERAGE_AND_HISTORICAL_OOS_UNVERIFIED"]}
        if claim["filing"]["publisher_size_conflict"]: result["missing_facts"].append("INDEX_SERVED_ORIGINAL_SIZE_CONFLICT_PRESERVED")
        if changes: result["missing_facts"].append("SOURCE_BASIS_CHANGE_QUALIFIERS_REQUIRE_INDEPENDENT_REVIEW_BEFORE_JOIN")
        result["candidate_id"] = "supplier-net-revenue:"+hashlib.sha256(_canonical(result)).hexdigest()
        return {**report,"status":"CANDIDATE_REVIEWED","claim":result}
    except _Rejected as error:
        return {**report,"reason":str(error)}
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError,UnicodeError,ET.ParseError,RecursionError) as error:
        return {**report,"reason":"INVALID_SUPPLIER_REVENUE_DENOMINATOR_INPUT","error_type":type(error).__name__}


__all__ = ["REVIEW_VERSION","review_sec_supplier_revenue_denominator"]
