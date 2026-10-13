"""Verify a publisher's exact RSS/page claim from bounded captured bytes.

No networking, requests, quota resets, issuer approval or scoring happens here.
An authenticated publisher source is distinct from dated issuer attribution and
from the factual truth or financial consequences of what the publisher says.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

import feedparser

from market_checker_app.models import NewsItem
from market_checker_app.utils.source_validation import public_https_reference


IR_PRIMARY_REVIEW_VERSION = "ir-primary-captured-source-v1"
MAX_IR_CAPTURE_BYTES = 2_000_000
MAX_FEED_ENTRIES = 30


class IRPrimaryVerificationError(ValueError):
    pass


def _aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise IRPrimaryVerificationError("IR source review requires an explicit knowledge timezone")
    return value.astimezone(timezone.utc)


def _url(value):
    if not isinstance(value, str) or not value or len(value) > 2_048:
        raise IRPrimaryVerificationError("IR source URL exceeds bounded reference policy")
    url = public_https_reference(value)
    parsed = urlsplit(url)
    if parsed.port not in {None, 443}:
        raise IRPrimaryVerificationError("IR source review requires default HTTPS ports")
    return url


def _url_key(value):
    parsed = urlsplit(_url(value))
    # Retain query semantics; fragment variation never proves another source.
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path, parsed.query, ""))


def _text_key(value):
    return " ".join(str(value).split()).casefold()


def _date(value):
    raw = str(value or "").strip()
    try:
        result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            result = parsedate_to_datetime(raw)
        except (ValueError, TypeError):
            return None
    if result.tzinfo is None or result.utcoffset() is None:
        return None
    return result.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class CapturedIRDocument:
    requested_url: str
    resolved_url: str
    body: bytes
    observed_at: datetime
    http_status: int = 200
    content_type: str = "text/html"
    http_date: str | None = None
    last_modified: str | None = None
    capture_origin: str = "PINNED_CAPTURE_REPLAY"

    def summary(self):
        _url(self.requested_url); _url(self.resolved_url)
        _aware(self.observed_at)
        if not isinstance(self.body, bytes) or len(self.body) > MAX_IR_CAPTURE_BYTES:
            raise IRPrimaryVerificationError("IR capture exceeds bounded raw byte policy")
        if self.capture_origin not in {"PINNED_CAPTURE_REPLAY", "SYNTHETIC_TEST_FIXTURE"}:
            raise IRPrimaryVerificationError("IR offline verifier cannot manufacture a LIVE capture claim")
        if any(value is not None and (not isinstance(value, str) or len(value) > 1_024)
               for value in (self.content_type, self.http_date, self.last_modified)):
            raise IRPrimaryVerificationError("IR capture headers exceed bounded reference policy")
        return {
            "requested_url": self.requested_url, "resolved_url": self.resolved_url,
            "observed_at": self.observed_at.isoformat(), "http_status": self.http_status,
            "content_type": self.content_type, "http_date": self.http_date, "last_modified": self.last_modified,
            "sha256": hashlib.sha256(self.body).hexdigest(), "size_bytes": len(self.body),
            "capture_origin": self.capture_origin,
        }


class _Page(HTMLParser):
    _VOID = {"meta", "link", "br", "img", "input", "hr", "source", "wbr", "area", "base", "embed", "param", "track", "col"}

    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.h1_parts = []
        self.paragraph_parts = []
        self.headlines = []
        self.paragraphs = []
        self.links = []
        self.canonicals = []
        self.publication_clocks = []
        self.visible = []
        self.nodes_seen = 0
        self.feed(body.decode("utf-8", errors="replace"))

    def _inside(self, tag):
        return tag in self.stack

    def handle_starttag(self, tag, attrs):
        self.nodes_seen += 1
        if self.nodes_seen > 40_000 or len(self.stack) > 128:
            raise IRPrimaryVerificationError("IR HTML capture exceeds bounded parser complexity")
        attributes = dict(attrs)
        if tag == "meta" and attributes.get("property") == "article:published_time":
            self.publication_clocks.append(str(attributes.get("content", "")))
        if tag == "link" and "canonical" in str(attributes.get("rel", "")).split():
            self.canonicals.append(str(attributes.get("href", "")))
        if tag == "a" and attributes.get("href"):
            self.links.append(str(attributes["href"]))
        if tag not in self._VOID:
            self.stack.append(tag)
        if self._inside("article") and tag == "h1":
            self.h1_parts = []
        if self._inside("article") and tag == "p":
            self.paragraph_parts = []
        self.visible.append(" ")

    def handle_endtag(self, tag):
        if tag == "h1" and self._inside("article"):
            self.headlines.append(" ".join("".join(self.h1_parts).split())[:2_000])
        if tag == "p" and self._inside("article") and len(self.paragraphs) < 128:
            text = " ".join("".join(self.paragraph_parts).split())[:4_000]
            if text:
                self.paragraphs.append(text)
        if tag in self.stack:
            self.stack = self.stack[:len(self.stack) - self.stack[::-1].index(tag) - 1]
        self.visible.append(" ")

    def handle_data(self, data):
        if self._inside("script") or self._inside("style") or self._inside("noscript"):
            return
        self.visible.append(data)
        if self._inside("article") and self._inside("h1"):
            self.h1_parts.append(data)
        if self._inside("article") and self._inside("p"):
            self.paragraph_parts.append(data)


def verify_primary_ir_lead(
    lead: NewsItem, *, feed_capture: CapturedIRDocument, primary_capture: CapturedIRDocument,
    authority_capture: CapturedIRDocument, publisher_hosts: tuple[str, ...], knowledge_at: datetime,
    claim_quote: str | None = None,
) -> dict[str, object]:
    """Review one exact lead/page pair; issuer and claim truth remain unverified.

