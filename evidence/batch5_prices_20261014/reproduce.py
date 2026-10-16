"""Offline replay of the two saved genuine chart responses; no HTTP or forecasts.

Uses the installed provider's primary parse functions. Observation clocks are
physical replay/capture clocks, not market dates in the earlier response.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd
import yfinance
from yfinance import utils as yahoo_utils

from market_checker_app.services.price_methodology import PRICE_METHOD_VERSION, YAHOO_ADJUSTMENT
from market_checker_app.services.prediction_label_service import PredictionLabelService
from market_checker_app.services.specialist_label_availability_service import _audit_capture, _recorded_capture
from market_checker_app.services.us_equity_calendar import target_us_equity_window
from market_checker_app.services.yahoo_price_provenance_service import (
    PROVIDER_ATTR, PROVENANCE_ATTR, capture_provider_frame, load_observation,
)
from market_checker_app.storage.yahoo_ohlc_cache_store import YahooOhlcCacheStore


OUT = ROOT / "evidence/batch5_prices_20261014"
probe = json.loads((OUT / "live_probe.json").read_text())
replayed = []
frames = {}
observations = OUT / "observations"
logical_clock = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
with tempfile.TemporaryDirectory() as temp:
    cache = YahooOhlcCacheStore(Path(temp) / "prices.db", adjustment=YAHOO_ADJUSTMENT,
        methodology_version=PRICE_METHOD_VERSION)
    cache.price_observation_directory = observations
    for captured in probe["captures"]:
        raw_path = ROOT / captured["raw_path"]
        raw = raw_path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == captured["response_sha256"]
        assert not re.search(rb"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|credential|bearer\s|cookie|crumb|[?&](token|key)=)", raw)
        result = json.loads(raw)["chart"]["result"][0]
        ticker = captured["ticker"]
        assert result["meta"]["symbol"] == ticker
        imported_at = datetime.now(timezone.utc)
        frame = yahoo_utils.parse_quotes(result)
        frame.index = pd.to_datetime(frame.index, utc=True)
        dividends, splits, capital_gains = yahoo_utils.parse_actions(result)
        for column, events in (("Dividends", dividends), ("Stock Splits", splits)):
            by_session = {} if events is None else {pd.Timestamp(index).date(): float(value)
                for index, value in events[column].items()}
            frame[column] = [by_session.get(timestamp.date(), 0.0) for timestamp in frame.index]
        frame.attrs["yahoo_synthesized_columns"] = ["Dividends", "Stock Splits"]
        # Zero-fill is a parsing input, never complete publisher action history.
        provider_record = capture_provider_frame(ticker, frame, started_at=imported_at,
            observed_at=datetime.now(timezone.utc), upstream_response_sha256=captured["response_sha256"],
            evidence_origin="CAPTURED_YAHOO_CHART_REPLAY")
        frame.attrs[PROVIDER_ATTR] = provider_record
        cache.upsert_success(ticker, frame, fetched_at=logical_clock)
        dates = tuple(timestamp.date() for timestamp in frame.index[-6:])
        lookup = cache.get(ticker, required_sessions=dates)
        frames[ticker] = lookup.frame
        provenance = lookup.frame.attrs[PROVENANCE_ATTR]
        cached = load_observation(observations / f"yahoo_price_{provenance['price_observation_id']}.json")
        source = load_observation(observations / f"yahoo_price_{provenance['source_capture_id']}.json")
        replayed.append({"ticker": ticker, "evidence_origin": "CURRENT_OFFLINE_IMPORT_OF_ACTUAL_CAPTURED_CHART",
            "raw_path": raw_path.relative_to(ROOT).as_posix(), "raw_sha256": captured["response_sha256"],
            "original_response_observed_at": captured["observed_at"],
            "actual_import_started_at": imported_at.isoformat(), "physical_cache_captured_at": cached["captured_at"],
            "logical_fetched_at": cached["logical_fetched_at"], "logical_clock_is_physical": False,
            "provider_rows": len(frame), "usable_cached_rows": len(lookup.frame),
            "recent_window_lookup_status": lookup.state, "recent_window_missing_sessions": [day.isoformat() for day in lookup.missing_sessions],
            "provider_observation": (observations / f"yahoo_price_{source['capture_id']}.json").relative_to(ROOT).as_posix(),
            "cache_observation": (observations / f"yahoo_price_{cached['capture_id']}.json").relative_to(ROOT).as_posix(),
            "provider_frame_sha256": source["frame_sha256"], "cache_frame_sha256": cached["frame_sha256"],
            "provider_observation_id": source["capture_id"], "cache_observation_id": cached["capture_id"],
            "observed_content_revision_status": cached["observed_content_revision"]["status"],
            "publisher_revision_timing_status": cached["publisher_revision_timing_status"],
            "requested_action_input_sessions": provenance["corporate_action_input_sessions_requested"],
            "recorded_action_input_sessions": provenance["corporate_action_input_sessions_recorded"],
            "strict_pit_eligible": False})
    event_window_as_of = datetime(2026, 10, 6, 20, 1, tzinfo=timezone.utc)
    actual_clock = datetime.now(timezone.utc)
    common = PredictionLabelService._common_price_windows(frames["AAPL"], frames["SPY"],
        snapshot_as_of=event_window_as_of, evaluation_as_of=actual_clock, horizon=5)
    assert common is None
    recent_window_gap = {"exact_window_ending_oct13_available": False,
        "reason": "MISSING_EXPECTED_OCT13_CLOSE_IN_BOTH_SOURCE_ARRAYS",
        "meta_regular_market_price_substituted": False, "horizon_shifted": False}
    # Choose a separately declared, earlier complete diagnostic window. This
    # never converts the unavailable latest target into another economic horizon.
    earlier_window_as_of = datetime(2026, 10, 5, 20, 1, tzinfo=timezone.utc)
    base, future = target_us_equity_window(earlier_window_as_of, 5)
    sessions = [base, *future]
    dates = tuple(session.session_date for session in sessions)
    frames = {ticker: cache.get(ticker, required_sessions=dates).frame for ticker in frames}
    actual_clock = datetime.now(timezone.utc)
    common = PredictionLabelService._common_price_windows(frames["AAPL"], frames["SPY"],
        snapshot_as_of=earlier_window_as_of, evaluation_as_of=actual_clock, horizon=5)
    assert common is not None
    asset, benchmark, endpoint = common
    gaps = {}
    for role, ticker, prices in (("asset", "AAPL", asset), ("benchmark", "SPY", benchmark)):
        recorded = _recorded_capture(ticker=ticker, sessions=sessions, prices=prices,
            observed_at=actual_clock, provenance=frames[ticker].attrs[PROVENANCE_ATTR])
        _, _, issue = _audit_capture(recorded, ticker=ticker, sessions=sessions, archive_observed_at=actual_clock)
        assert issue == "MISSING_PRICE_REVISION_PROVENANCE"
        gaps[role] = {"ticker": ticker, "issue": issue, "complete_recorded_action_input_window": recorded["recorded_action_input_window_complete"],
            "consumed_prices": prices, "consumed_session_count": len(prices), "price_observation_id": recorded["price_observation_id"]}
    with sqlite3.connect(cache.db_path) as connection:
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='prediction_snapshots'").fetchone() is None
    price_window_audit = {"scope": "RETROSPECTIVE_PRICE_INPUT_WINDOW_ONLY_NO_PREDICTION_CREATED",
        "separate_recent_window_gap": recent_window_gap,
        "session_dates": [session.session_date.isoformat() for session in sessions],
        "endpoint_close_is_event_time": endpoint.isoformat(), "observed_price_input_at": actual_clock.isoformat(),
        "actual_historical_prediction_exists": False, "saved_label_count": 0, "out_of_sample_sample_count": 0,
        "roles": gaps}

modules = ["tests.test_batch5_price_provenance", "tests.test_yahoo_ohlc_cache_store",
    "tests.test_yahoo_corporate_action_history", "tests.test_price_methodology",
    "tests.test_batch4_evaluation_label_availability", "tests.test_batch4_evaluation_label_persistence",
    "tests.test_prediction_label_service", "tests.test_prediction_label_runner", "tests.test_prediction_label_queue_fairness"]
suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
discovered = suite.countTestCases()
started = time.perf_counter()
log = OUT / "tests.log"
with log.open("w", encoding="utf-8") as handle:
    tested = unittest.TextTestRunner(stream=handle, verbosity=2).run(suite)
elapsed = time.perf_counter() - started
owned = ["market_checker_app/services/yahoo_price_provenance_service.py",
    "market_checker_app/services/yahoo_corporate_action_history.py", "market_checker_app/storage/yahoo_ohlc_cache_store.py",
    "market_checker_app/services/specialist_label_availability_service.py", "tests/test_batch5_price_provenance.py",
    "evidence/batch5_prices_20261014/reproduce.py"]
primary = Path(inspect.getfile(yahoo_utils)).resolve()
artifacts = [OUT / "live_probe.json", *sorted((OUT / "raw").glob("*.json")), *sorted(observations.glob("*.json")), log]
evidence = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
    "base_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    "source": {"actual_captured_chart_responses": 2, "fresh_price_requests_in_capture": probe["price_requests_attempted"],
        "fresh_requests_in_replay": 0, "raw_bytes_preserved": True, "new_backdated_predictions": 0},
    "actual_capture_replay": replayed, "retrospective_price_window_audit": price_window_audit,
    "tests": {"modules": modules, "discovered": discovered, "executed": tested.testsRun,
        "failures": len(tested.failures), "errors": len(tested.errors), "skips": len(tested.skipped),
        "expected_failures": len(tested.expectedFailures), "unexpected_successes": len(tested.unexpectedSuccesses),
        "successful": tested.wasSuccessful(), "elapsed_seconds": round(elapsed, 6), "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest()},
    "primary_local_library_source": {"distribution": "yfinance", "version": yfinance.__version__,
        "source_file": "yfinance/utils.py", "source_file_sha256": hashlib.sha256(primary.read_bytes()).hexdigest(),
        "functions_used": ["parse_quotes", "parse_actions"], "publisher_revision_timestamp_exposed_by_these_functions": False},
    "owned_file_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in owned},
    "artifact_sha256": {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in artifacts},
    "limitations": [
        "These are two current response captures and a current offline import. Market event timestamps and October 1 TTL metadata never backdate their availability.",
        "Both 22-row source envelopes have a null October 13 Close. Only 21 closes are usable; the latest exact horizon remains missing, independently of the earlier six-session diagnostic window.",
        "AAPL/SPY pilot and earlier six-session retrospective input window are not universe coverage, a historical prediction, a completed OOS sample or specialist acceptance.",
        "Yahoo does not supply independently dated publisher correction knowledge in these quote/action arrays. Publisher revision timing remains UNKNOWN and strict PIT excludes both roles.",
        "Observed content changes and price extensions are local observations, never an inferred publisher correction. Parsed zero action inputs are not complete corporate-action history.",
        "The legacy unannotated raw pipeline keeps its previous write path and UNKNOWN provenance; new immutable I/O is limited to the strict action cache or genuinely annotated provider frames.",
        "Actual user Windows end-to-end, true historical source versions and other shadow/candidate evaluator provenance gates remain separate work.",
    ], "claims": {"target_formula_changed": False, "score_changed": False, "timing_budget_changed": False,
        "canonical_universe_changed": False, "out_of_sample_acceptance_verified": False, "specialists_accepted": 0}}
(OUT / "handoff.json").write_text(json.dumps(evidence, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
print(json.dumps({"source": evidence["source"], "tests": evidence["tests"], "gaps": gaps}, sort_keys=True))
raise SystemExit(0 if tested.wasSuccessful() else 1)
