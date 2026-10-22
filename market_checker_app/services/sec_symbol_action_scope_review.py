"""Exact-day original SEC symbol-action scope; never an identity adoption.

Announcement, prospective effective day, reported completion, legal-name change
and an instrument's dated class/CUSIP tuple are independent claims. The review
does no network, filesystem discovery, registry write or interval extension.
"""
from __future__ import annotations

from array import array
from collections.abc import Mapping
from datetime import date, datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import re
from types import SimpleNamespace
from urllib.parse import parse_qs, urljoin, urlsplit

from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body
from market_checker_app.services.sec13f_identity_bulk import (
    _clock, _dated_action_text, _name, _valid_cusip, parse_schedule13, parse_ticker_filing,
)
from market_checker_app.services.sec_corporate_action_bridge import class_terms, issuer_names
from market_checker_app.services.sec_person_kind_review import _Index

VERSION = "sec-symbol-action-original-scope-v1"
MAX_BODY_BYTES, MAX_TOTAL_BYTES, MAX_CAPTURES = 4_000_000, 24_000_000, 16
MAX_REQUEST_BYTES, MAX_CLAIMS = 48_000, 24
_KEY = re.compile(r"[A-Za-z0-9_-]{1,80}")
_HASH = re.compile(r"[a-f0-9]{64}")
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def _encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def _physical_clock():
    return datetime.now(timezone.utc)


def _url(value):
    if not isinstance(value, str) or len(value) > 2_000:
        raise ValueError("A bounded primary SEC URL is required")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.port not in {None, 443} or "%" in value):
        raise ValueError("A canonical original SEC reference is required")
    if parsed.hostname == "data.sec.gov":
        match = re.fullmatch(r"/submissions/CIK([0-9]{10})\.json", parsed.path)
        if match:
            return match[1], None, "SUBMISSIONS"
    if parsed.hostname == "www.sec.gov":
        match = re.fullmatch(r"/Archives/edgar/data/([0-9]{1,10})/([0-9]{18})/([A-Za-z0-9_.-]+)", parsed.path)
        if match:
            return match[1].zfill(10), match[2], match[3]
    raise ValueError("Only exact primary SEC submissions or accession documents are supported")


def _captures(captures, scope, cutoff, physical):
    if not isinstance(captures, Mapping) or not 1 <= len(captures) <= MAX_CAPTURES:
        raise ValueError("Select one to sixteen exact bounded captures")
    result, total = {}, 0
    for key, pair in captures.items():
        if not isinstance(key, str) or not _KEY.fullmatch(key) or not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise ValueError("Capture must be an explicit key to metadata/original-bytes pair")
        metadata, body = pair
        if not isinstance(metadata, Mapping) or len(_encoded(dict(metadata))) > 16_000:
            raise ValueError("Malformed or overbudget capture metadata")
        if _has_sensitive_body(_encoded(dict(metadata))):
            raise ValueError("Access-bearing capture metadata cannot be reviewed")
        url = metadata.get("requested_url", metadata.get("url"))
        for field in ("requested_url", "url", "final_url"):
            if field in metadata and metadata[field] != url:
                raise ValueError("Original source URL and resolved URL differ")
        cik, accession, filename = _url(url)
        if cik != scope["issuer_cik"]:
            raise ValueError("Capture is outside the exact requested issuer routing scope")
        started = _clock(metadata.get("started_at"))
        observed = _clock(metadata.get("observed_at"))
        known = _clock(metadata.get("known_at", metadata.get("observed_at")))
        if not started <= observed <= known <= cutoff <= physical:
            raise ValueError("Reversed, unavailable or future original capture/knowledge clocks")
        digest = metadata.get("raw_sha256", metadata.get("sha256"))
        size = metadata.get("bytes")
        if (not isinstance(digest, str) or not _HASH.fullmatch(digest) or type(size) is not int
                or not 0 < size <= MAX_BODY_BYTES):
            raise ValueError("Exact bounded original hash and size are required")
        if "sha256" in metadata and metadata["sha256"] != digest:
            raise ValueError("Original-byte hash declarations conflict")
        unavailable = (metadata.get("public_raw_available") is False or metadata.get("body_withheld") is True
                       or metadata.get("retention") in {"MISSING_BODY", "WITHHELD_SENSITIVE_BODY"}
                       or any(str(metadata.get(field, "")).startswith(("WITHHELD", "MISSING"))
                              for field in ("status", "archive_status", "publication_status")))
        if unavailable:
            if body is not None:
                raise ValueError("Unavailable whole body cannot be restored or substituted")
        elif (metadata.get("http_status") != 200 or metadata.get("status") not in {"PUBLIC_ORIGINAL_BYTES_CAPTURED", "RAW_CAPTURED"}
              or metadata.get("body_transformed", False) is not False or not isinstance(body, bytes)
              or len(body) != size or hashlib.sha256(body).hexdigest() != digest):
            raise ValueError("Original complete public source bytes/hash/status conflict")
        elif _has_sensitive_body(body):
            raise ValueError("Whole access-bearing body is unavailable")
        total += size if body is not None else 0
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Original evidence total byte budget exceeded")
        result[key] = {"body": body, "url": url, "cik": cik, "accession": accession, "filename": filename,
                       "observed": observed, "known": known, "sha256": digest, "bytes": size,
                       "metadata_sha256": hashlib.sha256(_encoded(dict(metadata))).hexdigest()}
    return result


