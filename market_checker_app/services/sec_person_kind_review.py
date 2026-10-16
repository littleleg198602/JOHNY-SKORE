"""Pure dated person-fact review; SEC account/CIK attribution stays separate."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit, parse_qs

from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body
from market_checker_app.services.sec_form4_person_bridge import SECPersonCapture, _packet


VERSION = "sec-dated-person-kind-review-v1"
MAX_BYTES = 4_000_000


class PersonKindReviewError(ValueError):
    pass


def _clock(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PersonKindReviewError("Person fact review requires an explicit knowledge timezone")
    return value.astimezone(timezone.utc)


def _text(value, cap=8_000):
    if not isinstance(value, str) or not value.strip() or len(value) > cap:
        raise PersonKindReviewError("Person fact requires bounded exact source text")
    return value


def _norm(value):
    return " ".join(value.split())


def _url(value):
    _text(value, 2_048)
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.hostname != "www.sec.gov" or parsed.username or parsed.password
            or parsed.port not in {None, 443} or parsed.query or parsed.fragment or "%" in parsed.path
            or any(part in {".", ".."} for part in parsed.path.split("/"))):
        raise PersonKindReviewError("Person fact requires a canonical original SEC archive reference")
    match = re.fullmatch(r"/Archives/edgar/data/([0-9]+)/([0-9]{18})/([^\s/]+)", parsed.path)
    if not match:
        raise PersonKindReviewError("Person fact requires an original accession document")
    return match.groups()


@dataclass(frozen=True, slots=True)
class PersonKindCapture:
    record_id: str
    source_url: str
    body: bytes | None
    sha256: str
    size_bytes: int
    observed_at: datetime
    known_at: datetime | None = None
    retention: str = "ORIGINAL_BYTES"

    @property
    def available_at(self):
        observed = _clock(self.observed_at)
        known = _clock(self.observed_at if self.known_at is None else self.known_at)
        if known < observed:
            raise PersonKindReviewError("Person fact cannot be known before whole-body capture")
        return known

    def validate(self):
        _url(self.source_url); self.available_at
        if (not isinstance(self.record_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", self.record_id)
                or not isinstance(self.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.sha256)
                or isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int) or not 0 <= self.size_bytes <= MAX_BYTES
                or self.retention not in {"ORIGINAL_BYTES", "WITHHELD_SENSITIVE_BODY", "MISSING_BODY"}):
            raise PersonKindReviewError("Person fact capture metadata exceeds bounded policy")
        if self.retention == "ORIGINAL_BYTES":
            if not isinstance(self.body, bytes) or len(self.body) != self.size_bytes or hashlib.sha256(self.body).hexdigest() != self.sha256:
                raise PersonKindReviewError("Person fact whole original bytes/hash conflict")
        elif self.body is not None:
            raise PersonKindReviewError("Unavailable person fact cannot provide a replacement body")

    def summary(self):
        return {"record_id": self.record_id, "source_url": self.source_url, "sha256": self.sha256,
                "size_bytes": self.size_bytes, "observed_at": self.observed_at.isoformat(),
                "known_at": self.available_at.isoformat(), "retention": self.retention}


class _SelectedTables(HTMLParser):
    """Index all tables, collect only explicitly selected bounded contexts."""
    def __init__(self, body, selections):
        super().__init__(convert_charrefs=True)
        self.wanted = set(selections); self.table_count = 0; self.tables = []
        self.active = {}; self.contexts = {}; self.hidden = 0; self.nodes = 0
        self.feed(body.decode("utf-8", errors="strict"))

    def _open(self, key, tag):
        if key in self.wanted:
            if key in self.contexts or key in self.active:
                raise PersonKindReviewError("Person fact context locator is ambiguous")
            self.active[key] = {"tag": tag, "table": self.tables[-1][0], "parts": [], "size": 0}

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 200_000 or len(self.tables) > 64:
            raise PersonKindReviewError("Person fact HTML exceeds bounded complexity")
        if tag in {"script", "style", "noscript"}: self.hidden += 1
        if self.hidden: return
        if tag == "table":
            self.table_count += 1; self.tables.append([self.table_count, 0, 0])
            self._open(f"table:{self.table_count}", tag)
        elif tag == "tr" and self.tables:
            self.tables[-1][1] += 1; self.tables[-1][2] = 0
            self._open(f"table:{self.tables[-1][0]}/row:{self.tables[-1][1]}", tag)
        elif tag in {"td", "th"} and self.tables:
            self.tables[-1][2] += 1
            key = f"table:{self.tables[-1][0]}/row:{self.tables[-1][1]}/cell:{self.tables[-1][2]}"
            self._open(key, tag)
        for value in self.active.values(): value["parts"].append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self.hidden:
            self.hidden -= 1; return
        if self.hidden: return
        if self.tables:
            for key, value in list(self.active.items()):
                if value["tag"] == tag and value["table"] == self.tables[-1][0]:
                    self.contexts[key] = _norm("".join(value["parts"])); del self.active[key]
            if tag == "table": self.tables.pop()
        for value in self.active.values(): value["parts"].append(" ")

    def handle_data(self, data):
        if self.hidden: return
        for value in self.active.values():
            value["size"] += len(data)
            if value["size"] > 16_000:
                raise PersonKindReviewError("Selected person fact context exceeds text bound")
            value["parts"].append(data)


class _Index(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.rows = []; self.row = None; self.cell = None; self.cells = []
        self.label = None; self.current = None; self.labels = {}; self.nodes = 0
        self.company_depth = 0; self.filer_ciks = []
        self.feed(body.decode("utf-8", errors="strict"))

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 50_000 or len(self.rows) > 256:
            raise PersonKindReviewError("Person fact index exceeds bounded complexity")
        attrs = dict(attrs)
        if tag == "span" and (self.company_depth or attrs.get("class") == "companyName"):
            self.company_depth += 1
        if tag == "a" and self.company_depth:
            href = urlsplit(attrs.get("href", ""))
            ciks = parse_qs(href.query).get("CIK", [])
            if href.path == "/cgi-bin/browse-edgar" and len(ciks) == 1 and re.fullmatch(r"[0-9]{10}", ciks[0]):
                self.filer_ciks.append(ciks[0])
        if tag == "tr": self.row = []; self.cells = []
        if tag in {"td", "th"} and self.row is not None: self.cell = []
        if tag == "a" and self.row is not None: self.row.append(attrs.get("href", ""))
        if tag == "div" and attrs.get("class") in {"infoHead", "info"}:
            self.current = (attrs["class"], [])

    def handle_data(self, data):
        if self.cell is not None: self.cell.append(data)
        if self.current is not None: self.current[1].append(data)

    def handle_endtag(self, tag):
        if tag == "span" and self.company_depth: self.company_depth -= 1
        if tag in {"td", "th"} and self.cell is not None:
            self.cells.append(_norm("".join(self.cell))); self.cell = None
        if tag == "tr" and self.row is not None:
            self.rows.append({"hrefs": self.row, "cells": self.cells}); self.row = None
        if tag == "div" and self.current is not None:
            kind, parts = self.current; text = _norm("".join(parts))
            if kind == "infoHead": self.label = text
            elif self.label:
                if self.label in self.labels: raise PersonKindReviewError("SEC index clock label is ambiguous")
                self.labels[self.label] = text
            self.current = None


def _index_proof(index, primary):
    issuer, accession, filename = _url(primary.source_url)
    other_issuer, other_accession, index_name = _url(index.source_url)
    if (issuer, accession) != (other_issuer, other_accession) or not index_name.endswith("-index.html"):
        raise PersonKindReviewError("Person fact primary/index accession or issuer scope conflict")
    expected_index = f"{accession[:10]}-{accession[10:12]}-{accession[12:]}-index.html"
    if index_name != expected_index:
        raise PersonKindReviewError("Person fact index basename conflicts with its accession directory")
    parsed = _Index(index.body)
    if parsed.filer_ciks != [issuer.zfill(10)]:
        raise PersonKindReviewError("Person fact index filer CIK conflicts with accession source URL")
    matched = []
    for row in parsed.rows:
        hrefs = []
        for href in row["hrefs"]:
            if href.startswith("/ix?"):
                values = parse_qs(urlsplit(href).query).get("doc", [])
                href = values[0] if len(values) == 1 else ""
            hrefs.append(urljoin(index.source_url, href))
        if primary.source_url in hrefs: matched.append(row)
    if len(matched) != 1 or len(matched[0]["cells"]) < 5 or matched[0]["cells"][-2] != "DEF 14A":
        raise PersonKindReviewError("Person fact requires the index's exact primary DEF14A row")
    row = matched[0]
    try:
        filed_day = date.fromisoformat(parsed.labels["Filing Date"])
        declared_size = int(row["cells"][-1])
    except (KeyError, ValueError) as error:
        raise PersonKindReviewError("Person fact requires explicit index filing day and body size") from error
    if declared_size < 1:
        raise PersonKindReviewError("Person fact requires a positive declared primary size")
    if filed_day > min(_clock(index.observed_at), _clock(primary.observed_at)).date():
        raise PersonKindReviewError("Person fact filing day cannot follow original capture")
    return {"issuer_cik": issuer.zfill(10), "accession_key": accession, "primary_filename": filename,
            "form": "DEF 14A", "filing_day": filed_day.isoformat(),
            "acceptance_datetime_unqualified_raw": parsed.labels.get("Accepted"), "acceptance_timezone_inferred": False,
            "index_declared_primary_bytes": declared_size, "captured_primary_bytes": primary.size_bytes,
            "publisher_size_conflict": declared_size != primary.size_bytes,
            "exact_as_filed_body_version_verified": False}


def review_sec_person_kind(*, account_descriptor, account_capture: PersonKindCapture,
                          person_fact, primary_capture: PersonKindCapture,
                          index_capture: PersonKindCapture, knowledge_at: datetime,
                          requested_event_days=()) -> dict:
    """Verify a selected named age/birthplace bio, never its account-CIK link."""
    clock = _clock(knowledge_at)
    if not isinstance(account_descriptor, dict) or not isinstance(person_fact, dict):
        raise PersonKindReviewError("Person kind requires explicit account metadata and a bounded selected fact")
    cik = account_descriptor.get("owner_cik")
    if not isinstance(cik, str) or not re.fullmatch(r"[0-9]{10}", cik) or int(cik) == 0:
        raise PersonKindReviewError("Person kind requires the exact as-filed account CIK")
    account_name = _text(account_descriptor.get("owner_name"), 250)
    _url(account_descriptor.get("source_url"))
    if not re.fullmatch(r"[0-9a-f]{64}", str(account_descriptor.get("sha256", ""))):
        raise PersonKindReviewError("Person kind requires pinned original account source metadata")
    if not isinstance(requested_event_days, (list, tuple)) or len(requested_event_days) > 16:
        raise PersonKindReviewError("Person kind event selections exceed the bounded scope")
    try:
        event_days = [date.fromisoformat(day).isoformat() for day in requested_event_days]
    except (TypeError, ValueError) as error:
        raise PersonKindReviewError("Person kind needs explicit event calendar days") from error
    for capture in (primary_capture, index_capture, account_capture):
        if not isinstance(capture, PersonKindCapture): raise PersonKindReviewError("Person kind needs whole original captures")
        capture.validate()
    report = {"review_version": VERSION, "knowledge_at": clock.isoformat(), "status": "PARTIAL",
              "source_account_descriptor": {"owner_cik": cik, "owner_name": account_name,
                    "source_url": account_descriptor["source_url"], "sha256": account_descriptor["sha256"],
                    "validated_against_original_account_packet": False,
                    "legal_person_identity_from_account_verified": False},
              "natural_person_fact_supported": False, "source_account_to_person_cik_verified": False,
              "person_identity_verified": False, "approved_alias": False,
              "historical_event_validity_proven": False, "registry_modified": False, "scoring_applied": False,
              "instrument_identity_verified": False, "trading_allowed": False, "network_requests_performed": 0,
              "quota_or_lease_modified": False, "reasons": []}
    if any(capture.available_at > clock for capture in (primary_capture, index_capture, account_capture)):
        report["reasons"] = ["REQUIRED_ORIGINAL_CAPTURE_NOT_YET_KNOWN"]
        return report
    summaries = [capture.summary() for capture in (primary_capture, index_capture, account_capture)]
    for capture, summary in zip((primary_capture, index_capture, account_capture), summaries):
        if capture.retention != "ORIGINAL_BYTES" or _has_sensitive_body(capture.body):
            summary["retention"] = "WITHHELD_SENSITIVE_BODY" if capture.retention == "ORIGINAL_BYTES" else capture.retention
            report["captures"] = summaries; report["reasons"] = ["REQUIRED_ORIGINAL_BODY_UNAVAILABLE"]
            return report
    if account_descriptor["source_url"] != account_capture.source_url or account_descriptor["sha256"] != account_capture.sha256:
        raise PersonKindReviewError("Account descriptor/original capture pointer or hash conflict")
    packet_capture = SECPersonCapture(account_capture.record_id, "DATED_OWNERSHIP_PACKET", account_capture.source_url,
        account_capture.body, account_capture.sha256, account_capture.size_bytes, account_capture.observed_at,
        account_capture.known_at, account_capture.retention)
    packet_capture.validate()
    packet = _packet(packet_capture)
    account_matches = [owner for owner in packet["owners"] if owner["cik"] == cik and owner["name"] == account_name]
    if len(account_matches) != 1:
        raise PersonKindReviewError("Account descriptor CIK/name conflicts with original header/XML owner")
    report["source_account_descriptor"]["validated_against_original_account_packet"] = True
    report["source_account_descriptor"]["source_report_day"] = packet["report_day"]
    report["source_account_descriptor"]["issuer_as_filed"] = packet["issuer_as_filed"]
    report["source_account_descriptor"]["source_clock_conflicts"] = packet["clock_conflicts"]
    proof = _index_proof(index_capture, primary_capture)
    if proof["issuer_cik"] != packet["issuer_as_filed"]["cik"]:
        report["reasons"] = ["PERSON_BIO_ISSUER_SCOPE_DIFFERS_FROM_ACCOUNT_DISCLOSURE"]
        return report
    if _url(account_descriptor["source_url"])[1] == proof["accession_key"]:
        report["reasons"] = ["PERSON_FACT_NOT_INDEPENDENT_OF_ACCOUNT_DISCLOSURE"]
        return report
    name = _text(person_fact.get("person_name"), 250)
    locator = _text(person_fact.get("context_locator"), 80)
    quote = _text(person_fact.get("context_quote"))
    age_quote = _text(person_fact.get("age_quote"), 80)
    birthplace_quote = _text(person_fact.get("birthplace_quote"), 150)
    if not re.fullmatch(r"table:[1-9][0-9]{0,4}(?:/row:[1-9][0-9]{0,4}(?:/cell:[1-9][0-9]{0,4})?)?", locator):
        raise PersonKindReviewError("Person kind needs one exact bounded original table context")
    parser = _SelectedTables(primary_capture.body, (locator,))
    context = parser.contexts.get(locator)
    if context is None or context != _norm(quote):
        raise PersonKindReviewError("Person fact selected context is absent or exact quotation conflicts")
    name_matches = list(re.finditer(rf"(?<![A-Za-z0-9]){re.escape(_norm(name))}(?![A-Za-z0-9])", context, re.I))
    age = re.fullmatch(r"Age:\s*([0-9]{1,3})", _norm(age_quote))
    age_phrases = re.findall(r"\bAge:\s*[0-9]{1,3}\b", context)
    birth = re.fullmatch(r"Birthplace:\s*([A-Za-z][A-Za-z .'-]{1,100})", _norm(birthplace_quote))
    # One entire director-bio header must own all three facts. Broad tables,
    # a different person's age, a role label or entityType cannot support kind.
    bio = re.fullmatch(r"(.+?) Age: ([0-9]{1,3}) \| Director [Ss]ince: ([0-9]{4}) \| Birthplace: ([A-Za-z][A-Za-z .'-]{1,100})", context)
    organization_label = bool(re.search(r"\b(?:corp(?:oration)?|inc(?:orporated)?|ltd|limited|llc|llp|lp)\.?$", _norm(name), re.I))
    supported = (len(name_matches) == 1 and age is not None and 1 <= int(age[1]) <= 130
                 and len(age_phrases) == 1 and _norm(age_quote) in context and birth is not None
                 and _norm(birthplace_quote) in context and bio is not None
                 and bio[1].casefold() == _norm(name).casefold()
                 and _norm(age_quote) == f"Age: {bio[2]}"
                 and _norm(birthplace_quote) == f"Birthplace: {bio[4]}"
                 and 1900 <= int(bio[3]) <= date.fromisoformat(proof["filing_day"]).year
                 and not organization_label)
    report.update(captures=summaries, independent_disclosure_count=1, source_document=proof,
                  source_fact_available_at=max(primary_capture.available_at, index_capture.available_at, account_capture.available_at).isoformat(),
                  requested_events=[{"event_day": day, "matches_disclosure_point_day": day == proof["filing_day"],
                                     "account_person_identity_at_event_verified": False} for day in event_days])
    report["natural_person_fact_supported"] = bool(supported)
    report["reasons"] = (["NAMED_AGE_BIRTHPLACE_PERSON_CONTEXT_NOT_PROVEN"] if not supported else []) + [
        "ACCOUNT_CIK_TO_NAMED_PERSON_BRIDGE_UNPROVEN", "NO_HISTORICAL_EVENT_VALIDITY_FROM_DISCLOSURE_POINT"]
    if proof["publisher_size_conflict"]: report["reasons"].append("INDEX_DECLARED_PRIMARY_SIZE_DIFFERS_FROM_SERVED_ORIGINAL")
    if supported:
        report["dated_person_fact"] = {"name_as_published": name_matches[0].group(),
            "fact_kind": "NAMED_PERSON_BIO_WITH_EXPLICIT_AGE_AND_BIRTHPLACE", "age_as_published": int(age[1]),
            "age_quote": age_quote, "birthplace_quote": birthplace_quote, "context_locator": locator,
            "context_quote": context, "primary_original_sha256": primary_capture.sha256,
            "source_document_filing_day": proof["filing_day"], "captured_publication_timestamp_verified": False,
            "fact_scope": "AS_SERVED_DATED_DISCLOSURE_ANNOTATION_NOT_HISTORICAL_PERSON_VALIDITY",
            "legal_person_identity_or_birthday_inferred": False}
    report["account_name_exactly_matches_person_name"] = _norm(account_name).casefold() == _norm(name).casefold()
    report["limits"] = ["An independently dated named age/birthplace bio supports a natural-person fact, not the account CIK's legal identity",
        "Account-name order, POA/signature text and entityType cannot approve an alias or bind the CIK to this person",
        "A disclosure-point fact does not create a validity interval for earlier or later events",
        "Whole current original bytes and index metadata do not prove the exact historical as-filed byte version",
        "Index and primary document are one disclosure; capture time is machine knowledge time"]
    return report
