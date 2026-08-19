from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from market_checker_app.collectors.yahoo_client import YahooClient
from market_checker_app.collectors.sec_edgar_client import (
    SecCompany,
    SecCompanyBundle,
    SecCompanyFact,
    SecFiling,
)
from market_checker_app.collectors.short_report_client import FetchedShortReport
from market_checker_app.config import (
    AppConfig,
    CommodityEnergyConfig,
    CommodityEnergySourceConfig,
    FundamentalIngestionConfig,
    RegulatoryContractConfig,
    RegulatoryContractSourceConfig,
    ShortReportConfig,
    ShortReportSourceConfig,
    SupplyChainConfig,
    SupplyChainSourceConfig,
)
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


class _FakeSecClient:
    def fetch_company_bundle(self, ticker: str, **_: object) -> SecCompanyBundle | None:
        if ticker != "AAPL":
            return None
        filed_at = datetime(2025, 8, 1, tzinfo=timezone.utc)
        period_end = datetime(2025, 6, 28, tzinfo=timezone.utc)
        filing_url = (
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "000032019325000079/aapl-20250628.htm"
        )
        filing = SecFiling(
            accession_number="0000320193-25-000079",
            form="10-Q",
            filed_at=filed_at,
            report_date=period_end,
            primary_document="aapl-20250628.htm",
            filing_url=filing_url,
            index_url=(
                "https://www.sec.gov/Archives/edgar/data/320193/"
                "000032019325000079/0000320193-25-000079-index.html"
            ),
        )
        duration_start = datetime(2025, 3, 30, tzinfo=timezone.utc)

        def fact(
            concept: str,
            value: float,
            *,
            duration: bool,
        ) -> SecCompanyFact:
            return SecCompanyFact(
                taxonomy="us-gaap",
                concept=concept,
                label=concept,
                description=f"Test value for {concept}.",
                unit="USD",
                value=value,
                filed_at=filed_at,
                form="10-Q",
                accession_number=filing.accession_number,
                source_url=filing_url,
                period_start=duration_start if duration else None,
                period_end=period_end,
                fiscal_year=2025,
                fiscal_period="Q3",
                frame="CY2025Q2" if duration else "CY2025Q2I",
            )

        facts = tuple(
            fact(concept, value, duration=duration)
            for concept, value, duration in (
                ("RevenueFromContractWithCustomerExcludingAssessedTax", 100.0, True),
                ("NetIncomeLoss", 20.0, True),
                ("OperatingIncomeLoss", 15.0, True),
                ("NetCashProvidedByUsedInOperatingActivities", 25.0, True),
                ("PaymentsToAcquirePropertyPlantAndEquipment", 5.0, True),
                ("Assets", 200.0, False),
                ("Liabilities", 80.0, False),
                ("AssetsCurrent", 60.0, False),
                ("LiabilitiesCurrent", 30.0, False),
                ("LongTermDebtNoncurrent", 40.0, False),
            )
        )
        return SecCompanyBundle(
            SecCompany("AAPL", "0000320193", "Apple Inc.", "Nasdaq"),
            (filing,),
            facts,
        )


