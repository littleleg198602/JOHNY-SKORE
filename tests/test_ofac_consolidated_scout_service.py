from datetime import datetime, timedelta, timezone
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from market_checker_app.services.ofac_scout_service import (
    ALT_URL, SDN_URL, CONSOLIDATED_URL, CONSOLIDATED_ALT_URL,
    OfacConsolidatedClient, OfacConsolidatedScoutService, OfacSdnScoutService,
    attach_sdn_aliases, parse_consolidated_csv, parse_sdn_csv,
)
from market_checker_app.services.specialist_acceptance_service import build_specialist_acceptance_report
from market_checker_app.storage.scout_store import ScoutStore


NOW = datetime(2026, 10, 2, 6, tzinfo=timezone.utc)
PRIMARY = (b'1,"Example Entity Inc.",-0-,"SSI",-0-,-0-,-0-,-0-,-0-,-0-,-0-,"Subject to Directive 1"\n'
           b'2,"Individual Only",individual,"NS-PLC",-0-,-0-,-0-,-0-,-0-,-0-,-0-,-0-\n')
ALIASES = b'1,10,aka,"Former Example Inc.",-0-\n2,20,aka,"Excluded Person",-0-\n'


class FakeConsolidated:
    def snapshot(self):
        return attach_sdn_aliases(parse_consolidated_csv(PRIMARY), ALIASES)


class OfacConsolidatedTests(unittest.TestCase):
    def test_primary_alias_program_and_restrictions_remain_unverified_after_restart(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/"test.db"
            store = ScoutStore(path)
            for ticker, name in (("ONE", "Former Example Inc."), ("TWO", "Excluded Person"),
                                 ("THREE", "Example Entity")):
                store.observe_sec_identity(subject_id=ticker, cik="0000000001", company_name=name, as_of=NOW)
            result = OfacConsolidatedScoutService(store, client=FakeConsolidated()).run(as_of=NOW)
            self.assertEqual(("OK", 3, 1), (result["status"], result["checked_issuers"], result["new_findings"]))
            reopened = ScoutStore(path)
            saved = reopened.latest_findings(["ONE", "TWO", "THREE"], as_of=NOW, source="ofac_non_sdn")
            self.assertEqual(1, len(saved))
            self.assertEqual("UNVERIFIED", saved[0]["verification_status"])
            details = json.loads(reopened.findings_as_of("ONE", as_of=NOW)[0]["details_json"])
            self.assertEqual(("NON_SDN", "SSI", "Subject to Directive 1"),
                             (details["list_kind"], details["programs"], details["remarks"]))
            self.assertFalse(details["blocking_status_determined"])
            self.assertTrue(details["restrictions_require_program_review"])
            self.assertFalse(details["issuer_identity_verified"])
            self.assertFalse(details["scoring_applied"])
            self.assertEqual(CONSOLIDATED_ALT_URL, details["alias_snapshot_source"])
            self.assertEqual(0, OfacConsolidatedScoutService(reopened, client=FakeConsolidated()).run(
                as_of=NOW+timedelta(days=1))["new_findings"])
            self.assertEqual([], reopened.latest_findings(["ONE"], as_of=NOW-timedelta(seconds=1), source="ofac_non_sdn"))

    def test_sdn_check_and_same_record_id_cannot_complete_non_sdn(self):
        class FakeSdn:
            def snapshot(self):
                return attach_sdn_aliases(parse_sdn_csv(PRIMARY), ALIASES)
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory)/"test.db")
            store.observe_sec_identity(subject_id="ONE", cik="0000000001", company_name="Example Entity Inc.", as_of=NOW)
            self.assertEqual(1, OfacSdnScoutService(store, client=FakeSdn()).run(as_of=NOW)["new_findings"])
            self.assertEqual(0, store.specialist_coverage("ofac_non_sdn", as_of=NOW)["current_complete"])
            self.assertEqual(1, OfacConsolidatedScoutService(store, client=FakeConsolidated()).run(as_of=NOW)["new_findings"])
            self.assertEqual(2, len(store.findings_as_of("ONE", as_of=NOW)))

    def test_alias_failure_or_orphan_parent_never_creates_complete_negative(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory)/"test.db")
            store.observe_sec_identity(subject_id="ONE", cik="0000000001", company_name="Example Entity Inc.", as_of=NOW)
            for alias in (OSError("failed"), ALIASES.replace(b'1,10', b'99,10')):
                client = OfacConsolidatedClient()
                with patch.object(client, "_download", side_effect=[PRIMARY, alias]):
                    self.assertEqual("ERROR", OfacConsolidatedScoutService(store, client=client).run(as_of=NOW)["status"])
                self.assertEqual(0, store.specialist_coverage("ofac_non_sdn", as_of=NOW)["ever_checked"])

    def test_bounded_download_urls_and_malformed_primary(self):
        client = OfacConsolidatedClient()
        with patch.object(client, "_download", side_effect=[PRIMARY, ALIASES]) as download:
            snapshot = client.snapshot()
        self.assertEqual(1, len(snapshot.entities))
        self.assertEqual([CONSOLIDATED_URL, CONSOLIDATED_ALT_URL], [c.args[0] for c in download.call_args_list])
        self.assertEqual([1_000_000, 1_000_000], [c.kwargs["byte_budget"] for c in download.call_args_list])
        for body in (b'', PRIMARY+PRIMARY, b'<html>unavailable</html>', b' ' * 1_000_001):
            with self.subTest(body=body[:20]), self.assertRaises((ValueError, csv.Error)):
                parse_consolidated_csv(body)

    def test_storage_requires_correct_export_and_report_separates_never_run(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory)/"test.db")
            args = dict(source="ofac_non_sdn", subject_id="ONE", source_object_id="1", content_hash="a", title="lead",
                        locator="1", published_at=NOW, available_at=NOW, observed_at=NOW, details={})
            for url in (SDN_URL, ALT_URL, "https://example.org/CONS_PRIM.CSV"):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    store.record_finding(source_url=url, verification_status="UNVERIFIED", **args)
            with self.assertRaisesRegex(ValueError, "cannot verify"):
                store.record_finding(source_url=CONSOLIDATED_URL, **args)
            report = build_specialist_acceptance_report(store, as_of=NOW, environment={})
            self.assertEqual("NEVER_RUN", report["source_runs"]["ofac_non_sdn"]["status"])
            self.assertEqual(687, report["rotating_source_coverage"]["ofac_non_sdn"]["applicable_profile_subjects"])
            self.assertFalse(report["completion_verified"])


if __name__ == "__main__":
    unittest.main()