class _Blocks(HTMLParser):
    """Index whole visible leaf div/p blocks against the original byte offsets."""
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.text = body.decode("utf-8-sig", errors="strict")
        if self.text.encode() != body:
            raise ValueError("Select exact UTF-8 bytes without a rewritten encoding envelope")
        self.lines = [0] + [match.end() for match in re.finditer("\n", self.text)]
        self.offsets, position = array("I", [0]), 0
        for character in self.text:
            position += len(character.encode())
            self.offsets.append(position)
        self.stack, self.active, self.blocks = [], [], []
        self.counts, self.nodes = {"div": 0, "p": 0}, 0
        self.body = body
        self.feed(self.text); self.close()
        if self.active:
            raise ValueError("Incomplete original paragraph structure")

    def _position(self):
        line, column = self.getpos()
        return self.lines[line-1] + column

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 200_000 or len(self.stack) > 128:
            raise ValueError("Original paragraph complexity budget exceeded")
        attrs = dict(attrs)
        hidden = bool(self.stack and self.stack[-1][1]) or tag in {"script", "style", "noscript", "ix:hidden", "ix:header"}
        compact = str(attrs.get("style", "")).replace(" ", "").lower()
        hidden |= "hidden" in attrs or "display:none" in compact or "visibility:hidden" in compact
        if hidden:
            for block in self.active:
                block["hidden_content"] = True
        if tag in self.counts:
            self.counts[tag] += 1
            for block in self.active:
                block["nested"] = True
            self.active.append({"tag": tag, "depth": len(self.stack), "locator": f"{tag}:{self.counts[tag]}",
                                "start": self._position(), "parts": [], "nested": False, "hidden_content": hidden})
        if tag not in _VOID:
            self.stack.append((tag, hidden))

    def handle_endtag(self, tag):
        matching = next((index for index in range(len(self.stack)-1, -1, -1) if self.stack[index][0] == tag), None)
        if matching is None:
            return
        ending = self._position()
        stop = self.text.find(">", ending)
        if stop < ending:
            raise ValueError("Incomplete original paragraph end tag")
        for block in list(self.active):
            if block["depth"] >= matching:
                self.active.remove(block)
                if block["tag"] != tag:
                    raise ValueError("Crossed original paragraph structure")
                text = " ".join(" ".join(block["parts"]).split())
                if not block["nested"] and not block["hidden_content"] and 20 <= len(text) <= 4_000:
                    left, right = self.offsets[block["start"]], self.offsets[stop+1]
                    self.blocks.append({"locator": block["locator"], "byte_start": left, "byte_end": right,
                                        "sha256": hashlib.sha256(self.body[left:right]).hexdigest(), "text": text})
        del self.stack[matching:]

    def handle_data(self, data):
        for block in self.active:
            block["parts"].append(data)


def symbol_action_passages(body: bytes) -> list[dict]:
    """Locate candidate original blocks, without selecting facts or making writes."""
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_BODY_BYTES or _has_sensitive_body(body):
        raise ValueError("Only a safe bounded whole original body may be indexed")
    return _Blocks(body).blocks


