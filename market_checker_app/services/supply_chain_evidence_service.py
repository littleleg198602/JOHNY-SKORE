"""Normalize auditable supplier/customer evidence without inferring a graph.

The service provides a deliberately small evidence contract for Stage 3.  A
missing supplier identity, country, product or period remains explicit rather
than being filled from a name or a market-data lookup.  It has no scoring or
model-selection role.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from market_checker_app.agents.contracts import RelationshipType


ANONYMOUS_COUNTERPARTY_PREFIX = "unnamed "


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _text(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _identity_status(counterparty: str, configured: object) -> str:
    value = _text(configured)
    if value:
        allowed = {"IDENTIFIED", "ANONYMOUS"}
        if value.upper() not in allowed:
            raise ValueError("counterparty_identity_status must be IDENTIFIED or ANONYMOUS")
        return value.upper()
    return (
        "ANONYMOUS"
        if counterparty.casefold().startswith(ANONYMOUS_COUNTERPARTY_PREFIX)
        else "IDENTIFIED"
    )


def _direction(relationship_type: RelationshipType) -> str:
    if relationship_type == RelationshipType.CUSTOMER:
        return "COMPANY_TO_COUNTERPARTY"
    return "COUNTERPARTY_TO_COMPANY"


def build_supply_chain_evidence_metadata(
    *,
    ticker: str,
    counterparty: str,
    relationship_type: RelationshipType,
    published_at: datetime,
    observed_at: datetime,
    source_url: str,
    disclosure_period: object = None,
    product_or_input: object = None,
    counterparty_country: object = None,
    counterparty_identity_status: object = None,
    relationship_context: object = None,
    evidence_quote: object = None,
    evidence_level: object = None,
    discovery_method: object = None,
    existing: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Return machine-readable provenance for one oriented relationship edge."""

    published = _utc(published_at)
    observed = _utc(observed_at)
    age_days = max(0, (observed.date() - published.date()).days)
    identity = _identity_status(counterparty, counterparty_identity_status)
    period = _text(disclosure_period)
    product = _text(product_or_input)
    country = _text(counterparty_country)
    quote = _text(evidence_quote)
    method = _text(discovery_method) or "manual"
    level = _text(evidence_level) or (
        "EXPLICIT_FILING" if method == "sec_filing" else "PUBLIC_SOURCE_REFERENCE"
    )
    context = _text(relationship_context) or "RELATIONSHIP_DISCLOSED"
    missingness = {
        "counterparty_identity": "NOT_DISCLOSED" if identity == "ANONYMOUS" else "KNOWN",
        "product_or_input": "UNKNOWN" if product is None else "KNOWN",
        "counterparty_country": "UNKNOWN" if country is None else "KNOWN",
        "disclosure_period": "UNKNOWN" if period is None else "KNOWN",
        "evidence_quote": "NOT_CAPTURED" if quote is None else "CAPTURED",
    }
    metadata = dict(existing or {})
    metadata.update(
        {
            "evidence_schema_version": "supply_chain_evidence_v1",
            "edge_direction": _direction(relationship_type),
            "edge_path": (
                f"{counterparty} -> {ticker}"
                if relationship_type != RelationshipType.CUSTOMER
                else f"{ticker} -> {counterparty}"
            ),
            "counterparty_identity_status": identity,
            "product_or_input": product,
            "counterparty_country": country,
            "disclosure_period": period,
            "relationship_context": context,
            "evidence_quote": quote,
            "evidence_level": level,
            "evidence_freshness": "STALE" if age_days > 400 else "FRESH",
            "evidence_age_days": age_days,
            "evidence_source_url": source_url,
            "evidence_missingness": missingness,
            "prediction_input": False,
            "scoring_applied": False,
        }
    )
    return metadata


__all__ = ["ANONYMOUS_COUNTERPARTY_PREFIX", "build_supply_chain_evidence_metadata"]
