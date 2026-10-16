"""Review one original aggregate SEC commitment without inventing a supplier share.

Whole original paragraphs, their iXBRL value/context/unit and the exact filing
index stay separate from legal-entity membership, actual purchases or revenue.
This module performs no transport, storage, registry or scoring operation.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from html.parser import HTMLParser
from io import BytesIO
import re
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from market_checker_app.services.sec_ir_disclosure_authority_review import _filing_index
from market_checker_app.services.sec_operational_metrics_review import (
    _Rejected, _aware, _canonical, _dei_and_contexts, _period, _sensitive,
)
from market_checker_app.services.sec_person_kind_review import _Index, _url
from market_checker_app.services.sec_supply_chain_scope_review import _issuer, _original


REVIEW_VERSION = "sec-original-purchase-obligation-scope-v1"
MAX_BODY_BYTES = 4_000_000
MAX_SELECTION_BYTES = 32_000
MAX_BLOCK_BYTES = 8_000
MAX_XML_ELEMENTS = 100_000
MAX_XML_DEPTH = 128
_IX = "http://www.xbrl.org/2013/inlineXBRL"
_XBRLI = "http://www.xbrl.org/2003/instance"
_XBRLDI = "http://xbrl.org/2006/xbrldi"
_CATEGORY = "Manufacturing, supply, and capacity commitments"
_MEMBER = "ManufacturingProductionAndLongTermSupplyAndCapacityAgreementCommitmentsMember"
_SELECTED_KEYS = {
    "issuer_ticker", "issuer_context_id", "issuer_name", "issuer_cik", "fiscal_period_end",
    "claim_role", "statement_scope", "group_name_literal", "group_heading", "notes_heading",
    "section_heading", "paragraph", "quantity", "requested_counterparty",
}


def _fail(reason):
    raise _Rejected(reason)


def _physical_clock():
    return datetime.now(timezone.utc)


def _known(source, metadata, physical):
    capture = _aware(source["capture_completed_utc"])
    known = _aware(metadata.get("known_at", metadata.get("available_at", capture)))
    if not capture <= known <= physical:
        _fail("REVERSED_OR_FUTURE_SOURCE_KNOWLEDGE_CLOCK")
    return {**source, "original_source_known_at": known.isoformat()}


def _index_original(body, metadata, physical):
    # SEC's complete index is HTML4, with a public DOCTYPE. HTMLParser does
    # not resolve it; never remove that declaration or rewrite the body.
    if not isinstance(body, bytes) or len(body) > MAX_BODY_BYTES or not isinstance(metadata, Mapping):
        _fail("INDEX_BODY_OR_METADATA_BOUND_MISMATCH")
    if len(_canonical(dict(metadata))) > 64_000 or _sensitive(_canonical(dict(metadata))) or _sensitive(body):
        _fail("UNSAFE_WHOLE_SOURCE_OR_METADATA")
    if (metadata.get("public_raw_available") is False or metadata.get("body_withheld") is True
            or metadata.get("withheld") is True
            or any(str(metadata.get(key, "")).startswith("WITHHELD") for key in ("status", "outcome", "archive_status"))):
        _fail("WITHHELD_OR_UNAVAILABLE_WHOLE_BODY")
    legacy = metadata.get("capture_kind") == "RAW_HTTP_RESPONSE_BODY"
    url = metadata.get("url" if legacy else "requested_url")
    final = metadata.get("resolved_url" if legacy else "final_url")
    expected = metadata.get("sha256" if legacy else "raw_response_sha256")
    size = metadata.get("size_bytes" if legacy else "raw_response_bytes")
    if (type(size) is not int or size != len(body) or not isinstance(expected, str)
            or not re.fullmatch(r"[a-f0-9]{64}", expected) or hashlib.sha256(body).hexdigest() != expected):
        _fail("ORIGINAL_INDEX_HASH_LENGTH_OR_BOUND_MISMATCH")
    if (metadata.get("http_status") != 200 or type(metadata.get("http_status")) is not int
            or url != final or metadata.get("body_transformed", False) is not False
            or not (legacy or metadata.get("status") == "CAPTURED"
                    and metadata.get("origin") == "LIVE_PRIMARY_RESEARCH_HTTP"
                    and metadata.get("public_raw_available") is True)):
        _fail("NOT_COMPLETE_ORIGINAL_PUBLIC_SEC_INDEX")
    issuer, accession, _ = _url(url)
    started = _aware(metadata.get("retrieval_started_at" if legacy else "started_at"))
    captured = _aware(metadata.get("observed_at"))
    if not started <= captured <= physical:
        _fail("REVERSED_OR_FUTURE_SOURCE_CAPTURE_CLOCK")
    if re.search(rb"<!\s*ENTITY\b|<!DOCTYPE[^>]*\[", body, re.I):
        _fail("UNSUPPORTED_INDEX_ENTITY_OR_INTERNAL_DTD")
    return _known({"source_url": url, "body_sha256": expected, "body_bytes": size,
        "source_metadata_sha256": hashlib.sha256(_canonical(dict(metadata))).hexdigest(),
        "archive_routing_cik": issuer.zfill(10),
        "accession_number": f"{accession[:10]}-{accession[10:12]}-{accession[12:]}",
        "source_capture_started_at": started.isoformat(), "source_capture_completed_at": captured.isoformat(),
        "capture_completed_utc": captured.isoformat(), "publisher_publication_at": None,
        "publisher_publication_clock_verified": False}, metadata, physical)


class _VisibleIndex(_Index):
    """The index proof cannot borrow its row, clock or filer from hidden HTML."""
    def __init__(self, body):
        self.visibility, self.all_nodes = [], 0
        super().__init__(body)

    def handle_starttag(self, tag, attrs):
        self.all_nodes += 1
        if self.all_nodes > MAX_XML_ELEMENTS or len(self.visibility) > MAX_XML_DEPTH:
            _fail("INDEX_HTML_STRUCTURE_BOUND_EXCEEDED")
        values = dict(attrs)
        hidden = bool(self.visibility and self.visibility[-1][1]) or tag in {"script", "style", "noscript"}
        compact = re.sub(r"\s+", "", values.get("style", "")).lower()
        hidden |= "hidden" in values or "display:none" in compact or "visibility:hidden" in compact
        if tag not in {"br", "hr", "meta", "link", "img", "input"}:
            self.visibility.append((tag, hidden))
        if not hidden:
            super().handle_starttag(tag, attrs)

    def handle_data(self, data):
        if not self.visibility or not self.visibility[-1][1]:
            super().handle_data(data)

    def handle_endtag(self, tag):
        hidden = bool(self.visibility and self.visibility[-1][1])
        for index in range(len(self.visibility) - 1, -1, -1):
            if self.visibility[index][0] == tag:
                del self.visibility[index:]
                break
        if not hidden:
            super().handle_endtag(tag)


class _Blocks(HTMLParser):
    """Select complete original leaf blocks, retaining hidden-ancestor guards."""
    def __init__(self, body, selections):
        super().__init__(convert_charrefs=True)
        self.body, self.text = body, body.decode("utf-8", errors="strict")
        self.lines = [0] + [m.end() for m in re.finditer("\n", self.text)]
        self.targets, self.output, self.stack, self.active, self.nodes = {}, {}, [], None, 0
        for name, pin in selections.items():
            if not isinstance(pin, Mapping) or set(pin) != {"byte_start", "byte_end", "sha256"}:
                _fail("INVALID_WHOLE_BLOCK_PIN")
            start, end = pin["byte_start"], pin["byte_end"]
            if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(body)
                    or end - start > MAX_BLOCK_BYTES or hashlib.sha256(body[start:end]).hexdigest() != pin["sha256"]):
                _fail("WHOLE_BLOCK_HASH_OFFSET_OR_BOUND_MISMATCH")
            character = len(body[:start].decode("utf-8", errors="strict"))
            if character in self.targets:
                _fail("AMBIGUOUS_WHOLE_BLOCK_SELECTION")
            self.targets[character] = (name, dict(pin))

    def position(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > MAX_XML_ELEMENTS or len(self.stack) > MAX_XML_DEPTH:
            _fail("HTML_STRUCTURE_BOUND_EXCEEDED")
        attrs = dict(attrs)
        hidden = bool(self.stack and self.stack[-1][1]) or tag in {"script", "style", "noscript", "ix:hidden", "ix:header"}
        compact = re.sub(r"\s+", "", attrs.get("style", "")).lower()
        hidden |= "hidden" in attrs or "display:none" in compact or "visibility:hidden" in compact
        position = self.position()
        if position in self.targets:
            if self.active is not None or tag not in {"div", "p"} or hidden:
                _fail("NONVISIBLE_OR_NONLEAF_SELECTED_BLOCK")
            if any(ancestor in {"table", "tr", "td", "th"} for ancestor, _ in self.stack):
                _fail("UNSUPPORTED_TABLE_COLUMN_SCOPE")
            name, pin = self.targets[position]
            self.active = {"name": name, "pin": pin, "tag": tag, "depth": len(self.stack), "parts": [], "fact_ids": []}
        elif self.active is not None and (tag in {"div", "p", "table", "tr", "td", "th"} or hidden):
            _fail("NONVISIBLE_OR_NONLEAF_SELECTED_BLOCK")
        if self.active is not None and tag == "ix:nonfraction":
            self.active["fact_ids"].append(attrs.get("id"))
        if self.active is not None and (tag in {"sup", "a"} or attrs.get("footnoterefs")):
            _fail("UNRESOLVED_EXTERNAL_FOOTNOTE_OR_REFERENCE")
        if tag not in {"br", "hr", "meta", "link", "img", "input"}:
            self.stack.append((tag, hidden))

    def handle_data(self, data):
        if self.active is not None:
            self.active["parts"].append(data)

    def handle_endtag(self, tag):
        if (self.active is not None and tag == self.active["tag"]
                and len(self.stack) == self.active["depth"] + 1):
            end = self.text.find(">", self.position()) + 1
            if not end or len(self.text[:end].encode()) != self.active["pin"]["byte_end"]:
                _fail("SELECTION_NOT_COMPLETE_ORIGINAL_BLOCK")
            name = self.active["name"]
            self.output[name] = {**self.active["pin"], "literal_visible_passage": " ".join("".join(self.active["parts"]).split()),
                                 "fact_ids": self.active["fact_ids"], "format": "WHOLE_ORIGINAL_PARAGRAPH"}
            self.active = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def parse(self):
        self.feed(self.text); self.close()
        if self.active is not None or len(self.output) != len(self.targets):
            _fail("MISSING_OR_INCOMPLETE_ORIGINAL_BLOCK")
        return self.output


def _expanded(value, namespaces):
    prefix, local = value.split(":")
    return namespaces[prefix], local


def _quantity(body, selected, issuer, paragraph):
    if (not isinstance(selected, Mapping) or set(selected) != {
            "fact_id", "context_id", "unit_id", "value_literal", "unit_literal", "currency",
            "as_of_date", "payment_horizon_fiscal_year", "payment_qualifier_literal"}
            or selected["unit_literal"] != "billion" or selected["currency"] != "USD"
            or not isinstance(selected["value_literal"], str)
            or not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,8})?", selected["value_literal"])
            or Decimal(selected["value_literal"]) <= 0
            or selected["as_of_date"] != issuer["fiscal_period_end"]
            or type(selected["payment_horizon_fiscal_year"]) is not int
            or not int(issuer["fiscal_year_focus"]) <= selected["payment_horizon_fiscal_year"] <= int(issuer["fiscal_year_focus"]) + 10
            or selected["payment_qualifier_literal"] != "substantially all"):
        _fail("QUANTITY_UNIT_CURRENCY_PERIOD_OR_HORIZON_MISMATCH")
    facts, contexts, units, namespaces, pending, count, depth = [], [], [], [{}], {}, 0, 0
    element_namespaces = {}
    for event, node in ET.iterparse(BytesIO(body), events=("start-ns", "start", "end")):
        if event == "start-ns":
            pending[node[0]] = node[1]; continue
        if event == "start":
            count, depth = count + 1, depth + 1
            if count > MAX_XML_ELEMENTS or depth > MAX_XML_DEPTH:
                _fail("XML_STRUCTURE_BOUND_EXCEEDED")
            namespaces.append({**namespaces[-1], **pending}); pending = {}; continue
        ns = namespaces[-1]
        if node.tag in {f"{{{_XBRLDI}}}explicitMember", f"{{{_XBRLI}}}measure"}:
            element_namespaces[id(node)] = dict(ns)
        if node.tag == f"{{{_IX}}}nonFraction" and node.get("id") == selected["fact_id"]:
            facts.append((dict(node.attrib), "".join(node.itertext()), len(node), dict(ns)))
        elif node.tag == f"{{{_XBRLI}}}context" and node.get("id") == selected["context_id"]:
            contexts.append((node, dict(ns)))
        elif node.tag == f"{{{_XBRLI}}}unit" and node.get("id") == selected["unit_id"]:
            units.append((node, dict(ns)))
        namespaces.pop(); depth -= 1
    if len(facts) != 1 or len(contexts) != 1 or len(units) != 1 or paragraph["fact_ids"] != [selected["fact_id"]]:
        _fail("QUANTITY_FACT_CONTEXT_UNIT_OR_PARAGRAPH_AMBIGUOUS")
    fact, literal, children, ns = facts[0]
    taxonomy, concept = _expanded(fact["name"], ns)
    if (not re.fullmatch(r"https?://fasb.org/us-gaap/20[0-9]{2}", taxonomy) or concept != "OtherCommitment"
            or fact.get("contextRef") != selected["context_id"] or fact.get("unitRef") != selected["unit_id"]
            or fact.get("scale") != "9" or fact.get("sign") is not None or fact.get("format") is not None
            or fact.get("continuedAt") or fact.get("footnoteRefs") or children
            or any(key.endswith("}nil") and value != "false" for key, value in fact.items())
            or literal != selected["value_literal"] or not re.fullmatch(r"-?[0-9]{1,3}|INF", fact.get("decimals", ""))):
        _fail("NUMERIC_FACT_TAXONOMY_FORMAT_OR_SCALE_MISMATCH")
    context, context_ns = contexts[0]
    entities = context.findall(f"{{{_XBRLI}}}entity/{{{_XBRLI}}}identifier")
    periods = context.findall(f"{{{_XBRLI}}}period")
    members = list(context.iter(f"{{{_XBRLDI}}}explicitMember"))
    if (len(entities) != 1 or entities[0].get("scheme") != "http://www.sec.gov/CIK"
            or entities[0].text != issuer["cik"] or len(periods) != 1 or len(periods[0]) != 1
            or periods[0].findtext(f"{{{_XBRLI}}}instant") != selected["as_of_date"]
            or len(members) != 1 or list(context.iter(f"{{{_XBRLDI}}}typedMember"))
            or context.find(f"{{{_XBRLI}}}scenario") is not None):
        _fail("COMMITMENT_CONTEXT_ENTITY_DATE_OR_CATEGORY_MISMATCH")
    member_namespaces = element_namespaces[id(members[0])]
    axis_ns, axis = _expanded(members[0].get("dimension", ""), member_namespaces)
    member_ns, member = _expanded((members[0].text or "").strip(), member_namespaces)
    if (axis != "OtherCommitmentsAxis" or axis_ns != taxonomy or member != _MEMBER
            or not re.fullmatch(r"https?://[A-Za-z0-9.-]+/[0-9]{8}", member_ns)):
        _fail("SUPPLIER_OR_OTHER_CATEGORY_DIMENSION_REFUSED")
    unit, unit_ns = units[0]
    measures = unit.findall(f"{{{_XBRLI}}}measure")
    if (len(unit) != 1 or len(measures) != 1
            or _expanded(measures[0].text or "", element_namespaces[id(measures[0])]) != ("http://www.xbrl.org/2003/iso4217", "USD")):
        _fail("CURRENCY_UNIT_IS_NOT_SIMPLE_USD")
    text = paragraph["literal_visible_passage"]
    if not text.startswith(_CATEGORY + " reflect "):
        _fail("COMMITMENT_CATEGORY_NOT_LITERAL_IN_COMPLETE_PARAGRAPH")
    pattern = (r"As of ([A-Za-z]+ [0-9]{1,2}, [0-9]{4}), these commitments were \$" + re.escape(literal)
               + r" billion, of which substantially all will be paid through fiscal year "
               + str(selected["payment_horizon_fiscal_year"]) + r"\.")
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1 or _period(matches[0][1]).isoformat() != selected["as_of_date"]:
        _fail("COMMITMENT_VALUE_ASOF_OR_PAYMENT_SCOPE_NOT_LITERAL")
    qualifiers = ["in certain instances, these agreements are cancellable, able to be rescheduled, or adjustable for our business needs prior to placing firm orders",
                  "Changes to these agreements may result in additional costs."]
    if any(value not in text for value in qualifiers):
        _fail("COMMITMENT_QUALIFIERS_NOT_PRESERVED")
    return {"fact_id": selected["fact_id"], "fact_name": fact["name"], "fact_taxonomy_namespace": taxonomy,
        "context_id": selected["context_id"], "context_entity_identifier": issuer["cik"],
        "context_instant": selected["as_of_date"], "category_dimension": {
            "axis": members[0].get("dimension"), "axis_namespace": axis_ns,
            "member": (members[0].text or "").strip(), "member_namespace": member_ns,
            "scope_kind": "COMMITMENT_CATEGORY_NOT_COUNTERPARTY"},
        "value_literal": literal, "unit_literal": "billion", "currency": "USD", "unit_id": selected["unit_id"],
        "unit_measure": measures[0].text, "scale": 9, "decimals": fact["decimals"],
        "scaled_currency_amount": format((Decimal(literal) * Decimal(10) ** 9).normalize(), "f"),
        "scaled_amount_is_only_xbrl_unit_conversion": True,
        "measurement_kind": "OUTSTANDING_AGGREGATE_COMMITMENT_NOT_ACTUAL_SPEND",
        "category_literal": _CATEGORY, "payment_horizon_fiscal_year": selected["payment_horizon_fiscal_year"],
        "payment_horizon_qualifier_literal": "substantially all", "agreement_qualifiers_literal": qualifiers,
        "fully_non_cancelable_inferred": False, "footnote_refs": [],
        "source_format": "PARAGRAPH_WITH_INLINE_XBRL", "table_row_column_applicable": False}


def review_sec_purchase_obligation_scope(
    issuer_body: bytes, *, issuer_capture: Mapping, index_body: bytes,
    index_capture: Mapping, selection: Mapping, as_of: datetime | None = None,
) -> dict:
    """Pin a consolidated commitment category; never distribute it to suppliers.

    Supported scope is one complete manufacturing/supply/capacity paragraph
    with its literal billion-USD iXBRL fact, category dimension and qualifiers.
    A named supplier in another paragraph cannot be attached to this amount.
    Default cutoff and new review knowledge are physical ingestion, not the old
    source capture, fiscal date, index filing day or an unqualified accepted time.
    """
    physical = _aware(_physical_clock())
    report = {"schema_version": REVIEW_VERSION, "status": "REJECTED", "reason": "", "claim": None,
        "review_started_at": physical.isoformat(), "ingested_at": physical.isoformat(), "knowledge_at": physical.isoformat(),
        "as_of": None, "read_only": True, "source_http_attempts": 0, "usaspending_requests": 0,
        "registry_writes": 0, "scoring_applied": False, "model_modified": False, "trading_allowed": False,
        "supplier_attribution_allowed": False, "supplier_specific_quantity_verified": False,
        "supplier_legal_identity_verified": False, "actual_spend_verified": False, "purchase_volume_verified": False,
        "revenue_share_verified": False, "current_instrument_identity_verified": False,
        "current_q3_identity_verified": False, "continuous_identity_verified": False,
        "independent_sec_api_acceptance_clock_crosscheck_performed": False,
        "source_clock_conflict_resolved": False,
        "historical_availability_reconstructed": False, "out_of_sample_evaluation_verified": False}
    try:
        cutoff = _aware(as_of) if as_of is not None else None
        report["as_of"] = (cutoff or physical).isoformat()
        if cutoff is not None and physical > cutoff:
            _fail("AFTER_PHYSICAL_KNOWLEDGE_CUTOFF")
        if (not isinstance(selection, Mapping) or len(_canonical(dict(selection))) > MAX_SELECTION_BYTES
                or _sensitive(_canonical(dict(selection))) or set(selection) - _SELECTED_KEYS
                or selection.get("requested_counterparty") is not None
                or selection.get("claim_role") != "AGGREGATE_PURCHASE_COMMITMENT"
                or selection.get("statement_scope") != "CONSOLIDATED_GROUP"):
            _fail("UNSUPPORTED_OR_CROSS_SCOPE_SELECTION")
        source = _known(_original(issuer_body, issuer_capture, physical), issuer_capture, physical)
        index = _index_original(index_body, index_capture, physical)
        issuer = _issuer(issuer_body, selection, source["capture_completed_utc"])
        if issuer["cik"] != source["archive_routing_cik"] or issuer["document_type"] != "10-K":
            _fail("ISSUER_SOURCE_CONTEXT_CIK_OR_FORM_MISMATCH")
        facts, _ = _dei_and_contexts(issuer_body)
        ciks = {entry["value"] for context in facts.values() for entry in context.get("EntityCentralIndexKey", [])}
        if ciks != {issuer["cik"]}:
            _fail("COFILER_SCOPE_REQUIRES_SEPARATE_REVIEW")
        try:
            filing = _filing_index(
                SimpleNamespace(body=index_body, source_url=index["source_url"], observed_at=_aware(index["capture_completed_utc"])),
                SimpleNamespace(source_url=source["source_url"], observed_at=_aware(source["capture_completed_utc"]), size_bytes=len(issuer_body)),
                "10-K")
        except ValueError:
            _fail("INDEX_PRIMARY_SCOPE_ACCESSION_OR_DATE_CONFLICT")
        parsed, visible = _Index(index_body), _VisibleIndex(index_body)
        if any(getattr(parsed, key) != getattr(visible, key) for key in ("rows", "labels", "filer_ciks")):
            _fail("INDEX_PROOF_USES_NONVISIBLE_CONTENT")
        if filing["report_period_end"] != issuer["fiscal_period_end"]:
            _fail("INDEX_REPORT_PERIOD_DEI_CONFLICT")
        blocks = _Blocks(issuer_body, {key: selection.get(key) for key in (
            "group_heading", "notes_heading", "section_heading", "paragraph")}).parse()
        group, notes, section, paragraph = (blocks[key] for key in ("group_heading", "notes_heading", "section_heading", "paragraph"))
        group_literal = selection.get("group_name_literal")
        if (not isinstance(group_literal, str) or not 1 <= len(group_literal) <= 300
                or group["literal_visible_passage"] != group_literal or not group_literal.endswith(" and Subsidiaries")
                or notes["literal_visible_passage"] not in {"Notes to the Consolidated Financial Statements",
                                                           "Notes to the Consolidated Financial Statements (Continued)"}
                or section["literal_visible_passage"] != "Note 12 - Commitments and Contingencies"
                or not group["byte_end"] <= notes["byte_start"] < notes["byte_end"] <= section["byte_start"]
                < section["byte_end"] <= paragraph["byte_start"]
                or paragraph["byte_end"] - group["byte_start"] > 100_000):
            _fail("CONSOLIDATED_GROUP_NOTE_OR_PARAGRAPH_SCOPE_MISMATCH")
        quantity = _quantity(issuer_body, selection.get("quantity"), issuer, paragraph)
        completed = _aware(_physical_clock())
        if completed < physical:
            _fail("PHYSICAL_REVIEW_CLOCK_MOVED_BACKWARDS")
        if cutoff is not None and completed > cutoff:
            _fail("AFTER_PHYSICAL_KNOWLEDGE_CUTOFF")
        report.update(ingested_at=completed.isoformat(), knowledge_at=completed.isoformat(), as_of=(cutoff or completed).isoformat())
        claim = {"issuer_anchor": issuer, "primary_source": source, "filing_index_source": index, "filing": filing,
            "source_inputs_known_at": max(_aware(source["original_source_known_at"]), _aware(index["original_source_known_at"])).isoformat(),
            "claim_role": "AGGREGATE_PURCHASE_COMMITMENT", "quantity": quantity,
            "statement_scope": "CONSOLIDATED_GROUP", "group_name_literal": group_literal,
            "group_denominator": {"kind": "REPORTED_COMMITMENT_CATEGORY_TOTAL", "group_literal": group_literal,
                "category_literal": _CATEGORY, "ratio_denominator": None, "supplier_allocation_denominator": None,
                "all_group_commitment_categories_total_verified": False},
            "individual_issuer_amount_attribution_allowed": False, "group_legal_entity_membership_verified": False,
            "supplier_name": None, "supplier_quantity": None, "supplier_share": None, "supplier_revenue": None,
            "supplier_names_from_other_passages_joined": False,
            "evidence": blocks, "selection_sha256": hashlib.sha256(_canonical(dict(selection))).hexdigest(),
            "missing_facts": ["SUPPLIER_SPECIFIC_ALLOCATION_UNKNOWN", "SUPPLIER_LEGAL_IDENTITY_AND_CONTINUITY_UNVERIFIED",
                "CATEGORY_TOTAL_IS_NOT_ACTUAL_SPEND_VOLUME_OR_REVENUE", "INDIVIDUAL_GROUP_ENTITY_ALLOCATION_UNKNOWN",
                "SUBSTANTIALLY_ALL_IS_NOT_AN_EXACT_PAYMENT_SCHEDULE", "PUBLISHER_PUBLICATION_INSTANT_UNKNOWN",
                "SEC_API_INDEX_ACCEPTANCE_CLOCK_NOT_RECONCILED",
                "CURRENT_INSTRUMENT_AND_Q3_IDENTITY_UNVERIFIED", "RELEVANT_687_AND_HISTORICAL_COVERAGE_UNMEASURED"]}
        if filing["publisher_size_conflict"]:
            claim["missing_facts"].append("INDEX_SIZE_DIFFERS_FROM_RETAINED_SERVED_ORIGINAL")
        claim["candidate_id"] = "purchase-scope:" + hashlib.sha256(_canonical(claim)).hexdigest()
        return {**report, "status": "CANDIDATE_REVIEWED", "claim": claim}
    except _Rejected as error:
        return {**report, "reason": str(error)}
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError, ET.ParseError) as error:
        return {**report, "reason": "INVALID_PURCHASE_OBLIGATION_SCOPE_INPUT", "error_type": type(error).__name__}


__all__ = ["REVIEW_VERSION", "review_sec_purchase_obligation_scope"]