`publisher_hosts` is an explicit source policy, not a discovered legal identity.
All supplied data is bounded already captured evidence. LIVE request budgets,
provider leases and archives belong to a separate quota-managed caller.
"""
    clock = _aware(knowledge_at)
    if (not isinstance(lead, NewsItem) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,19}", lead.ticker)
            or not isinstance(lead.title, str) or not lead.title.strip() or len(lead.title) > 2_000):
        raise IRPrimaryVerificationError("IR review requires a bounded explicit RSS ticker hint/title")
    hosts = set(publisher_hosts)
    if (not isinstance(publisher_hosts, tuple) or not 1 <= len(hosts) <= 8
            or any(not re.fullmatch(r"[a-z0-9.-]+", host) for host in hosts)):
        raise IRPrimaryVerificationError("IR source review needs a bounded explicit publisher host policy")
    captures = {"feed": feed_capture, "primary": primary_capture, "authority": authority_capture}
    provenance = {kind: capture.summary() for kind, capture in captures.items()}
    if any(_aware(capture.observed_at) > clock for capture in captures.values()):
        raise IRPrimaryVerificationError("IR source review cannot expose future capture bytes")
    lead_observed, lead_published = _aware(lead.observed_at), _aware(lead.published_at)
    if lead_observed > clock:
        raise IRPrimaryVerificationError("IR source review cannot expose a future RSS lead")
    original = _url(lead.original_url or lead.url)
    feed_url = _url(lead.feed_url or lead.source)
    reasons = []
    if lead_published > lead_observed:
        reasons.append("RSS_LEAD_PUBLICATION_CLOCK_CONFLICT")
    for kind, capture in captures.items():
        if capture.http_status != 200:
            reasons.append(f"{kind.upper()}_HTTP_NOT_SUCCESSFUL")
        if urlsplit(capture.resolved_url).hostname not in hosts:
            reasons.append(f"{kind.upper()}_HOST_OUTSIDE_PUBLISHER_POLICY")
        if kind in {"feed", "authority"} and urlsplit(capture.requested_url).hostname not in hosts:
            reasons.append(f"{kind.upper()}_REQUEST_HOST_OUTSIDE_PUBLISHER_POLICY")
        mime = capture.content_type.split(";", 1)[0].lower().strip()
        if kind in {"primary", "authority"} and mime not in {"text/html", "application/xhtml+xml"}:
            reasons.append(f"{kind.upper()}_CONTENT_TYPE_NOT_HTML")
    if _url_key(feed_url) not in {_url_key(feed_capture.requested_url), _url_key(feed_capture.resolved_url)}:
        reasons.append("RSS_FEED_CAPTURE_URL_MISMATCH")
    if _url_key(original) != _url_key(primary_capture.resolved_url):
        reasons.append("RSS_ORIGINAL_PRIMARY_URL_MISMATCH")
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", feed_capture.body, re.I):
        raise IRPrimaryVerificationError("IR RSS capture contains unsafe DTD/entity declarations")
    parsed_feed = feedparser.parse(feed_capture.body)
    if parsed_feed.bozo:
        reasons.append("RSS_PARSE_PARTIAL")
    entries = list(parsed_feed.entries)[:MAX_FEED_ENTRIES]
    matched = []
    for entry in entries:
        try:
            link = _url_key(str(entry.get("link", "")))
        except ValueError:
            continue
        if link == _url_key(original):
            matched.append(entry)
    raw_feed_dates = [str(entry.get("published", "")) for entry in matched]
    if not matched:
        reasons.append("RSS_LEAD_NOT_IN_BOUNDED_CAPTURE")
    elif any(_text_key(entry.get("title", "")) != _text_key(lead.title) for entry in matched):
        reasons.append("RSS_EXACT_TITLE_MISMATCH")
    feed_dates = [_date(raw) for raw in raw_feed_dates]
    if not feed_dates or any(value is None for value in feed_dates):
        reasons.append("RSS_PUBLICATION_TIME_UNRESOLVED")
    elif any(value != lead_published for value in feed_dates):
        reasons.append("RSS_LEAD_PUBLICATION_TIME_CONFLICT")
    page, authority = _Page(primary_capture.body), _Page(authority_capture.body)
    if not page.headlines or any(_text_key(title) != _text_key(lead.title) for title in page.headlines):
        reasons.append("PRIMARY_ARTICLE_HEADLINE_MISMATCH_OR_MISSING")
    canonicals = []
    for value in page.canonicals:
        try:
            canonicals.append(_url_key(urljoin(primary_capture.resolved_url, value)))
        except ValueError:
            reasons.append("PRIMARY_CANONICAL_URL_INVALID")
    if not canonicals or any(value != _url_key(original) for value in canonicals):
        reasons.append("PRIMARY_CANONICAL_URL_MISMATCH_OR_MISSING")
    primary_dates = [_date(raw) for raw in page.publication_clocks]
    if not primary_dates or any(value is None for value in primary_dates):
        reasons.append("PRIMARY_PUBLICATION_TIME_UNRESOLVED")
    elif any(value != lead_published for value in primary_dates):
        reasons.append("PRIMARY_RSS_PUBLICATION_TIME_CONFLICT")
    if any(value is not None and value > _aware(primary_capture.observed_at) for value in primary_dates):
        reasons.append("PRIMARY_PUBLICATION_CAPTURE_CLOCK_CONFLICT")
    if not page.paragraphs:
        reasons.append("PRIMARY_ARTICLE_BODY_UNAVAILABLE")
    quote = claim_quote if claim_quote is not None else (page.paragraphs[0] if page.paragraphs else "")
    if not isinstance(quote, str) or len(quote) > 4_000:
        raise IRPrimaryVerificationError("IR source claim requires a bounded quote")
    paragraph = next((index for index, text in enumerate(page.paragraphs, 1)
                      if quote.strip() and _text_key(quote) in _text_key(text)), None)
    if paragraph is None:
        reasons.append("CLAIM_QUOTE_ABSENT_FROM_PRIMARY_ARTICLE")
    authority_links = {urljoin(authority_capture.resolved_url, value) for value in authority.links}
    requested_cited = primary_capture.requested_url in authority_links
    linked_from_authority = requested_cited or primary_capture.resolved_url in authority_links
    linked_to_ir = []
    for raw_link in page.links:
        try:
            link = _url(urljoin(primary_capture.resolved_url, raw_link))
        except ValueError:
            continue
        if (urlsplit(link).hostname == urlsplit(authority_capture.resolved_url).hostname
                and "/investor" in urlsplit(link).path.lower()):
            linked_to_ir.append(link)
    authority_is_ir = "/investor" in urlsplit(authority_capture.resolved_url).path.lower()
    if not authority_is_ir or not (linked_from_authority or linked_to_ir):
        reasons.append("IR_PORTAL_LINK_SCOPE_UNRESOLVED")
    if (_url_key(primary_capture.requested_url) != _url_key(original)
            and not requested_cited):
        reasons.append("PRIMARY_REQUEST_ALIAS_NOT_CITED_BY_IR_PORTAL")
    ticker_pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(lead.ticker)}(?![A-Za-z0-9])")
    issuer_mentions = [{"locator": f"article/paragraph:{index}", "quote": text}
                       for index, text in enumerate(page.paragraphs, 1) if ticker_pattern.search(text)][:3]
    return {
        "review_version": IR_PRIMARY_REVIEW_VERSION, "verification_mode": "READ_ONLY_CAPTURE_REVIEW",
        "reviewed_at": clock.isoformat(), "requested_ticker": lead.ticker,
        "source_verification_status": "PARTIAL" if reasons else "SOURCE_VERIFIED",
        "source_verified": not reasons, "reasons": list(dict.fromkeys(reasons)),
        "lead": {"original_url": original, "feed_url": feed_url, "title": lead.title,
                 "published_at": lead_published.isoformat(), "observed_at": lead_observed.isoformat()},
        "captures": provenance, "publisher_hosts_policy": sorted(hosts),
        "publication_clocks": {"rss_raw": raw_feed_dates, "primary_raw": page.publication_clocks,
                               "http_dates_are_not_publication_clocks": True,
                               "exact_first_dissemination_verified": False},
        "source_available_at": max(lead_observed, *(capture.observed_at for capture in captures.values())).isoformat(),
        "quoted_claim": {"quote": quote, "locator": f"article/paragraph:{paragraph}" if paragraph else None,
                         "source_url": primary_capture.resolved_url,
                         "source_sha256": provenance["primary"]["sha256"],
                         "quote_matches_primary_article": paragraph is not None,
                         "claim_truth_independently_verified": False},
        "publisher_ir_link": {"authority_url": authority_capture.resolved_url,
                              "authority_cites_requested_or_resolved_page": linked_from_authority,
                              "authority_cites_exact_requested_alias": requested_cited,
                              "primary_cites_ir_portal": linked_to_ir},
        "issuer_as_named_mentions": issuer_mentions,
        "issuer_identity_status": "PUBLISHER_MENTION_ONLY" if issuer_mentions else "TICKER_HINT_ONLY",
        "issuer_identity_verified": False, "issuer_attribution_allowed": False,
        "claim_truth_independently_verified": False, "scoring_applied": False,
        "feed_scope": {"entries_examined": len(entries), "max_entries": MAX_FEED_ENTRIES,
                       "all_publisher_history_complete": False},
        "network_requests_performed": 0, "quota_or_lease_modified": False,
    }


__all__ = ["CapturedIRDocument", "IRPrimaryVerificationError", "IR_PRIMARY_REVIEW_VERSION", "verify_primary_ir_lead"]
