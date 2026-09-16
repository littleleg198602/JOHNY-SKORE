from __future__ import annotations

"""Normalized, evidence-preserving source-degradation reporting."""

from collections import Counter
from collections.abc import Iterable
from datetime import datetime
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import pandas as pd


_URL_RE = re.compile(r"https?://[^\s)]+", re.IGNORECASE)


def classify_failure_reason(detail: object, *, fallback: str = "UNKNOWN_FAILURE") -> str:
    """Map unstable provider messages to a small, queryable reason contract."""
    text = str(detail or "").strip().lower()
    if not text:
        return fallback
    if "429" in text or "rate limit" in text or "too many request" in text:
        return "RATE_LIMITED"
    if "403" in text or "401" in text or "forbidden" in text or "unauthorized" in text:
        return "ACCESS_DENIED"
    if "timeout" in text or "timed out" in text or "time out" in text:
        return "TIMEOUT"
    if any(token in text for token in ("parse", "json", "xml", "malformed", "decode")):
        return "PARSE_ERROR"
    if any(token in text for token in ("identity", "cik", "isin", "lei", "entity conflict")):
        return "IDENTITY_UNRESOLVED"
    if any(token in text for token in ("retry", "backoff", "checkpoint", "deferred")):
        return "RETRY_DEFERRED"
    if any(token in text for token in ("config", "not configured", "disabled")):
        return "CONFIG_MISSING"
    if "undated" in text:
        return "UNDATED_DATA"
    if any(token in text for token in ("incomplete", "insufficient", "partial")):
        return "DATA_INCOMPLETE"
    if any(token in text for token in ("missing", "unavailable", "no data", "empty", "not usable")):
        return "DATA_MISSING"
    return fallback


def _safe_text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    value = str(value).strip()
    return value or None


def _ticker_from_url(url: str | None) -> str | None:
    if not url:
        return None
    query = parse_qs(urlparse(url).query)
    for key in ("ticker", "symbol", "s"):
        values = query.get(key, [])
        if len(values) == 1:
            return values[0].strip().upper() or None
    if urlparse(url).hostname == "news.google.com":
        values = query.get("q", [])
        if len(values) == 1:
            return values[0].split(maxsplit=1)[0].strip().upper() or None
    return None


def _degradation(
    *,
    ticker: str | None,
    provider: str,
    source_url: str | None,
    attempt_status: str,
    outcome_status: str,
    detail: object,
    observed_at: str,
    retry_state: str = "NONE",
    fallback_code: str = "UNKNOWN_FAILURE",
) -> dict[str, Any]:
    reason_detail = _safe_text(detail)
    return {
        "ticker": ticker,
        "provider": provider,
        "source_url": source_url,
        "attempt_status": attempt_status,
        "outcome_status": outcome_status,
        "reason_code": classify_failure_reason(
            reason_detail, fallback=fallback_code
        ),
        "reason_detail": reason_detail,
        "observed_at": observed_at,
        "retry_state": retry_state,
    }


