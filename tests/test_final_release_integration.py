from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from market_checker_app.config import AppConfig
from market_checker_app.services.pipeline_service import PipelineService
from market_checker_app.storage.sqlite_store import SQLiteStore
from market_checker_app.utils.ticker_universe import load_canonical_tickers
from market_checker_app.weekly_shadow_runner import finalize_analysis_run
from tests.test_runtime_integration import _history, _FakeYahooClient


class OfflineYahoo(_FakeYahooClient):
    def __init__(self, failures=()):
        self.failures = set(failures)
        self.calls = []
        self.frame = _history()

    def fetch_ohlc_batch(self, tickers, **kwargs):
        self.calls.extend(tickers)
        return ({ticker: self.frame for ticker in tickers if ticker not in self.failures},
                {ticker: "fixture timeout" for ticker in tickers if ticker in self.failures})


class FinalReleaseIntegration(unittest.TestCase):
    def test_canonical_universe_empty_cache_and_restart_without_mt5(self):
        tickers = load_canonical_tickers()
        self.assertEqual(687, len(tickers))
        with tempfile.TemporaryDirectory() as directory, patch("socket.socket.connect", side_effect=AssertionError("Offline integration attempted network")):
            root = Path(directory)
            config = AppConfig(output_dir=root, sqlite_path=root / "history.db", export_excel=False)
            store = SQLiteStore(config.sqlite_path)
            first = PipelineService(config)
            first.yahoo_client = OfflineYahoo()
            result = first.run(tickers, [], store, rss_enabled=False, mt5_enabled=False, yahoo_metadata_enabled=False)
            summary = finalize_analysis_run(result=result, config=config, tickers=tickers, store=store, pipeline=first)
            self.assertEqual(687, len(result["signals"]))
            self.assertEqual(687, summary["point_in_time_snapshot_count"])
            self.assertEqual("SUCCESS", summary["point_in_time_snapshot_status"])
            self.assertEqual(687, len(store.read_prediction_snapshots()))
            self.assertIn("macro_sector_regime", summary)
            self.assertIn("counterparty_health", summary)
            self.assertIn("layer_ablation", summary["candidate_model_walk_forward"])
            before = result["signals"].copy(deep=True)
            again = finalize_analysis_run(result=result, config=config, tickers=tickers, store=store, pipeline=first)
            self.assertEqual(687, again["point_in_time_snapshot_count"])
            self.assertTrue(before.equals(result["signals"]))
            self.assertEqual(687, len(store.read_prediction_snapshots()))

            # Expire one asset and a benchmark, including the terminal bar.
            expired = datetime.now(timezone.utc) - timedelta(days=7)
            first.yahoo_ohlc_cache.upsert_success(tickers[0], first.yahoo_client.frame, fetched_at=expired)
            first.yahoo_ohlc_cache.upsert_success("SPY", first.yahoo_client.frame, fetched_at=expired)
            restarted = PipelineService(config)
            restarted.yahoo_client = OfflineYahoo(failures=[tickers[0], "SPY"])
            restarted_store = SQLiteStore(config.sqlite_path)
            retry = restarted.run(tickers, [], restarted_store, rss_enabled=False, mt5_enabled=False, yahoo_metadata_enabled=False)
            final = finalize_analysis_run(result=retry, config=config, tickers=tickers, store=restarted_store, pipeline=restarted)
            self.assertEqual(1374, len(restarted_store.read_prediction_snapshots()))
            self.assertEqual(set(tickers), set(retry["signals"]["ticker"]))
            self.assertIn("SPY", restarted.yahoo_client.calls)
            self.assertIn(tickers[0], restarted.yahoo_client.calls)
            self.assertNotEqual("SUCCESS", final["pipeline_status"])
            exported = json.loads((root / "weekly_shadow_latest.json").read_text())
            self.assertEqual(final["pipeline_status"], exported["pipeline_status"])
            self.assertFalse(exported["automated_trading"]["enabled"])

    def test_small_universe_refreshes_expired_benchmark_and_report_errors_are_partial(self):
        with tempfile.TemporaryDirectory() as directory, patch("socket.socket.connect", side_effect=AssertionError("Unexpected network")):
            root = Path(directory)
            config = AppConfig(output_dir=root, sqlite_path=root / "history.db", export_excel=False)
            pipeline = PipelineService(config)
            pipeline.yahoo_client = OfflineYahoo()
            pipeline.yahoo_ohlc_cache.upsert_success("SPY", _history(), fetched_at=datetime.now(timezone.utc) - timedelta(days=7))
            store = SQLiteStore(config.sqlite_path)
            result = pipeline.run(["AAPL"], [], store, rss_enabled=False, mt5_enabled=False)
            self.assertIn("SPY", pipeline.yahoo_client.calls)
            self.assertIsNotNone(result["point_in_time_inputs"][0]["feature_payload"]["market_factors"]["relative_returns"]["5d"])
            with patch.object(store, "save_macro_regime_report", side_effect=RuntimeError("fixture write error")):
                summary = finalize_analysis_run(result=result, config=config, tickers=["AAPL"], store=store, pipeline=pipeline)
            self.assertEqual("FAILED", summary["macro_sector_regime"]["status"])
            self.assertNotEqual("SUCCESS", summary["pipeline_status"])
            self.assertTrue(any("fixture write error" in warning for warning in summary["warnings"]))
            self.assertTrue(any("fixture write error" in warning for warning in summary["pipeline_degradations"]))


if __name__ == "__main__":
    unittest.main()