def _filing(source, proof, descriptor):
    result = {"source_url": source["url"], "accession": source["accession"], "form": descriptor.get("form"),
              "filing_on": None, "publication_at": None, "acceptance_unqualified_raw": None,
              "publisher_size_conflict": None, "as_filed_body_version_verified": False, "blockers": []}
    if source["body"] is None or proof is None or proof["body"] is None:
        result["blockers"].append("ORIGINAL_SOURCE_OR_PUBLICATION_PROOF_UNAVAILABLE")
        return result
    mode = descriptor.get("proof_kind")
    if mode == "SUBMISSIONS":
        if proof["filename"] != "SUBMISSIONS":
            raise ValueError("Publication proof is not the original SEC submissions JSON")
        data = json.loads(proof["body"])
        if str(data.get("cik")).zfill(10) != source["cik"]:
            raise ValueError("Submissions JSON issuer differs from the requested source")
        recent = data.get("filings", {}).get("recent", {})
        numbers = recent.get("accessionNumber", [])
        if not isinstance(numbers, list) or len(numbers) > 20_000:
            raise ValueError("Unbounded or malformed SEC submissions rows")
        indices = [index for index, number in enumerate(numbers) if isinstance(number, str)
                   and re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", number)
                   and number.replace("-", "") == source["accession"]]
        if len(indices) != 1:
            raise ValueError("Exact source accession is missing or ambiguous in submissions")
        index = indices[0]
        try:
            row = {key: recent[key][index] for key in ("primaryDocument", "form", "filingDate", "acceptanceDateTime")}
        except (KeyError, IndexError, TypeError) as error:
            raise ValueError("Incomplete exact SEC submissions row") from error
        if row["form"] != descriptor.get("form"):
            raise ValueError("Requested form conflicts with the exact submissions accession")
        if row["primaryDocument"] != source["filename"]:
            if (row["form"] not in {"SCHEDULE 13G", "SCHEDULE 13G/A", "SCHEDULE 13D", "SCHEDULE 13D/A"}
                    or not re.fullmatch(r"xslSCHEDULE_13[GD]_X[0-9]{2}/" + re.escape(source["filename"]), row["primaryDocument"])):
                raise ValueError("Primary document path conflicts with the exact accession row")
            result["blockers"].append("RAW_XML_RENDERER_PATH_DIFFERS_NO_ORIGINAL_INDEX")
        result.update(filing_on=row["filingDate"], publication_at=_clock(row["acceptanceDateTime"]).isoformat(),
                      submissions_primary_document=row["primaryDocument"], submissions_row_index=index)
    elif mode == "INDEX":
        accession = source["accession"]
        expected = f"{accession[:10]}-{accession[10:12]}-{accession[12:]}-index"
        if proof["accession"] != accession or proof["filename"] not in {expected+".htm", expected+".html"}:
            raise ValueError("Source/index exact accession or index basename conflict")
        parsed = _Index(proof["body"])
        if parsed.filer_ciks != [source["cik"]]:
            raise ValueError("Original index filer CIK is ambiguous or different")
        matched = []
        for row in parsed.rows:
            for href in row["hrefs"]:
                if href.startswith("/ix?"):
                    docs = parse_qs(urlsplit(href).query).get("doc", [])
                    href = docs[0] if len(docs) == 1 else ""
                if urljoin(proof["url"], href) == source["url"]:
                    matched.append(row)
        if len(matched) != 1 or len(matched[0]["cells"]) < 5 or matched[0]["cells"][-2] != descriptor.get("form"):
            raise ValueError("Source is not the exact selected index document/form")
        declared = int(matched[0]["cells"][-1])
        if declared <= 0:
            raise ValueError("Index requires a positive declared original document size")
        result.update(filing_on=parsed.labels.get("Filing Date"), acceptance_unqualified_raw=parsed.labels.get("Accepted"),
                      index_declared_bytes=declared, publisher_size_conflict=declared != source["bytes"])
        if declared != source["bytes"]:
            result["blockers"].append("INDEX_DECLARED_SERVED_BYTE_CONFLICT")
    else:
        raise ValueError("Select an explicit original SUBMISSIONS or INDEX publication proof")
    filed = date.fromisoformat(result["filing_on"])
    if filed > min(source["observed"], proof["observed"]).date():
        raise ValueError("Filing day follows the whole original capture")
    if result["publication_at"] and _clock(result["publication_at"]) > min(source["observed"], proof["observed"]):
        raise ValueError("Declared SEC acceptance follows the original capture")
    return result


def _phase(text):
    if re.search(r"\b(?:not|never|no longer|cancel\w*|withdraw\w*)\b", text, re.I):
        return "UNRESOLVED"
    if re.search(r"\b(?:will|would|expect\w*|plan\w*|intend\w*|scheduled|propos\w*|may|might|could)\b", text, re.I):
        return "PLANNED"
    if re.search(r"\b(?:changed|updated|renamed|began|commenced|transitioned|remained)\b", text, re.I):
        return "EFFECTIVE"
    if re.search(r"\bannounc(?:ed|es|ement)\b", text, re.I):
        return "ANNOUNCED"
    return "UNRESOLVED"


