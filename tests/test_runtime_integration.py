from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from market_checker_app.collectors.yahoo_client import YahooClient
from market_checker_app.config import AppConfig
from market_checker_app.models import PerformanceSnapshot, RunMetadata, YahooSnapshot
from market_checker_app.services.pipeline_service import PipelineService
from market_checker_app.storage.sqlite_store import SQLiteStore


def _history() -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=260, freq="B", tz="UTC")
    close = pd.Series([100 + idx * 0.15 for idx in range(len(index))], index=index)
    return pd.DataFrame(
        {
            "Open": close - 0.4,
            "High": close + 0.7,
            "Low": close - 0.8,
            "Close": close,
            "Volume": 1_000_000,
        },
        index=index,
    )


class _FakeYahooClient:
    def fetch_snapshots(self, ticker: str):
        history = _history()
        data = {
            "currentPrice": float(history["Close"].iloc[-1]),
            "targetMeanPrice": 150.0,
            "targetMedianPrice": 148.0,
            "targetLowPrice": 120.0,
            "targetHighPrice": 170.0,
            "recommendationMean": 2.0,
            "numberOfAnalystOpinions": 12,
            "forwardPE": 22.0,
            "profitMargins": 0.2,
            "revenueGrowth": 0.1,
            "earningsGrowth": 0.12,
            "debtToEquity": 80.0,
        }
        performance = PerformanceSnapshot(ticker, 1.0, 2.0, 3.0, 4.0)
        return YahooSnapshot(ticker, data, "ok"), performance, None

    def fetch_ohlc(self, ticker: str, period: str = "1y", interval: str = "1d"):
        return _history(), None


class RuntimeIntegrationTests(unittest.TestCase):
    def test_pipeline_persists_signals_and_history_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            store = SQLiteStore(output_dir / "history.db")
            pipeline = PipelineService(
                AppConfig(output_dir=output_dir, sqlite_path=store.db_path, save_history=True)
            )
            pipeline.yahoo_client = _FakeYahooClient()

            result = pipeline.run(
                ["AAPL"],
                [],
                store,
                yahoo_only_tickers={"AAPL"},
                rss_enabled=False,
                mt5_enabled=False,
            )

            self.assertEqual(1, len(result["signals"]))
            self.assertIsNotNone(result["run_id"])
            self.assertEqual([], result["errors"])
            self.assertEqual([], result["warnings"])
            stored = store.read_signals_for_run(int(result["run_id"]))
            self.assertEqual(1, len(stored))
            self.assertEqual("AAPL", stored.iloc[0]["ticker"])
            self.assertFalse(store.read_global_history().empty)

    def test_empty_watchlist_is_rejected(self):
        pipeline = PipelineService(AppConfig(save_history=False))
        with self.assertRaisesRegex(ValueError, "Watchlist je prázdný"):
            pipeline.run([], [], None)

    def test_failed_signal_insert_rolls_back_run_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            now = datetime.now(timezone.utc)
            metadata = RunMetadata(now, now, 1, 1, 0, 0)
            signals = pd.DataFrame([{"ticker": "AAPL"}])
            store.ensure_schema()
            store.SIGNAL_HISTORY_INSERT = "INSERT INTO signal_history(run_id) VALUES (?, ?)"

            with patch.object(store, "_build_signal_payload", return_value=[(1, 2)]):
                with self.assertRaises(sqlite3.Error):
                    store.save_run(metadata, signals, now.isoformat())

            with store._connect() as conn:
                count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            self.assertEqual(0, count)


class YahooClientTests(unittest.TestCase):
    def setUp(self):
        YahooClient._cache.clear()
        YahooClient._rate_limited_until = 0.0

    def test_snapshot_and_ohlc_share_one_history_download(self):
        calls = {"info": 0, "history": 0}

        class FakeTicker:
            @property
            def info(self):
                calls["info"] += 1
                return {"currentPrice": 100.0}

            def history(self, **kwargs):
                calls["history"] += 1
                return _history()

        with patch("market_checker_app.collectors.yahoo_client.yf.Ticker", return_value=FakeTicker()):
            client = YahooClient(retry_attempts=1)
            snapshot, _, warning = client.fetch_snapshots("AAPL")
            ohlc, ohlc_warning = client.fetch_ohlc("AAPL")

        self.assertEqual("ok", snapshot.status)
        self.assertIsNone(warning)
        self.assertIsNone(ohlc_warning)
        self.assertIsNotNone(ohlc)
        self.assertEqual(1, calls["info"])
        self.assertEqual(1, calls["history"])


if __name__ == "__main__":
    unittest.main()
