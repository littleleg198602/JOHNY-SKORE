from __future__ import annotations

"""One explicit, lossless ticker-accounting contract for a pipeline run."""

from collections.abc import Iterable
from typing import Any

import pandas as pd


TRACEABILITY_SCHEMA_VERSION = 1
OUTCOME_STATUSES = ("USABLE", "PARTIAL", "FAILED", "NOT_ATTEMPTED")


def _normalised_tickers(tickers: Iterable[object]) -> list[str]:
    return list(
        dict.fromkeys(
            str(ticker).strip().upper()
            for ticker in tickers
            if str(ticker).strip()
        )
    )


def _true(value: object) -> bool:
    return value is True or (not pd.isna(value) and bool(value))


def _text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def build_ticker_traceability(
    requested_tickers: Iterable[object],
    signals: pd.DataFrame | None,
) -> list[dict[str, Any]]:
    """Account for every requested ticker, including absent pipeline output.

    ``attempt_status`` describes whether the pipeline produced a ticker row;
    ``outcome_status`` describes the quality of that result.  This keeps an
    absent row from being incorrectly counted as a failed result.
    """
    requested = _normalised_tickers(requested_tickers)
    by_ticker: dict[str, dict[str, object]] = {}
    if signals is not None and not signals.empty and "ticker" in signals.columns:
        for raw in signals.to_dict(orient="records"):
            ticker = _text(raw.get("ticker"))
            if ticker and ticker.upper() not in by_ticker:
                by_ticker[ticker.upper()] = raw

    records: list[dict[str, Any]] = []
    for ticker in requested:
        row = by_ticker.get(ticker)
        if row is None:
            records.append(
                {
                    "ticker": ticker,
                    "request_status": "REQUESTED",
                    "attempt_status": "NOT_ATTEMPTED",
                    "outcome_status": "NOT_ATTEMPTED",
                    "outcome_reason": "PIPELINE_ROW_MISSING",
                    "ranking_eligible": False,
                    "ranking_status": None,
                    "ranking_reason": None,
                    "current_price_status": None,
                    "current_price_reason": None,
                    "technical_status": None,
                    "technical_reason": None,
                }
            )
            continue

        ranking_eligible = _true(row.get("ranking_eligible"))
        current_status = _text(row.get("current_price_status"))
        technical_status = _text(row.get("technical_status"))
        ranking_status = _text(row.get("ranking_status"))
        ranking_reason = _text(row.get("ranking_reason"))
        if ranking_eligible:
            outcome_status = "USABLE"
            outcome_reason = "RANKING_ELIGIBLE"
        elif "PARTIAL" in {current_status, technical_status}:
            outcome_status = "PARTIAL"
            outcome_reason = ranking_reason or "INPUT_PARTIALLY_USABLE"
        else:
            outcome_status = "FAILED"
            outcome_reason = ranking_reason or "RANKING_INELIGIBLE"
        records.append(
            {
                "ticker": ticker,
                "request_status": "REQUESTED",
                "attempt_status": "ATTEMPTED",
                "outcome_status": outcome_status,
                "outcome_reason": outcome_reason,
                "ranking_eligible": ranking_eligible,
                "ranking_status": ranking_status,
                "ranking_reason": ranking_reason,
                "current_price_status": current_status,
                "current_price_reason": _text(row.get("current_price_reason")),
                "technical_status": technical_status,
                "technical_reason": _text(row.get("technical_reason")),
            }
        )
    return records


def summarize_ticker_traceability(
    records: Iterable[dict[str, Any]],
) -> dict[str, int | float]:
    records = list(records)
    counts = {status.lower(): 0 for status in OUTCOME_STATUSES}
    for record in records:
        outcome = str(record.get("outcome_status") or "NOT_ATTEMPTED").upper()
        counts[outcome.lower()] = counts.get(outcome.lower(), 0) + 1
    requested = len(records)
    attempted = sum(
        str(record.get("attempt_status") or "").upper() == "ATTEMPTED"
        for record in records
    )
    return {
        "requested": requested,
        "attempted": attempted,
        "usable": counts["usable"],
        "partial": counts["partial"],
        "failed": counts["failed"],
        "not_attempted": counts["not_attempted"],
        "attempt_coverage_pct": round(100.0 * attempted / requested, 2)
        if requested
        else 0.0,
        "usable_coverage_pct": round(100.0 * counts["usable"] / requested, 2)
        if requested
        else 0.0,
    }
