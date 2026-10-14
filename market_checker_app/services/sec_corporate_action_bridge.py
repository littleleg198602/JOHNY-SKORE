"""Independent primary-document bridge review; never an identity export.

The action date, issuer/instrument tuples, ticker contexts and exact class
terms are independent claims. A quarterly list/name join cannot bridge their
effective dates; a newer source cannot silently discard an older conflict.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
import json
import re
from urllib.parse import urlsplit

from market_checker_app.services.sec13f_identity_bulk import (
    _ActionPassageParser, _TickerCoverParser, _action_text, _clock, _dated_action_text, _literal_in, _name,
    _ticker, identity_version_sha256, parse_schedule13, parse_ticker_filing, share_class,
)
from market_checker_app.services.sec13f_scout_service import _valid_cusip

TUPLE_FIELDS = ("ticker", "issuer_cik", "cusip", "issuer_name", "class_description")


class _IssuerNameParser(_TickerCoverParser):
    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        attributes = dict(attrs)
        if tag == "ix:nonnumeric" and str(attributes.get("name", "")).casefold() == "dei:entityregistrantname":
            if (self.namespaces != {"dei", "ix", "xbrli"} or self.context
                    or not attributes.get("contextref") or attributes.get("continuedat")
                    or str(attributes.get("xsi:nil", "")).casefold() in {"true", "1"}
                    or any(capture[0] == tag for capture in self.captures)):
                raise ValueError("Incomplete independent issuer-name fact")
            self.captures.append([tag, attributes["contextref"], "dei:entityregistrantname", []])


def issuer_names(snapshot):
    parser = _IssuerNameParser()
    parser.feed(snapshot.payload.decode("utf-8-sig"))
    parser.close()
    if parser.captures or parser.context is not None or parser.namespaces != {"dei", "ix", "xbrli"}:
        raise ValueError("Incomplete independent issuer-name filing")
    result = []
    for context, values in parser.facts.items():
        names = values.get("dei:entityregistrantname", [])
        identity = parser.contexts.get(context, {})
        ciks = identity.get("xbrli:identifier", [])
        anchors = identity.get("xbrli:instant", []) + identity.get("xbrli:enddate", [])
        if len(names) == len(ciks) == len(anchors) == 1 and 1 <= len(names[0]) <= 300:
            result.append({"issuer_name": names[0], "issuer_cik": ciks[0].zfill(10),
                           "anchor_date": anchors[0], "context_ref": context})
    return result


def class_terms(title):
    """Keep explicit par terms; common-stock classification alone is incomplete."""
    if not isinstance(title, str) or not 1 <= len(title) <= 300:
        return None
    upper = " ".join(title.upper().split())
    no_par = bool(re.search(r"\b(?:WITHOUT|NO) PAR VALUE\b", upper))
    amounts = re.findall(r"\$([0-9]+(?:\.[0-9]+)?)\s*(?:PER SHARE\s*)?PAR VALUE", upper)
    amounts += re.findall(r"PAR VALUE\s*\$([0-9]+(?:\.[0-9]+)?)", upper)
    values = {str(Decimal(amount).normalize()) for amount in amounts}
    if len(values) > 1 or no_par and values:
        return None
    cleaned = re.sub(r"[,;]?\s*\(?\b(?:WITHOUT|NO) PAR VALUE\)?", "", upper).strip()
    equity = share_class(cleaned)
    if equity is None:
        return None
    return {"share_class": equity, "par_term": "NO_PAR" if no_par else
            "USD:" + next(iter(values)) if values else "UNSPECIFIED"}


def action_text(snapshot):
    """Support exact EX-4.(VI) envelope metadata without relaxing visible HTML."""
    try:
        return _action_text(snapshot)
    except ValueError:
        if snapshot.kind != "corporate_action_filing":
            raise
        payload = snapshot.payload.decode("utf-8-sig")
        envelope = re.fullmatch(
            r"\s*<DOCUMENT>\s*<TYPE>EX-[0-9]+\.\([IVXivx]+\)\s*<SEQUENCE>[0-9]+\s*"
            r"<FILENAME>([A-Za-z0-9_.-]+)\s*(?:<DESCRIPTION>[^\r\n<]*\r?\n)?"
            r"<TEXT>\s*(<HTML(?:\s[^>]*)?>.*</HTML>)\s*</TEXT>\s*</DOCUMENT>\s*",
            payload, re.I | re.S,
        )
        if envelope is None or envelope[1] != urlsplit(snapshot.url).path.rsplit("/", 1)[-1]:
            raise ValueError("Unsupported or mismatched corporate-action document envelope")
        parser = _ActionPassageParser()
        parser.feed(envelope[2])
        parser.close()
        if not parser.has_html or parser.stack:
            raise ValueError("Incomplete visible corporate-action document")
        return " ".join(" ".join(parser.parts).split())


def _snapshot(service, reference, kind):
    if reference is None:
        return None
    if not isinstance(reference, str) or not re.fullmatch(r"[a-f0-9]{64}", reference):
        raise ValueError("Malformed bridge source reference")
    snapshot = service.snapshots.get(reference)
    if snapshot is None or snapshot.kind != kind:
        raise ValueError("Bridge source is missing or has the wrong primary document kind")
    return snapshot


def _quote(snapshot, excerpt):
    if not isinstance(excerpt, str) or not 30 <= len(excerpt) <= 4_000:
        raise ValueError("Bridge requires a bounded exact primary passage")
    quote = " ".join(excerpt.split())
    if quote not in action_text(snapshot):
        raise ValueError("Bridge passage is absent from visible primary source text")
    return quote


def review_corporate_action_bridge(service, claim):
    if isinstance(claim, dict) and claim.get("bridge_kind") == "SAME_CIK_SYMBOL_RENAME":
        return review_symbol_rename_bridge(service, claim)
    if not isinstance(claim, dict) or claim.get("ticker") not in service.universe_set:
        raise ValueError("Bridge review requires a canonical ticker")
    previous = [row for row in service.existing
                if identity_version_sha256(row) == claim.get("from_identity_sha256")]
    if len(previous) != 1 or _ticker(previous[0]["ticker"]) != _ticker(claim["ticker"]):
        raise ValueError("Bridge does not bind the exact immutable prior identity version")
    previous = previous[0]
    if _clock(previous["known_at"]) > service.as_of:
        raise ValueError("Bridge cannot use a prior identity not yet known at review time")
    proposed = claim.get("to_identity")
    if (not isinstance(proposed, dict) or set(proposed) != set(TUPLE_FIELDS)
            or any(not isinstance(proposed[field], str) or not proposed[field] for field in TUPLE_FIELDS)
            or proposed["ticker"] != claim["ticker"] or not re.fullmatch(r"[0-9]{10}", proposed["issuer_cik"])
            or int(proposed["issuer_cik"]) == 0 or not _valid_cusip(proposed["cusip"])):
        raise ValueError("Bridge target needs an exact candidate CIK/CUSIP/ticker/class tuple")
    row = service.by_cusip.get(proposed["cusip"])
    if (row is None or "*D*" in row["flags"] or row["issuer_name"] != proposed["issuer_name"]
            or row["class_description"] != proposed["class_description"]):
        raise ValueError("Bridge target is not the active exact selected-quarter list row")
    catalog = {item["issuer_cik"] for item in service.catalog.get(_ticker(proposed["ticker"]), [])}
    if catalog != {proposed["issuer_cik"]}:
        raise ValueError("Bridge target CIK is unresolved or ambiguous in the observed catalog")
    action = _snapshot(service, claim.get("action_snapshot_id"), "corporate_action_filing")
    if action is None:
        raise ValueError("Bridge requires its archived primary action")
    action_cik = urlsplit(action.url).path.split("/")[4].zfill(10)
    if action_cik not in {previous["issuer_cik"], proposed["issuer_cik"]}:
        raise ValueError("Bridge action is filed by an unrelated issuer")
    excerpt = _quote(action, claim.get("action_excerpt"))
    effective = claim.get("effective_on")
    if not isinstance(effective, str) or date.fromisoformat(effective).isoformat() != effective:
        raise ValueError("Bridge requires an exact effective date")
    day = date.fromisoformat(effective)
    date_text = claim.get("effective_date_text")
    if (not isinstance(date_text, str) or not _dated_action_text(date_text, day)
            or not _literal_in(date_text, excerpt)
            or not date.fromisoformat(previous["effective_to"]) < day <= service.period[1]):
        raise ValueError("Action date conflicts with the primary passage or preserved prior interval")
    if any(not isinstance(claim.get(field), str) or not _literal_in(claim[field], excerpt)
           for field in ("from_name_text", "to_name_text", "from_class_title", "to_class_title")):
        raise ValueError("Bridge action does not quote both exact issuer names and security classes")
    blockers, proofs = [], {"action": {**action.metadata(), "snapshot_id": action.snapshot_id,
                                      "excerpt": excerpt, "effective_on": effective}}
    source_urls = {action.url}
    for role, identity in (("from", previous), ("to", proposed)):
        expected_terms = class_terms(claim[f"{role}_class_title"])
        if expected_terms is None or expected_terms["par_term"] == "UNSPECIFIED":
            blockers.append(f"{role.upper()}_ACTION_CLASS_TERMS_INCOMPLETE")
        if expected_terms is None or share_class(identity["class_description"]) != expected_terms["share_class"]:
            blockers.append(f"{role.upper()}_MANIFEST_CLASS_CONFLICT")
        instrument = _snapshot(service, claim.get(f"{role}_instrument_snapshot_id"), "schedule13")
        cover = _snapshot(service, claim.get(f"{role}_ticker_snapshot_id"), "ticker_filing")
        role_proofs = {"action_class_terms": expected_terms, "instrument": None, "ticker_class": None,
                       "other_class_conflicts": []}
        if instrument is None:
            blockers.append(f"{role.upper()}_CIK_CUSIP_CLASS_BINDING_MISSING")
        else:
            fact = parse_schedule13(instrument)
            role_proofs["instrument"] = {**fact, **instrument.metadata()}
            source_urls.add(instrument.url)
            anchor = date.fromisoformat(fact["event_date"])
            compatible_date = (date.fromisoformat(previous["effective_from"]) <= anchor < day
                               if role == "from" else day <= anchor <= service.period[1])
            terms = class_terms(fact["class_title"])
            if ((fact["issuer_cik"], fact["cusip"]) != (identity["issuer_cik"], identity["cusip"])
                    or not compatible_date or terms is None or expected_terms is None
                    or terms["share_class"] != expected_terms["share_class"]):
                blockers.append(f"{role.upper()}_STRUCTURED_INSTRUMENT_CONFLICT")
        if cover is None:
            blockers.append(f"{role.upper()}_TICKER_CIK_CLASS_BINDING_MISSING")
        else:
            matches = [fact for fact in parse_ticker_filing(cover)
                       if fact["issuer_cik"] == identity["issuer_cik"] and _ticker(fact["ticker"]) == _ticker(identity["ticker"])]
            names = [fact for fact in issuer_names(cover) if fact["issuer_cik"] == identity["issuer_cik"]
                     and len(matches) == 1 and fact["anchor_date"] == matches[0]["anchor_date"]]
            role_proofs["independent_issuer_names"] = names
            if not any(_name(fact["issuer_name"]) == _name(claim[f"{role}_name_text"]) for fact in names):
                blockers.append(f"{role.upper()}_INDEPENDENT_ISSUER_NAME_BINDING_MISSING")
            source_urls.add(cover.url)
            if len(matches) != 1:
                blockers.append(f"{role.upper()}_TICKER_CONTEXT_NOT_UNIQUE")
            else:
                fact = matches[0]
                role_proofs["ticker_class"] = {**fact, **cover.metadata(), "class_terms": class_terms(fact["class_title"])}
                anchor = date.fromisoformat(fact["anchor_date"])
                in_scope = (date.fromisoformat(previous["effective_from"]) <= anchor < day
                            if role == "from" else day <= anchor <= service.period[1])
                if not in_scope or class_terms(fact["class_title"]) != expected_terms:
                    blockers.append(f"{role.upper()}_TICKER_CLASS_OR_DATE_CONFLICT")
        for fact in service.covers:
            anchor = date.fromisoformat(fact["anchor_date"])
            if (fact["issuer_cik"] == identity["issuer_cik"] and _ticker(fact["ticker"]) == _ticker(identity["ticker"])
                    and (day <= anchor <= service.period[1] if role == "to" else
                         date.fromisoformat(previous["effective_from"]) <= anchor < day)
                    and class_terms(fact["class_title"]) != expected_terms):
                role_proofs["other_class_conflicts"].append(deepcopy(fact))
        if role_proofs["other_class_conflicts"]:
            blockers.append(f"CONFLICTING_{role.upper()}_CLASS_SOURCE")
        if instrument and cover and len({instrument.url, cover.url, action.url}) != 3:
            blockers.append(f"{role.upper()}_INDEPENDENT_PRIMARY_DOCUMENTS_MISSING")
        proofs[role] = role_proofs
    cusip_action = _snapshot(service, claim.get("cusip_bridge_snapshot_id"), "corporate_action_filing")
    proofs["cusip_effective_bridge"] = None
    if cusip_action is None:
        blockers.append("DATED_OLD_NEW_CUSIP_ACTION_BRIDGE_MISSING")
    else:
        quote = _quote(cusip_action, claim.get("cusip_bridge_excerpt"))
        if (urlsplit(cusip_action.url).path.split("/")[4].zfill(10) not in {previous["issuer_cik"], proposed["issuer_cik"]}
                or not all(_literal_in(value, quote) for value in (previous["cusip"], proposed["cusip"], date_text))):
            raise ValueError("CUSIP action passage does not bind both exact identifiers and effective date")
        source_urls.add(cusip_action.url)
        proofs["cusip_effective_bridge"] = {**cusip_action.metadata(), "snapshot_id": cusip_action.snapshot_id, "excerpt": quote}
    latest = max(_clock(previous["known_at"]), *[snapshot.observed_at for snapshot in service.snapshots.values()])
    report = {"schema_version": 1, "status": "PRIMARY_BRIDGE_PARTIAL" if blockers else "PRIMARY_BRIDGE_READY_FOR_EXPLICIT_REVIEW",
              "ticker": claim["ticker"], "prior_identity_sha256": identity_version_sha256(previous),
              "preserved_prior_identity": deepcopy(previous), "candidate_target_tuple": deepcopy(proposed),
              "effective_on": effective, "evidence_known_at": latest.isoformat(), "proofs": proofs,
              "blockers": list(dict.fromkeys(blockers)), "independent_primary_urls": sorted(source_urls),
              "identity_transition_approved": False, "interval_extension_allowed": False,
              "automatic_alias_allowed": False, "production_registry_changed": False, "scoring_allowed": False}
    report["bridge_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return report


def _symbol_tuple(value):
    if (not isinstance(value, dict) or set(value) != set(TUPLE_FIELDS)
            or any(not isinstance(value[field], str) or not 1 <= len(value[field]) <= 300 for field in TUPLE_FIELDS)
            or not re.fullmatch(r"[A-Z][A-Z0-9./-]{0,14}", value["ticker"])
            or not re.fullmatch(r"[0-9]{10}", value["issuer_cik"]) or int(value["issuer_cik"]) == 0
            or not _valid_cusip(value["cusip"]) or not _name(value["issuer_name"])
            or share_class(value["class_description"]) is None):
        raise ValueError("Symbol bridge needs exact unverified CIK/CUSIP/class/name/symbol tuples")
    return value


def _symbol_change_in(passage, old, new):
    # An isolated one-letter symbol or a heading/table cannot prove a rename.
    # This deliberately small vocabulary requires an explicit directed claim.
    pattern = (r"\b(?:ticker|trading)\s+symbol\s+(?:(?:was|is|will be)\s+)?(?:changed\s+)?from\s+"
               r"[\"'“‘(]*" + re.escape(old) + r"[\"'”’)]*\s+to\s+[\"'“‘(]*" + re.escape(new)
               + r"(?![A-Za-z0-9/-]|\.[A-Za-z0-9])")
    return (_literal_in(old, passage) and _literal_in(new, passage)
            and re.search(pattern, passage, re.I) is not None)


def review_symbol_rename_bridge(service, claim):
    """Review two dated symbols without inventing a prior registry identity.

    The target may be outside the canonical input (e.g. PSTG -> P), but it
    never enters that input. Unverified tuples remain proposals even when
    every independent bridge is ready for an explicit human review.
    """
    if (not isinstance(claim, dict) or claim.get("bridge_kind") != "SAME_CIK_SYMBOL_RENAME"
            or claim.get("ticker") not in service.universe_set):
        raise ValueError("Symbol bridge requires its explicit kind and canonical old ticker")
    previous, proposed = _symbol_tuple(claim.get("from_identity")), _symbol_tuple(claim.get("to_identity"))
    if (previous["ticker"] != claim["ticker"] or _ticker(previous["ticker"]) == _ticker(proposed["ticker"])
            or previous["issuer_cik"] != proposed["issuer_cik"] or previous["cusip"] != proposed["cusip"]):
        raise ValueError("Same-CIK symbol rename requires distinct symbols and an unchanged exact CIK/CUSIP")
    preserved = [row for row in service.existing if row["ticker"] == claim["ticker"]]
    if any(_clock(row["known_at"]) > service.as_of for row in preserved):
        raise ValueError("Symbol bridge contains future prior-identity knowledge")
    if any(snapshot.observed_at > service.as_of for snapshot in service.snapshots.values()):
        raise ValueError("Symbol bridge contains future source knowledge")
    row = service.by_cusip.get(proposed["cusip"])
    if (row is None or "*D*" in row["flags"] or row["issuer_name"] != proposed["issuer_name"]
            or row["class_description"] != proposed["class_description"]):
        raise ValueError("Symbol target is not the active exact selected-quarter list row")
    action = _snapshot(service, claim.get("action_snapshot_id"), "corporate_action_filing")
    if action is None or urlsplit(action.url).path.split("/")[4].zfill(10) != previous["issuer_cik"]:
        raise ValueError("Symbol action must be archived by the exact unchanged issuer")
    excerpt = _quote(action, claim.get("action_excerpt"))
    blockers, source_urls = [], {action.url}
    catalog = service.catalog.get(_ticker(proposed["ticker"]), [])
    if {item["issuer_cik"] for item in catalog} != {proposed["issuer_cik"]}:
        blockers.append("TO_OBSERVED_CATALOG_CIK_UNRESOLVED")
    if not _symbol_change_in(excerpt, previous["ticker"], proposed["ticker"]):
        blockers.append("DIRECTED_OLD_NEW_SYMBOL_ACTION_MISSING")
    effective, date_text = claim.get("effective_on"), claim.get("effective_date_text")
    day = None
    if effective is None and date_text is None:
        blockers.append("SYMBOL_ACTION_EFFECTIVE_DAY_MISSING")
    else:
        if (not isinstance(effective, str) or date.fromisoformat(effective).isoformat() != effective
                or not isinstance(date_text, str) or not _dated_action_text(date_text, date.fromisoformat(effective))):
            raise ValueError("Symbol action needs an exact date pair or an explicitly missing day")
        candidate_day = date.fromisoformat(effective)
        if not _literal_in(date_text, excerpt):
            blockers.append("SYMBOL_ACTION_EFFECTIVE_DAY_NOT_PROVEN")
        elif candidate_day > action.published_at.date() or candidate_day > service.period[1]:
            blockers.append("SYMBOL_ACTION_EFFECTIVE_DAY_CLOCK_CONFLICT")
        else:
            day = candidate_day
    proofs = {"action": {**action.metadata(), "snapshot_id": action.snapshot_id, "excerpt": excerpt,
                          "effective_on": day.isoformat() if day else None},
              "observed_catalog": deepcopy(catalog), "selected_quarter_list_row": deepcopy(row)}
    terms_by_role, anchors_by_role = {}, {}
    for role, identity in (("from", previous), ("to", proposed)):
        name_text, title = claim.get(f"{role}_name_text"), claim.get(f"{role}_class_title")
        terms = class_terms(title)
        terms_by_role[role] = terms
        if not isinstance(name_text, str) or _name(name_text) != _name(identity["issuer_name"]):
            raise ValueError("Symbol role must name its exact proposed issuer")
        if not _literal_in(name_text, excerpt):
            blockers.append(f"{role.upper()}_ACTION_ISSUER_NAME_BINDING_MISSING")
        if terms is None or terms["par_term"] == "UNSPECIFIED":
            blockers.append(f"{role.upper()}_ACTION_CLASS_TERMS_INCOMPLETE")
        if terms is None or terms["share_class"] != share_class(identity["class_description"]):
            blockers.append(f"{role.upper()}_MANIFEST_CLASS_CONFLICT")
        if not isinstance(title, str) or not _literal_in(title, excerpt):
            blockers.append(f"{role.upper()}_ACTION_CLASS_BINDING_MISSING")
        instrument = _snapshot(service, claim.get(f"{role}_instrument_snapshot_id"), "schedule13")
        cover = _snapshot(service, claim.get(f"{role}_ticker_snapshot_id"), "ticker_filing")
        role_proofs = {"action_class_terms": terms, "instrument": None, "ticker_class": None,
                       "independent_issuer_names": [], "competing_name_or_class_sources": [],
                       "competing_symbol_date_sources": []}
        if instrument is None:
            blockers.append(f"{role.upper()}_CIK_CUSIP_CLASS_BINDING_MISSING")
        else:
            fact = parse_schedule13(instrument)
            role_proofs["instrument"] = {**fact, **instrument.metadata()}
            source_urls.add(instrument.url)
            instrument_terms = class_terms(fact["class_title"])
            anchor = date.fromisoformat(fact["event_date"])
            correct_side = anchor < day if role == "from" and day else anchor >= day if day else True
            if (fact["issuer_cik"] != identity["issuer_cik"] or fact["cusip"] != identity["cusip"]
                    or _name(fact["issuer_name"]) != _name(name_text) or instrument_terms is None or terms is None
                    or instrument_terms["share_class"] != terms["share_class"]
                    or instrument_terms["par_term"] not in {"UNSPECIFIED", terms["par_term"]}
                    or not correct_side or anchor > service.period[1]):
                blockers.append(f"{role.upper()}_STRUCTURED_INSTRUMENT_CONFLICT")
        if cover is None:
            blockers.append(f"{role.upper()}_TICKER_CIK_CLASS_BINDING_MISSING")
        else:
            matches = [fact for fact in parse_ticker_filing(cover)
                       if fact["issuer_cik"] == identity["issuer_cik"] and fact["ticker"] == identity["ticker"]]
            source_urls.add(cover.url)
            if len(matches) != 1:
                blockers.append(f"{role.upper()}_TICKER_CONTEXT_NOT_UNIQUE")
            else:
                fact = matches[0]
                role_proofs["ticker_class"] = {**fact, **cover.metadata(), "class_terms": class_terms(fact["class_title"])}
                anchor = date.fromisoformat(fact["anchor_date"])
                names = [item for item in issuer_names(cover) if item["issuer_cik"] == identity["issuer_cik"]
                         and item["anchor_date"] == fact["anchor_date"] and item["context_ref"] == fact["context_ref"]]
                role_proofs["independent_issuer_names"] = names
                if len(names) != 1 or _name(names[0]["issuer_name"]) != _name(name_text):
                    blockers.append(f"{role.upper()}_INDEPENDENT_ISSUER_NAME_BINDING_MISSING")
                correct_side = anchor < day if role == "from" and day else anchor >= day if day else True
                if not correct_side or anchor > service.period[1] or class_terms(fact["class_title"]) != terms:
                    blockers.append(f"{role.upper()}_TICKER_CLASS_OR_DATE_CONFLICT")
        anchors = []
        for fact in service.covers:
            anchor = date.fromisoformat(fact["anchor_date"])
            if (fact["issuer_cik"] != identity["issuer_cik"] or fact["ticker"] != identity["ticker"]
                    or anchor > service.period[1]):
                continue
            anchors.append(anchor)
            source = service.snapshots[fact["snapshot_id"]]
            if day and not (anchor < day if role == "from" else anchor >= day):
                role_proofs["competing_symbol_date_sources"].append({**deepcopy(fact), **source.metadata()})
                source_urls.add(source.url)
            names = [item for item in issuer_names(source) if item["issuer_cik"] == identity["issuer_cik"]
                     and item["anchor_date"] == fact["anchor_date"] and item["context_ref"] == fact["context_ref"]]
            if (class_terms(fact["class_title"]) != terms or len(names) != 1
                    or _name(names[0]["issuer_name"]) != _name(name_text)):
                role_proofs["competing_name_or_class_sources"].append({**deepcopy(fact), **source.metadata(),
                                                                       "issuer_names": names})
                source_urls.add(source.url)
        anchors_by_role[role] = anchors
        if role_proofs["competing_name_or_class_sources"]:
            blockers.append(f"CONFLICTING_{role.upper()}_NAME_OR_CLASS_SOURCE")
        if role_proofs["competing_symbol_date_sources"]:
            blockers.append(f"CONFLICTING_{role.upper()}_SYMBOL_DATE_SOURCE")
        role_urls = [source.url for source in (instrument, cover, action) if source]
        if len(role_urls) != len(set(role_urls)):
            blockers.append(f"{role.upper()}_INDEPENDENT_PRIMARY_DOCUMENTS_MISSING")
        proofs[role] = role_proofs
    if terms_by_role["from"] != terms_by_role["to"]:
        blockers.append("SAME_CIK_RENAME_CLASS_CHANGED")
    old_anchors, new_anchors = anchors_by_role["from"], anchors_by_role["to"]
    bracket = {"latest_old_anchor": max(old_anchors).isoformat() if old_anchors else None,
               "earliest_new_anchor": min(new_anchors).isoformat() if new_anchors else None,
               "establishes_effective_day": False}
    if old_anchors and new_anchors and max(old_anchors) >= min(new_anchors):
        blockers.append("COMPETING_SYMBOL_ANCHOR_ORDER")
    for field, kind in (("ticker_snapshot_id", "ticker_filing"), ("instrument_snapshot_id", "schedule13")):
        old_source = _snapshot(service, claim.get(f"from_{field}"), kind)
        new_source = _snapshot(service, claim.get(f"to_{field}"), kind)
        if old_source and new_source and old_source.url == new_source.url:
            blockers.append("OLD_NEW_INDEPENDENT_DOCUMENTS_MISSING")
    cusip_action = _snapshot(service, claim.get("cusip_bridge_snapshot_id"), "corporate_action_filing")
    proofs["cusip_effective_bridge"] = None
    if cusip_action is None:
        blockers.append("DATED_SYMBOL_CUSIP_ACTION_BRIDGE_MISSING")
    else:
        quote = _quote(cusip_action, claim.get("cusip_bridge_excerpt"))
        if (urlsplit(cusip_action.url).path.split("/")[4].zfill(10) != previous["issuer_cik"]
                or not _literal_in(previous["cusip"], quote)
                or not _symbol_change_in(quote, previous["ticker"], proposed["ticker"])
                or day is None or not _literal_in(date_text, quote) or day > cusip_action.published_at.date()):
            blockers.append("DATED_SYMBOL_CUSIP_ACTION_BRIDGE_NOT_PROVEN")
        source_urls.add(cusip_action.url)
        proofs["cusip_effective_bridge"] = {**cusip_action.metadata(), "snapshot_id": cusip_action.snapshot_id,
                                           "excerpt": quote}
    report = {"schema_version": 1, "bridge_kind": "SAME_CIK_SYMBOL_RENAME",
              "status": "PRIMARY_BRIDGE_PARTIAL" if blockers else "PRIMARY_BRIDGE_READY_FOR_EXPLICIT_REVIEW",
              "ticker": claim["ticker"], "from_identity_proposal": deepcopy(previous),
              "candidate_target_tuple": deepcopy(proposed), "tuple_status": "UNVERIFIED_PRIMARY_CANDIDATES",
              "preserved_prior_identities": deepcopy(preserved), "effective_on": day.isoformat() if day else None,
              "proposed_effective_on": effective, "observed_symbol_anchor_bracket": bracket,
              "evidence_known_at": max(*[snapshot.observed_at for snapshot in service.snapshots.values()],
                                       *[_clock(row["known_at"]) for row in preserved]).isoformat(),
              "as_of": service.as_of.isoformat(), "proofs": proofs, "blockers": list(dict.fromkeys(blockers)),
              "independent_primary_urls": sorted(source_urls), "historical_knowledge_reconstructed": False,
              "identity_transition_approved": False, "interval_extension_allowed": False, "automatic_alias_allowed": False,
              "canonical_universe_changed": False, "production_registry_changed": False, "scoring_allowed": False}
    report["bridge_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return report
