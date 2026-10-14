from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from market_checker_app.services.price_methodology import PRICE_METHOD_VERSION, YAHOO_ADJUSTMENT
from market_checker_app.services.yahoo_corporate_action_history import YahooCorporateActionHistoryClient
from market_checker_app.services.yahoo_price_provenance_service import (
    PROVIDER_ATTR, PROVENANCE_ATTR, capture_provider_frame, digest, frame_json,
    load_observation, persist_observation,
)
from market_checker_app.storage.yahoo_ohlc_cache_store import YahooOhlcCacheStore
from market_checker_app.services.prediction_label_service import PredictionLabelService
from market_checker_app.services.specialist_evaluation_service import build_specialist_evaluation_report, read_archived_prediction_snapshots
from market_checker_app.services.specialist_label_availability_service import load_label_observation, _audit_capture, _recorded_capture
from market_checker_app.services.us_equity_calendar import target_us_equity_window
from tests.test_batch4_evaluation_label_persistence import _histories, _store, LOGICAL_EVALUATION


NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
LOGICAL = datetime(2026, 1, 12, 22, tzinfo=timezone.utc)


def _frame(values=(100.0, 101.0), dates=("2026-09-10", "2026-09-11")):
    return pd.DataFrame({"Close": values, "Stock Splits": [0.0] * len(values), "Dividends": [0.0] * len(values)},
        index=pd.to_datetime(dates, utc=True))


def _annotate(ticker, frame, clock=NOW):
    frame = frame.copy(deep=True)
    frame.attrs[PROVIDER_ATTR] = capture_provider_frame(ticker, frame,
        started_at=clock - timedelta(seconds=1), observed_at=clock)
    return frame


def _cache(directory, clock=NOW):
    return YahooOhlcCacheStore(Path(directory) / "history.db", adjustment=YAHOO_ADJUSTMENT,
        methodology_version=PRICE_METHOD_VERSION, now_provider=lambda: LOGICAL,
        observation_clock=lambda: clock)


def _captures(directory):
    return sorted((Path(directory) / "yahoo_price_observations").glob("yahoo_price_*.json"))


