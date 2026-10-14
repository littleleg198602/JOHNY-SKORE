"""Bounded primary IR HTTP capture, using the shared durable RSS ledger.

This collector authenticates a publisher's captured announcement. It never
authenticates the ticker hint, legal issuer, instrument, or financial claim.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
from html import unescape
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

import feedparser

from market_checker_app.models import NewsItem
from market_checker_app.services.ir_primary_verification_service import (
    CapturedIRDocument, MAX_FEED_ENTRIES, MAX_IR_CAPTURE_BYTES, verify_primary_ir_lead,
)
from market_checker_app.services.news_scout_service import NewsScoutService
from market_checker_app.services.public_source_budget import PublicBudgetExhausted, PublicSourceBudget
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.utils.source_validation import public_https_reference
from market_checker_app.utils.text import normalize_ticker
from market_checker_app.utils.ticker_universe import load_canonical_tickers


COLLECTOR_VERSION = "ir-primary-shared-ledger-v1"
_REDIRECTS = {301, 302, 303, 307, 308}


def _physical_clock():
    return datetime.now(timezone.utc)


def _aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("IR collection requires an explicit UTC-compatible timezone")
    return value.astimezone(timezone.utc)


def _reference(url, hosts):
    if not isinstance(url, str) or len(url) > 2_048:
        raise ValueError("IR collection requires a bounded public source reference")
    value = public_https_reference(url)
    parsed = urlsplit(value)
    # The explicit collector accepts source paths only. Query/fragment secrets
    # must not enter archives, exception text, or a redirect request.
    if (parsed.port not in {None, 443} or parsed.query or parsed.fragment
            or parsed.hostname not in hosts):
        raise ValueError("IR source reference is outside the explicit HTTPS host/path policy")
    return value


def _has_sensitive_body(body):
    """Gate the entire original body; never save a cleaned or encoded substitute."""
    text = unescape(body.decode("utf-8", errors="replace"))
    return any(re.search(pattern, text, re.I) for pattern in (
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        r"\bAKIA[A-Z0-9]{16}\b",
        r"(?:authorization[\"']?\s*[:=]\s*[\"']?\s*bearer|\bbearer)\s+[A-Za-z0-9._~+/=-]{12,}",
        r"(?:api[_-]?key|subscription[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|password)"
        r"[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._~+/=-]{8,}",
        r"[?&](?:sig|signature|x-amz-signature|x-goog-signature|access_token)=[^\s\"'<>]{8,}",
    ))


def _safe_header(value):
    if value is None:
        return None
    value = str(value)
    if len(value) > 1_024 or "\n" in value or "\r" in value or _has_sensitive_body(value.encode()):
        return None
    return value


@dataclass(frozen=True, slots=True)
class IRHTTPResponse:
    status: int
    body: bytes
    observed_at: datetime
    content_type: str = "text/html"
    http_date: str | None = None
    last_modified: str | None = None
    location: str | None = None
    retry_after: str | None = None
    content_encoding: str | None = None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class IRPrimaryHTTPClient:
    """One HTTP hop only; redirects must reserve a fresh shared request."""

    def fetch(self, url, *, timeout, max_bytes):
        started = time.perf_counter()
        request = Request(url, headers={
            "User-Agent": "JOHNY-SKORE primary-source-review/1.0",
            "Accept": "application/rss+xml, application/xml, text/html;q=0.9",
            "Accept-Encoding": "identity",
        })
        try:
            response = build_opener(_NoRedirect()).open(request, timeout=timeout)
        except HTTPError as error:
            # Preserve a bounded original error/redirect response before the
            # shared budget records failure/cooldown. No automatic next hop.
            response = error
        with response:
            chunks, size = [], 0
            while True:
                remaining = timeout - (time.perf_counter() - started)
                if remaining <= 0:
                    raise TimeoutError("IR HTTP complete-body deadline exhausted")
                wire_response = response.fp if isinstance(response, HTTPError) else response
                sock = getattr(getattr(getattr(wire_response, "fp", None), "raw", None), "_sock", None)
                if sock is not None:
                    sock.settimeout(remaining)
                # read1 performs at most one underlying read, allowing the
                # absolute deadline to be checked between streamed chunks.
                reader = getattr(response, "read1", response.read)
                chunk = reader(min(65_536, max_bytes + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("IR HTTP response exceeds the complete-body byte cap")
            raw_length = response.headers.get("Content-Length", "")
            if raw_length.isdigit() and int(raw_length) != size:
                raise ValueError("IR HTTP response was not a complete declared body")
            return IRHTTPResponse(
                status=int(response.code), body=b"".join(chunks), observed_at=_physical_clock(),
                content_type=response.headers.get("Content-Type", ""),
                http_date=response.headers.get("Date"), last_modified=response.headers.get("Last-Modified"),
                location=response.headers.get("Location"), retry_after=response.headers.get("Retry-After"),
                content_encoding=response.headers.get("Content-Encoding"),
            )


class _PhysicalRSSBudget(PublicSourceBudget):
    def now(self):
        # LIVE reservation dates cannot be supplied by a historical UI cutoff.
        return _aware(_physical_clock())


class _CaptureUnavailable(RuntimeError):
    pass


class IRPrimaryScoutService:
    def __init__(self, store: ScoutStore, *, archive_root: Path | None = None,
                 client=None, max_requests: int = 6, max_run_seconds: float = 60,
                 request_timeout: float = 12, max_bytes: int = MAX_IR_CAPTURE_BYTES):
        if (isinstance(max_requests, bool) or not isinstance(max_requests, int)
                or not 1 <= max_requests <= 6 or not 1 <= max_run_seconds <= 90
                or not 0 < request_timeout <= 15 or not 1 <= max_bytes <= MAX_IR_CAPTURE_BYTES):
            raise ValueError("IR collection exceeds the bounded run/request/byte policy")
        self.store = store
        self.archive_root = Path(archive_root or store.db_path.parent / "ir_primary_captures")
        self.client = client if client is not None else IRPrimaryHTTPClient()
        # Injected transports are tests, even when their body looks authentic.
        self.runtime_mode = "LIVE_HTTP" if type(self.client) is IRPrimaryHTTPClient else "TEST_TRANSPORT"
        self.max_requests, self.max_run_seconds = max_requests, max_run_seconds
        self.request_timeout, self.max_bytes = request_timeout, max_bytes

    @staticmethod
    def _write_once(path, body):
        with path.open("xb") as handle:
            handle.write(body)
            handle.flush()
            # An interrupted process leaves completed captures durable.
            import os
            os.fsync(handle.fileno())

    def _capture(self, *, budget, ticker, identity_key, url, role, hosts, archive, receipts):
        first_url, current = url, url
        started = budget.started
        for redirect in range(3):
            current = _reference(current, hosts)
            remaining = self.max_run_seconds - (time.perf_counter() - started)
            if remaining <= 0:
                raise _CaptureUnavailable("RUN_DEADLINE_EXHAUSTED")
            if budget.attempted_requests >= self.max_requests:
                raise _CaptureUnavailable("RUN_REQUEST_LIMIT")
            attempted_at = _aware(_physical_clock())
            sequence = len(receipts) + 1

            def fetch():
                try:
                    response = self.client.fetch(current, timeout=min(self.request_timeout, remaining),
                                                 max_bytes=self.max_bytes)
                    observed = _aware(response.observed_at)
                    if observed < attempted_at or observed > _aware(_physical_clock()):
                        raise ValueError("IR response does not have a physical capture clock")
                    if not isinstance(response.body, bytes) or len(response.body) > self.max_bytes:
                        raise ValueError("IR response exceeds complete-body cap")
                except Exception as error:
                    receipt = {"sequence": sequence, "role": role, "requested_url": current,
                               "attempted_at": attempted_at.isoformat(),
                               "completed_at": _aware(_physical_clock()).isoformat(),
                               "retention": "NO_COMPLETE_BODY", "error_type": type(error).__name__,
                               "runtime_mode": self.runtime_mode}
                    self._save_receipt(archive, receipt, receipts)
                    raise
                digest = hashlib.sha256(response.body).hexdigest()
                sensitive = _has_sensitive_body(response.body)
                encoded = str(response.content_encoding or "identity").lower().strip() not in {"", "identity"}
                retention = "WITHHELD_SENSITIVE_BODY" if sensitive else (
                    "WITHHELD_ENCODED_BODY" if encoded else "ORIGINAL_BYTES_ARCHIVED")
                receipt = {"sequence": sequence, "role": role, "requested_url": current,
                           "attempted_at": attempted_at.isoformat(), "completed_at": observed.isoformat(),
                           "status": response.status, "size_bytes": len(response.body), "sha256": digest,
                           "content_type": _safe_header(response.content_type),
                           "http_date": _safe_header(response.http_date),
                           "last_modified": _safe_header(response.last_modified),
                           "content_encoding": _safe_header(response.content_encoding),
                           "retention": retention, "runtime_mode": self.runtime_mode}
                if not sensitive and not encoded:
                    filename = f"{sequence:02d}_{role}_{digest}.raw"
                    self._write_once(archive / filename, response.body)
                    receipt["raw_path"] = filename
                self._save_receipt(archive, receipt, receipts)
                if response.status >= 400:
                    headers = {"Retry-After": _safe_header(response.retry_after) or ""}
                    raise HTTPError(current, response.status, "IR publisher HTTP failure", headers, None)
                if sensitive or encoded:
                    raise _CaptureUnavailable(retention)
                return response

            response = budget.call(ticker, identity_key, fetch)
            if response.status in _REDIRECTS:
                if redirect == 2 or not response.location:
                    raise _CaptureUnavailable("REDIRECT_CHAIN_BOUND")
                # Never publish an unsafe Location value; validation occurs
                # before another quota reservation or HTTP request.
                current = _reference(urljoin(current, response.location), hosts)
                continue
            if response.status != 200:
                raise _CaptureUnavailable("HTTP_NOT_200")
            return CapturedIRDocument(
                requested_url=first_url, resolved_url=current, body=response.body,
                observed_at=response.observed_at, http_status=response.status,
                content_type=_safe_header(response.content_type) or "",
                http_date=_safe_header(response.http_date), last_modified=_safe_header(response.last_modified),
                capture_origin="PINNED_CAPTURE_REPLAY" if self.runtime_mode == "LIVE_HTTP" else "SYNTHETIC_TEST_FIXTURE",
            )
        raise _CaptureUnavailable("REDIRECT_CHAIN_BOUND")

    def _save_receipt(self, archive, receipt, receipts):
        self._write_once(archive / f"{receipt['sequence']:02d}_receipt.json",
                         (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode())
        receipts.append(receipt)

    def run(self, ticker: str, *, feed_url: str, authority_url: str,
            publisher_hosts: tuple[str, ...], candidate_id: str | None = None,
            original_url: str | None = None, requested_as_of: datetime | None = None):
        """Fetch one selected announcement; the requested cutoff is context only.

        A caller must select an existing RSS lead or an exact URL in the newly
        captured bounded feed. A ticker never grants publisher/issuer identity.
        """
        ticker = normalize_ticker(ticker)
        if ticker not in set(load_canonical_tickers()):
            raise ValueError("IR ticker hint is outside the canonical 687 universe")
        if (not isinstance(publisher_hosts, tuple) or not 1 <= len(set(publisher_hosts)) <= 8
                or any(not isinstance(host, str) or not re.fullmatch(r"[a-z0-9.-]+", host)
                       for host in publisher_hosts)):
            raise ValueError("IR collection needs an explicit bounded publisher host policy")
        if candidate_id is not None and (not isinstance(candidate_id, str) or len(candidate_id) > 200):
            raise ValueError("IR collection requires a bounded selected candidate reference")
        hosts = set(publisher_hosts)
        feed_url, authority_url = _reference(feed_url, hosts), _reference(authority_url, hosts)
        context = _aware(requested_as_of).isoformat() if requested_as_of is not None else None
        candidate = None
        if candidate_id is not None:
            candidate = self.store.finding_by_id(candidate_id, subject_id=ticker)
            if (candidate is None or candidate["source"] != "rss"
                    or json.loads(candidate["details_json"]).get("stage") != "search_candidate"):
                raise ValueError("IR collection requires that ticker's existing RSS search candidate")
            original = _reference(candidate["source_url"], hosts)
            if original_url is not None and _reference(original_url, hosts) != original:
                raise ValueError("IR selected candidate and exact original URL disagree")
        elif original_url is not None:
            original = _reference(original_url, hosts)
        else:
            raise ValueError("IR collection requires a selected RSS lead or exact original URL")
        now = _aware(_physical_clock())
        budget = _PhysicalRSSBudget(self.store, "rss", as_of=now, max_run_seconds=self.max_run_seconds)
        report = {"collector_version": COLLECTOR_VERSION, "runtime_mode": self.runtime_mode,
                  "status": "PARTIAL", "ticker_hint": ticker, "requested_as_of": context,
                  "started_at": now.isoformat(), "capture_receipts": [], "new_findings": 0,
                  "issuer_identity_verified": False, "issuer_attribution_allowed": False,
                  "scoring_applied": False, "canonical_universe_count": 687,
                  "scope": {"selected_original_url": original, "feed_entry_cap": MAX_FEED_ENTRIES,
                            "max_primary_documents": 1, "all_publisher_history_complete": False,
                            "complete_negative_issuer_search": False},
                  "prospective_ledger_registration": True,
                  "historical_capture_quota_reconstructed": False}
        if not budget.acquire():
            return {**report, **budget.blocked_summary()}
        archive = None
        try:
            if not budget.select_due([{"subject_id": ticker}]):
                raise PublicBudgetExhausted("WAIT_QUOTA")
            identity_key = hashlib.sha256(json.dumps([feed_url, authority_url, original, sorted(hosts)]).encode()).hexdigest()
            archive = self.archive_root / (now.strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid4().hex)
            archive.mkdir(parents=True, exist_ok=False)
            report["archive_directory"] = archive.as_posix()
            receipts = report["capture_receipts"]
            feed = self._capture(budget=budget, ticker=ticker, identity_key=identity_key, url=feed_url,
                                 role="feed", hosts=hosts, archive=archive, receipts=receipts)
            if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", feed.body, re.I):
                raise _CaptureUnavailable("UNSAFE_FEED_ENTITY_DECLARATION")
            parsed = feedparser.parse(feed.body)
            entries = list(parsed.entries)[:MAX_FEED_ENTRIES]
            matching = [entry for entry in entries if str(entry.get("link", "")) == original]
            report["scope"]["feed_entries_examined"] = len(entries)
            report["scope"]["feed_parse_partial"] = bool(parsed.bozo)
            if len(matching) != 1:
                raise _CaptureUnavailable("SELECTED_URL_NOT_UNIQUE_IN_BOUNDED_FEED")
            entry = matching[0]
            published = _publication_date(entry.get("published", ""))
            title = str(entry.get("title", ""))
            if published is None or not title.strip() or len(title) > 350:
                raise _CaptureUnavailable("RSS_TITLE_OR_EXPLICIT_PUBLICATION_UNRESOLVED")
            if published > feed.observed_at:
                raise _CaptureUnavailable("RSS_PUBLICATION_CAPTURE_CLOCK_CONFLICT")
            authority_block = None
            try:
                authority = self._capture(budget=budget, ticker=ticker, identity_key=identity_key, url=authority_url,
                                          role="authority", hosts=hosts, archive=archive, receipts=receipts)
            except _CaptureUnavailable as error:
                if str(error) not in {"WITHHELD_SENSITIVE_BODY", "WITHHELD_ENCODED_BODY"}:
                    raise
                # A withheld portal cannot authenticate the announcement, but
                # its rejection need not discard a separately safe primary
                # page. Preserve the other bounded capture without parsing or
                # reconstructing the rejected portal body.
                authority, authority_block = None, str(error)
            primary = self._capture(budget=budget, ticker=ticker, identity_key=identity_key, url=original,
                                    role="primary", hosts=hosts, archive=archive, receipts=receipts)
            if authority is None:
                report["authority_review_blocked"] = True
                report["publication_clocks"] = {"rss_raw": str(entry.get("published", "")),
                                                "exact_first_dissemination_verified": False}
                control_lead = NewsItem(ticker, feed_url, title, "", published, 0.0, original,
                                        feed_url=feed_url, original_url=original,
                                        observed_at=_aware(_physical_clock()))
                report["partial_control"] = {
                    "kind": "FETCHED_RSS_FEED_IS_NOT_REQUIRED_IR_PORTAL",
                    "additional_http_requests": 0,
                    "review": verify_primary_ir_lead(control_lead, feed_capture=feed, primary_capture=primary,
                        authority_capture=feed, publisher_hosts=publisher_hosts,
                        knowledge_at=_aware(_physical_clock())),
                }
                raise _CaptureUnavailable(authority_block)
            completion = _aware(_physical_clock())
            if (time.perf_counter() - budget.started > self.max_run_seconds
                    or self.store.provider_cooldown("rss", as_of=completion)
                    or not self.store.renew_provider("rss", budget.token, as_of=completion,
                                                     seconds=budget.lease_seconds)):
                budget.stop_status = "LEASE_LOST"
                raise PublicBudgetExhausted("LEASE_LOST")
            news = NewsScoutService(self.store)
            if candidate is None:
                lead = NewsItem(ticker, feed_url, title, "", published, 0.0, original,
                                feed_url=feed_url, original_url=original, observed_at=completion)
                news.ingest([lead], allowed_tickers=[ticker], max_per_ticker=1)
                rows = self.store.findings_as_of(ticker, as_of=completion)
                candidate = next((row for row in rows if row["source"] == "rss"
                                  and row["source_url"] == original
                                  and json.loads(row["details_json"]).get("stage") == "search_candidate"), None)
                if candidate is None:
                    raise _CaptureUnavailable("RSS_CANDIDATE_NOT_IMPORTED")
                candidate_id = candidate["finding_id"]
            result = news.verify_primary_candidate(
                candidate_id, ticker=ticker, feed_capture=feed, primary_capture=primary,
                authority_capture=authority, publisher_hosts=publisher_hosts, as_of=_aware(_physical_clock()))
            report["candidate_id"], report["review_id"] = candidate_id, result["finding_id"]
            report["primary_source_review"] = result["primary_source_review"]
            report["persisted_available_at"] = result["persisted_available_at"]
            report["new_findings"] = int(result["created"])
            report["database_verification_status"] = result["verification_status"]
            report["status"] = "SOURCE_VERIFIED" if result["primary_source_review"]["source_verified"] else "PARTIAL"
            # Reuse actual fetched bytes as a page-binding control. It proves a
            # scoped rejection, never a full negative issuer/news search.
            control_lead = NewsItem(ticker, feed_url, str(candidate["title"]), "",
                                    datetime.fromisoformat(candidate["published_at"]), 0.0, original,
                                    feed_url=feed_url, original_url=original,
                                    observed_at=datetime.fromisoformat(candidate["first_observed_at"]))
            report["partial_control"] = {"kind": "FETCHED_IR_PORTAL_IS_NOT_SELECTED_ANNOUNCEMENT",
                "additional_http_requests": 0,
                "review": verify_primary_ir_lead(control_lead, feed_capture=feed, primary_capture=authority,
                    authority_capture=authority, publisher_hosts=publisher_hosts, knowledge_at=_aware(_physical_clock()))}
        except PublicBudgetExhausted:
            report["stop_reason"] = budget.stop_status or "WAIT_QUOTA"
        except HTTPError as error:
            report["status"] = "RATE_LIMITED" if error.code == 429 else (
                "ACCESS_BLOCKED" if error.code in {401, 403} else "PARTIAL")
            report["stop_reason"] = f"HTTP_{error.code}"
        except _CaptureUnavailable as error:
            report["stop_reason"] = str(error)
        except Exception as error:
            # Exception messages can contain response bodies/redirect secrets.
            report["stop_reason"] = "CAPTURE_OR_IMPORT_ERROR"
            report["error_type"] = type(error).__name__
        finally:
            report["completed_at"] = _aware(_physical_clock()).isoformat()
            report = budget.annotate(report)
            if budget.stop_status:
                report["status"] = budget.stop_status
            budget.release()
            if archive is not None:
                self._write_once(archive / "run.json", (json.dumps(report, indent=2, sort_keys=True) + "\n").encode())
        return report


def _publication_date(raw):
    try:
        value = parsedate_to_datetime(str(raw))
        return _aware(value)
    except (ValueError, TypeError, OverflowError):
        return None
