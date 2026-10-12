from datetime import datetime, timedelta, timezone
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
from market_checker_app.services.regulatory_identity_service import RegulatoryIdentityRegistry
from market_checker_app.services.public_source_budget import PUBLIC_DAILY_LIMITS
from market_checker_app.storage.scout_store import ScoutStore
from tests.test_regulatory_identity_service import NOW as IDENTITY_NOW, synthetic_record


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
            service=EpaEchoScoutService(store,client=FakeEpa(total=2))
            result=service.run(as_of=NOW,universe={"ONE"})
            self.assertEqual(("PARTIAL",1),(result["status"],result["truncated_issuers"]))
            versions=service.identities.identity_versions(store,as_of=NOW)
            self.assertEqual(1,store.specialist_coverage("epa",as_of=NOW,subjects={"ONE"},
                                                       identity_versions=versions)["current_partial"])

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

    def seed(self, directory, *, name="EXAMPLE ENTITY INC"):
        store=ScoutStore(Path(directory)/"test.db")
        store.observe_sec_identity(subject_id="ONE",cik="0000000001",company_name=name,as_of=IDENTITY_NOW)
        return store

    def coverage(self, service, *, as_of=IDENTITY_NOW):
        return service.store.specialist_coverage("epa",as_of=as_of,subjects={"ONE"},
                   identity_versions=service.identities.identity_versions(service.store,as_of=as_of))

    def test_reviewed_exact_frs_link_does_not_inherit_facility_penalty_or_conviction(self):
        with TemporaryDirectory() as directory:
            store=self.seed(directory)
            client=FakeEpa(rows=[{"FacName":"EXAMPLE ENTITY INC","RegistryID":"110018431295",
                                 "FacFormalActionCount":"3","FacTotalPenalties":"1000000","FacComplianceStatus":"Violation Identified"}])
            service=EpaEchoScoutService(store,client=client,identities=RegulatoryIdentityRegistry([synthetic_record()]))
            result=service.run(as_of=IDENTITY_NOW)
            self.assertEqual(("OK",1,0,1),(result["status"],result["verified_facilities"],
                                          result["unresolved_facilities"],self.coverage(service)["current_complete"]))
            saved=store.findings_as_of("ONE",as_of=IDENTITY_NOW)[0]
            detail=json.loads(saved["details_json"])
            self.assertEqual("UNVERIFIED",saved["verification_status"])
            self.assertTrue(detail["issuer_facility_relationship_verified"])
            self.assertEqual("epa_echo_dated_facility_ownership",detail["stage"])
            self.assertEqual("110018431295",detail["identity_evidence"]["frs_registry_id"])
            for key in ("issuer_conviction_verified","issuer_fine_verified","environmental_liability_verified",
                        "facility_regulatory_action_attribution_allowed","complete_facility_group_verified","scoring_applied"):
                self.assertFalse(detail[key],key)
            self.assertNotIn("FacTotalPenalties",detail)
            self.assertFalse(result["issuer_liability_negative_verified"])

    def test_documented_facility_name_is_queried_independently_of_parent_name(self):
        class Named(FakeEpa):
            def facilities(self,name):
                self.calls.append(name)
                return ([{"FacName":"EXAMPLE PLANT","RegistryID":"110018431295"}],1) if name=="EXAMPLE PLANT" else ([],0)
        with TemporaryDirectory() as directory:
            store=self.seed(directory,name="Example Parent Corp")
            registry=RegulatoryIdentityRegistry([synthetic_record(facility_name="EXAMPLE PLANT")])
            client=Named()
            service=EpaEchoScoutService(store,client=client,identities=registry)
            result=service.run(as_of=IDENTITY_NOW)
            self.assertEqual(["Example Parent Corp","EXAMPLE PLANT"],client.calls)
            self.assertEqual(("OK",1,0,2),(result["status"],result["verified_facilities"],
                                           result["missing_reviewed_facilities"],result["daily_requests"]))

    def test_empty_and_missing_reviewed_facilities_have_different_partial_semantics(self):
        with TemporaryDirectory() as directory:
            store=self.seed(directory)
            service=EpaEchoScoutService(store,client=FakeEpa(rows=[]))
            empty=service.run(as_of=IDENTITY_NOW)
            self.assertEqual(("OK",1,0),(empty["status"],self.coverage(service)["current_complete"],empty["new_findings"]))
            self.assertFalse(empty["issuer_liability_negative_verified"])
            self.assertFalse(empty["complete_facility_group_verified"])
            self.assertEqual("bounded exact facility-name queries only",empty["negative_scope"])
        with TemporaryDirectory() as directory:
            store=self.seed(directory)
            service=EpaEchoScoutService(store,client=FakeEpa(rows=[]),identities=RegulatoryIdentityRegistry([synthetic_record()]))
            missing=service.run(as_of=IDENTITY_NOW)
            self.assertEqual(("PARTIAL",1,1),(missing["status"],missing["missing_reviewed_facilities"],
                                               self.coverage(service)["current_partial"]))

    def test_added_reviewed_link_invalidates_scope_without_bypassing_daily_quota(self):
        with TemporaryDirectory() as directory:
            store=self.seed(directory)
            first=EpaEchoScoutService(store,client=FakeEpa())
            first.run(as_of=IDENTITY_NOW)
            upgraded=EpaEchoScoutService(store,client=FakeEpa(),identities=RegulatoryIdentityRegistry([synthetic_record()]))
            versions=upgraded.identities.identity_versions(store,as_of=IDENTITY_NOW)
            self.assertEqual(["ONE"],[row["subject_id"] for row in store.specialist_due(
                "epa",as_of=IDENTITY_NOW,limit=10,identity_versions=versions)])
            self.assertEqual(0,self.coverage(upgraded)["current_complete"])
            result=upgraded.run(as_of=IDENTITY_NOW)
            self.assertEqual(("WAIT_QUOTA",0),(result["status"],result["attempted_requests"]))
            next_day=upgraded.run(as_of=IDENTITY_NOW+timedelta(days=1))
            self.assertEqual(("OK",1),(next_day["status"],next_day["verified_facilities"]))

    def test_quota_interrupt_preserves_first_facility_and_partial_check_across_restart(self):
        rows=[synthetic_record(),synthetic_record(record_id="SYNTHETIC-other-plant",facility_name="EXAMPLE OTHER PLANT",
                                                 frs_registry_id="110018431296")]
        rows[1]["evidence"][0]["locator"]="SYNTHETIC FRS:110018431296"
        with TemporaryDirectory() as directory, patch.dict(PUBLIC_DAILY_LIMITS,{"epa":(10,1)}):
            store=self.seed(directory)
            service=EpaEchoScoutService(store,client=FakeEpa(),identities=RegulatoryIdentityRegistry(rows))
            result=service.run(as_of=IDENTITY_NOW)
            self.assertEqual(("PARTIAL",1,1,1,1),(result["status"],result["daily_requests"],result["new_findings"],
                                                   result["missing_reviewed_facilities"],self.coverage(service)["current_partial"]))
            reopened=ScoutStore(store.db_path)
            retry=EpaEchoScoutService(reopened,client=FakeEpa(),identities=RegulatoryIdentityRegistry(rows)).run(as_of=IDENTITY_NOW)
            self.assertEqual(("PARTIAL",0,1),(retry["status"],retry["attempted_requests"],retry["current_partial_checks"]))
            self.assertEqual(1,len(reopened.findings_as_of("ONE",as_of=IDENTITY_NOW)))

    def test_identity_query_count_is_bounded_and_omitted_facilities_remain_partial(self):
        records=[]
        for i in range(6):
            row=synthetic_record(record_id=f"SYNTHETIC-plant-{i}",facility_name=f"EXAMPLE PLANT {i}",
                                 frs_registry_id=f"11001843129{i}")
            row["evidence"][0]["locator"]=f"SYNTHETIC FRS:{row['frs_registry_id']}"
            records.append(row)
        with TemporaryDirectory() as directory:
            store=self.seed(directory)
            client=FakeEpa(rows=[])
            service=EpaEchoScoutService(store,client=client,identities=RegulatoryIdentityRegistry(records))
            result=service.run(as_of=IDENTITY_NOW)
            self.assertEqual(("PARTIAL",5,6,1),(result["status"],len(client.calls),
                                                result["missing_reviewed_facilities"],self.coverage(service)["current_partial"]))
            self.assertEqual(10,result["daily_request_limit"])

    def test_duplicate_frs_in_one_response_cannot_create_verified_ownership(self):
        row={"FacName":"EXAMPLE ENTITY INC","RegistryID":"110018431295"}
        with TemporaryDirectory() as directory:
            store=self.seed(directory)
            service=EpaEchoScoutService(store,client=FakeEpa(rows=[row,dict(row)]),
                                       identities=RegulatoryIdentityRegistry([synthetic_record()]))
            result=service.run(as_of=IDENTITY_NOW)
            self.assertEqual(("PARTIAL",0,1,1),(result["status"],result["new_findings"],result["rejected_rows"],
                                                result["missing_reviewed_facilities"]))

    def test_repeat_facility_across_exact_queries_is_deduplicated_without_losing_scope(self):
        with TemporaryDirectory() as directory:
            store=self.seed(directory,name="Example Entity Inc.")
            service=EpaEchoScoutService(store,client=FakeEpa(),identities=RegulatoryIdentityRegistry([synthetic_record()]))
            result=service.run(as_of=IDENTITY_NOW)
            self.assertEqual(("OK",1,1,2,0),(result["status"],result["new_findings"],result["verified_facilities"],
                                             result["attempted_requests"],result["rejected_rows"]))
            detail=json.loads(store.findings_as_of("ONE",as_of=IDENTITY_NOW)[0]["details_json"])
            self.assertEqual(["EXAMPLE ENTITY INC","Example Entity Inc."],detail["matched_query_names"])

    def test_second_query_access_failure_retains_first_result_as_partial(self):
        class LaterDenied(FakeEpa):
            def facilities(self,name):
                if self.calls:
                    raise HTTPError(EPA_ECHO_URL,403,"Denied",{},None)
                return super().facilities(name)
        with TemporaryDirectory() as directory:
            store=self.seed(directory,name="Example Entity Inc.")
            service=EpaEchoScoutService(store,client=LaterDenied(),identities=RegulatoryIdentityRegistry([synthetic_record()]))
            result=service.run(as_of=IDENTITY_NOW)
            self.assertEqual(("ACCESS_BLOCKED",1,1,1),(result["status"],result["new_findings"],
                                                      result["verified_facilities"],self.coverage(service)["current_partial"]))

    def test_expired_relationship_removes_queries_and_makes_old_complete_scope_due(self):
        registry=RegulatoryIdentityRegistry([synthetic_record(effective_to="2026-10-12")])
        with TemporaryDirectory() as directory:
            store=self.seed(directory,name="Example Entity Inc.")
            service=EpaEchoScoutService(store,client=FakeEpa(),identities=registry)
            self.assertEqual(1,service.run(as_of=IDENTITY_NOW)["verified_facilities"])
            tomorrow=IDENTITY_NOW+timedelta(days=1)
            self.assertEqual(0,self.coverage(service,as_of=tomorrow)["current_complete"])
            result=service.run(as_of=tomorrow)
            self.assertEqual(("OK",0,1,1),(result["status"],result["verified_facilities"],
                                           result["unresolved_facilities"],result["attempted_requests"]))

    def test_malformed_response_does_not_create_a_complete_negative_check(self):
        for rows,total in (([],True),([], -1),([{}],1),([],"0")):
            with self.subTest(rows=rows,total=total), TemporaryDirectory() as directory:
                store=self.seed(directory)
                service=EpaEchoScoutService(store,client=FakeEpa(rows=rows,total=total))
                result=service.run(as_of=IDENTITY_NOW)
                self.assertEqual("PARTIAL",result["status"])
                self.assertEqual(0,self.coverage(service)["current_complete"])
                self.assertFalse(result["issuer_liability_negative_verified"])


if __name__ == "__main__":
    unittest.main()
