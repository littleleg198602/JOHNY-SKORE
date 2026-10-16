from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import posixpath
import re
from collections.abc import Mapping
from urllib.parse import unquote, urlsplit

from market_checker_app.collectors.sec_edgar_client import SecCompany
from market_checker_app.utils.source_validation import public_https_reference
from market_checker_app.utils.symbols import normalize_yahoo_symbol


COUNTERPARTY_IDENTITY_VERSION = "dated-counterparty-v1"
DEFAULT_COUNTERPARTY_IDENTITIES = (Path(__file__).resolve().parents[1] / "data"
                                  / "verified_counterparty_identities.json")
_ROLES = {"DIRECT_ISSUER", "SUBSIDIARY_OF_ISSUER", "PRIVATE_LEGAL_ENTITY"}
_PURPOSES = {"ENTITY_IDENTITY", "DISCLOSED_NAME", "ISSUER_IDENTITY", "ENTITY_TO_ISSUER"}
_MAX_RECORDS = 10_000
_MAX_FILE_BYTES = 10_000_000
_ENTITY_AUTHORITIES = {
    "CIK": {"SEC": {"www.sec.gov"}},
    "LEI": {"GLEIF": {"api.gleif.org", "www.gleif.org"}},
    "LEGAL_REGISTER": {"Companies House": {"find-and-update.company-information.service.gov.uk"}},
}


def _legal_name_key(name: str) -> str:
    # Punctuation and case vary between a filing and SEC's ticker catalog.
    # Legal suffixes and every word remain required; no fuzzy name matching.
    return " ".join(re.findall(r"[a-z0-9]+", name.casefold()))


def exact_catalog_cik(
    disclosed_name: str, catalog: Mapping[str, SecCompany],
) -> tuple[str, tuple[str, ...]] | None:
    """Return a CIK only when all exact-name catalog rows agree on one issuer."""
    key = _legal_name_key(disclosed_name)
    if not key:
        return None
    matches = [company for company in catalog.values()
               if _legal_name_key(company.name) == key]
    ciks = {company.cik for company in matches}
    if len(ciks) != 1:
        return None
    return next(iter(ciks)), tuple(sorted({company.ticker for company in matches}))


def _aware(value: object) -> datetime:
    clock = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("Counterparty identity needs an explicit timezone")
    return clock.astimezone(timezone.utc)


