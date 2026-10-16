"""Synthetic offline daily inputs only; no forecast, DB or live/OOS acceptance."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from market_checker_app.prediction_contract import canonical_hash
from market_checker_app.services import captured_market_data_provider as module
from market_checker_app.services.captured_market_data_provider import (
    BENCHMARK_TICKERS, MANIFEST_SCHEMA_VERSION, PROVENANCE_ATTR,
    CapturedMarketDataProvider, build_captured_market_data_entry,
)
from market_checker_app.services.us_equity_calendar_service import last_completed_session, sessions_between
from market_checker_app.services.yahoo_price_provenance_service import capture_provider_frame, persist_observation


NOW = datetime(2026, 10, 15, 22, tzinfo=timezone.utc)


def fixture_frame(rows=66, *, as_of=NOW, actions=False):
    latest = last_completed_session(as_of)
    sessions = sessions_between(latest - pd.Timedelta(days=600), latest)[-rows:]
    close = [100.0 + index / 100 for index in range(rows)]
    frame = pd.DataFrame({"Open": [value-.2 for value in close], "High": [value+.3 for value in close],
        "Low": [value-.4 for value in close], "Close": close,
        "Volume": [1_000_000+index for index in range(rows)]}, index=pd.DatetimeIndex(sessions))
    if actions:
        frame["Stock Splits"], frame["Dividends"] = 0.0, 0.0
    return frame


def capture(root, ticker="AAPL", *, frame=None, observed=None, mutate=None):
    frame = fixture_frame() if frame is None else frame
    observed = observed or NOW-timedelta(minutes=1)
    record = capture_provider_frame(ticker, frame, started_at=observed-timedelta(seconds=1), observed_at=observed)
    # Mark all positive unit artifacts explicitly; they never populate real data.
    record["evidence_origin"] = "DETERMINISTIC_TEST_FIXTURE"
    if mutate:
        mutate(record)
    record["capture_id"] = canonical_hash({key:value for key,value in record.items() if key != "capture_id"})
    return persist_observation(record, Path(root)/"selected"), record


def write_manifest(root, entries, *, name="market_manifest.json"):
    path = Path(root)/name
    path.write_text(json.dumps({"schema_version":MANIFEST_SCHEMA_VERSION,"instruments":entries}, indent=2)+"\n", encoding="utf-8")
    return path


class CapturedMarketDataProviderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.path, self.record = capture(self.root)
        self.entry = build_captured_market_data_entry(archive_root=self.root,
            source_archive_path=self.path.relative_to(self.root).as_posix())
        self.manifest_path = write_manifest(self.root,[self.entry])
        self.addCleanup(patch.stopall)
        patch("socket.socket.connect",side_effect=AssertionError("No provider HTTP")).start()
        patch("sqlite3.connect",side_effect=AssertionError("No provider DB")).start()

    def provider(self, *, entries=None, clock=NOW):
        if entries is not None:
            write_manifest(self.root,entries)
        return CapturedMarketDataProvider(manifest_path=self.manifest_path,archive_root=self.root,
            observation_clock=lambda:clock)

    def check_missing(self, expected, *, provider=None):
        provider = provider or self.provider()
        frame, warning = provider.fetch_ohlc_only("AAPL")
        self.assertIsNone(frame)
        self.assertIn(expected,warning)
        report=provider.readiness(["AAPL"],include_benchmarks=False)
        self.assertEqual(("WAIT_DATA",0),(report["status"],report["usable_count"]))
        return report

    def replace(self, frame=None, *, observed=None, mutate=None, actions=False):
        self.path,self.record=capture(self.root,frame=frame,observed=observed,mutate=mutate)
        self.entry=build_captured_market_data_entry(archive_root=self.root,
            source_archive_path=self.path.relative_to(self.root).as_posix(),require_actions=actions)
        write_manifest(self.root,[self.entry])

    def test_complete_original_66_session_frame_is_returned_without_value_or_clock_reconstruction(self):
        provider=self.provider()
        with patch.object(Path,"glob",side_effect=AssertionError("No discovery")), \
             patch.object(Path,"rglob",side_effect=AssertionError("No discovery")):
            frame,warning=provider.fetch_ohlc_only("AAPL")
        self.assertIsNone(warning)
        pd.testing.assert_frame_equal(frame,fixture_frame(),check_freq=False)
        proof=frame.attrs[PROVENANCE_ATTR]
        self.assertEqual(self.entry["source"],proof["source"])
        self.assertEqual(self.record["observed_at"],proof["source"]["observed_at"])
        self.assertEqual("UNKNOWN",proof["publisher_revision_timing_status"])
        self.assertEqual("DETERMINISTIC_TEST_FIXTURE",proof["evidence_origin"])
        self.assertTrue(proof["fixture_only"])
        self.assertFalse(proof["strict_pit_eligible"])
        self.assertFalse(proof["source_clocks_rebased"])
        self.assertNotIn("point_in_time_price_provenance",frame.attrs)

    def test_marked_synthetic_capture_is_not_a_production_default_clock_input(self):
        with patch.object(module,"physical_clock",return_value=NOW):
            production=CapturedMarketDataProvider(manifest_path=self.manifest_path,archive_root=self.root)
        report=self.check_missing("SYNTHETIC_CAPTURE_NOT_PRODUCTION_INPUT",provider=production)
        self.assertFalse(report["fixture_only"])
        self.assertIsNone(production.provenance_for("AAPL")["provenance"])
        explicit=self.provider().readiness(["AAPL"],include_benchmarks=False)
        self.assertEqual("READY",explicit["status"])
        self.assertTrue(explicit["fixture_only"])

    def test_all_twelve_benchmark_reads_fail_locally_without_fallback(self):
        provider=self.provider()
        report=provider.readiness(["AAPL"])
        self.assertEqual(("WAIT_DATA",13,1,12),(report["status"],report["requested_count"],report["usable_count"],report["missing_count"]))
        progress=[]
        frames,warnings=provider.fetch_ohlc_batch(list(BENCHMARK_TICKERS),retry_missing_symbols=True,
            missing_symbol_retry_limit=999,progress_callback=lambda *args:progress.append(args))
        self.assertEqual({},frames)
        self.assertEqual(set(BENCHMARK_TICKERS),set(warnings))
        self.assertEqual(12,len(progress))
        self.assertTrue(all("MISSING_EXPLICIT_MARKET_INSTRUMENT" in value for value in warnings.values()))

    def test_complete_asset_and_twelve_benchmark_selection_can_pass_readiness_as_fixtures_only(self):
        entries=[self.entry]
        for ticker in BENCHMARK_TICKERS:
            path,_=capture(self.root,ticker)
            entries.append(build_captured_market_data_entry(archive_root=self.root,
                source_archive_path=path.relative_to(self.root).as_posix()))
        provider=self.provider(entries=entries)
        first=provider.readiness(["AAPL"])
        self.assertEqual(("READY",13),(first["status"],first["usable_count"]))
        final=provider.revalidate_for_persistence(["AAPL"],expected_readiness=first)
        self.assertEqual(("READY","PRE_PERSISTENCE"),(final["status"],final["phase"]))
        self.assertEqual([row["source"] for row in first["rows"]],[row["source"] for row in final["rows"]])
        self.assertFalse(final["prospective_audit_implicitly_enabled"])
        self.assertFalse(final["strict_pit_eligible"])

    def test_under_66_rows_are_not_padded(self):
        self.replace(fixture_frame(65))
        self.check_missing("INSUFFICIENT_CONTIGUOUS_66_SESSION_HISTORY")

    def test_older_latest_close_is_not_refreshed_by_importing_today(self):
        self.replace(fixture_frame(as_of=NOW-timedelta(days=1)))
        report=self.check_missing("MISSING_LATEST_COMPLETED_SESSION_CLOSE")
        self.assertEqual(self.record["captured_at"],report["rows"][0]["source"]["captured_at"])
        self.assertEqual("2026-10-15",report["latest_completed_session"])

    def test_missing_internal_session_is_not_shifted_to_another_row(self):
        frame=fixture_frame(67).drop(fixture_frame(67).index[-20])
        self.replace(frame)
        self.check_missing("MISSING_OR_SHIFTED_CAPTURED_SESSIONS")

    def test_duplicate_sessions_and_intraday_shifted_daily_labels_are_refused(self):
        for variant in ("duplicate","shifted","same_day"):
            with self.subTest(variant=variant):
                frame=fixture_frame()
                index=list(frame.index)
                if variant=="duplicate":index[-2]=index[-3]
                elif variant=="same_day":index[-2]=index[-3]+pd.Timedelta(hours=1)
                else:index=[stamp+pd.Timedelta(hours=1) for stamp in index]
                frame.index=pd.DatetimeIndex(index)
                self.replace(frame)
                self.check_missing("SESSIONS" if variant!="shifted" else "SHIFTED_DAILY_SESSION_TIMESTAMP")

    def test_whole_nyse_open_and_new_york_midnight_encodings_are_accepted_without_shifting_dates(self):
        original=fixture_frame()
        schedule=module._nyse_calendar().schedule(start_date=original.index[0].date(),end_date=original.index[-1].date())
        for index in (pd.DatetimeIndex(schedule.market_open),original.index.tz_localize(None).tz_localize("America/New_York")):
            with self.subTest(first=str(index[0])):
                frame=original.copy();frame.index=index
                self.replace(frame)
                result,warning=self.provider().fetch_ohlc_only("AAPL")
                self.assertIsNone(warning)
                self.assertEqual(list(index.tz_convert("UTC")),list(result.index))
                self.assertEqual(list(original["Close"]),list(result["Close"]))

    def test_future_unfinished_session_is_not_silently_dropped_into_a_different_archive(self):
        before_close=datetime(2026,10,15,19,tzinfo=timezone.utc)
        self.replace(fixture_frame(),observed=NOW-timedelta(minutes=1))
        self.check_missing("FUTURE_OR_INVALID_CAPTURED_KNOWLEDGE_CLOCK",provider=self.provider(clock=before_close))

    def test_current_unfinished_close_cannot_have_been_observed_before_it_completed(self):
        self.replace(fixture_frame(),observed=datetime(2026,10,15,19,tzinfo=timezone.utc))
        self.check_missing("CAPTURED_SESSION_NOT_YET_COMPLETE_WHEN_OBSERVED")

    def test_a_read_crossing_the_next_session_close_rechecks_freshness_at_completion(self):
        clock=datetime(2026,10,15,19,59,tzinfo=timezone.utc)
        self.replace(fixture_frame(as_of=clock),observed=clock-timedelta(minutes=1))
        times=iter([clock,clock,clock,datetime(2026,10,15,20,tzinfo=timezone.utc)])
        provider=CapturedMarketDataProvider(manifest_path=self.manifest_path,archive_root=self.root,
            observation_clock=lambda:next(times))
        report=provider.readiness(["AAPL"],include_benchmarks=False)
        self.assertEqual(("WAIT_DATA",0,"2026-10-15"),(report["status"],report["usable_count"],report["latest_completed_session"]))

    def test_physical_clock_rollback_cannot_publish_ready_frames(self):
        times=iter([NOW,NOW,NOW+timedelta(seconds=1),NOW])
        provider=CapturedMarketDataProvider(manifest_path=self.manifest_path,archive_root=self.root,
            observation_clock=lambda:next(times))
        report=provider.readiness(["AAPL"],include_benchmarks=False)
        self.assertEqual("CAPTURED_MARKET_PHYSICAL_CLOCK_ROLLBACK",report["reason"])
        self.assertEqual(("WAIT_DATA",0),(report["status"],report["usable_count"]))

    def test_naive_physical_clock_is_not_reinterpreted_as_utc(self):
        self.check_missing("INVALID_HISTORY_KNOWLEDGE_CLOCK",provider=self.provider(clock=NOW.replace(tzinfo=None)))

    def test_all_original_ohlcv_columns_are_required_and_never_invented_from_close(self):
        for missing in ("Open","High","Low","Volume"):
            with self.subTest(missing=missing):
                self.replace(fixture_frame().drop(columns=[missing]))
                self.check_missing("MISSING_OR_UNSUPPORTED_ORIGINAL_OHLCV")

    def test_nonfinite_missing_negative_and_inconsistent_values_fail_closed(self):
        for column,value in (("Close",float("nan")),("High",float("inf")),("Close",0.0),("Volume",-1),("High",1.0)):
            with self.subTest(column=column,value=value):
                frame=fixture_frame();frame.loc[frame.index[-1],column]=value
                self.replace(frame)
                self.check_missing("MARKET_VALUE" if not pd.notna(value) or value==float("inf") else "INVALID_OR_INCONSISTENT_ORIGINAL_OHLCV")

    def test_actions_are_only_accepted_when_original_and_explicit_requirements_are_met(self):
        self.replace(fixture_frame(),actions=True)
        self.check_missing("MISSING_ORIGINAL_CORPORATE_ACTION_INPUTS")
        self.replace(fixture_frame(actions=True),actions=True)
        frame,warning=self.provider().fetch_ohlc_only("AAPL")
        self.assertIsNone(warning)
        self.assertEqual([0.0]*66,list(frame["Stock Splits"]))
        self.replace(fixture_frame(actions=True),mutate=lambda record:record.update(synthesized_columns=["Dividends"]))
        self.check_missing("SYNTHESIZED_SOURCE_COLUMNS_REFUSED")

    def test_adjusted_cache_and_unknown_method_are_never_reconstructed_to_raw(self):
        for mutate in (lambda record:record.update(kind="CACHE_MERGED_FRAME"),
                       lambda record:record.update(methodology="split_only"),
                       lambda record:record.update(auto_adjust_requested=True)):
            with self.subTest(mutate=mutate):
                self.replace(mutate=mutate)
                self.check_missing("UNSUPPORTED_CAPTURE_METHOD")

    def test_whole_manifest_and_file_pins_detect_tamper_without_overwriting_the_input(self):
        provider=self.provider();before=self.path.read_bytes()
        changed=before.replace(b"100.0",b"109.0",1)
        self.assertNotEqual(before,changed)
        self.assertEqual(len(before),len(changed))
        self.path.write_bytes(changed)
        self.check_missing("CAPTURED_MARKET_FILE_PIN_MISMATCH",provider=provider)
        self.path.write_bytes(before+b" \n")
        self.check_missing("CAPTURED_MARKET_FILE_PIN_MISMATCH",provider=provider)
        self.assertEqual(before+b" \n",self.path.read_bytes())

    def test_manifest_switch_mid_run_cannot_select_a_new_valid_capture(self):
        provider=self.provider();first=provider.readiness(["AAPL"],include_benchmarks=False)
        self.replace(fixture_frame(67))
        self.check_missing("CAPTURED_MARKET_MANIFEST_CHANGED",provider=provider)
        self.assertIsNone(provider.provenance_for("AAPL")["provenance"])
        self.assertEqual(first["manifest_sha256"],provider.selection_provenance()["manifest_sha256"])

    def test_pre_persistence_comparison_refuses_a_different_valid_provider_selection(self):
        entries=[self.entry]
        for ticker in BENCHMARK_TICKERS:
            path,_=capture(self.root,ticker)
            entries.append(build_captured_market_data_entry(archive_root=self.root,source_archive_path=path.relative_to(self.root).as_posix()))
        first=self.provider(entries=entries).readiness(["AAPL"])
        frame=fixture_frame();frame["Close"]+=.01
        self.replace(frame)
        entries[0]=self.entry
        second=self.provider(entries=entries)
        final=second.revalidate_for_persistence(["AAPL"],expected_readiness=first)
        self.assertEqual(("WAIT_DATA",0),(final["status"],final["usable_count"]))
        self.assertEqual("CAPTURED_INPUT_BINDING_CHANGED_BEFORE_PERSISTENCE",final["reason"])
        self.assertTrue(all(row["status"]=="WAIT_DATA" for row in final["rows"]))

    def test_foreign_or_ambiguous_instrument_and_duplicate_selected_versions_are_refused(self):
        for mutate in (lambda row:row.update(ticker="NOT_REAL"),lambda row:row.update(provider_symbol="MSFT")):
            row=deepcopy(self.entry);mutate(row)
            self.check_missing("FOREIGN_OR_AMBIGUOUS_MARKET_INSTRUMENT",provider=self.provider(entries=[row]))
        self.check_missing("AMBIGUOUS_CAPTURED_MARKET_INSTRUMENT",provider=self.provider(entries=[self.entry,self.entry]))

    def test_resealed_foreign_source_cannot_be_used_for_the_selected_instrument(self):
        path,record=capture(self.root,"MSFT")
        entry=build_captured_market_data_entry(archive_root=self.root,source_archive_path=path.relative_to(self.root).as_posix())
        entry.update(ticker="AAPL",provider_symbol="AAPL")
        self.check_missing("FOREIGN_OR_AMBIGUOUS_MARKET_INSTRUMENT",provider=self.provider(entries=[entry]))

    def test_source_pin_clocks_cannot_replace_original_capture_availability(self):
        row=deepcopy(self.entry);row["source"]["available_at"]=NOW.isoformat()
        self.check_missing("CAPTURED_MARKET_AVAILABILITY_PIN_MISMATCH",provider=self.provider(entries=[row]))
        row=deepcopy(self.entry);row["source"]["observed_at"]=NOW.isoformat()
        self.check_missing("CAPTURED_MARKET_SOURCE_PIN_MISMATCH",provider=self.provider(entries=[row]))

    def test_future_capture_clock_stays_missing_and_unknown_revision_is_never_upgraded(self):
        self.replace(observed=NOW+timedelta(seconds=1))
        self.check_missing("FUTURE_OR_INVALID_CAPTURED_KNOWLEDGE_CLOCK")
        self.replace()
        proof=self.provider().provenance_for("AAPL")
        self.assertEqual("UNKNOWN",proof["provenance"]["publisher_revision_timing_status"])
        self.assertIsNone(proof["provenance"]["publisher_revision_at"])
        self.assertFalse(proof["provenance"]["historical_acceptance_verified"])

    def test_explicit_paths_refuse_escape_missing_and_parent_symlink_without_discovery(self):
        original=Path.is_symlink
        for path in ("../"+self.path.name,str(self.path),"selected/*.json","selected/../"+self.path.name,"missing/"+self.path.name):
            row=deepcopy(self.entry);row["source"]["path"]=path
            with self.subTest(path=path):
                frame,warning=self.provider(entries=[row]).fetch_ohlc_only("AAPL")
                self.assertIsNone(frame);self.assertTrue(warning.startswith("WAIT_DATA:"))
        write_manifest(self.root,[self.entry])
        guard_hits=[]
        def symlink(path):
            if path==self.path.parent:
                guard_hits.append(path)
                return True
            return original(path)
        with patch.object(Path,"is_symlink",symlink):
            self.check_missing("HISTORY_SYMLINK_REFUSED")
        self.assertTrue(guard_hits,"The canonical source parent must reach the symlink guard")

    def test_temporary_root_alias_is_canonical_before_capture_and_parent_guard_is_exercised(self):
        from types import SimpleNamespace
        native_directory=tempfile.TemporaryDirectory
        native_capture=capture
        roots=[]
        aliases=[]
        def aliased_directory(*args,**kwargs):
            temporary=native_directory(*args,**kwargs)
            physical=Path(temporary.name)
            alias=physical/'..'/physical.name
            aliases.append((physical,alias))
            return SimpleNamespace(name=str(alias),cleanup=temporary.cleanup)
        def canonical_capture(root,*args,**kwargs):
            roots.append(root)
            self.assertEqual(root.resolve(),root)
            self.assertNotIn('..',root.parts)
            return native_capture(root,*args,**kwargs)
        case=CapturedMarketDataProviderTests(
            'test_explicit_paths_refuse_escape_missing_and_parent_symlink_without_discovery')
        result=unittest.TestResult()
        with patch.object(tempfile,'TemporaryDirectory',side_effect=aliased_directory), \
             patch(__name__+'.capture',side_effect=canonical_capture):
            case.run(result)
        self.assertEqual(1,len(aliases))
        physical,alias=aliases[0]
        self.assertNotEqual(physical,alias)
        self.assertEqual(physical.resolve(),alias.resolve())
        self.assertEqual([physical.resolve()],roots)
        self.assertEqual(1,result.testsRun)
        self.assertFalse(result.skipped)
        self.assertTrue(result.wasSuccessful(),str(result.failures)+str(result.errors))

    def test_unsafe_whole_bodies_are_withheld_without_projection_or_error_echo(self):
        sentinel="synthetic-fixture-private-value"
        self.path.write_text('{"access_token":"'+sentinel+'"}')
        row=deepcopy(self.entry);row["source"].update(file_bytes=self.path.stat().st_size,file_sha256=hashlib.sha256(self.path.read_bytes()).hexdigest())
        report=self.check_missing("WITHHELD_HISTORY_ACCESS_VALUE",provider=self.provider(entries=[row]))
        self.assertNotIn(sentinel,json.dumps(report))
        manifest=json.loads(self.manifest_path.read_text());manifest["password"]=sentinel
        self.manifest_path.write_text(json.dumps(manifest))
        self.check_missing("WITHHELD_HISTORY_ACCESS_VALUE")

    def test_duplicate_outer_and_embedded_json_keys_are_not_silently_projected(self):
        raw=self.path.read_text();self.path.write_text(raw.replace('"kind":','"kind":"PROVIDER_RETURNED_FRAME","kind":',1))
        row=deepcopy(self.entry);row["source"].update(file_bytes=self.path.stat().st_size,file_sha256=hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.check_missing("DUPLICATE_HISTORY_JSON_KEY",provider=self.provider(entries=[row]))
        def duplicate_frame(record):
            record["frame_json"]=record["frame_json"].replace('"columns":','"columns":[],"columns":',1)
            record["frame_sha256"]=hashlib.sha256(record["frame_json"].encode()).hexdigest()
        path,record=capture(self.root,mutate=duplicate_frame)
        row=deepcopy(self.entry)
        row["source"].update(path=path.relative_to(self.root).as_posix(),file_bytes=path.stat().st_size,
            file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),capture_id=record["capture_id"],frame_sha256=record["frame_sha256"])
        self.check_missing("DUPLICATE_CAPTURED_FRAME_JSON_KEY",provider=self.provider(entries=[row]))

    def test_unselected_unsafe_file_is_not_discovered_or_opened(self):
        unselected=self.root/("yahoo_price_"+"f"*64+".json")
        unselected.write_text('{"access_token":"synthetic-fixture-only"}')
        original=Path.open
        def opened(path,*args,**kwargs):
            if path==unselected:
                raise AssertionError("Unselected file must stay unread")
            return original(path,*args,**kwargs)
        with patch.object(Path,"open",opened), \
             patch.object(Path,"glob",side_effect=AssertionError("No glob")), \
             patch.object(Path,"rglob",side_effect=AssertionError("No glob")):
            report=self.provider().readiness(["AAPL"],include_benchmarks=False)
        self.assertEqual("READY",report["status"])

    def test_malformed_second_archive_clock_is_row_local_and_does_not_erase_first_usable_input(self):
        path,record=capture(self.root,"SPY")
        row=build_captured_market_data_entry(archive_root=self.root,source_archive_path=path.relative_to(self.root).as_posix())
        record.pop("observed_at")
        record["capture_id"]=canonical_hash({key:value for key,value in record.items() if key!="capture_id"})
        malformed=path.parent/("yahoo_price_"+record["capture_id"]+".json")
        malformed.write_text(json.dumps(record),encoding="utf-8")
        row["source"].update(path=malformed.relative_to(self.root).as_posix(),capture_id=record["capture_id"],
            file_bytes=malformed.stat().st_size,file_sha256=hashlib.sha256(malformed.read_bytes()).hexdigest())
        provider=self.provider(entries=[self.entry,row])
        report=provider.readiness(["AAPL","SPY"],include_benchmarks=False)
        self.assertEqual(("WAIT_DATA",1,1),(report["status"],report["usable_count"],report["missing_count"]))
        self.assertEqual("INVALID_CAPTURED_MARKET_DATA",report["rows"][1]["reason"])
        frames,warnings=provider.fetch_ohlc_batch(["AAPL","SPY"])
        self.assertEqual({"AAPL"},set(frames))
        self.assertEqual({"SPY"},set(warnings))

    def test_exact_frame_and_session_pins_cannot_be_replaced_by_declared_metadata(self):
        for field,value in (("frame_sha256","f"*64),("row_count",65),
                            ("first_session","2000-01-03"),("last_session","2026-10-14")):
            with self.subTest(field=field):
                row=deepcopy(self.entry);row["source"][field]=value
                self.check_missing("SOURCE_PIN_MISMATCH" if field=="frame_sha256" else "SESSION_PIN_MISMATCH",
                    provider=self.provider(entries=[row]))

    def test_byte_row_and_request_limits_stop_before_any_unselected_source_read(self):
        with patch.object(module,"MAX_SELECTED_BYTES",1):
            provider=self.provider()
        self.check_missing("CAPTURED_MARKET_TOTAL_BYTE_LIMIT",provider=provider)
        provider=self.provider()
        report=provider.readiness(["AAPL"]*(module.MAX_INSTRUMENTS+1),include_benchmarks=False)
        self.assertEqual("CAPTURED_REQUEST_COUNT_LIMIT_OR_EMPTY",report["reason"])
        self.assertEqual(0,report["usable_count"])
        row=deepcopy(self.entry);row["source"]["row_count"]=module.MAX_FRAME_ROWS+1
        self.check_missing("CAPTURED_MARKET_SOURCE_BYTE_OR_ROW_LIMIT",provider=self.provider(entries=[row]))

    def test_unsupported_interval_or_period_and_empty_selection_do_not_enable_download(self):
        provider=self.provider()
        for period,interval in (("5y","1d"),("1y","1h")):
            frame,warning=provider.fetch_ohlc_only("AAPL",period,interval)
            self.assertIsNone(frame);self.assertIn("UNSUPPORTED_CAPTURED_MARKET_REQUEST",warning)
        self.assertEqual("WAIT_DATA",provider.readiness([])["status"])

    def test_metadata_is_explicitly_missing_and_performance_uses_only_the_existing_formula(self):
        provider=self.provider();metadata,performance,warning=provider.fetch_snapshots("AAPL")
        self.assertEqual(("AAPL",{},"missing"),(metadata.ticker,metadata.data,metadata.status))
        self.assertEqual("WAIT_DATA:CAPTURED_METADATA_NOT_SUPPLIED",warning)
        frame=fixture_frame()
        self.assertEqual((frame.Close.iloc[-1]/frame.Close.iloc[-8]-1)*100,performance.last_week_change_pct)
        self.assertEqual((frame.Close.iloc[-1]/frame.Close.iloc[-64]-1)*100,performance.last_3m_change_pct)
        self.assertEqual({},provider.fetch_metadata("AAPL")[0].data)

    def test_repeated_reads_are_independent_and_original_files_are_byte_identical(self):
        provider=self.provider();before={self.path:self.path.read_bytes(),self.manifest_path:self.manifest_path.read_bytes()}
        first,_=provider.fetch_ohlc("AAPL");first.iloc[-1,0]=1
        first.attrs[PROVENANCE_ATTR]["source"]["file_sha256"]="changed"
        second,_=provider.fetch_ohlc_only("AAPL")
        self.assertNotEqual(1,second.iloc[-1,0])
        self.assertEqual(self.entry["source"]["file_sha256"],second.attrs[PROVENANCE_ATTR]["source"]["file_sha256"])
        proof=provider.provenance_for("AAPL");proof["provenance"]["source"].clear()
        self.assertEqual(self.entry["source"],provider.provenance_for("AAPL")["provenance"]["source"])
        self.assertEqual(before,{path:path.read_bytes() for path in before})


if __name__=="__main__":
    unittest.main()
