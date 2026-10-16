"""Real captured-review dispatch and synthetic offline pipeline integration.

Synthetic inputs here are temporary unit fixtures, never live/history evidence.
"""
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from market_checker_app.config import AppConfig
from market_checker_app.prediction_contract import canonical_hash
from market_checker_app.release_manifest import config_hash
from market_checker_app.services import captured_identity_review_service as reviews
from market_checker_app.services.pipeline_service import PipelineService
from market_checker_app.services.us_equity_calendar_service import last_completed_session, sessions_between
from market_checker_app.services.yahoo_price_provenance_service import capture_provider_frame, persist_observation

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 15, 22, tzinfo=timezone.utc)


def selected_fixture(root, *, complete=True):
    from market_checker_app.services.captured_market_data_provider import (
        BENCHMARK_TICKERS, build_captured_market_data_entry, MANIFEST_SCHEMA_VERSION,
    )
    latest = last_completed_session(NOW)
    days = sessions_between(latest - timedelta(days=600), latest)[-66:]
    frame = pd.DataFrame({"Open": [100.0]*66, "High": [102.0]*66, "Low": [99.0]*66,
        "Close": [101.0]*66, "Volume": [1000000]*66}, index=pd.DatetimeIndex(days))
    entries = []
    for ticker in (["AAPL", *BENCHMARK_TICKERS] if complete else ["AAPL"]):
        record = capture_provider_frame(ticker, frame, started_at=NOW-timedelta(minutes=2),
            observed_at=NOW-timedelta(minutes=1))
        record["evidence_origin"] = "DETERMINISTIC_TEST_FIXTURE"
        record["capture_id"] = canonical_hash({k:v for k,v in record.items() if k != "capture_id"})
        path = persist_observation(record, root / "selected")
        entries.append(build_captured_market_data_entry(archive_root=root,
            source_archive_path=path.relative_to(root).as_posix()))
    manifest = root / "market_manifest.json"
    manifest.write_text(json.dumps({"schema_version": MANIFEST_SCHEMA_VERSION, "instruments": entries}), encoding="utf-8")
    return manifest


