from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from market_checker_app.services.usaspending_recipient_discovery import (
    UsaSpendingRecipientDiscovery,
)
from market_checker_app.storage.scout_store import ScoutStore


CLOCK = datetime(2026, 9, 30, tzinfo=timezone.utc)


class Recipients:
    def __init__(self):
        self.calls = []

    def recipients(self, name, *, page):
        self.calls.append((name, page))
        return {"results": [
            {"id": "id-1", "uei": "ABCDEFGHIJKL", "name": name,
             "recipient_level": "C"},
            {"id": "id-2", "uei": "MNOPQRSTUVWX", "name": name,
             "recipient_level": "P"},
            {"id": "id-3", "uei": "ZZZZZZZZZZZZ", "name": "SOME OTHER CORP"},
        ], "page_metadata": {"hasNext": False}}


class RecipientDiscoveryTests(unittest.TestCase):
    def test_two_exact_name_recipients_are_candidates_and_rechecks_are_bounded(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            store.observe_sec_identity(subject_id="LMT", cik="0000000123",
                                       company_name="LOCKHEED MARTIN CORP", as_of=CLOCK)
            store.observe_sec_identity(subject_id="RTX", cik="0000000456",
                                       company_name="RTX CORP", as_of=CLOCK)
            client = Recipients()
            service = UsaSpendingRecipientDiscovery(store, client=client, max_subjects=1)
            first = service.run(as_of=CLOCK, universe={"LMT", "RTX"})
            self.assertEqual(1, first["checked_names"])
            self.assertEqual(2, first["new_candidates"])
            second = service.run(as_of=CLOCK, universe={"LMT", "RTX"})
            self.assertEqual(1, second["checked_names"])
            self.assertEqual(2, second["new_candidates"])
            self.assertEqual(0, service.run(as_of=CLOCK)["checked_names"])
            self.assertEqual(1, service.run(as_of=CLOCK + timedelta(days=31))["checked_names"])
            with store._connect() as conn:
                rows = conn.execute("SELECT subject_id, verification_status, details_json "
                                    "FROM scout_findings WHERE source='usaspending'").fetchall()
            self.assertEqual(4, len(rows))
            self.assertTrue(all(row["verification_status"] == "UNVERIFIED" for row in rows))
            self.assertTrue(all(not json.loads(row["details_json"])
                                ["contract_attribution_allowed"] for row in rows))

    def test_no_sec_identity_causes_no_lookup_and_partial_is_reported(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            client = Recipients()
            service = UsaSpendingRecipientDiscovery(store, client=client)
            self.assertEqual(0, service.run(as_of=CLOCK)["checked_names"])
            self.assertEqual([], client.calls)
            store.observe_sec_identity(subject_id="LMT", cik="0000000123",
                                       company_name="LOCKHEED MARTIN CORP", as_of=CLOCK)
            class Endless(Recipients):
                def recipients(self, name, *, page):
                    return {"results": [], "page_metadata": {"hasNext": True}}
            partial = UsaSpendingRecipientDiscovery(
                store, client=Endless(), max_pages=2).run(as_of=CLOCK)
            self.assertEqual("PARTIAL", partial["status"])
            self.assertEqual(1, partial["truncated_names"])

    def test_failed_lookup_stays_due_without_blocking_other_names(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            for ticker, cik, name in (("AAA", "0000000123", "AAA INC"),
                                      ("BBB", "0000000456", "BBB INC")):
                store.observe_sec_identity(subject_id=ticker, cik=cik,
                                           company_name=name, as_of=CLOCK)
            class Failing(Recipients):
                def recipients(self, name, *, page):
                    if name == "AAA INC":
                        raise OSError("temporary outage")
                    return super().recipients(name, page=page)
            result = UsaSpendingRecipientDiscovery(
                store, client=Failing(), max_subjects=2).run(as_of=CLOCK)
            self.assertEqual(1, result["failed_names"])
            self.assertEqual(1, result["checked_names"])
            self.assertEqual("PARTIAL", result["status"])
            self.assertEqual([{"subject_id": "AAA", "company_name": "AAA INC"}],
                             store.recipient_discovery_due(as_of=CLOCK))


if __name__ == "__main__":
    unittest.main()
