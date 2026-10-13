from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from urllib.parse import urlsplit

from market_checker_app.agents.contracts import (
    RelationshipType,
    ResourceExposureType,
    DocumentRecord,
)
from market_checker_app.collectors.short_report_client import FetchedShortReport
from market_checker_app.config import (
    CommodityEnergySourceConfig,
    SupplyChainSourceConfig,
)
from market_checker_app.services.sec_counterparty_identity import DatedCounterpartyIdentityRegistry


@dataclass(frozen=True, slots=True)
class FilingSupplyChainFinding:
    source: SupplyChainSourceConfig
    support_term: str
    reason: str
    evidence_quote: str
    identity_resolution: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class FilingCommodityFinding:
    source: CommodityEnergySourceConfig
    support_term: str
    reason: str
    evidence_quote: str
    provenance: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class FilingExposureFindings:
    supply_chain: tuple[FilingSupplyChainFinding, ...] = ()
    commodity_energy: tuple[FilingCommodityFinding, ...] = ()


_CUSTOMER_CONCENTRATION = re.compile(
    r"(?P<phrase>(?:(?:one|a single|our largest|a major|the largest)\s+)?"
    r"customers?(?:\s+[a-z][a-z0-9&'/-]*){0,8}\s+"
    r"(?:accounted for|represented|comprised)\s+"
    r"(?:approximately\s+|about\s+|around\s+)?"
    r"(?P<pct>\d{1,3}(?:\.\d+)?)\s*%)",
    flags=re.IGNORECASE,
)
_SUPPLIER_CONCENTRATION = re.compile(
    r"(?P<phrase>(?:single|sole|single-source|limited number of|few)\s+"
    r"(?:source\s+)?suppliers?)",
    flags=re.IGNORECASE,
)
_CONTRACT_MANUFACTURING = re.compile(
    r"(?P<phrase>contract manufacturers?|outsourc(?:e|ed|ing)\s+"
    r"(?:a material portion of\s+)?(?:our\s+)?manufacturing)",
    flags=re.IGNORECASE,
)
_SUPPLY_DISRUPTION = re.compile(
    r"(?P<phrase>(?:supply(?:\s+chain)?|supplier)\s+"
    r"(?:disruption|interruption|shortage|constraint|delay)s?)",
    flags=re.IGNORECASE,
)
_NAMED_SUPPLIER = re.compile(
    r"\b(?:[Ww]e|[Oo]ur company)\s+(?:purchase|procure|source)\s+"
    r"(?P<product>(?:[a-z][a-z0-9-]*\s+){0,5}[a-z][a-z0-9-]*)\s+"
    r"from\s+(?P<name>(?:[A-Z][A-Za-z0-9&-]*\s+){1,5}"
    r"(?:Inc\.|Corporation|Corp\.|LLC|Ltd\.))",
)
_NAMED_SUPPLIER_LIST = re.compile(
    r"\bwe\s+(?:currently\s+)?rely on suppliers such as\s+"
    r"(?P<names>.{3,190}?)\s+for these cells\b",
    flags=re.IGNORECASE,
)
_HEDGE_SHARE = re.compile(
    r"\b(?:hedg(?:ed|e|ing)|fixed-price contracts? (?:cover|covered))\s+"
    r"(?P<pct>\d{1,3}(?:\.\d+)?)\s*%\s+of\s+"
    r"(?:our\s+)?(?:anticipated\s+|expected\s+|forecast\s+)?"
    r"(?:[a-z-]+\s+){0,5}"
    r"(?:purchases|requirements|consumption)\b",
    flags=re.IGNORECASE,
)
_PERIOD = re.compile(r"\b(?:20\d{2}|FY\s*20\d{2})\b", flags=re.IGNORECASE)
_INPUT_RELATION = re.compile(
    r"\b(?:we\s+(?:purchase|procure|consume|source|depend|hedg\w*)\b|"
    r"our\s+(?:input\s+costs?|raw\s+materials?|costs?|expenses?|purchases?|"
    r"consumption|requirements|exposure)\b)", re.IGNORECASE,
)
_OPERATIONAL_OUTPUT = re.compile(
    r"\b(?:our\s+(?:principal\s+)?business|we)\b"
    r"(?P<relation>[^.;!?]{0,140}\b(?:produc(?:e|es|ing)|production\s+of|sell)\b"
    r"(?P<output_resources>[^.;!?]{0,110}))", re.IGNORECASE,
)
_NON_OPERATIONAL = re.compile(
    r"\b(?:not|never|no\s+longer|may|might|could|plan|plans|intend|intends|"
    r"expect|expects|will|would|potential)\b", re.IGNORECASE,
)
_OUTPUT_RESOURCES = (("Crude oil", ("crude oil",)), ("Natural gas", ("natural gas",)))
_FISCAL_PERIOD = re.compile(
    r"\bfor\s+the\s+(?:fiscal\s+)?year\s+ended\s+"
    r"(?P<date>[A-Za-z]+\s+\d{1,2},?\s+20\d{2})\b", re.IGNORECASE,
)


