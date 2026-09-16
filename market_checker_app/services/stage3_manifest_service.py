from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
import math

from market_checker_app.agents.contracts import (
    RegulatoryContractEventType,
    RegulatoryEventStatus,
    RelationshipType,
    ResourceExposureType,
)
from market_checker_app.agents.source_policy import source_priority_for
from market_checker_app.config import (
    CommodityEnergySourceConfig,
    ResourcePricePointConfig,
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


def _optional_signed_number(value: str) -> float | None:
    normalized = str(value or "").strip()
    if normalized in {"", "-", "N/A", "n/a", "NONE", "None"}:
        return None
    numeric = float(normalized.replace(" ", "").replace(",", "."))
    if not math.isfinite(numeric):
        raise ValueError("hodnota musí být konečné číslo")
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
        if len(parts) not in {7, 14}:
            errors.append(
                f"Síť firem řádek {line_number}: očekávám 7 základních polí nebo 14 polí včetně identity, produktu, země, období, kontextu, úrovně důkazu a citace."
            )
            continue
        raw_ticker, counterparty, raw_type, raw_share, publisher, raw_date, raw_url = parts[:7]
        optional = parts[7:] if len(parts) == 14 else ["-"] * 7
        raw_identity, raw_product, raw_country, raw_period, raw_context, raw_level, raw_quote = optional
        ticker = normalize_ticker(raw_ticker)
        try:
            if not ticker or not counterparty or not publisher:
                raise ValueError("chybí ticker, protistrana nebo vydavatel")
            relationship_type = RelationshipType(raw_type.upper())
            dependency_pct = _optional_number(raw_share, percentage=True)
            published_at = _published_at(raw_date)
            url = public_https_reference(raw_url)
            identity = None if raw_identity in {"", "-"} else raw_identity.upper()
            if identity not in {None, "IDENTIFIED", "ANONYMOUS"}:
                raise ValueError("identita protistrany musí být IDENTIFIED, ANONYMOUS nebo -")
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
                counterparty_identity_status=identity,
                product_or_input=None if raw_product in {"", "-"} else raw_product,
                counterparty_country=None if raw_country in {"", "-"} else raw_country,
                disclosure_period=None if raw_period in {"", "-"} else raw_period,
                relationship_context=None if raw_context in {"", "-"} else raw_context,
                evidence_level=None if raw_level in {"", "-"} else raw_level,
                evidence_quote=None if raw_quote in {"", "-"} else raw_quote,
            )
        )
    return tuple(sources), errors