class CapturedRuntimeIntegrationTests(unittest.TestCase):
    def config(self, root, manifest, *, bulk=False):
        return AppConfig(output_dir=root/"outputs", sqlite_path=root/"must-not-exist.db",
            save_history=False, agent_stage1_enabled=False,
            captured_market_manifest=manifest, captured_market_archive_root=root,
            large_universe_threshold=0 if bulk else 100)

    def pipeline(self, config):
        from market_checker_app.services.captured_market_data_provider import CapturedMarketDataProvider
        provider = CapturedMarketDataProvider(manifest_path=config.captured_market_manifest,
            archive_root=config.captured_market_archive_root, observation_clock=lambda: NOW)
        with patch("market_checker_app.services.captured_market_data_provider.CapturedMarketDataProvider", return_value=provider), \
             patch("market_checker_app.services.pipeline_service.YahooClient", side_effect=AssertionError("Online client")), \
             patch("market_checker_app.services.pipeline_service.YahooCacheStore", side_effect=AssertionError("Metadata cache")), \
             patch("market_checker_app.services.pipeline_service.YahooOhlcCacheStore", side_effect=AssertionError("OHLC cache")):
            pipeline = PipelineService(config)
        return pipeline

    def test_default_model_fingerprint_is_preserved_and_explicit_input_selection_is_distinct(self):
        default = AppConfig()
        old = asdict(default)
        old.pop("captured_market_manifest"); old.pop("captured_market_archive_root")
        self.assertEqual(config_hash(old), config_hash(default))
        active = replace(default, captured_market_manifest=Path("explicit.json"), captured_market_archive_root=Path("originals"))
        self.assertNotEqual(config_hash(default), config_hash(active))
        self.assertFalse(active.prospective_history_enabled)

    def test_incomplete_benchmark_scope_stops_before_any_cache_database_or_remote_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); manifest=selected_fixture(root, complete=False)
            with patch("socket.socket.connect", side_effect=AssertionError("HTTP")), \
                 patch("sqlite3.connect", side_effect=AssertionError("DB")):
                pipeline=self.pipeline(self.config(root, manifest))
                with self.assertRaisesRegex(RuntimeError, "WAIT_DATA"):
                    pipeline.run(["AAPL"], ["https://example.com/rss"], None, rss_enabled=True, mt5_enabled=True)
            self.assertFalse((root/"must-not-exist.db").exists())

    def test_normal_single_and_bulk_pipeline_use_exact_sources_without_network_or_cache(self):
        for bulk in (False, True):
            with self.subTest(bulk=bulk), tempfile.TemporaryDirectory() as directory:
                root=Path(directory); manifest=selected_fixture(root)
                with patch("socket.socket.connect", side_effect=AssertionError("HTTP")), \
                     patch("sqlite3.connect", side_effect=AssertionError("DB")), \
                     patch("market_checker_app.services.pipeline_service.utc_now", return_value=NOW):
                    pipeline=self.pipeline(self.config(root, manifest, bulk=bulk))
                    result=pipeline.run(["AAPL"], ["https://example.com/rss"], None,
                        rss_enabled=True, mt5_enabled=True, yahoo_metadata_enabled=True)
                row=result["signals"].iloc[0]
                self.assertEqual("captured_market_data", row["tech_source_used"])
                self.assertEqual(101.0, row["current_price"])
                self.assertEqual("captured_market_data_close", row["current_price_source"])
                self.assertEqual("captured_metadata_missing", row["yahoo_data_status"])
                provenance=result["point_in_time_inputs"][0]["provenance"]["captured_market_inputs"]
                self.assertEqual("DETERMINISTIC_TEST_FIXTURE",
                    result["point_in_time_inputs"][0]["provenance"]["evidence_origin"])
                self.assertTrue(result["point_in_time_inputs"][0]["provenance"]["fixture_only"])
                self.assertTrue(provenance["asset"]["source"]["file_sha256"])
                self.assertTrue(provenance["benchmark"]["source"]["file_sha256"])
                self.assertFalse(provenance["strict_pit_eligible"])
                self.assertEqual("READY", result["captured_market_data"]["pre_persistence"]["status"])
                self.assertIsNone(result["run_id"])
                self.assertFalse((root/"must-not-exist.db").exists())

    def test_mid_run_manifest_change_cannot_save_run_or_silently_use_another_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); manifest=selected_fixture(root)
            pipeline=self.pipeline(self.config(root, manifest))
            changed=False
            def progress(state):
                nonlocal changed
                if not changed:
                    manifest.write_bytes(manifest.read_bytes()+b" ")
                    changed=True
            with patch("socket.socket.connect", side_effect=AssertionError("HTTP")), \
                 patch("sqlite3.connect", side_effect=AssertionError("DB")), \
                 patch("market_checker_app.services.pipeline_service.utc_now", return_value=NOW):
                with self.assertRaisesRegex(RuntimeError, "WAIT_DATA"):
                    pipeline.run(["AAPL"], [], None, progress_callback=progress)
            self.assertTrue(changed)
            self.assertFalse((root/"must-not-exist.db").exists())

    def test_weekly_wait_data_preflight_does_not_initialize_history_or_output(self):
        from market_checker_app.weekly_shadow_runner import run_weekly_shadow, RuntimeConfigurationError
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); manifest=selected_fixture(root, complete=False)
            config=self.config(root, manifest)
            pipeline=self.pipeline(config)
            with patch("market_checker_app.services.pipeline_service.PipelineService", return_value=pipeline), \
                 patch("market_checker_app.weekly_shadow_runner.SQLiteStore", side_effect=AssertionError("History opened")), \
                 patch("socket.socket.connect", side_effect=AssertionError("HTTP")):
                with self.assertRaisesRegex(RuntimeConfigurationError, "WAIT_DATA"):
                    run_weekly_shadow(config=config, tickers=["AAPL"], rss_sources=[],
                        rss_enabled=True, mt5_enabled=True, yahoo_metadata_enabled=True)
            self.assertFalse((root/"outputs").exists())

    def test_cli_readiness_is_local_and_never_loads_runtime_or_initializes_history(self):
        from market_checker_app import weekly_shadow_runner as cli
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); manifest=selected_fixture(root, complete=False)
            args=["runner", "--tickers", "AAPL", "--captured-market-manifest", str(manifest),
                "--captured-market-archive-root", str(root), "--captured-market-readiness-only"]
            with patch("sys.argv", args), patch("sys.stdout", new_callable=io.StringIO) as output, \
                 patch("socket.socket.connect", side_effect=AssertionError("HTTP")), \
                 patch("sqlite3.connect", side_effect=AssertionError("DB")), \
                 patch.object(cli, "AgentRuntimeService", side_effect=AssertionError("Runtime loaded")):
                cli.main()
            report=json.loads(output.getvalue())
            self.assertEqual("WAIT_DATA", report["status"])
            self.assertGreater(report["missing_count"], 0)


