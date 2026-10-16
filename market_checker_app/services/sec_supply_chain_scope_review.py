"""Exact SEC supplier/object review; independent identity and quantity stay separate.

No graph edge, registry, score or financial attribution is written. A renderer's
legal name/CIK in one date column is a source snapshot, not a continuous legal
validity interval or proof of supplier-specific purchases/revenue.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
from html.parser import HTMLParser
import json
import re
from xml.etree import ElementTree as ET

from market_checker_app.services.sec_operational_metrics_review import (
    _Rejected, _aware, _canonical, _capture, _dei_and_contexts, _field, _period,
    _sensitive, _span, _visible_spans,
)
from market_checker_app.utils.ticker_universe import load_canonical_tickers


REVIEW_VERSION = "sec-supply-chain-original-scope-v1"
MAX_BODY_BYTES = 4_000_000
MAX_SELECTION_BYTES = 32_000
MAX_TABLE_ROWS = 2_000
_NON_CURRENT = re.compile(r"\b(?:not|never|no longer|will|would|might|could|expect\w*|plan\w*|forecast\w*)\b", re.I)


def _fail(reason):
    raise _Rejected(reason)


def _physical_clock():
    return datetime.now(timezone.utc)


def _original(body, metadata, physical):
    if not isinstance(metadata, Mapping) or not isinstance(body, bytes) or len(body) > MAX_BODY_BYTES:
        _fail("CAPTURE_BODY_OR_METADATA_BOUND_MISMATCH")
    original = dict(metadata)
    if (original.get("public_raw_available") is False or original.get("body_withheld") is True
            or original.get("withheld") is True
            or any(str(original.get(key, "")).startswith("WITHHELD") for key in ("status", "outcome", "archive_status"))):
        _fail("WITHHELD_OR_UNAVAILABLE_WHOLE_BODY")
    if original.get("capture_kind") == "RAW_HTTP_RESPONSE_BODY":
        if original.get("http_status") != 200 or original.get("url") != original.get("resolved_url"):
            _fail("INCOMPLETE_OR_CHANGED_ORIGINAL_CAPTURE")
        normalized = {**original, "requested_url": original["url"], "final_url": original["resolved_url"],
                      "started_at": original.get("retrieval_started_at"), "raw_response_sha256": original.get("sha256"),
                      "raw_response_bytes": original.get("size_bytes"), "status": "CAPTURED",
                      "origin": "LIVE_PRIMARY_RESEARCH_HTTP", "public_raw_available": True}
    else:
        normalized = original
    source = _capture(body, normalized, normalized.get("raw_response_sha256"), physical)
    source["original_capture_metadata_sha256"] = hashlib.sha256(_canonical(original)).hexdigest()
    return source


def _issuer(body, selection, captured):
    facts, contexts = _dei_and_contexts(body)
    identifier = selection.get("issuer_context_id")
    chosen, context = facts.get(identifier, {}), contexts.get(identifier)
    name, names = _field(chosen, "EntityRegistrantName")
    cik, ciks = _field(chosen, "EntityCentralIndexKey")
    period, periods = _field(chosen, "DocumentPeriodEndDate")
    year, years = _field(chosen, "DocumentFiscalYearFocus")
    document_type, _ = _field(chosen, "DocumentType")
    if (name != selection.get("issuer_name") or cik != selection.get("issuer_cik")
            or not re.fullmatch(r"[0-9]{10}", cik) or int(cik) == 0):
        _fail("ISSUER_DEI_NAME_CIK_CONTEXT_MISMATCH")
    if (context is None or context["entity_identifier"] != cik or context["dimensions"]
            or context["has_segment_or_scenario"]):
        _fail("ISSUER_CONTEXT_SCOPE_AMBIGUOUS")
    end, start = _period(period), date.fromisoformat(context["start_date"])
    if (document_type not in {"10-K", "20-F"} or end.isoformat() != selection.get("fiscal_period_end")
            or end.isoformat() != context["end_date"] or year != str(end.year)
            or not 0 < (end - start).days <= 370 or end > _aware(captured).date()):
        _fail("ISSUER_FISCAL_PERIOD_CONFLICT")
    ticker = selection.get("issuer_ticker")
    if ticker not in load_canonical_tickers():
        _fail("ISSUER_HINT_OUTSIDE_CANONICAL_UNIVERSE")
    return {"context_id": identifier, "reported_dei_name": name, "cik": cik, "canonical_ticker_hint": ticker,
            "name_fact_ids": names, "cik_fact_ids": ciks, "period_fact_ids": periods, "year_fact_ids": years,
            "fiscal_period_start": start.isoformat(), "fiscal_period_end": end.isoformat(),
            "fiscal_year_focus": year, "document_type": document_type,
            "current_instrument_identity_verified": False}


class _Rows(HTMLParser):
    """Read visible table cells and retain exact original row byte ranges."""
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.text = body.decode("utf-8", errors="strict")
        self.lines = [0] + [m.end() for m in re.finditer("\n", self.text)]
        self.tables, self.table, self.row, self.cell, self.hidden = [], None, None, None, 0
        self.visibility = []
        self.skipped_table_depth = 0

    def position(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        hidden = bool(self.visibility and self.visibility[-1][1]) or tag in {"script", "style"}
        compact = attrs.get("style", "").replace(" ", "").lower()
        hidden |= "hidden" in attrs or "display:none" in compact or "visibility:hidden" in compact
        if tag not in {"br", "hr", "meta", "link", "img", "input"}:
            self.visibility.append((tag, hidden))
        # SEC R1 renderers append hidden nested taxonomy-reference tables.
        # They are not facts. Retain a sentinel for each hidden top-level table
        # so a caller cannot select it as a visible legal-identity table.
        if self.skipped_table_depth:
            self.skipped_table_depth += int(tag == "table")
            return
        if hidden and tag == "table":
            self.skipped_table_depth = 1
            if self.table is None:
                self.tables.append([])
            return
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag == "table":
            if self.table is not None:
                _fail("NESTED_IDENTITY_RENDERER_TABLE")
            self.table = []
        if tag == "tr" and self.table is not None:
            if len(self.table) >= MAX_TABLE_ROWS or self.row is not None:
                _fail("IDENTITY_TABLE_ROW_BOUND_OR_STRUCTURE")
            self.row = {"cells": [], "start": self.position()}
        if tag in {"td", "th"} and self.row is not None:
            if self.cell is not None:
                _fail("NONVISIBLE_OR_NESTED_IDENTITY_CELL")
            self.cell = {"text": [], "colspan": int(attrs.get("colspan", 1)), "rowspan": int(attrs.get("rowspan", 1))}
            if not 1 <= self.cell["colspan"] <= 20 or not 1 <= self.cell["rowspan"] <= 20:
                _fail("IDENTITY_TABLE_SPAN_BOUND")

    def handle_data(self, text):
        if self.cell is not None and not self.hidden and not (self.visibility and self.visibility[-1][1]):
            self.cell["text"].append(text)

    def handle_endtag(self, tag):
        for index in range(len(self.visibility) - 1, -1, -1):
            if self.visibility[index][0] == tag:
                del self.visibility[index:]
                break
        if self.skipped_table_depth:
            self.skipped_table_depth -= int(tag == "table")
            return
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
        if tag in {"td", "th"} and self.cell is not None:
            self.cell["text"] = " ".join(" ".join(self.cell["text"]).split())
            self.row["cells"].append(self.cell)
            self.cell = None
        if tag == "tr" and self.row is not None:
            stop = self.text.index(">", self.position()) + 1
            self.row.update(byte_start=len(self.text[:self.row["start"]].encode()), byte_end=len(self.text[:stop].encode()))
            self.table.append(self.row)
            self.row = None
        if tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table = None

    def parse(self):
        self.feed(self.text)
        self.close()
        if self.table is not None or self.row is not None or self.skipped_table_depth or not self.tables:
            _fail("INCOMPLETE_IDENTITY_TABLE")
        return self.tables


def _grid(rows):
    spans, result = {}, []
    for row in rows:
        cells = {column: value for column, (value, remaining) in spans.items()}
        following = {column: (value, remaining - 1) for column, (value, remaining) in spans.items() if remaining > 1}
        column = 0
        for cell in row["cells"]:
            while column in cells:
                column += 1
            for target in range(column, column + cell["colspan"]):
                if target in cells or target > 20:
                    _fail("AMBIGUOUS_IDENTITY_TABLE_GRID")
                cells[target] = cell["text"]
                if cell["rowspan"] > 1:
                    following[target] = (cell["text"], cell["rowspan"] - 1)
            column += cell["colspan"]
        result.append({**row, "values": [cells.get(column, "") for column in range(max(cells, default=0) + 1)]})
        spans = following
    return result


def _render_date(text):
    value = text.strip()
    try:
        return date.fromisoformat(value)
    except ValueError:
        return datetime.strptime(value.replace(".", ""), "%b %d, %Y").date()


def _row_pin(body, row, pin):
    if not isinstance(pin, Mapping) or pin.get("byte_start") != row["byte_start"] or pin.get("byte_end") != row["byte_end"]:
        _fail("IDENTITY_FIELD_ROW_PIN_MISMATCH")
    raw = body[row["byte_start"]:row["byte_end"]]
    if pin.get("sha256") != hashlib.sha256(raw).hexdigest():
        _fail("IDENTITY_FIELD_ROW_HASH_MISMATCH")
    return {"byte_start": row["byte_start"], "byte_end": row["byte_end"], "original_byte_sha256": pin["sha256"],
            "literal_cells": row["values"]}


def _legal_snapshot(body, capture, selected, named, physical):
    result = {"status": "NAMED_ONLY", "independent_source_snapshot_supported": False,
              "relationship_date_identity_verified": False, "continuous_legal_validity_verified": False,
              "registry_identity_adopted": False, "financial_attribution_allowed": False}
    if body is None and capture is None and selected is None:
        return {**result, "reason": "NO_INDEPENDENT_LEGAL_SOURCE"}
    try:
        if not isinstance(selected, Mapping) or len(_canonical(dict(selected))) > MAX_SELECTION_BYTES:
            _fail("INVALID_COUNTERPARTY_SELECTION")
        source = _original(body, capture, physical)
        if selected.get("mode") != "SEC_R1_TABLE" or not source["source_url"].endswith("/R1.htm"):
            _fail("UNSUPPORTED_INDEPENDENT_IDENTITY_SOURCE")
        index, column = selected.get("table_index"), selected.get("value_column")
        if type(index) is not int or index < 0 or type(column) is not int or not 1 <= column <= 20:
            _fail("INVALID_IDENTITY_TABLE_SELECTION")
        tables = _Rows(body).parse()
        if index >= len(tables):
            _fail("MISSING_IDENTITY_TABLE")
        if not tables[index]:
            _fail("NONVISIBLE_IDENTITY_TABLE_CONTENT")
        rows, evidence, values = _grid(tables[index]), {}, {}
        for field, label in (("legal_name", "Entity Registrant Name"), ("cik", "Entity Central Index Key"),
                             ("period_end", "Document Period End Date"), ("fiscal_year", "Document Fiscal Year Focus"),
                             ("document_type", "Document Type")):
            choices = [row for row in rows if row["values"][0] == label]
            if len(choices) != 1 or column >= len(choices[0]["values"]):
                _fail("IDENTITY_FIELD_OR_COLUMN_AMBIGUOUS")
            evidence[field] = _row_pin(body, choices[0], selected.get("field_pins", {}).get(field))
            values[field] = choices[0]["values"][column]
        period = _render_date(values["period_end"])
        header_dates = []
        for row in rows[:2]:
            if column < len(row["values"]):
                match = re.match(r"[A-Z][a-z]{2}\.?\s+[0-9]{1,2},\s+20[0-9]{2}", row["values"][column])
                if match:
                    header_dates.append((row, _render_date(match[0])))
        if len(header_dates) != 1 or header_dates[0][1] != period:
            _fail("IDENTITY_DATE_COLUMN_HEADER_MISMATCH")
        evidence["header_period"] = _row_pin(body, header_dates[0][0], selected.get("field_pins", {}).get("header_period"))
        if (values["legal_name"] != selected.get("legal_name") or values["legal_name"] != named
                or values["cik"] != selected.get("cik") or values["cik"] != source["archive_routing_cik"]
                or not re.fullmatch(r"[0-9]{10}", values["cik"]) or int(values["cik"]) == 0
                or values["document_type"] not in {"20-F", "10-K"}
                or values["fiscal_year"] != str(period.year) or period.isoformat() != selected.get("report_period_end")
                or period > _aware(source["capture_completed_utc"]).date()):
            _fail("INDEPENDENT_LEGAL_NAME_CIK_OR_PERIOD_MISMATCH")
        return {**result, "status": "INDEPENDENT_LEGAL_SOURCE_SNAPSHOT", "independent_source_snapshot_supported": True,
                "reported_legal_name": values["legal_name"], "cik": values["cik"], "value_column": column,
                "report_period_end": period.isoformat(), "source": source, "field_evidence": evidence,
                "scope": "ONE_RENDERER_DATE_COLUMN_NOT_A_LEGAL_EFFECTIVE_INTERVAL"}
    except _Rejected as error:
        return {**result, "reason": str(error)}


def review_sec_supply_chain_scope(
    issuer_body: bytes, *, issuer_capture: Mapping, selection: Mapping,
    counterparty_body: bytes | None = None, counterparty_capture: Mapping | None = None,
    counterparty_selection: Mapping | None = None, as_of: datetime | None = None,
) -> dict:
    """Review one literal supplier/object claim plus optional independent snapshot.

    Selection pins issuer context/name/CIK/fiscal period and claim byte span. A
    claimed purchase share additionally needs its own literal value, unit, object
    denominator and fiscal year in a pinned source passage. Default knowledge is
    physical ingestion now; an old cutoff cannot backdate a newly formed review.
    """
    physical = _aware(_physical_clock())
    report = {"schema_version": REVIEW_VERSION, "status": "REJECTED", "claim": None,
              "review_recorded_at": physical.isoformat(), "ingested_at": physical.isoformat(),
              "knowledge_at": physical.isoformat(), "as_of": None, "read_only": True, "http_attempts": 0,
              "usaspending_requests": 0, "registry_writes": 0, "scoring_applied": False, "prediction_input": False,
              "current_instrument_identity_verified": False, "current_q3_identity_verified": False,
              "historical_availability_reconstructed": False, "counterparty_financials_attribution_allowed": False,
              "quantity_or_revenue_share_inferred": False, "sole_source_inferred": False,
              "counterparty_identity_verified": False, "out_of_sample_evaluation_verified": False,
              "fixtures_count_as_live": False}
    try:
        cutoff = _aware(as_of) if as_of is not None else physical
        report["as_of"] = cutoff.isoformat()
        if physical > cutoff:
            _fail("AFTER_PHYSICAL_KNOWLEDGE_CUTOFF")
        if not isinstance(selection, Mapping) or len(_canonical(dict(selection))) > MAX_SELECTION_BYTES or _sensitive(_canonical(dict(selection))):
            _fail("INVALID_OR_UNSAFE_SUPPLY_SELECTION")
        source = _original(issuer_body, issuer_capture, physical)
        issuer = _issuer(issuer_body, selection, source["capture_completed_utc"])
        if issuer["cik"] != source["archive_routing_cik"]:
            _fail("ISSUER_SOURCE_CONTEXT_CIK_MISMATCH")
        passage = _span(issuer_body, selection.get("claim_passage"))
        _visible_spans(issuer_body, [passage])
        named, obj, role = (selection.get(key) for key in ("counterparty_name", "product_or_input", "claim_role"))
        if any(not isinstance(value, str) or not 1 <= len(value) <= 300 for value in (named, obj, role)):
            _fail("INVALID_EXPLICIT_CLAIM_FIELDS")
        text = passage["literal_visible_passage"]
        if _NON_CURRENT.search(text):
            _fail("NEGATED_OR_FORWARD_RELATIONSHIP")
        if role == "FOUNDRY_SUPPLIER":
            pattern = r"\bWe utilize foundries, such as " + re.escape(named) + r"[^;\n]{0,250}\bto produce our " + re.escape(obj) + r"\b"
        elif role == "INPUT_SUPPLIER":
            pattern = r"\bWe purchase " + re.escape(obj) + r" from [^;\n]{0,100}" + re.escape(named)
        elif role == "CONTRACT_MANUFACTURER":
            pattern = r"\bWe engage with [^;\n]{0,150}\bsuch as " + re.escape(named) + r"[^;\n]{0,250}\bto perform " + re.escape(obj)
        else:
            _fail("UNSUPPORTED_EXPLICIT_SUPPLY_ROLE")
        if not re.search(pattern, text, re.I):
            _fail("NAMED_ROLE_OR_OBJECT_NOT_LITERAL")
        alias = selection.get("counterparty_alias")
        if alias is not None and (not isinstance(alias, str) or not re.fullmatch(r"[A-Za-z0-9.-]{1,30}", alias)
                                  or not re.search(re.escape(named) + r",?\s+or\s+" + re.escape(alias) + r"\b", text)):
            _fail("COUNTERPARTY_ALIAS_NOT_LITERAL_IN_SAME_CLAIM")
        quantity = {"status": "NOT_DISCLOSED_IN_SELECTED_PASSAGE", "value": None, "unit": None,
                    "denominator": None, "supplier_specific_quantity_reported": False, "revenue_share_reported": False}
        requested = selection.get("quantity")
        if requested is not None:
            if not isinstance(requested, Mapping):
                _fail("INVALID_QUANTITY_SELECTION")
            qpass = _span(issuer_body, requested.get("passage"))
            _visible_spans(issuer_body, [qpass])
            value, unit, denominator = (requested.get(key) for key in ("value_literal", "unit_literal", "denominator_literal"))
            if (not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,4})?", value)
                    or not 0 <= Decimal(value) <= 100 or unit not in {"%", "percent"}
                    or denominator != f"our purchases of {obj}"):
                _fail("QUANTITY_UNIT_DENOMINATOR_OR_RANGE_MISMATCH")
            operators = [named] + ([alias] if alias else [])
            qpattern = (r"\b(?:" + "|".join(re.escape(name) for name in operators) + r") supplied " + re.escape(value)
                        + r"\s*" + re.escape(unit) + r"\s+of " + re.escape(denominator) + r" in fiscal "
                        + re.escape(issuer["fiscal_year_focus"]) + r"\b")
            if _NON_CURRENT.search(qpass["literal_visible_passage"]) or not re.search(qpattern, qpass["literal_visible_passage"]):
                _fail("SUPPLIER_QUANTITY_NOT_LITERAL_FOR_OBJECT_AND_PERIOD")
            quantity.update(status="LITERAL_REPORTED_PURCHASE_SHARE", value=value, unit=unit, denominator=denominator,
                            supplier_specific_quantity_reported=True, passage=qpass)
        legal = _legal_snapshot(counterparty_body, counterparty_capture, counterparty_selection, named, physical)
        if legal.get("source") and (legal["source"]["accession_number"] == source["accession_number"]
                                    or legal["source"]["body_sha256"] == source["body_sha256"]):
            legal.update(status="NAMED_ONLY", independent_source_snapshot_supported=False, reason="SAME_DISCLOSURE_NOT_INDEPENDENT")
        missing = ["RELATIONSHIP_EXACT_EVENT_DAY_NOT_DISCLOSED", "CONTINUOUS_LEGAL_IDENTITY_AT_RELATIONSHIP_DATE_UNVERIFIED",
                   "CURRENT_INSTRUMENT_IDENTITY_UNVERIFIED", "RELEVANT_687_COVERAGE_UNMEASURED"]
        if not legal["independent_source_snapshot_supported"]:
            missing.append("INDEPENDENT_COUNTERPARTY_LEGAL_SNAPSHOT_MISSING")
        if not quantity["supplier_specific_quantity_reported"]:
            missing.extend(["SUPPLIER_SPECIFIC_QUANTITY_UNKNOWN", "SUPPLIER_SPECIFIC_UNIT_UNKNOWN", "PURCHASE_DENOMINATOR_UNKNOWN"])
        claim = {"issuer_anchor": issuer, "source": source, "claim_role": role, "counterparty_name": named,
                 "source_disclosed_alias": alias, "product_or_input": obj, "claim_passage": passage,
                 "relationship_statement_supported": True, "relationship_exact_event_day": None,
                 "counterparty_legal_identity": legal, "quantified_exposure": quantity,
                 "missing_facts": missing, "ownership_or_country_inferred": False}
        claim["candidate_id"] = "supply-scope:" + hashlib.sha256(_canonical(claim)).hexdigest()
        return {**report, "status": "CANDIDATE_REVIEWED", "claim": claim}
    except _Rejected as error:
        return {**report, "reason": str(error)}
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError, ET.ParseError) as error:
        return {**report, "reason": "INVALID_SUPPLY_SCOPE_INPUT", "error_type": type(error).__name__}


__all__ = ["REVIEW_VERSION", "review_sec_supply_chain_scope"]
