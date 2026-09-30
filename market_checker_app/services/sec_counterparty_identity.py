from __future__ import annotations

import re
from collections.abc import Mapping

from market_checker_app.collectors.sec_edgar_client import SecCompany


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