class Batch8ReviewBudgetTests(unittest.TestCase):
    def test_all_new_review_kinds_refuse_oversized_whole_body_before_file_io(self):
        item={"metadata": {}, "body_available": True,
            "body_ref": {"path": "not-opened.body", "sha256": "0"*64, "bytes": 4000001}}
        for kind in ("SEC_SYMBOL_ACTION_SCOPE", "FDA_STRUCTURED_PRODUCT", "DOJ_CASE_POSTURE",
                     "SEC_PURCHASE_OBLIGATION_SCOPE", "SEC_OWNER_INSTRUMENT_SCOPE"):
            with self.subTest(kind=kind), patch.object(Path, "open", side_effect=AssertionError("Body read")):
                with self.assertRaises(ValueError):
                    reviews._load_captures({"kind": kind, "captures": {"source": item}}, ROOT, datetime.now(timezone.utc))

    def test_actual_consolidated_commitments_survive_runtime_without_supplier_allocation(self):
        with tempfile.TemporaryDirectory() as directory, patch("socket.socket.connect", side_effect=AssertionError("HTTP")):
            result=reviews.run_captured_identity_review(source_root=ROOT,
                plan_name="evidence/batch8_contracts_20261015/review_plan.json", archive_root=Path(directory))
            raw=(Path(directory)/result["archive_filename"]).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), result["archive_sha256"])
            leaf=json.loads(raw)["review"]
        self.assertEqual("CANDIDATE_REVIEWED",leaf["status"])
        self.assertEqual("95200000000",leaf["claim"]["quantity"]["scaled_currency_amount"])
        self.assertEqual("CONSOLIDATED_GROUP",leaf["claim"]["statement_scope"])
        self.assertIsNone(leaf["claim"]["supplier_share"])
        self.assertFalse(leaf["claim"]["individual_issuer_amount_attribution_allowed"])
        self.assertFalse(result["full_specialist_acceptance"])