class Batch5PriceProvenanceTests(unittest.TestCase):
    def test_client_records_physical_provider_return_and_canonical_symbol(self):
        clocks = iter((NOW - timedelta(seconds=2), NOW))
        client = YahooCorporateActionHistoryClient(retry_attempts=1, observation_clock=lambda: next(clocks))
        with patch("market_checker_app.services.yahoo_corporate_action_history.yf.Ticker") as ticker:
            ticker.return_value.history.return_value = _frame()
            returned, warning = client.fetch_one("BRKB")
        self.assertIsNone(warning)
        record = returned.attrs[PROVIDER_ATTR]
        self.assertEqual("BRKB", record["ticker"])
        self.assertEqual("BRK-B", record["provider_symbol"])
        self.assertEqual(NOW.isoformat(), record["observed_at"])
        self.assertEqual("UNKNOWN", record["publisher_revision_timing_status"])
        self.assertEqual(digest(frame_json(returned)), record["frame_sha256"])

    def test_cache_restores_content_verified_provenance_at_physical_not_logical_clock(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = _cache(temp)
            cache.upsert_success("AAPL", _annotate("AAPL", _frame()), fetched_at=LOGICAL)
            lookup = cache.get("AAPL", now=LOGICAL, required_sessions=(date(2026, 9, 10), date(2026, 9, 11)))
            provenance = lookup.frame.attrs[PROVENANCE_ATTR]
            self.assertEqual([100.0, 101.0], lookup.frame["Close"].tolist())
            self.assertEqual(NOW.isoformat(), provenance["provider_observed_at"])
            self.assertEqual(NOW.isoformat(), provenance["cache_read_at"])
            self.assertEqual(LOGICAL, lookup.fetched_at)
            self.assertEqual("UNKNOWN", provenance["revision_status"])
            self.assertEqual(2, provenance["corporate_action_input_sessions_recorded"])
            records = [load_observation(path) for path in _captures(temp)]
            self.assertEqual(2, len(records))
            cached = next(record for record in records if record["kind"] == "CACHE_MERGED_FRAME")
            self.assertEqual(provenance["price_observation_id"], cached["capture_id"])
            self.assertFalse(cached["logical_fetched_at_is_physical_observation"])
            self.assertEqual("RECORDED_PROVIDER_FRAME", cached["source_observation_status"])

    def test_observed_change_extension_and_repeat_do_not_infer_publisher_correction(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = _cache(temp)
            cache.upsert_success("AAPL", _frame())
            first = cache.get("AAPL").frame.attrs[PROVENANCE_ATTR]
            self.assertEqual("FIRST_CAPTURE", first["observed_content_revision_status"])
            cache.upsert_success("AAPL", _frame())
            self.assertEqual("UNCHANGED_RETRIEVAL", cache.get("AAPL").frame.attrs[PROVENANCE_ATTR]["observed_content_revision_status"])
            cache.upsert_success("AAPL", _frame((102.0,), ("2026-09-14",)))
            self.assertEqual("EXTENDED_ONLY", cache.get("AAPL").frame.attrs[PROVENANCE_ATTR]["observed_content_revision_status"])
            cache.upsert_success("AAPL", _frame((99.0, 101.0)))
            record_id = cache.get("AAPL").frame.attrs[PROVENANCE_ATTR]["price_observation_id"]
            record = load_observation(Path(temp) / "yahoo_price_observations" / f"yahoo_price_{record_id}.json")
            self.assertEqual("CONTENT_CHANGED", record["observed_content_revision"]["status"])
            self.assertEqual(1, record["observed_content_revision"]["changed_overlap_rows"])
            self.assertFalse(record["observed_content_revision"]["publisher_correction_inferred"])
            self.assertIsNone(record["publisher_revision_at"])
            self.assertEqual("UNKNOWN", record["publisher_revision_timing_status"])

    def test_split_and_dividend_inputs_survive_with_unchanged_price_method(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = _cache(temp)
            frame = _frame((400.0, 100.0))
            frame["Stock Splits"] = [0.0, 4.0]
            frame["Dividends"] = [1.0, 0.0]
            cache.upsert_success("AAPL", _annotate("AAPL", frame))
            lookup = cache.get("AAPL", required_sessions=(date(2026, 9, 10), date(2026, 9, 11)))
            self.assertEqual([100.0, 100.0], lookup.frame["Close"].tolist())
            inputs = lookup.frame.attrs[PROVENANCE_ATTR]["corporate_action_session_inputs"]
            self.assertEqual(4.0, inputs[1]["values"]["Stock Splits"])
            self.assertEqual(1.0, inputs[0]["values"]["Dividends"])
            self.assertFalse(lookup.frame.attrs[PROVENANCE_ATTR]["dividends_included"])

    def test_legacy_cache_without_capture_remains_usable_and_unproven(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = _cache(temp)
            cache.upsert_success("AAPL", _frame())
            with sqlite3.connect(cache.db_path) as conn:
                conn.execute("UPDATE yahoo_ohlc_cache_v2 SET capture_provenance_json='{}'")
            lookup = cache.get("AAPL")
            self.assertTrue(lookup.usable)
            self.assertNotIn(PROVENANCE_ATTR, lookup.frame.attrs)
            self.assertEqual("LEGACY_UNCAPTURED", lookup.frame.attrs["yahoo_price_provenance_gap"])

    def test_unannotated_legacy_pipeline_path_has_no_new_price_archive_io(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = YahooOhlcCacheStore(Path(temp) / "history.db",
                observation_clock=lambda: self.fail("Legacy raw cache must not add physical capture work"))
            frame = _frame()
            frame.attrs[PROVENANCE_ATTR] = {"revision_status": "CAPTURED_CURRENT_VERSION"}
            returned = cache.upsert_success("AAPL", frame)
            self.assertNotIn(PROVENANCE_ATTR, returned.attrs)
            self.assertNotIn(PROVENANCE_ATTR, cache.get("AAPL").frame.attrs)
            self.assertFalse((Path(temp) / "yahoo_price_observations").exists())
            self.assertEqual([100.0, 101.0], returned["Close"].tolist())

    def test_missing_capture_does_not_erase_old_sessions_on_short_refresh(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = _cache(temp)
            cache.upsert_success("AAPL", _frame())
            _captures(temp)[0].unlink()
            self.assertTrue(cache.get("AAPL").usable)
            cache.upsert_success("AAPL", _frame((102.0,), ("2026-09-14",)))
            self.assertEqual([100.0, 101.0, 102.0], cache.get("AAPL").frame["Close"].tolist())

    def test_missing_provider_body_is_not_a_source_verified_name_only_pointer(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = _cache(temp)
            cache.upsert_success("AAPL", _annotate("AAPL", _frame()))
            for path in _captures(temp):
                if load_observation(path)["kind"] == "PROVIDER_RETURNED_FRAME":
                    path.unlink()
            lookup = cache.get("AAPL")
            self.assertTrue(lookup.usable)
            self.assertNotIn(PROVENANCE_ATTR, lookup.frame.attrs)
            self.assertEqual("MISSING_OR_INVALID_CAPTURE", lookup.frame.attrs["yahoo_price_provenance_gap"])

    def test_changed_cache_bytes_without_matching_capture_are_corrupt_and_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = _cache(temp)
            cache.upsert_success("AAPL", _frame())
            with sqlite3.connect(cache.db_path) as conn:
                before = conn.execute("SELECT frame_json FROM yahoo_ohlc_cache_v2").fetchone()[0]
                changed = before.replace("100.0", "900.0")
                conn.execute("UPDATE yahoo_ohlc_cache_v2 SET frame_json=?", (changed,))
            self.assertEqual("corrupt", cache.get("AAPL").state)
            with self.assertRaises(ValueError):
                cache.upsert_success("AAPL", _frame((102.0,), ("2026-09-14",)))
            with sqlite3.connect(cache.db_path) as conn:
                self.assertEqual(changed, conn.execute("SELECT frame_json FROM yahoo_ohlc_cache_v2").fetchone()[0])

    def test_access_bearing_whole_frame_is_withheld_before_a_merge_or_export(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = _cache(temp)
            cache.upsert_success("AAPL", _frame())
            originals = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in _captures(temp)}
            frame = _frame()
            frame.attrs["api_key"] = "never-export-sentinel"
            with self.assertRaisesRegex(ValueError, "WITHHELD_PRICE_CAPTURE"):
                cache.upsert_success("AAPL", frame)
            self.assertEqual(originals, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in _captures(temp)})
            self.assertEqual([100.0, 101.0], cache.get("AAPL").frame["Close"].tolist())

    def test_client_withholds_access_bearing_return_without_filtered_export(self):
        frame = _frame()
        frame["private_payload"] = "Bearer never-export-sentinel"
        client = YahooCorporateActionHistoryClient(retry_attempts=1, observation_clock=lambda: NOW)
        with patch("market_checker_app.services.yahoo_corporate_action_history.yf.Ticker") as ticker:
            ticker.return_value.history.return_value = frame
            returned, warning = client.fetch_one("AAPL")
        self.assertIsNone(returned)
        self.assertNotIn("never-export-sentinel", warning)

    def test_mismatched_canonical_ticker_cannot_reuse_a_provider_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = _cache(temp)
            with self.assertRaises(ValueError):
                cache.upsert_success("MSFT", _annotate("AAPL", _frame()))
            self.assertEqual([], _captures(temp))

    def test_label_sidecars_preserve_adapter_hashes_and_exact_action_rows_but_stay_excluded(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            store = _store(directory)
            cache = _cache(directory)
            histories = _histories()
            dates = tuple(timestamp.date() for timestamp in histories["AAPL"].index)
            for ticker, frame in histories.items():
                frame["Stock Splits"] = 0.0
                frame["Dividends"] = 0.0
                cache.upsert_success(ticker, _annotate(ticker, frame), fetched_at=LOGICAL)
            resolution = PredictionLabelService(observation_clock=lambda: NOW + timedelta(hours=1)).resolve_pending_snapshots(
                store=store, price_loader=lambda ticker: cache.get(ticker, required_sessions=dates).frame, as_of=LOGICAL_EVALUATION)
            self.assertEqual(1, resolution["resolved"])
            self.assertAlmostEqual(0.05, store.read_prediction_snapshots().iloc[0]["target_value"])
            sidecar = load_label_observation(next((directory / "prediction_label_observations").glob("prediction_label_*.json")))
            for capture in sidecar["price_captures"].values():
                self.assertEqual(64, len(capture["price_observation_id"]))
                self.assertEqual(6, len(capture["recorded_action_session_inputs"]))
                self.assertTrue(capture["recorded_action_input_window_complete"])
                self.assertEqual("UNKNOWN", capture["publisher_revision_timing_status"])
                load_observation(directory / "yahoo_price_observations" / f"yahoo_price_{capture['price_observation_id']}.json")
            report = build_specialist_evaluation_report(read_archived_prediction_snapshots(store.db_path),
                as_of=NOW + timedelta(hours=1), label_archive_directory=directory / "prediction_label_observations")
            self.assertEqual(0, report["input"]["resolved_labels_known_at_cutoff"])
            self.assertEqual({"MISSING_PRICE_REVISION_PROVENANCE": 1}, report["label_availability"]["label_reason_counts"])

    def test_immutable_price_version_rejects_tampering_and_preserves_original_file(self):
        with tempfile.TemporaryDirectory() as temp:
            record = capture_provider_frame("AAPL", _frame(), started_at=NOW, observed_at=NOW)
            path = persist_observation(record, Path(temp))
            original = path.read_bytes()
            self.assertEqual(path, persist_observation(record, Path(temp)))
            self.assertEqual(original, path.read_bytes())
            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                persist_observation(record, Path(temp))
            self.assertEqual("{}", path.read_text(encoding="utf-8"))

    def test_future_cache_read_clock_cannot_hide_in_otherwise_observed_metadata(self):
        base, future = target_us_equity_window(datetime(2026, 1, 2, 22, tzinfo=timezone.utc), 5)
        sessions = [base, *future]
        metadata = {"provider": "yfinance", "price_basis": "split_adjusted_close_ex_dividend",
            "price_method_version": PRICE_METHOD_VERSION, "dividends_included": False,
            "revision_status": "UNKNOWN", "cache_read_at": (NOW + timedelta(seconds=1)).isoformat()}
        recorded = _recorded_capture(ticker="AAPL", sessions=sessions, prices=[100.0] * 6,
            observed_at=NOW, provenance=metadata)
        self.assertEqual("INVALID_CACHE_PROVENANCE_KNOWLEDGE", _audit_capture(recorded, ticker="AAPL",
            sessions=sessions, archive_observed_at=NOW)[2])

    def test_actual_saved_chart_null_close_stays_a_missing_session_not_meta_price(self):
        root = Path(__file__).resolve().parents[1]
        probe = json.loads((root / "evidence/batch5_prices_20261014/live_probe.json").read_text())
        self.assertEqual(2, probe["price_requests_attempted"])
        for capture in probe["captures"]:
            raw = (root / capture["raw_path"]).read_bytes()
            self.assertEqual(capture["response_sha256"], hashlib.sha256(raw).hexdigest())
            result = json.loads(raw)["chart"]["result"][0]
            self.assertEqual(capture["ticker"], result["meta"]["symbol"])
            self.assertEqual(22, len(result["timestamp"]))
            self.assertIsNone(result["indicators"]["quote"][0]["close"][-1])

    def test_failed_capture_publication_keeps_existing_cache_bytes_and_retry_state(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = _cache(temp)
            cache.upsert_success("AAPL", _frame())
            before = cache.db_path.read_bytes()
            with patch("market_checker_app.storage.yahoo_ohlc_cache_store.persist_observation", side_effect=OSError("disk unavailable")):
                with self.assertRaises(OSError):
                    cache.upsert_success("AAPL", _frame((102.0,), ("2026-09-14",)))
            self.assertEqual(before, cache.db_path.read_bytes())
            self.assertEqual([100.0, 101.0], cache.get("AAPL").frame["Close"].tolist())


if __name__ == "__main__":
    unittest.main()
