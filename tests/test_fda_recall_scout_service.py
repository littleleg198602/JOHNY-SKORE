from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from io import BytesIO
from urllib.error import HTTPError

from market_checker_app.services.fda_recall_scout_service import (
    FdaRecallScoutService, OpenFdaRecallClient, MAX_RESPONSE_BYTES,
)
from market_checker_app.storage.scout_store import ScoutStore


NOW = datetime(2026, 9, 30, 16, tzinfo=timezone.utc)


class FakeFda:
    def __init__(self, fail: bool = False):
        self.calls = []
        self.fail = fail

    def recalls(self, product_type, firm_name, *, limit, skip=0):
        self.calls.append((product_type, firm_name, limit))
        if self.fail:
            raise OSError("temporary API failure")
        if product_type == "drug":
            return {"meta": {"results": {"total": 2}}, "results": [
                {"recalling_firm": firm_name, "recall_number": "D-111-2026",
                 "report_date": "20260929", "product_description": "Test product"},
                {"recalling_firm": "Another Company", "recall_number": "D-222-2026",
                 "report_date": "20260929"},
            ]}
        return {"meta": {"results": {"total": 0}}, "results": []}

    def complete_response_letters(self, firm_name, *, limit, skip=0):
        self.calls.append(("crl", firm_name, limit))
        if self.fail:
            raise OSError("temporary API failure")
        return {"meta": {"results": {"total": 0}}, "results": []}


class FakeCrlFda(FakeFda):
    def complete_response_letters(self, firm_name, *, limit, skip=0):
        self.calls.append(("crl", firm_name, limit))
        return {"meta": {"results": {"total": 4}}, "results": [
            {"company_name": firm_name, "letter_type": "COMPLETE RESPONSE",
             "application_number": "NDA 123456", "file_name": "letter.pdf",
             "letter_date": "09/20/2025"},
            {"company_name": "Similar Other Inc.", "letter_type": "COMPLETE RESPONSE",
             "application_number": "NDA 987654", "file_name": "other.pdf",
             "letter_date": "09/20/2025"},
            {"company_name": firm_name, "letter_type": "APPROVAL",
             "application_number": "NDA 543210", "file_name": "approval.pdf",
             "letter_date": "09/20/2025"},
            {"company_name": firm_name, "letter_type": "COMPLETE RESPONSE",
             "application_number": "NDA 234567", "file_name": "future.pdf",
             "letter_date": "10/20/2026"},
        ]}


