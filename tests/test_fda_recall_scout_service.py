from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from market_checker_app.services.fda_recall_scout_service import FdaRecallScoutService
from market_checker_app.storage.scout_store import ScoutStore


NOW = datetime(2026, 9, 30, 16, tzinfo=timezone.utc)


class FakeFda:
    def __init__(self, fail: bool = False):
        self.calls = []
        self.fail = fail

    def recalls(self, product_type, firm_name, *, limit):
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

    def complete_response_letters(self, firm_name, *, limit):
        self.calls.append(("crl", firm_name, limit))
        if self.fail:
            raise OSError("temporary API failure")
        return {"meta": {"results": {"total": 0}}, "results": []}


class FakeCrlFda(FakeFda):
    def complete_response_letters(self, firm_name, *, limit):
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
