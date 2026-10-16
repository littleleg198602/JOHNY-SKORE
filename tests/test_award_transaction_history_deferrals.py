"""Real SQLite quota/lease transitions with synthetic HTTP inputs.

These tests exercise LIVE request accounting without making source requests;
their captured rows are synthetic unit-test data, never live acceptance.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from market_checker_app.services.award_transaction_history_service import (
    CAPTURE_VERSION, SOURCE_URL, AwardTransactionHistoryService,
    CapturedTransactionPage, UsaSpendingTransactionsClient,
)
from market_checker_app.services.usaspending_scout_service import _award_details
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.storage.usaspending_checkpoint_store import UsaSpendingCheckpointStore


CLOCK = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
AWARD_ID = "CONT_AWD_DEFERRAL_9700_PARENT_9700"
IDENTITY = {"ticker": "AAA", "uei": "ABCDEFGHIJKL", "recipient_name": "AAA INC",
            "uei_evidence_url": "https://www.usaspending.gov/award/example",
            "relationship_evidence_url": "https://www.sec.gov/Archives/edgar/data/example",
            "effective_from": "2020-01-01", "effective_to": None,
            "known_at": "2026-09-01T00:00:00+00:00"}


def first_page():
    transaction = {"id": "CONT_TX_DEFERRAL", "type": "C", "type_description": "DELIVERY ORDER",
                   "action_date": "2026-09-01", "action_type": "C", "action_type_description": "FUNDING ONLY ACTION",
                   "modification_number": "P00001", "description": "Synthetic deferral test",
                   "federal_action_obligation": -5, "face_value_loan_guarantee": 0,
                   "original_loan_subsidy_cost": 0}
    body = json.dumps({"results": [transaction], "page_metadata": {
        "page": 1, "next": 2, "previous": None, "hasNext": True, "hasPrevious": False}}).encode()
    return CapturedTransactionPage(body, {
        "schema_version": CAPTURE_VERSION, "capture_mode": "LIVE", "source_url": SOURCE_URL,
        "source_url_final": SOURCE_URL, "method": "POST",
        "request": {"award_id": AWARD_ID, "page": 1, "limit": 100, "sort": "action_date", "order": "asc"},
        "request_started_at": (CLOCK - timedelta(seconds=2)).isoformat(),
        "response_completed_at": (CLOCK - timedelta(seconds=1)).isoformat(),
        "http_status": 200, "response_headers": {}, "complete_response": True,
        "response_bytes": len(body), "response_sha256": hashlib.sha256(body).hexdigest(),
    })


class AwardTransactionHistoryDeferralTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.store = ScoutStore(self.directory / "scout.db")
        self.ledger = UsaSpendingCheckpointStore(self.store)
        self.client = UsaSpendingTransactionsClient()
        previously_observed = CLOCK - timedelta(days=1)
        details = _award_details({"generated_internal_id": AWARD_ID, "Award ID": "DEFERRAL",
                                  "Recipient UEI": IDENTITY["uei"], "Recipient Name": IDENTITY["recipient_name"],
                                  "Start Date": "2026-08-01", "Award Amount": 1000},
                                 IDENTITY, clock=previously_observed, state={"query_end": previously_observed.date().isoformat()})
        self.finding_id = self.store.record_finding(
            source="usaspending", subject_id="AAA", source_object_id=IDENTITY["uei"] + ":" + AWARD_ID,
            content_hash=hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest(),
            title="Synthetic prime award for request-accounting tests",
            source_url="https://api.usaspending.gov/api/v2/search/spending_by_award/", locator="synthetic-test",
            published_at=previously_observed, available_at=previously_observed,
            observed_at=previously_observed, details=details)[0]

    def run_service(self, *, daily_limit=120):
        return AwardTransactionHistoryService(
            self.store, archive_directory=self.directory / "captures", client=self.client,
            identities=[IDENTITY], max_pages=2, max_requests=2, max_run_seconds=30,
            daily_request_limit=daily_limit,
        ).run(subject_id="AAA", award_finding_id=self.finding_id, as_of=CLOCK)

    def request_rows(self):
        with self.store._connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM scout_usaspending_requests ORDER BY rowid")]

    def assert_deferral(self, result, reason, *, accepted=0):
        self.assertEqual(reason, result["status"])
        self.assertEqual(reason, result["stop_reason"])
        self.assertNotIn("error", result)
        self.assertFalse(result["traversal_complete"])
        self.assertEqual(accepted, result["accepted_rows"])
        self.assertEqual(accepted, result["new_findings"])
        self.assertEqual(accepted, result["captured_pages"])
        self.assertEqual(accepted, result["attempted_requests"])
        self.assertEqual(accepted, len(result["source_transactions"]))
        self.assertEqual(accepted, len(result["capture_manifests"]))
        if accepted:
            summary = result["source_transactions"][0]
            self.assertEqual("CONT_TX_DEFERRAL", summary["transaction_id"])
            stored = self.store.finding_by_id(summary["finding_id"], subject_id="AAA")
            self.assertIsNotNone(stored)
            self.assertEqual(-5, json.loads(stored["details_json"])["reported_transaction_obligation_usd"])
            manifest = json.loads(Path(result["capture_manifests"][0]).read_bytes())
            self.assertEqual(first_page().body, (self.directory / "captures" / manifest["body_file"]).read_bytes())

    def test_exhausted_shared_recipient_quota_before_first_page_preserves_wait_quota(self):
        token = self.store.claim_provider("usaspending", as_of=CLOCK, seconds=60)
        reservation = self.ledger.reserve("recipients", "other-lane", "BBB", page=1,
                                          as_of=CLOCK, daily_limit=1, lease_token=token)
        self.assertEqual("RESERVED", reservation["status"])
        self.ledger.finish(reservation["request_id"], outcome="RETURNED")
        self.store.release_provider("usaspending", token)
        with patch.object(self.client, "transactions") as fetch:
            result = self.run_service(daily_limit=1)
        fetch.assert_not_called()
        self.assert_deferral(result, "WAIT_QUOTA")
        self.assertEqual(1, result["daily_requests_used"])
        self.assertEqual([("recipients", "RETURNED")], [(row["kind"], row["outcome"]) for row in self.request_rows()])

    def test_quota_deferral_after_first_page_keeps_rows_and_does_not_call_second_page(self):
        with patch.object(self.client, "transactions", return_value=first_page()) as fetch:
            result = self.run_service(daily_limit=1)
        self.assertEqual(1, fetch.call_count)
        self.assert_deferral(result, "WAIT_QUOTA", accepted=1)
        self.assertEqual(1, result["daily_requests_used"])
        self.assertEqual([(1, "RETURNED")], [(row["page"], row["outcome"]) for row in self.request_rows()])

    def test_real_lease_loss_before_reservation_preserves_lease_lost_without_io(self):
        original_claim = self.store.claim_provider

        def claim_then_lose(source, *, as_of, seconds):
            token = original_claim(source, as_of=as_of, seconds=seconds)
            self.assertIsNotNone(token)
            self.store.release_provider(source, token)
            return token

        with patch.object(self.store, "claim_provider", side_effect=claim_then_lose), \
             patch.object(self.client, "transactions") as fetch:
            result = self.run_service()
        fetch.assert_not_called()
        self.assert_deferral(result, "LEASE_LOST")
        self.assertEqual(0, result["daily_requests_used"])
        self.assertEqual([], self.request_rows())

    def test_real_lease_loss_after_first_page_keeps_rows_and_stops_second_io(self):
        def fetch_then_lose(*args, **kwargs):
            with self.store._connect() as connection:
                token = connection.execute("SELECT lease_token FROM scout_provider_leases WHERE source='usaspending'").fetchone()[0]
            self.store.release_provider("usaspending", token)
            return first_page()

        with patch.object(self.client, "transactions", side_effect=fetch_then_lose) as fetch:
            result = self.run_service()
        self.assertEqual(1, fetch.call_count)
        self.assert_deferral(result, "LEASE_LOST", accepted=1)
        self.assertEqual(1, result["daily_requests_used"])
        self.assertEqual([(1, "RETURNED")], [(row["page"], row["outcome"]) for row in self.request_rows()])


if __name__ == "__main__":
    unittest.main()
