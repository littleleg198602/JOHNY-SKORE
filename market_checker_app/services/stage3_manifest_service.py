from __future__ import annotations

from datetime import datetime, timezone
import math

from market_checker_app.agents.contracts import (
    RegulatoryContractEventType,
    RegulatoryEventStatus,
    RelationshipType,
    ResourceExposureType,
)
from market_checker_app.config import (
    CommodityEnergySourceConfig,
    RegulatoryContractSourceConfig,
    SupplyChainSourceConfig,
)
from market_checker_app.utils.source_validation import public_https_reference
from market_checker_app.utils.text import normalize_ticker


def _published_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _optional_number(value: str, *, percentage: bool = False) -> float | None:
    normalized = str(value or "").strip()
    if normalized in {"", "-", "N/A", "n/a", "NONE", "None"}:
        return None
    numeric = float(normalized.replace(" ", "").replace(",", "."))
    if not math.isfinite(numeric) or numeric < 0.0:
        raise ValueError("hodnota musí být nezáporné konečné číslo")
    if percentage and numeric > 100.0:
        raise ValueError("podíl musí být mezi 0 a 100 %")
    return numeric


def parse_supply_chain_sources(
    value: str,
) -> tuple[tuple[SupplyChainSourceConfig, ...], list[str]]:
    """Parse supply-chain rows without source discovery or network access."""

    sources: list[SupplyChainSourceConfig] = []
    errors: list[str] = []
    seen: set[tuple[object, ...]] = set()
    for line_number, raw_line in enumerate(str(value or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 7:
            errors.append(
                f"Síť firem řádek {line_number}: očekávám TICKER | protistrana | typ | podíl %/- | vydavatel | datum | HTTPS URL."
            )
            continue
        raw_ticker, counterparty, raw_type, raw_share, publisher, raw_date, raw_url = parts
        ticker = normalize_ticker(raw_ticker)
        try:
            if not ticker or not counterparty or not publisher:
                raise ValueError("chybí ticker, protistrana nebo vydavatel")
            relationship_type = RelationshipType(raw_type.upper())
            dependency_pct = _optional_number(raw_share, percentage=True)
            published_at = _published_at(raw_date)
            url = public_https_reference(raw_url)
        except (TypeError, ValueError) as exc:
            errors.append(f"Síť firem řádek {line_number}: {exc}.")
            continue
        key = (
            ticker,
            counterparty.casefold(),
            relationship_type.value,
            dependency_pct,
            publisher.casefold(),
            published_at.isoformat(),
            url,
        )
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            SupplyChainSourceConfig(
                ticker=ticker,
                counterparty=counterparty,
                relationship_type=relationship_type.value,
                dependency_pct=dependency_pct,
                publisher=publisher,
                published_at=published_at,
                url=url,
            )
        )
    return tuple(sources), errors


def parse_commodity_energy_sources(
    value: str,
) -> tuple[tuple[CommodityEnergySourceConfig, ...], list[str]]:
    """Parse material and energy exposure rows with explicit provenance."""

    sources: list[CommodityEnergySourceConfig] = []
    errors: list[str] = []
    seen: set[tuple[object, ...]] = set()
    for line_number, raw_line in enumerate(str(value or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 7:
            errors.append(
                f"Materiály/energie řádek {line_number}: očekávám TICKER | zdroj | typ | podíl %/- | vydavatel | datum | HTTPS URL."
            )
            continue
        raw_ticker, resource_name, raw_type, raw_share, publisher, raw_date, raw_url = parts
        ticker = normalize_ticker(raw_ticker)
        try:
            if not ticker or not resource_name or not publisher:
                raise ValueError("chybí ticker, zdroj/komodita nebo vydavatel")
            exposure_type = ResourceExposureType(raw_type.upper())
            dependency_pct = _optional_number(raw_share, percentage=True)
            published_at = _published_at(raw_date)
            url = public_https_reference(raw_url)
        except (TypeError, ValueError) as exc:
            errors.append(f"Materiály/energie řádek {line_number}: {exc}.")
            continue
        key = (
            ticker,
            resource_name.casefold(),
            exposure_type.value,
            dependency_pct,
            publisher.casefold(),
            published_at.isoformat(),
            url,
        )
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            CommodityEnergySourceConfig(
                ticker=ticker,
                resource_name=resource_name,
                exposure_type=exposure_type.value,
                dependency_pct=dependency_pct,
                publisher=publisher,
                published_at=published_at,
                url=url,
            )
        )
    return tuple(sources), errors


def parse_regulatory_contract_sources(
    value: str,
) -> tuple[tuple[RegulatoryContractSourceConfig, ...], list[str]]:
    """Parse contract/regulatory rows and keep optional amounts non-directional."""

    sources: list[RegulatoryContractSourceConfig] = []
    errors: list[str] = []
    seen: set[tuple[object, ...]] = set()
    for line_number, raw_line in enumerate(str(value or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 10:
            errors.append(
                f"Regulace/kontrakty řádek {line_number}: očekávám TICKER | typ | stav | název | protistrana/úřad | hodnota/- | měna/- | vydavatel | datum | HTTPS URL."
            )
            continue
        (
            raw_ticker,
            raw_type,
            raw_status,
            title,
            authority,
            raw_value,
            raw_currency,
            publisher,
            raw_date,
            raw_url,
        ) = parts
        ticker = normalize_ticker(raw_ticker)
        try:
            if not ticker or not title or not authority or not publisher:
                raise ValueError(
                    "chybí ticker, název, protistrana/úřad nebo vydavatel"
                )
            event_type = RegulatoryContractEventType(raw_type.upper())
            status = RegulatoryEventStatus(raw_status.upper())
            event_value = _optional_number(raw_value)
            currency = str(raw_currency or "").strip().upper()
            if currency in {"", "-", "N/A", "NONE"}:
                currency = None
            if event_value is not None and (
                currency is None or len(currency) != 3 or not currency.isalpha()
            ):
                raise ValueError("číselná hodnota vyžaduje třípísmennou měnu")
            published_at = _published_at(raw_date)
            url = public_https_reference(raw_url)
        except (TypeError, ValueError) as exc:
            errors.append(f"Regulace/kontrakty řádek {line_number}: {exc}.")
            continue
        key = (
            ticker,
            event_type.value,
            status.value,
            title.casefold(),
            authority.casefold(),
            event_value,
            currency,
            publisher.casefold(),
            published_at.isoformat(),
            url,
        )
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            RegulatoryContractSourceConfig(
                ticker=ticker,
                event_type=event_type.value,
                status=status.value,
                title=title,
                authority_or_counterparty=authority,
                event_value=event_value,
                currency=currency,
                publisher=publisher,
                published_at=published_at,
                url=url,
            )
        )
    return tuple(sources), errors
