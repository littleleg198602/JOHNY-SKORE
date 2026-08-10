from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest

import pandas as pd

from market_checker_app.config import AppConfig
from market_checker_app.services.pipeline_service import PipelineService
from market_checker_app.storage.yahoo_cache_store import YahooCacheStore


def _history() -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=90, freq="B", tz="UTC")
    close = pd.Series([100.0 + index * 0.1 for index in range(len(index))], index=index)
    return pd.DataFrame(
        {
            "Open": close - 0.3,
            "High": close + 0.8,
            "Low": close - 0.8,
            "Close": close,
            "Volume": 1_000_000,
        },
        index=index,
    )


class _BatchMT5:
    def __init__(self) -> None:
        self.calls = 0
        self.frame = _history()

    def fetch_ohlcv_batch(self, tickers, bars=300, progress_callback=None):
        self.calls += 1
        frames = {}
        for completed, ticker in enumerate(tickers, start=1):
            frames[ticker] = self.frame
            if progress_callback:
                progress_callback(completed, len(tickers), ticker)
        return frames, {}


class _NoLiveYahoo:
    def fetch_snapshots(self, ticker):
        raise AssertionError("687-ticker analysis must read Yahoo metadata from cache")

    def fetch_ohlc(self, ticker, period="1y", interval="1d"):
        raise AssertionError("687-ticker analysis must use MT5 batch OHLC")

    fetch_ohlc_only = fetch_ohlc


class FullUniverseContractTests(unittest.TestCase):
    def test_exactly_687_unique_tickers_complete_with_cached_yahoo(self) -> None:
        tickers = [f"T{index:04d}" for index in range(687)]
        metadata = {
            "_market_checker_yahoo_quality": "ok",
            "currentPrice": 100.0,
            "targetMeanPrice": 112.0,
            "targetMedianPrice": 111.0,
            "recommendationMean": 2.1,
            "numberOfAnalystOpinions": 15,
            "forwardPE": 20.0,
            "profitMargins": 0.18,
            "revenueGrowth": 0.09,
            "earningsGrowth": 0.11,
            "debtToEquity": 70.0,
        }

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "market.db"
            cache = YahooCacheStore(db_path)
            for ticker in tickers:
                cache.upsert_success(ticker, metadata)

            config = AppConfig(
                output_dir=Path(tmp),
                sqlite_path=db_path,
                save_history=False,
                export_excel=False,
                large_universe_threshold=100,
                max_tickers_per_run=1000,
            )
            pipeline = PipelineService(config)
            mt5 = _BatchMT5()
            pipeline.mt5_client = mt5
            pipeline.yahoo_client = _NoLiveYahoo()
            progress_samples: list[tuple[float, int, str]] = []

            started = time.monotonic()
            result = pipeline.run(
                tickers,
                [],
                None,
                rss_enabled=False,
                mt5_enabled=True,
                progress_callback=lambda state: progress_samples.append(
                    (state.overall_progress, state.processed_symbols, state.current_step)
                ),
            )
            elapsed = time.monotonic() - started

            signals = result["signals"]
            self.assertEqual(687, len(signals))
            self.assertEqual(687, signals["ticker"].nunique())
            self.assertEqual(set(tickers), set(signals["ticker"]))
            self.assertTrue((signals["yahoo_data_status"] == "cache_fresh").all())
            self.assertTrue((signals["yahoo_confidence"] > 0).all())
            self.assertEqual(687, cache.coverage(tickers).fresh)
            self.assertEqual(1, mt5.calls)
            self.assertEqual([], result["errors"])
            self.assertEqual((1.0, 687, "done"), progress_samples[-1])
            self.assertTrue(
                all(left[0] <= right[0] for left, right in zip(progress_samples, progress_samples[1:]))
            )
            self.assertLess(elapsed, 60.0)


if __name__ == "__main__":
    unittest.main()
