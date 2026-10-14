"""Read-only operational quantity review over complete original SEC bytes.

DEI facts must pair name, CIK and fiscal period in one explicit context. A
combined co-filing's operational group quantity is never assigned to one of
its registrants. Values/units and scope are pinned literal source passages.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
from html import unescape
from html.parser import HTMLParser
from io import BytesIO
import json
import re
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET


REVIEW_VERSION = "sec-original-operational-metric-review-v1"
MAX_BODY_BYTES = 8_000_000
MAX_METADATA_BYTES = 64_000
MAX_SELECTION_BYTES = 16_000
MAX_PASSAGE_BYTES = 4_000
MAX_XML_ELEMENTS = 100_000
MAX_XML_DEPTH = 128
_IX = "http://www.xbrl.org/2013/inlineXBRL"
_XBRLI = "http://www.xbrl.org/2003/instance"
_XBRLDI = "http://xbrl.org/2006/xbrldi"
_DEI_FIELDS = {"EntityRegistrantName", "EntityCentralIndexKey", "DocumentPeriodEndDate",
               "DocumentFiscalYearFocus", "DocumentType"}
_METRICS = {
    "MANUFACTURING_SITE_COUNT": ("manufacturing sites", ("operates", "operated")),
    "EMPLOYEE_COUNT": ("employees", ("employs", "employed", "had")),
    "PRODUCTION_WELL_COUNT": ("production wells", ("operates", "operated", "had")),
}
_FORWARD_OR_FINANCIAL = re.compile(
    r"\b(?:not|never|no longer|will|would|may|might|could|expect\w*|forecast\w*|projected|planned|"
    r"target\w*|revenue|sales|shareholder\w*|stock price|market price)\b|[$%]", re.I)


class _Rejected(ValueError):
    pass


def _fail(reason: str) -> None:
    raise _Rejected(reason)


def _physical_clock() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: object) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        _fail("INVALID_KNOWLEDGE_CLOCK")
    return result.astimezone(timezone.utc)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()


def _sensitive(encoded: bytes) -> bool:
    text = unescape(encoded.decode("utf-8", errors="replace"))
    return any(re.search(pattern, text, re.I) for pattern in (
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", r"\bAKIA[A-Z0-9]{16}\b",
        r"\bbearer\s+[A-Za-z0-9._~+/=-]{12,}",
        r"(?:api[_-]?key|subscription[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|password)"
        r"[\"']?\s*[:=]\s*[\"']?[^\s\"'<>]{1,}",
        r"[?&](?:sig|signature|x-amz-signature|x-goog-signature|access_token)=[^\s\"'<>]{8,}",
    ))


def _capture(body: bytes, metadata: Mapping, expected: str, physical: datetime) -> dict:
    if not isinstance(metadata, Mapping) or len(_canonical(dict(metadata))) > MAX_METADATA_BYTES:
        _fail("INVALID_CAPTURE_METADATA")
    if (metadata.get("public_raw_available") is False or metadata.get("body_withheld") is True
            or metadata.get("withheld") is True
            or any(str(metadata.get(key, "")).startswith("WITHHELD")
                   for key in ("status", "outcome", "archive_status"))):
        _fail("WITHHELD_OR_UNAVAILABLE_WHOLE_BODY")
    if _sensitive(_canonical(dict(metadata))):
        _fail("UNSAFE_WHOLE_SOURCE_OR_METADATA")
    if (not isinstance(body, bytes) or len(body) > MAX_BODY_BYTES or not isinstance(expected, str)
            or not re.fullmatch(r"[a-f0-9]{64}", expected)
            or hashlib.sha256(body).hexdigest() != expected or metadata.get("raw_response_sha256") != expected
            or type(metadata.get("raw_response_bytes")) is not int or metadata["raw_response_bytes"] != len(body)):
        _fail("ORIGINAL_BODY_HASH_LENGTH_OR_BOUND_MISMATCH")
    if (type(metadata.get("http_status")) is not int or metadata["http_status"] != 200
            or ("body_transformed" in metadata and metadata["body_transformed"] is not False)
            or not (metadata.get("status") == "CAPTURED" and metadata.get("origin") == "LIVE_PRIMARY_RESEARCH_HTTP"
                    and metadata.get("public_raw_available") is True
                    or metadata.get("outcome") == "HTTP_SUCCESS" and metadata.get("access") == "public_without_credentials")):
        _fail("NOT_COMPLETE_ORIGINAL_PUBLIC_SEC_CAPTURE")
    source = metadata.get("requested_url")
    if not isinstance(source, str) or len(source) > 2_000 or source != metadata.get("final_url"):
        _fail("SEC_SOURCE_REFERENCE_CONFLICT")
    parsed = urlsplit(source)
    path = re.fullmatch(r"/Archives/edgar/data/([0-9]{1,10})/([0-9]{18})/([A-Za-z0-9_.-]+\.html?)", parsed.path)
    if (parsed.scheme != "https" or parsed.hostname != "www.sec.gov" or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.query or parsed.fragment or path is None):
        _fail("UNSAFE_OR_NONPRIMARY_SEC_REFERENCE")
    started, captured = _aware(metadata.get("started_at")), _aware(metadata.get("observed_at"))
    if not started <= captured <= physical:
        _fail("REVERSED_OR_FUTURE_SOURCE_CAPTURE_CLOCK")
    if _sensitive(body):
        _fail("UNSAFE_WHOLE_SOURCE_OR_METADATA")
    if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", body, re.I):
        _fail("UNSUPPORTED_XML_ENTITY_OR_DTD")
    accession = path[2]
    return {"source_url": source, "body_sha256": expected, "body_bytes": len(body),
            "source_metadata_sha256": hashlib.sha256(_canonical(dict(metadata))).hexdigest(),
            "archive_path": metadata.get("archive_path"), "archive_routing_cik": f"{int(path[1]):010d}",
            "accession_number": f"{accession[:10]}-{accession[10:12]}-{accession[12:]}",
            "source_capture_started_at": metadata["started_at"], "source_capture_completed_at": metadata["observed_at"],
            "capture_completed_utc": captured.isoformat(), "publisher_publication_at": None,
            "publisher_publication_clock_verified": False}


def _dei_and_contexts(body: bytes) -> tuple[dict, dict]:
    contexts, facts, namespaces, pending = {}, {}, [{}], {}
    elements, depth = 0, 0
    for event, node in ET.iterparse(BytesIO(body), events=("start-ns", "start", "end")):
        if event == "start-ns":
            pending[node[0]] = node[1]
            continue
        if event == "start":
            elements, depth = elements + 1, depth + 1
            if elements > MAX_XML_ELEMENTS or depth > MAX_XML_DEPTH:
                _fail("XML_STRUCTURE_BOUND_EXCEEDED")
            namespaces.append({**namespaces[-1], **pending})
            pending = {}
            continue
        if node.tag == f"{{{_XBRLI}}}context":
            identifier = node.get("id")
            if not identifier or identifier in contexts:
                _fail("DUPLICATE_OR_MISSING_XBRL_CONTEXT")
            entities = node.findall(f"{{{_XBRLI}}}entity/{{{_XBRLI}}}identifier")
            if len(entities) != 1 or entities[0].get("scheme") != "http://www.sec.gov/CIK":
                _fail("INVALID_XBRL_CONTEXT_ENTITY")
            dimensions = [{"dimension": member.get("dimension"), "member": " ".join(member.itertext()).strip()}
                          for member in node.iter() if member.tag in {
                              f"{{{_XBRLDI}}}explicitMember", f"{{{_XBRLDI}}}typedMember"}]
            contexts[identifier] = {"entity_identifier": (entities[0].text or "").strip(),
                                    "start_date": node.findtext(f"{{{_XBRLI}}}period/{{{_XBRLI}}}startDate"),
                                    "end_date": node.findtext(f"{{{_XBRLI}}}period/{{{_XBRLI}}}endDate"),
                                    "has_segment_or_scenario": (node.find(f"{{{_XBRLI}}}entity/{{{_XBRLI}}}segment") is not None
                                                                or node.find(f"{{{_XBRLI}}}scenario") is not None),
                                    "dimensions": dimensions}
        elif node.tag == f"{{{_IX}}}nonNumeric":
            name = node.get("name", "").split(":")
            if (len(name) == 2 and name[1] in _DEI_FIELDS
                    and re.fullmatch(r"https?://xbrl.sec.gov/dei/20[0-9]{2}", namespaces[-1].get(name[0], ""))):
                context = node.get("contextRef")
                if not context or node.get("continuedAt") or node.get("escape") == "true":
                    _fail("UNSUPPORTED_OR_MISSING_DEI_CONTEXT")
                value = " ".join("".join(node.itertext()).split())
                entries = facts.setdefault(context, {}).setdefault(name[1], [])
                if len(entries) >= 16 or len(value) > 1_000:
                    _fail("DEI_FACT_BOUND_EXCEEDED")
                entries.append({"value": value, "fact_id": node.get("id")})
        namespaces.pop()
        depth -= 1
    if len(facts) > 32:
        _fail("DEI_CONTEXT_BOUND_EXCEEDED")
    return facts, contexts


def _field(facts: dict, name: str) -> tuple[str, list[str | None]]:
    entries = facts.get(name, [])
    values = {entry["value"] for entry in entries}
    if len(values) != 1:
        _fail("MISSING_OR_CONFLICTING_SAME_CONTEXT_DEI_FACT")
    return next(iter(values)), [entry["fact_id"] for entry in entries]


def _period(value: str) -> date:
    value = re.sub(r"\s*,\s*", ", ", value)
    try:
        return date.fromisoformat(value)
    except ValueError:
        return datetime.strptime(value, "%B %d, %Y").date()


class _Passage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "ix:hidden"} or any(
            key in {"hidden", "style"} and (key == "hidden" or "display:none" in str(value).replace(" ", "").lower())
            for key, value in attrs):
            _fail("NONVISIBLE_SELECTED_PASSAGE")
        self.parts.append(" ")

    def handle_data(self, data):
        self.parts.append(data)


class _OriginalVisibility(HTMLParser):
    """Require selected plain spans to be content, outside hidden ancestors.

    Standalone text could otherwise borrow a number from a comment/script or a
    hidden ancestor that is absent from the selected fragment. No DOM rewrite
    or substitute body is created; offsets still bind the original bytes.
    """
    def __init__(self, text: str, targets: list[tuple[int, int]]):
        super().__init__(convert_charrefs=False)
        self.lines = [0] + [match.end() for match in re.finditer("\n", text)]
        self.targets, self.covered, self.stack = targets, [0] * len(targets), []

    def handle_starttag(self, tag, attrs):
        hidden = bool(self.stack and self.stack[-1][1]) or tag in {"script", "style", "ix:hidden", "ix:header"}
        for key, value in attrs:
            compact = str(value).replace(" ", "").lower()
            hidden |= key == "hidden" or key == "style" and any(
                marker in compact for marker in ("display:none", "visibility:hidden"))
        if tag not in {"br", "hr", "meta", "link", "img", "input"}:
            self.stack.append((tag, hidden))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return

    def _token(self, length: int):
        line, column = self.getpos()
        start = self.lines[line - 1] + column
        end = start + length
        for index, (left, right) in enumerate(self.targets):
            overlap = max(0, min(end, right) - max(start, left))
            if overlap:
                if self.stack and self.stack[-1][1]:
                    _fail("NONVISIBLE_SELECTED_PASSAGE")
                self.covered[index] += overlap

    def handle_data(self, data):
        self._token(len(data))

    def handle_entityref(self, name):
        self._token(len(name) + 2)

    def handle_charref(self, name):
        self._token(len(name) + 3)


def _visible_spans(body: bytes, spans: list[dict]) -> None:
    text, targets = body.decode("utf-8", errors="strict"), []
    for span in spans:
        raw = body[span["byte_start"]:span["byte_end"]]
        if b"<" in raw or b">" in raw:
            _fail("SELECT_PLAIN_ORIGINAL_PASSAGE_TEXT")
        start = len(body[:span["byte_start"]].decode("utf-8", errors="strict"))
        targets.append((start, start + len(raw.decode("utf-8", errors="strict"))))
    parser = _OriginalVisibility(text, targets)
    parser.feed(text)
    parser.close()
    if any(count != end - start for count, (start, end) in zip(parser.covered, targets)):
        _fail("PASSAGE_NOT_ORIGINAL_VISIBLE_CONTENT")


def _span(body: bytes, selection: object) -> dict:
    if not isinstance(selection, Mapping):
        _fail("INVALID_PASSAGE_SELECTION")
    start, end, digest = selection.get("byte_start"), selection.get("byte_end"), selection.get("sha256")
    if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(body)
            or end - start > MAX_PASSAGE_BYTES):
        _fail("PASSAGE_BYTE_BOUND_OR_OFFSET_MISMATCH")
    original = body[start:end]
    if digest != hashlib.sha256(original).hexdigest():
        _fail("PASSAGE_HASH_MISMATCH")
    if body.count(original) != 1:
        _fail("AMBIGUOUS_REPEATED_PASSAGE")
    parser = _Passage()
    parser.feed(original.decode("utf-8", errors="strict"))
    parser.close()
    text = " ".join("".join(parser.parts).split())
    if not text or len(text) > MAX_PASSAGE_BYTES:
        _fail("EMPTY_OR_UNBOUNDED_SELECTED_PASSAGE")
    return {"byte_start": start, "byte_end": end, "original_byte_sha256": digest,
            "literal_visible_passage": text}


def review_sec_operational_metric(
    body: bytes, *, capture_metadata: Mapping, expected_body_sha256: str,
    selection: Mapping, as_of: datetime | None = None,
) -> dict:
    """Review one pinned operating count, without promoting issuer/asset identity.

    Selection contains issuer_context_id/name/cik, fiscal_period_end, metric_kind,
    value_literal/unit_literal, metric_passage and scope_passage byte spans/hashes,
    and scope_kind (SINGLE_ISSUER_GROUP or COMBINED_REPORT_GROUP). No store, network
    or registry is touched. Physical review ingestion is never caller-controlled.
    An omitted cutoff uses that same physical clock; an explicit earlier cutoff
    cannot turn this newly ingested review into historical knowledge.
    """
    physical = _aware(_physical_clock())
    report = {"schema_version": REVIEW_VERSION, "status": "REJECTED", "candidate": None,
              "review_recorded_at": physical.isoformat(), "ingested_at": physical.isoformat(),
              "as_of": None, "knowledge_at": None, "http_attempts": 0, "read_only": True,
              "current_live_collection": False, "issuer_identity_promoted": False,
              "current_instrument_identity_verified": False, "current_q3_identity_verified": False,
              "asset_ownership_inferred": False, "quantity_or_revenue_share_inferred": False,
              "market_price_inference": False, "forward_looking_inference": False,
              "historical_availability_reconstructed": False, "scoring_applied": False,
              "prediction_input": False, "out_of_sample_evaluation_verified": False,
              "fixtures_count_as_live": False, "source_manifest_is_publisher_signed": False}
    try:
        cutoff = _aware(as_of) if as_of is not None else physical
        report["as_of"] = cutoff.isoformat()
        source = _capture(body, capture_metadata, expected_body_sha256, physical)
        report["source"] = source
        knowledge = max(physical, _aware(source["capture_completed_utc"]))
        report["knowledge_at"] = knowledge.isoformat()
        if knowledge > cutoff:
            _fail("AFTER_PHYSICAL_KNOWLEDGE_CUTOFF")
        if not isinstance(selection, Mapping) or len(_canonical(dict(selection))) > MAX_SELECTION_BYTES:
            _fail("INVALID_OR_UNBOUNDED_METRIC_SELECTION")
        if _sensitive(_canonical(dict(selection))):
            _fail("UNSAFE_SELECTION")
        context_id, issuer_name, issuer_cik = (selection.get(key) for key in ("issuer_context_id", "issuer_name", "issuer_cik"))
        if (not isinstance(context_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", context_id)
                or not isinstance(issuer_name, str) or not 1 <= len(issuer_name) <= 300
                or not isinstance(issuer_cik, str) or not re.fullmatch(r"[0-9]{10}", issuer_cik) or int(issuer_cik) == 0):
            _fail("INVALID_EXPLICIT_ISSUER_SELECTION")
        facts, contexts = _dei_and_contexts(body)
        chosen, context = facts.get(context_id, {}), contexts.get(context_id)
        name, name_ids = _field(chosen, "EntityRegistrantName")
        cik, cik_ids = _field(chosen, "EntityCentralIndexKey")
        period, period_ids = _field(chosen, "DocumentPeriodEndDate")
        year, year_ids = _field(chosen, "DocumentFiscalYearFocus")
        document_type, _ = _field(chosen, "DocumentType")
        if name != issuer_name or cik != issuer_cik:
            _fail("DEI_NAME_CIK_SAME_CONTEXT_MISMATCH")
        if (context is None or context["entity_identifier"] != cik or context["dimensions"]
                or context["has_segment_or_scenario"]):
            _fail("CONTEXT_ENTITY_OR_DIMENSIONS_NEED_SEPARATE_REVIEW")
        period_end = _period(period)
        period_start = date.fromisoformat(context["start_date"])
        if (document_type != "10-K" or period_end.isoformat() != selection.get("fiscal_period_end")
                or context["end_date"] != period_end.isoformat() or year != str(period_end.year)
                or not 0 < (period_end - period_start).days <= 370
                or period_end > _aware(source["capture_completed_utc"]).date()):
            _fail("FISCAL_PERIOD_OR_DOCUMENT_TYPE_MISMATCH")
        pairs = []
        for other_context, other_facts in facts.items():
            if "EntityRegistrantName" not in other_facts or "EntityCentralIndexKey" not in other_facts:
                continue
            other_name, _ = _field(other_facts, "EntityRegistrantName")
            other_cik, _ = _field(other_facts, "EntityCentralIndexKey")
            if not re.fullmatch(r"[0-9]{10}", other_cik) or int(other_cik) == 0:
                _fail("INVALID_EXPLICIT_COFILER_CIK")
            pairs.append({"context_id": other_context, "issuer_name": other_name, "issuer_cik": other_cik,
                          "context_entity_identifier": contexts.get(other_context, {}).get("entity_identifier"),
                          "context_has_segment_or_scenario": contexts.get(other_context, {}).get("has_segment_or_scenario"),
                          "context_dimensions": contexts.get(other_context, {}).get("dimensions", [])})
        metric, scope = _span(body, selection.get("metric_passage")), _span(body, selection.get("scope_passage"))
        _visible_spans(body, [metric, scope])
        metric_kind, value, unit = (selection.get(key) for key in ("metric_kind", "value_literal", "unit_literal"))
        if metric_kind not in _METRICS or not isinstance(value, str) or not re.fullmatch(r"(?:[0-9]{1,9}|[0-9]{1,3}(?:,[0-9]{3}){1,2})", value):
            _fail("UNSUPPORTED_OPERATIONAL_COUNT_OR_VALUE")
        expected_unit, relations = _METRICS[metric_kind]
        if unit != expected_unit or _FORWARD_OR_FINANCIAL.search(metric["literal_visible_passage"]):
            _fail("NONOPERATIONAL_OR_FORWARD_LOOKING_PASSAGE")
        relation = (r"\b(?:the Company|we|" + re.escape(issuer_name) + r")\s+(?:" + "|".join(relations)
                    + r")\s+(?P<qualifier>approximately\s+)?" + re.escape(value) + r"\s+" + re.escape(unit) + r"\b")
        matches = list(re.finditer(relation, metric["literal_visible_passage"], re.I))
        if len(matches) != 1:
            _fail("METRIC_VALUE_UNIT_OR_OPERATOR_NOT_LITERAL")
        if any(value != year for value in re.findall(r"\b20[0-9]{2}\b", metric["literal_visible_passage"])):
            _fail("METRIC_PERIOD_REFERENCE_CONFLICT")
        scope_kind, scope_text = selection.get("scope_kind"), scope["literal_visible_passage"]
        unique_ciks = {pair["issuer_cik"] for pair in pairs}
        if scope_kind == "COMBINED_REPORT_GROUP":
            if (len(unique_ciks) < 2 or not re.search(r"\bcombined report\b", scope_text, re.I)
                    or not re.search(r"\bconsolidated subsidiaries\b", scope_text, re.I)
                    or not all(pair["issuer_name"] in scope_text for pair in pairs)):
                _fail("COMBINED_REPORT_SCOPE_NOT_LITERAL")
        elif scope_kind == "SINGLE_ISSUER_GROUP":
            if len(unique_ciks) != 1 or issuer_name not in scope_text:
                _fail("INDIVIDUAL_ATTRIBUTION_AMBIGUOUS_IN_COFILING")
        else:
            _fail("UNSUPPORTED_EXPLICIT_OPERATIONAL_SCOPE")
        identity = {"context_id": context_id, "issuer_name": name, "issuer_cik": cik,
                    "context_entity_identifier": context["entity_identifier"], "context_dimensions": context["dimensions"],
                    "name_fact_ids": name_ids, "cik_fact_ids": cik_ids, "period_fact_ids": period_ids,
                    "fiscal_year_fact_ids": year_ids, "identity_status": "EXACT_DEI_SAME_CONTEXT_CANDIDATE"}
        candidate = {"issuer_anchor": identity, "cofiler_dei_context_pairs": pairs,
                     "fiscal_reporting_period": {"start": period_start.isoformat(), "end": period_end.isoformat(),
                                                 "fiscal_year_focus": year, "document_type": document_type},
                     "metric_kind": metric_kind, "value_literal": value, "value": str(Decimal(value.replace(",", ""))),
                     "unit_literal": unit, "approximate_in_source": bool(matches[0]["qualifier"]),
                     "metric_passage": metric, "scope_passage": scope, "scope_kind": scope_kind,
                     "individual_issuer_quantity_attribution_verified": False,
                     "metric_measurement_date_verified": False,
                     "period_basis": "LITERAL_FILING_FISCAL_CONTEXT_NOT_AN_INFERRED_METRIC_MEASUREMENT_DAY",
                     "source_body_sha256": source["body_sha256"]}
        candidate["candidate_id"] = "operational-metric:" + hashlib.sha256(_canonical(candidate)).hexdigest()
        return {**report, "status": "CANDIDATE_REVIEWED", "candidate": candidate}
    except _Rejected as error:
        return {**report, "reason": str(error)}
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError, ET.ParseError) as error:
        return {**report, "reason": "INVALID_OR_UNAVAILABLE_OPERATIONAL_REVIEW_INPUT", "error_type": type(error).__name__}


__all__ = ["REVIEW_VERSION", "review_sec_operational_metric"]
