from __future__ import annotations

from market_checker_app.utils.text import normalize_ticker


# Yahoo uses hyphens for these US class-share symbols.  Keep the mapping in a
# dependency-free utility so entity registration does not need to import the
# network collector (and, transitively, yfinance).
YAHOO_SYMBOL_ALIASES: dict[str, str] = {
    "BRK.B": "BRK-B",
    "BF.B": "BF-B",
}


def normalize_yahoo_symbol(ticker: str) -> str:
    normalized = normalize_ticker(ticker)
    return YAHOO_SYMBOL_ALIASES.get(normalized, normalized)
