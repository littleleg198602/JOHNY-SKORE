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
            self.assertEqual(1, len(store.findings_as_of(
                "AAPL", as_of=cutoff, actual_observation=False
            )))
            rows = store.findings_as_of("AAPL", as_of=seen + timedelta(days=1))
            self.assertEqual(identifier, rows[0]["finding_id"])
            self.assertEqual(seen.isoformat(), rows[0]["first_observed_at"])

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


if __name__ == "__main__":
    unittest.main()
