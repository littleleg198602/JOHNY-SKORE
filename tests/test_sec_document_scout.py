from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import tempfile
import unittest

from market_checker_app.collectors.sec_edgar_client import (
    MAX_SEC_DOCUMENT_BYTES, SecCompany, SecEdgarClient, SecEdgarError, SecFiling,
    _SecRedirectPolicy, _allowed_sec_url,
)
from market_checker_app.agents import (
    EntityRegistryAgent, GovernanceEventAgent, OrchestratorAgent, PredictionV21AdapterAgent,
    QualityGateAgent, SourceResolutionAgent,
)
from market_checker_app.agents.contracts import (
    GovernanceEventStatus, GovernanceEventType,
)
from market_checker_app.agents.scout_index_agent import ScoutIndexAgent
from market_checker_app.exporters.excel_exporter import ExcelExporter
from market_checker_app.services.sec_document_extraction import extract_sec_item_excerpts
from market_checker_app.services.sec_scout_service import SecScoutService
from market_checker_app.storage.scout_store import ScoutStore
from tests.test_sec_scout_service import FakeIndex
import pandas as pd


class DocumentIndex(FakeIndex):
    def fetch_filing_document(self, filing: SecFiling, *, cik: str) -> bytes:
        assert cik == "0000320193"
        return (b"<html><script>Item 9.99 fake</script>"
                b"<h2>Item 2.02</h2><p>Results were furnished.</p>"
                b"<h2>Item 5.02</h2><p>A director departed.</p></html>")


class ExposureIndex(DocumentIndex):
    def fetch_filing_document(self, filing: SecFiling, *, cik: str) -> bytes:
        return (b"<html><script>single supplier of copper fake</script>"
                b"<p>We depend on a single supplier for production.</p>"
                b"<p>Our raw material costs for copper may increase.</p></html>")


class DetailedExposureIndex(DocumentIndex):
    def ticker_map(self):
        return {"ACME": SecCompany("ACME", "0000001234", "ACME COMPONENTS, INC.", "NYSE")}

    def fetch_filing_document(self, filing: SecFiling, *, cik: str) -> bytes:
        return (b"<html><p>We purchase semiconductor components from Acme Components Inc.</p>"
                b"<p>We hedged 60% of our anticipated jet fuel purchases for 2027.</p></html>")


class OwnershipDocumentIndex(DocumentIndex):
    def fetch_filing_index(self, ticker, **kwargs):
        company, filings = super().fetch_filing_index(ticker, **kwargs)
        return company, (replace(
            filings[0], form="SC 13G",
            report_date=datetime(2026, 3, 13, tzinfo=timezone.utc),
        ),)

    def fetch_filing_document(self, filing: SecFiling, *, cik: str) -> bytes:
        return b"""<html><body><h1>SCHEDULE 13G</h1>
        <p>Apple Inc.</p><p>(Name of Issuer)</p>
        <p>Common Stock</p><p>(Title of Class of Securities)</p>
        <p>037833100</p><p>(CUSIP Number)</p>
        <p>Notice Recipient</p><p>(Name, Address and Telephone Number of Person Authorized to Receive Notices and Communications)</p>
        <p>03/13/2026</p><p>(Date of Event Which Requires Filing of This Statement)</p>
        <div>1 | Name of reporting person Example Asset Manager LLC</div>
        <div>2 | Check the appropriate box if a member of a Group</div>
        <div>11 | Aggregate amount beneficially owned by each reporting person 100,000.00</div>
        <div>13 | Percent of class represented by amount in Row (11) 1.0 %</div>
        </body></html>"""


