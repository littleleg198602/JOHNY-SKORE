"""Load optional reviewed identity inputs without leaking configured values."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from market_checker_app.services.sec_counterparty_identity import (
    DEFAULT_COUNTERPARTY_IDENTITIES, DatedCounterpartyIdentityRegistry,
)
from market_checker_app.services.regulatory_identity_service import RegulatoryIdentityRegistry


def configured_counterparty_identities(
    environment: Mapping[str, str] | None = None,
) -> tuple[DatedCounterpartyIdentityRegistry, str]:
    env = os.environ if environment is None else environment
    configured = env.get("JOHNY_SKORE_COUNTERPARTY_IDENTITIES_FILE", "").strip()
    path = Path(configured) if configured else DEFAULT_COUNTERPARTY_IDENTITIES
    if not configured and not path.exists():
        return DatedCounterpartyIdentityRegistry([]), "UNCONFIGURED"
    try:
        return DatedCounterpartyIdentityRegistry.load(path), "LOADED"
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
