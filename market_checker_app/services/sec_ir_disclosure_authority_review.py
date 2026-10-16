"""Exact SEC-disclosed IR website facts; never publisher-authority adoption.

The original DEI context, whole visible paragraph and literal URI are separate
claims. A filing's dated website annotation does not verify the present domain
owner, publisher page, feed, issuer continuity or historical availability.
"""
from __future__ import annotations

from datetime import date, datetime
import hashlib
from html.parser import HTMLParser
import ipaddress
import json
import re
from types import SimpleNamespace
from urllib.parse import parse_qs, urljoin, urlsplit

from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body
from market_checker_app.services.sec_corporate_action_bridge import issuer_names
from market_checker_app.services.sec_operational_metrics_review import (
    _dei_and_contexts, _field, _period, _span, _visible_spans,
)
from market_checker_app.services.sec_person_kind_review import (
    PersonKindCapture as SECIRDisclosureCapture, _Index, _clock, _norm, _url,
)


REVIEW_VERSION = "sec-original-ir-website-disclosure-v1"
MAX_BODY_BYTES = 4_000_000
MAX_SELECTION_BYTES = 16_000


class IRDisclosureReviewError(ValueError):
    pass


class _IRBlock(HTMLParser):
    """Collect one complete, non-nested original div with character bounds."""
    def __init__(self, text, locator):
        super().__init__(convert_charrefs=True)
        if not isinstance(locator, str) or not re.fullmatch(r"div:[1-9][0-9]{0,5}", locator):
            raise IRDisclosureReviewError("Select one bounded whole original IR paragraph")
        self.target = int(locator[4:]); self.div_count = 0; self.div_depth = 0
        self.parts = []; self.text_size = 0; self.selected_depth = None
        self.inner_start = None; self.inner_end = None; self.hidden = 0; self.nodes = 0
        self.lines = [0] + [match.end() for match in re.finditer("\n", text)]
        self.feed(text); self.close()
        self.quote = _norm("".join(self.parts))

    def _position(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 100_000:
            raise IRDisclosureReviewError("IR document exceeds bounded HTML complexity")
        if tag in {"script", "style", "noscript", "ix:hidden"}: self.hidden += 1
        if tag == "div":
            self.div_count += 1; self.div_depth += 1
            if self.selected_depth is not None:
                raise IRDisclosureReviewError("Select a single IR paragraph, not a broad nested div")
            if self.div_count == self.target:
                self.selected_depth = self.div_depth
                self.inner_start = self._position() + len(self.get_starttag_text())

    def handle_endtag(self, tag):
        if tag == "div":
            if self.selected_depth == self.div_depth:
                self.inner_end = self._position(); self.selected_depth = None
            self.div_depth -= 1
        if tag in {"script", "style", "noscript", "ix:hidden"} and self.hidden: self.hidden -= 1

    def handle_data(self, data):
        if self.selected_depth is not None and not self.hidden:
            self.text_size += len(data)
            if self.text_size > 4_000:
                raise IRDisclosureReviewError("Selected IR paragraph exceeds its text bound")
            self.parts.append(data)


def _literal_uri(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 512 or not value.isascii()
            or any(ch.isspace() for ch in value)):
        raise IRDisclosureReviewError("Select the literal bounded IR URI without rewriting it")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.query
            or parsed.fragment or parsed.port not in {None, 80 if parsed.scheme == "http" else 443}
            or not parsed.hostname or parsed.hostname != parsed.netloc
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\.[a-z]{2,24}", parsed.hostname)
            or "%" in value or any(part in {".", ".."} for part in parsed.path.split("/"))):
        raise IRDisclosureReviewError("IR URI is not a literal public HTTP reference")
    if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in parsed.hostname.split(".")):
        raise IRDisclosureReviewError("IR URI has an invalid literal DNS label")
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return parsed
    raise IRDisclosureReviewError("A numeric host cannot establish disclosed issuer IR scope")