class SecDocumentScoutTest(unittest.TestCase):
    def test_source_verified_13g_keeps_identity_into_governance_without_claiming_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            now = datetime(2026, 4, 1, tzinfo=timezone.utc)
            scout = SecScoutService(
                store, client=OwnershipDocumentIndex(now - timedelta(days=1)),
            )
            scout.schedule(["AAPL"], as_of=now)
            self.assertEqual("OK", scout.run_batch(as_of=now, limit=2)["status"])

            orchestrator = OrchestratorAgent(shadow_mode=True)
            orchestrator.register(EntityRegistryAgent({"AAPL": {
                "entity_id": "listing:aapl",
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "cik": "320193",
                "lei": "HWUPKR0MPOU8FGXBT394",
                "isin": "US0378331005",
                "source": "primary_manifest",
                "source_url": "https://www.sec.gov/Archives/edgar/data/320193/",
                "confidence": 1.0,
            }}))
            orchestrator.register(ScoutIndexAgent(store.db_path))
            orchestrator.register(GovernanceEventAgent(
                dependencies=("entity_registry", "scout_index"),
            ))
            report = orchestrator.run(watchlist=["AAPL"])

            document = next(item for item in report.documents
                            if not item.metadata["filing_index_only"])
            self.assertEqual("SC 13G", document.metadata["form"])
            self.assertEqual("0000320193-26-000001", document.metadata["accession_number"])
            self.assertEqual("0000320193", document.metadata["issuer_cik"])
            self.assertEqual("2026-03-13T00:00:00+00:00", document.metadata["report_date"])
            ownership_events = [
                item for item in report.governance_events
                if item.event_type == GovernanceEventType.BENEFICIAL_OWNERSHIP_FILING
            ]
            self.assertEqual(1, len(ownership_events))
            event = ownership_events[0]
            self.assertTrue(event.metadata["as_filed_cover_fields_extracted"])
            self.assertEqual(GovernanceEventStatus.UNVERIFIED, event.status)
            cover = event.metadata["as_filed_cover_fields"]
            self.assertEqual(["037833100"], cover["instrument"]["cusips"])
            self.assertEqual("Common Stock", cover["instrument"]["class_title"])
            self.assertEqual("Example Asset Manager LLC",
                             cover["reporting_persons"][0]["name"])
            self.assertEqual("NAME_ONLY",
                             cover["reporting_persons"][0]["identity_status"])
            self.assertFalse(event.metadata["beneficial_owner_identity_verified"])
            self.assertFalse(event.metadata["instrument_identity_verified"])
            self.assertFalse(event.metadata["ownership_change_interpreted"])
            self.assertFalse(event.metadata["scoring_applied"])

    def test_named_supplier_and_hedge_details_survive_storage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            now = datetime.now(timezone.utc)
            scout = SecScoutService(store, client=DetailedExposureIndex(now - timedelta(days=1)))
            scout.schedule(["AAPL"], as_of=now)
            self.assertEqual("OK", scout.run_batch(as_of=now, limit=2)["status"])
            details = [json.loads(row["details_json"]) for row in store.findings_as_of("AAPL", as_of=now)]
            named = next(row for row in details if row.get("resource_or_counterparty") == "Acme Components Inc.")
            self.assertEqual("NAMED_ONLY", named["identity_status"])
            self.assertEqual("semiconductor components", named["product_or_input"])
            self.assertEqual("0000001234", named["sec_catalog_match"]["cik"])
            self.assertEqual(now.isoformat(), named["sec_catalog_match"]["observed_at"])
            fuel = next(row for row in details if row.get("resource_or_counterparty") == "Jet fuel")
            self.assertEqual(60.0, fuel["hedged_share_pct"])
            self.assertEqual("2027", fuel["disclosure_period"])

    def test_annual_filing_opens_supplier_and_commodity_questions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            now = datetime.now(timezone.utc)
            scout = SecScoutService(store, client=ExposureIndex(now - timedelta(days=1)))
            scout.schedule(["AAPL"], as_of=now)
            result = scout.run_batch(as_of=now, limit=2)
            self.assertEqual("OK", result["status"])
            self.assertEqual(4, result["new_findings"])
            found = store.findings_as_of("AAPL", as_of=now)
            exposures = [row for row in found
                         if json.loads(row["details_json"])["stage"] == "exposure_candidate"]
            self.assertEqual(2, len(exposures))
            self.assertEqual({"supply_chain", "commodity_energy"},
                             {json.loads(row["details_json"])["kind"] for row in exposures})
            self.assertTrue(all("fake" not in json.loads(row["details_json"])["quote"]
                                for row in exposures))
            self.assertEqual(3, len(store.open_leads(["AAPL"], as_of=now)))
            orchestrator = OrchestratorAgent(shadow_mode=True)
            orchestrator.register(EntityRegistryAgent())
            orchestrator.register(ScoutIndexAgent(store.db_path))
            orchestrator.register(SourceResolutionAgent(dependencies=("entity_registry",)))
            orchestrator.register(PredictionV21AdapterAgent())
            orchestrator.register(QualityGateAgent())
            report = orchestrator.run(watchlist=["AAPL"], state={"signals": pd.DataFrame([{
                "ticker": "AAPL", "action": "NO_TRADE", "forecast": "FLAT",
                "decision_confidence": 0.5, "risk_score": 0.0,
                "action_reasons": '["test"]',
            }])})
            candidate_evidence = [item for item in report.evidence
                                  if item.event_type == "SEC_EXPOSURE_CANDIDATE"]
            self.assertEqual(2, len(candidate_evidence))
            self.assertTrue(all(item.direction == 0 for item in candidate_evidence))
            self.assertEqual(0, scout.run_batch(as_of=now, limit=2)["new_findings"])

    def test_sec_connector_refuses_cross_domain_requests_and_redirects(self) -> None:
        self.assertEqual("https://data.sec.gov/submissions/CIK0000320193.json",
                         _allowed_sec_url(
                             "https://data.sec.gov/submissions/CIK0000320193.json"
                         ))
        for url in (
            "http://www.sec.gov/Archives/edgar/data/123/a.htm",
            "https://example.com/Archives/edgar/data/123/a.htm",
            "https://www.sec.gov@evil.example/Archives/edgar/data/123/a.htm",
            "https://www.sec.gov/private/data",
        ):
            with self.assertRaises(SecEdgarError):
                _allowed_sec_url(url)
        with self.assertRaises(SecEdgarError):
            _SecRedirectPolicy().redirect_request(
                None, None, 302, "Found", {}, "https://evil.example/steal",
            )

    def test_8k_section_leads_have_primary_source_and_stay_open(self) -> None:
        class EightK(DocumentIndex):
            def fetch_filing_index(self, ticker, **kwargs):
                company, filings = super().fetch_filing_index(ticker, **kwargs)
                return company, (replace(filings[0], form="8-K"),)

        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            now = datetime.now(timezone.utc)
            scout = SecScoutService(store, client=EightK(now - timedelta(days=1)))
            scout.schedule(["AAPL"], as_of=now)
            self.assertEqual(2, scout.run_batch(as_of=now, limit=2)["new_findings"])
            leads = store.open_leads(["AAPL"], as_of=now)
            self.assertEqual(3, len(leads))
            self.assertEqual({0, 1}, {row["depth"] for row in leads})
            self.assertTrue(any("Item 2.02" in row["question"] for row in leads))
            self.assertTrue(all(row["source_url"].startswith("https://www.sec.gov/")
                                for row in leads))

    def test_content_has_actual_hash_cited_items_and_historical_cutoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            now = datetime.now(timezone.utc)
            scout = SecScoutService(store, client=DocumentIndex(now - timedelta(days=1)))
            scout.schedule(["AAPL"], as_of=now)
            result = scout.run_batch(as_of=now, limit=2)
            self.assertEqual("OK", result["status"])
            self.assertEqual(2, result["new_findings"])
            self.assertEqual([], store.findings_as_of("AAPL", as_of=now - timedelta(seconds=1)))
            records = store.findings_as_of("AAPL", as_of=now)
            self.assertEqual(2, len(records))
            self.assertEqual({"filing_index", "filing_document"},
                             {row["stage"] for row in store.latest_findings(
                                 ["AAPL"], as_of=now,
                             )})
            self.assertTrue(any('"stage": "filing_document"' in row["details_json"]
                                for row in records))
            leads = store.open_leads(["AAPL"], as_of=now)
            self.assertEqual(1, len(leads))  # FakeIndex returns 10-K, no 8-K claims.
            orchestrator = OrchestratorAgent(shadow_mode=True)
            orchestrator.register(EntityRegistryAgent())
            orchestrator.register(ScoutIndexAgent(store.db_path))
            orchestrator.register(SourceResolutionAgent(dependencies=("entity_registry",)))
            orchestrator.register(PredictionV21AdapterAgent())
            orchestrator.register(QualityGateAgent())
            report = orchestrator.run(
                watchlist=["AAPL"], state={"signals": pd.DataFrame([{
                    "ticker": "AAPL", "action": "NO_TRADE", "forecast": "FLAT",
                    "decision_confidence": 0.5, "risk_score": 0.0,
                    "action_reasons": '["test"]',
                }])},
            )
            scout_docs = [document for document in report.documents
                          if document.source == "SEC EDGAR index"]
            self.assertEqual(1, len(scout_docs))
            self.assertFalse(scout_docs[0].metadata["filing_index_only"])
            self.assertTrue(all(item.direction == 0 for item in report.evidence
                                if item.agent_name == "scout_index"))
            scout_execution = next(item for item in report.executions
                                   if item.agent_name == "scout_index")
            self.assertEqual(1, scout_execution.result.metadata["superseded_index_documents"])
            self.assertEqual(2, scout_execution.result.metadata["source_findings_in_snapshot"])
            saved = store.analysis_snapshot(report.orchestration_id)
            self.assertEqual({row["finding_id"] for row in records},
                             set(saved["finding_ids"]))
            export_rows = store.findings_for_snapshot(report.orchestration_id)
            self.assertEqual({row["finding_id"] for row in records},
                             {row["finding_id"] for row in export_rows})
            workbook = Path(directory) / "report.xlsx"
            ExcelExporter().export(
                workbook, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {},
                scout_evidence=pd.DataFrame(export_rows),
            )
            with pd.ExcelFile(workbook) as sheets:
                self.assertIn("ScoutEvidence", sheets.sheet_names)
                exported = pd.read_excel(sheets, sheet_name="ScoutEvidence")
            self.assertEqual({row["finding_id"] for row in records},
                             set(exported["finding_id"]))

    def test_extracts_8k_items_without_script_and_without_claim_inference(self) -> None:
        raw = (b"<script>Item 9.99 false</script><h2>Item 2.02</h2>"
               b"<p>Sales changed in the quarter.</p><h2>Item 5.02</h2>"
               b"<p>A director departed.</p>")
        self.assertEqual(("Item 2.02", "Sales changed in the quarter"),
                         extract_sec_item_excerpts(raw, form="8-K")[0])
        self.assertEqual((), extract_sec_item_excerpts(raw, form="10-K"))

    def test_primary_document_must_match_sec_identity_and_host(self) -> None:
        urls = []

        def transport(url, headers, timeout):
            urls.append(url)
            return b"<html>Source</html>"

        client = SecEdgarClient(user_agent="Test tests@example.com", text_transport=transport)
        now = datetime.now(timezone.utc)
        valid_url = client._filing_url("0000320193", "0000320193-26-000001", "test.htm")
        filing = SecFiling("0000320193-26-000001", "8-K", now, None,
                           "test.htm", valid_url, valid_url)
        self.assertEqual(b"<html>Source</html>", client.fetch_filing_document(
            filing, cik="0000320193",
        ))
        self.assertEqual([valid_url], urls)
        malicious = SecFiling("0000320193-26-000001", "8-K", now, None,
                              "../test.htm", valid_url, valid_url)
        with self.assertRaises(SecEdgarError):
            client.fetch_filing_document(malicious, cik="0000320193")
        wrong_host = SecFiling("0000320193-26-000001", "8-K", now, None,
                               "test.htm", valid_url.replace("www.sec.gov", "example.com"),
                               valid_url)
        with self.assertRaises(SecEdgarError):
            client.fetch_filing_document(wrong_host, cik="0000320193")

    def test_oversized_primary_document_does_not_retry(self) -> None:
        calls = 0

        def transport(url, headers, timeout):
            nonlocal calls
            calls += 1
            return b"x" * (MAX_SEC_DOCUMENT_BYTES + 1)

        client = SecEdgarClient(user_agent="Test tests@example.com", text_transport=transport)
        now = datetime.now(timezone.utc)
        url = client._filing_url("0000320193", "0000320193-26-000001", "test.htm")
        filing = SecFiling("0000320193-26-000001", "8-K", now, None,
                           "test.htm", url, url)
        with self.assertRaises(SecEdgarError):
            client.fetch_filing_document(filing, cik="0000320193")
        self.assertEqual(1, calls)


if __name__ == "__main__":
    unittest.main()