def _actor(claim, text, verbs):
    name, reference = claim.get("issuer_name_text"), claim.get("issuer_reference_text")
    if not isinstance(name,str) or not isinstance(reference,str):
        return r"(?!)"
    subject = re.escape(reference)
    if reference != name:
        definition = re.escape(name)+r'\s*\(\s*(?:the\s+)?["“\x27‘]'+re.escape(reference)+r'["”\x27’]\s*\)'
        if re.search(definition,text) is None:
            return r"(?!)"
        subject = "(?:"+definition+"|"+subject+")"
    return r"(?<![A-Za-z0-9])"+subject+r"\s+(?:(?:has|have|will|would|also|formally|now)\s+){0,3}(?:"+verbs+r")\b"


def _subject(claim, text, selected, filings, expected_cik):
    key, context = claim.get("issuer_source_key"), claim.get("issuer_context_ref")
    name, reference = claim.get("issuer_name_text"), claim.get("issuer_reference_text")
    if (key not in filings or not isinstance(context, str) or not 1 <= len(context) <= 100
            or any(not isinstance(value, str) or not 1 <= len(value) <= 300 for value in (name, reference))):
        return ["ACTION_SUBJECT_EXACT_CIK_NAME_CONTEXT_UNPROVEN"]
    proof, descriptor = selected[key], filings[key]
    if proof["body"] is None or descriptor["publication_at"] is None:
        return ["ACTION_SUBJECT_EXACT_CIK_NAME_CONTEXT_UNPROVEN"]
    try:
        names = [row for row in issuer_names(_snapshot(proof, descriptor, "ticker_filing"))
                 if row["context_ref"] == context and row["issuer_cik"] == expected_cik and row["issuer_name"] == name]
    except ValueError:
        return ["ACTION_SUBJECT_EXACT_CIK_NAME_CONTEXT_UNPROVEN"]
    if len(names) != 1:
        return ["ACTION_SUBJECT_EXACT_CIK_NAME_CONTEXT_UNPROVEN"]
    if reference != name:
        definition = re.escape(name)+r'\s*\(\s*(?:the\s+)?["“\x27‘]'+re.escape(reference)+r'["”\x27’]\s*\)'
        if re.search(definition,text) is None:
            return ["ACTION_SUBJECT_LOCAL_REFERENCE_UNPROVEN"]
    actor = _actor(claim,text,r"change[ds]?|update[ds]?|rename[ds]?|began|commenced|transitioned|announce[ds]?")
    if re.search(actor,text,re.I) is None:
        return ["DIRECTED_ACTION_SUBJECT_NOT_BOUND_TO_VERB"]
    return []


def _action_clause(claim, text, scope, kind):
    """Keep actor, verb, owned object and old/new direction in one clause."""
    if kind == "LEGAL_NAME_CHANGE":
        old,new = claim.get("from_name"),claim.get("to_name")
        if any(not isinstance(value,str) or not 1 <= len(value) <= 300 for value in (old,new)):
            raise ValueError("Legal-name claims require exact separate old/new name text")
        if old != claim.get("issuer_name_text"):
            return None
        patterns = [_actor(claim,text,r"change[ds]?")+r"\s+its\s+(?:corporate|legal) name\s+to\s+"+re.escape(new)]
    else:
        left,right = map(re.escape,(scope["from_symbol"],scope["to_symbol"]))
        quote = r'["“”\x27‘’]?'
        direction = r"from\s+"+quote+left+quote+r"\s+to\s+"+quote+right+quote+r"(?![A-Za-z0-9])"
        patterns = [
            _actor(claim,text,r"change[ds]?|update[ds]?|transitioned")+r"\s+its\s+(?:ticker|trading)\s+symbol\s+"+direction,
            _actor(claim,text,r"announc(?:ed|es)")+r"\s+(?:a|its|the)\s+(?:ticker|trading)\s+symbol\s+change\s+"+direction,
        ]
    matches = [match for pattern in patterns for match in re.finditer(pattern,text,re.I)]
    return matches[0] if len(matches) == 1 else None


