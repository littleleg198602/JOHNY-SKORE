from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from market_checker_app.models import RunMetadata
from market_checker_app.prediction_contract import build_point_in_time_snapshot
from market_checker_app.services.prediction_label_service import PredictionLabelService
from market_checker_app.services.us_equity_calendar_service import expected_sessions
from market_checker_app.storage.sqlite_store import SQLiteStore


SNAPSHOT_AT = datetime(2026, 1, 2, 22, tzinfo=timezone.utc)
EVALUATION_AT = datetime(2026, 1, 12, 22, tzinfo=timezone.utc)


def _history(sessions: list[pd.Timestamp], values: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"Close": values}, index=sessions)


def _target_sessions() -> list[pd.Timestamp]:
    return list(
        expected_sessions(
            snapshot_as_of=SNAPSHOT_AT,
            evaluation_as_of=EVALUATION_AT,
            horizon=5,
        )[:6]
    )


class PredictionLabelServiceTests(unittest.TestCase):
    def _snapshot(self, store: SQLiteStore, *, as_of: datetime = SNAPSHOT_AT) -> int:
        store.ensure_schema()
        run_id = store.insert_run(RunMetadata(started_at=as_of, finished_at=as_of, watchlist_size=1, processed_symbols=1, warnings_count=0, errors_count=0))
        snapshot = build_point_in_time_snapshot(
            run_id=run_id, ticker="AAPL", observed_at=as_of,
            feature_payload={"current_price": 100.0}, baseline_output={"action": "BUY"},
            provenance={"price_source": "test"}, benchmark_ticker="SPY",
            benchmark_selection="default_fallback",
        )
        self.assertEqual(1, store.save_prediction_snapshots([snapshot]))
        return run_id

    def test_mature_exact_session_window_is_resolved_and_replay_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            self._snapshot(store)
            sessions = _target_sessions()
            histories = {
                "AAPL": _history(sessions, [100, 101, 102, 103, 104, 110]),
                "SPY": _history(sessions, [100, 100, 101, 102, 103, 105]),
            }
            service = PredictionLabelService()
            first = service.resolve_pending_snapshots(store=store, price_loader=lambda ticker: histories[ticker], as_of=EVALUATION_AT)
            self.assertEqual(1, first["resolved"])
            stored = store.read_prediction_snapshots(ticker="AAPL")
            self.assertEqual("RESOLVED", stored.iloc[0]["label_status"])
            self.assertAlmostEqual(0.05, float(stored.iloc[0]["target_value"]))
            second = service.resolve_pending_snapshots(store=store, price_loader=lambda ticker: histories[ticker], as_of=EVALUATION_AT)
            self.assertEqual(0, second["pending_before"])

    def test_incomplete_horizon_remains_pending_before_target_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            self._snapshot(store)
            sessions = _target_sessions()[:3]
            histories = {"AAPL": _history(sessions, [100, 101, 102]), "SPY": _history(sessions, [100, 100, 101])}
            result = PredictionLabelService().resolve_pending_snapshots(
                store=store, price_loader=lambda ticker: histories[ticker],
                as_of=datetime(2026, 1, 6, 22, tzinfo=timezone.utc),
            )
            self.assertEqual(0, result["resolved"])
            self.assertEqual(1, result["deferred"])
            self.assertEqual("PENDING", store.read_prediction_snapshots(ticker="AAPL").iloc[0]["label_status"])

    def test_missing_calendar_session_is_not_silently_skipped_by_both_series(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            self._snapshot(store)
            sessions = _target_sessions()
            incomplete = sessions[:2] + sessions[3:]
            histories = {
                "AAPL": _history(incomplete, [100, 101, 103, 104, 105]),
                "SPY": _history(incomplete, [100, 100, 102, 103, 104]),
            }
            result = PredictionLabelService(maturity_grace_days=7).resolve_pending_snapshots(
                store=store, price_loader=lambda ticker: histories[ticker], as_of=EVALUATION_AT
            )
            self.assertEqual(0, result["resolved"])
            self.assertEqual(1, result["unavailable"])

    def test_loader_prices_after_evaluation_clock_do_not_close_label_early(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            self._snapshot(store)
            sessions = _target_sessions()
            histories = {
                "AAPL": _history(sessions, [100, 101, 102, 103, 104, 110]),
                "SPY": _history(sessions, [100, 100, 101, 102, 103, 105]),
            }
            result = PredictionLabelService().resolve_pending_snapshots(
                store=store, price_loader=lambda ticker: histories[ticker],
                as_of=datetime(2026, 1, 6, 22, tzinfo=timezone.utc),
            )
            self.assertEqual(0, result["resolved"])
            self.assertEqual(1, result["deferred"])
            self.assertEqual("PENDING", store.read_prediction_snapshots(ticker="AAPL").iloc[0]["label_status"])

    def test_loaded_but_mature_unusable_window_becomes_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            self._snapshot(store)
            sessions = _target_sessions()[:2]
            histories = {"AAPL": _history(sessions, [100, 101]), "SPY": _history(sessions, [100, 100])}
            result = PredictionLabelService(maturity_grace_days=7).resolve_pending_snapshots(
                store=store, price_loader=lambda ticker: histories[ticker],
                as_of=datetime(2026, 1, 20, 22, tzinfo=timezone.utc),
            )
            self.assertEqual(0, result["resolved"])
            self.assertEqual(1, result["unavailable"])


if __name__ == "__main__":
    unittest.main()
