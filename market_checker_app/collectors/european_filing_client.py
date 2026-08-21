from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from market_checker_app.collectors.short_report_client import (
    FetchedShortReport,
    ShortReportClient,
)
from market_checker_app.config import (
    EuropeanFilingConfig,
    EuropeanFilingSourceConfig,
    ShortReportSourceConfig,
)
from market_checker_app.utils.source_validation import public_https_reference


class EuropeanFilingError(RuntimeError):
    """Raised when a European filing source violates its authority policy."""


@dataclass(frozen=True, slots=True)
class FetchedEuropeanFiling:
    source: EuropeanFilingSourceConfig
    final_url: str
    mime_type: str
    title: str
    text: str
    content_hash: str
    size_bytes: int
    extractor: str


AUTHORITY_HOST_SUFFIXES = {
    "EURONEXT": ("euronext.com",),
    "FCA_NSM": ("fca.org.uk",),
    "FCA_RNS": ("fca.org.uk", "londonstockexchange.com"),
    "RNS": ("londonstockexchange.com",),
    "AFM": ("afm.nl",),
    "BAFIN": ("bafin.de",),
    "CNB": ("cnb.cz",),
}
CONFIGURABLE_AUTHORITIES = {"LOCAL_EXCHANGE", "ISSUER_IR"}


def normalized_authority(value: object) -> str:
    return str(value or "").strip().upper().replace("/", "_").replace("-", "_")


def _host_matches(hostname: str, suffix: str) -> bool:
    normalized = suffix.strip().lower().lstrip(".")
    return bool(normalized) and (
        hostname == normalized or hostname.endswith(f".{normalized}")
    )


class EuropeanFilingClient:
    """Fetch direct official documents; discovery endpoints remain configurable."""

    def __init__(
        self,
        config: EuropeanFilingConfig,
        *,
        client: ShortReportClient | None = None,
    ) -> None:
        self.config = config
        self._client = client or ShortReportClient(
            user_agent=config.user_agent,
            timeout_seconds=config.request_timeout_seconds,
            max_download_bytes=config.max_download_bytes,
            max_text_characters=config.max_text_characters,
        )

    def _validate_authority_url(
        self,
        source: EuropeanFilingSourceConfig,
        raw_url: str,
    ) -> str:
        url = public_https_reference(raw_url)
        hostname = str(urlparse(url).hostname or "").lower()
        authority = normalized_authority(source.authority)
        allowed_suffixes = AUTHORITY_HOST_SUFFIXES.get(authority)
        if allowed_suffixes is None:
            if authority not in CONFIGURABLE_AUTHORITIES:
                raise EuropeanFilingError(
                    f"Unsupported European filing authority: {source.authority}"
                )
            allowed_suffixes = tuple(self.config.allowed_local_exchange_hosts)
        if not allowed_suffixes or not any(
            _host_matches(hostname, suffix) for suffix in allowed_suffixes
        ):
            raise EuropeanFilingError(
                f"Host {hostname!r} is not approved for authority {authority}"
            )
        return url

    def validate_source(self, source: EuropeanFilingSourceConfig) -> str:
        return self._validate_authority_url(source, source.url)

    def fetch(self, source: EuropeanFilingSourceConfig) -> FetchedEuropeanFiling:
        url = self.validate_source(source)
        fetched: FetchedShortReport = self._client.fetch(
            ShortReportSourceConfig(
                ticker=source.ticker,
                publisher=normalized_authority(source.authority),
                published_at=source.published_at,
                url=url,
                discovery_method="official_document",
            )
        )
        final_url = self._validate_authority_url(source, fetched.final_url)
        return FetchedEuropeanFiling(
            source=source,
            final_url=final_url,
            mime_type=fetched.mime_type,
            title=fetched.title,
            text=fetched.text,
            content_hash=fetched.content_hash,
            size_bytes=fetched.size_bytes,
            extractor=fetched.extractor,
        )