def _date_on_clause(text, clause, date_text, role, phase):
    if clause is None:
        return False
    prefix, suffix = text[:clause.start()],text[clause.end():]
    marker = r"(?:On|Effective|Beginning|Commencing)" if role == "EFFECTIVE" else r"On"
    before = re.search(r"(?:^|[.!?;]\s+)(?:"+marker+r"\s+)?"+re.escape(date_text)+r",?\s*$",prefix,re.I)
    after = re.match(r"\s*,?\s*(?:on|effective(?:\s+on)?|beginning)\s+"+re.escape(date_text)+r"(?=[,.;]|\s*$)",suffix,re.I)
    if role == "ANNOUNCEMENT":
        return bool(before and phase == "ANNOUNCED")
    return bool((before or after) and phase in {"EFFECTIVE","PLANNED"})


def _action(claim, source, filing, scope, blocks, sources, filings):
    selection = claim.get("selection")
    if not isinstance(selection, dict) or len(_encoded(selection)) > 12_000:
        raise ValueError("Select an exact bounded whole original paragraph")
    selected = [block for block in blocks if block == selection]
    if len(selected) != 1:
        raise ValueError("Paragraph locator/whole-byte span/hash/text is absent or ambiguous")
    text = selected[0]["text"]
    blockers = _subject(claim, text, sources, filings, scope["issuer_cik"])
    kind, requested_phase = claim.get("event_kind"), claim.get("asserted_phase")
    if kind not in {"SYMBOL_CHANGE", "LEGAL_NAME_CHANGE", "CUSIP_CONTINUITY"} or requested_phase not in {"ANNOUNCED", "PLANNED", "EFFECTIVE"}:
        raise ValueError("Symbol, legal-name and CUSIP claim phases must be explicit")
    clause = _action_clause(claim,text,scope,kind)
    phase = _phase(clause.group()) if clause is not None else _phase(text)
    if phase == "EFFECTIVE" and _phase(text) in {"PLANNED","UNRESOLVED"}:
        blockers.append("WHOLE_PASSAGE_QUALIFIES_ACTION_PHASE")
    if phase != requested_phase:
        blockers.append("ASSERTED_PHASE_NOT_SUPPORTED_BY_WHOLE_PASSAGE")
    if clause is None:
        blockers.append("DIRECTED_EXACT_OLD_NEW_ACTION_BINDING_MISSING")
    if kind == "CUSIP_CONTINUITY":
        # One immediate instrument sentence, without an intervening subject,
        # cannot borrow a competitor's identifier/class elsewhere in a block.
        continuation = (r"^\.\s+CUSIP\s+"+re.escape(scope["cusip"])
                        +r"\s+(?:(?:will\s+)?remain(?:ed|s)?|is|was) unchanged for its\s+"
                        +re.escape(scope["class_title"])+r"(?=[.;]|\s*$)")
        if clause is None or re.search(continuation,text[clause.end():],re.I) is None:
            blockers.append("EXACT_CLASS_CUSIP_CONTINUITY_STATEMENT_MISSING")
    day, date_text, role = claim.get("day"), claim.get("date_text"), claim.get("date_role")
    if role not in {"EFFECTIVE", "ANNOUNCEMENT", "UNKNOWN"}:
        raise ValueError("An explicit event date role is required")
    valid_day = None
    if day is None and date_text is None:
        blockers.append("EXACT_EVENT_DAY_UNKNOWN")
    else:
        if (not isinstance(day, str) or day != date.fromisoformat(day).isoformat()
                or not isinstance(date_text, str) or not _dated_action_text(date_text, date.fromisoformat(day))):
            raise ValueError("A complete exact source day is required; a month/filing day is insufficient")
        if date_text not in text:
            blockers.append("EXACT_EVENT_DATE_ABSENT_FROM_PASSAGE")
        elif role == "EFFECTIVE" and not _date_on_clause(text,clause,date_text,role,phase):
            blockers.append("DATE_NOT_EXPLICITLY_BOUND_TO_EFFECTIVE_ACTION")
        elif role == "ANNOUNCEMENT" and not _date_on_clause(text,clause,date_text,role,phase):
            blockers.append("DATE_NOT_EXPLICITLY_BOUND_TO_ANNOUNCEMENT")
        elif role == "UNKNOWN":
            blockers.append("EXACT_EVENT_DATE_ROLE_UNKNOWN")
        elif phase != "PLANNED" and date.fromisoformat(day) > date.fromisoformat(filing["filing_on"]):
            blockers.append("REPORTED_EVENT_DAY_AFTER_DISCLOSURE")
        else:
            valid_day = day
    supported = not blockers and phase == requested_phase
    effective = valid_day if supported and phase == "EFFECTIVE" and role == "EFFECTIVE" else None
    return {"source_key": claim["source_key"], "event_kind": kind, "asserted_phase": requested_phase,
            "literal_phase": phase, "reported_day": valid_day, "date_role": role,
            "announced_on": valid_day if supported and role == "ANNOUNCEMENT" else None,
            "planned_effective_on": valid_day if supported and phase == "PLANNED" and role == "EFFECTIVE" else None,
            "effective_on": effective, "effective_action_supported": effective is not None,
            "whole_original_passage": selected[0], "accession": source["accession"],
            "action_clause": {"text":clause.group(),"visible_text_start":clause.start(),"visible_text_end":clause.end()} if clause else None,
            "from_name": claim.get("from_name") if kind == "LEGAL_NAME_CHANGE" else None,
            "to_name": claim.get("to_name") if kind == "LEGAL_NAME_CHANGE" else None, "blockers": blockers}