def _filing_provenance(
    fetched: FetchedShortReport, *, as_of: datetime | None,
    filing_document: DocumentRecord | None,
    require_issuer_path: bool,
) -> dict[str, object]:
    """Keep disclosure, knowledge and historical filing identity separate."""
    requested, final = urlsplit(fetched.source.url), urlsplit(fetched.final_url)
    for parsed in (requested, final):
        if (parsed.scheme != "https" or parsed.hostname != "www.sec.gov"
                or parsed.port not in {None, 443} or parsed.username or parsed.password
                or parsed.query or parsed.fragment
                or not parsed.path.startswith("/Archives/")
                or (require_issuer_path and not re.fullmatch(
                    r"/Archives/edgar/data/\d{1,10}/[^?#]+", parsed.path))):
            raise ValueError("Commodity filing requires an exact official SEC archive source")
    if requested.path != final.path or fetched.source.publisher.strip().casefold() != "sec edgar":
        raise ValueError("Commodity filing source changed publisher or filing identity")
    published = fetched.source.published_at
    if published.tzinfo is None or published.utcoffset() is None:
        raise ValueError("Commodity filing publication needs timezone")
    if as_of is not None:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("Commodity filing knowledge cutoff needs timezone")
        if published > as_of:
            raise ValueError("Commodity filing disclosure is after knowledge cutoff")
    if not re.fullmatch(r"[a-f0-9]{64}", fetched.content_hash):
        raise ValueError("Commodity filing needs captured content SHA-256")
    match = re.fullmatch(r"/Archives/edgar/data/(?P<cik>\d{1,10})/"
                         r"(?P<accession>\d{18})/(?P<document>[^/]+)", final.path)
    issuer_path = re.match(r"/Archives/edgar/data/(\d{1,10})/", final.path)
    cik = f"{int(issuer_path[1]):010d}" if issuer_path else None
    accession = match["accession"] if match else None
    accession_number = (f"{accession[:10]}-{accession[10:12]}-{accession[12:]}"
                        if accession else None)
    report_date = None
    period = _FISCAL_PERIOD.search(fetched.text)
    if period:
        try:
            report_date = datetime.strptime(period["date"].replace(",", ""), "%B %d %Y").date()
        except ValueError:
            pass
    observed = None
    acceptance = None
    acceptance_source = None
    acceptance_observed = None
    if filing_document is not None:
        if (filing_document.url != fetched.final_url
                or filing_document.ticker != fetched.source.ticker
                or filing_document.content_hash != fetched.content_hash
                or filing_document.source_type != "regulatory_filing"):
            raise ValueError("Commodity filing document does not match fetched evidence")
        metadata = filing_document.metadata
        if metadata.get("cik") and f"{int(metadata['cik']):010d}" != cik:
            raise ValueError("Commodity filing document CIK mismatch")
        if (accession_number is not None and metadata.get("accession_number")
                and metadata["accession_number"] != accession_number):
            raise ValueError("Commodity filing document accession mismatch")
        observed = filing_document.observed_at
        if filing_document.reporting_period_end is not None:
            doc_period = filing_document.reporting_period_end.date()
            if report_date is not None and doc_period != report_date:
                raise ValueError("Commodity filing report period mismatch")
            report_date = doc_period
        raw_acceptance = metadata.get("acceptanceDateTime") or metadata.get("accepted_at")
        if raw_acceptance:
            if not cik or not accession_number:
                raise ValueError("Commodity filing acceptance lacks exact SEC filing identity")
            acceptance_source = metadata.get("acceptance_source_url") or metadata.get("index_url")
            parsed = urlsplit(str(acceptance_source or ""))
            expected_index = (f"/Archives/edgar/data/{int(cik)}/{accession}/"
                              f"{accession_number}-index.htm")
            if (not accession_number or parsed.scheme != "https" or parsed.username or parsed.password
                    or parsed.port not in {None, 443} or parsed.query or parsed.fragment
                    or (parsed.hostname, parsed.path) not in {
                        ("data.sec.gov", f"/submissions/CIK{cik}.json"),
                        ("www.sec.gov", expected_index),
                    }):
                raise ValueError("Commodity filing acceptance lacks exact SEC provenance")
            acceptance = datetime.fromisoformat(str(raw_acceptance).replace("Z", "+00:00"))
            if acceptance.tzinfo is None or acceptance.utcoffset() is None:
                raise ValueError("Commodity filing acceptance needs timezone")
            # A date-only SEC filingDate can be refined by the independent
            # acceptance timestamp; an already precise conflicting clock cannot.
            date_only = published.astimezone(timezone.utc).time().isoformat() == "00:00:00"
            if ((date_only and published.date() != acceptance.date())
                    or (not date_only and published != acceptance)):
                raise ValueError("Commodity filing publication/acceptance clock conflict")
            published = acceptance
            if metadata.get("acceptance_source_observed_at"):
                acceptance_observed = datetime.fromisoformat(
                    str(metadata["acceptance_source_observed_at"]).replace("Z", "+00:00"))
                if (acceptance_observed.tzinfo is None or acceptance_observed.utcoffset() is None
                        or acceptance_observed < acceptance):
                    raise ValueError("Commodity filing acceptance observation clock is invalid")
            if as_of is not None and published > as_of:
                raise ValueError("Commodity filing acceptance is after knowledge cutoff")
        if observed < published:
            raise ValueError("Commodity filing observation precedes its publication")
    if report_date is not None and report_date > published.date():
        raise ValueError("Commodity filing report period is after publication")
    known = max(published, observed, acceptance_observed or observed) if observed is not None else None
    return {"source_url": fetched.final_url, "requested_source_url": fetched.source.url,
            "content_sha256": fetched.content_hash, "filing_cik": cik,
            "filing_accession_number": accession_number,
            "filing_identity_status": ("EXACT_ARCHIVE_ACCESSION" if match else
                                       "ACCESSION_NOT_SUPPLIED" if cik else "ISSUER_PATH_NOT_SUPPLIED"),
            "filing_published_at": published.isoformat(),
            "provided_publication_at": fetched.source.published_at.isoformat(),
            "sec_acceptance_at": acceptance.isoformat() if acceptance else None,
            "acceptance_source_url": acceptance_source,
            "acceptance_source_observed_at": acceptance_observed.isoformat() if acceptance_observed else None,
            "acceptance_clock_verified": acceptance is not None,
            "publication_clock_basis": ("EXACT_SEC_ACCEPTANCE_METADATA" if acceptance is not None
                                        else "CONFIGURED_TIMESTAMP_NOT_INDEPENDENTLY_VERIFIED"),
            "reporting_period_end": report_date.isoformat() if report_date else None,
            "source_observed_at": observed.isoformat() if observed else None,
            "known_at": known.isoformat() if known else None,
            "knowledge_cutoff": as_of.isoformat() if as_of else None,
            "available_at_cutoff": known <= as_of if known is not None and as_of is not None else None,
            "knowledge_time_status": "MATCHED_FILING_OBSERVATION" if observed else "NOT_SUPPLIED",
            "issuer_scope": "HISTORICAL_FILING_ISSUER_ONLY" if cik else "UNRESOLVED_SEC_ARCHIVE_CANDIDATE",
            "current_instrument_identity_verified": False, "asset_ownership_inferred": False,
            "quantity_or_revenue_share_inferred": False, "historical_availability_reconstructed": False,
            "scoring_applied": False, "prediction_input": False}

