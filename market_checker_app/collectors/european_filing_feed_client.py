from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
from typing import Callable
from xml.etree import ElementTree

from market_checker_app.collectors.european_filing_client import (
    EuropeanFilingClient,
    EuropeanFilingError,
)
from market_checker_app.collectors.short_report_client import _default_transport
from market_checker_app.config import (
    EuropeanFilingConfig,
    EuropeanFilingFeedConfig,
    EuropeanFilingSourceConfig,
)


EuropeanFeedTransport = Callable[
    [str, dict[str, str], float, int],
    tuple[bytes, str, str],
]


def _local_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1].lower()


def _entry_value(entry: ElementTree.Element, *names: str) -> str:
    allowed = {name.lower() for name in names}
    values: list[str] = []
    for child in entry.iter():
        if _local_name(child.tag) not in allowed:
            continue
        text = " ".join(str(child.text or "").split())
        if text:
            values.append(text)
    return " ".join(values).strip()


def _entry_link(entry: ElementTree.Element) -> str:
    for child in entry.iter():
        if _local_name(child.tag) != "link":
            continue
        href = str(child.attrib.get("href") or "").strip()
        relation = str(child.attrib.get("rel") or "alternate").strip().lower()
        if href and relation in {"", "alternate"}:
            return href
        text = str(child.text or "").strip()
        if text:
            return text
    return ""


def _published_at(entry: ElementTree.Element) -> datetime | None:
    raw = _entry_value(entry, "published", "updated", "pubdate", "date")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _identity_tokens(feed: EuropeanFilingFeedConfig) -> tuple[str, ...]:
    return tuple(
        re.sub(r"[^A-Z0-9]", "", value.upper())
        for value in (feed.lei, feed.isin)
        if value
    )


class EuropeanFilingFeedClient:
    """Discover exact-identity European filings from approved RSS/Atom feeds."""

    def __init__(
        self,
        config: EuropeanFilingConfig,
        *,
        transport: EuropeanFeedTransport | None = None,
    ) -> None:
        self.config = config
        self._transport = transport or _default_transport
        self._document_client = EuropeanFilingClient(config)

    @staticmethod
    def _validation_source(
        feed: EuropeanFilingFeedConfig,
        url: str,
        *,
        title: str,
        published_at: datetime,
    ) -> EuropeanFilingSourceConfig:
        return EuropeanFilingSourceConfig(
            ticker=feed.ticker,
            authority=feed.authority,
            document_type=feed.document_type,
            title=title,
            published_at=published_at,
            url=url,
            lei=feed.lei,
            isin=feed.isin,
            issuer_name=feed.issuer_name,
            audited=feed.audited,
            esef=feed.esef,
            language=feed.language,
            discovery_method="official_feed",
        )

    def discover(
        self,
        feed: EuropeanFilingFeedConfig,
        *,
        as_of: datetime,
    ) -> tuple[EuropeanFilingSourceConfig, ...]:
        as_of = (
            as_of.replace(tzinfo=timezone.utc)
            if as_of.tzinfo is None or as_of.utcoffset() is None
            else as_of.astimezone(timezone.utc)
        )
        feed_validation = self._validation_source(
            feed,
            feed.feed_url,
            title="European filing discovery feed",
            published_at=as_of,
        )
        feed_url = self._document_client.validate_source(feed_validation)
        payload, raw_mime_type, final_url = self._transport(
            feed_url,
            {
                "User-Agent": self.config.user_agent,
                "Accept": (
                    "application/atom+xml,application/rss+xml,application/xml,"
                    "text/xml;q=0.9"
                ),
            },
            self.config.request_timeout_seconds,
            self.config.max_feed_download_bytes,
        )
        if len(payload) > self.config.max_feed_download_bytes:
            raise EuropeanFilingError("Evropský feed překročil bezpečný limit.")
        self._document_client.validate_source(
            self._validation_source(
                feed,
                final_url,
                title="European filing discovery feed",
                published_at=as_of,
            )
        )
        mime_type = str(raw_mime_type or "").split(";", 1)[0].strip().lower()
        if mime_type and mime_type not in {
            "application/atom+xml",
            "application/rss+xml",
            "application/xml",
            "text/xml",
        }:
            raise EuropeanFilingError(
                f"Evropský feed má nepodporovaný Content-Type {mime_type}."
            )
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError as exc:
            raise EuropeanFilingError(f"Evropský feed není platné XML: {exc}") from exc

        tokens = _identity_tokens(feed)
        if not tokens:
            raise EuropeanFilingError(
                "Evropský feed nemá přesný LEI/ISIN pro kontrolu identity."
            )
        entries = [
            node
            for node in root.iter()
            if _local_name(node.tag) in {"item", "entry"}
        ]
        discovered: list[EuropeanFilingSourceConfig] = []
        seen: set[tuple[str, str]] = set()
        for entry in entries:
            title = _entry_value(entry, "title")[:500]
            summary = _entry_value(entry, "description", "summary", "content")
            link = _entry_link(entry)
            published_at = _published_at(entry)
            searchable = re.sub(
                r"[^A-Z0-9]",
                "",
                f"{title} {summary} {link}".upper(),
            )
            if (
                not title
                or not link
                or published_at is None
                or published_at > as_of
                or not any(token in searchable for token in tokens)
            ):
                continue
            candidate = self._validation_source(
                feed,
                link,
                title=title,
                published_at=published_at,
            )
            validated_link = self._document_client.validate_source(candidate)
            candidate = EuropeanFilingSourceConfig(
                ticker=candidate.ticker,
                authority=candidate.authority,
                document_type=candidate.document_type,
                title=candidate.title,
                published_at=candidate.published_at,
                url=validated_link,
                lei=candidate.lei,
                isin=candidate.isin,
                issuer_name=candidate.issuer_name,
                audited=candidate.audited,
                esef=candidate.esef,
                language=candidate.language,
                discovery_method=candidate.discovery_method,
            )
            key = (validated_link, published_at.isoformat())
            if key in seen:
                continue
            seen.add(key)
            discovered.append(candidate)
            if len(discovered) >= feed.max_entries:
                break
        return tuple(discovered)
