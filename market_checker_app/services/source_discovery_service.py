from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from market_checker_app.config import (
    RegulatoryContractSourceConfig,
    ShortReportSourceConfig,
)
from market_checker_app.models import NewsItem
from market_checker_app.utils.source_validation import PublicSourceError, public_https_reference
from market_checker_app.utils.text import normalize_ticker


SHORT_REPORT_MARKERS = (
    "short report",
    "short-seller report",
    "short seller report",
    "activist short",
    "report alleges",
    "research alleges",
)
KNOWN_SHORT_PUBLISHERS = {
    "hindenburg": ("Hindenburg Research", ("hindenburgresearch.com",)),
    "muddy waters": ("Muddy Waters Research", ("muddywatersresearch.com",)),
    "gotham city": ("Gotham City Research", ("gothamcityresearch.com",)),
    "grizzly research": ("Grizzly Research", ("grizzlyreports.com",)),
    "viceroy research": ("Viceroy Research", ("viceroyresearch.org",)),
    "fuzzy panda": ("Fuzzy Panda Research", ("fuzzypandaresearch.com",)),
    "spruce point": ("Spruce Point Capital Management", ("sprucepointcap.com",)),
    "blue orca": ("Blue Orca Capital", ("blueorcacapital.com",)),
    "wolfpack research": ("Wolfpack Research", ("wolfpackresearch.com",)),
    "culper research": ("Culper Research", ("culperresearch.com",)),
    "scorpion capital": ("Scorpion Capital", ("scorpioncapital.com",)),
}


REGULATORY_RULES = (
    (("contract award", "awarded a contract", "wins contract"), "CONTRACT_AWARD"),
    (("contract loss", "loses contract", "contract terminated"), "CONTRACT_LOSS"),
    (("investigation", "regulatory probe", "antitrust probe"), "INVESTIGATION"),
    (("sanction", "regulatory fine", "fined by"), "SANCTION"),
    (("regulatory approval", "fda approves", "approved by the fda"), "REGULATORY_APPROVAL"),
    (("license suspended", "license revoked", "licence suspended"), "LICENSE_CHANGE"),
    (("government grant", "awarded a grant"), "GRANT"),
)


@dataclass(frozen=True, slots=True)
class DiscoveredAgentSources:
    short_reports: tuple[ShortReportSourceConfig, ...] = ()
    regulatory_events: tuple[RegulatoryContractSourceConfig, ...] = ()


class SourceDiscoveryService:
    """Conservative discovery over already collected, dated RSS news items."""

    @staticmethod
    def _public_url(item: NewsItem) -> str | None:
        try:
            return public_https_reference(item.url)
        except (PublicSourceError, ValueError):
            return None

    @staticmethod
    def _short_publisher(text: str, url: str) -> str | None:
        hostname = (urlparse(url).hostname or "").rstrip(".").lower()
        for marker, (publisher, domains) in KNOWN_SHORT_PUBLISHERS.items():
            if marker not in text:
                continue
            if any(
                hostname == domain or hostname.endswith(f".{domain}")
                for domain in domains
            ):
                return publisher
        return None

    def discover(
        self,
        items: list[NewsItem],
        *,
        as_of: datetime,
        discover_short_reports: bool,
        discover_regulatory_events: bool,
        max_short_reports: int = 25,
        max_regulatory_events: int = 100,
    ) -> DiscoveredAgentSources:
        short_reports: list[ShortReportSourceConfig] = []
        regulatory_events: list[RegulatoryContractSourceConfig] = []
        seen_short: set[tuple[str, str]] = set()
        seen_regulatory: set[tuple[str, str, str]] = set()

        ordered = sorted(items, key=lambda item: (item.published_at, item.ticker, item.url))
        for item in ordered:
            ticker = normalize_ticker(item.ticker)
            if (
                not ticker
                or item.published_at.tzinfo is None
                or item.published_at.utcoffset() is None
                or item.published_at > as_of
            ):
                continue
            url = self._public_url(item)
            if url is None:
                continue
            text = f"{item.title} {item.summary}".lower()

            short_publisher = self._short_publisher(text, url)
            explicit_report = any(marker in text for marker in SHORT_REPORT_MARKERS)
            short_key = (ticker, url)
            if (
                discover_short_reports
                and len(short_reports) < max(0, int(max_short_reports))
                and short_publisher is not None
                and explicit_report
                and short_key not in seen_short
            ):
                seen_short.add(short_key)
                short_reports.append(
                    ShortReportSourceConfig(
                        ticker=ticker,
                        publisher=short_publisher,
                        published_at=item.published_at,
                        url=url,
                        discovery_method="rss",
                    )
                )

            if discover_regulatory_events and len(regulatory_events) < max(
                0, int(max_regulatory_events)
            ):
                for markers, event_type in REGULATORY_RULES:
                    if not any(marker in text for marker in markers):
                        continue
                    event_key = (ticker, event_type, url)
                    if event_key in seen_regulatory:
                        break
                    seen_regulatory.add(event_key)
                    regulatory_events.append(
                        RegulatoryContractSourceConfig(
                            ticker=ticker,
                            event_type=event_type,
                            status="ANNOUNCED",
                            title=item.title[:500] or f"RSS {event_type}",
                            authority_or_counterparty="Neověřeno – viz zdroj",
                            publisher=(urlparse(url).hostname or "RSS discovery"),
                            published_at=item.published_at,
                            url=url,
                            confidence=0.45,
                            discovery_method="rss",
                        )
                    )
                    break

        return DiscoveredAgentSources(
            short_reports=tuple(short_reports),
            regulatory_events=tuple(regulatory_events),
        )