_RESOURCE_RULES: tuple[
    tuple[str, ResourceExposureType, tuple[str, ...]], ...
] = (
    (
        "Electricity",
        ResourceExposureType.ELECTRICITY,
        ("electricity", "electric power", "power prices"),
    ),
    (
        "Natural gas",
        ResourceExposureType.FUEL,
        ("natural gas",),
    ),
    (
        "Diesel fuel",
        ResourceExposureType.FUEL,
        ("diesel fuel", "diesel prices"),
    ),
    (
        "Jet fuel",
        ResourceExposureType.FUEL,
        ("jet fuel",),
    ),
    (
        "Gasoline",
        ResourceExposureType.FUEL,
        ("gasoline",),
    ),
    (
        "Steel",
        ResourceExposureType.MATERIAL_INPUT,
        ("steel",),
    ),
    (
        "Aluminium",
        ResourceExposureType.MATERIAL_INPUT,
        ("aluminum", "aluminium"),
    ),
    (
        "Copper",
        ResourceExposureType.MATERIAL_INPUT,
        ("copper",),
    ),
    (
        "Lithium",
        ResourceExposureType.MATERIAL_INPUT,
        ("lithium",),
    ),
    (
        "Cobalt",
        ResourceExposureType.MATERIAL_INPUT,
        ("cobalt",),
    ),
    (
        "Nickel",
        ResourceExposureType.MATERIAL_INPUT,
        ("nickel",),
    ),
    (
        "Semiconductors",
        ResourceExposureType.MATERIAL_INPUT,
        ("semiconductors", "semiconductor components"),
    ),
    (
        "Rare earths",
        ResourceExposureType.MATERIAL_INPUT,
        ("rare earth", "rare-earth"),
    ),
    (
        "Resins and plastics",
        ResourceExposureType.MATERIAL_INPUT,
        ("resin", "plastic resins"),
    ),
    (
        "Wood pulp",
        ResourceExposureType.MATERIAL_INPUT,
        ("wood pulp",),
    ),
)
_EXPOSURE_CONTEXT = (
    "raw material",
    "input cost",
    "cost of",
    "prices for",
    "price volatility",
    "supply shortage",
    "supply disruption",
    "availability",
    "purchase",
    "procure",
    "consume",
    "consumption",
    "depend",
    "exposure",
)


