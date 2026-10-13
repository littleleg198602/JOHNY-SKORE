"""Read-only exact Form 4 identity review; source observations remain as filed.

CIKs, director/officer checkboxes, security titles and footnotes are not person
or instrument verification. Supplied independent captures are reproducible
review candidates only: this service never approves a registry identity.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import posixpath
import re
from urllib.parse import unquote, urlsplit

from market_checker_app.services.sec_form4_service import Form4ValidationError, PARSER_VERSION


IDENTITY_REVIEW_VERSION = "sec-form4-exact-identity-review-v1"
MAX_IDENTITY_CAPTURES = 64
MAX_CAPTURE_BYTES = 2_000_000
MAX_TOTAL_CAPTURE_BYTES = 16_000_000
MAX_REVIEW_PAIRS = 4_096
_KINDS = {"OWNER_CIK_PROFILE", "OWNER_PERSON_IDENTITY", "INSTRUMENT_IDENTITY"}


def _aware(value: object) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    except ValueError as error:
        raise Form4ValidationError("Form 4 identity review has an invalid knowledge clock") from error
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise Form4ValidationError("Form 4 identity review requires a timezone")
    return result.astimezone(timezone.utc)


def _text(value: object, *, maximum: int = 8_000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise Form4ValidationError("Form 4 identity review needs bounded nonempty text")
    return value.strip()


def _cik(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{10}", value) or int(value) == 0:
        raise Form4ValidationError("Form 4 identity review requires an exact ten-digit CIK")
    return value


def _key(value: str) -> str:
    # Only case/whitespace differ. No name order, suffix, class or fuzzy aliases.
    return " ".join(value.split()).casefold()


def _valid_identifier(scheme: str, value: str) -> bool:
    if scheme == "CUSIP":
        if not re.fullmatch(r"[A-Z0-9]{8}[0-9]", value):
            return False
        total = 0
        for index, character in enumerate(value[:8], start=1):
            number = int(character) if character.isdigit() else ord(character) - ord("A") + 10
            number *= 2 if index % 2 == 0 else 1
            total += number // 10 + number % 10
        return (10 - total % 10) % 10 == int(value[-1])
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", value):
        return False
    digits = "".join(character if character.isdigit() else str(ord(character) - ord("A") + 10)
                     for character in value)
    total = 0
    for index, character in enumerate(reversed(digits)):
        number = int(character) * (2 if index % 2 else 1)
        total += number // 10 + number % 10
    return total % 10 == 0


def _url(value: object) -> str:
    url = _text(value, maximum=2_000)
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in {"www.sec.gov", "data.sec.gov"}
            or parsed.username or parsed.password or parsed.port not in {None, 443}):
        raise Form4ValidationError("Form 4 identity evidence needs a public SEC HTTPS reference")
    path = posixpath.normpath(unquote(parsed.path))
    prefixes = ("/Archives/edgar/data/",) if parsed.hostname == "www.sec.gov" else ("/submissions/",)
    if not path.startswith(prefixes):
        raise Form4ValidationError("Form 4 identity evidence references an unsupported SEC endpoint")
    return url


def _disclosure_key(url: str) -> tuple[str, str] | None:
    # Raw XML, XSL, index, submission text, exhibits, query/fragment variants
    # within one accession are the same disclosure, never independent evidence.
    path = posixpath.normpath(unquote(urlsplit(url).path))
    match = re.match(r"^/Archives/edgar/data/([0-9]+)/([0-9]{18})/", path)
    return (str(int(match[1])), match[2]) if match else None


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
        self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _validated_capture(raw: object, documents: Mapping[str, bytes]) -> dict[str, object]:
    if not isinstance(raw, Mapping) or raw.get("kind") not in _KINDS:
        raise Form4ValidationError("Form 4 identity evidence requires an explicit supported purpose")
    row = {key: _text(raw.get(key)) for key in ("record_id", "kind", "quote")}
    row["source_url"] = _url(raw.get("source_url"))
    digest = raw.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise Form4ValidationError("Form 4 identity capture hash is missing or malformed")
    body = documents.get(digest)
    if (not isinstance(body, bytes) or len(body) > MAX_CAPTURE_BYTES
            or hashlib.sha256(body).hexdigest() != digest):
        raise Form4ValidationError("Form 4 identity capture bytes/hash conflict")
    row["sha256"] = digest
    observed = _aware(raw.get("observed_at"))
    known = _aware(raw.get("known_at"))
    if known < observed:
        raise Form4ValidationError("Form 4 identity evidence cannot be known before capture")
    row["observed_at"], row["known_at"] = observed.isoformat(), known.isoformat()
    published = raw.get("published_at")
    if published is not None:
        published = _aware(published)
    row["published_at"] = published.isoformat() if published is not None else None
    row["publication_clock_conflict"] = published is not None and published > observed
    try:
        start = date.fromisoformat(str(raw.get("effective_from")))
        end = date.fromisoformat(str(raw.get("effective_to")))
    except ValueError as error:
        raise Form4ValidationError("Form 4 identity candidate requires a bounded declared date interval") from error
    if start >= end:
        raise Form4ValidationError("Form 4 identity candidate has an empty/reversed date interval")
    row["effective_from"], row["effective_to"] = start.isoformat(), end.isoformat()
    text = body.decode("utf-8", errors="strict")
    if row["kind"] == "OWNER_CIK_PROFILE":
        owner = json.loads(text)
        row["owner_cik"], row["owner_name"] = _cik(raw.get("owner_cik")), _text(raw.get("owner_name"))
        expected_url = f"/submissions/CIK{row['owner_cik']}.json"
        if (urlsplit(str(row["source_url"])).hostname != "data.sec.gov"
                or urlsplit(str(row["source_url"])).path != expected_url
                or not isinstance(owner, Mapping) or str(owner.get("cik")) != row["owner_cik"]
                or _key(str(owner.get("name", ""))) != _key(str(row["owner_name"]))):
            raise Form4ValidationError("Form 4 owner profile CIK/name/source conflict")
        row["publisher_entity_type"] = owner.get("entityType")
        visible = text
    else:
        parser = _VisibleText()
        parser.feed(text)
        visible = " ".join(parser.parts)
        if row["kind"] == "OWNER_PERSON_IDENTITY":
            row["owner_cik"], row["owner_name"] = _cik(raw.get("owner_cik")), _text(raw.get("owner_name"))
            if any(_key(str(row[key])) not in _key(str(row["quote"])) for key in ("owner_cik", "owner_name")):
                raise Form4ValidationError("Form 4 person evidence quote lacks exact name/CIK")
        else:
            row["issuer_cik"] = _cik(raw.get("issuer_cik"))
            row["security_title"] = _text(raw.get("security_title"))
            row["ticker"] = _text(raw.get("ticker"), maximum=30)
            if type(raw.get("derivative")) is not bool:
                raise Form4ValidationError("Form 4 instrument candidate requires explicit derivative type")
            row["derivative"] = raw["derivative"]
            row["instrument_identifier"] = raw.get("instrument_identifier")
            if row["instrument_identifier"] is not None:
                identifier = row["instrument_identifier"]
                if (not isinstance(identifier, Mapping) or set(identifier) != {"scheme", "value"}
                        or identifier.get("scheme") not in {"CUSIP", "ISIN"}
                        or not isinstance(identifier.get("value"), str)
                        or not _valid_identifier(identifier["scheme"], identifier["value"])
                        or _key(identifier["value"]) not in _key(str(row["quote"]))):
                    raise Form4ValidationError("Form 4 instrument evidence lacks exact quoted identifier")
                row["instrument_identifier"] = dict(identifier)
            if (not urlsplit(str(row["source_url"])).path.startswith(
                    f"/Archives/edgar/data/{int(str(row['issuer_cik']))}/")
                    or _key(str(row["security_title"])) not in _key(str(row["quote"]))
                    or not re.search(rf"(?<![a-z0-9]){re.escape(_key(str(row['ticker'])))}(?![a-z0-9])",
                                     _key(str(row["quote"])) )):
                raise Form4ValidationError("Form 4 instrument quote/source lacks exact issuer/title/ticker")
    if _key(str(row["quote"])) not in _key(visible):
        raise Form4ValidationError("Form 4 identity quote is absent from captured source bytes")
    return row


def _candidate_review(record, *, observation, clock, event_date, conflicts):
    if _aware(record["known_at"]) > clock:
        # A future capture's quoted facts must not enter a point-in-time report.
        return {"record_id": record["record_id"], "kind": record["kind"], "status": "WITHHELD",
                "reasons": ["NOT_YET_KNOWN"], "capture": None, "independently_verified": False,
                "declared_effective_interval_verified": False}
    reasons = list(conflicts)
    if record["publication_clock_conflict"]:
        reasons.append("PUBLISHER_PUBLICATION_CLOCK_CONFLICT")
    if not (date.fromisoformat(record["effective_from"]) <= event_date
            < date.fromisoformat(record["effective_to"])):
        reasons.append("OUTSIDE_DECLARED_EFFECTIVE_INTERVAL")
    if _disclosure_key(record["source_url"]) == _disclosure_key(observation["source_url"]) \
            and _disclosure_key(record["source_url"]) is not None:
        reasons.append("SAME_FILING_DISCLOSURE")
    if record["sha256"] == observation.get("source_document_sha256"):
        reasons.append("SAME_SOURCE_DOCUMENT_BYTES")
    if record["kind"] == "OWNER_CIK_PROFILE":
        reasons.append("PERSON_KIND_NOT_ESTABLISHED_BY_CIK_PROFILE")
    elif record["kind"] == "INSTRUMENT_IDENTITY" and record["instrument_identifier"] is None:
        reasons.append("EXACT_INSTRUMENT_IDENTIFIER_MISSING")
    return {
        "record_id": record["record_id"], "kind": record["kind"],
        "status": "WITHHELD" if reasons else "EXACT_DATED_CANDIDATE_REQUIRES_INDEPENDENT_REVIEW",
        "reasons": reasons, "capture": deepcopy(record), "independently_verified": False,
        "declared_effective_interval_verified": False,
    }


def review_form4_identities(
    observation: Mapping[str, object], *, knowledge_at: datetime,
    evidence: Sequence[Mapping[str, object]] = (), captured_documents: Mapping[str, bytes] | None = None,
    source_document_sha256: str | None = None,
) -> dict[str, object]:
    """Reproducible review of exact identities, never approval or trade inference.

    Effective intervals in candidate input are explicitly declared, not established
    by this matcher. Every candidate remains subject to independent review even if
    hash, quoted facts, exact names/CIKs/title/type and all clocks agree.
    """
    clock = _aware(knowledge_at)
    if (not isinstance(observation, Mapping) or observation.get("parser_version") != PARSER_VERSION
            or observation.get("issuer_identity_checked_against_index") is not True):
        raise Form4ValidationError("Form 4 identity review requires an issuer-checked source observation")
    _cik(observation.get("issuer_cik"))
    _url(observation.get("source_url"))
    if source_document_sha256 is not None and (not isinstance(source_document_sha256, str)
                                                or not re.fullmatch(r"[0-9a-f]{64}", source_document_sha256)):
        raise Form4ValidationError("Form 4 source observation hash is malformed")
    observation = dict(observation, source_document_sha256=source_document_sha256)
    if _aware(observation.get("observed_at")) > clock:
        raise Form4ValidationError("Form 4 identity review cannot expose a future source observation")
    documents = captured_documents or {}
    if (not isinstance(documents, Mapping) or len(documents) > MAX_IDENTITY_CAPTURES
            or any(not isinstance(value, bytes) for value in documents.values())
            or sum(len(value) for value in documents.values()) > MAX_TOTAL_CAPTURE_BYTES
            or not isinstance(evidence, (list, tuple)) or len(evidence) > MAX_IDENTITY_CAPTURES):
        raise Form4ValidationError("Form 4 identity evidence/captures exceed limits")
    records = [_validated_capture(row, documents) for row in evidence]
    if len({row["record_id"] for row in records}) != len(records):
        raise Form4ValidationError("Form 4 identity review contains duplicate evidence IDs")
    owners, rows, footnotes = (observation.get(key) for key in ("reporting_owners", "transactions", "footnotes"))
    if (not isinstance(owners, list) or not 1 <= len(owners) <= 32
            or not isinstance(rows, list) or len(rows) > 1_000 or not isinstance(footnotes, Mapping)):
        raise Form4ValidationError("Form 4 identity review has invalid/beyond-limit source rows")
    period = date.fromisoformat(str(observation["period_of_report"]))
    if any(not isinstance(row, Mapping) for row in rows):
        raise Form4ValidationError("Form 4 identity review has an invalid source transaction")
    transaction_dates = sorted({date.fromisoformat(str(row["transaction_date"])) for row in rows})
    if len(records) * (len(owners) * (1 + len(transaction_dates)) + len(rows)) > MAX_REVIEW_PAIRS:
        raise Form4ValidationError("Form 4 identity review source/evidence comparisons exceed limits")
    owner_reviews = []
    for owner in owners:
        if not isinstance(owner, Mapping):
            raise Form4ValidationError("Form 4 identity review has an invalid source owner")
        cik, name = _cik(owner.get("cik")), _text(owner.get("name"))
        candidates = []
        dated_candidates = {event_date.isoformat(): [] for event_date in transaction_dates}
        for record in records:
            if record["kind"] == "INSTRUMENT_IDENTITY" or record["owner_cik"] != cik:
                continue
            conflicts = [] if _key(record["owner_name"]) == _key(name) else ["EXACT_OWNER_NAME_MISMATCH"]
            candidates.append(_candidate_review(record, observation=observation, clock=clock,
                                                 event_date=period, conflicts=conflicts))
            for event_date in transaction_dates:
                dated_candidates[event_date.isoformat()].append(_candidate_review(
                    record, observation=observation, clock=clock, event_date=event_date, conflicts=conflicts))
        owner_reviews.append({"as_filed": deepcopy(dict(owner)), "match_date": period.isoformat(),
                              "status": "WAIT_INDEPENDENT_REPORTING_OWNER_IDENTITY", "candidates": candidates,
                              "transaction_date_checks": dated_candidates,
                              "owner_person_or_entity_kind_verified": False,
                              "role_checkboxes_are_identity_evidence": False})
    instrument_reviews = []
    for row in rows:
        if not isinstance(row, Mapping) or type(row.get("derivative")) is not bool:
            raise Form4ValidationError("Form 4 identity review requires explicit source instrument type")
        event_date = date.fromisoformat(str(row["transaction_date"]))
        refs = row.get("footnote_ids")
        if not isinstance(refs, list) or any(key not in footnotes for key in refs):
            raise Form4ValidationError("Form 4 identity review has unresolved source footnotes")
        title = row.get("security_title")
        if title is not None:
            _text(title)
        candidates = []
        for record in records:
            if record["kind"] != "INSTRUMENT_IDENTITY" or record["issuer_cik"] != observation["issuer_cik"]:
                continue
            conflicts = []
            if title is None or _key(record["security_title"]) != _key(title):
                conflicts.append("EXACT_SECURITY_TITLE_MISMATCH")
            if record["ticker"] != observation.get("issuer_trading_symbol"):
                conflicts.append("EXACT_TRADING_SYMBOL_MISMATCH")
            if record["derivative"] is not row["derivative"]:
                conflicts.append("DERIVATIVE_TYPE_MISMATCH")
            candidates.append(_candidate_review(record, observation=observation, clock=clock,
                                                 event_date=event_date, conflicts=conflicts))
        instrument_reviews.append({
            "as_filed": deepcopy(dict(row)), "match_date": event_date.isoformat(),
            "footnotes": {key: footnotes[key] for key in refs}, "candidates": candidates,
            "status": "WAIT_INDEPENDENT_INSTRUMENT_IDENTITY" if title else "WAIT_AS_FILED_SECURITY_TITLE",
            "underlying_security_scope": "NOT_RETAINED_IN_SOURCE_OBSERVATION" if row["derivative"] else "NOT_APPLICABLE",
            "row_owner_scope": "JOINT_REPORT_NOT_ALLOCATED" if len(owners) > 1 else "SINGLE_AS_FILED_OWNER",
        })
    return {
        "review_version": IDENTITY_REVIEW_VERSION, "reviewed_at": clock.isoformat(),
        "accession_number": observation["accession_number"], "issuer_cik": observation["issuer_cik"],
        "source_document_sha256": source_document_sha256,
        "source_url": observation["source_url"], "status": "WAIT_INDEPENDENT_IDENTITY_REVIEW",
        "reporting_owners": owner_reviews, "instruments": instrument_reviews,
        "reporting_owner_identity_independently_verified": False, "instrument_identity_independently_verified": False,
        "execution_verified": False, "ownership_change_interpreted": False, "scoring_applied": False,
        "candidate_captures_examined": len(records),
    }


__all__ = ["IDENTITY_REVIEW_VERSION", "review_form4_identities"]
