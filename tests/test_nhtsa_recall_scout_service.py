from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from market_checker_app.services.nhtsa_recall_scout_service import (
    NhtsaRecallScoutService, load_verified_models,
)
from market_checker_app.storage.scout_store import ScoutStore


NOW = datetime(2026, 9, 30, 21, tzinfo=timezone.utc)
MODEL = {"ticker": "TSLA", "manufacturer": "Tesla, Inc.", "make": "TESLA",
         "model": "MODEL 3", "model_year": 2026,
         "product_evidence_url": "https://www.sec.gov/Archives/edgar/data/1318605/tesla-10k.htm",
         "known_at": "2026-09-30T20:27:48+00:00"}


class FakeRecalls:
    def __init__(self, rows=None, fail=False):
        self.rows = rows if rows is not None else [
            {"Make": "TESLA", "Model": "MODEL 3", "ModelYear": "2026",
             "Manufacturer": "Tesla, Inc.", "NHTSACampaignNumber": "26V123000",
             "ReportReceivedDate": "09/01/2026", "Component": "ELECTRICAL SYSTEM",
             "Summary": "A described model-year campaign", "parkIt": False},
        ]
        self.fail, self.calls = fail, []

    def recalls(self, make, model, model_year):
        self.calls.append((make, model, model_year))
        if self.fail:
            raise OSError("NHTSA temporary failure")
        return {"Count": len(self.rows), "results": self.rows}


class NhtsaRecallTests(unittest.TestCase):
    def test_live_day_month_date_and_rejected_response_retry(self):
        row = {"Make": "TESLA", "Model": "MODEL 3", "ModelYear": "2026",
               "Manufacturer": "Tesla, Inc.", "NHTSACampaignNumber": "25V410000",
               "ReportReceivedDate": "18/06/2025"}
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            client = FakeRecalls([row])
            service = NhtsaRecallScoutService(store, client=client, models=[MODEL])
            self.assertEqual(1, service.run(as_of=NOW)["new_findings"])
            with store._connect() as conn:
                details = json.loads(conn.execute("SELECT details_json FROM scout_findings").fetchone()[0])
            self.assertEqual("2025-06-18", details["report_received_date"])
            client.rows = [dict(row, ReportReceivedDate="09/29/2026")]
            partial = service.run(as_of=NOW + timedelta(days=31))
            self.assertEqual(("PARTIAL", 1), (partial["status"], partial["rejected_rows"]))
            client.rows = [dict(row, ReportReceivedDate="05/06/2025")]
            self.assertEqual(1, service.run(as_of=NOW + timedelta(days=32))["checked_models"])
            with store._connect() as conn:
                details = json.loads(conn.execute("SELECT details_json FROM scout_findings ORDER BY first_observed_at DESC").fetchone()[0])
            self.assertEqual("2025-06-05", details["report_received_date"])

    def test_exact_model_manufacturer_date_and_monthly_refresh(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "models.json"
            path.write_text(json.dumps([MODEL]), encoding="utf-8")
            store = ScoutStore(Path(directory) / "test.db")
            client = FakeRecalls()
            scout = NhtsaRecallScoutService(store, client=client, models=load_verified_models(path))
            self.assertEqual("WAIT_IDENTITY", scout.run(as_of=NOW - timedelta(hours=2))["status"])
            self.assertEqual(1, scout.run(as_of=NOW, universe={"TSLA"})["new_findings"])
            self.assertEqual(0, scout.run(as_of=NOW + timedelta(days=1))["checked_models"])
            self.assertEqual(1, len(client.calls))
            self.assertEqual(0, scout.run(as_of=NOW + timedelta(days=31))["new_findings"])
            self.assertEqual(2, len(client.calls))
            with store._connect() as conn:
                rows = conn.execute("SELECT source_url, published_at, details_json FROM scout_findings "
                                    "WHERE source='nhtsa'").fetchall()
            self.assertEqual(1, len(rows))
            details = json.loads(rows[0]["details_json"])
            self.assertEqual("26V123000", details["campaign"])
            self.assertFalse(details["issuer_financial_impact_verified"])
            self.assertEqual(NOW.isoformat(), rows[0]["published_at"])
            self.assertIn("modelYear=2026", rows[0]["source_url"])

    def test_failed_and_mismatched_rows_do_not_mark_false_finding(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            fail = NhtsaRecallScoutService(store, client=FakeRecalls(fail=True), models=[MODEL])
            self.assertEqual(("PARTIAL", 1), (fail.run(as_of=NOW)["status"],
                                               fail.run(as_of=NOW)["failed_models"]))
            mismatch = FakeRecalls([{"Make": "TESLA", "Model": "MODEL 3", "ModelYear": "2026",
                                     "Manufacturer": "Another Company", "NHTSACampaignNumber": "26V123000",
                                     "ReportReceivedDate": "09/01/2026"}])
            self.assertEqual(0, NhtsaRecallScoutService(store, client=mismatch,
                                models=[MODEL]).run(as_of=NOW)["new_findings"])

    def test_manifest_and_host_policy(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "models.json"
            path.write_text(json.dumps([dict(MODEL, product_evidence_url="https://example.org/product")]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SEC filing"):
                load_verified_models(path)
            store = ScoutStore(Path(directory) / "test.db")
            with self.assertRaisesRegex(ValueError, "official recall API"):
                store.record_finding(source="nhtsa", subject_id="TSLA", source_object_id="x",
                                     content_hash="x", title="recall", source_url="https://api.nhtsa.gov/other",
                                     locator="x", published_at=NOW, available_at=NOW,
                                     observed_at=NOW, details={})


if __name__ == "__main__":
    unittest.main()
