from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
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
    "is short",
    "we are short",
    "short thesis",
    "initiating coverage",
    "research report",
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
    (("beats earnings", "beats eps", "earnings beat", "profit beats estimates"), "EARNINGS_BEAT"),
    (("misses earnings", "misses eps", "earnings miss", "profit misses estimates"), "EARNINGS_MISS"),
    (("raises guidance", "raised guidance", "raises outlook", "boosts forecast"), "GUIDANCE_RAISE"),
    (("cuts guidance", "cut guidance", "lowers outlook", "slashes forecast"), "GUIDANCE_CUT"),
    (("share buyback", "stock buyback", "repurchase program", "share repurchase"), "BUYBACK"),
    (("raises dividend", "dividend increase", "increases dividend"), "DIVIDEND_INCREASE"),
    (("cuts dividend", "dividend cut", "suspends dividend"), "DIVIDEND_CUT"),
    (("to acquire", "acquisition of", "merger agreement", "agrees to buy"), "MERGER_ACQUISITION"),
    (("public offering", "share offering", "equity offering", "capital increase"), "CAPITAL_RAISE"),
    (("debt refinancing", "refinances debt", "senior notes offering", "debt offering"), "DEBT_REFINANCING"),
    (("ceo resigns", "cfo resigns", "appoints new ceo", "appoints new cfo"), "EXECUTIVE_CHANGE"),
)


@dataclass(frozen=True, slots=True)
class DiscoveredAgentSources:
    short_reports: tuple[ShortReportSourceConfig, ...] = ()
    regulatory_events: tuple[RegulatoryContractSourceConfig, ...] = ()


class SourceDiscoveryService:
    """Conservative discovery over already collected, dated RSS news items."""

    @staticmethod
    def _public_url(item: NewsItem) -> str | None:
        for candidate in (item.original_url, item.url):
            if not str(candidate or "").strip():
                continue
            try:
                return public_https_reference(candidate)
            except (PublicSourceError, ValueError):
                continue
        return None

    @staticmethod
    def _short_publisher(text: str, url: str) -> str | None:
        hostname = (urlparse(url).hostname or "").rstrip(".").lower()
        for _marker, (publisher, domains) in KNOWN_SHORT_PUBLISHERS.items():
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

        # Round-robin by ticker prevents a universe-wide limit from silently
        # favouring the first ticker in alphabetical/date order.
        by_ticker: dict[str, list[NewsItem]] = {}
        for item in items:
            ticker = normalize_ticker(item.ticker)
            if ticker:
                by_ticker.setdefault(ticker, []).append(item)
        for ticker_items in by_ticker.values():
            ticker_items.sort(key=lambda item: (item.published_at, item.url), reverse=True)
        ordered: list[NewsItem] = []
        depth = 0
        while True:
            appended = False
            for ticker in sorted(by_ticker):
                ticker_items = by_ticker[ticker]
                if depth < len(ticker_items):
                    ordered.append(ticker_items[depth])
                    appended = True
            if not appended:
                break
            depth += 1
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
            explicit_report = any(
                marker in text for marker in SHORT_REPORT_MARKERS
            ) or re.search(r"\bshort\b", text) is not None
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
                            authority_or_counterparty=(
                                str(item.publisher or "").strip()
                                or str(item.publisher_domain or "").strip()
                                or "Neověřeno – viz zdroj"
                            ),
                            publisher=(
                                str(item.publisher or "").strip()
                                or str(item.publisher_domain or "").strip()
                                or (urlparse(url).hostname or "RSS discovery")
                            ),
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
