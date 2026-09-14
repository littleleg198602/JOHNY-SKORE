from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError

_RETRYABLE_HTTP = {429, 500, 502, 503, 504}

def _root_error(error: BaseException) -> BaseException:
    current = error
    while current.__cause__ is not None:
        current = current.__cause__
    return current

def source_failure_detail(*, source: str, ticker: str, url: str, error: BaseException, attempts: int | None = None, parser: str | None = None) -> dict[str, Any]:
    """Return a JSON-safe, actionable source failure record."""
    root = _root_error(error)
    http_status = root.code if isinstance(root, HTTPError) else None
    if http_status == 403:
        category, remediation = "ACCESS_DENIED", "Ověřit User-Agent, přístupovou politiku a URL zdroje."
    elif http_status == 429:
        category, remediation = "RATE_LIMITED", "Zachovat backoff; opakovat až po omezení zdroje."
    elif http_status in {404, 410}:
        category, remediation = "NOT_PUBLISHED", "Ověřit accession a primární dokument."
    elif http_status is not None:
        category, remediation = "HTTP_ERROR", "Zkontrolovat HTTP stav a dostupnost zdroje."
    elif isinstance(root, TimeoutError):
        category, remediation = "TIMEOUT", "Dočasný výpadek; opakovat omezený počet pokusů."
    elif isinstance(root, URLError):
        category, remediation = "NETWORK_UNAVAILABLE", "Zkontrolovat síť/DNS; opakovat později."
    elif isinstance(root, (UnicodeError, ValueError)):
        category, remediation = "PARSER_ERROR", "Dokument je dostupný, ale nelze jej bezpečně zpracovat."
    else:
        category, remediation = "UNKNOWN", "Zkontrolovat detail chyby a zdrojovou URL."
    return {
        "source": source,
        "ticker": ticker,
        "url": url,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "category": category,
        "remediation": remediation,
        "http_status": http_status,
        "attempts": max(1, int(attempts or getattr(error, "attempts", 1))),
        "error_type": type(root).__name__,
        "error": str(root),
        "parser": parser,
    }

def is_retryable_transport_error(error: BaseException) -> bool:
    root = _root_error(error)
    if isinstance(root, HTTPError):
        return root.code in _RETRYABLE_HTTP
    return isinstance(root, (URLError, TimeoutError))
