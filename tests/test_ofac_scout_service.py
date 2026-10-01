from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import Request
import unittest

from market_checker_app.services.ofac_scout_service import (
    SDN_URL, OfacSnapshot, OfacSdnScoutService, _OfacRedirects, parse_sdn_csv,
)
from market_checker_app.storage.scout_store import ScoutStore

NOW = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
CSV = (b'1,"Example Entity Inc.",-0-,"TEST",-0-,-0-,-0-,-0-,-0-,-0-,-0-,-0-\n'
       b'2,"Example Entity Inc.",individual,"TEST",-0-,-0-,-0-,-0-,-0-,-0-,-0-,-0-\n')


class FakeOfac:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0
        self.payload = parse_sdn_csv(CSV)

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
            client.payload = OfacSnapshot(client.payload.entities, "f" * 64, 3)
            self.assertEqual(0, service.run(as_of=NOW + timedelta(days=1))["new_findings"])
            rows = store.latest_findings(["ONE"], as_of=NOW, source="ofac")
            self.assertEqual("UNVERIFIED", rows[0]["verification_status"])
            self.assertEqual([], store.latest_findings(["TWO"], as_of=NOW, source="ofac"))
            self.assertEqual([], store.latest_findings(["ONE"], as_of=NOW - timedelta(seconds=1), source="ofac"))
            store.record_source_run("ofac", as_of=NOW, summary=result)
            self.assertEqual(parse_sdn_csv(CSV).content_sha256, store.latest_source_runs()["ofac"]["snapshot_sha256"])

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
