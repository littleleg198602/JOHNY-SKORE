from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from market_checker_app.services.epa_echo_scout_service import (
    EPA_ECHO_URL, EpaEchoFacilityClient, EpaEchoScoutService, parse_epa_facility,
)
from market_checker_app.services.specialist_acceptance_service import build_specialist_acceptance_report
from market_checker_app.storage.scout_store import ScoutStore


NOW = datetime(2026, 10, 2, 11, tzinfo=timezone.utc)


class FakeEpa:
    def __init__(self, rows=None, total=None, fail=False):
        self.rows = rows if rows is not None else [{"FacName":"EXAMPLE ENTITY INC", "RegistryID":"110018431295"}]
        self.total = len(self.rows) if total is None else total
        self.fail, self.calls = fail, []

    def facilities(self, name):
        self.calls.append(name)
        if self.fail:
            raise OSError("temporary")
        return self.rows, self.total


class EpaEchoScoutTests(unittest.TestCase):
    def test_exact_facility_lead_is_unverified_and_does_not_claim_ownership(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory)/"test.db")
            store.observe_sec_identity(subject_id="ONE", cik="0000000001",
                                       company_name="Example Entity Inc.", as_of=NOW)
            result = EpaEchoScoutService(store, client=FakeEpa()).run(as_of=NOW, universe={"ONE"})
            self.assertEqual(("OK",1,1),(result["status"],result["checked_issuers"],result["new_findings"]))
            saved = store.findings_as_of("ONE", as_of=NOW)[0]
            detail = json.loads(saved["details_json"])
            self.assertEqual("UNVERIFIED", saved["verification_status"])
            self.assertTrue(detail["facility_identity_verified"])
            self.assertFalse(detail["issuer_facility_relationship_verified"])
            self.assertFalse(detail["environmental_liability_verified"])
            self.assertFalse(detail["scoring_applied"])

    def test_loose_api_names_are_filtered_and_truncation_is_partial(self):
        self.assertIsNone(parse_epa_facility({"FacName":"Example Entity Plant", "RegistryID":"110018431295"},
                                             "Example Entity Inc."))
        with TemporaryDirectory() as directory:
            store=ScoutStore(Path(directory)/"test.db")
            store.observe_sec_identity(subject_id="ONE",cik="0000000001",company_name="Example Entity Inc.",as_of=NOW)
            result=EpaEchoScoutService(store,client=FakeEpa(total=2)).run(as_of=NOW,universe={"ONE"})
            self.assertEqual(("PARTIAL",1),(result["status"],result["truncated_issuers"]))
            self.assertEqual(1,store.specialist_coverage("epa",as_of=NOW,subjects={"ONE"})["current_partial"])

    def test_failure_budget_and_universe_leave_other_identities_due(self):
        with TemporaryDirectory() as directory:
            store=ScoutStore(Path(directory)/"test.db")
            for i in range(3):
                store.observe_sec_identity(subject_id=f"T{i}",cik=f"{i+1:010d}",company_name=f"Company {i} Inc.",as_of=NOW)
            result=EpaEchoScoutService(store,client=FakeEpa(fail=True),max_failures=1).run(as_of=NOW,universe={"T0","T1"})
            self.assertEqual(("PARTIAL",1,True),(result["status"],result["failed_issuers"],result["budget_exhausted"]))
            self.assertEqual(0,store.specialist_coverage("epa",as_of=NOW,subjects={"T0","T1"})["ever_checked"])

    def test_client_validates_host_body_count_and_schema(self):
        payload={"Results":{"Message":"Success","QueryRows":"1",
                            "Facilities":[{"FacName":"EXAMPLE ENTITY INC","RegistryID":"110018431295"}]}}
        with patch("market_checker_app.services.epa_echo_scout_service.urlopen") as opened:
            response=opened.return_value.__enter__.return_value
            response.geturl.return_value=EPA_ECHO_URL
            response.read.return_value=json.dumps(payload).encode()
            self.assertEqual(1,EpaEchoFacilityClient().facilities("Example Entity Inc.")[1])
            payload["Results"]["QueryRows"]="0"
            response.read.return_value=json.dumps(payload).encode()
            with self.assertRaises(ValueError): EpaEchoFacilityClient().facilities("Example Entity Inc.")
            response.read.return_value=b" "*2_000_001
            with self.assertRaisesRegex(ValueError,"byte budget"): EpaEchoFacilityClient().facilities("Example Entity Inc.")

    def test_access_failure_and_source_policy_fail_closed(self):
        class Denied:
            def facilities(self,name): raise HTTPError(EPA_ECHO_URL,403,"Forbidden",{},None)
        with TemporaryDirectory() as directory:
            store=ScoutStore(Path(directory)/"test.db")
            store.observe_sec_identity(subject_id="ONE",cik="0000000001",company_name="Example Entity Inc.",as_of=NOW)
            self.assertEqual("ACCESS_BLOCKED",EpaEchoScoutService(store,client=Denied()).run(as_of=NOW)["status"])
            args=dict(source="epa",subject_id="ONE",source_object_id="1",content_hash="h",title="lead",
                      locator="FRS:1",published_at=NOW,available_at=NOW,observed_at=NOW,details={})
            with self.assertRaisesRegex(ValueError,"official facility endpoint"):
                store.record_finding(source_url="https://example.org/echo",verification_status="UNVERIFIED",**args)
            with self.assertRaisesRegex(ValueError,"cannot verify"):
                store.record_finding(source_url=f"{EPA_ECHO_URL}?output=JSON",**args)
            self.assertEqual("NEVER_RUN",build_specialist_acceptance_report(store,as_of=NOW,environment={})["source_runs"]["epa"]["status"])


if __name__ == "__main__":
    unittest.main()
