from __future__ import annotations

from datetime import datetime, timezone
import re

from market_checker_app.config import (
    EuropeanFilingFeedConfig,
    EuropeanFilingSourceConfig,
)
from market_checker_app.utils.entity_identifiers import (
    normalize_cik,
    normalize_country_code,
    normalize_isin,
    normalize_lei,
    normalize_mic,
)
from market_checker_app.utils.source_validation import public_https_reference
from market_checker_app.utils.text import normalize_ticker


_MISSING = {"", "-", "N/A", "n/a", "NONE", "None", "null", "NULL"}


def _optional(value: object) -> str | None:
    normalized = str(value or "").strip()
    return None if normalized in _MISSING else normalized


def _boolean(value: object, label: str) -> bool:
    normalized = str(value or "").strip().casefold()
    if normalized in {"true", "1", "yes", "ano"}:
        return True
    if normalized in {"false", "0", "no", "ne"}:
        return False
    raise ValueError(f"{label} musí být true/false")


def _timestamp(value: object, label: str) -> datetime:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"chybí {label}")
    parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _optional_timestamp(value: object, label: str) -> datetime | None:
    normalized = _optional(value)
    return _timestamp(normalized, label) if normalized is not None else None


def parse_identity_records(
    value: str,
) -> tuple[dict[str, dict[str, object]], list[str]]:
    """Parse exact ticker identities; no name-only or fuzzy lookup is allowed.

    Format:
    TICKER | legal name | CIK/- | ISIN/- | LEI/- | MIC/- | country/- |
    exchange/- | source | HTTPS source URL
    """

    records: dict[str, dict[str, object]] = {}
    errors: list[str] = []
    for line_number, raw_line in enumerate(str(value or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 10:
            errors.append(
                f"Identity řádek {line_number}: očekávám TICKER | právní název | "
                "CIK/- | ISIN/- | LEI/- | MIC/- | země/- | burza/- | zdroj | HTTPS URL."
            )
            continue
        (
            raw_ticker,
            name,
            raw_cik,
            raw_isin,
            raw_lei,
            raw_mic,
            raw_country,
            raw_exchange,
            source,
            raw_url,
        ) = parts
        ticker = normalize_ticker(raw_ticker)
        try:
            if not ticker or not name or not source:
                raise ValueError("chybí ticker, právní název nebo zdroj")
            cik = normalize_cik(_optional(raw_cik))
            isin = normalize_isin(_optional(raw_isin))
            lei = normalize_lei(_optional(raw_lei))
            mic = normalize_mic(_optional(raw_mic))
            country_code = normalize_country_code(_optional(raw_country))
            if not any((cik, isin, lei)):
                raise ValueError("je nutný alespoň jeden přesný CIK, ISIN nebo LEI")
            source_url = public_https_reference(raw_url)
        except (TypeError, ValueError) as exc:
            errors.append(f"Identity řádek {line_number}: {exc}.")
            continue
        record: dict[str, object] = {
            "entity_id": f"listing:{ticker.lower()}",
            "ticker": ticker,
            "name": name,
            "cik": cik,
            "isin": isin,
            "lei": lei,
            "mic": mic,
            "country_code": country_code,
            "exchange": _optional(raw_exchange),
            "source": source,
            "source_url": source_url,
            "confidence": 1.0,
            "metadata": {
                "runtime_identity_manifest": True,
                "name_matching_used": False,
            },
        }
        record = {key: item for key, item in record.items() if item is not None}
        existing = records.get(ticker)
        if existing is not None and existing != record:
            errors.append(
                f"Identity řádek {line_number}: ticker {ticker} má konfliktní záznam."
            )
            continue
        records[ticker] = record
    return records, errors


def parse_european_filing_sources(
    value: str,
) -> tuple[tuple[EuropeanFilingSourceConfig, ...], list[str]]:
    """Parse exact European document URLs with exact issuer identifiers.

    Format:
    TICKER | authority | document type | title | published at |
    reporting period/- | LEI/- | ISIN/- | audited | ESEF | language/- |
    canonical key/- | HTTPS URL
    """

    sources: list[EuropeanFilingSourceConfig] = []
    errors: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for line_number, raw_line in enumerate(str(value or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 13:
            errors.append(
                f"Evropský filing řádek {line_number}: očekávám TICKER | autorita | "
                "typ | název | datum zveřejnění | období/- | LEI/- | ISIN/- | "
                "audit true/false | ESEF true/false | jazyk/- | canonical key/- | HTTPS URL."
            )
            continue
        (
            raw_ticker,
            authority,
            document_type,
            title,
            raw_published,
            raw_period,
            raw_lei,
            raw_isin,
            raw_audited,
            raw_esef,
            raw_language,
            raw_canonical_key,
            raw_url,
        ) = parts
        ticker = normalize_ticker(raw_ticker)
        try:
            if not ticker or not authority or not document_type or not title:
                raise ValueError("chybí ticker, autorita, typ nebo název dokumentu")
            lei = normalize_lei(_optional(raw_lei))
            isin = normalize_isin(_optional(raw_isin))
            if not (lei or isin):
                raise ValueError("dokument musí mít přesný LEI nebo ISIN")
            published_at = _timestamp(raw_published, "datum zveřejnění")
            reporting_period_end = _optional_timestamp(raw_period, "účetní období")
            audited = _boolean(raw_audited, "audit")
            esef = _boolean(raw_esef, "ESEF")
            url = public_https_reference(raw_url)
        except (TypeError, ValueError) as exc:
            errors.append(f"Evropský filing řádek {line_number}: {exc}.")
            continue
        key = (ticker, url, published_at.isoformat())
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            EuropeanFilingSourceConfig(
                ticker=ticker,
                authority=authority,
                document_type=document_type,
                title=title,
                published_at=published_at,
                url=url,
                lei=lei,
                isin=isin,
                reporting_period_end=reporting_period_end,
                audited=audited,
                esef=esef,
                language=_optional(raw_language),
                canonical_event_key=_optional(raw_canonical_key),
                discovery_method="manual",
            )
        )
    return tuple(sources), errors


def parse_european_filing_feeds(
    value: str,
) -> tuple[tuple[EuropeanFilingFeedConfig, ...], list[str]]:
    """Parse issuer-identity-scoped official RSS/Atom discovery feeds.

    A discovered entry is accepted only when its text contains the configured
    exact LEI or ISIN.  The system never assigns an issuer by similar name.

    Format:
    TICKER | authority | document type | LEI/- | ISIN/- | audited | ESEF |
    language/- | max entries | HTTPS feed URL
    """

    feeds: list[EuropeanFilingFeedConfig] = []
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for line_number, raw_line in enumerate(str(value or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 10:
            errors.append(
                f"Evropský feed řádek {line_number}: očekávám TICKER | autorita | "
                "typ | LEI/- | ISIN/- | audit true/false | ESEF true/false | "
                "jazyk/- | max položek | HTTPS feed URL."
            )
            continue
        (
            raw_ticker,
            authority,
            document_type,
            raw_lei,
            raw_isin,
            raw_audited,
            raw_esef,
            raw_language,
            raw_max_entries,
            raw_url,
        ) = parts
        ticker = normalize_ticker(raw_ticker)
        try:
            if not ticker or not authority or not document_type:
                raise ValueError("chybí ticker, autorita nebo typ dokumentu")
            lei = normalize_lei(_optional(raw_lei))
            isin = normalize_isin(_optional(raw_isin))
            if not (lei or isin):
                raise ValueError("feed musí mít přesný LEI nebo ISIN")
            audited = _boolean(raw_audited, "audit")
            esef = _boolean(raw_esef, "ESEF")
            max_entries = int(raw_max_entries)
            feed_url = public_https_reference(raw_url)
            feed = EuropeanFilingFeedConfig(
                ticker=ticker,
                authority=authority,
                document_type=document_type,
                feed_url=feed_url,
                lei=lei,
                isin=isin,
                audited=audited,
                esef=esef,
                language=_optional(raw_language),
                max_entries=max_entries,
            )
        except (TypeError, ValueError) as exc:
            errors.append(f"Evropský feed řádek {line_number}: {exc}.")
            continue
        key = (ticker, feed.feed_url)
        if key in seen:
            continue
        seen.add(key)
        feeds.append(feed)
    return tuple(feeds), errors


def parse_european_allowed_hosts(value: str) -> tuple[tuple[str, ...], list[str]]:
    """Parse explicit host suffixes for issuer IR and local exchanges."""

    hosts: list[str] = []
    errors: list[str] = []
    for line_number, raw_line in enumerate(str(value or "").splitlines(), start=1):
        host = raw_line.strip().lower().lstrip(".")
        if not host or host.startswith("#"):
            continue
        if (
            "://" in host
            or "/" in host
            or host in {"localhost", "local"}
            or host.endswith((".localhost", ".local"))
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)
            or "." not in host
        ):
            errors.append(
                f"Evropský allowlist řádek {line_number}: očekávám pouze veřejný hostname, například investor.example.com."
            )
            continue
        if host not in hosts:
            hosts.append(host)
    return tuple(hosts), errors