def _snapshot(source, filing, kind):
    if filing["publication_at"] is None:
        raise ValueError("A date-only index cannot supply a substituted SEC publication timestamp")
    published = _clock(filing["publication_at"])
    return SimpleNamespace(kind=kind, url=source["url"], payload=source["body"], observed_at=source["observed"],
                           published_at=published, snapshot_id=source["sha256"])


def review_symbol_action_scope(request: dict, captures: Mapping, *, as_of: datetime | None = None) -> dict:
    """Review explicit source/proof/block selections; return facts and blockers.

    Capture values are ``(metadata, original_bytes_or_None)`` as loaded by the
    captured-review adapter. Request keys: scope, filings, action_claims,
    cover_claims, instrument_claims. No filename glob, clock substitution,
    automatic alias or current-quarter instrument promotion occurs.
    """
    if not isinstance(request, dict) or set(request) != {"scope", "filings", "action_claims", "cover_claims", "instrument_claims"} or len(_encoded(request)) > MAX_REQUEST_BYTES:
        raise ValueError("An exact bounded symbol-action request is required")
    if _has_sensitive_body(_encoded(request)):
        raise ValueError("Access-bearing request fields cannot be reviewed")
    scope = request["scope"]
    if (not isinstance(scope, dict) or set(scope) != {"issuer_cik", "from_symbol", "to_symbol", "class_title", "cusip"}
            or not isinstance(scope["issuer_cik"], str) or not re.fullmatch(r"[0-9]{10}", scope["issuer_cik"]) or int(scope["issuer_cik"]) == 0
            or any(not isinstance(scope[key], str) or not re.fullmatch(r"[A-Z0-9./-]{1,15}", scope[key]) for key in ("from_symbol", "to_symbol"))
            or scope["from_symbol"] == scope["to_symbol"] or not isinstance(scope["cusip"], str) or not _valid_cusip(scope["cusip"])
            or class_terms(scope["class_title"]) is None or class_terms(scope["class_title"])["par_term"] == "UNSPECIFIED"
            or class_terms(scope["class_title"])["share_class"] == "EQUITY:UNCLASSIFIED"):
        raise ValueError("Exact separate issuer/symbol/class/CUSIP scope is required")
    for key in ("filings", "action_claims", "cover_claims", "instrument_claims"):
        if not isinstance(request[key], list) or len(request[key]) > MAX_CLAIMS or any(not isinstance(row, dict) for row in request[key]):
            raise ValueError("Review selections exceed their explicit claim budget")
    physical = _clock(_physical_clock())
    cutoff = physical if as_of is None else _clock(as_of)
    if cutoff < physical:
        raise ValueError("AFTER_PHYSICAL_KNOWLEDGE_CUTOFF: a new review cannot be backdated")
    if cutoff > physical:
        raise ValueError("A review cannot assert a future physical knowledge cutoff")
    selected = _captures(captures, scope, cutoff, physical)
    filings = {}
    for descriptor in request["filings"]:
        key, proof = descriptor.get("source_key"), descriptor.get("proof_key")
        if key in filings or key not in selected or proof not in selected:
            raise ValueError("Each filing requires one explicit unique source/proof selection")
        filings[key] = _filing(selected[key], selected[proof], descriptor)
    actions, covers, name_snapshots, instruments, blockers, parsed_blocks = [], [], [], [], [], {}
    for claim in request["action_claims"]:
        key = claim.get("source_key")
        if key not in filings:
            raise ValueError("Action source has no explicit filing/publication descriptor")
        source, filing = selected[key], filings[key]
        if source["body"] is None or filing["filing_on"] is None:
            blockers.append("ACTION_OR_PUBLICATION_ORIGINAL_UNAVAILABLE")
            continue
        if key not in parsed_blocks:
            parsed_blocks[key] = symbol_action_passages(source["body"])
        actions.append(_action(claim, source, filing, scope, parsed_blocks[key], selected, filings))
    for claim in request["cover_claims"]:
        key, role = claim.get("source_key"), claim.get("role")
        if key not in filings or role not in {"FROM", "TO", "NAME_ONLY"}:
            raise ValueError("Cover requires its exact filing and old/new role")
        source, filing = selected[key], filings[key]
        if source["body"] is None or filing["filing_on"] is None or filing["publication_at"] is None:
            blockers.append("COVER_OR_EXACT_PUBLICATION_TIMESTAMP_UNAVAILABLE")
            continue
        snapshot = _snapshot(source, filing, "ticker_filing")
        names = [row for row in issuer_names(snapshot) if row["context_ref"] == claim.get("context_ref")]
        if role == "NAME_ONLY":
            supported = (len(names) == 1 and names[0]["issuer_cik"] == scope["issuer_cik"]
                         and names[0]["issuer_name"] == claim.get("issuer_name"))
            name_snapshots.append({"source_key": key, "context_ref": claim.get("context_ref"), "name_facts": names,
                                   "supported": supported, "supplies_instrument_symbol_identity": False,
                                   "context_anchor_proves_legal_name_effective_day": False,
                                   "blockers": [] if supported else ["EXACT_DATED_ISSUER_NAME_CONTEXT_MISSING"]})
            continue
        securities = parse_ticker_filing(snapshot)
        exact = [row for row in securities if row["context_ref"] == claim.get("context_ref")]
        if len(exact) != 1 or len(names) != 1:
            blockers.append("EXACT_COVER_CIK_NAME_SYMBOL_CLASS_CONTEXT_MISSING")
            continue
        security, name = exact[0], names[0]
        reasons = []
        if (security["issuer_cik"] != scope["issuer_cik"] or name["issuer_cik"] != scope["issuer_cik"]
                or name["anchor_date"] != security["anchor_date"] or name["issuer_name"] != claim.get("issuer_name")
                or security["ticker"] != scope["from_symbol" if role == "FROM" else "to_symbol"]
                or class_terms(security["class_title"]) != class_terms(scope["class_title"])):
            reasons.append("EXACT_COVER_TUPLE_CONFLICT")
        covers.append({"source_key": key, "role": role, **security, "issuer_name": name["issuer_name"],
                       "context_anchor_proves_legal_name_effective_day": False,
                       "supported": not reasons, "blockers": reasons})
    for claim in request["instrument_claims"]:
        key, role = claim.get("source_key"), claim.get("role")
        if key not in filings or role not in {"FROM", "TO"}:
            raise ValueError("Instrument requires its exact filing and old/new role")
        source, filing = selected[key], filings[key]
        if source["body"] is None or filing["filing_on"] is None or filing["publication_at"] is None:
            blockers.append("INSTRUMENT_OR_EXACT_PUBLICATION_TIMESTAMP_UNAVAILABLE")
            continue
        fact = parse_schedule13(_snapshot(source, filing, "schedule13"))
        reasons = []
        paired = [row for row in covers if row["source_key"] == claim.get("cover_source_key")
                  and row["context_ref"] == claim.get("cover_context_ref") and row["role"] == role and row["supported"]]
        if fact["issuer_cik"] != scope["issuer_cik"] or fact["cusip"] != scope["cusip"]:
            reasons.append("EXACT_STRUCTURED_CIK_CUSIP_CONFLICT")
        if class_terms(fact["class_title"]) != class_terms(scope["class_title"]):
            reasons.append("EXACT_INSTRUMENT_CLASS_OR_PAR_TERM_MISSING")
        if len(paired) != 1 or (paired and (paired[0]["anchor_date"] != fact["event_date"]
                or _name(paired[0]["issuer_name"]) != _name(fact["issuer_name"]))):
            reasons.append("SAME_DAY_NAME_CLASS_SYMBOL_CUSIP_BRIDGE_MISSING")
        if claim.get("cover_source_key") in selected and source["accession"] == selected[claim["cover_source_key"]]["accession"]:
            reasons.append("INDEPENDENT_INSTRUMENT_COVER_ACCESSIONS_MISSING")
        instruments.append({"source_key": key, "role": role, **fact, "supported": not reasons, "blockers": reasons})
    completed = [row for row in actions if row["event_kind"] == "SYMBOL_CHANGE" and row["effective_action_supported"]]
    days = sorted({row["effective_on"] for row in completed})
    if len(days) != 1:
        blockers.append("EXACT_SYMBOL_EFFECTIVE_DAY_UNKNOWN" if not days else "COMPETING_SYMBOL_EFFECTIVE_DAYS")
    if len({row["accession"] for row in completed}) < 2:
        blockers.append("TWO_INDEPENDENT_EFFECTIVE_SYMBOL_DISCLOSURES_MISSING")
    planned_days = sorted({row["planned_effective_on"] for row in actions if row["event_kind"] == "SYMBOL_CHANGE"
                           and row["planned_effective_on"] is not None})
    if days and any(day not in days for day in planned_days):
        blockers.append("PLANNED_AND_REPORTED_EFFECTIVE_DAY_CONFLICT")
    if len(days) == 1:
        for row in instruments:
            if row["role"] == "FROM" and row["event_date"] >= days[0]:
                row["blockers"].append("FROM_INSTRUMENT_NOT_STRICTLY_PRIOR_TO_SYMBOL_ACTION")
                row["supported"] = False
            if row["role"] == "TO" and row["event_date"] < days[0]:
                row["blockers"].append("TO_INSTRUMENT_PRECEDES_SYMBOL_ACTION")
                row["supported"] = False
    for role in ("FROM", "TO"):
        if not any(row["role"] == role and row["supported"] for row in instruments):
            blockers.append(role + "_EXACT_DATED_INSTRUMENT_SYMBOL_TUPLE_MISSING")
    old_names = {_name(row["issuer_name"]) for row in covers if row["role"] == "FROM" and row["supported"]}
    new_names = {_name(row["issuer_name"]) for row in covers if row["role"] == "TO" and row["supported"]}
    transitions = {(_name(row["from_name"]),_name(row["to_name"])) for row in actions
                   if row["event_kind"] == "LEGAL_NAME_CHANGE" and row["effective_action_supported"]}
    if old_names and new_names and old_names != new_names and (len(old_names) != 1 or len(new_names) != 1
            or (next(iter(old_names)),next(iter(new_names))) not in transitions):
        blockers.append("INDEPENDENT_EXACT_LEGAL_NAME_CHANGE_BRIDGE_MISSING")
    continuity = [row for row in actions if row["event_kind"] == "CUSIP_CONTINUITY" and row["effective_action_supported"]
                  and row["effective_on"] in days]
    if not continuity:
        blockers.append("DATED_EXACT_CLASS_CUSIP_ACTION_CONTINUITY_MISSING")
    for rows in (actions, covers, name_snapshots, instruments, list(filings.values())):
        for row in rows:
            blockers.extend(row["blockers"])
    blockers = sorted(set(blockers))
    report = {"schema_version": VERSION, "status": "PRIMARY_SCOPE_READY_FOR_EXPLICIT_REVIEW" if not blockers else "PRIMARY_SCOPE_PARTIAL",
              "scope": dict(scope), "as_of": cutoff.isoformat(), "review_recorded_at": physical.isoformat(),
              "source_knowledge_at": max(row["known"] for row in selected.values()).isoformat(),
              "knowledge_at": physical.isoformat(),
              "filings": filings, "action_claims": actions, "dated_cover_tuples": covers,
              "dated_issuer_name_snapshots": name_snapshots, "instrument_claims": instruments,
              "supported_effective_symbol_days": days, "effective_on": days[0] if len(days) == 1 else None,
              "planned_effective_symbol_days": planned_days,
              "independent_effective_disclosures": len({row["accession"] for row in completed}), "blockers": blockers,
              "capture_pins": {key: {field: row[field] for field in ("url", "sha256", "bytes", "metadata_sha256")}
                               | {"observed_at": row["observed"].isoformat(), "known_at": row["known"].isoformat(),
                                  "body_available": row["body"] is not None} for key, row in selected.items()},
              "new_verified_identities": 0, "current_q3_verified_coverage": 0, "production_registry_changed": False,
              "automatic_alias_allowed": False, "interval_extension_allowed": False, "scoring_allowed": False,
              "historical_machine_availability_inferred": False, "historical_as_filed_versions_verified": False,
              "network_requests_performed": 0, "quota_or_lease_modified": False, "trading_allowed": False}
    report["review_sha256"] = hashlib.sha256(_encoded(report)).hexdigest()
    return report
