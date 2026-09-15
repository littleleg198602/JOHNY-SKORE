from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from market_checker_app.models import RunMetadata
from market_checker_app.services.ticker_traceability_service import (
    build_ticker_traceability,
    summarize_ticker_traceability,
)
from market_checker_app.storage.sqlite_store import SQLiteStore


class TickerTraceabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signals = pd.DataFrame(
            [
                {
                    "ticker": "USABLE",
                    "ranking_eligible": True,
                    "ranking_status": "USABLE",
                    "current_price_status": "USABLE",
                    "technical_status": "USABLE",
                },
                {
                    "ticker": "PARTIAL",
                    "ranking_eligible": False,
                    "ranking_status": "INELIGIBLE",
                    "ranking_reason": "TECHNICAL_HISTORY_INSUFFICIENT",
                    "current_price_status": "USABLE",
                    "technical_status": "PARTIAL",
                },
                {
                    "ticker": "FAILED",
                    "ranking_eligible": False,
                    "ranking_status": "INELIGIBLE",
                    "ranking_reason": "CURRENT_PRICE_UNAVAILABLE",
                    "current_price_status": "FAILED",
                    "technical_status": "FAILED",
                },
            ]
        )

    def test_every_requested_ticker_has_unambiguous_outcome(self) -> None:
        records = build_ticker_traceability(
            ["USABLE", "PARTIAL", "FAILED", "NOT_RUN"], self.signals
        )
        by_ticker = {record["ticker"]: record for record in records}

        self.assertEqual("ATTEMPTED", by_ticker["USABLE"]["attempt_status"])
        self.assertEqual("USABLE", by_ticker["USABLE"]["outcome_status"])
        self.assertEqual("PARTIAL", by_ticker["PARTIAL"]["outcome_status"])
        self.assertEqual("FAILED", by_ticker["FAILED"]["outcome_status"])
        self.assertEqual("NOT_ATTEMPTED", by_ticker["NOT_RUN"]["attempt_status"])
        self.assertEqual("PIPELINE_ROW_MISSING", by_ticker["NOT_RUN"]["outcome_reason"])

        summary = summarize_ticker_traceability(records)
        self.assertEqual(
            {
                "requested": 4,
                "attempted": 3,
                "usable": 1,
                "partial": 1,
                "failed": 1,
                "not_attempted": 1,
                "attempt_coverage_pct": 75.0,
                "usable_coverage_pct": 25.0,
            },
            summary,
        )

    def test_sqlite_persists_the_same_complete_accounting_as_the_run(self) -> None:
        records = build_ticker_traceability(
            ["USABLE", "PARTIAL", "FAILED", "NOT_RUN"], self.signals
        )
        now = datetime.now(timezone.utc)
        metadata = RunMetadata(now, now, 4, 3, 0, 0, "")
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            run_id = store.save_run(
                metadata,
                pd.DataFrame(),
                now.isoformat(),
                ticker_traceability=records,
            )
            persisted = store.read_ticker_traceability_for_run(run_id)

        self.assertEqual(4, len(persisted))
        self.assertEqual(
            {"USABLE", "PARTIAL", "FAILED", "NOT_ATTEMPTED"},
            set(persisted["outcome_status"]),
        )
        self.assertEqual(
            "NOT_ATTEMPTED",
            persisted.loc[persisted["ticker"] == "NOT_RUN", "attempt_status"].iloc[0],
        )


if __name__ == "__main__":
    unittest.main()
