from __future__ import annotations

from dataclasses import dataclass
import re

from market_checker_app.agents.contracts import (
    RelationshipType,
    ResourceExposureType,
)
from market_checker_app.collectors.short_report_client import FetchedShortReport
from market_checker_app.config import (
    CommodityEnergySourceConfig,
    SupplyChainSourceConfig,
)


@dataclass(frozen=True, slots=True)
class FilingSupplyChainFinding:
    source: SupplyChainSourceConfig
    support_term: str
    reason: str


@dataclass(frozen=True, slots=True)
class FilingCommodityFinding:
    source: CommodityEnergySourceConfig
    support_term: str
    reason: str


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
    "depend",
    "exposure",
)


def _sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return []
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?;])\s+", normalized)
        if sentence.strip()
    ]


class FilingExposureDiscoveryService:
    """Extract conservative, low-confidence exposure facts from SEC 10-K text.

    The parser records only explicit concentration or input-cost language.  It
    does not infer price direction, score the company, or persist filing text.
    """

    def discover(
        self,
        fetched: FetchedShortReport,
        *,
        max_supply_chain: int = 6,
        max_commodity_energy: int = 12,
    ) -> FilingExposureFindings:
        ticker = fetched.source.ticker.strip().upper()
        published_at = fetched.source.published_at
        url = fetched.final_url
        sentences = _sentences(fetched.text)

        supply: list[FilingSupplyChainFinding] = []
        seen_supply: set[tuple[str, str]] = set()

        def add_supply(
            *,
            counterparty: str,
            relationship_type: RelationshipType,
            support_term: str,
            reason: str,
            dependency_pct: float | None = None,
        ) -> None:
            if len(supply) >= max(0, int(max_supply_chain)):
                return
            key = (relationship_type.value, counterparty.casefold())
            if key in seen_supply:
                return
            seen_supply.add(key)
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
                    ),
                    support_term=support_term,
                    reason=reason,
                )
            )

        for sentence in sentences:
            customer = _CUSTOMER_CONCENTRATION.search(sentence)
            if customer:
                dependency = min(100.0, float(customer.group("pct")))
                add_supply(
                    counterparty="Unnamed major customer",
                    relationship_type=RelationshipType.CUSTOMER,
                    support_term=customer.group("phrase"),
                    reason="sec_10k_customer_concentration",
                    dependency_pct=dependency,
                )
            supplier = _SUPPLIER_CONCENTRATION.search(sentence)
            if supplier:
                add_supply(
                    counterparty="Unnamed critical supplier",
                    relationship_type=RelationshipType.SUPPLIER,
                    support_term=supplier.group("phrase"),
                    reason="sec_10k_supplier_concentration",
                )
            manufacturer = _CONTRACT_MANUFACTURING.search(sentence)
            if manufacturer:
                add_supply(
                    counterparty="Unnamed contract manufacturer",
                    relationship_type=RelationshipType.CONTRACT_MANUFACTURER,
                    support_term=manufacturer.group("phrase"),
                    reason="sec_10k_contract_manufacturing",
                )

        commodities: list[FilingCommodityFinding] = []
        seen_resources: set[str] = set()
        for sentence in sentences:
            lowered = sentence.casefold()
            if not any(marker in lowered for marker in _EXPOSURE_CONTEXT):
                continue
            for resource_name, exposure_type, terms in _RESOURCE_RULES:
                matched = next((term for term in terms if term in lowered), None)
                if matched is None or resource_name in seen_resources:
                    continue
                if len(commodities) >= max(0, int(max_commodity_energy)):
                    break
                seen_resources.add(resource_name)
                commodities.append(
                    FilingCommodityFinding(
                        source=CommodityEnergySourceConfig(
                            ticker=ticker,
                            resource_name=resource_name,
                            exposure_type=exposure_type.value,
                            publisher="SEC EDGAR",
                            published_at=published_at,
                            url=url,
                            confidence=0.40,
                        ),
                        support_term=matched,
                        reason="sec_10k_material_or_energy_exposure",
                    )
                )

        return FilingExposureFindings(
            supply_chain=tuple(supply),
            commodity_energy=tuple(commodities),
        )