class FdaScoutTests(unittest.TestCase):
    def test_deadline_retains_previous_issuer_and_does_not_complete_interrupted_issuer(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            for index in range(3):
                store.observe_sec_identity(subject_id=f"T{index}", cik=f"{index+1:010d}",
                                           company_name=f"Company {index} Inc.", as_of=NOW)
            client = FakeFda()
            with patch("market_checker_app.services.fda_recall_scout_service.time.monotonic",
                       side_effect=[0, 0, 0, 1, 2, 3, 4, 4, 6]):
                result = FdaRecallScoutService(store, client=client, max_run_seconds=5).run(as_of=NOW)
            self.assertEqual(("PARTIAL", 1, 1, True),
                             (result["status"], result["checked_issuers"], result["failed_issuers"],
                              result["budget_exhausted"]))
            self.assertEqual(1, len(store.latest_findings(["T0"], as_of=NOW, source="fda")))
            reopened = ScoutStore(store.db_path)
            self.assertEqual(["T1", "T2"], [r["subject_id"] for r in reopened.specialist_due("fda", as_of=NOW, limit=10)])
            self.assertEqual([], reopened.latest_findings(["T1", "T2"], as_of=NOW, source="fda"))

    def test_failure_limit_prevents_repeating_every_failed_issuer(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            for index in range(5):
                store.observe_sec_identity(subject_id=f"T{index}", cik=f"{index+1:010d}",
                                           company_name=f"Company {index} Inc.", as_of=NOW)
            client = FakeFda(fail=True)
            result = FdaRecallScoutService(store, client=client, max_failures=2).run(as_of=NOW)
            self.assertEqual(("PARTIAL", 2, True),
                             (result["status"], result["failed_issuers"], result["budget_exhausted"]))
            self.assertEqual(2, len(client.calls))
            self.assertEqual(0, store.specialist_coverage("fda", as_of=NOW)["ever_checked"])

    def test_rejected_exact_firm_row_cannot_be_a_complete_negative(self):
        class MalformedFda(FakeFda):
            def recalls(self, product_type, firm_name, *, limit, skip=0):
                if product_type == "drug":
                    return {"meta": {"results": {"total": 1}}, "results": [
                        {"recalling_firm": firm_name, "recall_number": "D-1", "report_date": "20260929extra"}]}
                return {"meta": {"results": {"total": 0}}, "results": []}
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            store.observe_sec_identity(subject_id="ONE", cik="0000000001", company_name="One Inc.", as_of=NOW)
            result = FdaRecallScoutService(store, client=MalformedFda()).run(as_of=NOW)
            self.assertEqual(("PARTIAL", 1, 0), (result["status"], result["rejected_rows"], result["new_findings"]))
            self.assertEqual(0, store.specialist_coverage("fda", as_of=NOW)["current_complete"])
            self.assertEqual(1, len(store.specialist_due("fda", as_of=NOW+timedelta(days=1), limit=10)))

    def test_only_documented_no_match_404_counts_as_empty(self):
        for body, empty in ((b'{"error":{"code":"NOT_FOUND","message":"No matches found!"}}', True),
                            (b'{"error":{"code":"NOT_FOUND","message":"Unknown endpoint"}}', False),
                            (b'<html>Not found</html>', False)):
            with self.subTest(body=body):
                error = HTTPError("https://api.fda.gov/drug/enforcement.json", 404, "Not Found", {}, BytesIO(body))
                with patch("market_checker_app.services.fda_recall_scout_service.urlopen", side_effect=error):
                    client = OpenFdaRecallClient()
                    if empty:
                        self.assertEqual([], client.recalls("drug", "One Inc.", limit=1)["results"])
                    else:
                        with self.assertRaises(HTTPError):
                            client.recalls("drug", "One Inc.", limit=1)

    def test_response_byte_budget_and_invalid_budgets(self):
        with patch("market_checker_app.services.fda_recall_scout_service.urlopen") as opened:
            opened.return_value.__enter__.return_value.geturl.return_value = "https://api.fda.gov/drug/enforcement.json"
            opened.return_value.__enter__.return_value.read.return_value = b" " * (MAX_RESPONSE_BYTES+1)
            with self.assertRaisesRegex(ValueError, "byte budget"):
                OpenFdaRecallClient().recalls("drug", "One Inc.", limit=1)
        for options in ({"max_run_seconds": float("inf")}, {"max_run_seconds": True},
                        {"max_failures": 0}, {"max_subjects": 1000}, {"max_pages": True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                FdaRecallScoutService(None, client=FakeFda(), **options)

    def test_second_page_is_collected_and_page_cap_remains_partial(self):
        class PagedFda(FakeFda):
            def recalls(self, product_type, firm_name, *, limit, skip=0):
                self.calls.append((product_type, skip))
                if product_type != "drug":
                    return {"meta": {"results": {"total": 0, "skip": skip}}, "results": []}
                return {"meta": {"results": {"total": 3, "skip": skip}}, "results": [
                    {"recalling_firm": firm_name, "recall_number": f"D-{skip + 1}-2026",
                     "report_date": "20260929"},
                ]}

        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            store.observe_sec_identity(subject_id="TEST", cik="0000000001",
                                       company_name="Example Pharma Inc.", as_of=NOW)
            client = PagedFda()
            first = FdaRecallScoutService(store, client=client, max_results=1,
                                          max_pages=2).run(as_of=NOW)
            self.assertEqual(("PARTIAL", 2, 1),
                             (first["status"], first["new_findings"], first["truncated_issuers"]))
            self.assertIn(("drug", 1), client.calls)
            complete = FdaRecallScoutService(store, client=client, max_results=1,
                                             max_pages=3).run(as_of=NOW + timedelta(days=1))
            self.assertEqual(("OK", 1), (complete["status"], complete["new_findings"]))
            self.assertEqual(1, store.specialist_coverage(
                "fda", as_of=NOW + timedelta(days=1))["current_complete"])
            with store._connect() as conn:
                self.assertEqual(3, conn.execute(
                    "SELECT COUNT(*) FROM scout_findings WHERE source='fda'").fetchone()[0])

    def test_food_enforcement_is_a_separate_unverified_candidate(self):
        class FoodFda(FakeFda):
            def recalls(self, product_type, firm_name, *, limit, skip=0):
                if product_type != "food":
                    return {"meta": {"results": {"total": 0}}, "results": []}
                return {"meta": {"results": {"total": 2}}, "results": [
                    {"recalling_firm": firm_name, "recall_number": "F-123-2026",
                     "report_date": "20260930", "product_description": "Food product"},
                    {"recalling_firm": "Another Company", "recall_number": "F-124-2026",
                     "report_date": "20260930"},
                ]}

        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            store.observe_sec_identity(subject_id="FOOD", cik="0000000002",
                                       company_name="Example Foods Inc.", as_of=NOW)
            summary = FdaRecallScoutService(store, client=FoodFda()).run(as_of=NOW)
            self.assertEqual(1, summary["new_findings"])
            with store._connect() as conn:
                row = conn.execute("SELECT source_object_id, verification_status, details_json "
                                   "FROM scout_findings WHERE source='fda'").fetchone()
            self.assertEqual("food:F-123-2026", row["source_object_id"])
            self.assertEqual("UNVERIFIED", row["verification_status"])
            self.assertFalse(json.loads(row["details_json"])["product_attribution_allowed"])

    def test_crl_name_match_is_candidate_with_observation_time(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            store.observe_sec_identity(subject_id="TEST", cik="0000000001",
                                       company_name="Example Pharma Inc.", as_of=NOW)
            summary = FdaRecallScoutService(store, client=FakeCrlFda()).run(as_of=NOW)
            self.assertEqual(2, summary["new_findings"])
            with store._connect() as conn:
                rows = conn.execute("SELECT source_object_id, published_at, verification_status, "
                                    "details_json FROM scout_findings WHERE source='fda' "
                                    "AND source_object_id LIKE 'crl:%'").fetchall()
            self.assertEqual(1, len(rows))
            self.assertEqual(NOW.isoformat(), rows[0]["published_at"])
            self.assertEqual("UNVERIFIED", rows[0]["verification_status"])
            detail = json.loads(rows[0]["details_json"])
            self.assertEqual("09/20/2025", detail["letter_date"])
            self.assertFalse(detail["letter_date_is_publication_time"])
            self.assertFalse(detail["product_attribution_allowed"])

    def test_exact_name_is_only_unverified_candidate_and_rotates(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            for index in range(3):
                store.observe_sec_identity(
                    subject_id=f"T{index}", cik=f"{index:010d}",
                    company_name=f"Company {index} Inc.", as_of=NOW,
                )
            client = FakeFda()
            scout = FdaRecallScoutService(store, client=client, max_subjects=2)
            first = scout.run(as_of=NOW)
            self.assertEqual((2, 2), (first["checked_issuers"], first["new_findings"]))
            second = scout.run(as_of=NOW)
            self.assertEqual((1, 1), (second["checked_issuers"], second["new_findings"]))
            self.assertEqual(0, scout.run(as_of=NOW)["checked_issuers"])
            with store._connect() as conn:
                rows = conn.execute("SELECT verification_status, published_at, details_json "
                                    "FROM scout_findings WHERE source='fda'").fetchall()
            self.assertEqual(3, len(rows))
            self.assertTrue(all(row["verification_status"] == "UNVERIFIED" for row in rows))
            self.assertTrue(all(row["published_at"] == NOW.isoformat() for row in rows))
            self.assertTrue(all(not json.loads(row["details_json"])["product_attribution_allowed"]
                                for row in rows))
            self.assertEqual(2, scout.run(as_of=NOW + timedelta(days=31))["checked_issuers"])

    def test_failed_source_does_not_mark_issuer_checked(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            store.observe_sec_identity(subject_id="AAPL", cik="0000320193",
                                       company_name="Apple Inc.", as_of=NOW)
            failing = FdaRecallScoutService(store, client=FakeFda(fail=True))
            self.assertEqual(1, failing.run(as_of=NOW)["failed_issuers"])
            self.assertEqual(1, FdaRecallScoutService(store, client=FakeFda()).run(
                as_of=NOW)["checked_issuers"])

    def test_fda_source_policy_rejects_foreign_host_and_verified_identity(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            kwargs = dict(source="fda", subject_id="AAPL", source_object_id="drug:D-1",
                          content_hash="hash", title="candidate", locator="recall_number:D-1",
                          published_at=NOW, available_at=NOW, observed_at=NOW,
                          details={})
            with self.assertRaisesRegex(ValueError, "official API"):
                store.record_finding(source_url="https://example.org/recall", **kwargs)
            with self.assertRaisesRegex(ValueError, "cannot verify"):
                store.record_finding(source_url="https://api.fda.gov/drug/enforcement.json",
                                     **kwargs)


if __name__ == "__main__":
    unittest.main()
