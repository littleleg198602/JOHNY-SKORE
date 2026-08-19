from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


class PublicSourceError(ValueError):
    """Raised when a source reference is not a safe public HTTPS URL."""


def public_https_reference(url: str) -> str:
    """Validate a reference URL without resolving or requesting its host."""

    normalized = str(url or "").strip()
    parsed = urlparse(normalized)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise PublicSourceError("Zdroj musí být veřejná HTTPS adresa.")
    if parsed.username or parsed.password:
        raise PublicSourceError("Zdrojová URL nesmí obsahovat přihlašovací údaje.")
    hostname = parsed.hostname.rstrip(".").lower()
    if (
        hostname == "localhost"
        or hostname.endswith(".localhost")
        or hostname.endswith(".local")
    ):
        raise PublicSourceError("Lokální zdrojové adresy nejsou povolené.")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise PublicSourceError("Privátní nebo lokální IP adresy nejsou povolené.")
    return normalized
