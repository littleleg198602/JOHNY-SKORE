from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from market_checker_app.storage.scout_store import ScoutStore


class ScoutStoreTests(unittest.TestCase):
    def test_recovery_dedup_and_stale_worker_cannot_complete_new_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scout.db"
            first_store = ScoutStore(path)
            now = datetime.now(timezone.utc)
            original_id = first_store.enqueue(
                source="sec", subject_id="AAPL", reason="daily",
                due_at=now,
            )
            self.assertEqual(original_id, first_store.enqueue(
                source="sec", subject_id="AAPL", reason="daily", due_at=now,
            ))
            original = first_store.lease(as_of=now, seconds=60)
            self.assertIsNotNone(original)
            second_store = ScoutStore(path)
            self.assertIsNone(second_store.lease(as_of=now + timedelta(seconds=10)))
            recovered = second_store.lease(as_of=now + timedelta(seconds=61))
            self.assertEqual(2, recovered.attempts)
            self.assertFalse(first_store.finish(original, as_of=now + timedelta(seconds=62)))
            self.assertTrue(second_store.finish(recovered, as_of=now + timedelta(seconds=62)))
            self.assertEqual({"DONE": 1}, first_store.metrics())

    def test_as_of_requires_actual_observation_and_keeps_first_seen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            published = datetime(2026, 1, 1, tzinfo=timezone.utc)
            seen = published + timedelta(days=2)
            parameters = dict(
                source="sec", subject_id="AAPL", source_object_id="filing:123",
                content_hash="sha256:abc", title="Filing", source_url="https://www.sec.gov/filing",
                locator="page 1", published_at=published, available_at=published,
                details={"form": "10-K"},
            )
            identifier, created = store.record_finding(**parameters, observed_at=seen)
            self.assertTrue(created)
            self.assertFalse(store.record_finding(
                **parameters, observed_at=seen + timedelta(days=1)
            )[1])
            cutoff = published + timedelta(days=1)
            self.assertEqual([], store.findings_as_of("AAPL", as_of=cutoff))
            self.assertFalse(store.has_findings(["AAPL"], as_of=cutoff))
            self.assertEqual(1, len(store.findings_as_of(
                "AAPL", as_of=cutoff, actual_observation=False
            )))
            rows = store.findings_as_of("AAPL", as_of=seen + timedelta(days=1))
            self.assertEqual(identifier, rows[0]["finding_id"])
            self.assertEqual(seen.isoformat(), rows[0]["first_observed_at"])
            self.assertTrue(store.has_findings(["AAPL"], as_of=seen))

    def test_leads_are_bounded_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            now = datetime.now(timezone.utc)
            finding, _ = store.record_finding(
                source="sec", subject_id="MU", source_object_id="abc",
                content_hash="hash", title="Filing", source_url="https://www.sec.gov/a",
                locator="section", published_at=now, available_at=now,
                observed_at=now, details={},
            )
            parent = store.add_lead(subject_id="MU", finding_id=finding,
                                    question="Inventory?", as_of=now)
            self.assertEqual(parent, store.add_lead(subject_id="MU", finding_id=finding,
                                                   question="Inventory?", as_of=now))
            child = store.add_lead(subject_id="MU", finding_id=finding,
                                   question="Customer demand?", as_of=now,
                                   parent_lead_id=parent)
            grandchild = store.add_lead(subject_id="MU", finding_id=finding,
                                        question="Sector demand?", as_of=now,
                                        parent_lead_id=child)
            with self.assertRaises(ValueError):
                store.add_lead(subject_id="MU", finding_id=finding,
                               question="More?", as_of=now,
                               parent_lead_id=grandchild)
            self.assertEqual([], store.open_leads(["MU"], as_of=now - timedelta(seconds=1)))
            questions = store.open_leads(["MU"], as_of=now)
            self.assertEqual(3, len(questions))
            self.assertEqual({"Inventory?", "Customer demand?", "Sector demand?"},
                             {row["question"] for row in questions})
            self.assertEqual([], store.open_leads(["AAPL"], as_of=now))

    def test_two_processes_do_not_share_sec_rate_limit_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scout.db"
            first = ScoutStore(path)
            second = ScoutStore(path)
            now = datetime.now(timezone.utc)
            token = first.claim_provider("sec", as_of=now, seconds=30)
            self.assertIsNotNone(token)
            self.assertIsNone(second.claim_provider("sec", as_of=now))
            first.release_provider("sec", "incorrect-token")
            self.assertIsNone(second.claim_provider("sec", as_of=now))
            first.release_provider("sec", token)
            self.assertIsNotNone(second.claim_provider("sec", as_of=now))

    def test_provider_cooldown_survives_process_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scout.db"
            first = ScoutStore(path)
            now = datetime.now(timezone.utc)
            token = first.claim_provider("sec", as_of=now)
            self.assertTrue(first.defer_provider(
                "sec", token, as_of=now, retry_after=timedelta(hours=1),
                reason="SEC Retry-After",
            ))
            first.release_provider("sec", token)
            restarted = ScoutStore(path)
            self.assertIsNone(restarted.claim_provider("sec", as_of=now + timedelta(minutes=30)))
            self.assertEqual((now + timedelta(hours=1)).isoformat(),
                             restarted.provider_retry_at("sec", as_of=now))
            self.assertIsNotNone(restarted.claim_provider(
                "sec", as_of=now + timedelta(hours=1),
            ))

    def test_source_leases_cannot_steal_other_provider_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            now = datetime.now(timezone.utc)
            store.enqueue(source="fda", subject_id="AAPL", reason="events", due_at=now)
            store.enqueue(source="sec", subject_id="MSFT", reason="filings", due_at=now)
            job = store.lease(source="sec", as_of=now)
            self.assertEqual("sec", job.source)
            self.assertEqual("fda", store.lease(source="fda", as_of=now).source)
            self.assertIsNone(store.lease(source="sec", as_of=now))
            with store._connect() as conn:
                self.assertEqual(1, conn.execute(
                    "SELECT COUNT(*) FROM scout_schema_migrations WHERE version=1"
                ).fetchone()[0])

    def test_provider_lease_renews_only_for_current_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            now = datetime.now(timezone.utc)
            token = store.claim_provider("sec", as_of=now, seconds=30)
            self.assertFalse(store.renew_provider("sec", "other", as_of=now))
            self.assertTrue(store.renew_provider(
                "sec", token, as_of=now + timedelta(seconds=20), seconds=30,
            ))
            self.assertIsNone(store.claim_provider(
                "sec", as_of=now + timedelta(seconds=40), seconds=30,
            ))

    def test_analysis_snapshot_is_immutable_and_historical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            now = datetime.now(timezone.utc)
            finding, _ = store.record_finding(
                source="sec", subject_id="AAPL", source_object_id="index",
                content_hash="abc", title="Test", source_url="https://www.sec.gov/",
                locator="accession:test", published_at=now, available_at=now,
                observed_at=now, details={},
            )
            with self.assertRaises(ValueError):
                store.record_analysis_snapshot("old", as_of=now - timedelta(days=1),
                                               finding_ids=[finding])
            store.record_analysis_snapshot("run1", as_of=now, finding_ids=[finding])
            store.record_analysis_snapshot("run1", as_of=now, finding_ids=[finding])
            self.assertEqual([finding], store.analysis_snapshot("run1")["finding_ids"])
            with self.assertRaises(ValueError):
                store.record_analysis_snapshot("run1", as_of=now + timedelta(minutes=1),
                                               finding_ids=[finding])

    def test_lead_transitions_preserve_asof_status_and_require_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            now = datetime.now(timezone.utc)
            finding, _ = store.record_finding(
                source="sec", subject_id="AAPL", source_object_id="a",
                content_hash="a", title="Source", source_url="https://www.sec.gov/a",
                locator="Item 2.02", published_at=now, available_at=now,
                observed_at=now, details={},
            )
            lead = store.add_lead(subject_id="AAPL", finding_id=finding,
                                  question="Impact?", as_of=now)
            later = now + timedelta(hours=1)
            store.advance_lead(lead, status="INVESTIGATING", as_of=later,
                               reason="Read the source")
            self.assertEqual("OPEN", store.open_leads(["AAPL"], as_of=now)[0]["status"])
            self.assertEqual("INVESTIGATING", store.open_leads(
                ["AAPL"], as_of=later,
            )[0]["status"])
            with self.assertRaises(ValueError):
                store.advance_lead(lead, status="VERIFIED", as_of=later,
                                   reason="No evidence")
            with self.assertRaises(ValueError):
                store.advance_lead(lead, status="VERIFIED", as_of=later,
                                   reason="Other issuer", evidence_for=("missing",))
            with self.assertRaises(ValueError):
                store.advance_lead(lead, status="VERIFIED", as_of=later,
                                   reason="Only a source document", evidence_for=(finding,))
            claim, _ = store.record_finding(
                source="sec", subject_id="AAPL", source_object_id="claim:a",
                content_hash="claim:a", title="Verified claim",
                source_url="https://www.sec.gov/a", locator="Item 2.02",
                published_at=now, available_at=later, observed_at=later,
                verification_status="CLAIM_VERIFIED",
                details={"claim_text": "Specific claim", "verification_method": "source_match"},
            )
            store.advance_lead(lead, status="VERIFIED", as_of=later,
                               reason="Verified with cited claim", evidence_for=(claim,))
            self.assertEqual([], store.open_leads(["AAPL"], as_of=later))
            self.assertEqual([], store.closed_leads(["AAPL"], as_of=now))
            self.assertEqual("VERIFIED", store.closed_leads(
                ["AAPL"], as_of=later,
            )[0]["status"])
            self.assertEqual("OPEN", store.open_leads(["AAPL"], as_of=now)[0]["status"])


if __name__ == "__main__":
    unittest.main()