def build_source_degradation_report(
    signals: pd.DataFrame | None,
    *,
    yahoo_ohlc_failures: Iterable[dict[str, object]] = (),
    yahoo_ohlc_retry_deferred: Iterable[dict[str, object]] = (),
    rss_warnings: Iterable[object] = (),
    observed_at: datetime,
) -> dict[str, object]:
    """Build provider evidence without treating degraded data as a hard failure.

    The report intentionally stores only non-usable source outcomes.  Healthy
    rows remain visible in the normal ticker traceability ledger, while this
    compact report makes outages reviewable at full-universe scale.
    """
    observed_at_text = observed_at.isoformat()
    records: list[dict[str, Any]] = []
    if signals is not None and not signals.empty:
        for row in signals.to_dict(orient="records"):
            ticker = _safe_text(row.get("ticker"))
            current_status = (_safe_text(row.get("current_price_status")) or "FAILED").upper()
            if current_status != "USABLE":
                reason = row.get("current_price_reason")
                records.append(
                    _degradation(
                        ticker=ticker,
                        provider=_safe_text(row.get("current_price_source")) or "current_price",
                        source_url=None,
                        attempt_status="ATTEMPTED",
                        outcome_status=current_status,
                        detail=reason,
                        observed_at=observed_at_text,
                        fallback_code="DATA_MISSING",
                    )
                )
            technical_status = (_safe_text(row.get("technical_status")) or "FAILED").upper()
            if technical_status != "USABLE":
                reason = row.get("technical_source_detail") or row.get("technical_reason")
                records.append(
                    _degradation(
                        ticker=ticker,
                        provider=_safe_text(row.get("tech_source_used")) or "technical_history",
                        source_url=None,
                        attempt_status="ATTEMPTED",
                        outcome_status=technical_status,
                        detail=reason,
                        observed_at=observed_at_text,
                        fallback_code="DATA_MISSING",
                    )
                )
            yahoo_status = (_safe_text(row.get("yahoo_data_status")) or "disabled").lower()
            if yahoo_status not in {"cache_fresh", "live_ok"}:
                if yahoo_status == "disabled":
                    attempt_status, outcome_status = "NOT_ATTEMPTED", "NOT_ATTEMPTED"
                elif yahoo_status.endswith("partial") or yahoo_status == "cache_stale":
                    attempt_status, outcome_status = "ATTEMPTED", "PARTIAL"
                else:
                    attempt_status, outcome_status = "ATTEMPTED", "FAILED"
                records.append(
                    _degradation(
                        ticker=ticker,
                        provider="yahoo_metadata",
                        source_url=None,
                        attempt_status=attempt_status,
                        outcome_status=outcome_status,
                        detail=row.get("yahoo_data_reason") or yahoo_status,
                        observed_at=observed_at_text,
                        fallback_code=(
                            "CONFIG_MISSING"
                            if yahoo_status == "disabled"
                            else "DATA_MISSING"
                        ),
                    )
                )

    for failure in yahoo_ohlc_failures:
        ticker = _safe_text(failure.get("ticker"))
        detail = failure.get("error")
        records.append(
            _degradation(
                ticker=ticker,
                provider="yahoo_ohlc",
                source_url=(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                    if ticker
                    else None
                ),
                attempt_status="ATTEMPTED",
                outcome_status="FAILED",
                detail=detail,
                observed_at=observed_at_text,
                fallback_code="DATA_MISSING",
            )
        )
    for deferred in yahoo_ohlc_retry_deferred:
        ticker = _safe_text(deferred.get("ticker"))
        records.append(
            _degradation(
                ticker=ticker,
                provider="yahoo_ohlc",
                source_url=(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                    if ticker
                    else None
                ),
                attempt_status="NOT_ATTEMPTED",
                outcome_status="NOT_ATTEMPTED",
                detail=deferred.get("error"),
                observed_at=observed_at_text,
                retry_state="CIRCUIT_OPEN",
                fallback_code="RETRY_DEFERRED",
            )
        )
    for raw_warning in rss_warnings:
        detail = _safe_text(raw_warning)
        if not detail or "rss" not in detail.lower():
            continue
        match = _URL_RE.search(detail)
        source_url = match.group(0) if match else None
        # An empty source is a degradation, but a harmless skipped undated item
        # is not an outage of the provider.
        if "bez data publikace" in detail.lower() or "budoucím datem" in detail.lower():
            continue
        records.append(
            _degradation(
                ticker=_ticker_from_url(source_url),
                provider="rss",
                source_url=source_url,
                attempt_status="ATTEMPTED",
                outcome_status="PARTIAL" if "parser" in detail.lower() else "FAILED",
                detail=detail,
                observed_at=observed_at_text,
                fallback_code="DATA_MISSING",
            )
        )

    by_reason = Counter(str(record["reason_code"]) for record in records)
    by_provider = Counter(str(record["provider"]) for record in records)
    retry_deferred = sum(record["retry_state"] == "CIRCUIT_OPEN" for record in records)
    return {
        "schema_version": 1,
        "observed_at": observed_at_text,
        "degradation_count": len(records),
        "by_reason_code": dict(sorted(by_reason.items())),
        "by_provider": dict(sorted(by_provider.items())),
        "provider_circuits": {
            "yahoo_ohlc": "CIRCUIT_OPEN" if retry_deferred else "CLOSED",
        },
        "records": records,
    }
