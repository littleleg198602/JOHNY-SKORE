"""Load optional reviewed identity inputs without leaking configured values."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from market_checker_app.services.sec_counterparty_identity import (
    DEFAULT_COUNTERPARTY_IDENTITIES, DatedCounterpartyIdentityRegistry,
)
from market_checker_app.services.regulatory_identity_service import RegulatoryIdentityRegistry

DEFAULT_SUBSIDIARY_IDENTITIES = Path(__file__).resolve().parents[1] / 'data/verified_subsidiary_identities.json'


@lru_cache(maxsize=4)
def _combined_registries(base, supplement):
    return DatedCounterpartyIdentityRegistry([*base.records(), *supplement.records()])


def configured_counterparty_identities(
    environment: Mapping[str, str] | None = None,
) -> tuple[DatedCounterpartyIdentityRegistry, str]:
    env = os.environ if environment is None else environment
    configured = env.get("JOHNY_SKORE_COUNTERPARTY_IDENTITIES_FILE", "").strip()
    path = Path(configured) if configured else DEFAULT_COUNTERPARTY_IDENTITIES
    if not configured and not path.exists():
        return DatedCounterpartyIdentityRegistry([]), "UNCONFIGURED"
    try:
        registry = DatedCounterpartyIdentityRegistry.load(path)
        if not configured:
            supplement = DatedCounterpartyIdentityRegistry.load(DEFAULT_SUBSIDIARY_IDENTITIES)
            registry = _combined_registries(registry, supplement)
        return registry, "LOADED"
    except (OSError, ValueError, TypeError, KeyError):
        return DatedCounterpartyIdentityRegistry([]), "INVALID_IDENTITY_MANIFEST"


def configured_regulatory_identities(
    environment: Mapping[str, str] | None = None,
) -> tuple[RegulatoryIdentityRegistry, str]:
    env = os.environ if environment is None else environment
    configured = env.get("JOHNY_SKORE_REGULATORY_IDENTITIES_FILE", "").strip()
    if not configured:
        return RegulatoryIdentityRegistry(), "UNCONFIGURED"
    try:
        return RegulatoryIdentityRegistry.load(Path(configured)), "LOADED"
    except (OSError, ValueError, TypeError, KeyError):
        return RegulatoryIdentityRegistry(), "INVALID_IDENTITY_MANIFEST"
