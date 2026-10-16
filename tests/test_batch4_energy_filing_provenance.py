"""Historical captured SEC facts remain distinct from today's issuer/instrument identity."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from market_checker_app.agents.commodity_energy_agent import CommodityEnergyAgent
from market_checker_app.agents.contracts import AgentContext, AgentResult, AgentStatus
from market_checker_app.collectors.short_report_client import FetchedShortReport, _extract_html
from market_checker_app.config import CommodityEnergyConfig, ShortReportSourceConfig, Stage3SourceVerificationConfig
from market_checker_app.services.filing_exposure_discovery_service import FilingExposureDiscoveryService

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "evidence" / "batch4_energy_20261013"
SPEC = importlib.util.spec_from_file_location("batch4_energy_offline_replay", FOLDER / "replay_captures.py")
REPLAY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPLAY)
ACCEPTED = datetime(2026, 2, 18, 21, 6, 52, tzinfo=timezone.utc)
CLOCK = datetime(2026, 10, 13, 14, tzinfo=timezone.utc)
URL = "https://www.sec.gov/Archives/edgar/data/34088/000003408826000045/xom-20251231.htm"


def synthetic(text: str, *, url: str = URL) -> FetchedShortReport:
    """Synthetic transport-free lexical fixture, never a new live proof."""
    source = ShortReportSourceConfig(ticker="XOM", publisher="SEC EDGAR", published_at=ACCEPTED,
                                    url=url, discovery_method="sec_filing")
    return FetchedShortReport(source=source, final_url=url, mime_type="text/html",
        title="Synthetic lexical fixture", text=text, content_hash=hashlib.sha256(text.encode()).hexdigest(),
        size_bytes=len(text.encode()), extractor="synthetic-fixture")


def run_agent(items: list[FetchedShortReport], *, document=None, clock: datetime = CLOCK) -> AgentResult:
    state = {"sec_filing_texts_by_ticker": {"XOM": items}}
    if document is not None:
        state["agent_results"] = {"f2_sec": AgentResult(status=AgentStatus.SUCCESS, documents=[document])}
    context = AgentContext(orchestration_id="batch4-energy-test", watchlist=("XOM",), started_at=clock, state=state)
    with patch("market_checker_app.agents.commodity_energy_agent.utc_now", return_value=clock):
        return CommodityEnergyAgent(CommodityEnergyConfig(enabled=True,
            source_verification=Stage3SourceVerificationConfig(enabled=True))).run(context)


class Batch4EnergyFilingProvenanceTests(unittest.TestCase):
    def test_actual_primary_bytes_and_independent_index_acceptance_agree(self) -> None:
        index = REPLAY.capture_index()
        self.assertEqual(3, len(index))
        for capture_id, metadata in index.items():
            with self.subTest(capture_id=capture_id):
                body = REPLAY.raw(capture_id)
                self.assertEqual(200, metadata["http_status"])
                self.assertEqual(len(body), metadata["raw_response_bytes"])
                self.assertLessEqual(len(body), metadata["max_response_bytes"])
                self.assertEqual(metadata["raw_response_sha256"], hashlib.sha256(body).hexdigest())
                self.assertEqual(metadata["requested_url"], metadata["final_url"])
                self.assertLessEqual(datetime.fromisoformat(metadata["started_at"]),
                                     datetime.fromisoformat(metadata["observed_at"]))
        text = " ".join(_extract_html(REPLAY.raw("sec_xom_2025_10k_index"), 100_000)[1].split())
        accepted_text = re.search(r"Accepted\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", text)[1]
        index_clock = datetime.strptime(accepted_text, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
        fetched, document, row = REPLAY.historical_input()
        self.assertEqual(ACCEPTED, index_clock)
        self.assertEqual(ACCEPTED, datetime.fromisoformat(row["acceptanceDateTime"].replace("Z", "+00:00")))
        self.assertEqual(("2025-12-31", "10-K", "xom-20251231.htm"),
                         (row["reportDate"], row["form"], row["primaryDocument"]))
        self.assertIn("Exxon Mobil Corporation", fetched.text)
        self.assertEqual(REPLAY.capture_index()["sec_xom_2025_10k"]["raw_response_sha256"], fetched.content_hash)

    def test_actual_filing_replay_emits_only_two_explicit_operational_outputs(self) -> None:
        fetched, document, _ = REPLAY.historical_input()
        findings = FilingExposureDiscoveryService().discover(fetched, max_supply_chain=0,
            filing_document=document, as_of=CLOCK)
        self.assertEqual({("Crude oil", "COMMODITY_OUTPUT"), ("Natural gas", "COMMODITY_OUTPUT")},
                         {(f.source.resource_name, f.source.exposure_type) for f in findings.commodity_energy})
        for finding in findings.commodity_energy:
            self.assertIn("Our principal business involves exploration for, and production of", finding.evidence_quote)
            self.assertLessEqual(len(finding.evidence_quote), 320)
            self.assertEqual(ACCEPTED, finding.source.published_at)
            self.assertEqual("2025-12-31", finding.source.disclosure_period)
            self.assertIsNone(finding.source.dependency_pct)
            self.assertLessEqual(finding.source.confidence, 0.40)
            provenance = finding.provenance
            self.assertEqual("0000034088", provenance["filing_cik"])
            self.assertEqual(REPLAY.ACCESSION, provenance["filing_accession_number"])
            self.assertTrue(provenance["acceptance_clock_verified"])
            self.assertEqual("HISTORICAL_FILING_ISSUER_ONLY", provenance["issuer_scope"])
            self.assertFalse(provenance["current_instrument_identity_verified"])
            self.assertFalse(provenance["asset_ownership_inferred"])
            self.assertFalse(provenance["historical_availability_reconstructed"])
            self.assertGreater(datetime.fromisoformat(provenance["known_at"]), ACCEPTED)

    def test_agent_preserves_historical_provenance_and_output_scenario_limits(self) -> None:
        fetched, document, _ = REPLAY.historical_input()
        result = run_agent([fetched], document=document)
        self.assertEqual(AgentStatus.SUCCESS, result.status)
        self.assertEqual(2, len(result.resource_exposures))
        self.assertEqual(1, len(result.documents))
        self.assertIsNone(result.documents[0].legal_entity_id)
        self.assertIsNone(result.documents[0].instrument_id)
        for exposure in result.resource_exposures:
            provenance = exposure.metadata["filing_exposure_provenance"]
            self.assertEqual(REPLAY.ACCESSION, provenance["filing_accession_number"])
            self.assertEqual(CLOCK.isoformat(), provenance["relationship_observed_at"])
            self.assertEqual(CLOCK.isoformat(), provenance["known_at"])
            self.assertEqual(ACCEPTED, exposure.published_at)
            self.assertFalse(exposure.metadata["scoring_applied"])
            self.assertFalse(exposure.metadata["price_series_attached"])
            self.assertFalse(exposure.metadata["prediction_input"])
            scenario = exposure.metadata["resource_margin_scenario"]
            self.assertEqual("INSUFFICIENT_DATA", scenario["status"])
            self.assertIn("OUTPUT_REVENUE_SENSITIVITY_NOT_SUPPORTED_BY_COST_FORMULA", scenario["reason"])
        self.assertTrue(all(e.direction == 0 and e.risk_score == 0 and not e.metadata["scoring_applied"]
                            for e in result.evidence))

    def test_macro_only_and_remote_cost_context_are_not_company_exposures(self) -> None:
        for text in (
            "Natural gas price volatility and supply availability increased globally in 2025.",
            "Prices for electricity and steel affect industry input costs.",
            "Our costs and purchasing availability increased " + "unrelated discussion " * 40 +
            " industry lithium prices changed globally.",
        ):
            with self.subTest(text=text):
                finding = FilingExposureDiscoveryService().discover(synthetic(text), max_supply_chain=0)
                self.assertEqual((), finding.commodity_energy)
                self.assertEqual([], run_agent([synthetic(text)]).resource_exposures)

    def test_first_person_outputs_exclude_negative_future_and_other_entities(self) -> None:
        for text in ("We do not produce crude oil or natural gas.",
                     "We plan to produce crude oil and natural gas.",
                     "We may sell natural gas from a future project.",
                     "Our supplier produces crude oil and natural gas.",
                     "An industry producer sells natural gas."):
            with self.subTest(text=text):
                self.assertEqual((), FilingExposureDiscoveryService().discover(
                    synthetic(text), max_supply_chain=0).commodity_energy)

    def test_explicit_input_output_direction_and_budget_are_kept_separate(self) -> None:
        fetched = synthetic("We produce natural gas. We purchase natural gas for our input costs.")
        findings = FilingExposureDiscoveryService().discover(fetched, max_supply_chain=0)
        self.assertEqual({"COMMODITY_OUTPUT", "FUEL"}, {f.source.exposure_type for f in findings.commodity_energy})
        self.assertTrue(all(f.source.dependency_pct is None for f in findings.commodity_energy))
        consumed = FilingExposureDiscoveryService().discover(
            synthetic("We consume natural gas to produce electricity."), max_supply_chain=0)
        self.assertEqual([("Natural gas", "FUEL")],
                         [(f.source.resource_name, f.source.exposure_type) for f in consumed.commodity_energy])
        bounded = FilingExposureDiscoveryService().discover(fetched, max_supply_chain=0, max_commodity_energy=1)
        self.assertEqual(1, len(bounded.commodity_energy))
        legacy = synthetic("We produce natural gas.", url="https://www.sec.gov/Archives/edgar/data/34088/test.htm")
        self.assertEqual((), FilingExposureDiscoveryService().discover(legacy, max_supply_chain=0).commodity_energy)

    def test_invalid_source_or_provenance_cannot_be_auto_adopted(self) -> None:
        fetched, document, _ = REPLAY.historical_input()
        variants = [replace(fetched, final_url=URL.replace("www.sec.gov", "eia.gov")),
                    replace(fetched, final_url=URL.replace("xom-20251231", "other-document")),
                    replace(fetched, source=replace(fetched.source, publisher="EIA")),
                    replace(fetched, source=replace(fetched.source, ticker="MSFT")),
                    replace(fetched, content_hash="not-a-hash")]
        for wrong in variants:
            with self.subTest(wrong=wrong.final_url):
                result = run_agent([wrong])
                self.assertEqual(AgentStatus.UNAVAILABLE, result.status)
                self.assertEqual(1, result.metadata["rejected_filings"])
                self.assertEqual([], result.resource_exposures)
        for key, value in (("cik", "2115436"), ("accession_number", "0002115436-26-000006"),
                           ("acceptance_source_url", "https://example.org/acceptance")):
            wrong = deepcopy(document)
            wrong.metadata[key] = value
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    FilingExposureDiscoveryService().discover(fetched, max_supply_chain=0,
                        filing_document=wrong, as_of=CLOCK)

    def test_real_acceptance_is_not_backdated_to_filing_day_midnight(self) -> None:
        fetched, document, _ = REPLAY.historical_input()
        before_acceptance = datetime(2026, 2, 18, 10, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "acceptance is after"):
            FilingExposureDiscoveryService().discover(fetched, max_supply_chain=0,
                filing_document=document, as_of=before_acceptance)
        conflict = replace(fetched, source=replace(fetched.source, published_at=ACCEPTED - timedelta(seconds=1)))
        with self.assertRaisesRegex(ValueError, "clock conflict"):
            FilingExposureDiscoveryService().discover(conflict, max_supply_chain=0,
                filing_document=document, as_of=CLOCK)

    def test_today_observation_does_not_reconstruct_historical_availability(self) -> None:
        fetched, document, _ = REPLAY.historical_input()
        past = datetime(2026, 3, 1, tzinfo=timezone.utc)
        findings = FilingExposureDiscoveryService().discover(fetched, max_supply_chain=0,
            filing_document=document, as_of=past)
        self.assertEqual(2, len(findings.commodity_energy))
        for finding in findings.commodity_energy:
            self.assertFalse(finding.provenance["available_at_cutoff"])
            self.assertFalse(finding.provenance["historical_availability_reconstructed"])
        result = run_agent([fetched], document=document, clock=past)
        for exposure in result.resource_exposures:
            self.assertFalse(exposure.metadata["filing_exposure_provenance"]["available_at_cutoff"])
            self.assertFalse(exposure.metadata["prediction_input"])

    def test_bad_second_filing_does_not_discard_completed_good_observations(self) -> None:
        fetched, document, _ = REPLAY.historical_input()
        wrong = replace(fetched, final_url=URL.replace("www.sec.gov", "eia.gov"))
        result = run_agent([fetched, wrong], document=document)
        self.assertEqual(AgentStatus.PARTIAL, result.status)
        self.assertEqual(2, len(result.resource_exposures))
        self.assertEqual((2, 1), (result.metadata["scanned_sec_filings"], result.metadata["rejected_filings"]))
        self.assertTrue(all(not e.metadata["scoring_applied"] for e in result.evidence))


if __name__ == "__main__":
    unittest.main()
