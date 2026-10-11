from datetime import datetime, timedelta, timezone
from dataclasses import replace
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import Request
import unittest
from unittest.mock import patch

from market_checker_app.services.ofac_scout_service import (
    ALT_URL, SDN_URL, OfacSdnClient, OfacSdnScoutService, _OfacRedirects,
    attach_sdn_aliases, parse_sdn_csv,
)
from market_checker_app.storage.scout_store import ScoutStore

NOW = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
CSV = (b'1,"Example Entity Inc.",-0-,"TEST",-0-,-0-,-0-,-0-,-0-,-0-,-0-,-0-\n'
       b'2,"Example Entity Inc.",individual,"TEST",-0-,-0-,-0-,-0-,-0-,-0-,-0-,-0-\n')
ALIASES = (b'1,10,aka,"Former Example Corp.",-0-\n'
           b'1,11,fka,"Former Example Corp.",-0-\n'
           b'2,12,aka,"Person Only Alias",-0-\n')


class FakeOfac:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0
        self.payload = attach_sdn_aliases(parse_sdn_csv(CSV), ALIASES)

    def snapshot(self):
        self.calls += 1
        if self.fail:
            raise OSError("temporary download error")
        return self.payload


class OfacScoutTests(unittest.TestCase):
    def test_exact_entity_candidate_is_deduplicated_and_not_a_sanctions_claim(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            for ticker, name in (("ONE", "Example Entity Inc."), ("TWO", "Other Entity Inc.")):
                store.observe_sec_identity(subject_id=ticker, cik="0000000001", company_name=name, as_of=NOW)
            client = FakeOfac()
            service = OfacSdnScoutService(store, client=client)
            result = service.run(as_of=NOW)
            self.assertEqual(("OK", 2, 1), (result["status"], result["checked_issuers"], result["new_findings"]))
            self.assertEqual(0, service.run(as_of=NOW)["checked_issuers"])
            client.payload = replace(client.payload, content_sha256="f" * 64, total_rows=3)
            self.assertEqual(0, service.run(as_of=NOW + timedelta(days=1))["new_findings"])
            rows = store.latest_findings(["ONE"], as_of=NOW, source="ofac")
            self.assertEqual("UNVERIFIED", rows[0]["verification_status"])
            self.assertEqual([], store.latest_findings(["TWO"], as_of=NOW, source="ofac"))
            self.assertEqual([], store.latest_findings(["ONE"], as_of=NOW - timedelta(seconds=1), source="ofac"))
            store.record_source_run("ofac", as_of=NOW, summary=result)
            self.assertEqual(parse_sdn_csv(CSV).content_sha256, store.latest_source_runs()["ofac"]["snapshot_sha256"])

    def test_alias_join_deduplicates_by_parent_and_preserves_candidate_provenance(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            names = {"ONE": "Former Example Corp.", "TWO": "Person Only Alias",
                     "THREE": "Former Example", "OUTSIDE": "Former Example Corp."}
            for ticker, name in names.items():
                store.observe_sec_identity(subject_id=ticker, cik="0000000001", company_name=name, as_of=NOW)
            client = FakeOfac()
            service = OfacSdnScoutService(store, client=client)
            result = service.run(as_of=NOW, universe={"ONE", "TWO", "THREE"})
            self.assertEqual(("OK", 3, 1, 1), (result["status"], result["checked_issuers"],
                                              result["new_findings"], result["matched_rows"]))
            finding = store.latest_findings(["ONE"], as_of=NOW, source="ofac")[0]
            details = json.loads(store.findings_as_of("ONE", as_of=NOW)[0]["details_json"])
            self.assertEqual("UNVERIFIED", finding["verification_status"])
            self.assertFalse(details["issuer_identity_verified"])
            self.assertFalse(details["scoring_applied"])
            self.assertEqual("1", details["sdn_id"])
            self.assertEqual(["10", "11"], [n["alias_id"] for n in details["matched_names"]])
            self.assertEqual(ALT_URL, details["alias_snapshot_source"])
            self.assertEqual(client.payload.alias_content_sha256, details["alias_snapshot_content_sha256"])
            self.assertEqual([], store.latest_findings(["TWO", "THREE", "OUTSIDE"], as_of=NOW, source="ofac"))
            self.assertEqual([], store.latest_findings(["ONE"], as_of=NOW - timedelta(seconds=1), source="ofac"))
            client.payload = replace(client.payload, alias_content_sha256="e" * 64)
            self.assertEqual(0, service.run(as_of=NOW + timedelta(days=1), universe={"ONE"})["new_findings"])
            store.record_source_run("ofac", as_of=NOW, summary=result)
            saved = store.latest_source_runs()["ofac"]
            self.assertEqual(result["alias_snapshot_sha256"], saved["alias_snapshot_sha256"])
            self.assertEqual(3, saved["alias_rows"])

    def test_orphan_duplicate_bad_shape_or_type_aliases_are_not_screened(self):
        primary = parse_sdn_csv(CSV)
        self.assertEqual(2, len(attach_sdn_aliases(primary, ALIASES + b'\x1a\r\n').entities[0]["aliases"]))
        invalids = (b'', ALIASES + ALIASES, ALIASES.replace(b'1,10', b'99,10'),
                    ALIASES.replace(b',aka,', b',unknown,'), b'1,10,aka,name\n',
                    b'1,10,aka,"unterminated,-0-\n', ALIASES.replace(b',10,', b',0,'),
                    ALIASES.replace(b'Former Example Corp.', b'-0-'),
                    ALIASES.replace(b'Former Example Corp.', b'Name\x1a'))
        for body in invalids:
            with self.subTest(body=body[:40]), self.assertRaises((ValueError, csv.Error)):
                attach_sdn_aliases(primary, body)

    def test_alias_download_failure_leaves_no_completed_checks(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            store.observe_sec_identity(subject_id="ONE", cik="0000000001", company_name="Example Entity Inc.", as_of=NOW)
            client = OfacSdnClient()
            with patch.object(client, "_download", side_effect=[CSV, OSError("alias unavailable")]) as download:
                result = OfacSdnScoutService(store, client=client).run(as_of=NOW)
                self.assertEqual("ERROR", result["status"])
                self.assertEqual(ALT_URL, download.call_args_list[1].args[0])
                self.assertEqual(0, store.specialist_coverage("ofac", as_of=NOW)["ever_checked"])
                self.assertEqual([], store.latest_findings(["ONE"], as_of=NOW, source="ofac"))

    def test_primary_only_snapshot_is_partial_for_alias_coverage(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            store.observe_sec_identity(subject_id="ONE", cik="0000000001", company_name="Example Entity Inc.", as_of=NOW)
            client = FakeOfac()
            client.payload = parse_sdn_csv(CSV)
            result = OfacSdnScoutService(store, client=client).run(as_of=NOW)
            self.assertEqual("PARTIAL", result["status"])
            self.assertEqual(1, store.specialist_coverage("ofac", as_of=NOW)["current_partial"])

    def test_old_primary_check_does_not_hide_due_alias_screening(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            store.observe_sec_identity(subject_id="ONE", cik="0000000001", company_name="Former Example Corp.", as_of=NOW)
            # Persist the exact pre-upgrade key, rather than using the new API.
            with store._connect() as connection:
                connection.execute("INSERT INTO scout_specialist_checks "
                                   "(source,subject_id,identity_key,checked_at,candidate_count,truncated) "
                                   "VALUES(?,?,?,?,0,0)",
                                   ("ofac", "ONE", "0000000001:Former Example Corp.", NOW.isoformat()))
            self.assertEqual(0, store.specialist_coverage("ofac", as_of=NOW)["current_complete"])
            result = OfacSdnScoutService(store, client=FakeOfac()).run(as_of=NOW)
            self.assertEqual(1, result["new_findings"])
            self.assertEqual(1, store.specialist_coverage("ofac", as_of=NOW)["current_complete"])
            with store._connect() as connection:
                self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM scout_specialist_checks").fetchone()[0])

    def test_universe_filter_and_download_failure_do_not_complete_checks(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            store.observe_sec_identity(subject_id="ONE", cik="0000000001", company_name="Example Entity Inc.", as_of=NOW)
            client = FakeOfac(fail=True)
            service = OfacSdnScoutService(store, client=client)
            self.assertEqual("WAIT_IDENTITY", service.run(as_of=NOW, universe={"OTHER"})["status"])
            self.assertEqual(0, client.calls)
            self.assertEqual("ERROR", service.run(as_of=NOW)["status"])
            self.assertEqual(0, store.specialist_coverage("ofac", as_of=NOW)["ever_checked"])

    def test_csv_empty_malformed_duplicate_and_unknown_type_are_rejected(self):
        self.assertEqual(1, len(parse_sdn_csv(CSV).entities))
        self.assertEqual(1, len(parse_sdn_csv(CSV + b'\x1a\r\n').entities))
        for invalid in (b'', b'<html>error</html>', CSV + CSV, CSV.replace(b'-0-,"TEST"', b'unknown,"TEST"')):
            with self.subTest(invalid=invalid[:30]), self.assertRaises(ValueError):
                parse_sdn_csv(invalid)

    def test_redirect_and_storage_do_not_accept_unrelated_hosts(self):
        redirect = _OfacRedirects()
        for target in ("http://sanctionslistservice.ofac.treas.gov/data", "https://evil.example/SDN.CSV",
                       "https://other-bucket.s3.us-gov-west-1.amazonaws.com/SDN.CSV"):
            with self.assertRaises(ValueError):
                redirect.redirect_request(Request(SDN_URL), None, 302, "", {}, target)
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            args = dict(source="ofac", subject_id="ONE", source_object_id="1", content_hash="x", title="x",
                        locator="1", published_at=NOW, available_at=NOW, observed_at=NOW, details={})
            with self.assertRaisesRegex(ValueError, "official SDN"):
                store.record_finding(source_url="https://example.org/SDN.CSV", verification_status="UNVERIFIED", **args)
            with self.assertRaisesRegex(ValueError, "cannot verify"):
                store.record_finding(source_url=SDN_URL, **args)


if __name__ == "__main__":
    unittest.main()
