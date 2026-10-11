from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from market_checker_app.services.doj_scout_service import (
    DOJ_URL, DojPressReleaseClient, DojPressReleaseScoutService, parse_doj_candidate,
)
from market_checker_app.services.specialist_acceptance_service import build_specialist_acceptance_report
from market_checker_app.storage.scout_store import ScoutStore


NOW = datetime(2026, 10, 2, 6, tzinfo=timezone.utc)


def release(index=1, name="Example Entity Inc."):
    return {"uuid": f"52b541b0-1f60-4b76-b789-{index:012d}",
            "title": f"Department announces action concerning {name}",
            "url": "https://www.justice.gov/opa/pr/example-release",
            "date": str(int((NOW-timedelta(days=1)).timestamp()))}


class FakeDoj:
    def __init__(self, fail=False, total=1):
        self.fail, self.total, self.calls = fail, total, []

    def page(self, name, *, size, page):
        self.calls.append((name, size, page))
        if self.fail:
            raise OSError("temporary failure")
        start = page*size
        return [release(i+1, name) for i in range(start, min(start+size, self.total))], self.total


class DojScoutTests(unittest.TestCase):
    def test_exact_title_candidate_persists_without_promoting_liability_or_old_availability(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/"test.db"
            store = ScoutStore(path)
            store.observe_sec_identity(subject_id="ONE", cik="0000000001", company_name="Example Entity Inc.", as_of=NOW)
            scout = DojPressReleaseScoutService(store, client=FakeDoj())
            result = scout.run(as_of=NOW)
            self.assertEqual(("OK", 1, 1), (result["status"], result["checked_issuers"], result["new_findings"]))
            saved = ScoutStore(path).findings_as_of("ONE", as_of=NOW)[0]
            self.assertEqual("UNVERIFIED", saved["verification_status"])
            self.assertEqual(NOW.isoformat(), saved["published_at"])
            detail = json.loads(saved["details_json"])
            self.assertFalse(detail["issuer_identity_verified"])
            self.assertFalse(detail["liability_verified"])
            self.assertFalse(detail["complete_issuer_legal_risk_coverage"])
            self.assertFalse(detail["scoring_applied"])
            self.assertNotEqual(detail["source_declared_at"], saved["published_at"])
            self.assertEqual([], ScoutStore(path).findings_as_of("ONE", as_of=NOW-timedelta(seconds=1)))
            self.assertEqual(0, scout.run(as_of=NOW+timedelta(days=31))["new_findings"])

    def test_page_cap_remains_partial_and_next_day_replay_deduplicates(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory)/"test.db")
            store.observe_sec_identity(subject_id="ONE", cik="0000000001", company_name="Example Entity Inc.", as_of=NOW)
            client = FakeDoj(total=3)
            first = DojPressReleaseScoutService(store, client=client, page_size=1, max_pages=2).run(as_of=NOW)
            self.assertEqual(("PARTIAL", 2, 1), (first["status"], first["new_findings"], first["truncated_issuers"]))
            complete = DojPressReleaseScoutService(store, client=client, page_size=1, max_pages=3).run(as_of=NOW+timedelta(days=1))
            self.assertEqual(("OK", 1), (complete["status"], complete["new_findings"]))
            self.assertEqual(3, len(store.findings_as_of("ONE", as_of=NOW+timedelta(days=1))))
            self.assertEqual(1, store.specialist_coverage("doj", as_of=NOW+timedelta(days=1))["current_complete"])

    def test_future_invalid_uuid_and_wrong_host_remain_partial(self):
        changes = ({"uuid":"bad"}, {"url":"https://example.org/opa/pr/example"},
                   {"date":str(int((NOW+timedelta(days=1)).timestamp()))})
        for change in changes:
            class BadDoj:
                def page(self, name, *, size, page):
                    return [{**release(), **change}], 1
            with self.subTest(change=change), TemporaryDirectory() as directory:
                store = ScoutStore(Path(directory)/"test.db")
                store.observe_sec_identity(subject_id="ONE", cik="0000000001", company_name="Example Entity Inc.", as_of=NOW)
                result = DojPressReleaseScoutService(store, client=BadDoj()).run(as_of=NOW)
                self.assertEqual(("PARTIAL", 1, 0), (result["status"], result["rejected_rows"], result["new_findings"]))
                self.assertEqual(0, store.specialist_coverage("doj", as_of=NOW)["current_complete"])

    def test_api_loose_match_cannot_replace_full_name_or_parent(self):
        self.assertIsNone(parse_doj_candidate(release(name="Other Example Entity Inc."), "Example Corp.", NOW))
        self.assertIsNone(parse_doj_candidate(release(name="Google"), "Alphabet Inc.", NOW))
        self.assertIsNone(parse_doj_candidate(release(name="Example Entity Incorporated"), "Example Entity Inc.", NOW))
        self.assertIsNotNone(parse_doj_candidate(release(name="EXAMPLE ENTITY, INC."), "Example Entity Inc.", NOW))

    def test_failed_page_never_completes_issuer_and_failure_budget_stops_batch(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory)/"test.db")
            for i in range(4):
                store.observe_sec_identity(subject_id=f"T{i}", cik=f"{i+1:010d}", company_name=f"Company {i} Inc.", as_of=NOW)
            client = FakeDoj(fail=True)
            result = DojPressReleaseScoutService(store, client=client, max_failures=2).run(as_of=NOW)
            self.assertEqual(("PARTIAL", 2, True), (result["status"], result["failed_issuers"], result["budget_exhausted"]))
            self.assertEqual(2, len(client.calls))
            self.assertEqual(0, store.specialist_coverage("doj", as_of=NOW)["ever_checked"])

    def test_deadline_and_universe_do_not_complete_deferred_issuers(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory)/"test.db")
            for i in range(3):
                store.observe_sec_identity(subject_id=f"T{i}", cik=f"{i+1:010d}", company_name=f"Company {i} Inc.", as_of=NOW)
            client = FakeDoj()
            with patch("market_checker_app.services.doj_scout_service.time.monotonic", side_effect=[0,0,0,10]):
                result = DojPressReleaseScoutService(store, client=client, max_run_seconds=5).run(as_of=NOW, universe={"T0","T1"})
            self.assertEqual((1, True), (result["checked_issuers"], result["budget_exhausted"]))
            self.assertEqual(["T1","T2"], [r["subject_id"] for r in store.specialist_due("doj", as_of=NOW, limit=10)])

    def test_http_failure_is_not_empty_and_access_status_remains_visible(self):
        class Denied:
            def page(self, name, *, size, page):
                raise HTTPError(DOJ_URL, 403, "Forbidden", {}, None)
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory)/"test.db")
            store.observe_sec_identity(subject_id="ONE", cik="0000000001", company_name="Example Entity Inc.", as_of=NOW)
            result = DojPressReleaseScoutService(store, client=Denied()).run(as_of=NOW)
            self.assertEqual("ACCESS_BLOCKED", result["status"])
            self.assertEqual(0, store.specialist_coverage("doj", as_of=NOW)["ever_checked"])

    def test_client_checks_page_total_and_body_before_records(self):
        payload={"metadata":{"responseInfo":{"status":200},"resultset":{"count":"1","page":0,"pagesize":"1"}},
                 "results":[release()]}
        with patch("market_checker_app.services.doj_scout_service.urlopen") as opened:
            response=opened.return_value.__enter__.return_value
            response.geturl.return_value=DOJ_URL
            response.read.return_value=json.dumps(payload).encode()
            self.assertEqual(1, DojPressReleaseClient().page("Example Entity Inc.", size=1, page=0)[1])
            payload["metadata"]["resultset"]["page"]=1
            response.read.return_value=json.dumps(payload).encode()
            with self.assertRaises(ValueError): DojPressReleaseClient().page("Example Entity Inc.",size=1,page=0)
            response.read.return_value=b" "*2_000_001
            with self.assertRaisesRegex(ValueError,"byte budget"): DojPressReleaseClient().page("Example Entity Inc.",size=1,page=0)

    def test_source_policy_and_report_keep_never_run_separate(self):
        with TemporaryDirectory() as directory:
            store=ScoutStore(Path(directory)/"test.db")
            args=dict(source="doj",subject_id="ONE",source_object_id="uuid",content_hash="hash",title="lead",locator="uuid",
                      published_at=NOW,available_at=NOW,observed_at=NOW,details={})
            with self.assertRaisesRegex(ValueError,"official press release"):
                store.record_finding(source_url="https://evil.example/opa/pr/article",verification_status="UNVERIFIED",**args)
            with self.assertRaisesRegex(ValueError,"cannot verify"):
                store.record_finding(source_url=release()["url"],**args)
            report=build_specialist_acceptance_report(store,as_of=NOW,environment={})
            self.assertEqual("NEVER_RUN",report["source_runs"]["doj"]["status"])
            self.assertFalse(report["completion_verified"])


if __name__=="__main__":
    unittest.main()