class ActualNewScopeDispatchTests(unittest.TestCase):
    def run_plan(self, name):
        with tempfile.TemporaryDirectory() as directory, \
             patch('socket.socket.connect', side_effect=AssertionError('No review HTTP')), \
             patch('sqlite3.connect', side_effect=AssertionError('No review DB')):
            result=reviews.run_captured_identity_review(source_root=ROOT,
                plan_name='evidence/specialist_review_plans/'+name, archive_root=Path(directory))
            raw=(Path(directory)/result['archive_filename']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),result['archive_sha256'])
            self.assertEqual(result['review'],json.loads(raw)['review'])
        self.assertEqual(0,result['source_http_requests'])
        self.assertEqual(0,result['registry_adoptions'])
        self.assertFalse(result['scoring_applied'])
        self.assertFalse(result['full_specialist_acceptance'])
        return result['review']

    def test_actual_symbol_scope_keeps_missing_effective_day_and_118_byte_conflict(self):
        leaf=self.run_plan('PSTG_zmena_tickeru_a_instrumentu.json')
        self.assertEqual('PRIMARY_SCOPE_PARTIAL',leaf['status'])
        self.assertIsNone(leaf['effective_on'])
        self.assertEqual(0,leaf['current_q3_verified_coverage'])
        self.assertFalse(leaf['automatic_alias_allowed'])
        self.assertTrue(any(row.get('publisher_size_conflict') for row in leaf['filings'].values()))
        self.assertEqual(118,leaf['capture_pins']['release']['bytes']-leaf['filings']['release']['index_declared_bytes'])

    def test_actual_fda_missing_structured_original_and_crl_wrong_dataset_both_stay_wait_data(self):
        for name,reason in [('PFE_strukturovany_produkt_FDA.json','STRUCTURED_WHOLE_BODY_UNAVAILABLE'),
                            ('PFE_CRL_jiny_dataset.json','RETAINED_SOURCE_IS_NOT_STRUCTURED_DRUGSFDA_DATASET')]:
            with self.subTest(name=name):
                leaf=self.run_plan(name)
                self.assertEqual('WAIT_DATA',leaf['data_status'])
                self.assertIn(reason,leaf['reasons'])
                self.assertEqual([],leaf['structured_rows'])
                self.assertIsNone(leaf['applicant_product_candidate'])
                self.assertFalse(leaf['legal_owner_verified'])

    def test_actual_opinion_and_opposition_do_not_become_a_court_order_or_merits_finding(self):
        leaf=self.run_plan('AAPL_procesni_stav_DOJ.json')
        self.assertEqual(2,leaf['supported_procedural_events'])
        self.assertEqual(1,leaf['court_opinions_supported'])
        self.assertEqual(0,leaf['original_court_orders_supported'])
        self.assertFalse(leaf['final_judgment_verified'])
        self.assertFalse(leaf['merits_adjudication_verified'])
        self.assertFalse(leaf['issuer_attribution_verified'])

    def test_actual_owner_xml_preserves_all_rows_without_inventing_missing_packet_or_person_identity(self):
        leaf=self.run_plan('MSFT_soucasny_ucet_a_instrument.json')
        self.assertEqual(7,len(leaf['instrument_rows']))
        self.assertTrue(leaf['dated_reporting_account_match_supported'])
        self.assertTrue(leaf['source_only_xml_scope_supported'])
        self.assertFalse(leaf['current_header_corroborated'])
        self.assertFalse(leaf['natural_person_identity_verified'])
        self.assertFalse(leaf['instrument_identity_verified'])
        self.assertIsNone(leaf['captures']['packet']['sha256'])
        self.assertIsNone(leaf['captures']['packet']['size_bytes'])


class CapturedReadinessUITests(unittest.TestCase):
    def test_readiness_requires_explicit_button_and_never_registers_or_downloads(self):
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); manifest=selected_fixture(root,complete=False)
            before={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
            script=('from pathlib import Path\nimport streamlit as st\n'
                'from market_checker_app.specialist_evidence_ui import render_captured_market_readiness\n'
                f'render_captured_market_readiness(st,manifest_path=Path({str(manifest)!r}),archive_root=Path({str(root)!r}),tickers=["AAPL"])\n')
            with patch('socket.socket.connect',side_effect=AssertionError('No UI HTTP')), \
                 patch('sqlite3.connect',side_effect=AssertionError('No UI DB')):
                app=AppTest.from_string(script).run(timeout=30)
                self.assertEqual([],list(app.exception))
                self.assertEqual(0,len(app.json))
                next(b for b in app.button if b.label=='Ověřit připravenost vybraných cenových archivů').click().run(timeout=30)
                self.assertEqual([],list(app.exception))
                self.assertTrue(app.warning)
                self.assertIn('WAIT_DATA',app.json[0].value)
            self.assertEqual(before,{p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()})


if __name__ == '__main__':
    unittest.main()
