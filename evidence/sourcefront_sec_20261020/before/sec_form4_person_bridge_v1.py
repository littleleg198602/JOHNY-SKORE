"""Original-byte independent SEC account/name bridge for Form 4 owners.

An exact CIK/filer-name match is not a verified natural person, a legal-name
alias, instrument identity, or director/officer appointment. A current profile
is an observed snapshot; a dated older filing proves its own disclosed date
only. This service performs no HTTP, storage, registry adoption or scoring.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from urllib.parse import urlsplit
from xml.etree import ElementTree

from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body
from market_checker_app.services.sec_form4_service import PARSER_VERSION


BRIDGE_VERSION = "sec-form4-independent-owner-account-bridge-v1"
MAX_CAPTURE_BYTES = 2_000_000
MAX_EVIDENCE_CAPTURES = 8
MAX_SOURCE_OWNERS = 16
MAX_SOURCE_ROWS = 2_000
MAX_REVIEW_PAIRS = 4_096


class Form4PersonBridgeError(ValueError):
    pass


def _clock(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise Form4PersonBridgeError("Person bridge requires an explicit knowledge timezone")
    return value.astimezone(timezone.utc)


def _cik(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{10}", value) or int(value) == 0:
        raise Form4PersonBridgeError("Person bridge requires an exact ten-digit CIK")
    return value


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 250:
        raise Form4PersonBridgeError("Person bridge requires bounded explicit filer names")
    return value


def _name_key(value):
    # Case/whitespace only: no reordering, punctuation removal, fuzzy names or
    # organization suffix aliases, including a signer/attorney spelling.
    return " ".join(_name(value).split()).casefold()


def _reference(value):
    if not isinstance(value, str) or len(value) > 2_048:
        raise Form4PersonBridgeError("Person bridge requires a bounded SEC reference")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.hostname not in {"www.sec.gov", "data.sec.gov"}
            or parsed.port not in {None, 443} or parsed.username or parsed.password
            or parsed.query or parsed.fragment or "%" in parsed.path
            or any(part in {".", ".."} for part in parsed.path.split("/"))):
        raise Form4PersonBridgeError("Person bridge rejected a noncanonical SEC reference")
    if parsed.hostname == "data.sec.gov":
        if not re.fullmatch(r"/submissions/CIK[0-9]{10}\.json", parsed.path):
            raise Form4PersonBridgeError("Person bridge supports an exact SEC submissions profile only")
    elif not re.fullmatch(r"/Archives/edgar/data/[0-9]+/[0-9]{18}/[^\s]+", parsed.path):
        raise Form4PersonBridgeError("Person bridge requires an original SEC accession reference")
    return value


def _accession_key(url):
    # An accession is globally unique. Owner/issuer directory variants, XSL,
    # index, SGML, and raw XML of it are all the same disclosure.
    match = re.match(r"/Archives/edgar/data/[0-9]+/([0-9]{18})/", urlsplit(url).path)
    return match[1] if match else None


@dataclass(frozen=True, slots=True)
class SECPersonCapture:
    record_id: str
    purpose: str
    source_url: str
    body: bytes | None
    sha256: str
    size_bytes: int
    observed_at: datetime
    known_at: datetime | None = None
    retention: str = "ORIGINAL_BYTES"

    @property
    def available_at(self):
        observed, known = _clock(self.observed_at), _clock(self.known_at or self.observed_at)
        if known < observed:
            raise Form4PersonBridgeError("Person evidence cannot be known before original capture")
        return known

    def validate(self):
        _reference(self.source_url)
        if (not isinstance(self.record_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", self.record_id)
                or self.purpose not in {"FORM4_SOURCE", "OWNER_PROFILE", "DATED_OWNERSHIP_PACKET", "FORM144_NOTICE_PACKET"}
                or not isinstance(self.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.sha256)
                or isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int)
                or not 0 <= self.size_bytes <= MAX_CAPTURE_BYTES
                or self.retention not in {"ORIGINAL_BYTES", "WITHHELD_SENSITIVE_BODY", "MISSING_BODY"}):
            raise Form4PersonBridgeError("Person capture requires bounded exact original metadata")
        self.available_at
        if self.retention == "ORIGINAL_BYTES":
            if (not isinstance(self.body, bytes) or len(self.body) != self.size_bytes
                    or hashlib.sha256(self.body).hexdigest() != self.sha256):
                raise Form4PersonBridgeError("Person evidence original bytes/hash conflict")
        elif self.body is not None:
            raise Form4PersonBridgeError("Unavailable person evidence cannot provide a replacement body")

    def summary(self):
        return {"record_id": self.record_id, "purpose": self.purpose, "source_url": self.source_url,
                "sha256": self.sha256, "size_bytes": self.size_bytes, "observed_at": self.observed_at.isoformat(),
                "known_at": self.available_at.isoformat(), "retention": self.retention}


def _xml(raw):
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", raw, re.I):
        raise Form4PersonBridgeError("Person bridge rejected an XML entity declaration")
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as error:
        raise Form4PersonBridgeError("Person bridge requires a complete ownership XML document") from error
    if root.tag != "ownershipDocument" or sum(1 for _ in root.iter()) > 20_000:
        raise Form4PersonBridgeError("Person bridge rejected ownership XML scope/complexity")
    return root


def _one(root, path):
    nodes = root.findall(path)
    if len(nodes) != 1 or not nodes[0].text:
        raise Form4PersonBridgeError("Person bridge source has missing/ambiguous identity fields")
    return nodes[0].text.strip()


def _xml_owners(root):
    nodes = root.findall("reportingOwner")
    if not 1 <= len(nodes) <= MAX_SOURCE_OWNERS:
        raise Form4PersonBridgeError("Person bridge reporting-owner scope is unbounded or empty")
    owners = [{"cik": _cik(_one(node, "reportingOwnerId/rptOwnerCik")),
               "name": _name(_one(node, "reportingOwnerId/rptOwnerName"))} for node in nodes]
    if len({row["cik"] for row in owners}) != len(owners):
        raise Form4PersonBridgeError("Person bridge has duplicate/conflicting reporting-owner CIKs")
    return owners


def _field(text, label):
    values = re.findall(rf"^[ \t]*{re.escape(label)}:[ \t]*([^\r\n]+)", text, re.M)
    if len(values) != 1:
        raise Form4PersonBridgeError("Dated SEC header has missing/ambiguous identity fields")
    return values[0].strip()


def _packet(capture):
    text = capture.body.decode("utf-8", errors="strict")
    headers = re.findall(r"<SEC-HEADER>(.*?)</SEC-HEADER>", text, re.S)
    if len(headers) != 1:
        raise Form4PersonBridgeError("Dated person evidence requires one original SEC header")
    header = headers[0]
    accession = _field(header, "ACCESSION NUMBER")
    if (not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", accession)
            or accession.replace("-", "") != _accession_key(capture.source_url)
            or not capture.source_url.endswith("/" + accession + ".txt")):
        raise Form4PersonBridgeError("Person evidence header/accession URL conflict")
    form = _field(header, "CONFORMED SUBMISSION TYPE")
    if form not in {"3", "3/A", "4", "4/A", "5", "5/A"}:
        raise Form4PersonBridgeError("Person bridge supports original dated ownership packets only")
    filed_raw = _field(header, "FILED AS OF DATE")
    try:
        filed_day = datetime.strptime(filed_raw, "%Y%m%d").date()
    except ValueError as error:
        raise Form4PersonBridgeError("Dated person source has an invalid filing calendar day") from error
    accepted = re.findall(r"<ACCEPTANCE-DATETIME>([0-9]{14})", header)
    if len(accepted) != 1:
        raise Form4PersonBridgeError("Dated person source lacks one raw acceptance clock")
    try:
        raw_acceptance = datetime.strptime(accepted[0], "%Y%m%d%H%M%S")
    except ValueError as error:
        raise Form4PersonBridgeError("Dated person source has an invalid raw acceptance clock") from error
    sections = re.split(r"(?m)^\s*(REPORTING-OWNER|ISSUER|SUBJECT COMPANY|FILER):\s*\n", header)
    header_owners, header_issuers = [], []
    for offset in range(1, len(sections), 2):
        kind, section = sections[offset:offset+2]
        if kind not in {"REPORTING-OWNER", "ISSUER"}:
            continue
        row = {"cik": _cik(_field(section, "CENTRAL INDEX KEY")),
               "name": _name(_field(section, "COMPANY CONFORMED NAME"))}
        (header_owners if kind == "REPORTING-OWNER" else header_issuers).append(row)
    embedded = re.findall(r"<XML>\s*(.*?)\s*</XML>", text, re.S)
    if len(embedded) != 1:
        raise Form4PersonBridgeError("Dated person packet requires one unambiguous ownership XML")
    xml = _xml(embedded[0].encode())
    xml_owners = _xml_owners(xml)
    if (len(header_owners) != len(xml_owners) or
            {(row["cik"], _name_key(row["name"])) for row in header_owners} !=
            {(row["cik"], _name_key(row["name"])) for row in xml_owners}):
        raise Form4PersonBridgeError("Dated person header/XML reporting-owner conflict")
    issuer = {"cik": _cik(_one(xml, "issuer/issuerCik")), "name": _name(_one(xml, "issuer/issuerName"))}
    if len(header_issuers) != 1 or header_issuers[0] != issuer or _one(xml, "documentType") != form:
        raise Form4PersonBridgeError("Dated person header/XML issuer or form conflict")
    try:
        event_day = date.fromisoformat(_one(xml, "periodOfReport"))
    except ValueError as error:
        raise Form4PersonBridgeError("Dated person packet lacks an exact report day") from error
    signatures = [{"signature_name_raw": node.findtext("signatureName", ""),
                   "signature_date_raw": node.findtext("signatureDate", "")} for node in xml.findall("ownerSignature")]
    return {"capture": capture.summary(), "scope_mode": "DATED_DISCLOSURE_POINT", "owners": xml_owners,
            "issuer_as_filed": issuer, "form": form, "accession": accession, "report_day": event_day.isoformat(),
            "filing_day": filed_day.isoformat(), "acceptance_datetime_unqualified_raw": accepted[0],
            "clock_conflicts": (["REPORT_DAY_AFTER_FILED_DAY"] if event_day > filed_day else []) +
                               (["ACCEPTANCE_DATE_DIFFERS_FROM_FILED_DAY"] if raw_acceptance.date() != filed_day else []),
            "acceptance_timezone_inferred": False, "published_at": None,
            "source_kind": "REPORTING_OWNER_ACCOUNT_KIND_UNRESOLVED", "natural_person_kind_proven": False,
            "legal_person_name_verified": False, "signatures": signatures,
            "signature_names_are_approved_aliases": False,
            "date_scope_extended_to_later_events": False,
            "identity_locators": ["SEC-HEADER/REPORTING-OWNER/COMPANY-DATA", "ownershipDocument/reportingOwner/reportingOwnerId"]}


def _profile(capture):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise Form4PersonBridgeError("Person profile contains duplicate JSON keys")
            result[key] = value
        return result
    profile = json.loads(capture.body, object_pairs_hook=unique)
    if not isinstance(profile, Mapping):
        raise Form4PersonBridgeError("Person profile must contain one SEC account object")
    owner = {"cik": _cik(profile.get("cik")), "name": _name(profile.get("name"))}
    if urlsplit(capture.source_url).path != f"/submissions/CIK{owner['cik']}.json":
        raise Form4PersonBridgeError("Person profile URL/CIK conflict")
    entity_type = profile.get("entityType")
    if entity_type is not None and (not isinstance(entity_type, str) or len(entity_type) > 100):
        raise Form4PersonBridgeError("Person profile entity type must be a bounded publisher label")
    return {"capture": capture.summary(), "scope_mode": "OBSERVED_SNAPSHOT", "owners": [owner],
            "snapshot_day": capture.observed_at.astimezone(timezone.utc).date().isoformat(),
            "publisher_entity_type": entity_type, "published_at": None,
            "source_kind": "SEC_ACCOUNT_ENTITY_TYPE_NOT_NATURAL_PERSON_PROOF", "natural_person_kind_proven": False,
            "legal_person_name_verified": False, "historical_effective_date_proven": False,
            "identity_locators": ["JSON:/cik", "JSON:/name", "JSON:/entityType"],
            "former_names_are_approved_aliases": False}


def review_form4_person_bridge(observation: Mapping, *, source_capture: SECPersonCapture,
                              evidence: Sequence[SECPersonCapture], knowledge_at: datetime,
                              scope_mode: str = "EVENT_DATE") -> dict:
    """Corroborate exact source accounts without granting person attribution.

    EVENT_DATE requires that capture's actual disclosed report day, or profile
    snapshot day, to match each requested row day. OBSERVED_SNAPSHOT exposes
    the narrow observed account link without manufacturing historical scope.
    """
    clock = _clock(knowledge_at)
    if (not isinstance(observation, Mapping) or observation.get("parser_version") != PARSER_VERSION
            or not isinstance(source_capture, SECPersonCapture) or source_capture.purpose != "FORM4_SOURCE"
            or not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes))
            or len(evidence) > MAX_EVIDENCE_CAPTURES or scope_mode not in {"EVENT_DATE", "OBSERVED_SNAPSHOT"}):
        raise Form4PersonBridgeError("Person bridge requires a bounded original Form4 observation and explicit scope")
    source_capture.validate()
    if _accession_key(source_capture.source_url) is None:
        raise Form4PersonBridgeError("Form4 source must be an original SEC accession document")
    if (source_capture.available_at > clock or source_capture.retention != "ORIGINAL_BYTES"
            or _has_sensitive_body(source_capture.body)):
        raise Form4PersonBridgeError("Form4 source body is not available for this knowledge cutoff")
    source_xml = _xml(source_capture.body)
    source_owners = _xml_owners(source_xml)
    owners, transactions = observation.get("reporting_owners"), observation.get("transactions")
    if (not isinstance(owners, list) or not 1 <= len(owners) <= MAX_SOURCE_OWNERS
            or not isinstance(transactions, list) or not 1 <= len(transactions) <= MAX_SOURCE_ROWS
            or len(owners) * len(transactions) > MAX_REVIEW_PAIRS):
        raise Form4PersonBridgeError("Person bridge requires bounded explicit owners and source rows")
    if (source_owners != [{"cik": _cik(row.get("cik")), "name": _name(row.get("name"))} for row in owners]
            or _one(source_xml, "issuer/issuerCik") != _cik(observation.get("issuer_cik"))
            or _one(source_xml, "periodOfReport") != observation.get("period_of_report")
            or _one(source_xml, "documentType") != observation.get("form")):
        raise Form4PersonBridgeError("Person bridge observation/original XML identity conflict")
    xml_rows = [row for table, tag in (
        ("nonDerivativeTable", "nonDerivativeTransaction"), ("derivativeTable", "derivativeTransaction"))
        for row in source_xml.findall(f"{table}/{tag}")]
    if len(xml_rows) != len(transactions):
        raise Form4PersonBridgeError("Person bridge source row count mismatch")
    if any(_one(row, "transactionDate/value") != item.get("transaction_date")
           for row, item in zip(xml_rows, transactions)):
        raise Form4PersonBridgeError("Person bridge source/observation transaction date conflict")
    parsed, unavailable = [], []
    ids, capture_keys = set(), set()
    for capture in evidence:
        if not isinstance(capture, SECPersonCapture) or capture.purpose not in {"OWNER_PROFILE", "DATED_OWNERSHIP_PACKET"}:
            raise Form4PersonBridgeError("Person bridge evidence purpose is missing or self-source")
        capture.validate()
        if capture.record_id in ids:
            raise Form4PersonBridgeError("Person bridge evidence IDs must be distinct")
        ids.add(capture.record_id)
        if capture.available_at > clock:
            unavailable.append({"record_id": capture.record_id, "status": "WITHHELD", "reasons": ["NOT_YET_KNOWN"], "capture": None})
            continue
        detected_sensitive = capture.retention == "ORIGINAL_BYTES" and _has_sensitive_body(capture.body)
        if capture.retention != "ORIGINAL_BYTES" or detected_sensitive:
            summary = capture.summary()
            if detected_sensitive:
                summary["retention"] = "WITHHELD_SENSITIVE_BODY"
            unavailable.append({"record_id": capture.record_id, "status": "WITHHELD",
                                "reasons": ["REQUIRED_ORIGINAL_BODY_UNAVAILABLE"], "capture": summary})
            continue
        if capture.sha256 == source_capture.sha256 or (
                _accession_key(capture.source_url) is not None and
                _accession_key(capture.source_url) == _accession_key(source_capture.source_url)):
            unavailable.append({"record_id": capture.record_id, "status": "WITHHELD", "reasons": ["SAME_FORM4_DISCLOSURE"], "capture": capture.summary()})
            continue
        capture_key = (capture.purpose, capture.sha256, capture.observed_at.isoformat())
        if capture_key in capture_keys:
            unavailable.append({"record_id": capture.record_id, "status": "WITHHELD",
                                "reasons": ["DUPLICATE_CAPTURE_VERSION"], "capture": capture.summary()})
            continue
        capture_keys.add(capture_key)
        parsed.append(_profile(capture) if capture.purpose == "OWNER_PROFILE" else _packet(capture))
    row_reviews = []
    for transaction in transactions:
        try:
            event = date.fromisoformat(transaction["transaction_date"])
        except (KeyError, TypeError, ValueError) as error:
            raise Form4PersonBridgeError("Person bridge row requires an exact transaction calendar day") from error
        for owner in owners:
            matches, conflicts = [], []
            for packet in parsed:
                same_cik = [row for row in packet["owners"] if row["cik"] == owner["cik"]]
                if same_cik and any(_name_key(row["name"]) != _name_key(owner["name"]) for row in same_cik):
                    conflicts.append(packet["capture"]["record_id"])
                    continue
                if not same_cik:
                    continue
                day = packet.get("snapshot_day") or packet.get("report_day")
                event_matches = not packet.get("clock_conflicts") and day == event.isoformat()
                scope_matches = not packet.get("clock_conflicts") and (
                    scope_mode == "OBSERVED_SNAPSHOT" or event_matches)
                matches.append({"record_id": packet["capture"]["record_id"], "source_url": packet["capture"]["source_url"],
                                "sha256": packet["capture"]["sha256"], "source_filer_name": same_cik[0]["name"],
                                "owner_cik": same_cik[0]["cik"], "scope_mode": packet["scope_mode"],
                                "source_scope_day": day, "requested_event_day_matches": event_matches,
                                "requested_scope_matches": scope_matches,
                                "clock_conflicts": packet.get("clock_conflicts", []),
                                "natural_person_kind_proven": False, "legal_person_name_verified": False})
            reasons = ["NATURAL_PERSON_KIND_AND_LEGAL_NAME_UNRESOLVED"]
            if not matches:
                reasons.append("NO_EXACT_INDEPENDENT_ACCOUNT_MATCH")
            elif not any(match["requested_scope_matches"] for match in matches):
                reasons.append("NO_DATED_BRIDGE_FOR_REQUESTED_EVENT")
            if conflicts:
                reasons.append("SAME_CIK_NAME_CONFLICT_ALIAS_UNRESOLVED")
            if owner["cik"] == observation["issuer_cik"]:
                reasons.append("OWNER_AND_ISSUER_CIK_PERSON_SCOPE_CONFLICT")
            row_reviews.append({"row_locator": transaction.get("row_locator"), "event_day": event.isoformat(),
                "as_filed_owner_cik": owner["cik"], "as_filed_owner_name": owner["name"],
                "status": "WITHHELD", "reasons": reasons, "exact_source_account_matches": matches,
                "conflicting_capture_ids": conflicts, "independent_account_source_corroborated": bool(matches) and not conflicts,
                "person_identity_independently_verified": False, "instrument_identity_independently_verified": False,
                "role_text_is_person_kind_or_identity_proof": False, "issuer_attribution_allowed": False})
    return {"bridge_version": BRIDGE_VERSION, "knowledge_at": clock.isoformat(), "scope_mode": scope_mode,
            "status": "PARTIAL", "source_capture": source_capture.summary(),
            "independent_evidence": parsed, "unavailable_evidence": unavailable, "row_reviews": row_reviews,
            "source_owner_count": len(owners), "source_row_count": len(transactions),
            "independent_dated_disclosure_count": len({row["accession"] for row in parsed if row["scope_mode"] == "DATED_DISCLOSURE_POINT"}),
            "person_verified_row_count": 0, "source_only_account_corroborated_rows": sum(row["independent_account_source_corroborated"] for row in row_reviews),
            "issuer_identity_from_reporting_owner_allowed": False, "instrument_identity_from_person_allowed": False,
            "role_or_signature_aliases_approved": False, "historical_disclosure_capture_is_historical_knowledge": False,
            "registry_modified": False, "scoring_applied": False, "trading_allowed": False,
            "network_requests_performed": 0, "quota_or_lease_modified": False,
            "limits": ["This bridge corroborates exact SEC account CIK/name from independent original sources, not a natural person's legal identity",
                       "A current SEC profile is one observed snapshot and cannot establish an older event-date identity",
                       "A dated disclosure anchors its own reported date; no interval is extended between it and a later snapshot",
                       "Attorneys, role text, individual/group filing scope and signer spellings do not supply entity kind or approved aliases",
                       "All members and directory variants of one accession are one disclosure, not independent evidence"]}


FORM144_REVIEW_VERSION = "sec-form144-original-account-notice-v1"
_FORM144_NAMESPACE = "http://www.sec.gov/edgar/ownership"


def _physical_clock():
    return datetime.now(timezone.utc)


def _notice_day(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{2}/[0-9]{2}/[0-9]{4}", value):
        raise Form4PersonBridgeError("Form144 notice requires an exact literal US calendar day")
    try:
        return datetime.strptime(value, "%m/%d/%Y").date()
    except ValueError as error:
        raise Form4PersonBridgeError("Form144 notice calendar day is invalid") from error


def _quantity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,18}(?:\.[0-9]{1,12})?", value):
        raise Form4PersonBridgeError("Notice comparison requires bounded nonnegative literal quantities")
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise Form4PersonBridgeError("Notice comparison quantity is invalid") from error


def _notice_proof(body, tag, *, container=None):
    """Locate original leaf bytes, never a reserialized XML replacement."""
    first, last = 0, len(body)
    if container is not None:
        scopes = list(re.finditer(rb"<" + container.encode() + rb">[\s\S]*?</" + container.encode() + rb">", body))
        if len(scopes) != 1:
            raise Form4PersonBridgeError("Form144 quoted context is absent or ambiguous")
        first, last = scopes[0].span()
    matches = list(re.finditer(rb"<" + tag.encode() + rb">[^<>]*</" + tag.encode() + rb">", body[first:last]))
    if len(matches) != 1:
        raise Form4PersonBridgeError("Form144 quoted field is absent or ambiguous")
    start, end = first + matches[0].start(), first + matches[0].end()
    raw = body[start:end]
    return {"byte_start": start, "byte_end": end, "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(), "quote": raw.decode("utf-8")}


def _notice_whole_body_withheld(body):
    if _has_sensitive_body(body):
        return True
    # EDGAR's public filerCredentials contains an account CIK and a masked CCC.
    # Only the all-X publisher placeholder is admitted. No CCC value is emitted.
    ccc = re.findall(rb"<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?ccc\b[^>]*>(.*?)</(?:[A-Za-z_][A-Za-z0-9_.-]*:)?ccc\s*>", body, re.S | re.I)
    return any(re.fullmatch(rb"[Xx]{8}", value.strip()) is None for value in ccc)


def _parse_form144_notice(capture, selection):
    """The supported notice scope is one whole packet and one original XML."""
    body = capture.body
    if b"\x00" in body or re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", body, re.I):
        raise Form4PersonBridgeError("Form144 original cannot contain NUL or entity declarations")
    text = body.decode("utf-8", errors="strict")
    # Full-match consumes the envelope, header, sole document and closing line;
    # a copied XML excerpt, hidden second document or trailing data is rejected.
    packet = re.fullmatch(
        r"<SEC-DOCUMENT>([0-9]{10}-[0-9]{2}-[0-9]{6})\.txt : ([0-9]{8})\r?\n"
        r"<SEC-HEADER>\1\.hdr\.sgml : \2\r?\n(?P<header>[\s\S]*?)</SEC-HEADER>\r?\n"
        r"[ \t\r\n]*<DOCUMENT>\r?\n<TYPE>144\r?\n<SEQUENCE>1\r?\n"
        r"<FILENAME>(?P<filename>[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\.xml)\r?\n"
        r"<TEXT>\r?\n<XML>\r?\n(?P<xml>[\s\S]*?)</XML>\r?\n</TEXT>\r?\n</DOCUMENT>\r?\n"
        r"</SEC-DOCUMENT>[\r\n]*", text)
    if not packet or any(text.count(token) != 1 for token in ("<DOCUMENT>", "</DOCUMENT>", "<XML>", "</XML>")):
        raise Form4PersonBridgeError("Form144 requires a complete single-document original packet")
    accession = selection["notice_accession"]
    if (packet[1] != accession or packet["filename"] != selection["notice_primary_filename"]
            or capture.source_url != f"https://www.sec.gov/Archives/edgar/data/{int(selection['issuer_cik'])}/{accession.replace('-', '')}/{accession}.txt"):
        raise Form4PersonBridgeError("Form144 packet envelope/document/URL selection conflicts")
    header = packet["header"]
    sections = re.split(r"(?m)^[ \t]*(SUBJECT COMPANY|REPORTING-OWNER|ISSUER|FILER):[ \t]*\r?\n", header)
    top = sections[0]
    if (len(sections) != 5 or sections[1::2] != ["SUBJECT COMPANY", "REPORTING-OWNER"]
            or _field(top, "ACCESSION NUMBER") != accession
            or _field(top, "CONFORMED SUBMISSION TYPE") != "144"
            or _field(top, "PUBLIC DOCUMENT COUNT") != "1"):
        raise Form4PersonBridgeError("Form144 original header account roles/form/count conflict")
    fields = {key: _field(top, key) for key in ("FILED AS OF DATE", "DATE AS OF CHANGE")}
    if any(not re.fullmatch(r"[0-9]{8}", value) for value in fields.values()):
        raise Form4PersonBridgeError("Form144 original header dates are malformed")
    filed, changed = (datetime.strptime(fields[key], "%Y%m%d").date() for key in ("FILED AS OF DATE", "DATE AS OF CHANGE"))
    if packet[2] != fields["FILED AS OF DATE"]:
        raise Form4PersonBridgeError("Form144 envelope date differs from original filed date")
    accepts = re.findall(r"(?m)^<ACCEPTANCE-DATETIME>([0-9]{14})\r?$", top)
    if len(accepts) != 1 or header.count("<ACCEPTANCE-DATETIME>") != 1:
        raise Form4PersonBridgeError("Form144 needs one unqualified original acceptance clock")
    accepted = datetime.strptime(accepts[0], "%Y%m%d%H%M%S")
    issuer = {"cik": _cik(_field(sections[2], "CENTRAL INDEX KEY")), "name": _name(_field(sections[2], "COMPANY CONFORMED NAME"))}
    owner = {"cik": _cik(_field(sections[4], "CENTRAL INDEX KEY")), "name": _name(_field(sections[4], "COMPANY CONFORMED NAME"))}
    if issuer != {"cik": selection["issuer_cik"], "name": selection["issuer_header_name"]} or owner != {"cik": selection["owner_cik"], "name": selection["owner_header_name"]}:
        raise Form4PersonBridgeError("Form144 selected header issuer or reporting account conflicts")
    for section in (sections[2], sections[4]):
        if _field(section, "FORM TYPE") != "144":
            raise Form4PersonBridgeError("Form144 header account filing form conflicts")
    try:
        root = ElementTree.fromstring(packet["xml"])
    except ElementTree.ParseError as error:
        raise Form4PersonBridgeError("Form144 original XML is incomplete") from error
    ns = "{" + _FORM144_NAMESPACE + "}"
    if root.tag != ns + "edgarSubmission" or sum(1 for _ in root.iter()) > 10_000:
        raise Form4PersonBridgeError("Form144 XML namespace/scope is unsupported")
    def scalar(path):
        return _one(root, "/".join(ns + part for part in path.split("/")))
    if (scalar("headerData/submissionType") != "144" or scalar("headerData/filerInfo/liveTestFlag") != "LIVE"
            or _cik(scalar("headerData/filerInfo/filer/filerCredentials/cik")) != owner["cik"]
            or _cik(scalar("formData/issuerInfo/issuerCik")) != issuer["cik"]):
        raise Form4PersonBridgeError("Form144 XML filer/issuer CIK or form conflicts with original header")
    placeholder = scalar("headerData/filerInfo/filer/filerCredentials/ccc")
    if not re.fullmatch(r"[Xx]{8}", placeholder):
        raise Form4PersonBridgeError("Form144 whole access-bearing original must be withheld")
    # This is a publisher field label, not proof that the seller is an individual.
    seller = _name(scalar("formData/issuerInfo/nameOfPersonForWhoseAccountTheSecuritiesAreToBeSold"))
    relations = root.findall(ns + "formData/" + ns + "issuerInfo/" + ns + "relationshipsToIssuer/" + ns + "relationshipToIssuer")
    if not 1 <= len(relations) <= 8 or any(not node.text or len(node.text) > 100 for node in relations):
        raise Form4PersonBridgeError("Form144 relationships must be explicit bounded source labels")
    scalar_paths = {
        "issuer_name_xml_raw": "formData/issuerInfo/issuerName",
        "proposed_security_title_raw": "formData/securitiesInformation/securitiesClassTitle",
        "proposed_units_raw": "formData/securitiesInformation/noOfUnitsSold",
        "approx_sale_day_raw": "formData/securitiesInformation/approxSaleDate",
        "acquired_security_title_raw": "formData/securitiesToBeSold/securitiesClassTitle",
        "acquired_day_raw": "formData/securitiesToBeSold/acquiredDate",
        "acquired_units_raw": "formData/securitiesToBeSold/amountOfSecuritiesAcquired",
        "acquisition_nature_raw": "formData/securitiesToBeSold/natureOfAcquisitionTransaction",
        "acquired_from_raw": "formData/securitiesToBeSold/nameOfPersonfromWhomAcquired",
        "notice_day_raw": "formData/noticeSignature/noticeDate",
        "signature_raw": "formData/noticeSignature/signature",
    }
    values = {key: scalar(path) for key, path in scalar_paths.items()}
    if any(len(value) > 500 for value in values.values()):
        raise Form4PersonBridgeError("Form144 claim fields exceed bounded review scope")
    for key in ("proposed_units_raw", "acquired_units_raw"):
        _quantity(values[key])
    days = {key.removesuffix("_raw"): _notice_day(values[key]).isoformat()
            for key in ("approx_sale_day_raw", "acquired_day_raw", "notice_day_raw")}
    plan_nodes = root.findall(ns + "formData/" + ns + "noticeSignature/" + ns + "planAdoptionDates/" + ns + "planAdoptionDate")
    if len(plan_nodes) > 16:
        raise Form4PersonBridgeError("Form144 plan date scope is unbounded")
    plans = [{"literal": node.text, "day": _notice_day(node.text).isoformat()} for node in plan_nodes]
    upper = _clock(capture.observed_at).date()
    if max(filed, changed, accepted.date(), date.fromisoformat(days["acquired_day"]),
           date.fromisoformat(days["notice_day"]), *[date.fromisoformat(p["day"]) for p in plans]) > upper:
        raise Form4PersonBridgeError("Form144 disclosed dates exceed original physical capture day")
    conflicts = (["ACCEPTANCE_DAY_DIFFERS_FROM_FILED_DAY"] if accepted.date() != filed else [])
    conflicts += (["CHANGE_DAY_BEFORE_FILED_DAY"] if changed < filed else [])
    if date.fromisoformat(days["notice_day"]) > filed:
        conflicts.append("NOTICE_DAY_AFTER_FILED_DAY")
    if date.fromisoformat(days["acquired_day"]) > date.fromisoformat(days["notice_day"]):
        conflicts.append("ACQUISITION_DAY_AFTER_NOTICE_DAY")
    if date.fromisoformat(days["approx_sale_day"]) < date.fromisoformat(days["acquired_day"]):
        conflicts.append("APPROX_SALE_DAY_BEFORE_ACQUISITION_DAY")
    if any(date.fromisoformat(p["day"]) > date.fromisoformat(days["notice_day"]) for p in plans):
        conflicts.append("PLAN_DAY_AFTER_NOTICE_DAY")
    xml_start = len(text[:packet.start("xml")].encode("utf-8"))
    xml_end = len(text[:packet.end("xml")].encode("utf-8"))
    proof_tags = {
        "seller_name": ("nameOfPersonForWhoseAccountTheSecuritiesAreToBeSold", None),
        "relationships": ("relationshipsToIssuer", None),
        "acquired_title": ("securitiesClassTitle", "securitiesToBeSold"),
        "acquired_quantity": ("amountOfSecuritiesAcquired", "securitiesToBeSold"),
        "acquired_day": ("acquiredDate", "securitiesToBeSold"),
        "acquisition_nature": ("natureOfAcquisitionTransaction", "securitiesToBeSold"),
        "proposed_title": ("securitiesClassTitle", "securitiesInformation"),
        "proposed_quantity": ("noOfUnitsSold", "securitiesInformation"),
        "approx_sale_day": ("approxSaleDate", "securitiesInformation"),
        "notice_day": ("noticeDate", "noticeSignature"), "signature": ("signature", "noticeSignature"),
    }
    proofs = {key: _notice_proof(body, tag, container=container) for key, (tag, container) in proof_tags.items() if key != "relationships"}
    # Relationship context includes child elements; preserve its whole literal span.
    relation_matches = list(re.finditer(br"<relationshipsToIssuer>[\s\S]*?</relationshipsToIssuer>", body))
    if len(relation_matches) != 1:
        raise Form4PersonBridgeError("Form144 relationship quote context is ambiguous")
    a, z = relation_matches[0].span(); raw = body[a:z]
    proofs["relationships"] = {"byte_start": a, "byte_end": z, "bytes": z-a, "sha256": hashlib.sha256(raw).hexdigest(), "quote": raw.decode("utf-8")}
    return {"accession": accession, "form": "144", "whole_original_consumed": True,
            "document_filename": packet["filename"], "document_sequence_raw": "1",
            "xml_original_span": {"byte_start": xml_start, "byte_end": xml_end, "bytes": xml_end-xml_start,
                                  "sha256": hashlib.sha256(body[xml_start:xml_end]).hexdigest()},
            "reporting_account_as_filed": owner, "subject_company_as_filed": issuer,
            "seller_name_as_filed": seller, "relationships_as_filed": [node.text for node in relations],
            "filer_ccc_classification": "PUBLISHED_PLACEHOLDER_ONLY_VALUE_NOT_EXPORTED",
            "filed_day": filed.isoformat(), "change_day": changed.isoformat(),
            "acceptance_datetime_unqualified_raw": accepts[0], "acceptance_timezone": "UNKNOWN",
            "acceptance_timezone_inferred": False, "published_at": None,
            **values, **days, "plan_adoption_days": plans,
            "approx_sale_day_is_proposed_not_execution": True,
            "approx_sale_day_after_capture": date.fromisoformat(days["approx_sale_day"]) > upper,
            "clock_conflicts": conflicts, "original_field_proofs": proofs}


def review_form144_account_notice(*, notice_capture: SECPersonCapture,
                                 account_packet_capture: SECPersonCapture,
                                 selection: dict, knowledge_at: datetime | None = None) -> dict:
    """Compare a separate original Form144 notice with original Form4 facts.

    This verifies only the scope of independently filed account assertions.
    Even exact title/day/quantity agreement would not prove trade execution,
    natural-person account binding, a legal alias or a continuous legal role.
    """
    physical = _clock(_physical_clock())
    cutoff = physical if knowledge_at is None else _clock(knowledge_at)
    if cutoff > physical:
        raise Form4PersonBridgeError("Future Form144 interpretation knowledge is unavailable")
    report = {"review_version": FORM144_REVIEW_VERSION, "status": "PARTIAL",
              "requested_knowledge_at": None if knowledge_at is None else cutoff.isoformat(),
              "review_ingested_at": physical.isoformat(), "source_account_corroborated": False,
              "source_issuer_cik_corroborated": False, "acquisition_day_overlap": False,
              "same_transaction_supported": False, "execution_verified": False,
              "natural_person_identity_verified": False, "person_cik_binding_verified": False,
              "approved_name_alias": False, "instrument_identity_verified": False,
              "legal_role_at_transaction_verified": False, "role_continuity_verified": False,
              "issuer_attribution_allowed": False, "historical_machine_availability_inferred": False,
              "registry_modified": False, "scoring_applied": False, "trading_allowed": False,
              "network_requests_performed": 0, "quota_or_lease_modified": False, "gaps": []}
    def finish():
        end = _clock(_physical_clock())
        if end < physical:
            raise Form4PersonBridgeError("Physical Form144 interpretation clock rolled back")
        if knowledge_at is not None and end > cutoff:
            for key in ("notice", "account_packet", "transaction_comparisons", "source_inputs_available_at"):
                report.pop(key, None)
            for key in ("source_account_corroborated", "source_issuer_cik_corroborated", "acquisition_day_overlap"):
                report[key] = False
            report["gaps"] = ["AFTER_PHYSICAL_REVIEW_COMPLETION_KNOWLEDGE_CUTOFF"]
        report["review_available_at"] = end.isoformat()
        return report
    if cutoff < physical:
        report["gaps"] = ["AFTER_PHYSICAL_REVIEW_INGESTION_KNOWLEDGE_CUTOFF"]
        return finish()
    for capture, purpose in ((notice_capture, "FORM144_NOTICE_PACKET"), (account_packet_capture, "DATED_OWNERSHIP_PACKET")):
        if not isinstance(capture, SECPersonCapture) or capture.purpose != purpose:
            raise Form4PersonBridgeError("Form144 review requires separate original notice and ownership packets")
        capture.validate()
    if notice_capture.record_id == account_packet_capture.record_id:
        raise Form4PersonBridgeError("Form144 and Form4 capture IDs must be distinct")
    if any(capture.available_at > cutoff for capture in (notice_capture, account_packet_capture)):
        report["gaps"] = ["REQUIRED_ORIGINAL_NOT_YET_KNOWN"]
        return finish()
    summaries = {"notice": notice_capture.summary(), "account_packet": account_packet_capture.summary()}
    unavailable = False
    for key, capture in (("notice", notice_capture), ("account_packet", account_packet_capture)):
        sensitive = capture.retention == "ORIGINAL_BYTES" and _notice_whole_body_withheld(capture.body)
        if sensitive:
            summaries[key]["retention"] = "WITHHELD_SENSITIVE_BODY"
        unavailable |= capture.retention != "ORIGINAL_BYTES" or sensitive
    report["captures"] = summaries
    if unavailable:
        report["gaps"] = ["REQUIRED_WHOLE_ORIGINAL_WITHHELD_OR_MISSING_NO_SUBSTITUTE"]
        return finish()
    keys = {"notice_accession", "notice_primary_filename", "account_accession", "account_primary_filename",
            "issuer_cik", "issuer_header_name", "owner_cik", "owner_header_name", "transaction_day"}
    if not isinstance(selection, dict) or set(selection) != keys:
        raise Form4PersonBridgeError("Form144 selection requires exact bounded original account/document fields")
    for key in ("notice_accession", "account_accession"):
        if not isinstance(selection[key], str) or not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", selection[key]):
            raise Form4PersonBridgeError("Form144 selection has an invalid accession")
    for key in ("notice_primary_filename", "account_primary_filename"):
        if not isinstance(selection[key], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,195}\.xml", selection[key]):
            raise Form4PersonBridgeError("Form144 selection has an invalid original filename")
    for key in ("issuer_cik", "owner_cik"):
        _cik(selection[key])
    for key in ("issuer_header_name", "owner_header_name"):
        _name(selection[key])
    try:
        requested = date.fromisoformat(selection["transaction_day"])
    except (TypeError, ValueError) as error:
        raise Form4PersonBridgeError("Form144 comparison needs an exact requested transaction day") from error
    if selection["transaction_day"] != requested.isoformat() or selection["owner_cik"] == selection["issuer_cik"]:
        raise Form4PersonBridgeError("Form144 comparison needs distinct exact account/issuer scope")
    if selection["notice_accession"] == selection["account_accession"] or notice_capture.sha256 == account_packet_capture.sha256:
        raise Form4PersonBridgeError("A Form144 notice and Form4 must be separate original disclosures")
    expected_url = f"https://www.sec.gov/Archives/edgar/data/{int(selection['issuer_cik'])}/{selection['account_accession'].replace('-', '')}/{selection['account_accession']}.txt"
    if account_packet_capture.source_url != expected_url:
        raise Form4PersonBridgeError("Form4 complete-packet URL and selected issuer/accession conflict")
    notice = _parse_form144_notice(notice_capture, selection)
    # Import at call time: complete packet parsing reuses the original bridge's
    # strict XML/header helpers, without making either module import cyclically.
    from market_checker_app.services.sec_complete_packet_review import parse_complete_sec_packet
    packet = parse_complete_sec_packet(account_packet_capture.body, expected_accession=selection["account_accession"])
    header = packet["header"]
    issuer = {"cik": selection["issuer_cik"], "name": selection["issuer_header_name"]}
    owner = {"cik": selection["owner_cik"], "name": selection["owner_header_name"]}
    if (header["submission_type"] != "4"
            or [{"cik": x["cik"], "name": x["name"]} for x in header["account_roles"] if x["role"] == "ISSUER"] != [issuer]
            or [{"cik": x["cik"], "name": x["name"]} for x in header["account_roles"] if x["role"] == "REPORTING-OWNER"] != [owner]):
        raise Form4PersonBridgeError("Original Form4 header selected account/issuer/form conflicts")
    documents = [x for x in packet["documents"] if x["document_type"] == "4"]
    if len(documents) != 1 or documents[0]["filename"] != selection["account_primary_filename"]:
        raise Form4PersonBridgeError("Form4 original primary document identity is absent or ambiguous")
    span = documents[0]["ownership_xml_span"]
    xml = _xml(account_packet_capture.body[span["byte_start"]:span["byte_end"]])
    if (_xml_owners(xml) != [owner] or {"cik": _one(xml, "issuer/issuerCik"), "name": _one(xml, "issuer/issuerName")} != issuer
            or _one(xml, "documentType") != "4" or _one(xml, "periodOfReport") != header["period_day"]):
        raise Form4PersonBridgeError("Form4 original XML and header account/date/form conflicts")
    if max(date.fromisoformat(header[key]) for key in ("filed_day", "period_day", "change_day")) > _clock(account_packet_capture.observed_at).date():
        raise Form4PersonBridgeError("Form4 original header dates exceed physical source capture")
    rows = []
    for table in ("nonDerivativeTable", "derivativeTable"):
        for at, node in enumerate(xml.findall(table + "/" + ("nonDerivativeTransaction" if table == "nonDerivativeTable" else "derivativeTransaction")), 1):
            day = date.fromisoformat(_one(node, "transactionDate/value"))
            if day > date.fromisoformat(header["filed_day"]):
                raise Form4PersonBridgeError("Form4 transaction day exceeds filing date")
            if day != requested:
                continue
            quantity, title = _one(node, "transactionAmounts/transactionShares/value"), _one(node, "securityTitle/value")
            rows.append({"row_locator": f"{table}/{node.tag}[{at}]", "transaction_day": day.isoformat(),
                         "transaction_code_raw": _one(node, "transactionCoding/transactionCode"),
                         "acquired_disposed_raw": _one(node, "transactionAmounts/transactionAcquiredDisposedCode/value"),
                         "quantity_raw": quantity, "security_title_raw": title,
                         "notice_acquired_day_equal": notice["acquired_day"] == day.isoformat(),
                         "notice_acquired_quantity_equal": _quantity(quantity) == _quantity(notice["acquired_units_raw"]),
                         "notice_security_title_literal_equal": title == notice["acquired_security_title_raw"],
                         "same_transaction_supported": False})
    if not rows:
        raise Form4PersonBridgeError("Requested transaction day has no original Form4 transaction rows")
    conflicts = notice["clock_conflicts"] + header["clock_conflicts"]
    if conflicts:
        report["gaps"] = ["ORIGINAL_DISCLOSURE_CLOCK_CONFLICT", *conflicts]
    supported = not conflicts
    report.update(source_account_corroborated=supported, source_issuer_cik_corroborated=supported,
                  acquisition_day_overlap=supported and notice["acquired_day"] == requested.isoformat(),
                  notice=notice, account_packet={"accession": selection["account_accession"], "header": header,
                      "primary_xml_original_span": span, "owner": owner, "issuer": issuer,
                      "whole_original_consumed": True}, transaction_comparisons=rows,
                  source_inputs_available_at=max(x.available_at for x in (notice_capture, account_packet_capture)).isoformat(),
                  independent_disclosure_count=2, sale_notice_is_trade_execution=False,
                  live_test_flag_is_collector_live_acceptance=False, issuer_xml_name_variant_is_approved_alias=False,
                  seller_name_order_is_approved_account_alias=False)
    report["gaps"].extend(["NATURAL_PERSON_ACCOUNT_LEGAL_ALIAS_BINDING_UNVERIFIED",
                           "ROLE_LABELS_DO_NOT_ESTABLISH_CONTINUOUS_LEGAL_ROLE_AT_TRANSACTION",
                           "COMMON_TITLE_IS_NOT_COMMON_STOCK_CLASS_OR_CUSIP_BINDING",
                           "PROPOSED_SALE_NOTICE_IS_NOT_EXECUTION_OR_TRANSACTION_IDENTITY"])
    if any(not row["notice_acquired_quantity_equal"] for row in rows):
        report["gaps"].append("NOTICE_AND_FORM4_TRANSACTION_QUANTITIES_DIFFER")
    if any(not row["notice_security_title_literal_equal"] for row in rows):
        report["gaps"].append("NOTICE_AND_FORM4_SECURITY_TITLES_DIFFER")
    return finish()
