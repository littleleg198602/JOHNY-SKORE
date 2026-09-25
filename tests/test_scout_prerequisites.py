from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

import pandas as pd

from market_checker_app.agents.quality_gate_agent import QualityGateAgent
from market_checker_app.models import RunMetadata
from market_checker_app.services.ranking_service import RankingService
from market_checker_app.storage.sqlite_store import SQLiteStore


class ScoutPrerequisiteTests(unittest.TestCase):
    def test_unranked_signal_is_persisted_as_sql_null(self) -> None:
        now = datetime.now(timezone.utc)
        fields = SQLiteStore.SIGNAL_HISTORY_INSERT.split("VALUES", 1)[0]
        columns = fields.split("(", 1)[1].rsplit(")", 1)[0]
        optional_columns = [item.strip() for item in columns.split(",") if item.strip() not in {"run_id", "updated_at"}]
        rows = []
        for ticker, score, price, usable in [
            ("BAD", 90.0, None, False),
            ("GOOD", 50.0, 100.0, True),
        ]:
            row = {key: None for key in optional_columns}
            row.update(
                ticker=ticker,
                final_total_score=score,
                current_price=price,
                current_price_source="yahoo_ohlc_close" if usable else "missing",
                ohlc_history_usable=usable,
            )
            rows.append(row)
        ranked = RankingService.apply_ranking(pd.DataFrame(rows))
        with tempfile.TemporaryDirectory() as folder:
            store = SQLiteStore(Path(folder) / "test.db")
            run_id = store.save_run(
                RunMetadata(now, now, 2, 2, 0, 0), ranked, now.isoformat()
            )
            with sqlite3.connect(store.db_path) as conn:
                saved = dict(conn.execute(
                    "SELECT ticker, rank_in_watchlist FROM signal_history WHERE run_id=?",
                    (run_id,),
                ).fetchall())
        self.assertIsNone(saved["BAD"])
        self.assertEqual(1, saved["GOOD"])

    def test_long_run_evidence_and_real_future_are_distinguished(self) -> None:
        gate = QualityGateAgent()
        started = datetime.now(timezone.utc) - timedelta(minutes=53)
        checked = datetime.now(timezone.utc)
        for minute in (0, 25, 52):
            rejects: list[dict[str, str]] = []
            gate._check_timestamp(started + timedelta(minutes=minute), checked, "Evidence", rejects)
            self.assertEqual([], rejects)
        rejects = []
        gate._check_timestamp(
            started + timedelta(minutes=52),
            checked,
            "Signál",
            rejects,
            max_age_minutes=15,
            age_reference=started,
        )
        self.assertEqual([], rejects)
        rejects = []
        gate._check_timestamp(
            checked + timedelta(minutes=3), checked, "Evidence", rejects
        )
        self.assertIn("future_observation", {item["code"] for item in rejects})
        rejects = []
        gate._check_timestamp(
            started - timedelta(minutes=16),
            checked,
            "Signál",
            rejects,
            max_age_minutes=15,
            age_reference=started,
        )
        self.assertIn("stale_observation", {item["code"] for item in rejects})


if __name__ == "__main__":
    unittest.main()
