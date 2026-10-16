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
                or self.purpose not in {"FORM4_SOURCE", "OWNER_PROFILE", "DATED_OWNERSHIP_PACKET"}
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
        if not isinstance(capture, SECPersonCapture) or capture.purpose == "FORM4_SOURCE":
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
