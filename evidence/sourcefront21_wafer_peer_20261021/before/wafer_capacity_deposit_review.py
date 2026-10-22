"""Offline original-byte review of refundable wafer-capacity financial assets.

Deposits/prepayments are reported asset balances, not purchases, outstanding
commitments or a physical capacity denominator. Nested equivalent iXBRL tags
describe one displayed balance and must never be added together.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from io import BytesIO
import json
import re
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from market_checker_app.services.sec_ir_disclosure_authority_review import _filing_index
from market_checker_app.services.sec_operational_metrics_review import (
    _Rejected, _aware, _canonical, _dei_and_contexts, _period, _sensitive,
)
from market_checker_app.services.sec_person_kind_review import _Index
from market_checker_app.services.sec_purchase_obligation_scope_review import (
    _Blocks, _expanded, _index_original, _known, _VisibleIndex,
)
from market_checker_app.services.sec_supply_chain_scope_review import _issuer, _original


REVIEW_VERSION = "wafer-capacity-deposit-original-review-v1"
REVIEW_KIND = "WAFER_CAPACITY_DEPOSITS"
MAX_BODY_BYTES = 4_000_000
MAX_REQUEST_BYTES = 32_000
_IX = "http://www.xbrl.org/2013/inlineXBRL"
_XBRLI = "http://www.xbrl.org/2003/instance"
_XBRLDI = "http://xbrl.org/2006/xbrldi"
_ROLE = "CAPACITY_RESERVATION_REFUNDABLE_DEPOSIT_AND_PREPAYMENT_ASSET"
_BLOCKS = ("notes_heading", "company_definition", "consolidation_scope", "section_heading",
           "cancellation_qualifiers", "deposit_paragraph")
_SELECT = {"issuer_ticker", "issuer_context_id", "issuer_name", "issuer_cik", "fiscal_period_end",
           "claim_role", "reporting_scope", "balances", *_BLOCKS}
_COMPONENTS = {
    "total": {"PrepaidExpenseCurrentAndNoncurrent", "DepositsAssets"},
    "current": {"PrepaidExpenseCurrent", "DepositsAssetsCurrent"},
    "noncurrent": {"PrepaidExpenseNoncurrent", "DepositsAssetsNoncurrent"},
}
_NUMBER = r"(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,6})?"
_INTRO = ("The Company maintains certain minimum purchase commitments under long-term capacity reservation "
          "agreements primarily with foundries for the purchase of wafers. Under these agreements, the Company "
          "has agreed to pay a combination of refundable deposits and prepayments to the suppliers in exchange "
          "for reserved manufacturing production capacity over the term of the agreements.")
_CANCELLATION = ("The Company purchases materials primarily pursuant to individual purchase orders, some of "
    "which have underlying master purchase agreements. Some of these purchase commitments are cancelable, "
    "and some are non-cancelable, depending on the terms with each individual supplier. In the event of "
    "cancellation, the Company may be required to pay costs incurred through the date of cancellation or other "
    "fees. When cancellation would result in incurring costs or other fees, the Company has historically sought "
    "to negotiate amended terms to the original agreements and orders to limit such exposure. As such, the "
    "Company believes that purchase commitments as of any particular date may not be a reliable indicator of future liabilities.")


def _fail(reason):
    raise _Rejected(reason)


def _physical_clock():
    return datetime.now(timezone.utc)


def _inputs(request, captures):
    if (not isinstance(request, Mapping) or set(request) != {"selection"}
            or len(_canonical(dict(request))) > MAX_REQUEST_BYTES or _sensitive(_canonical(dict(request)))
            or not isinstance(request["selection"], Mapping) or set(request["selection"]) != _SELECT):
        _fail("INVALID_OR_CROSS_SCOPE_DEPOSIT_REQUEST")
    if not isinstance(captures, Mapping) or set(captures) != {"issuer", "index"}:
        _fail("EXACT_ISSUER_AND_INDEX_CAPTURES_REQUIRED")
    copied = {}
    for name, value in captures.items():
        if (not isinstance(value, (tuple, list)) or len(value) != 2 or not isinstance(value[0], Mapping)
                or len(_canonical(dict(value[0]))) > 64_000 or _sensitive(_canonical(dict(value[0])))):
            _fail("INVALID_OR_UNSAFE_CAPTURE_METADATA")
        metadata, body = value
        if (body is not None and (type(body) is not bytes or len(body) > MAX_BODY_BYTES)):
            _fail("ORIGINAL_BODY_BOUND_OR_TYPE_MISMATCH")
        if (metadata.get("raw_publication_permitted") is False or metadata.get("public_raw_available") is False
                or metadata.get("body_withheld") is True or metadata.get("withheld") is True
                or any(str(metadata.get(key, "")).startswith("WITHHELD")
                       for key in ("status", "outcome", "archive_status"))):
            _fail("WITHHELD_OR_UNAVAILABLE_WHOLE_BODY")
        if metadata.get("complete_response") is not True:
            _fail("COMPLETE_ORIGINAL_RESPONSE_MANIFEST_REQUIRED")
        copied[name] = (json.loads(_canonical(dict(metadata))), body)
    return json.loads(_canonical(dict(request)))["selection"], copied


class _Xbrl:
    def __init__(self, body):
        self.ids, self.ns, self.parents = {}, {}, {}
        namespaces, pending, stack, count = [{}], {}, [], 0
        for event, node in ET.iterparse(BytesIO(body), events=("start-ns", "start", "end")):
            if event == "start-ns":
                pending[node[0]] = node[1]
            elif event == "start":
                count += 1
                if count > 100_000 or len(stack) >= 128:
                    _fail("DEPOSIT_XML_STRUCTURE_BOUND_EXCEEDED")
                namespaces.append({**namespaces[-1], **pending}); pending = {}
                if stack:
                    self.parents[node] = stack[-1]
                stack.append(node)
            else:
                identifier = node.get("id")
                if identifier:
                    if identifier in self.ids:
                        _fail("DUPLICATE_ORIGINAL_ELEMENT_IDENTIFIER")
                    self.ids[identifier] = node
                self.ns[node] = dict(namespaces[-1])
                namespaces.pop(); stack.pop()

    def node(self, identifier, kind):
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", identifier):
            _fail("INVALID_EXPLICIT_ORIGINAL_IDENTIFIER")
        node = self.ids.get(identifier)
        if node is None or node.tag != kind:
            _fail("ORIGINAL_FACT_CONTEXT_OR_UNIT_MISSING")
        return node

    def usd(self, identifier):
        unit = self.node(identifier, f"{{{_XBRLI}}}unit")
        if (len(unit) != 1 or unit[0].tag != f"{{{_XBRLI}}}measure"
                or _expanded(unit[0].text or "", self.ns[unit[0]]) != ("http://www.xbrl.org/2003/iso4217", "USD")):
            _fail("SIMPLE_USD_CURRENCY_UNIT_REQUIRED")
        return unit[0].text

    def fact(self, identifier, context, literal, concepts):
        node = self.node(identifier, f"{{{_IX}}}nonFraction")
        taxonomy, concept = _expanded(node.get("name", ""), self.ns[node])
        attrs = {"id", "name", "contextRef", "unitRef", "scale", "decimals", "format"}
        if (set(node.attrib) - attrs or not re.fullmatch(r"https?://fasb.org/us-gaap/20[0-9]{2}", taxonomy)
                or concept not in concepts or node.get("contextRef") != context
                or node.get("scale") != "6" or node.get("decimals") != "-5"
                or "".join(node.itertext()) != literal):
            _fail("ASSET_FACT_CONCEPT_CONTEXT_VALUE_OR_PRECISION_MISMATCH")
        if node.get("format") is not None:
            transform, name = _expanded(node.get("format"), self.ns[node])
            if (name != "num-dot-decimal" or transform not in {
                    "http://www.xbrl.org/inlineXBRL/transformation/2020-02-12",
                    "http://www.xbrl.org/inlineXBRL/transformation/2015-02-26"}):
                _fail("UNSUPPORTED_DEPOSIT_NUMERIC_TRANSFORMATION")
        unit = node.get("unitRef")
        measure = self.usd(unit)
        return node, {"fact_id": identifier, "source_concept": node.get("name"), "concept_local": concept,
            "taxonomy_namespace": taxonomy, "context_id": context, "value_literal": literal,
            "unit_id": unit, "unit_measure_literal": measure, "currency": "USD", "scale": 6,
            "decimals": -5, "format_literal": node.get("format"), "sign_attribute": None}

    def context(self, identifier, issuer, day, taxonomy):
        node = self.node(identifier, f"{{{_XBRLI}}}context")
        entities = node.findall(f"{{{_XBRLI}}}entity")
        periods = node.findall(f"{{{_XBRLI}}}period")
        if (len(node) != 2 or len(entities) != 1 or len(periods) != 1 or len(periods[0]) != 1
                or periods[0].findtext(f"{{{_XBRLI}}}instant") != day
                or len(entities[0]) != 2):
            _fail("ASSET_CONTEXT_MUST_BE_AN_EXACT_CATEGORY_INSTANT")
        identifiers = entities[0].findall(f"{{{_XBRLI}}}identifier")
        segments = entities[0].findall(f"{{{_XBRLI}}}segment")
        if (len(identifiers) != 1 or identifiers[0].get("scheme") != "http://www.sec.gov/CIK"
                or identifiers[0].text != issuer["cik"] or len(segments) != 1 or len(segments[0]) != 1
                or segments[0][0].tag != f"{{{_XBRLDI}}}explicitMember"):
            _fail("ASSET_CONTEXT_ENTITY_OR_CATEGORY_SCOPE_MISMATCH")
        member = segments[0][0]
        axis_ns, axis = _expanded(member.get("dimension", ""), self.ns[member])
        member_ns, member_name = _expanded(member.text or "", self.ns[member])
        if (axis_ns != taxonomy or axis != "RecordedUnconditionalPurchaseObligationByCategoryOfItemPurchasedAxis"
                or member_name != "WaferPurchasesMember"
                or not re.fullmatch(r"https?://[A-Za-z0-9.-]+/[0-9]{8}", member_ns)):
            _fail("EXACT_WAFER_CATEGORY_REQUIRED_NOT_A_NAMED_COUNTERPARTY")
        return {"context_id": identifier, "entity_identifier": issuer["cik"], "instant": day,
            "dimension": {"axis_literal": member.get("dimension"), "axis_namespace": axis_ns,
                "member_literal": member.text, "member_namespace": member_ns,
                "member_is_counterparty_identity": False, "axis_is_outstanding_obligation_amount": False}}


def _number(value):
    if not isinstance(value, str) or not re.fullmatch(_NUMBER, value):
        _fail("FINITE_NONNEGATIVE_LITERAL_ASSET_BALANCE_REQUIRED")
    amount = Decimal(value)
    if not amount.is_finite() or amount < 0:
        _fail("FINITE_NONNEGATIVE_LITERAL_ASSET_BALANCE_REQUIRED")
    return amount


def _balances(body, selected, issuer, paragraph, heading):
    periods = selected.get("balances")
    if not isinstance(periods, list) or len(periods) != 2:
        _fail("TWO_ORDERED_ASSET_INSTANTS_REQUIRED")
    xml = _Xbrl(body)
    groups, used, values, disclosures = [], [], [], []
    for index, period in enumerate(periods):
        if not isinstance(period, dict) or set(period) != {"as_of_day", "context_id", *_COMPONENTS}:
            _fail("EXACT_TOTAL_CURRENT_NONCURRENT_SELECTION_REQUIRED")
        day = date.fromisoformat(period["as_of_day"])
        expected = date.fromisoformat(issuer["fiscal_period_end"] if index == 0 else issuer["fiscal_period_start"])
        if index:
            expected -= timedelta(days=1)
        if day.isoformat() != period["as_of_day"] or day != expected:
            _fail("ASSET_INSTANT_DIFFERS_FROM_EXACT_FILING_COMPARATIVE_DATE")
        components, numeric = {}, {}
        for role, concepts in _COMPONENTS.items():
            part = period[role]
            if (not isinstance(part, dict) or set(part) != {"value_literal", "fact_ids"}
                    or not isinstance(part["fact_ids"], list) or not 1 <= len(part["fact_ids"]) <= 2):
                _fail("ONE_LITERAL_BALANCE_AND_ITS_EQUIVALENT_ANNOTATIONS_REQUIRED")
            literal, fact_ids = part["value_literal"], part["fact_ids"]
            numeric[role] = _number(literal)
            annotations, nodes = [], []
            for identifier in fact_ids:
                if identifier in used:
                    _fail("ONE_ANNOTATION_CANNOT_BE_REUSED_AS_ANOTHER_BALANCE")
                node, annotation = xml.fact(identifier, period["context_id"], literal, concepts)
                nodes.append(node); annotations.append(annotation); used.append(identifier)
            if (len({a["concept_local"] for a in annotations}) != len(annotations)
                    or len({a["taxonomy_namespace"] for a in annotations}) != 1
                    or len({a["unit_id"] for a in annotations}) != 1
                    or len(nodes[-1]) or len(nodes) == 1 and len(nodes[0])
                    or len(nodes) == 2 and (len(nodes[0]) != 1 or nodes[0][0] is not nodes[1]
                        or (nodes[0].text or "").strip() or (nodes[1].tail or "").strip())):
                _fail("EQUIVALENT_TAGS_MUST_WRAP_THE_SAME_SINGLE_DISPLAYED_BALANCE")
            context = xml.context(period["context_id"], issuer, period["as_of_day"], annotations[0]["taxonomy_namespace"])
            components[role] = {"component": role, "value_literal": literal, "unit_literal": "million",
                "currency": "USD", "source_unit_only_usd_conversion": format(numeric[role] * 1_000_000, "f"),
                "unit_conversion_is_not_a_customer_amount": True, "context": context,
                "equivalent_annotations": annotations, "unique_displayed_balance_count": 1,
                "equivalent_annotations_are_additive": False}
            for node in nodes:
                parent = xml.parents.get(node)
                while parent is not None and parent.tag != f"{{{_IX}}}nonNumeric":
                    parent = xml.parents.get(parent)
                if (parent is None or parent.get("contextRef") != issuer["context_id"]
                        or parent.get("continuedAt") or parent.get("escape") != "true"
                        or _expanded(parent.get("name", ""), xml.ns[parent]) != (
                            annotations[0]["taxonomy_namespace"], "CommitmentsAndContingenciesDisclosureTextBlock")
                        or not " ".join("".join(parent.itertext()).split()).startswith(heading["literal_visible_passage"])):
                    _fail("ASSET_FACTS_MUST_SHARE_THE_EXACT_ISSUER_COMMITMENTS_DISCLOSURE")
                disclosures.append(parent.get("id"))
        if numeric["total"] != numeric["current"] + numeric["noncurrent"]:
            _fail("DISCLOSED_TOTAL_DOES_NOT_EQUAL_CURRENT_PLUS_NONCURRENT")
        groups.append({"as_of_day": period["as_of_day"], "date_basis": "CURRENT_FILING_AS_REPORTED_INSTANT" if index == 0
                       else "CURRENT_FILING_COMPARATIVE_NOT_PRIOR_AS_FILED", "components": components,
                       "arithmetic": "total = current + noncurrent", "arithmetic_exact": True,
                       "total_and_components_must_not_be_summed": True})
        values.append({key: period[key]["value_literal"] for key in _COMPONENTS})
    if len(set(disclosures)) != 1:
        _fail("MIXED_DISCLOSURE_TEXT_BLOCKS_NOT_ALLOWED")
    sentences = []
    for index, parts in enumerate(values):
        sentences.append(r"As of (?P<date" + str(index) + r">[A-Za-z]+ [0-9]{1,2}, [0-9]{4}), deposits and prepayments "
            r"under the long-term capacity reservation agreements were \$" + re.escape(parts["total"])
            + r" million, with \$" + re.escape(parts["current"]) + r" million recorded within other current assets and \$"
            + re.escape(parts["noncurrent"]) + r" million recorded within other long-term assets\.")
    impairment = (r"(?: During fiscal (?P<impairment_year>20[0-9]{2}), the Company recorded impairment charges of \$"
        + r"(?P<impairment_value>" + _NUMBER + r") million within cost of goods sold due to reduced overall market demand "
        + r"related to long-term supply capacity deposits\.)?")
    match = re.fullmatch(re.escape(_INTRO) + impairment + " " + " ".join(sentences), paragraph["literal_visible_passage"])
    if not match or any(_period(match["date" + str(i)]).isoformat() != periods[i]["as_of_day"] for i in range(2)):
        _fail("COMPLETE_NARRATIVE_DATES_MONEY_UNITS_OR_ASSET_SCOPE_CONFLICT")
    excluded = [identifier for identifier in paragraph["fact_ids"] if identifier not in used]
    if len(set(paragraph["fact_ids"])) != len(paragraph["fact_ids"]) or len(set(used)) != len(used):
        _fail("DUPLICATE_DISPLAYED_ASSET_ANNOTATIONS")
    if match["impairment_value"] is None:
        if excluded:
            _fail("UNASSIGNED_NUMERIC_ANNOTATION_IN_DEPOSIT_PARAGRAPH")
    else:
        if len(excluded) != 1:
            _fail("IMPAIRMENT_QUALIFIER_IS_NOT_A_DEPOSIT_BALANCE")
        node = xml.node(excluded[0], f"{{{_IX}}}nonFraction")
        xml.fact(excluded[0], node.get("contextRef"), match["impairment_value"], {"AssetImpairmentCharges"})
        context = xml.node(node.get("contextRef"), f"{{{_XBRLI}}}context")
        if (context.findtext(f"{{{_XBRLI}}}entity/{{{_XBRLI}}}identifier") != issuer["cik"]
                or context.findtext(f"{{{_XBRLI}}}period/{{{_XBRLI}}}endDate", "")[:4] != match["impairment_year"]):
            _fail("IMPAIRMENT_QUALIFIER_ENTITY_OR_YEAR_CONFLICT")
    ordered = [identifier for period in periods for role in _COMPONENTS for identifier in period[role]["fact_ids"]]
    if [identifier for identifier in paragraph["fact_ids"] if identifier in used] != ordered:
        _fail("BALANCE_ANNOTATIONS_NOT_IN_THE_SELECTED_ORIGINAL_PARAGRAPH_ORDER")
    return groups, {"unique_balance_components": 6, "asset_numeric_annotation_count": len(used),
        "equivalent_nested_annotation_count": len(used) - 6, "disclosure_text_block_id": disclosures[0],
        "excluded_impairment_annotation_ids": excluded, "nested_tags_were_added": False,
        "unselected_impairment_is_not_a_deposit": True}


def review_wafer_capacity_deposits(request: Mapping, captures: Mapping, *, as_of: datetime | None = None) -> dict:
    """Review exactly two original as-reported asset instants, with no I/O.

    captures maps exactly issuer/index to (metadata, whole original bytes).
    request.selection pins complete blocks and total/current/noncurrent literal
    balances with their nested annotation IDs. Omitted cutoff uses physical
    completion; no supplied cutoff can backdate a newly constructed review.
    """
    started = _aware(_physical_clock())
    report = {"schema_version": REVIEW_VERSION, "review_kind": REVIEW_KIND, "status": "REJECTED", "reason": "", "claim": None,
        "review_started_at": started.isoformat(), "ingested_at": started.isoformat(), "knowledge_at": started.isoformat(), "as_of": None,
        "source_http_requests": 0, "collector_requests": 0, "usaspending_requests": 0, "db_writes": 0,
        "registry_adoptions": 0, "read_only": True, "scoring_applied": False, "model_modified": False,
        "fitter_modified": False, "trading_allowed": False, "activation_allowed": False,
        "named_foundry_attribution_allowed": False, "physical_capacity_verified": False, "physical_volume_verified": False,
        "purchases_verified": False, "outstanding_obligation_amount_verified": False, "actual_spend_verified": False,
        "revenue_amount_verified": False, "apple_allocation_allowed": False, "direct_purchase_share_verified": False,
        "supplier_identity_continuity_verified": False, "issuer_identity_adopted": False,
        "current_instrument_or_q3_verified": False, "historical_as_filed_comparison_verified": False,
        "historical_availability_reconstructed": False, "out_of_sample_verified": False,
        "source_manifest_is_publisher_signed": False, "source_identity_continuity_verified": False}
    try:
        cutoff = _aware(as_of) if as_of is not None else None
        report["as_of"] = (cutoff or started).isoformat()
        if cutoff is not None and cutoff < started:
            _fail("AFTER_PHYSICAL_KNOWLEDGE_CUTOFF")
        selected, captured = _inputs(request, captures)
        if selected["claim_role"] != _ROLE or selected["reporting_scope"] != "CONSOLIDATED_REPORTING_GROUP":
            _fail("ONLY_REFUNDABLE_DEPOSIT_PREPAYMENT_ASSET_SCOPE_SUPPORTED")
        metadata, body = captured["issuer"]; index_metadata, index_body = captured["index"]
        source = _known(_original(body, metadata, started), metadata, started)
        index = _index_original(index_body, index_metadata, started)
        issuer = _issuer(body, selected, source["capture_completed_utc"])
        facts, _ = _dei_and_contexts(body)
        if (issuer["cik"] != source["archive_routing_cik"] or issuer["document_type"] != "10-K"
                or {entry["value"] for fields in facts.values() for entry in fields.get("EntityCentralIndexKey", [])} != {issuer["cik"]}):
            _fail("COFILER_OR_ISSUER_SOURCE_SCOPE_CONFLICT")
        try:
            filing = _filing_index(SimpleNamespace(body=index_body, source_url=index["source_url"], observed_at=_aware(index["capture_completed_utc"])),
                SimpleNamespace(source_url=source["source_url"], observed_at=_aware(source["capture_completed_utc"]), size_bytes=len(body)), "10-K")
        except ValueError:
            _fail("INDEX_PRIMARY_ACCESSION_FORM_OR_DATE_CONFLICT")
        raw_index, visible_index = _Index(index_body), _VisibleIndex(index_body)
        if (filing["report_period_end"] != issuer["fiscal_period_end"]
                or any(getattr(raw_index, key) != getattr(visible_index, key) for key in ("rows", "labels", "filer_ciks"))):
            _fail("INDEX_PERIOD_OR_VISIBLE_PRIMARY_SCOPE_CONFLICT")
        blocks = _Blocks(body, {key: selected[key] for key in _BLOCKS}).parse()
        notes, definition, consolidation, heading, cancellation, paragraph = (blocks[key] for key in _BLOCKS)
        alias = re.match(re.escape(issuer["reported_dei_name"]) + r", together with its consolidated subsidiaries \([“\"]([A-Za-z0-9 .&'-]{1,100})[”\"] or the [“\"]Company[”\"]\), ", definition["literal_visible_passage"])
        if (not alias or notes["literal_visible_passage"] != "NOTES TO CONSOLIDATED FINANCIAL STATEMENTS"
                or consolidation["literal_visible_passage"] != "All " + alias[1] + " subsidiaries are included in the Company’s consolidated financial statements and all intercompany balances are eliminated in consolidation."
                or not re.fullmatch(r"[0-9]{1,2}\. COMMITMENTS AND CONTINGENCIES", heading["literal_visible_passage"])
                or cancellation["literal_visible_passage"] != _CANCELLATION
                or not all(blocks[a]["byte_end"] <= blocks[b]["byte_start"] for a, b in zip(_BLOCKS, _BLOCKS[1:]))
                or paragraph["byte_end"] - heading["byte_start"] > 80_000):
            _fail("EXACT_COMPANY_CONSOLIDATED_NOTE_AND_QUALIFIERS_REQUIRED")
        balances, audit = _balances(body, selected, issuer, paragraph, heading)
        completed = _aware(_physical_clock())
        if completed < started:
            _fail("PHYSICAL_REVIEW_CLOCK_MOVED_BACKWARDS")
        if cutoff is not None and completed > cutoff:
            _fail("AFTER_PHYSICAL_KNOWLEDGE_CUTOFF")
        report.update(ingested_at=completed.isoformat(), knowledge_at=completed.isoformat(), as_of=(cutoff or completed).isoformat())
        claim = {"issuer_anchor": issuer, "primary_source": source, "filing_index_source": index, "filing": filing,
            "claim_role": _ROLE, "reporting_scope": "CONSOLIDATED_REPORTING_GROUP", "balances": balances,
            "annotation_audit": audit, "evidence": blocks, "source_format": "WHOLE_ORIGINAL_NOTE_PARAGRAPH_WITH_NESTED_EQUIVALENT_IXBRL",
            "table_row_column_applicable": False, "unresolved_footnote_refs": [], "named_counterparty": None,
            "source_inputs_known_at": max(_aware(item["original_source_known_at"]) for item in (source, index)).isoformat(),
            "selection_sha256": hashlib.sha256(_canonical({"selection": selected})).hexdigest(),
            "publisher_size_difference_bytes": filing["captured_primary_bytes"] - filing["index_declared_primary_bytes"],
            "source_version_qualification": "RETAINED_SERVED_ORIGINAL_LITERAL_FACTS_ONLY_NOT_EXACT_HISTORICAL_AS_FILED",
            "missing_facts": ["NAMED_FOUNDRY_LEGAL_IDENTITY_AND_CONTINUITY_UNVERIFIED",
                "NO_PHYSICAL_WAFER_OR_RESERVED_CAPACITY_QUANTITY", "DEPOSITS_ARE_NOT_PURCHASES_SPEND_REVENUE_OR_REMAINING_COMMITMENTS",
                "NO_APPLE_OR_OTHER_CUSTOMER_ALLOCATION_OR_DIRECT_PURCHASE_SHARE", "COMPARATIVE_IS_CURRENT_FILING_NOT_PRIOR_AS_FILED",
                "PUBLISHER_PUBLICATION_INSTANT_UNKNOWN", "ORIGINAL_USASPENDING_DURABLE_HISTORY_AND_BROADER_AWARD_UEI_COVERAGE_MISSING"]}
        if filing["publisher_size_conflict"]:
            claim["missing_facts"].append("INDEX_DECLARED_SIZE_DIFFERS_FROM_RETAINED_SERVED_ORIGINAL")
        claim["candidate_id"] = "wafer-deposit:" + hashlib.sha256(_canonical(claim)).hexdigest()
        return {**report, "status": "CANDIDATE_REVIEWED", "claim": claim}
    except _Rejected as error:
        return {**report, "reason": str(error)}
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError, ET.ParseError, RecursionError) as error:
        return {**report, "reason": "INVALID_WAFER_CAPACITY_DEPOSIT_INPUT", "error_type": type(error).__name__}


__all__ = ["REVIEW_VERSION", "REVIEW_KIND", "review_wafer_capacity_deposits"]
