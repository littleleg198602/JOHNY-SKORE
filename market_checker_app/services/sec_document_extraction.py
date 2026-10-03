from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from html.parser import HTMLParser
import math
import re
from typing import Mapping

from market_checker_app.services.sec13f_scout_service import load_verified_securities


class _ReadableText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._hidden += 1
        elif tag in {"p", "br", "div", "tr", "h1", "h2", "h3"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._hidden:
            self._hidden -= 1
        elif tag in {"p", "div", "tr", "h1", "h2", "h3"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data)


def extract_sec_item_excerpts(document: bytes, *, form: str) -> tuple[tuple[str, str], ...]:
    """Return bounded, cited 8-K sections for review, not interpreted claims."""
    if form.upper().removesuffix("/A") != "8-K":
        return ()
    parser = _ReadableText()
    parser.feed(document.decode("utf-8", errors="replace"))
    plain = " ".join(" ".join(parser.parts).split())
    pattern = re.compile(r"\bItem\s+(\d{1,2}\.\d{2})\b", re.IGNORECASE)
    matches = list(pattern.finditer(plain))
    excerpts: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, match in enumerate(matches):
        code = match.group(1)
        if code in seen:
            continue
        seen.add(code)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(plain)
        excerpt = plain[match.end():min(end, match.end() + 700)].strip(" .:-")
        if excerpt:
            excerpts.append((f"Item {code}", excerpt))
        if len(excerpts) >= 3:
            break
    return tuple(excerpts)


def readable_sec_text(document: bytes, *, max_characters: int = 500_000) -> str:
    """Bounded text for source-linked exposure discovery; excludes scripts/styles."""
    parser = _ReadableText()
    parser.feed(document.decode("utf-8", errors="replace"))
    return " ".join(" ".join(parser.parts).split())[:max_characters]


def _valid_cusip(value: str) -> bool:
    if not re.fullmatch(r"[A-Z0-9]{8}[0-9]", value):
        return False
    total = 0
    for index, character in enumerate(value[:8], start=1):
        number = int(character) if character.isdigit() else ord(character) - ord("A") + 10
        if index % 2 == 0:
            number *= 2
        total += number // 10 + number % 10
    return (10 - total % 10) % 10 == int(value[-1])


def _number(value: str) -> float | None:
    try:
        parsed = float(value.replace(",", ""))
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0.0 else None


def extract_schedule_13_ownership(document: bytes, *, form: str) -> dict[str, object] | None:
    """Extract bounded Schedule 13D/G cover fields without inferring a change.

    The SEC primary document is authoritative for the as-filed cover values, but
    a reporting-person name is not a resolved legal identity and one filing does
    not establish a change from an earlier position.  Callers must preserve those
    distinctions and must not use this output for scoring.
    """
    base_form = form.upper().removesuffix("/A")
    if base_form not in {"SC 13D", "SC 13G"}:
        return None
    plain = readable_sec_text(document, max_characters=500_000)
    if not re.search(r"\bSCHEDULE\s+13[DG]\b", plain, re.IGNORECASE):
        return None

    class_match = re.search(
        r"\(Name of Issuer\)\s+(.{1,300}?)\s+\(Title of Class of Securities\)",
        plain, re.IGNORECASE,
    )
    class_title = class_match.group(1).strip(" .|:-") if class_match else None
    cusip_match = re.search(
        r"\(Title of Class of Securities\)\s+(.{1,100}?)\s+\(CUSIP Number\)",
        plain, re.IGNORECASE,
    )
    cusips: list[str] = []
    if cusip_match:
        for candidate in re.findall(r"[A-Z0-9]{9}", cusip_match.group(1).upper()):
            if _valid_cusip(candidate) and candidate not in cusips:
                cusips.append(candidate)
    event_match = re.search(
        r"\(Name, Address and Telephone Number of Person Authorized to Receive Notices and "
        r"Communications\)\s+(\d{2}/\d{2}/\d{4})\s+\(Date of Event Which Requires Filing",
        plain, re.IGNORECASE,
    )
    event_date = None
    if event_match:
        try:
            event_date = datetime.strptime(event_match.group(1), "%m/%d/%Y").date().isoformat()
        except ValueError:
            pass

    person_pattern = re.compile(
        r"\b1\s*(?:\|\s*)?Name of reporting person\s+",
        re.IGNORECASE,
    )
    starts = list(person_pattern.finditer(plain))[:32]
    people: list[dict[str, object]] = []
    for position, match in enumerate(starts):
        end = starts[position + 1].start() if position + 1 < len(starts) else len(plain)
        block = plain[match.end():end]
        name_match = re.match(
            r"(.{1,300}?)\s+2\s*(?:\|\s*)?Check the appropriate box",
            block, re.IGNORECASE,
        )
        if not name_match:
            continue
        name = name_match.group(1).strip(" .|:-")
        if not name:
            continue
        shares_match = re.search(
            r"\b11\s*(?:\|\s*)?Aggregate amount beneficially owned by each reporting "
            r"person\s+([0-9][0-9,]*(?:\.\d+)?)",
            block, re.IGNORECASE,
        )
        percent_match = re.search(
            r"\b13\s*(?:\|\s*)?Percent of class represented by amount in Row\s*\(11\)\s+"
            r"([0-9]*\.?[0-9]+)\s*%",
            block, re.IGNORECASE,
        )
        row: dict[str, object] = {
            "name": name,
            "identity_status": "NAME_ONLY",
        }
        if shares_match and (shares := _number(shares_match.group(1))) is not None:
            row["aggregate_beneficial_shares"] = shares
        if percent_match and (percent := _number(percent_match.group(1))) is not None:
            if percent <= 100.0:
                row["percent_of_class"] = percent
        people.append(row)

    if not people and not cusips and not class_title:
        return None
    return {
        "parser_version": "schedule-13-cover-v1",
        "form": base_form,
        "event_date": event_date,
        "instrument": {
            "class_title": class_title,
            "cusips": cusips,
            "identity_status": "AS_FILED_NOT_REGISTRY_MATCHED",
        },
        "reporting_persons": people,
        "reporting_person_identity_verified": False,
        "instrument_identity_verified": False,
        "ownership_change_interpreted": False,
        "scoring_applied": False,
    }


def match_schedule_13_instrument(
    ownership: Mapping[str, object] | None,
    *,
    ticker: str,
    issuer_cik: str,
    knowledge_at: datetime,
    securities: list[dict] | None = None,
) -> dict[str, object] | None:
    """Attach one exact, dated SEC registry match without resolving the owner.

    A checksum-valid CUSIP from the filing is still only an as-filed identifier.
    It becomes registry matched here only when exactly one reviewed SEC 13F
    manifest row agrees on canonical ticker, issuer CIK, CUSIP and effective
    date, and that row was already known at ``knowledge_at``.  This deliberately
    does not interpret an ownership change or verify a reporting person.
    """
    if ownership is None:
        return None
    result = deepcopy(dict(ownership))
    instrument = result.get("instrument")
    if not isinstance(instrument, Mapping):
        return result
    cusips = instrument.get("cusips")
    event_value = result.get("event_date")
    if (not isinstance(cusips, list) or len(cusips) != 1
            or not isinstance(cusips[0], str)
            or not isinstance(event_value, str)
            or knowledge_at.tzinfo is None or knowledge_at.utcoffset() is None):
        return result
    try:
        event_date = date.fromisoformat(event_value)
    except ValueError:
        return result
    normalized_ticker = ticker.strip().upper()
    normalized_cik = issuer_cik.strip().zfill(10)
    matches = []
    for entry in securities if securities is not None else load_verified_securities():
        try:
            known_at = datetime.fromisoformat(str(entry["known_at"]))
            effective_from = date.fromisoformat(str(entry["effective_from"]))
            effective_to = date.fromisoformat(str(entry["effective_to"]))
        except (KeyError, TypeError, ValueError):
            continue
        if known_at.tzinfo is None or known_at.utcoffset() is None:
            continue
        if (
            known_at <= knowledge_at
            and effective_from <= event_date <= effective_to
            and str(entry.get("ticker", "")).strip().upper() == normalized_ticker
            and str(entry.get("issuer_cik", "")).strip().zfill(10) == normalized_cik
            and str(entry.get("cusip", "")).strip().upper() == cusips[0].upper()
        ):
            matches.append(entry)
    if len(matches) != 1:
        return result
    match = matches[0]
    resolved_instrument = dict(instrument)
    resolved_instrument.update({
        "identity_status": "REGISTRY_MATCHED",
        "registry_match": {
            key: match[key]
            for key in (
                "ticker", "issuer_cik", "cusip", "issuer_name",
                "class_description", "effective_from", "effective_to", "known_at",
                "cusip_evidence_url", "instrument_evidence_url", "ticker_evidence_url",
            )
        },
    })
    result["instrument"] = resolved_instrument
    result["instrument_identity_verified"] = True
    return result
