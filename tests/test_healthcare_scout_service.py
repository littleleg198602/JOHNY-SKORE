from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from market_checker_app.services.healthcare_scout_service import (
    CMS_URL, TRIALS_URL, CmsHospitalOwnerClient, ClinicalTrialsClient,
    HealthcareNameScoutService,
)
from market_checker_app.storage.scout_store import ScoutStore

NOW = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
CMS_ROW = {"ENROLLMENT ID": "TEST-1", "ASSOCIATE ID - OWNER": "001",
           "TYPE - OWNER": "O", "ORGANIZATION NAME - OWNER": "Example Hospital Owner Inc.",
           "ORGANIZATION NAME": "Example Hospital LLC", "ASSOCIATION DATE - OWNER": "2025-01-01",
           "ROLE CODE - OWNER": "35", "ROLE TEXT - OWNER": "Indirect owner", "PERCENTAGE OWNERSHIP": "80"}
TRIAL = {"protocolSection": {
    "identificationModule": {"nctId": "NCT00000001", "briefTitle": "Example study"},
    "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Example Sponsor Inc."}},
    "statusModule": {"overallStatus": "COMPLETED", "lastUpdatePostDateStruct": {"date": "2026-09-01"}},
    "designModule": {"phases": ["PHASE2"]}}, "hasResults": True}


class FakeClient:
    def __init__(self, source="cms", pages=None, error=None):
        self.source = source
        self.source_url = CMS_URL if source == "cms" else TRIALS_URL
        self.pages = pages if pages is not None else [([deepcopy(CMS_ROW)], None)]
        self.error, self.calls = error, []

    def page(self, name, *, size, cursor):
        self.calls.append((name, size, cursor))
        if self.error:
            raise self.error
        return self.pages[min(len(self.calls) - 1, len(self.pages) - 1)]


