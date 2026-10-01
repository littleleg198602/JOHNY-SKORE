from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.scout_runner import run as run_scout
from market_checker_app.utils.ticker_universe import load_canonical_ticker_records


class ScoutStoreTests(unittest.TestCase):
    def test_specialist_coverage_distinguishes_current_partial_and_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            now = datetime(2026, 10, 1, tzinfo=timezone.utc)
            for ticker, cik in (("AAPL", "0000320193"), ("MSFT", "0000789019"),
                                ("NVDA", "0001045810"), ("JPM", "0000019617")):
                store.observe_sec_identity(subject_id=ticker, cik=cik,
                                           company_name=f"{ticker} Inc.",
                                           as_of=now - timedelta(days=40))
            store.record_specialist_check("fda", subject_id="AAPL",
                identity_key="0000320193:AAPL Inc.", as_of=now,
                candidate_count=0, truncated=False)
            store.record_specialist_check("fda", subject_id="MSFT",
                identity_key="0000789019:MSFT Inc.", as_of=now,
                candidate_count=100, truncated=True)
            store.record_specialist_check("fda", subject_id="NVDA",
                identity_key="0001045810:NVDA Inc.",
                as_of=now - timedelta(days=31), candidate_count=0, truncated=False)
            # A check attached to a different name must not count for JPM.
            store.record_specialist_check("fda", subject_id="JPM",
                identity_key="0000019617:Old Name", as_of=now,
                candidate_count=0, truncated=False)
            self.assertEqual({"active_identities": 4, "ever_checked": 3,
                              "current_complete": 1, "current_partial": 1,
                              "not_current": 2},
                             store.specialist_coverage("fda", as_of=now))
            self.assertEqual({"active_identities": 4, "ever_checked": 0,
                              "current_complete": 0, "current_partial": 0,
                              "not_current": 4},
                             store.specialist_coverage("finra", as_of=now))

    def test_runner_keeps_completed_source_status_when_later_source_stops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scout.db"
            with patch.dict("os.environ", {"JOHNY_SKORE_FRED_API_KEY": "dummy",
                                        "JOHNY_SKORE_SEC_USER_AGENT": ""}):
                with patch("market_checker_app.scout_runner.FredScoutService.run",
                           side_effect=KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        run_scout(db_path=path, limit=0)
            rows = ScoutStore(path).latest_source_runs()
            self.assertEqual("WAIT_ACCESS", rows["sec"]["status"])
            self.assertNotIn("fred", rows)

    def test_source_run_status_survives_restart_and_latest_wins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scout.db"
            now = datetime(2026, 9, 30, tzinfo=timezone.utc)
            store = ScoutStore(path)
            store.record_source_run("finra", as_of=now,
                                    summary={"status": "WAIT_ACCESS", "new_findings": 0})
            store.record_source_run("finra", as_of=now + timedelta(days=1),
                                    summary={"status": "OK", "checked_issuers": 25,
                                             "new_findings": 3, "credential": "never-persist"})
            store.record_source_run("fdic", as_of=now, summary={"status": "WAIT_IDENTITY"})
            rows = ScoutStore(path).latest_source_runs()
            self.assertEqual({"finra", "fdic"}, set(rows))
            self.assertEqual("OK", rows["finra"]["status"])
            self.assertEqual(25, rows["finra"]["checked_issuers"])
            self.assertNotIn("credential", rows["finra"])
            self.assertEqual("WAIT_IDENTITY", rows["fdic"]["status"])

    def test_runner_rejects_changed_input_and_shows_positions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scout.db"
            with patch.dict("os.environ", {"JOHNY_SKORE_SEC_USER_AGENT": ""}):
                first = run_scout(db_path=path, limit=0)
                self.assertEqual("CREATED", first["universe_snapshot"]["status"])
                self.assertEqual("UNCHANGED", run_scout(
                    db_path=path, limit=0,
                )["universe_snapshot"]["status"])
                changed = load_canonical_ticker_records()
                changed[0], changed[1] = changed[1], changed[0]
                with patch("market_checker_app.scout_runner.load_canonical_ticker_records",
                           side_effect=[ValueError("Unapproved CSV digest"), changed]):
                    with self.assertRaisesRegex(ValueError, "Změněných pozic: 2"):
                        run_scout(db_path=path, limit=0)
            with ScoutStore(path)._connect() as conn:
                self.assertEqual(687, conn.execute(
                    "SELECT COUNT(*) FROM scout_universe_input_rows"
                ).fetchone()[0])

    def test_universe_rows_are_immutable_and_reordering_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            now = datetime.now(timezone.utc)
            rows = [{"ticker": "GOOG", "yahoo_ticker": "GOOG"},
                    {"ticker": "GOOGL", "yahoo_ticker": "GOOGL"}]
            first = store.record_universe_snapshot(
                source_name="export.xlsx", source_sha256="a" * 64,
                records=rows, as_of=now,
            )
            self.assertEqual("CREATED", first["status"])
            self.assertEqual("UNCHANGED", store.record_universe_snapshot(
                source_name="export.xlsx", source_sha256="a" * 64,
                records=rows, as_of=now + timedelta(days=1),
            )["status"])
            reordered = store.record_universe_snapshot(
                source_name="export.xlsx", source_sha256="b" * 64,
                records=list(reversed(rows)), as_of=now + timedelta(days=2),
            )
            self.assertEqual("CHANGED", reordered["status"])
            self.assertEqual([1, 2], [item["position"] for item in reordered["changes"]])
            self.assertEqual([], store.preview_universe_changes(list(reversed(rows))))
            self.assertEqual([1, 2], [item["position"] for item in
                             store.preview_universe_changes(rows)])
            with store._connect() as conn:
                original = conn.execute(
                    "SELECT ticker FROM scout_universe_input_rows "
                    "WHERE snapshot_id=? ORDER BY input_position", (first["snapshot_id"],),
                ).fetchall()
                self.assertEqual(["GOOG", "GOOGL"], [row[0] for row in original])
                self.assertEqual(2, conn.execute(
                    "SELECT COUNT(*) FROM scout_universe_snapshots"
                ).fetchone()[0])
            with self.assertRaises(ValueError):
                store.record_universe_snapshot(
                    source_name="export.xlsx", source_sha256="a" * 64,
                    records=list(reversed(rows)), as_of=now,
                )

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

    def test_future_publication_cannot_enter_historical_findings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            observed = datetime(2026, 9, 29, tzinfo=timezone.utc)
            with self.assertRaisesRegex(ValueError, "before publication"):
                store.record_finding(
                    source="rss", subject_id="AAPL", source_object_id="future",
                    content_hash="future", title="Future article",
                    source_url="https://example.com/future", locator="rss:title",
                    published_at=observed + timedelta(days=1),
                    available_at=observed, observed_at=observed,
                    verification_status="UNVERIFIED", details={},
                )
            self.assertEqual([], store.findings_as_of(
                "AAPL", as_of=observed + timedelta(days=2),
            ))

    def test_storage_source_policy_rejects_forged_sec_and_promoted_rss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            now = datetime(2026, 9, 29, tzinfo=timezone.utc)
            finding = dict(
                subject_id="AAPL", source_object_id="candidate", content_hash="hash",
                title="Candidate", locator="source:title", published_at=now,
                available_at=now, observed_at=now, details={},
            )
            with self.assertRaisesRegex(ValueError, "official SEC"):
                store.record_finding(
                    source="sec", source_url="https://www.sec.gov.evil.example/Archives/a",
                    **finding,
                )
            with self.assertRaisesRegex(ValueError, "approved storage policy"):
                store.record_finding(
                    source="unknown", source_url="https://example.org/story", **finding,
                )
            with self.assertRaisesRegex(ValueError, "RSS search candidates"):
                store.record_finding(
                    source="rss", source_url="https://example.org/story",
                    verification_status="CLAIM_VERIFIED", **finding,
                )
            with self.assertRaises(ValueError):
                store.record_finding(
                    source="rss", source_url="https://127.0.0.1/story",
                    verification_status="UNVERIFIED", **finding,
                )
            self.assertEqual([], store.findings_as_of("AAPL", as_of=now))

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
            with self.assertRaisesRegex(ValueError, "new finding"):
                store.add_lead(subject_id="MU", finding_id=finding,
                               question="Same evidence?", as_of=now,
                               parent_lead_id=parent)

            def new_finding(index: int) -> str:
                identifier, _ = store.record_finding(
                    source="sec", subject_id="MU", source_object_id=f"item:{index}",
                    content_hash=f"hash:{index}", title="New section",
                    source_url="https://www.sec.gov/b", locator=f"Item {index}",
                    published_at=now, available_at=now, observed_at=now, details={},
                )
                return identifier

            child_finding = new_finding(1)
            child = store.add_lead(subject_id="MU", finding_id=child_finding,
                                   question="Customer demand?", as_of=now,
                                   parent_lead_id=parent)
            self.assertEqual(child, store.add_lead(
                subject_id="MU", finding_id=child_finding,
                question="Customer demand?", as_of=now, parent_lead_id=parent,
            ))
            grandchild_finding = new_finding(2)
            grandchild = store.add_lead(subject_id="MU", finding_id=grandchild_finding,
                                        question="Sector demand?", as_of=now,
                                        parent_lead_id=child)
            with self.assertRaises(ValueError):
                store.add_lead(subject_id="MU", finding_id=new_finding(3),
                               question="More?", as_of=now,
                               parent_lead_id=grandchild)
            self.assertEqual([], store.open_leads(["MU"], as_of=now - timedelta(seconds=1)))
            questions = store.open_leads(["MU"], as_of=now)
            self.assertEqual(3, len(questions))
            self.assertEqual({"Inventory?", "Customer demand?", "Sector demand?"},
                             {row["question"] for row in questions})
            self.assertEqual([], store.open_leads(["AAPL"], as_of=now))

    def test_followup_budget_rejects_unverified_and_fourth_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            now = datetime.now(timezone.utc)

            def evidence(number: int, *, rss: bool = False) -> str:
                finding, _ = store.record_finding(
                    source="rss" if rss else "sec", subject_id="AAPL",
                    source_object_id=f"source:{number}", content_hash=f"hash:{number}",
                    title="Observed", locator="section", published_at=now,
                    available_at=now, observed_at=now, details={},
                    source_url=("https://example.org/story" if rss
                                else "https://www.sec.gov/Archives/test"),
                    verification_status="UNVERIFIED" if rss else "SOURCE_VERIFIED",
                )
                return finding

            parent = store.add_lead(
                subject_id="AAPL", finding_id=evidence(0),
                question="What changed?", as_of=now,
            )
            with self.assertRaisesRegex(ValueError, "verified source evidence"):
                store.add_lead(
                    subject_id="AAPL", finding_id=evidence(1, rss=True),
                    question="RSS follow-up?", as_of=now, parent_lead_id=parent,
                )
            for number in range(2, 5):
                store.add_lead(
                    subject_id="AAPL", finding_id=evidence(number),
                    question=f"Item {number}?", as_of=now, parent_lead_id=parent,
                )
            with self.assertRaisesRegex(ValueError, "Maximum three"):
                store.add_lead(
                    subject_id="AAPL", finding_id=evidence(5),
                    question="Fourth item?", as_of=now, parent_lead_id=parent,
                )
            self.assertEqual(4, len(store.open_leads(["AAPL"], as_of=now)))

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
