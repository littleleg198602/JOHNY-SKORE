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
from market_checker_app.storage.yahoo_cache_store import YahooCacheStore


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


class _PartialYahooClient(_FakeYahooClient):
    def fetch_snapshots(self, ticker: str):
        performance = PerformanceSnapshot(ticker, 1.0, 2.0, 3.0, 4.0)
        snapshot = YahooSnapshot(
            ticker,
            {"currentPrice": 100.0, "forwardPE": 20.0},
            "partial",
        )
        return snapshot, performance, f"Yahoo metadata jsou pro {ticker} pouze částečná."


class _ForbiddenYahooClient:
    def fetch_snapshots(self, ticker: str):
        raise AssertionError("Large-universe mode must not call Yahoo metadata")

    def fetch_ohlc(self, ticker: str, period: str = "1y", interval: str = "1d"):
        raise AssertionError("Large-universe mode must not call Yahoo OHLC fallback")

    fetch_ohlc_only = fetch_ohlc


class _FakeBatchMT5Client:
    def fetch_ohlcv_batch(self, tickers, bars=300, progress_callback=None):
        frames = {}
        for completed, ticker in enumerate(tickers, start=1):
            frames[ticker] = _history()
            if progress_callback:
                progress_callback(completed, len(tickers), ticker)
        return frames, {}


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
            self.assertEqual("yahoo_metadata", stored.iloc[0]["current_price_source"])
            for column in ("decision_signal", "forecast", "action", "action_reasons"):
                self.assertIn(column, stored.columns)
            self.assertEqual(result["signals"].iloc[0]["action"], stored.iloc[0]["action"])
            global_history = store.read_global_history()
            self.assertFalse(global_history.empty)
            self.assertIn("forecast", global_history.columns)
            self.assertIn("action", global_history.columns)

    def test_existing_database_is_migrated_additively_for_v21(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "history.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "CREATE TABLE signal_history (id INTEGER PRIMARY KEY, run_id INTEGER, ticker TEXT)"
                )

            store = SQLiteStore(db_path)
            store.ensure_schema()
            with store._connect() as conn:
                columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(signal_history)").fetchall()
                }

            self.assertTrue(
                {
                    "decision_signal",
                    "forecast",
                    "action",
                    "action_reasons",
                    "panic_score",
                    "bull_bear_spread",
                    "blocked_reasons",
                    "module_breakdown",
                }.issubset(columns)
            )

    def test_empty_watchlist_is_rejected(self):
        pipeline = PipelineService(AppConfig(save_history=False))
        with self.assertRaisesRegex(ValueError, "Watchlist je prázdný"):
            pipeline.run([], [], None)

    def test_partial_yahoo_metadata_is_not_reported_as_total_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = PipelineService(
                AppConfig(
                    output_dir=Path(tmp),
                    sqlite_path=Path(tmp) / "history.db",
                    save_history=False,
                )
            )
            pipeline.yahoo_client = _PartialYahooClient()

            result = pipeline.run(
                ["AAPL"],
                [],
                None,
                yahoo_only_tickers={"AAPL"},
                rss_enabled=False,
                mt5_enabled=False,
            )

        self.assertEqual([], result["errors"])
        self.assertEqual("live_partial", result["signals"].iloc[0]["yahoo_data_status"])
        self.assertGreater(float(result["signals"].iloc[0]["yahoo_confidence"]), 0.0)

    def test_large_universe_processes_every_ticker_without_yahoo_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = PipelineService(
                AppConfig(
                    output_dir=Path(tmp),
                    sqlite_path=Path(tmp) / "history.db",
                    save_history=False,
                    large_universe_threshold=2,
                    max_tickers_per_run=1000,
                )
            )
            pipeline.yahoo_client = _ForbiddenYahooClient()
            pipeline.mt5_client = _FakeBatchMT5Client()

            result = pipeline.run(
                ["AAPL", "MSFT", "NVDA"],
                [],
                None,
                rss_enabled=False,
                mt5_enabled=True,
            )

        signals = result["signals"]
        self.assertEqual(3, len(signals))
        self.assertEqual({"AAPL", "MSFT", "NVDA"}, set(signals["ticker"]))
        self.assertTrue((signals["tech_source_used"] == "mt5").all())
        self.assertTrue(signals["current_price"].notna().all())
        self.assertTrue((signals["current_price_source"] == "mt5_close").all())
        self.assertTrue((signals["yahoo_confidence"] == 0.0).all())
        self.assertEqual([], result["errors"])

    def test_large_universe_uses_persistent_yahoo_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "history.db"
            cache = YahooCacheStore(db_path)
            cache.upsert_success(
                "AAPL",
                {
                    "_market_checker_yahoo_quality": "ok",
                    "currentPrice": 140.0,
                    "targetMeanPrice": 160.0,
                    "targetMedianPrice": 158.0,
                    "recommendationMean": 2.0,
                    "numberOfAnalystOpinions": 20,
                    "forwardPE": 22.0,
                    "profitMargins": 0.2,
                    "revenueGrowth": 0.1,
                    "earningsGrowth": 0.12,
                    "debtToEquity": 80.0,
                },
            )
            pipeline = PipelineService(
                AppConfig(
                    output_dir=Path(tmp),
                    sqlite_path=db_path,
                    save_history=False,
                    large_universe_threshold=1,
                )
            )
            pipeline.yahoo_client = _ForbiddenYahooClient()
            pipeline.mt5_client = _FakeBatchMT5Client()

            result = pipeline.run(
                ["AAPL", "MSFT"],
                [],
                None,
                rss_enabled=False,
                mt5_enabled=True,
            )

        signals = result["signals"].set_index("ticker")
        self.assertEqual("cache_fresh", signals.loc["AAPL", "yahoo_data_status"])
        self.assertGreater(float(signals.loc["AAPL", "yahoo_confidence"]), 0.0)
        self.assertEqual("missing", signals.loc["MSFT", "yahoo_data_status"])
        self.assertEqual(0.0, float(signals.loc["MSFT", "yahoo_confidence"]))

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
                return {
                    "currentPrice": 100.0,
                    "targetMeanPrice": 120.0,
                    "targetMedianPrice": 119.0,
                    "recommendationMean": 2.0,
                    "numberOfAnalystOpinions": 12,
                    "forwardPE": 22.0,
                }

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