def parse_commodity_energy_sources(
    value: str,
) -> tuple[tuple[CommodityEnergySourceConfig, ...], list[str]]:
    """Parse material and energy exposure rows with explicit provenance."""

    sources: list[CommodityEnergySourceConfig] = []
    errors: list[str] = []
    seen: dict[tuple[object, ...], int] = {}
    for line_number, raw_line in enumerate(str(value or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) not in {7, 19}:
            errors.append(
                f"Materiály/energie řádek {line_number}: očekávám 7 základních polí nebo 19 polí včetně nákladového podílu, hedge/fixace, scénáře a datované ceny."
            )
            continue
        raw_ticker, resource_name, raw_type, raw_share, publisher, raw_date, raw_url = parts[:7]
        optional = parts[7:] if len(parts) == 19 else ["-"] * 12
        (
            raw_cost_share,
            raw_hedged_share,
            raw_fixed_share,
            raw_pass_through,
            raw_scenario_change,
            raw_unit,
            raw_currency,
            raw_price_observed_at,
            raw_price_available_at,
            raw_price_value,
            raw_period,
            raw_quote,
        ) = optional
        ticker = normalize_ticker(raw_ticker)
        try:
            if not ticker or not resource_name or not publisher:
                raise ValueError("chybí ticker, zdroj/komodita nebo vydavatel")
            exposure_type = ResourceExposureType(raw_type.upper())
            dependency_pct = _optional_number(raw_share, percentage=True)
            published_at = _published_at(raw_date)
            url = public_https_reference(raw_url)
            cost_share = _optional_number(raw_cost_share, percentage=True)
            hedged_share = _optional_number(raw_hedged_share, percentage=True)
            fixed_share = _optional_number(raw_fixed_share, percentage=True)
            pass_through = _optional_number(raw_pass_through, percentage=True)
            scenario_change = _optional_signed_number(raw_scenario_change)
            price_fields = (
                raw_unit,
                raw_currency,
                raw_price_observed_at,
                raw_price_available_at,
                raw_price_value,
            )
            price_present = [item not in {"", "-"} for item in price_fields]
            if any(price_present) and not all(price_present):
                raise ValueError("datovaný cenový bod musí mít jednotku, měnu, observed_at, available_at a hodnotu")
            price_points: tuple[ResourcePricePointConfig, ...] = ()
            if all(price_present):
                price_value = _optional_number(raw_price_value)
                assert price_value is not None
                price_points = (
                    ResourcePricePointConfig(
                        observed_at=_published_at(raw_price_observed_at),
                        available_at=_published_at(raw_price_available_at),
                        value=price_value,
                        unit=raw_unit,
                        currency=raw_currency.upper(),
                        source_url=url,
                    ),
                )
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
            cost_share,
            hedged_share,
            fixed_share,
            pass_through,
            scenario_change,
            raw_period,
            raw_quote,
        )
        existing_index = seen.get(key)
        if existing_index is not None:
            if price_points:
                existing = sources[existing_index]
                sources[existing_index] = replace(
                    existing,
                    price_points=existing.price_points + price_points,
                )
            continue
        seen[key] = len(sources)
        sources.append(
            CommodityEnergySourceConfig(
                ticker=ticker,
                resource_name=resource_name,
                exposure_type=exposure_type.value,
                dependency_pct=dependency_pct,
                publisher=publisher,
                published_at=published_at,
                url=url,
                disclosed_cost_share_of_revenue_pct=cost_share,
                hedged_share_pct=hedged_share,
                fixed_price_share_pct=fixed_share,
                pass_through_pct=pass_through,
                scenario_price_change_pct=scenario_change,
                disclosure_period=None if raw_period in {"", "-"} else raw_period,
                evidence_quote=None if raw_quote in {"", "-"} else raw_quote,
                price_points=price_points,
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
        if len(parts) not in {10, 11, 12, 13}:
            errors.append(
                f"Regulace/kontrakty řádek {line_number}: očekávám TICKER | typ | stav | název | protistrana/úřad | hodnota/- | měna/- | vydavatel | datum | HTTPS URL | source type (volitelné) | source authority/- (volitelné) | canonical key/- (volitelné)."
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
        ) = parts[:10]
        raw_source_type = parts[10] if len(parts) >= 11 else "media_article"
        raw_source_authority = parts[11] if len(parts) >= 12 else "-"
        raw_canonical_key = parts[12] if len(parts) >= 13 else "-"
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
            source_type = str(raw_source_type or "").strip().lower()
            if source_priority_for(source_type) <= 0:
                raise ValueError(
                    "source type musí být regulatory_filing, audited_financial_statement, exchange_announcement, investor_relations, management_presentation nebo media_article"
                )
            source_authority = str(raw_source_authority or "").strip()
            if source_authority.upper() in {"", "-", "N/A", "NONE"}:
                source_authority = None
            canonical_event_key = str(raw_canonical_key or "").strip()
            if canonical_event_key.upper() in {"", "-", "N/A", "NONE"}:
                canonical_event_key = None
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
            source_type,
            source_authority,
            canonical_event_key,
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
                source_type=source_type,
                source_authority=source_authority,
                canonical_event_key=canonical_event_key,
            )
        )
    return tuple(sources), errors
