from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from market_checker_app.storage.scout_store import ScoutStore


NOW = datetime(2026, 10, 11, 10, tzinfo=timezone.utc)


class SpecialistCheckScopeTests(unittest.TestCase):
    def store(self, directory):
        store = ScoutStore(Path(directory) / "checks.db")
        for ticker, cik in (("AAPL", "0000320193"), ("MSFT", "0000789019")):
            store.observe_sec_identity(subject_id=ticker, cik=cik,
                                       company_name=ticker + " Inc.", as_of=NOW)
        return store

    def check(self, store, source, *, version=None, when=NOW):
        store.record_specialist_check(source, subject_id="AAPL",
                                     identity_key="0000320193:AAPL Inc.", as_of=when,
                                     candidate_count=0, truncated=False, identity_version=version)

    def test_source_upgrade_preserves_legacy_check_without_reusing_coverage(self):
        for source, old_suffix in (("fda", ""), ("cms", ""), ("clinicaltrials", ""),
                                   ("ofac", ":sdn-alt-v1"), ("ofac_non_sdn", ":non-sdn-alt-v1")):
            with self.subTest(source=source), TemporaryDirectory() as directory:
                store = self.store(directory)
                with store._connect() as conn:
                    conn.execute("INSERT INTO scout_specialist_checks VALUES(?,?,?,?,?,?)",
                                 (source, "AAPL", "0000320193:AAPL Inc." + old_suffix,
                                  NOW.isoformat(), 0, 0))
                self.assertEqual(0, store.specialist_coverage(source, as_of=NOW)["current_complete"])
                self.assertEqual(2, len(store.specialist_due(source, as_of=NOW, limit=10)))
                self.check(store, source)
                self.assertEqual(1, store.specialist_coverage(source, as_of=NOW)["current_complete"])
                with store._connect() as conn:
                    self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM scout_specialist_checks").fetchone()[0])

    def test_registry_change_invalidates_only_the_changed_subject_across_restart(self):
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            v1, v2 = "a" * 64, "b" * 64
            self.check(store, "fda", version=v1)
            versions = {"AAPL": v1, "MSFT": v1}
            self.assertEqual(["MSFT"], [r["subject_id"] for r in store.specialist_due(
                "fda", as_of=NOW, limit=10, identity_versions=versions)])
            versions["AAPL"] = v2
            store = ScoutStore(store.db_path)
            self.assertEqual(0, store.specialist_coverage("fda", as_of=NOW,
                                                       identity_versions=versions)["current_complete"])
            self.assertEqual(2, len(store.specialist_due("fda", as_of=NOW, limit=10,
                                                       identity_versions=versions)))
            self.check(store, "fda", version=v2)
            self.assertEqual(1, store.specialist_coverage("fda", as_of=NOW,
                                                       identity_versions=versions)["current_complete"])
            self.assertEqual(1, store.specialist_coverage("fda", as_of=NOW,
                                                       identity_versions={"AAPL": v1})["current_complete"])

    def test_future_check_cannot_suppress_a_historical_due_subject(self):
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            self.check(store, "fda", when=NOW + timedelta(days=1))
            self.assertEqual(0, store.specialist_coverage("fda", as_of=NOW)["current_complete"])
            self.assertEqual(2, len(store.specialist_due("fda", as_of=NOW, limit=10)))
            self.assertEqual(1, store.specialist_coverage("fda", as_of=NOW + timedelta(days=1))["current_complete"])

    def test_invalid_registry_tokens_fail_before_recording_or_querying(self):
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            for version in (True, "short", "A" * 64, "a" * 63):
                with self.subTest(version=version):
                    with self.assertRaises(ValueError):
                        self.check(store, "fda", version=version)
                    with self.assertRaises(ValueError):
                        store.specialist_due("fda", as_of=NOW, limit=10,
                                             identity_versions={"AAPL": version})
            with store._connect() as conn:
                self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM scout_specialist_checks").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
