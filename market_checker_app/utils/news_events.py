from __future__ import annotations

import hashlib
import re

from market_checker_app.utils.text import normalize_text


_STOP_WORDS = {
    "a", "an", "and", "at", "by", "for", "from", "in", "is", "of",
    "on", "or", "the", "to", "with",
}
_TERM_ALIASES = {
    "beats": "beat",
    "beating": "beat",
    "estimates": "estimate",
    "raises": "raise",
    "raised": "raise",
    "lifting": "raise",
    "lifts": "raise",
    "guidance": "outlook",
    "forecast": "outlook",
    "forecasts": "outlook",
}


def canonical_news_event_id(title: str) -> str:
    """Create a publisher-independent ID for an exact normalized headline."""
    normalized = normalize_text(title)
    if not normalized:
        return ""
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return f"news:v2:{digest}"


def news_event_terms(title: str, summary: str = "") -> frozenset[str]:
    """Return conservative comparable terms for cross-publisher clustering."""
    text = normalize_text(f"{title} {summary}")
    terms: set[str] = set()
    for token in re.findall(r"(?:[a-z]{2,}|\d+)", text):
        normalized = _TERM_ALIASES.get(token, token)
        if normalized not in _STOP_WORDS:
            terms.add(normalized)
    return frozenset(terms)


def headlines_describe_same_event(
    first_title: str,
    first_summary: str,
    second_title: str,
    second_summary: str,
) -> bool:
    """Conservatively match a rewritten headline only with enough overlap."""
    first = news_event_terms(first_title, first_summary)
    second = news_event_terms(second_title, second_summary)
    if not first or not second:
        return False
    if first == second:
        return True
    first_numbers = {term for term in first if term.isdigit()}
    second_numbers = {term for term in second if term.isdigit()}
    if first_numbers and second_numbers and first_numbers != second_numbers:
        return False
    overlap = len(first.intersection(second))
    return overlap >= 3 and overlap / min(len(first), len(second)) >= 0.60
