from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from market_checker_app.models import RunMetadata
from market_checker_app.services.source_degradation_service import (
    build_source_degradation_report,
    classify_failure_reason,
)
from market_checker_app.storage.sqlite_store import SQLiteStore


class SourceDegradationTests(unittest.TestCase):
    def test_transport_and_data_failures_have_stable_reason_codes(self) -> None:
        self.assertEqual("RATE_LIMITED", classify_failure_reason("HTTP 429 Too Many Requests"))
        self.assertEqual("ACCESS_DENIED", classify_failure_reason("HTTP 403 forbidden"))
        self.assertEqual("TIMEOUT", classify_failure_reason("request timed out"))
        self.assertEqual("PARSE_ERROR", classify_failure_reason("JSON decode error"))
        self.assertEqual("IDENTITY_UNRESOLVED", classify_failure_reason("CIK identity conflict"))
        self.assertEqual("RETRY_DEFERRED", classify_failure_reason("retry checkpoint deferred"))

    def test_report_keeps_ticker_url_time_and_retry_evidence(self) -> None:
        signals = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "current_price_status": "PARTIAL",
                    "current_price_reason": "UNDATED_METADATA_QUOTE",
                    "current_price_source": "yahoo_metadata_quote_undated",
                    "technical_status": "FAILED",
                    "technical_reason": "OHLC_HISTORY_UNAVAILABLE",
                    "tech_source_used": "yfinance_bulk",
                    "technical_source_detail": "HTTP 429 Too Many Requests",
                    "yahoo_data_status": "cache_stale",
                    "yahoo_data_reason": "stale cache",
                }
            ]
        )
        report = build_source_degradation_report(
            signals,
            yahoo_ohlc_failures=[{"ticker": "MSFT", "error": "HTTP 403 forbidden"}],
            yahoo_ohlc_retry_deferred=[
                {"ticker": "NVDA", "error": "retry checkpoint deferred"}
            ],
            rss_warnings=[
                "RSS načtení selhalo (https://news.google.com/rss/search?q=AMD%20stock). Detail: request timed out"
            ],
            observed_at=datetime(2026, 9, 15, tzinfo=timezone.utc),
        )

        self.assertEqual("CIRCUIT_OPEN", report["provider_circuits"]["yahoo_ohlc"])
        by_key = {(record["provider"], record["ticker"]): record for record in report["records"]}
        self.assertEqual("UNDATED_DATA", by_key[("yahoo_metadata_quote_undated", "AAPL")]["reason_code"])
        self.assertEqual("RATE_LIMITED", by_key[("yfinance_bulk", "AAPL")]["reason_code"])
        self.assertEqual("ACCESS_DENIED", by_key[("yahoo_ohlc", "MSFT")]["reason_code"])
        deferred = by_key[("yahoo_ohlc", "NVDA")]
        self.assertEqual("NOT_ATTEMPTED", deferred["attempt_status"])
        self.assertEqual("CIRCUIT_OPEN", deferred["retry_state"])
        rss = by_key[("rss", "AMD")]
        self.assertEqual("TIMEOUT", rss["reason_code"])
        self.assertEqual("2026-09-15T00:00:00+00:00", rss["observed_at"])

    def test_sqlite_persists_normalized_degradation_rows(self) -> None:
        now = datetime.now(timezone.utc)
        report = build_source_degradation_report(
            pd.DataFrame(),
            yahoo_ohlc_failures=[{"ticker": "AAPL", "error": "HTTP 429"}],
            observed_at=now,
        )
        metadata = RunMetadata(now, now, 1, 0, 0, 0, "")
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            run_id = store.save_run(
                metadata,
                pd.DataFrame(),
                now.isoformat(),
                source_degradations=report["records"],
            )
            persisted = store.read_source_degradations_for_run(run_id)

        self.assertEqual(1, len(persisted))
        self.assertEqual("RATE_LIMITED", persisted.iloc[0]["reason_code"])
        self.assertEqual("AAPL", persisted.iloc[0]["ticker"])


if __name__ == "__main__":
    unittest.main()