def _filing_index(index, source, expected_form):
    issuer, accession, filename = _url(source.source_url)
    other_issuer, other_accession, index_name = _url(index.source_url)
    if ((issuer, accession) != (other_issuer, other_accession)
            or not index_name.endswith(("-index.html", "-index.htm"))):
        raise IRDisclosureReviewError("IR original index and primary issuer/accession differ")
    expected_index = f"{accession[:10]}-{accession[10:12]}-{accession[12:]}-index"
    if index_name not in {expected_index + ".html", expected_index + ".htm"}:
        raise IRDisclosureReviewError("IR index basename conflicts with its accession directory")
    parsed = _Index(index.body)
    if parsed.filer_ciks != [issuer.zfill(10)]:
        raise IRDisclosureReviewError("IR index filer CIK differs from its archive context")
    matched = []
    for row in parsed.rows:
        refs = []
        for href in row["hrefs"]:
            if href.startswith("/ix?"):
                docs = parse_qs(urlsplit(href).query).get("doc", [])
                href = docs[0] if len(docs) == 1 else ""
            refs.append(urljoin(index.source_url, href))
        if source.source_url in refs: matched.append(row)
    if len(matched) != 1 or len(matched[0]["cells"]) < 5 or matched[0]["cells"][-2] != expected_form:
        raise IRDisclosureReviewError("IR source is not the index's exact primary filing row")
    try:
        filing_day = date.fromisoformat(parsed.labels["Filing Date"])
        declared = int(matched[0]["cells"][-1])
        report_day = date.fromisoformat(parsed.labels["Period of Report"])
    except (KeyError, ValueError) as error:
        raise IRDisclosureReviewError("IR index needs literal filing/report days and complete-body size") from error
    if declared < 1 or report_day > filing_day or filing_day > min(_clock(index.observed_at), _clock(source.observed_at)).date():
        raise IRDisclosureReviewError("IR index date or declared-body scope is inconsistent")
    return {"issuer_cik": issuer.zfill(10), "accession_key": accession, "primary_filename": filename,
        "document_type": expected_form, "filing_day": filing_day.isoformat(), "report_period_end": report_day.isoformat(),
        "acceptance_datetime_unqualified_raw": parsed.labels.get("Accepted"), "acceptance_timezone_inferred": False,
        "index_declared_primary_bytes": declared, "captured_primary_bytes": source.size_bytes,
        "publisher_size_conflict": declared != source.size_bytes, "exact_historical_as_filed_version_verified": False}