def _text(value: object, *, maximum: int = 1000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("Counterparty identity requires bounded nonempty text")
    return value.strip()


def _name_key(name: object) -> str:
    # Only whitespace and case vary here. Brand names, legal suffixes,
    # punctuation and parent/subsidiary names must be explicitly documented.
    return " ".join(str(name or "").split()).casefold()


def _reference(value: object) -> str:
    url = public_https_reference(_text(value))
    parsed = urlsplit(url)
    if parsed.port not in {None, 443} or parsed.fragment:
        raise ValueError("Counterparty evidence requires a public official HTTPS reference")
    return url


def _same_document(left: str, right: str) -> bool:
    # A different fragment/query is not an independent document.
    a, b = urlsplit(left), urlsplit(right)
    def key(url: object) -> tuple[str, str]:
        host = str(url.hostname or "").rstrip(".").removeprefix("www.")
        return host, posixpath.normpath(unquote(url.path)).rstrip("/")
    return key(a) == key(b)


def _validate_record(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ValueError("Counterparty identity record must be an object")
    row = deepcopy(raw)
    for key in ("record_id", "disclosed_name", "legal_name"):
        row[key] = _text(row.get(key))
    role = row.get("relationship_role")
    if role not in _ROLES:
        raise ValueError("Counterparty identity needs an explicit legal-entity role")
    identity = row.get("entity_identifier")
    if (not isinstance(identity, dict) or set(identity) != {"scheme", "authority", "value"}
            or identity.get("scheme") not in {"CIK", "LEI", "LEGAL_REGISTER"}):
        raise ValueError("Counterparty identity needs an independent legal identifier")
    identity = {key: _text(value) for key, value in identity.items()}
    if identity["authority"] not in _ENTITY_AUTHORITIES[identity["scheme"]]:
        raise ValueError("Counterparty legal identifier needs a supported primary register")
    if (identity["scheme"] == "CIK" and (identity["authority"] != "SEC"
            or not re.fullmatch(r"\d{10}", identity["value"]) or int(identity["value"]) == 0)):
        raise ValueError("Counterparty CIK must use its exact ten-digit SEC identifier")
    if identity["scheme"] == "LEI" and not re.fullmatch(r"[A-Z0-9]{20}", identity["value"]):
        raise ValueError("Counterparty LEI must use its exact identifier")
    if identity["scheme"] == "LEGAL_REGISTER" and not re.fullmatch(r"[A-Z0-9]{8}", identity["value"]):
        raise ValueError("Counterparty Companies House number must use its exact identifier")
    row["entity_identifier"] = identity
    public = role != "PRIVATE_LEGAL_ENTITY"
    if public:
        if (not isinstance(row.get("issuer_cik"), str)
                or not re.fullmatch(r"\d{10}", row["issuer_cik"])
                or not isinstance(row.get("issuer_ticker"), str)
                or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,12}", row["issuer_ticker"])):
            raise ValueError("Counterparty issuer link needs exact SEC CIK and ticker")
        row["issuer_legal_name"] = _text(row.get("issuer_legal_name"))
        if role == "DIRECT_ISSUER" and (
            identity != {"scheme": "CIK", "authority": "SEC", "value": row["issuer_cik"]}
            or _name_key(row["legal_name"]) != _name_key(row["issuer_legal_name"])
        ):
            raise ValueError("Direct issuer identity must be the same legal entity")
        if role == "SUBSIDIARY_OF_ISSUER" and (
            _name_key(row["legal_name"]) == _name_key(row["issuer_legal_name"])
            or identity == {"scheme": "CIK", "authority": "SEC", "value": row["issuer_cik"]}
        ):
            raise ValueError("Subsidiary identity must remain separate from its issuer")
    elif any(row.get(key) is not None for key in ("issuer_cik", "issuer_ticker", "issuer_legal_name")):
        raise ValueError("Private legal entity cannot inherit a public issuer")
    row["effective_from"] = _text(row.get("effective_from"))
    row["effective_to"] = _text(row["effective_to"]) if row.get("effective_to") else None
    start = date.fromisoformat(row["effective_from"])
    end = date.fromisoformat(row["effective_to"]) if row["effective_to"] else None
    if end is not None and end <= start:
        raise ValueError("Counterparty identity effective interval must increase")
    known = _aware(row.get("known_at"))
    row["known_at"] = known.isoformat()
    issuer_symbol=row.get('issuer_trading_symbol',row.get('issuer_ticker'))
    if public and issuer_symbol!=row['issuer_ticker']:
        expected=normalize_yahoo_symbol(row['issuer_ticker'])
        if issuer_symbol!=expected:
            from market_checker_app.services.reviewed_identity_service import ReviewedIdentityRegistry
            decision=ReviewedIdentityRegistry.load().decision(row['issuer_ticker'],as_of=known)
            if not decision or decision['cik']!=row['issuer_cik'] or decision['current_symbol']!=issuer_symbol:
                raise ValueError('Unreviewed canonical-to-current issuer symbol')
    evidence = row.get("evidence")
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 10:
        raise ValueError("Counterparty identity requires bounded independent evidence")
    purposes: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict) or item.get("purpose") not in _PURPOSES:
            raise ValueError("Counterparty evidence needs a declared purpose")
        purpose = item["purpose"]
        item["url"] = _reference(item.get("url"))
        item["locator"] = _text(item.get("locator"))
        item["quote"] = _text(item.get("quote"), maximum=2000)
        item["publisher"] = _text(item.get("publisher"))
        published = _aware(item.get("published_at"))
        observed = _aware(item.get("observed_at"))
        if published > observed or observed > known:
            raise ValueError("Counterparty identity cannot be known before its evidence")
        item["published_at"], item["observed_at"] = published.isoformat(), observed.isoformat()
        quote = _name_key(item["quote"])
        parsed = urlsplit(item["url"])
        if purpose == "ENTITY_IDENTITY":
            if _name_key(row["legal_name"]) not in quote or identity["value"].casefold() not in quote:
                raise ValueError("Entity evidence must document its legal name and identifier")
            projection=item.get('fact_projection')
            if item.get('representation')=='VALIDATED_INLINE_XBRL_FACTS' and not isinstance(projection,dict):
                raise ValueError('Missing legal entity fact projection')
            if projection is not None:
                if (not isinstance(projection,dict) or item.get('representation')!='VALIDATED_INLINE_XBRL_FACTS'
                        or projection.get('legal_name')!=row['legal_name']
                        or projection.get('cik')!=identity['value']
                        or not projection.get('context_ref')
                        or not re.fullmatch('[0-9a-f]{64}',item.get('sha256',''))):
                    raise ValueError('Invalid legal entity fact projection')
            if parsed.hostname not in _ENTITY_AUTHORITIES[identity["scheme"]][identity["authority"]]:
                raise ValueError("Entity evidence must cite its primary legal register")
            if identity["scheme"] == "CIK" and (parsed.hostname != "www.sec.gov"
                    or not parsed.path.startswith(f"/Archives/edgar/data/{int(identity['value'])}/")):
                raise ValueError("Entity CIK evidence must cite its own SEC archive")
            if identity["scheme"] == "LEGAL_REGISTER":
                entry_path = "/company/" + identity["value"]
                if parsed.path.rstrip("/") != entry_path and not parsed.path.startswith(entry_path + "/"):
                    raise ValueError("Entity registration evidence must cite its exact register entry")
            if identity["scheme"] == "LEI" and identity["value"] not in parsed.path:
                raise ValueError("Entity LEI evidence must cite its exact register entry")
        if purpose == "DISCLOSED_NAME":
            disclosed, legal = re.escape(_name_key(row["disclosed_name"])), re.escape(_name_key(row["legal_name"]))
            affirmative = (re.search(
                rf"{disclosed}\s+(?:is|was)\s+(?:a|an|the)\s+(?:trade|trading|brand) name of\s+{legal}", quote
            ) or re.search(rf"{legal}\s+(?:also known as|trading as|doing business as|d/b/a)\s+{disclosed}", quote))
            if not affirmative or re.search(r"\b(?:not|no longer|former)\b", quote):
                raise ValueError("Disclosed alias must be independently linked to the legal name")
        if purpose in {"ISSUER_IDENTITY", "ENTITY_TO_ISSUER"}:
            if not public or parsed.hostname != "www.sec.gov" or not parsed.path.startswith(
                f"/Archives/edgar/data/{int(row['issuer_cik'])}/"
            ):
                raise ValueError("Issuer relationship evidence must cite that issuer's SEC archive")
            table_projection=(purpose=='ENTITY_TO_ISSUER' and item.get('representation')=='SEC_EXHIBIT_21_ROW')
            if not table_projection and _name_key(row["issuer_legal_name"]) not in quote:
                raise ValueError("Issuer evidence must document its exact legal name")
            if purpose == "ISSUER_IDENTITY" and not re.search(
                rf"(?:ticker|trading symbols?|symbol|common stock|ordinary shares)"
                rf"[\s:=\"'()]*{re.escape(issuer_symbol.casefold())}(?![a-z0-9])", quote
            ):
                raise ValueError("Issuer evidence must document its exact ticker")
            if purpose == "ENTITY_TO_ISSUER":
                child, parent = re.escape(_name_key(row["legal_name"])), re.escape(_name_key(row["issuer_legal_name"]))
                affirmative = (re.search(
                    rf"{child}\s+(?:is|was)\s+(?:a|an)\s+(?:(?:wholly[ -]owned|direct|indirect|consolidated)\s+)*subsidiary of\s+{parent}", quote
                ) or re.search(rf"subsidiaries of\s+{parent}.{{0,500}}{child}", quote))
                if item.get('representation')=='SEC_EXHIBIT_21_ROW':
                    # Reviewed projection of the exhibit's actual heading and
                    # one literal row, never a manufactured ownership sentence.
                    projection=item.get('exhibit_row',{})
                    if not isinstance(projection,dict):raise ValueError('Invalid subsidiary exhibit row')
                    affirmative=(projection.get('parent_legal_name')==row['issuer_legal_name']
                        and projection.get('parent_cik')==row['issuer_cik']
                        and projection.get('child_legal_name')==row['legal_name']
                        and projection.get('relationship_scope')=='SUBSIDIARY'
                        and re.fullmatch(r'EX-21(?:\.[A-Za-z0-9]+)?',projection.get('document_type',''))
                        and _name_key(row['legal_name']) in quote
                        and re.search(r'\bsubsidiar(?:y|ies)\b',quote)
                        and not re.search(r'\bjoint ventures?\b',quote)
                        and re.fullmatch('[0-9a-f]{64}',item.get('sha256',''))
                        and projection.get('row_text') and _name_key(projection['row_text']) in quote)
                if (not affirmative or re.search(r"\b(?:not|no longer|former|divested)\b", quote)):
                    raise ValueError("Subsidiary relationship requires an explicit documented link")
        purposes.add(purpose)
    required = {"ENTITY_IDENTITY"}
    if public:
        required.add("ISSUER_IDENTITY")
    if role == "SUBSIDIARY_OF_ISSUER":
        required.add("ENTITY_TO_ISSUER")
    if _name_key(row["disclosed_name"]) != _name_key(row["legal_name"]):
        required.add("DISCLOSED_NAME")
    if not required <= purposes:
        raise ValueError("Counterparty identity lacks independent entity, alias or issuer evidence")
    row["required_evidence_purposes"] = sorted(required)
    return row


class DatedCounterpartyIdentityRegistry:
    """Resolve only explicitly reviewed legal entities at disclosure/knowledge time.

    Loading validates a supplied evidence manifest; it does not fetch, verify
    live publisher contents or manufacture identities from the SEC ticker map.
    A subsidiary issuer link never grants the parent's financials to the child.
    """

    def __init__(self, records: object):
        if not isinstance(records, (list, tuple)) or len(records) > _MAX_RECORDS:
            raise ValueError("Counterparty manifest must be a bounded record list")
        self._records = tuple(_validate_record(record) for record in records)
        ids = [row["record_id"] for row in self._records]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate counterparty identity record ID")

    @classmethod
    def load(cls, path: Path = DEFAULT_COUNTERPARTY_IDENTITIES) -> "DatedCounterpartyIdentityRegistry":
        path = Path(path)
        with path.open('rb') as stream:
            body=stream.read(_MAX_FILE_BYTES+1)
        if len(body) > _MAX_FILE_BYTES:
            raise ValueError("Counterparty identity manifest exceeds the byte limit")
        raw = json.loads(body)
        if not isinstance(raw, dict) or type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
            raise ValueError("Unsupported counterparty identity manifest")
        # Validation compiles many issuer-specific evidence expressions. Reuse
        # only an identical byte snapshot, never an mtime or a path alone.
        # Reviewed provider aliases also depend on the independent issuer
        # catalog, so re-evaluate their validation context before cache lookup.
        context=[]
        for row in raw.get('records',[]) if isinstance(raw.get('records'),list) else []:
            if not isinstance(row,dict):continue
            ticker=row.get('issuer_ticker');symbol=row.get('issuer_trading_symbol',ticker)
            if isinstance(ticker,str) and isinstance(symbol,str) and symbol!=normalize_yahoo_symbol(ticker):
                from market_checker_app.services.reviewed_identity_service import ReviewedIdentityRegistry
                decision=ReviewedIdentityRegistry.load().decision(ticker,as_of=_aware(row.get('known_at')))
                context.append((ticker,symbol,json.dumps(decision,sort_keys=True)))
        return _validated_registry_snapshot(cls,body,tuple(context))

    def fingerprint(self, *, as_of: datetime) -> str:
        clock = _aware(as_of)
        records = sorted((row for row in self._records if _aware(row["known_at"]) <= clock),
                         key=lambda row: row["record_id"])
        payload = {"resolver_version": COUNTERPARTY_IDENTITY_VERSION, "records": records}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def records(self) -> tuple[dict, ...]:
        """Detached dated records; presence is not continuous group ownership."""
        return deepcopy(self._records)

    def resolve(self, *, disclosed_name: str, published_at: datetime,
                as_of: datetime, exposure_source_url: str) -> dict[str, object]:
        denied: dict[str, object] = {
            "counterparty_identity_status": "NAMED_ONLY",
            "counterparty_identity_evidence": None,
            "counterparty_issuer_attribution_allowed": False,
            "counterparty_financials_attribution_allowed": False,
            "counterparty_identity_resolver_version": COUNTERPARTY_IDENTITY_VERSION,
        }
        clock, published = _aware(as_of), _aware(published_at)
        if published > clock:
            return dict(denied, counterparty_identity_resolution="FUTURE_DISCLOSURE")
        source_url = _reference(exposure_source_url)
        candidates = [row for row in self._records
                      if _name_key(row["disclosed_name"]) == _name_key(disclosed_name)]
        if not candidates:
            return dict(denied, counterparty_identity_resolution="UNMAPPED_NAME")
        known = [row for row in candidates if _aware(row["known_at"]) <= clock]
        if not known:
            return dict(denied, counterparty_identity_resolution="NOT_YET_KNOWN")
        active = [row for row in known if date.fromisoformat(row["effective_from"]) <= published.date()
                  and (not row.get("effective_to") or published.date() < date.fromisoformat(row["effective_to"]))]
        if not active:
            return dict(denied, counterparty_identity_resolution="OUTSIDE_EFFECTIVE_PERIOD")
        # Check globally before selecting an issuer. Competing parent mappings
        # must not be filtered away by a desired ticker or document URL.
        if len(active) != 1:
            return dict(denied, counterparty_identity_resolution="AMBIGUOUS_DATED_IDENTITY")
        row = active[0]
        independent = {item["purpose"] for item in row["evidence"]
                       if not _same_document(item["url"], source_url)}
        if not set(row["required_evidence_purposes"]) <= independent:
            return dict(denied, counterparty_identity_resolution="SAME_DISCLOSURE_ONLY")
        details = deepcopy(row)
        details["matched_disclosure_date"] = published.date().isoformat()
        details["resolution_as_of"] = clock.isoformat()
        return dict(denied, counterparty_identity_status="IDENTIFIED",
                    counterparty_identity_resolution="EXACT_DATED_LEGAL_ENTITY",
                    counterparty_identity_evidence=details,
                    counterparty_issuer_attribution_allowed=row["relationship_role"] != "PRIVATE_LEGAL_ENTITY")


__all__ = ["COUNTERPARTY_IDENTITY_VERSION", "DEFAULT_COUNTERPARTY_IDENTITIES",
           "DatedCounterpartyIdentityRegistry", "exact_catalog_cik"]


@lru_cache(maxsize=4)
def _validated_registry_snapshot(registry_type,body: bytes, validation_context: tuple) -> DatedCounterpartyIdentityRegistry:
    # Consumers receive detached records/results through the public methods;
    # the validated internal snapshot remains read-only between analyses.
    return registry_type(json.loads(body).get('records'))