class HealthcareScoutTests(unittest.TestCase):
    def store(self, directory, ticker="HCA", name="Example Hospital Owner Inc."):
        store = ScoutStore(Path(directory) / "test.db")
        store.observe_sec_identity(subject_id=ticker, cik="0000000001", company_name=name, as_of=NOW)
        return store

    def test_cms_exact_organization_name_candidate_has_no_issuer_attribution(self):
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            service = HealthcareNameScoutService(store, client=FakeClient())
            summary = service.run(as_of=NOW, universe={"HCA"})
            self.assertEqual(("OK", 1), (summary["status"], summary["new_findings"]))
            self.assertEqual(0, service.run(as_of=NOW)["checked_issuers"])
            self.assertEqual(0, service.run(as_of=NOW + timedelta(days=31))["new_findings"])
            rows = store.latest_findings(["HCA"], as_of=NOW, source="cms")
            self.assertEqual(1, len(rows))
            self.assertEqual("UNVERIFIED", rows[0]["verification_status"])
            with store._connect() as conn:
                import json
                details = json.loads(conn.execute("SELECT details_json FROM scout_findings").fetchone()[0])
            self.assertFalse(details["issuer_attribution_allowed"])
            self.assertFalse(details["association_date_is_publication_time"])
            self.assertNotIn("ADDRESS LINE 1 - OWNER", details)

    def test_cms_mismatch_individual_future_and_invalid_percentage_remain_partial(self):
        for changed in ({"ORGANIZATION NAME - OWNER": "Different Owner"}, {"TYPE - OWNER": "I"},
                        {"ASSOCIATION DATE - OWNER": "2027-01-01"}, {"PERCENTAGE OWNERSHIP": "101"},
                        {"PERCENTAGE OWNERSHIP": "NaN"}):
            with self.subTest(changed=changed), TemporaryDirectory() as directory:
                store = self.store(directory)
                client = FakeClient(pages=[([dict(CMS_ROW, **changed)], None)])
                service = HealthcareNameScoutService(store, client=client)
                result = service.run(as_of=NOW)
                self.assertEqual(("PARTIAL", 0, 1),
                                 (result["status"], result["new_findings"], result["rejected_rows"]))
                self.assertEqual(1, service.run(as_of=NOW + timedelta(days=1))["checked_issuers"])

    def test_clinical_sponsor_role_and_posted_time_are_preserved(self):
        with TemporaryDirectory() as directory:
            store = self.store(directory, "PFE", "Example Sponsor Inc.")
            service = HealthcareNameScoutService(store, client=FakeClient(
                source="clinicaltrials", pages=[([deepcopy(TRIAL)], None)]))
            self.assertEqual(1, service.run(as_of=NOW)["new_findings"])
            self.assertEqual(1, len(store.latest_findings(["PFE"], as_of=NOW, source="clinicaltrials")))
            self.assertEqual([], store.latest_findings(["PFE"], as_of=NOW - timedelta(seconds=1),
                                                      source="clinicaltrials"))

    def test_timeout_between_pages_leaves_issuer_due_and_commits_no_partial_rows(self):
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            client = FakeClient(pages=[([deepcopy(CMS_ROW)], "1")])
            service = HealthcareNameScoutService(store, client=client)
            with patch("market_checker_app.services.healthcare_scout_service.time.monotonic",
                       side_effect=[0, 0, 0, 61]):
                result = service.run(as_of=NOW)
            self.assertEqual(("PARTIAL", 0, True),
                             (result["status"], result["checked_issuers"], result["budget_exhausted"]))
            self.assertEqual(1, len(client.calls))
            self.assertEqual([], store.latest_findings(["HCA"], as_of=NOW, source="cms"))
            self.assertEqual(0, store.specialist_coverage("cms", as_of=NOW)["ever_checked"])

    def test_source_outage_stops_at_failure_budget_and_leaves_other_issuers_due(self):
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            store.observe_sec_identity(subject_id="UHS", cik="0000000002",
                                       company_name="Other Hospital Owner Inc.", as_of=NOW)
            client = FakeClient(error=OSError("unavailable"))
            result = HealthcareNameScoutService(store, client=client, max_failures=1).run(as_of=NOW)
            self.assertEqual(("PARTIAL", 1, True),
                             (result["status"], result["failed_issuers"], result["budget_exhausted"]))
            self.assertEqual(1, len(client.calls))
            self.assertEqual(0, store.specialist_coverage("cms", as_of=NOW)["ever_checked"])

    def test_clinical_collaborator_is_not_lead_sponsor_and_future_version_rejected(self):
        with TemporaryDirectory() as directory:
            store = self.store(directory, "PFE", "Example Sponsor Inc.")
            row = deepcopy(TRIAL)
            row["protocolSection"]["sponsorCollaboratorsModule"] = {
                "leadSponsor": {"name": "Other Sponsor"},
                "collaborators": [{"name": "Example Sponsor Inc."}]}
            client = FakeClient(source="clinicaltrials", pages=[([row], None)])
            service = HealthcareNameScoutService(store, client=client)
            self.assertEqual(1, service.run(as_of=NOW)["new_findings"])
            row["protocolSection"]["statusModule"]["lastUpdatePostDateStruct"]["date"] = "2027-01-01"
            self.assertEqual("PARTIAL", service.run(as_of=NOW + timedelta(days=31))["status"])

    def test_page_cap_and_duplicate_page_do_not_mark_complete(self):
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            client = FakeClient(pages=[([deepcopy(CMS_ROW)], "1"), ([deepcopy(CMS_ROW)], "2")])
            result = HealthcareNameScoutService(store, client=client, page_size=1).run(as_of=NOW)
            self.assertEqual(("PARTIAL", 1, 1),
                             (result["status"], result["new_findings"], result["truncated_issuers"]))
            self.assertEqual(1, store.specialist_coverage("cms", as_of=NOW, subjects={"HCA"})["current_partial"])
            self.assertEqual(0, store.specialist_coverage("cms", as_of=NOW, subjects={"NVDA"})["active_identities"])

    def test_outage_does_not_complete_identity_and_rate_limit_stops_batch(self):
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            client = FakeClient(error=OSError("temporary failure"))
            service = HealthcareNameScoutService(store, client=client)
            self.assertEqual("PARTIAL", service.run(as_of=NOW)["status"])
            client.error = HTTPError(CMS_URL, 429, "Too many", {}, None)
            self.assertEqual("RATE_LIMITED", service.run(as_of=NOW)["status"])
            self.assertEqual(0, store.specialist_coverage("cms", as_of=NOW)["ever_checked"])

    def test_profile_scope_never_queries_unrelated_issuer(self):
        with TemporaryDirectory() as directory:
            store = self.store(directory, "NVDA", "Nvidia Inc.")
            client = FakeClient()
            self.assertEqual("WAIT_IDENTITY", HealthcareNameScoutService(store, client=client).run(as_of=NOW)["status"])
            self.assertEqual("NOT_APPLICABLE", HealthcareNameScoutService(store, client=client).run(
                as_of=NOW, universe={"NVDA"})["status"])
            self.assertEqual([], client.calls)

    def test_client_schema_and_storage_reject_wrong_host_and_verification(self):
        with patch("market_checker_app.services.healthcare_scout_service._json", return_value={}):
            with self.assertRaises(ValueError):
                CmsHospitalOwnerClient().page("Owner", size=1)
        with patch("market_checker_app.services.healthcare_scout_service._json",
                   return_value={"studies": [], "nextPageToken": 4}):
            with self.assertRaises(ValueError):
                ClinicalTrialsClient().page("Sponsor", size=1)
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            args = dict(source="cms", subject_id="HCA", source_object_id="x", content_hash="x",
                        title="x", locator="x", published_at=NOW, available_at=NOW, observed_at=NOW, details={})
            with self.assertRaisesRegex(ValueError, "official dataset"):
                store.record_finding(source_url="https://example.org/data", verification_status="UNVERIFIED", **args)
            with self.assertRaisesRegex(ValueError, "cannot verify"):
                store.record_finding(source_url=CMS_URL, **args)


if __name__ == "__main__":
    unittest.main()