def review_sec_ir_disclosure_authority(*, source_capture: SECIRDisclosureCapture,
        index_capture: SECIRDisclosureCapture, selection: dict, knowledge_at: datetime) -> dict:
    """Review one original issuer-disclosed IR URI; leave authority unresolved."""
    clock = _clock(knowledge_at)
    report = {"review_version": REVIEW_VERSION, "status": "PARTIAL", "knowledge_at": clock.isoformat(),
        "issuer_disclosed_ir_website_fact_supported": False, "publisher_authority_verified": False,
        "live_verified": False, "issuer_continuity_verified": False, "feed_activation_allowed": False,
        "source_alias_approved": False, "hostname_is_issuer_identity": False, "issuer_attribution_allowed": False,
        "scoring_applied": False, "registry_modified": False, "trading_allowed": False,
        "historical_website_validity_verified": False, "historical_machine_availability_inferred": False,
        "network_requests_performed": 0, "quota_or_lease_modified": False, "reasons": []}
    for capture in (source_capture, index_capture):
        if not isinstance(capture, SECIRDisclosureCapture):
            raise IRDisclosureReviewError("IR disclosure requires two exact complete original captures")
        capture.validate()
    if any(capture.available_at > clock for capture in (source_capture, index_capture)):
        report["reasons"] = ["REQUIRED_ORIGINAL_CAPTURE_NOT_YET_KNOWN"]
        return report
    summaries = [capture.summary() for capture in (source_capture, index_capture)]
    for capture, summary in zip((source_capture, index_capture), summaries):
        if capture.retention != "ORIGINAL_BYTES" or _has_sensitive_body(capture.body):
            if capture.retention == "ORIGINAL_BYTES": summary["retention"] = "WITHHELD_SENSITIVE_BODY"
            report.update(captures=summaries, reasons=["REQUIRED_WHOLE_ORIGINAL_UNAVAILABLE"])
            return report
    if not isinstance(selection, dict):
        raise IRDisclosureReviewError("IR disclosure needs one bounded explicit selection")
    try:
        encoded = json.dumps(selection, allow_nan=False, ensure_ascii=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError) as error:
        raise IRDisclosureReviewError("IR disclosure selection is not bounded JSON") from error
    if len(encoded) > MAX_SELECTION_BYTES or _has_sensitive_body(encoded):
        raise IRDisclosureReviewError("IR disclosure selection is unavailable or exceeds its bound")
    context_id, name, cik = (selection.get(key) for key in ("issuer_context_id", "issuer_name", "issuer_cik"))
    if (not isinstance(context_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", context_id)
            or not isinstance(name, str) or not 1 <= len(name) <= 300
            or not isinstance(cik, str) or not re.fullmatch(r"[0-9]{10}", cik) or int(cik) == 0
            or selection.get("document_type") != "10-K"):
        raise IRDisclosureReviewError("IR disclosure requires exact issuer DEI and annual-form selections")
    index_proof = _filing_index(index_capture, source_capture, selection["document_type"])
    if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", source_capture.body, re.I):
        raise IRDisclosureReviewError("IR issuer context cannot use XML entity or DTD declarations")
    facts, contexts = _dei_and_contexts(source_capture.body)
    chosen, context = facts.get(context_id, {}), contexts.get(context_id)
    source_name, name_ids = _field(chosen, "EntityRegistrantName")
    source_cik, cik_ids = _field(chosen, "EntityCentralIndexKey")
    source_period, period_ids = _field(chosen, "DocumentPeriodEndDate")
    source_form, form_ids = _field(chosen, "DocumentType")
    period = _period(source_period).isoformat()
    if (source_name != name or source_cik != cik or index_proof["issuer_cik"] != cik
            or source_form != selection["document_type"] or period != selection.get("document_period_end")
            or period != index_proof["report_period_end"] or context is None
            or context["entity_identifier"] != cik or context["end_date"] != period
            or context["has_segment_or_scenario"] or context["dimensions"]):
        raise IRDisclosureReviewError("IR issuer DEI CIK/name/period are not the same exact source context")
    # The name parser accepts observed documents. Do not construct a filing
    # snapshot with an invented UTC publication instant from an unqualified day.
    source = SimpleNamespace(url=source_capture.source_url, observed_at=source_capture.observed_at,
                             payload=source_capture.body, published_at=None)
    bound = issuer_names(source)
    selected = {"issuer_name": name, "issuer_cik": cik, "anchor_date": period, "context_ref": context_id}
    if selected not in bound or {item["issuer_cik"] for item in bound} != {cik}:
        raise IRDisclosureReviewError("Co-filer or transformed DEI identity needs separate website-scope evidence")
    uri = selection.get("ir_url_literal"); parsed_uri = _literal_uri(uri)
    span = _span(source_capture.body, selection.get("ir_context_span"))
    _visible_spans(source_capture.body, [span])
    quote = selection.get("ir_context_quote")
    if not isinstance(quote, str) or not 1 <= len(quote) <= 4_000 or _norm(quote) != span["literal_visible_passage"]:
        raise IRDisclosureReviewError("IR whole paragraph quotation differs from its original span")
    text = source_capture.body.decode("utf-8", errors="strict")
    block = _IRBlock(text, selection.get("ir_context_locator"))
    left = len(source_capture.body[:span["byte_start"]].decode("utf-8"))
    right = len(source_capture.body[:span["byte_end"]].decode("utf-8"))
    if (block.inner_start is None or block.inner_end is None or not block.inner_start <= left < right <= block.inner_end
            or block.quote != span["literal_visible_passage"]):
        raise IRDisclosureReviewError("IR selection is not the complete exact original paragraph")
    # Require ownership wording and an explicit IR role for this exact URI.
    # Do not derive a scheme, subdomain, owner or authority from its hostname.
    matches = list(re.finditer(r"\bour investor relations website,\s*(" + re.escape(uri)
                              + r")(?=$|[\s),;]|\.(?:\s|$))", span["literal_visible_passage"], re.I))
    own_voice = re.match(r"^(?:Our investor relations website|We\b|A discussion regarding our\b)", span["literal_visible_passage"], re.I)
    ambiguous = re.search(r'\b(?:not|never|no longer|formerly|previously|third.party|their|subsidiary|affiliate|said|says|example)\b|["“”]',
                          span["literal_visible_passage"], re.I)
    if len(matches) != 1 or not own_voice or ambiguous:
        report.update(captures=summaries, reasons=["EXACT_OWN_ISSUER_IR_WEBSITE_RELATION_NOT_PROVEN"])
        return report
    raw = source_capture.body[span["byte_start"]:span["byte_end"]]
    literal = uri.encode("ascii")
    if raw.count(literal) != 1:
        raise IRDisclosureReviewError("IR URI must be one literal original byte occurrence in its paragraph")
    uri_start = span["byte_start"] + raw.index(literal)
    report.update(issuer_disclosed_ir_website_fact_supported=True, captures=summaries,
        source_inputs_available_at=max(source_capture.available_at, index_capture.available_at).isoformat(),
        independent_primary_disclosure_count=1, index_and_body_same_disclosure=True, filing=index_proof,
        issuer_disclosed_website_fact={"issuer_name": source_name, "issuer_cik": source_cik, "context_id": context_id,
            "context_entity_identifier": context["entity_identifier"], "document_type": source_form,
            "document_period_end": period, "name_fact_ids": name_ids, "cik_fact_ids": cik_ids,
            "period_fact_ids": period_ids, "form_fact_ids": form_ids,
            "ir_url_literal": uri, "disclosed_hostname": parsed_uri.hostname, "uri_scheme_rewritten": False,
            "paragraph_locator": selection["ir_context_locator"], "paragraph": span,
            "url_original_span": {"byte_start": uri_start, "byte_end": uri_start + len(literal),
                                  "sha256": hashlib.sha256(literal).hexdigest()},
            "whole_original_sha256": source_capture.sha256,
            "fact_scope": "AS_SERVED_DATED_ISSUER_DISCLOSURE_NOT_CURRENT_PUBLISHER_AUTHORITY"},
        reasons=["CURRENT_PUBLISHER_PAGE_AND_DOMAIN_AUTHORITY_UNVERIFIED", "NO_ISSUER_OR_WEBSITE_CONTINUITY_INTERVAL",
                 "NO_FEED_ACTIVATION_SOURCE_ALIAS_OR_HISTORICAL_AVAILABILITY_FROM_DISCLOSURE"])
    if index_proof["publisher_size_conflict"]:
        report["reasons"].append("INDEX_PRIMARY_SIZE_DIFFERS_FROM_SERVED_ORIGINAL")
    return report