def _input_quote(sentence: str, term: str) -> str | None:
    # HTML paragraphs and bullet lists need not contain terminal punctuation.
    # Keep the exact company-input relation near the resource instead of
    # borrowing cost language from an unrelated part of a long section.
    output = _OPERATIONAL_OUTPUT.search(sentence)
    for match in re.finditer(r"(?<!\w)" + re.escape(term) + r"(?!\w)", sentence, re.IGNORECASE):
        if output and output.start("output_resources") <= match.start() < output.end("output_resources"):
            continue
        quote = sentence[max(0, match.start() - 180):match.end() + 180].strip()
        if (_INPUT_RELATION.search(quote)
                and any(marker in quote.casefold() for marker in _EXPOSURE_CONTEXT)):
            return quote
    return None


def _sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return []
    normalized = re.sub(r"\bCo\.(?=\s+(?:Limited|Ltd\.))", "Co\u2423", normalized)
    return [
        sentence.replace("\u2423", ".").strip()
        for sentence in re.split(r"(?<=[.!?;])\s+", normalized)
        if sentence.strip()
    ]


class FilingExposureDiscoveryService:
    """Extract conservative, low-confidence exposure facts from SEC 10-K text.

    The parser records explicit concentration, input-cost and first-person
    operating-output language. It does not infer asset ownership, current
    instrument identity, price direction or company scores.
    """

    def discover(
        self,
        fetched: FetchedShortReport,
        *,
        max_supply_chain: int = 6,
        max_commodity_energy: int = 12,
        counterparty_identities: DatedCounterpartyIdentityRegistry | None = None,
        as_of: datetime | None = None,
        filing_document: DocumentRecord | None = None,
        require_commodity_issuer_path: bool = False,
    ) -> FilingExposureFindings:
        ticker = fetched.source.ticker.strip().upper()
        published_at = fetched.source.published_at
        url = fetched.final_url
        sentences = _sentences(fetched.text)
        if counterparty_identities is not None and as_of is None:
            raise ValueError("Counterparty identity resolution needs explicit knowledge time")

        supply: list[FilingSupplyChainFinding] = []
        seen_supply: set[tuple[str, str]] = set()

        def add_supply(
            *,
            counterparty: str,
            relationship_type: RelationshipType,
            support_term: str,
            reason: str,
            dependency_pct: float | None = None,
            relationship_context: str | None = None,
            evidence_quote: str = "",
            identity_status: str = "ANONYMOUS",
            product_or_input: str | None = None,
        ) -> None:
            if len(supply) >= max(0, int(max_supply_chain)):
                return
            key = (relationship_type.value, counterparty.casefold())
            if key in seen_supply:
                return
            seen_supply.add(key)
            identity_resolution = None
            if identity_status == "NAMED_ONLY" and counterparty_identities is not None:
                identity_resolution = counterparty_identities.resolve(
                    disclosed_name=counterparty, published_at=published_at,
                    as_of=as_of, exposure_source_url=url,
                )
                identity_status = str(identity_resolution["counterparty_identity_status"])
            supply.append(
                FilingSupplyChainFinding(
                    source=SupplyChainSourceConfig(
                        ticker=ticker,
                        counterparty=counterparty,
                        relationship_type=relationship_type.value,
                        publisher="SEC EDGAR",
                        published_at=published_at,
                        url=url,
                        dependency_pct=dependency_pct,
                        confidence=0.45,
                        counterparty_identity_status=identity_status,
                        product_or_input=product_or_input,
                        relationship_context=relationship_context,
                        evidence_level="EXPLICIT_FILING",
                        evidence_quote=evidence_quote,
                    ),
                    support_term=support_term,
                    reason=reason,
                    evidence_quote=evidence_quote,
                    identity_resolution=identity_resolution,
                )
            )

        # Exact named mentions receive the small per-filing budget before
        # repeated anonymous concentration language can exhaust it.
        for sentence in sentences:
            named = _NAMED_SUPPLIER.search(sentence)
            if named:
                add_supply(
                    counterparty=named.group("name"),
                    relationship_type=RelationshipType.SUPPLIER,
                    support_term=named.group(0),
                    reason="sec_named_supplier_with_product",
                    identity_status="NAMED_ONLY",
                    product_or_input=named.group("product"),
                    relationship_context="EXPLICIT_NAMED_SOURCE",
                    evidence_quote=sentence,
                )
            supplier_list = _NAMED_SUPPLIER_LIST.search(sentence)
            if supplier_list:
                for item in re.split(r"\s+and\s+|,\s+(?=[A-Z])", supplier_list.group("names")):
                    counterparty = re.sub(r"\s+\([A-Z0-9-]{2,12}\)$", "", item.strip())
                    if not re.fullmatch(r"[A-Z][A-Za-z0-9&., -]{2,90}", counterparty):
                        continue
                    add_supply(
                        counterparty=counterparty,
                        relationship_type=RelationshipType.SUPPLIER,
                        support_term=supplier_list.group(0),
                        reason="sec_explicit_named_supplier_list",
                        identity_status="NAMED_ONLY",
                        relationship_context="EXPLICIT_NAMED_SOURCE",
                        evidence_quote=sentence,
                    )
        for sentence in sentences:
            named = _NAMED_SUPPLIER.search(sentence)
            customer = _CUSTOMER_CONCENTRATION.search(sentence)
            if customer:
                dependency = min(100.0, float(customer.group("pct")))
                add_supply(
                    counterparty="Unnamed major customer",
                    relationship_type=RelationshipType.CUSTOMER,
                    support_term=customer.group("phrase"),
                    reason="sec_10k_customer_concentration",
                    dependency_pct=dependency,
                    relationship_context="CONCENTRATION",
                    evidence_quote=sentence,
                )
            supplier = _SUPPLIER_CONCENTRATION.search(sentence)
            if supplier and not named:
                add_supply(
                    counterparty="Unnamed critical supplier",
                    relationship_type=RelationshipType.SUPPLIER,
                    support_term=supplier.group("phrase"),
                    reason="sec_10k_supplier_concentration",
                    relationship_context=(
                        "SINGLE_SOURCE"
                        if any(term in sentence.casefold() for term in ("single", "sole", "single-source"))
                        else "SUPPLIER_CONCENTRATION"
                    ),
                    evidence_quote=sentence,
                )
            manufacturer = _CONTRACT_MANUFACTURING.search(sentence)
            if manufacturer:
                add_supply(
                    counterparty="Unnamed contract manufacturer",
                    relationship_type=RelationshipType.CONTRACT_MANUFACTURER,
                    support_term=manufacturer.group("phrase"),
                    reason="sec_10k_contract_manufacturing",
                    relationship_context="CONTRACT_MANUFACTURING",
                    evidence_quote=sentence,
                )
            disruption = _SUPPLY_DISRUPTION.search(sentence)
            if disruption:
                add_supply(
                    counterparty="Unnamed supply-chain disruption",
                    relationship_type=RelationshipType.SUPPLIER,
                    support_term=disruption.group("phrase"),
                    reason="sec_10k_supply_disruption",
                    relationship_context="DISRUPTION",
                    evidence_quote=sentence,
                )

        commodities: list[FilingCommodityFinding] = []
        seen_resources: set[tuple[str, str]] = set()
        provenance = (_filing_provenance(fetched, as_of=as_of, filing_document=filing_document,
                                         require_issuer_path=require_commodity_issuer_path)
                      if max_commodity_energy > 0 else None)
        commodity_publication = (datetime.fromisoformat(str(provenance["filing_published_at"]))
                                 if provenance is not None else published_at)
        for sentence in sentences:
            lowered = sentence.casefold()
            output = _OPERATIONAL_OUTPUT.search(sentence)
            if (output is not None and not _NON_OPERATIONAL.search(output.group(0))
                    and provenance is not None and provenance["filing_accession_number"]):
                quote = output.group(0).strip()
                for resource_name, terms in _OUTPUT_RESOURCES:
                    matched = next((term for term in terms if re.search(
                        r"(?<!\w)" + re.escape(term) + r"(?!\w)", output["output_resources"], re.IGNORECASE)), None)
                    key = (resource_name, ResourceExposureType.COMMODITY_OUTPUT.value)
                    if (matched is None or key in seen_resources
                            or len(commodities) >= max(0, int(max_commodity_energy))):
                        continue
                    seen_resources.add(key)
                    commodities.append(FilingCommodityFinding(
                        source=CommodityEnergySourceConfig(
                            ticker=ticker, resource_name=resource_name,
                            exposure_type=ResourceExposureType.COMMODITY_OUTPUT.value,
                            publisher="SEC EDGAR", published_at=commodity_publication, url=url,
                            confidence=0.40, evidence_quote=quote,
                            disclosure_period=str(provenance["reporting_period_end"] or "") or None,
                        ), support_term=matched, reason="sec_filing_explicit_operational_output",
                        evidence_quote=quote,
                        provenance=dict(provenance, operational_relation="EXPLICIT_FIRST_PERSON_OUTPUT"),
                    ))
            if (not any(marker in lowered for marker in _EXPOSURE_CONTEXT)
                    or not _INPUT_RELATION.search(sentence)):
                continue
            matching_resources = [name for name, _kind, terms in _RESOURCE_RULES
                                  if any(_input_quote(sentence, term) is not None for term in terms)]
            hedge = _HEDGE_SHARE.search(sentence) if len(matching_resources) == 1 else None
            hedge_pct = float(hedge.group("pct")) if hedge else None
            if hedge_pct is not None and not 0 <= hedge_pct <= 100:
                hedge_pct = None
            for resource_name, exposure_type, terms in _RESOURCE_RULES:
                matched = next((term for term in terms if _input_quote(sentence, term) is not None), None)
                key = (resource_name, exposure_type.value)
                if matched is None or key in seen_resources:
                    continue
                quote = _input_quote(sentence, matched)
                period = _PERIOD.search(quote)
                if len(commodities) >= max(0, int(max_commodity_energy)):
                    break
                seen_resources.add(key)
                commodities.append(
                    FilingCommodityFinding(
                        source=CommodityEnergySourceConfig(
                            ticker=ticker,
                            resource_name=resource_name,
                            exposure_type=exposure_type.value,
                            publisher="SEC EDGAR",
                            published_at=commodity_publication,
                            url=url,
                            confidence=0.40,
                            evidence_quote=quote,
                            hedged_share_pct=hedge_pct,
                            disclosure_period=(period.group(0) if period else
                                               str(provenance["reporting_period_end"] or "") or None),
                        ),
                        support_term=matched,
                        reason="sec_10k_material_or_energy_exposure",
                        evidence_quote=quote,
                        provenance=dict(provenance, operational_relation="EXPLICIT_INPUT_OR_COST_LANGUAGE"),
                    )
                )

        return FilingExposureFindings(
            supply_chain=tuple(supply),
            commodity_energy=tuple(commodities),
        )
