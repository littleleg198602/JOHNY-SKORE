"""Original-byte EPA facility/ownership review, never a registry adoption or I/O.

Facility names, program interests and operators cannot prove legal ownership.
Original capture time and physical import time remain separate from fact dates.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
from html import unescape
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from market_checker_app.services.sec13f_identity_bulk import _ActionPassageParser, _TickerCoverParser

MAX_CAPTURE_BYTES = 6_000_000
MAX_CAPTURES = 4
EPA_HOSTS = {"www.epa.gov", "echo.epa.gov", "echodata.epa.gov", "enviro.epa.gov",
             "ofmpub.epa.gov", "frs-public.epa.gov", "data.epa.gov"}
_SENSITIVE = re.compile(rb'["\'](?:azure_map_key|azureMapsKey|subscriptionKey|api[_-]?key|'
    rb'accessToken|client_secret|password)["\']\s*[:=]|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|'
    rb'\bAKIA[A-Z0-9]{16}\b|\bbearer\s+[A-Za-z0-9._~+/=-]{12,}|'
    rb'(?:api[_-]?key|subscription[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|password)'
    rb'["\']?\s*[:=]\s*["\']?[A-Za-z0-9._~+/=-]{8,}|'
    rb'[?&](?:sig|signature|x-amz-signature|x-goog-signature|access_token)=[^\s"\'<>]{8,}', re.I)


def _clock(value):
    value = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("FRS review clocks require an explicit timezone")
    return value.astimezone(timezone.utc)


def _text(value, limit=500):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("FRS review requires bounded exact text")
    return value.strip()


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("FRS fact dates require YYYY-MM-DD")
    return date.fromisoformat(value)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Ambiguous duplicate primary JSON key")
        result[key] = value
    return result


def _pointer(payload, pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/") or len(pointer) > 500:
        raise ValueError("Exact bounded JSON pointer required")
    for token in pointer[1:].split("/"):
        if re.search(r"~(?![01])", token):
            raise ValueError("Invalid JSON pointer escape")
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(payload, list):
            if not re.fullmatch(r"0|[1-9][0-9]{0,4}", key):
                raise ValueError("Bounded exact JSON row index required")
            payload = payload[int(key)]
        else:
            payload = payload[key]
    return payload


def _capture(root, raw, ingested):
    metadata = deepcopy(raw)
    if metadata.get("public_raw_available") is False or not metadata.get("archive_path"):
        return metadata, None, "MISSING_OR_WITHHELD_ORIGINAL_BODY"
    try:
        requested, final = urlsplit(metadata["requested_url"]), urlsplit(metadata["final_url"])
        if (requested.scheme != "https" or requested.hostname not in EPA_HOSTS | {"www.sec.gov"}
                or requested.port not in {None, 443} or requested.username or requested.password
                or requested.fragment or metadata["requested_url"] != metadata["final_url"]
                or final.fragment or metadata.get("http_status") != 200
                or isinstance(metadata.get("http_status"), bool)):
            return metadata, None, "UNSUCCESSFUL_OR_CHANGED_PRIMARY_SCOPE"
        started, observed = _clock(metadata["started_at"]), _clock(metadata["observed_at"])
        if started > observed or observed > ingested:
            return metadata, None, "INVALID_SOURCE_CAPTURE_CLOCK"
        relative = metadata["archive_path"]
        if (not isinstance(relative, str) or "\\" in relative or Path(relative).is_absolute()
                or ".." in Path(relative).parts or metadata.get("archive_encoding") not in {None, "raw"}):
            return metadata, None, "NOT_AN_ORIGINAL_REPOSITORY_BODY"
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return metadata, None, "MISSING_OR_WITHHELD_ORIGINAL_BODY"
        with path.open("rb") as stream:
            body = stream.read(MAX_CAPTURE_BYTES + 1)
        if (len(body) > MAX_CAPTURE_BYTES or isinstance(metadata.get("raw_response_bytes"), bool)
                or metadata.get("raw_response_bytes") != len(body)
                or metadata.get("raw_response_sha256") != hashlib.sha256(body).hexdigest()):
            return metadata, None, "ORIGINAL_BODY_PIN_MISMATCH_OR_BYTE_BOUND"
        if _SENSITIVE.search(unescape(body.decode("utf-8", errors="replace")).encode()):
            metadata.update(public_raw_available=False, archive_path=None,
                            publication_status="WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE")
            return metadata, None, "WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE"
        return metadata, body, None
    except (KeyError, TypeError, ValueError, OSError):
        return metadata, None, "INVALID_OR_UNAVAILABLE_CAPTURE_METADATA"


class _LegalFacts(_TickerCoverParser):
    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        attributes = dict(attrs)
        key = str(attributes.get("name", "")).casefold()
        if tag == "ix:nonnumeric" and key in {"dei:entityregistrantname", "dei:entitycentralindexkey"}:
            if (self.namespaces != {"dei", "ix", "xbrli"} or self.context
                    or not attributes.get("contextref") or attributes.get("continuedat")
                    or str(attributes.get("xsi:nil", "")).casefold() in {"true", "1"}
                    or any(capture[0] == tag for capture in self.captures)):
                raise ValueError("Ambiguous legal filing fact")
            self.captures.append([tag, attributes["contextref"], key, []])


def _legal_fact(body, scope):
    parser = _LegalFacts()
    parser.feed(body.decode("utf-8-sig"))
    parser.close()
    if parser.captures or parser.context is not None or parser.namespaces != {"dei", "ix", "xbrli"}:
        raise ValueError("Incomplete legal filing context")
    matches = []
    for context, values in parser.facts.items():
        names, ciks = values.get("dei:entityregistrantname", []), values.get("dei:entitycentralindexkey", [])
        dates = parser.contexts.get(context, {}).get("xbrli:instant", []) + parser.contexts.get(context, {}).get("xbrli:enddate", [])
        # Explicit DEI CIK is essential in co-filings: the XBRL entity identifier
        # can remain the parent CIK even for a subsidiary registrant context.
        if (len(names) == len(ciks) == len(dates) == 1 and re.fullmatch(r"\d{10}", ciks[0])
                and (names[0], ciks[0], dates[0]) ==
                    (scope["issuer_name"], scope["issuer_cik"], scope["issuer_anchor_date"])):
            matches.append({"issuer_name": names[0], "issuer_cik": ciks[0],
                            "anchor_date": dates[0], "context_ref": context})
    return matches[0] if len(matches) == 1 else None


def _visible(body):
    parser = _ActionPassageParser()
    parser.feed(body.decode("utf-8-sig"))
    parser.close()
    if not parser.has_html or parser.stack:
        raise ValueError("Incomplete visible primary ownership document")
    return " ".join(" ".join(parser.parts).split())


def review_frs_ownership(proposal, captures, *, repository_root: Path,
                         ingested_at: datetime, as_of: datetime | None = None) -> dict:
    """Bounded captured-only field review. No network, DB, adoption or scores."""
    ingested, cutoff = _clock(ingested_at), _clock(as_of or ingested_at)
    if (not isinstance(proposal, dict) or not isinstance(captures, list)
            or not 1 <= len(captures) <= MAX_CAPTURES or any(not isinstance(row, dict) for row in captures)):
        raise ValueError("FRS review requires one proposal and at most four original capture pins")
    scope = {key: _text(proposal.get(key)) for key in
             ("ticker", "issuer_cik", "issuer_name", "issuer_anchor_date", "frs_registry_id", "facility_name")}
    if (not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,12}", scope["ticker"])
            or not re.fullmatch(r"\d{10}", scope["issuer_cik"]) or int(scope["issuer_cik"]) == 0
            or not re.fullmatch(r"\d{12}", scope["frs_registry_id"])):
        raise ValueError("FRS review needs an exact ticker/CIK/12-digit facility scope")
    _day(scope["issuer_anchor_date"])
    if _day(scope["issuer_anchor_date"]) > ingested.date():
        raise ValueError("Legal fact date cannot follow physical ingestion")
    scope["owner_entity_name"] = _text(proposal["owner_entity_name"]) if proposal.get("owner_entity_name") else None
    scope["ownership_date"] = _day(proposal["ownership_date"]).isoformat() if proposal.get("ownership_date") else None
    ids = [_text(row.get("capture_id"), 150) for row in captures]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate FRS primary capture pin")
    result = {"schema_version": 1, "status": "PARTIAL", "verification_status": "UNVERIFIED",
        "scope": scope, "ingested_at": ingested.isoformat(), "known_at": ingested.isoformat(),
        "as_of": cutoff.isoformat(), "time_basis": "CAPTURED_SOURCE_REVIEW", "captures": [],
        "facility_identity_supported": False, "program_bindings": [], "issuer_identity_supported": False,
        "issuer_identity_fact": None, "ownership_statement_supported": False, "missing_evidence": [],
        "issuer_attribution_allowed": False, "issuer_facility_relationship_verified": False,
        "environmental_liability_verified": False, "issuer_fine_verified": False,
        "issuer_conviction_verified": False, "complete_facility_group_verified": False,
        "historical_availability_reconstructed": False, "live_acceptance_verified": False,
        "registry_adoption_allowed": False, "scoring_applied": False, "prediction_input": False}
    if ingested > cutoff:
        return {**result, "missing_evidence": ["NOT_YET_KNOWN_AT_PHYSICAL_IMPORT"]}
    loaded = {}
    root = Path(repository_root).resolve()
    for raw in captures:
        metadata, body, error = _capture(root, raw, ingested)
        loaded[raw["capture_id"]] = (metadata, body)
        result["captures"].append({**metadata, "review_status": "PARTIAL" if error else "ORIGINAL_BYTES_PINNED",
                                   "review_reason": error})
        if error:
            result["missing_evidence"].append("PARTIAL_PRIMARY_CAPTURE:" + raw["capture_id"])
    try:
        pin = proposal["facility_pin"]
        metadata, body = loaded[pin["capture_id"]]
        if body is None or urlsplit(metadata["requested_url"]).hostname not in EPA_HOSTS:
            raise ValueError("Unavailable primary EPA facility")
        if metadata["content_type"].split(";", 1)[0].strip().casefold() != "application/json":
            raise ValueError("EPA facility pin is not original JSON")
        payload = json.loads(body, object_pairs_hook=_unique)
        if isinstance(payload, dict):
            envelope = payload.get("Results", payload)
            if (not isinstance(envelope, dict)
                    or any(envelope.get(key) for key in ("Error", "Errors", "error", "errors"))
                    or "Message" in envelope and envelope["Message"] != "Success"):
                raise ValueError("Unsuccessful EPA facility envelope")
            rows = envelope.get("Facilities")
            if rows is not None:
                if not isinstance(rows, list) or len(rows) > 100:
                    raise ValueError("Unbounded EPA facility rows")
                same_id = [item for item in rows if isinstance(item, dict)
                           and item.get("RegistryId", item.get("RegistryID")) == scope["frs_registry_id"]]
                if len(same_id) != 1:
                    raise ValueError("Ambiguous exact FRS facility")
                count = envelope.get("QueryRows", len(rows))
                if isinstance(count, bool) or not re.fullmatch(r"\d{1,10}", str(count)) or int(count) < len(rows):
                    raise ValueError("Inconsistent EPA facility count")
                if int(count) > len(rows):
                    result["missing_evidence"].append("INCOMPLETE_FACILITY_QUERY")
        row = _pointer(payload, pin["json_pointer"])
        names = [row[key] for key in ("FacilityName", "FacName") if key in row]
        registry_ids = [row[key] for key in ("RegistryId", "RegistryID") if key in row]
        if names != [scope["facility_name"]] or registry_ids != [scope["frs_registry_id"]]:
            raise ValueError("Exact original FRS ID/name not pinned")
        result["facility_identity_supported"] = True
        programs = row.get("ProgramFacilities", [])
        if not isinstance(programs, list) or len(programs) > 100:
            raise ValueError("Unbounded EPA program scope")
        seen = set()
        for program in programs:
            record = {key: _text(program[key]) for key in
                      ("ProgramSystemAcronym", "ProgramSystemId", "ProgramFacilityName")}
            key = (record["ProgramSystemAcronym"], record["ProgramSystemId"])
            if key in seen:
                raise ValueError("Duplicate EPA program binding")
            seen.add(key)
            result["program_bindings"].append(record)
    except (KeyError, TypeError, ValueError, IndexError):
        result["facility_identity_supported"] = False
        result["program_bindings"] = []
        result["missing_evidence"].append("MISSING_EXACT_ORIGINAL_FRS_FACILITY_PROGRAM_PIN")
    if not result["program_bindings"]:
        result["missing_evidence"].append("PROGRAM_BINDING_NOT_CAPTURED")
    try:
        metadata, body = loaded[proposal["issuer_pin"]["capture_id"]]
        if (body is None or urlsplit(metadata["requested_url"]).hostname != "www.sec.gov"
                or not urlsplit(metadata["requested_url"]).path.startswith("/Archives/edgar/data/")):
            raise ValueError("Independent primary SEC legal filing required")
        fact = _legal_fact(body, scope)
        if fact is None:
            raise ValueError("Exact legal entity/context/date not found")
        result.update(issuer_identity_supported=True, issuer_identity_fact=fact)
    except (KeyError, TypeError, ValueError, UnicodeError):
        result["missing_evidence"].append("MISSING_SAME_CONTEXT_DATED_SEC_LEGAL_ENTITY")
    try:
        pin = proposal["ownership_pin"]
        if pin["capture_id"] == proposal["facility_pin"]["capture_id"]:
            raise ValueError("Independent ownership source required")
        metadata, body = loaded[pin["capture_id"]]
        if body is None or not scope["owner_entity_name"] or not scope["ownership_date"]:
            raise ValueError("Missing original dated owner statement")
        facility_metadata = loaded[proposal["facility_pin"]["capture_id"]][0]
        if (metadata["requested_url"] == facility_metadata.get("requested_url")
                or metadata["raw_response_sha256"] == facility_metadata.get("raw_response_sha256")):
            raise ValueError("Copied facility source cannot be independent ownership evidence")
        excerpt = " ".join(_text(pin["excerpt"], 4000).split())
        day = _day(scope["ownership_date"])
        dated = _text(pin["effective_date_text"], 80)
        allowed_dates = {day.isoformat(), f"{day.strftime('%B')} {day.day}, {day.year}"}
        if (excerpt not in _visible(body) or dated not in allowed_dates or dated not in excerpt
                or set(re.findall(r"\b\d{12}\b", excerpt)) != {scope["frs_registry_id"]}
                or re.search(r"\b(?:not|operator|operates|formerly|previously|proposed)\b", excerpt, re.I)):
            raise ValueError("Not an exact unambiguous dated ownership passage")
        owner, facility = re.escape(scope["owner_entity_name"]), re.escape(scope["facility_name"])
        qualifier = rf"(?:\s*\(FRS (?:Registry ID|RegistryId) {scope['frs_registry_id']}\))?"
        pattern = rf"(?:{owner}\s+(?:owns|is the owner of)\s+{facility}|{facility}{qualifier}\s+is owned by\s+{owner})(?![\w.])"
        if not re.search(pattern, excerpt) or _day(scope["ownership_date"]) > ingested.date():
            raise ValueError("No explicit exact facility owner relation at a valid date")
        result["ownership_statement_supported"] = True
        if scope["owner_entity_name"] != scope["issuer_name"]:
            result["missing_evidence"].append("MISSING_DATED_OWNER_TO_ISSUER_BRIDGE")
    except (KeyError, TypeError, ValueError, UnicodeError):
        result["missing_evidence"].append("MISSING_INDEPENDENT_DATED_EXACT_FACILITY_OWNER_STATEMENT")
    if not result["missing_evidence"]:
        result["status"] = "REVIEWABLE_SOURCE_CANDIDATE"
    return result


def import_frs_ownership_review(proposal, captures, *, repository_root: Path, archive_root: Path) -> dict:
    """Persist one immutable review with actual UTC import time; never raw substitutes."""
    clock = datetime.now(timezone.utc)
    report = review_frs_ownership(proposal, captures, repository_root=repository_root, ingested_at=clock)
    serialized = json.dumps(report, sort_keys=True, indent=2) + "\n"
    review_id = hashlib.sha256(serialized.encode()).hexdigest()
    folder = Path(archive_root) / review_id
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "review.json"
    try:
        with target.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(serialized)
    except FileExistsError:
        if target.read_text(encoding="utf-8") != serialized:
            raise ValueError("Immutable FRS review collision")
    return {**report, "review_id": review_id, "review_path": target.as_posix()}