class _FakeShortReportClient:
    def fetch(self, source: ShortReportSourceConfig) -> FetchedShortReport:
        text = (
            "We believe operating cash flow is weak and cash conversion is unsustainable. "
            "The company carries excessive debt and leverage that threaten its balance sheet. "
            "Management failed to disclose material related-party arrangements."
        )
        return FetchedShortReport(
            source=source,
            final_url=source.url,
            mime_type="text/html",
            title="Runtime fixture report",
            text=text,
            content_hash="b" * 64,
            size_bytes=len(text.encode("utf-8")),
            extractor="fixture",
        )


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
            self.assertEqual("SUCCESS", result["agent_status"])
            self.assertEqual("PASS", result["quality_gate_decision"])
            self.assertTrue(result["agent_report"].shadow_mode)
            self.assertEqual(
                result["signals"].iloc[0]["action"],
                result["agent_report"].signals[0].action,
            )
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
            self.assertEqual(3, len(store.read_agent_runs()))
            self.assertEqual(1, len(store.read_entities()))
            self.assertEqual(1, len(store.read_evidence()))
            self.assertEqual(1, len(store.read_agent_signals()))
            self.assertEqual(1, len(store.read_quality_gate_checks()))

    def test_pipeline_persists_stage2_sec_ingestion_without_rescoring(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            store = SQLiteStore(output_dir / "history.db")
            pipeline = PipelineService(
                AppConfig(
                    output_dir=output_dir,
                    sqlite_path=store.db_path,
                    save_history=True,
                    fundamental_ingestion=FundamentalIngestionConfig(
                        enabled=True,
                        user_agent="JohnySkoreTests tests@example.com",
                    ),
                )
            )
            pipeline.yahoo_client = _FakeYahooClient()
            pipeline.sec_client = _FakeSecClient()

            result = pipeline.run(
                ["AAPL"],
                [],
                store,
                yahoo_only_tickers={"AAPL"},
                rss_enabled=False,
                mt5_enabled=False,
            )

            self.assertEqual("SUCCESS", result["agent_status"])
            self.assertEqual("SUCCESS", result["fundamental_ingestion_status"])
            self.assertEqual("SUCCESS", result["financial_forensics_status"])
            self.assertEqual(1, result["fundamental_document_count"])
            self.assertEqual(10, result["fundamental_fact_count"])
            self.assertEqual(1, result["financial_forensics_evidence_count"])
            self.assertEqual(0, result["financial_forensics_high_findings"])
            self.assertEqual(0, result["financial_forensics_warning_findings"])
            self.assertEqual(
                result["signals"].iloc[0]["action"],
                result["agent_report"].signals[0].action,
            )
            self.assertEqual(5, len(store.read_agent_runs()))
            facts = store.read_fundamental_facts("AAPL")
            self.assertEqual(10, len(facts))
            assets = facts.loc[facts["concept"] == "Assets"].iloc[0]
            self.assertEqual(200.0, float(assets["value"]))

    def test_pipeline_runs_short_report_claim_verification_in_shadow_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            store = SQLiteStore(output_dir / "history.db")
            source = ShortReportSourceConfig(
                ticker="AAPL",
                publisher="Example Research",
                published_at=datetime.now(timezone.utc) - timedelta(days=1),
                url="https://research.example.test/aapl-report.html",
            )
            pipeline = PipelineService(
                AppConfig(
                    output_dir=output_dir,
                    sqlite_path=store.db_path,
                    save_history=True,
                    fundamental_ingestion=FundamentalIngestionConfig(
                        enabled=True,
                        user_agent="JohnySkoreTests tests@example.com",
                    ),
                    short_reports=ShortReportConfig(
                        enabled=True,
                        sources=(source,),
                    ),
                )
            )
            pipeline.yahoo_client = _FakeYahooClient()
            pipeline.sec_client = _FakeSecClient()
            pipeline.short_report_client = _FakeShortReportClient()

            result = pipeline.run(
                ["AAPL"],
                [],
                store,
                yahoo_only_tickers={"AAPL"},
                rss_enabled=False,
                mt5_enabled=False,
            )

            self.assertEqual("PASS", result["quality_gate_decision"])
            self.assertEqual("SUCCESS", result["short_report_status"])
            self.assertEqual("PARTIAL", result["claim_verification_status"])
            self.assertEqual(1, result["short_report_document_count"])
            self.assertEqual(3, result["short_report_claim_count"])
            self.assertEqual(0, result["claim_corroborated_count"])
            self.assertEqual(2, result["claim_contradicted_count"])
            self.assertEqual(1, result["claim_insufficient_count"])
            self.assertEqual(
                result["signals"].iloc[0]["action"],
                result["agent_report"].signals[0].action,
            )
            self.assertEqual(7, len(store.read_agent_runs()))
            self.assertEqual(3, len(store.read_research_claims("AAPL")))
            with store._connect() as conn:
                observations = conn.execute(
                    "SELECT COUNT(*) FROM research_claim_observations"
                ).fetchone()[0]
            self.assertEqual(6, observations)

    def test_pipeline_runs_stage3_network_agents_without_rescoring(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            store = SQLiteStore(output_dir / "history.db")
            published_at = datetime(2026, 1, 15, tzinfo=timezone.utc)
            pipeline = PipelineService(
                AppConfig(
                    output_dir=output_dir,
                    sqlite_path=store.db_path,
                    save_history=True,
                    supply_chain=SupplyChainConfig(
                        enabled=True,
                        sources=(
                            SupplyChainSourceConfig(
                                ticker="AAPL",
                                counterparty="Example Supplier",
                                relationship_type="SUPPLIER",
                                dependency_pct=20.0,
                                publisher="Company report",
                                published_at=published_at,
                                url="https://example.com/company-report",
                            ),
                        ),
                    ),
                    commodity_energy=CommodityEnergyConfig(
                        enabled=True,
                        sources=(
                            CommodityEnergySourceConfig(
                                ticker="AAPL",
                                resource_name="Electricity",
                                exposure_type="ELECTRICITY",
                                publisher="Sustainability report",
                                published_at=published_at,
                                url="https://example.com/sustainability",
                            ),
                        ),
                    ),
                    regulatory_contract=RegulatoryContractConfig(
                        enabled=True,
                        sources=(
                            RegulatoryContractSourceConfig(
                                ticker="AAPL",
                                event_type="REGULATORY_APPROVAL",
                                status="COMPLETED",
                                title="Example approval",
                                authority_or_counterparty="Example Authority",
                                publisher="Example Authority",
                                published_at=published_at,
                                url="https://authority.example.org/approval",
                            ),
                        ),
                    ),
                )
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

            self.assertEqual("PASS", result["quality_gate_decision"])
            self.assertEqual("SUCCESS", result["supply_chain_status"])
            self.assertEqual("SUCCESS", result["commodity_energy_status"])
            self.assertEqual("SUCCESS", result["regulatory_contract_status"])
            self.assertEqual(1, result["supply_chain_relationship_count"])
            self.assertEqual(1, result["commodity_energy_exposure_count"])
            self.assertEqual(1, result["regulatory_contract_event_count"])
            self.assertEqual(
                result["signals"].iloc[0]["action"],
                result["agent_report"].signals[0].action,
            )
            self.assertEqual(1, len(store.read_company_relationships("AAPL")))
            self.assertEqual(1, len(store.read_resource_exposures("AAPL")))
            self.assertEqual(1, len(store.read_regulatory_contract_events("AAPL")))

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
                quality_gate_columns = {
                    row[1]
                    for row in conn.execute(
                        "PRAGMA table_info(quality_gate_checks)"
                    ).fetchall()
                }
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
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
            self.assertTrue(
                {
                    "orchestration_runs",
                    "agent_runs",
                    "entities",
                    "entity_observations",
                    "documents",
                    "document_observations",
                    "fundamental_facts",
                    "fundamental_fact_observations",
                    "research_claims",
                    "research_claim_observations",
                    "company_relationships",
                    "company_relationship_observations",
                    "resource_exposures",
                    "resource_exposure_observations",
                    "regulatory_contract_events",
                    "regulatory_contract_event_observations",
                    "evidence",
                    "agent_signals",
                    "quality_gate_checks",
                }.issubset(tables)
            )
            self.assertIn("claim_ids_json", quality_gate_columns)
            self.assertIn("relationship_ids_json", quality_gate_columns)
            self.assertIn("exposure_ids_json", quality_gate_columns)
            self.assertIn("regulatory_event_ids_json", quality_gate_columns)

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
