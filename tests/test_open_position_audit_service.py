from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from market_checker_app.models import RunMetadata
from market_checker_app.services.open_position_audit_service import (
    build_open_position_audit,
)
from market_checker_app.storage.sqlite_store import SQLiteStore


def _position(**changes: object) -> pd.DataFrame:
    record: dict[str, object] = {
        "position_ticket": "1001",
        "ticker": "AAPL",
        "side": "BUY",
        "volume": 0.1,
        "opened_at": "2026-09-18T08:00:00+00:00",
        "entry_price": 100.0,
        "current_price": 105.0,
        "stop_loss": 90.0,
        "take_profit": 120.0,
        "swap": 0.0,
        "profit": 5.0,
        "comment": "fixture",
    }
    record.update(changes)
    return pd.DataFrame([record])


def _signals(**changes: object) -> pd.DataFrame:
    record: dict[str, object] = {
        "ticker": "AAPL",
        "forecast": "UP",
        "action": "BUY",
        "final_total_score": 72.0,
        "final_confidence": 81.0,
        "risk_score": 25.0,
        "data_quality_score": 90.0,
    }
    record.update(changes)
    return pd.DataFrame([record])


class OpenPositionAuditServiceTests(unittest.TestCase):
    def test_matching_position_is_monitor_only_and_reports_pnl(self) -> None:
        report = build_open_position_audit(_position(), _signals())

        self.assertEqual("SUCCESS", report["status"])
        self.assertTrue(report["analysis_only"])
        row = report["positions"][0]
        self.assertEqual("MONITOR", row["audit_status"])
        self.assertEqual(5.0, row["price_change_pct"])
        self.assertEqual("BUY", row["action"])

    def test_conflict_and_missing_stop_loss_need_review(self) -> None:
        report = build_open_position_audit(
            _position(stop_loss=0.0),
            _signals(action="SELL", forecast="DOWN"),
        )

        row = report["positions"][0]
        self.assertEqual("REVIEW", row["audit_status"])
        self.assertIn("chybí stop-loss", row["audit_reasons"])
        self.assertTrue(any("rozporu" in reason for reason in row["audit_reasons"]))

    def test_unanalysed_position_needs_review(self) -> None:
        report = build_open_position_audit(_position(ticker="XAUUSD"), _signals())

        row = report["positions"][0]
        self.assertEqual("REVIEW", row["audit_status"])
        self.assertEqual("UNANALYZED", row["action"])

    def test_previous_status_is_exposed_after_persisting_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "history.db")
            now = datetime.now(timezone.utc)
            metadata = RunMetadata(now, now, 1, 1, 0, 0)
            first_run = store.save_run(metadata, pd.DataFrame(), now.isoformat())
            first = build_open_position_audit(_position(), _signals(), observed_at=now)
            store.save_open_position_audits(
                first_run, first["positions"], observed_at=str(first["observed_at"])
            )
            second_run = store.save_run(metadata, pd.DataFrame(), now.isoformat())
            previous = store.read_latest_open_position_audits_before(second_run)
            second = build_open_position_audit(
                _position(), _signals(action="SELL"), previous, observed_at=now
            )

        row = second["positions"][0]
        self.assertEqual("MONITOR", row["previous_audit_status"])
        self.assertTrue(row["status_changed"])
        self.assertEqual("REVIEW", row["audit_status"])


if __name__ == "__main__":
    unittest.main()
