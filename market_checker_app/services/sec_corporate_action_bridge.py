"""Independent primary-document bridge review; never an identity export.

The action date, issuer/instrument tuples, ticker contexts and exact class
terms are independent claims. A quarterly list/name join cannot bridge their
effective dates; a newer source cannot silently discard an older conflict.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import re
from urllib.parse import urlsplit
from xml.etree import ElementTree

from market_checker_app.services.sec13f_identity_bulk import (
    _ActionPassageParser, _TickerCoverParser, _action_text, _clock, _dated_action_text, _literal_in, _name,
    _ticker, identity_version_sha256, official_url, parse_schedule13, parse_ticker_filing, share_class,
)
from market_checker_app.services.sec13f_scout_service import _valid_cusip

TUPLE_FIELDS = ("ticker", "issuer_cik", "cusip", "issuer_name", "class_description")


class _IssuerNameParser(_TickerCoverParser):
    def __init__(self):
        super().__init__()
        self.namespace_bindings = {}
        self.identity_dimensions = {}
        self.dimension_containers = set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        for key, value in attributes.items():
            if key.startswith("xmlns:"):
                prefix = key.split(":", 1)[1]
                if prefix in self.namespace_bindings and self.namespace_bindings[prefix] != value:
                    raise ValueError("Rebound independent issuer namespace")
                self.namespace_bindings[prefix] = value
        if self.context and any(capture[2].startswith("issuer_dimension:") for capture in self.captures):
            raise ValueError("Nested independent issuer dimension")
        super().handle_starttag(tag, attrs)
        if self.context and tag in {"xbrli:segment", "xbrli:scenario"}:
            self.dimension_containers.add(self.context)
        if self.context and tag in {"xbrldi:explicitmember", "xbrldi:typedmember"}:
            dimensions = self.identity_dimensions.setdefault(self.context, [])
            key = f"issuer_dimension:{len(dimensions)}"
            shape = self.context_stack in (
                ["xbrli:context", "xbrli:entity", "xbrli:segment", tag],
                ["xbrli:context", "xbrli:scenario", tag],
            )
            dimensions.append({"kind": tag, "axis": attributes.get("dimension"), "key": key, "shape": shape})
            if tag == "xbrldi:explicitmember":
                self.captures.append([tag, self.context, key, []])
        fact = str(attributes.get("name", "")).casefold()
        if fact in {"dei:entityregistrantname", "dei:entitycentralindexkey"}:
            label = "issuer-name" if fact.endswith("entityregistrantname") else "issuer-CIK"
            if (self.namespaces != {"dei", "ix", "xbrli"} or self.context
                    or tag != "ix:nonnumeric" or attributes.get("format") or attributes.get("escape")
                    or not attributes.get("contextref") or attributes.get("continuedat")
                    or str(attributes.get("xsi:nil", "")).casefold() in {"true", "1"}
                    or any(capture[0] == tag for capture in self.captures)):
                raise ValueError(f"Incomplete independent {label} fact")
            self.captures.append([tag, attributes["contextref"], fact, []])


def _issuer_anchor(identity, snapshot):
    instant, start, end = [identity.get(key, []) for key in ("xbrli:instant", "xbrli:startdate", "xbrli:enddate")]
    if len(instant) == 1 and not start and not end:
        text = instant[0]
    elif not instant and len(start) == len(end) == 1:
        if start[0] != date.fromisoformat(start[0]).isoformat() or date.fromisoformat(start[0]) > date.fromisoformat(end[0]):
            return None
        text = end[0]
    else:
        return None
    if text != date.fromisoformat(text).isoformat():
        return None
    anchor = date.fromisoformat(text)
    published = getattr(snapshot, "published_at", None)
    published_on = getattr(snapshot, "published_on", None)
    observed = getattr(snapshot, "observed_at", None)
    if published is not None and anchor > _clock(published).date():
        return None
    if published_on is not None and anchor > date.fromisoformat(str(published_on)):
        return None
    if observed is not None and anchor > _clock(observed).date():
        return None
    return text


def _legal_entity_dimension(parser, context):
    dimensions = parser.identity_dimensions.get(context, [])
    if context not in parser.dimension_containers and not dimensions:
        return "NONE"
    if len(dimensions) != 1 or dimensions[0]["kind"] != "xbrldi:explicitmember" or not dimensions[0]["shape"]:
        return "UNSUPPORTED"
    dimension = dimensions[0]
    if parser.namespace_bindings.get("xbrldi") != "http://xbrl.org/2006/xbrldi":
        return "UNSUPPORTED"
    axis = dimension["axis"]
    if not isinstance(axis, str) or axis != "dei:LegalEntityAxis":
        return "UNSUPPORTED"
    members = parser.contexts[context].get(dimension["key"], [])
    if len(members) != 1 or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*:[A-Za-z_][A-Za-z0-9_.-]*", members[0]) is None:
        return "UNSUPPORTED"
    prefix = members[0].split(":", 1)[0]
    namespace = parser.namespace_bindings.get(prefix)
    if not isinstance(namespace, str) or not namespace.startswith(("http://", "https://")):
        return "UNSUPPORTED"
    return "EXPLICIT_LEGAL_ENTITY"


def issuer_names(snapshot):
    """Pair a name/date with its same-context DEI CIK, never a parent-only join.

    A sole unambiguous legacy registrant can use its undimensioned context
    identifier. A legal-entity dimension needs explicit DEI CIK; the member
    label never supplies a CIK or an issuer alias.
    """
    official_url("ticker_filing", snapshot.url)
    parser = _IssuerNameParser()
    parser.feed(snapshot.payload.decode("utf-8-sig"))
    parser.close()
    if parser.captures or parser.context is not None or parser.namespaces != {"dei", "ix", "xbrli"}:
        raise ValueError("Incomplete independent issuer-name filing")
    candidates, fallback_contexts = [], []
    archive_cik = urlsplit(snapshot.url).path.split("/")[4].zfill(10)
    name_contexts = [(context, values) for context, values in parser.facts.items() if values.get("dei:entityregistrantname")]
    fallback_unambiguous = True
    legacy_names, legacy_ciks = set(), set()
    def valid_cik(value):
        return isinstance(value, str) and re.fullmatch(r"[0-9]{1,10}", value) is not None and int(value) > 0

    for context, values in name_contexts:
        names = values.get("dei:entityregistrantname", [])
        identity = parser.contexts.get(context, {})
        root_ciks, explicit_ciks = identity.get("xbrli:identifier", []), values.get("dei:entitycentralindexkey", [])
        dimension = _legal_entity_dimension(parser, context)
        try:
            anchor = _issuer_anchor(identity, snapshot)
        except ValueError:
            anchor = None
        complete = (len(names) == len(root_ciks) == 1 and 1 <= len(names[0]) <= 300 and _name(names[0])
                    and valid_cik(root_ciks[0]) and anchor is not None and dimension != "UNSUPPORTED")
        if not complete:
            fallback_unambiguous = False
            continue
        root_cik = root_ciks[0].zfill(10)
        legacy_names.add(_name(names[0]))
        legacy_ciks.add(root_cik)
        fact = {"issuer_name": names[0], "anchor_date": anchor, "context_ref": context}
        if explicit_ciks:
            if len(explicit_ciks) != 1 or not valid_cik(explicit_ciks[0]):
                fallback_unambiguous = False
                continue
            explicit_cik = explicit_ciks[0].zfill(10)
            if dimension == "NONE" and explicit_cik != root_cik:
                fallback_unambiguous = False
                continue
            candidates.append({**fact, "issuer_cik": explicit_cik})
            if explicit_cik != root_cik or dimension != "NONE":
                fallback_unambiguous = False
        else:
            if dimension != "NONE" or root_cik != archive_cik:
                fallback_unambiguous = False
                continue
            fallback_contexts.append({**fact, "issuer_cik": root_cik})
    # A CIK declared elsewhere must not be silently moved to a name context.
    # Competing/unreadable declarations also make context-only fallback unsafe.
    for values in parser.facts.values():
        declared = values.get("dei:entitycentralindexkey", [])
        if declared and (len(declared) != 1 or not valid_cik(declared[0]) or declared[0].zfill(10) not in legacy_ciks):
            fallback_unambiguous = False
    if fallback_unambiguous and len(legacy_names) == len(legacy_ciks) == 1:
        candidates.extend(fallback_contexts)
    conflicts = {}
    for fact in candidates:
        conflicts.setdefault((fact["issuer_cik"], fact["anchor_date"]), set()).add(_name(fact["issuer_name"]))
    result = [fact for fact in candidates if len(conflicts[(fact["issuer_cik"], fact["anchor_date"])]) == 1]
    # Preserve source ordering and the existing four-field result shape.
    return sorted(result, key=lambda fact: list(parser.facts).index(fact["context_ref"]))


def class_terms(title):
    """Keep explicit par terms; common-stock classification alone is incomplete."""
    if not isinstance(title, str) or not 1 <= len(title) <= 300:
        return None
    upper = " ".join(title.upper().split())
    no_par = bool(re.search(r"\b(?:WITHOUT|NO) PAR VALUE\b", upper))
    decimal = r"([0-9]+(?:\.[0-9]+)?|\.[0-9]+)"
    amounts = re.findall(r"\$" + decimal + r"\s*(?:PER SHARE\s*)?PAR VALUE", upper)
    amounts += re.findall(r"PAR VALUE\s*\$" + decimal, upper)
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
    if isinstance(claim, dict) and claim.get("bridge_kind") == "STOCK_MERGER_EXCHANGE":
        return review_stock_merger_bridge(service, claim)
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


@dataclass(frozen=True)
class Form25Snapshot:
    """Exact raw exchange withdrawal XML with separate publication/capture clocks.

    Supports the normal captured-source interface. The bulk service retains
    its metadata in the source fingerprint without treating it as Schedule13.
    The archived submissions row must supply the 25-NSE publication clock.
    """
    url: str
    observed_at: datetime
    payload: bytes
    published_at: datetime

    def __post_init__(self):
        official_url("schedule13", self.url)  # Exact raw XML URL policy only.
        object.__setattr__(self, "observed_at", _clock(self.observed_at))
        object.__setattr__(self, "published_at", _clock(self.published_at))
        if not isinstance(self.payload, bytes) or not 0 < len(self.payload) <= 100_000:
            raise ValueError("Empty or unbounded original Form25 XML")
        if self.published_at > self.observed_at:
            raise ValueError("Form25 capture precedes its publication")
        from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body
        if _has_sensitive_body(self.payload):
            raise ValueError("Whole sensitive Form25 body is unavailable for review")

    @property
    def kind(self):
        return "form25_nse"

    @property
    def sha256(self):
        return hashlib.sha256(self.payload).hexdigest()

    def metadata(self):
        return {"kind": self.kind, "url": self.url, "sha256": self.sha256,
                "observed_at": self.observed_at.isoformat(), "published_at": self.published_at.isoformat()}

    @property
    def snapshot_id(self):
        return hashlib.sha256(json.dumps(self.metadata(), sort_keys=True).encode()).hexdigest()


def parse_form25(snapshot):
    if not isinstance(snapshot, Form25Snapshot) or re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", snapshot.payload, re.I):
        raise ValueError("Requires exact original exchange Form25 XML without DTD/entities")
    try:
        root = ElementTree.fromstring(snapshot.payload)
    except ElementTree.ParseError as error:
        raise ValueError("Incomplete original Form25 XML") from error
    if (root.tag != "notificationOfRemoval" or sum(1 for _ in root.iter()) > 200
            or any(not isinstance(node.tag, str) or "}" in node.tag for node in root.iter())):
        raise ValueError("Unsupported Form25 namespace or element budget")

    def one(parent, key):
        nodes = [node for node in parent if node.tag == key]
        if len(nodes) != 1:
            raise ValueError("Missing or ambiguous Form25 " + key)
        return nodes[0]

    def value(parent, key):
        node = one(parent, key)
        text = (node.text or "").strip()
        if len(node) or not 1 <= len(text) <= 300:
            raise ValueError("Malformed Form25 " + key)
        return text

    if value(root, "schemaVersion") != "X0203":
        raise ValueError("Unsupported Form25 schema version")
    issuer, exchange, signature = one(root, "issuer"), one(root, "exchange"), one(root, "signatureData")
    issuer_cik, exchange_cik = value(issuer, "cik"), value(exchange, "cik")
    signed = value(signature, "signatureDate")
    if (not all(re.fullmatch(r"[0-9]{1,10}", cik) and int(cik) > 0 for cik in (issuer_cik, exchange_cik))
            or issuer_cik.zfill(10) != urlsplit(snapshot.url).path.split("/")[4].zfill(10)
            or signed != date.fromisoformat(signed).isoformat() or date.fromisoformat(signed) > snapshot.published_at.date()):
        raise ValueError("Form25 issuer/archive/signature-clock conflict")
    return {"issuer_cik": issuer_cik.zfill(10), "issuer_name": value(issuer, "entityName"),
            "exchange_cik": exchange_cik.zfill(10), "exchange_name": value(exchange, "entityName"),
            "class_title": value(root, "descriptionClassSecurity"), "signature_date": signed,
            "rule_provision": value(root, "ruleProvision"), "snapshot_id": snapshot.snapshot_id,
            "source_scope": "FILED_WITHDRAWAL_NOTIFICATION_ONLY", "actual_delisting_effective_on": None}


def _stock_exchange_terms(passage, ratio):
    amounts = re.findall(r"(?:automatically\s+)?converted\s+into\s+the\s+right\s+to\s+receive\s+"
                         r"([0-9]+(?:\.[0-9]+)?)\s+shares\b", passage, re.I)
    fraction = re.search(r"\bcash\s+(?:paid\s+)?in\s+lieu\s+of\s+(?:any\s+)?fractional\s+shares\b", passage, re.I)
    return (len(amounts) == 1 and Decimal(amounts[0]) == Decimal(ratio)
            and fraction is not None and len(re.findall(r"\bcash\b", passage, re.I)) == 1)


def review_stock_merger_bridge(service, claim):
    """Separate filed stock-conversion facts from full instrument/delisting proof."""
    if (not isinstance(claim, dict) or claim.get("bridge_kind") != "STOCK_MERGER_EXCHANGE"
            or claim.get("ticker") not in service.universe_set):
        raise ValueError("Stock-merger review requires its explicit kind and canonical old ticker")
    previous, proposed = _symbol_tuple(claim.get("from_identity")), _symbol_tuple(claim.get("to_identity"))
    if previous["ticker"] != claim["ticker"] or previous["issuer_cik"] == proposed["issuer_cik"]:
        raise ValueError("Stock-merger proposals need the exact old ticker and a separate recipient issuer")
    ratio = claim.get("exchange_ratio")
    if (not isinstance(ratio, str) or re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", ratio) is None
            or not 0 < Decimal(ratio) <= 100 or claim.get("fractional_cash_only") is not True):
        raise ValueError("Stock-merger review requires exact positive share ratio and fractional-cash-only scope")
    if any(snapshot.observed_at > service.as_of for snapshot in service.snapshots.values()):
        raise ValueError("Stock-merger sources contain future knowledge")
    preserved = [row for row in service.existing if row["ticker"] == claim["ticker"]]
    if any(_clock(row["known_at"]) > service.as_of for row in preserved):
        raise ValueError("Stock-merger prior identity contains future knowledge")
    row = service.by_cusip.get(proposed["cusip"])
    if (row is None or "*D*" in row["flags"] or row["issuer_name"] != proposed["issuer_name"]
            or row["class_description"] != proposed["class_description"]):
        raise ValueError("Recipient proposal is not the active exact selected-quarter instrument row")
    old_row = service.by_cusip.get(previous["cusip"])
    catalog = service.catalog.get(_ticker(proposed["ticker"]), [])
    blockers, source_urls, proofs = [], set(), {"selected_old_list_row": deepcopy(old_row),
                                               "selected_target_list_row": deepcopy(row), "observed_target_catalog": deepcopy(catalog)}
    if old_row and (_name(old_row["issuer_name"]) != _name(previous["issuer_name"])
                    or share_class(old_row["class_description"]) != share_class(previous["class_description"])):
        blockers.append("FROM_SELECTED_LIST_NAME_OR_CLASS_CONFLICT")
    if {item["issuer_cik"] for item in catalog} != {proposed["issuer_cik"]}:
        blockers.append("RECIPIENT_OBSERVED_CATALOG_CIK_UNRESOLVED")
    effective, date_text = claim.get("effective_on"), claim.get("effective_date_text")
    candidate_day = None
    if effective is not None or date_text is not None:
        if (not isinstance(effective, str) or effective != date.fromisoformat(effective).isoformat()
                or not isinstance(date_text, str) or not _dated_action_text(date_text, date.fromisoformat(effective))):
            raise ValueError("Merger requires an exact effective date pair or explicitly missing dates")
        candidate_day = date.fromisoformat(effective)
    action = _snapshot(service, claim.get("action_snapshot_id"), "corporate_action_filing")
    completion = _snapshot(service, claim.get("completion_snapshot_id"), "corporate_action_filing")
    action_day, conversion_ok = None, False
    for role, source, identity in (("action", action, previous), ("completion", completion, proposed)):
        proofs[role] = None
        if source is None:
            blockers.append("MERGER_PRIMARY_ACTION_MISSING" if role == "action" else "INDEPENDENT_RECIPIENT_COMPLETION_MISSING")
            continue
        if urlsplit(source.url).path.split("/")[4].zfill(10) != identity["issuer_cik"]:
            raise ValueError("Merger action is filed by an unrelated exact issuer")
        excerpt = _quote(source, claim.get(f"{role}_excerpt"))
        source_urls.add(source.url)
        proofs[role] = {**source.metadata(), "snapshot_id": source.snapshot_id, "excerpt": excerpt}
        day_proved, named_issuers = False, True
        if candidate_day is None or not _literal_in(date_text, excerpt):
            blockers.append(f"{role.upper()}_MERGER_EFFECTIVE_DAY_UNPROVEN")
        elif candidate_day > source.published_at.date() or candidate_day > service.period[1]:
            blockers.append(f"{role.upper()}_MERGER_EFFECTIVE_DAY_CLOCK_CONFLICT")
        else:
            day_proved = True
        for name in (claim.get("from_action_name_text"), claim.get("to_action_name_text")):
            if not isinstance(name, str) or not 1 <= len(name) <= 300 or not _literal_in(name, excerpt):
                blockers.append(f"{role.upper()}_NAMED_ISSUERS_NOT_BOUND")
                named_issuers = False
        completed = re.search(r"\b(?:merged with and into|consummated|completed (?:the )?(?:acquisition|merger)|"
                              r"merger (?:was|has been) (?:completed|consummated))\b", excerpt, re.I) is not None
        if not completed:
            blockers.append(f"{role.upper()}_COMPLETION_LANGUAGE_MISSING")
        if role == "action" and day_proved and named_issuers and completed:
            action_day = candidate_day
    if action is not None:
        exchange = _quote(action, claim.get("exchange_excerpt"))
        proofs["exchange_consideration"] = {**action.metadata(), "snapshot_id": action.snapshot_id,
                                             "excerpt": exchange, "exchange_ratio": ratio,
                                             "scope": "Eligible common shares; cancellations/dissenters and awards remain separate"}
        conversion_ok = _stock_exchange_terms(exchange, ratio)
        if not conversion_ok:
            blockers.append("EXACT_STOCK_RATIO_OR_FRACTIONAL_CASH_SCOPE_UNPROVEN")
        titles = [claim.get("from_class_title"), claim.get("to_class_title")]
        if (any(not isinstance(title, str) or not _literal_in(title, exchange) for title in titles)
                or titles[0] == titles[1] and exchange.count(titles[0]) < 2):
            blockers.append("BOTH_CONVERSION_CLASSES_NOT_BOUND")
    else:
        proofs["exchange_consideration"] = None
    if completion and (not _stock_exchange_terms(proofs["completion"]["excerpt"], ratio)):
        blockers.append("RECIPIENT_STOCK_RATIO_OR_FRACTIONAL_CASH_SCOPE_CONFLICT")
    if action and completion and action.url == completion.url:
        blockers.append("INDEPENDENT_RECIPIENT_COMPLETION_MISSING")
    if action and completion:
        old_text, new_text = proofs["action"]["excerpt"], proofs["completion"]["excerpt"]
        if (re.search(r"\b(?:indirect[^.;]{0,90}subsidiary|subsidiary[^.;]{0,90}indirect)\b", old_text, re.I)
                and re.search(r"\b(?:direct[^.;]{0,90}subsidiary|subsidiary[^.;]{0,90}direct)\b", new_text, re.I)):
            blockers.append("DIRECT_INDIRECT_CONTROL_RELATION_CONFLICT")
    for role, identity in (("from", previous), ("to", proposed)):
        expected = class_terms(claim.get(f"{role}_class_title"))
        role_proofs = {"class_terms": expected, "instrument": None, "ticker_class": None,
                       "independent_issuer_names": [], "competing_sources": [], "anchor_alignment": "UNKNOWN"}
        if (expected is None or expected["par_term"] == "UNSPECIFIED"
                or expected["share_class"] != share_class(identity["class_description"])):
            blockers.append(f"{role.upper()}_EXACT_CLASS_TERMS_UNPROVEN")
        instrument = _snapshot(service, claim.get(f"{role}_instrument_snapshot_id"), "schedule13")
        cover = _snapshot(service, claim.get(f"{role}_ticker_snapshot_id"), "ticker_filing")
        if instrument is None:
            blockers.append(f"{role.upper()}_CIK_CUSIP_CLASS_BINDING_MISSING")
        else:
            fact = parse_schedule13(instrument)
            source_urls.add(instrument.url)
            role_proofs["instrument"] = {**fact, **instrument.metadata()}
            terms = class_terms(fact["class_title"])
            if (fact["issuer_cik"] != identity["issuer_cik"] or fact["cusip"] != identity["cusip"]
                    or _name(fact["issuer_name"]) != _name(identity["issuer_name"]) or terms is None or expected is None
                    or terms["share_class"] != expected["share_class"]
                    or terms["par_term"] not in {"UNSPECIFIED", expected["par_term"]}
                    or date.fromisoformat(fact["event_date"]) > service.period[1]
                    or role == "from" and action_day and date.fromisoformat(fact["event_date"]) >= action_day):
                blockers.append(f"{role.upper()}_STRUCTURED_INSTRUMENT_CONFLICT")
        if cover is None:
            blockers.append(f"{role.upper()}_TICKER_CIK_CLASS_BINDING_MISSING")
        else:
            facts = [fact for fact in parse_ticker_filing(cover)
                     if fact["issuer_cik"] == identity["issuer_cik"] and fact["ticker"] == identity["ticker"]]
            source_urls.add(cover.url)
            if len(facts) != 1:
                blockers.append(f"{role.upper()}_TICKER_CONTEXT_NOT_UNIQUE")
            else:
                fact = facts[0]
                role_proofs["ticker_class"] = {**fact, **cover.metadata()}
                names = [name for name in issuer_names(cover) if name["issuer_cik"] == identity["issuer_cik"]
                         and name["context_ref"] == fact["context_ref"] and name["anchor_date"] == fact["anchor_date"]]
                role_proofs["independent_issuer_names"] = names
                if len(names) != 1 or _name(names[0]["issuer_name"]) != _name(identity["issuer_name"]):
                    blockers.append(f"{role.upper()}_INDEPENDENT_ISSUER_NAME_BINDING_MISSING")
                if (class_terms(fact["class_title"]) != expected or date.fromisoformat(fact["anchor_date"]) > service.period[1]
                        or role == "from" and action_day and date.fromisoformat(fact["anchor_date"]) >= action_day):
                    blockers.append(f"{role.upper()}_TICKER_CLASS_OR_DATE_CONFLICT")
                if role_proofs["instrument"]:
                    aligned = fact["anchor_date"] == role_proofs["instrument"]["event_date"]
                    role_proofs["anchor_alignment"] = "SAME_DATED_ANCHOR" if aligned else "DIFFERENT_DATED_ANCHORS"
                    if not aligned:
                        blockers.append(f"{role.upper()}_DATED_TUPLE_ANCHORS_DO_NOT_ALIGN")
        for fact in service.covers:
            if fact["issuer_cik"] == identity["issuer_cik"] and fact["ticker"] == identity["ticker"]:
                if class_terms(fact["class_title"]) != expected:
                    role_proofs["competing_sources"].append(deepcopy(fact))
        if role_proofs["competing_sources"]:
            blockers.append(f"CONFLICTING_{role.upper()}_CLASS_SOURCE")
        for fact in service.instruments:
            if (role == "from" and action_day and fact["issuer_cik"] == identity["issuer_cik"]
                    and fact["cusip"] == identity["cusip"] and date.fromisoformat(fact["event_date"]) >= action_day):
                role_proofs["competing_sources"].append({**deepcopy(fact), "scope": "POST_CONVERSION_OLD_INSTRUMENT_ANCHOR"})
        role_urls = [source.url for source in (instrument, cover, action if role == "from" else completion) if source]
        if len(role_urls) != len(set(role_urls)):
            blockers.append(f"{role.upper()}_INDEPENDENT_PRIMARY_DOCUMENTS_MISSING")
        proofs[role] = role_proofs
    withdrawal = _snapshot(service, claim.get("withdrawal_snapshot_id"), "form25_nse")
    proofs["withdrawal_notification"] = None
    if withdrawal is None:
        blockers.append("INDEPENDENT_FORM25_WITHDRAWAL_MISSING")
    else:
        fact = parse_form25(withdrawal)
        proofs["withdrawal_notification"] = {**fact, **withdrawal.metadata()}
        source_urls.add(withdrawal.url)
        if (fact["issuer_cik"] != previous["issuer_cik"] or _name(fact["issuer_name"]) != _name(previous["issuer_name"])
                or share_class(fact["class_title"]) != share_class(previous["class_description"])
                or action_day and date.fromisoformat(fact["signature_date"]) < action_day):
            blockers.append("FORM25_ISSUER_CLASS_OR_SIGNATURE_CONFLICT")
    proofs["dated_cusip_action_bridge"] = None
    cusip_action = _snapshot(service, claim.get("cusip_bridge_snapshot_id"), "corporate_action_filing")
    if cusip_action is None:
        blockers.append("DATED_OLD_NEW_CUSIP_ACTION_BRIDGE_MISSING")
    else:
        quote = _quote(cusip_action, claim.get("cusip_bridge_excerpt"))
        source_urls.add(cusip_action.url)
        if (urlsplit(cusip_action.url).path.split("/")[4].zfill(10) not in {previous["issuer_cik"], proposed["issuer_cik"]}
                or action_day is None or not all(_literal_in(value, quote) for value in
                                                   (previous["cusip"], proposed["cusip"], date_text))
                or not _stock_exchange_terms(quote, ratio)):
            blockers.append("DATED_OLD_NEW_CUSIP_ACTION_BRIDGE_NOT_PROVEN")
        proofs["dated_cusip_action_bridge"] = {**cusip_action.metadata(), "snapshot_id": cusip_action.snapshot_id, "excerpt": quote}
    report = {"schema_version": 1, "bridge_kind": "STOCK_MERGER_EXCHANGE", "status": "PRIMARY_BRIDGE_PARTIAL" if blockers else
              "PRIMARY_BRIDGE_READY_FOR_EXPLICIT_REVIEW", "ticker": claim["ticker"], "quarter": service.quarter,
              "from_identity_proposal": deepcopy(previous), "candidate_target_tuple": deepcopy(proposed),
              "tuple_status": "UNVERIFIED_PRIMARY_CANDIDATES", "preserved_prior_identities": deepcopy(preserved),
              "merger_effective_on": action_day.isoformat() if action_day else None,
              "proposed_effective_on": effective, "exchange_ratio": ratio, "fractional_cash_only": conversion_ok,
              "actual_delisting_effective_on": None, "delisting_effective_date_status": "UNKNOWN",
              "deleted_list_is_delisting_proof": False, "absent_catalog_is_delisting_proof": False,
              "as_of": service.as_of.isoformat(), "evidence_known_at": max(*[snapshot.observed_at for snapshot in service.snapshots.values()],
                 *[_clock(row["known_at"]) for row in preserved]).isoformat(), "proofs": proofs,
              "blockers": list(dict.fromkeys(blockers)), "independent_primary_urls": sorted(source_urls),
              "historical_knowledge_reconstructed": False, "identity_transition_approved": False,
              "interval_extension_allowed": False, "automatic_alias_allowed": False,
              "canonical_universe_changed": False, "production_registry_changed": False, "scoring_allowed": False}
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
