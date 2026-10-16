"""Regression checks for real study-sponsor captures and pinned object scope."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from evidence.batch4_healthcare_20261013.replay import (
    ROOT, ISSUER, STUDY_IDS, ReplayTransport, build_replay_report, capture,
    normalized_html, read_json,
)
from market_checker_app.services.healthcare_identity_service import (
    HealthcareIdentityRegistry, clinical_trial_version,
)
from market_checker_app.services.healthcare_scout_service import (
    ClinicalTrialsClient, HealthcareNameScoutService, _candidate, _identity_details,
)
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.utils.ticker_universe import load_canonical_tickers


class Batch4HealthcareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = read_json(ROOT / "identity_proposal.json")
        cls.registry = HealthcareIdentityRegistry.load(ROOT / "identity_proposal.json")
        cls.clock = datetime.fromisoformat(cls.manifest["reviewed_at"]) + timedelta(seconds=1)
        cls.report = build_replay_report()
        cls.source_row = json.loads(capture("trial_surpass2_complete_record")[1])

    def detail(self, row=None, name=ISSUER["company_name"]):
        matched = _candidate("clinicaltrials", self.source_row if row is None else row, name, self.clock)
        self.assertIsNotNone(matched)
        return matched[1]

    def test_original_bodies_have_exact_integrity_explicit_real_clocks_and_primary_hosts(self):
        index = read_json(ROOT / "capture_index.json")
        self.assertEqual(10, index["request_count"])
        self.assertEqual(10, len(self.report["verified_captures"]))
        for entry in index["captures"]:
            with self.subTest(entry=entry["capture_key"]):
                meta, raw = capture(entry["capture_key"])
                requested, observed = map(datetime.fromisoformat,
                                          (meta["requested_at"], meta["observed_at"]))
                self.assertIsNotNone(requested.utcoffset())
                self.assertLessEqual(requested, observed)
                self.assertEqual(date(2026, 10, 13), observed.date())
                self.assertIn(urlsplit(meta["request_url"]).hostname,
                              {"www.sec.gov", "data.sec.gov", "clinicaltrials.gov"})
                self.assertLessEqual(len(raw), meta["byte_budget"])
                self.assertNotIn("set-cookie", meta["response_headers"])
        self.assertEqual(0, self.report["network_requests"])

    def test_capture_corruption_and_incomplete_bytes_cannot_be_replayed_as_proof(self):
        meta, raw = capture("trial_surpass2_complete_record")
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "captures").mkdir()
            path = root / "captures" / "trial_surpass2_complete_record.metadata.json"
            path.write_text(json.dumps(meta))
            body = root / "captures" / meta["body_path"]
            body.write_bytes(raw + b" ")
            with self.assertRaisesRegex(ValueError, "integrity"):
                capture("trial_surpass2_complete_record", root)
            body.write_bytes(raw)
            path.write_text(json.dumps(dict(meta, body_complete_within_byte_budget=False)))
            with self.assertRaisesRegex(ValueError, "budget"):
                capture("trial_surpass2_complete_record", root)

    def test_real_pagination_preserves_missing_later_totals_and_terminal_empty_page(self):
        first = json.loads(capture("trial_lilly_positive_page_1")[1])
        second = json.loads(capture("trial_lilly_positive_page_2")[1])
        terminal = json.loads(capture("trial_lilly_positive_page_3")[1])
        self.assertEqual(2, first["totalCount"])
        self.assertNotIn("totalCount", second)
        self.assertIn("nextPageToken", second)
        self.assertEqual({"studies": []}, terminal)
        self.assertEqual({"study_ids": list(STUDY_IDS), "pages": 3, "publisher_total": 2,
                          "returned_count": 2, "complete": True}, self.report["bounded_positive_query"])

    def test_capture_publisher_and_clock_tampering_is_rejected_before_replay(self):
        meta, raw = capture("trial_surpass2_complete_record")
        for change in ({"request_url": "https://example.org/study", "response_url": "https://example.org/study"},
                       {"requested_at": "2026-10-13T00:00:00"},
                       {"requested_at": "2026-10-14T00:00:00+00:00"}):
            with self.subTest(change=change), TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "captures").mkdir()
                (root / "captures" / meta["body_path"]).write_bytes(raw)
                (root / "captures" / "trial_surpass2_complete_record.metadata.json").write_text(
                    json.dumps(dict(meta, **change)))
                with self.assertRaisesRegex(ValueError, "publisher|clocks"):
                    capture("trial_surpass2_complete_record", root)

    def test_successful_negative_query_has_exact_study_and_sponsor_scope(self):
        meta, raw = capture("trial_pfizer_exact_study_negative")
        self.assertEqual(200, meta["http_status"])
        self.assertEqual({"totalCount": 0, "studies": []}, json.loads(raw))
        query = parse_qs(urlsplit(meta["request_url"]).query)
        self.assertEqual(["Pfizer"], query["query.spons"])
        self.assertEqual([",".join(STUDY_IDS)], query["filter.ids"])
        self.assertTrue(self.report["bounded_negative_query"]["complete"])
        self.assertFalse(self.report["issuer_wide_completeness"])

    def test_independent_source_year_issuer_and_product_fields_are_in_archived_bodies(self):
        review = read_json(ROOT / "source_review.json")
        self.assertEqual(3, len(review["independent_issuer_reviews"]))
        for item in review["independent_issuer_reviews"]:
            with self.subTest(key=item["capture_key"]):
                meta, raw = capture(item["capture_key"])
                self.assertEqual("59478", urlsplit(meta["request_url"]).path.split("/")[4])
                text = normalized_html(raw)
                self.assertIn("0000059478", text)
                for excerpt in item["excerpts"]:
                    self.assertEqual(excerpt["text"],
                                     text[excerpt["normalized_text_start"]:excerpt["normalized_text_end"]])
        submissions = json.loads(capture("sec_lly_submissions")[1])
        self.assertEqual("0000059478", str(submissions["cik"]).zfill(10))
        self.assertIn("LLY", submissions["tickers"])
        recent = submissions["filings"]["recent"]
        filings = {recent["primaryDocument"][i]: recent["filingDate"][i]
                   for i, form in enumerate(recent["form"]) if form == "10-K"}
        self.assertEqual("2022-02-23", filings["lly-20211231.htm"])
        self.assertEqual("2025-02-19", filings["lly-20241231.htm"])
        self.assertEqual("2026-02-12", filings["lly-20251231.htm"])

    def test_projected_and_full_record_views_pin_the_same_sponsor_relationship(self):
        projected = json.loads(capture("trial_lilly_positive_page_2")[1])["studies"][0]
        full, view = clinical_trial_version(self.source_row), clinical_trial_version(projected)
        self.assertEqual(full["relationship_snapshot_sha256"], view["relationship_snapshot_sha256"])
        self.assertEqual("ACTUAL", full["record_version_type"])
        self.assertIsNone(view["record_version_type"])
        self.assertEqual("2022-02-14", view["record_version_date"])
        self.assertEqual("2019-06-17", view["study_first_posted"])
        self.assertEqual("2022-01-21", view["last_update_submitted"])
        self.assertEqual("DAY", view["publisher_clock_precision"])
        self.assertFalse(view["publisher_version_id_available"])
        self.assertFalse(view["publisher_clocks_are_first_observation"])

    def test_comparator_and_placebo_never_inherit_lilly_product_attribution(self):
        objects = {row["nct_id"]: row for row in self.report["objects"]}
        for row in objects.values():
            self.assertEqual("EXACT_DATED_OBJECT", row["identity_resolution"])
            self.assertTrue(row["relationship_snapshot_pinned"])
            self.assertEqual(["Tirzepatide"], row["attributed_intervention_names"])
            self.assertFalse(row["sponsor_role_establishes_product_ownership"])
        self.assertEqual(["Semaglutide"], objects["NCT03987919"]["unattributed_intervention_names"])
        self.assertEqual(["Placebo"], objects["NCT04847557"]["unattributed_intervention_names"])

    def test_same_posted_day_changed_sponsor_scope_cannot_inherit_a_pinned_link(self):
        changed = deepcopy(self.source_row)
        changed["protocolSection"]["sponsorCollaboratorsModule"]["collaborators"] = [{"name": "Other Sponsor"}]
        original = self.detail()
        revised = self.detail(changed)
        self.assertEqual(original["last_update_posted"], revised["last_update_posted"])
        self.assertNotEqual(original["relationship_snapshot_sha256"], revised["relationship_snapshot_sha256"])
        self.assertEqual("UNMAPPED_OBJECT", _identity_details(
            self.registry, "clinicaltrials", revised, ISSUER, self.clock)["identity_resolution"])

    def test_same_day_intervention_change_and_unmapped_nct_remain_unattributed(self):
        for change in ("intervention", "nct"):
            with self.subTest(change=change):
                row = deepcopy(self.source_row)
                if change == "intervention":
                    row["protocolSection"]["armsInterventionsModule"]["interventions"].append({"name": "Unmapped drug"})
                else:
                    row["protocolSection"]["identificationModule"]["nctId"] = "NCT00000001"
                result = _identity_details(self.registry, "clinicaltrials", self.detail(row), ISSUER, self.clock)
                self.assertFalse(result["issuer_attribution_allowed"])

    def test_exact_object_time_and_first_knowledge_are_separate(self):
        self.assertIn("LLY", load_canonical_tickers())
        for record in self.registry.records():
            args = dict(source="clinicaltrials", kind="clinical_trial_sponsor", ticker="LLY",
                        cik=ISSUER["cik"], entity_name=ISSUER["company_name"],
                        identifiers=record["identifiers"], event_date=date.fromisoformat(record["effective_from"]))
            self.assertEqual("NOT_YET_KNOWN", self.registry.resolve(
                as_of=datetime.fromisoformat(record["known_at"]) - timedelta(microseconds=1), **args)["identity_resolution"])
            self.assertEqual("WRONG_ISSUER", self.registry.resolve(
                as_of=self.clock, **dict(args, ticker="PFE", cik="0000078003"))["identity_resolution"])
            self.assertEqual("OUTSIDE_EFFECTIVE_PERIOD", self.registry.resolve(
                as_of=self.clock, **dict(args, event_date=self.clock.date()))["identity_resolution"])
            self.assertLess(date.fromisoformat(record["identifiers"]["record_version_date"]),
                            datetime.fromisoformat(record["known_at"]).date())
            self.assertFalse(record["historical_legal_continuity_proven"])

    def test_conflicting_estimated_or_malformed_publisher_clocks_are_rejected(self):
        mutations = [{"studyFirstPostDateStruct": {"date": "2023-01-01"}},
                     {"lastUpdateSubmitDate": "2023-01-01"},
                     {"lastUpdateSubmitDate": "not-a-date"},
                     {"lastUpdatePostDateStruct": {"date": "2022-02-14", "type": "ESTIMATED"}},
                     {"studyFirstPostDateStruct": {"date": "2019-06-17", "type": "ESTIMATED"}},
                     {"lastUpdatePostDateStruct": {"date": "20220214"}}]
        for changes in mutations:
            with self.subTest(changes=changes):
                row = deepcopy(self.source_row)
                row["protocolSection"]["statusModule"].update(changes)
                with self.assertRaises(ValueError):
                    clinical_trial_version(row)
                self.assertIsNone(_candidate("clinicaltrials", row, ISSUER["company_name"], self.clock))

    def test_future_posted_version_and_different_named_sponsor_are_rejected(self):
        row = deepcopy(self.source_row)
        row["protocolSection"]["statusModule"]["lastUpdatePostDateStruct"]["date"] = "2030-01-01"
        self.assertIsNone(_candidate("clinicaltrials", row, ISSUER["company_name"], self.clock))
        self.assertIsNone(_candidate("clinicaltrials", self.source_row, "Lilly", self.clock))

    def test_invalid_intervention_scope_is_partial_and_never_a_completed_negative(self):
        row = deepcopy(self.source_row)
        row["protocolSection"]["armsInterventionsModule"]["interventions"] = "Tirzepatide"
        with TemporaryDirectory() as temporary:
            store = ScoutStore(Path(temporary) / "scout.db")
            store.observe_sec_identity(subject_id="LLY", cik=ISSUER["cik"],
                                       company_name=ISSUER["company_name"], as_of=self.clock)
            service = HealthcareNameScoutService(store, client=ClinicalTrialsClient(), identities=self.registry)
            with patch("market_checker_app.services.healthcare_scout_service._json",
                       return_value={"totalCount": 1, "studies": [row]}):
                result = service.run(as_of=self.clock, universe={"LLY"})
            self.assertEqual(("PARTIAL", 0, 1), (result["status"], result["new_findings"], result["rejected_rows"]))
            self.assertEqual(0, store.specialist_coverage("clinicaltrials", as_of=self.clock)["current_complete"])

    def test_bad_sponsor_or_intervention_items_do_not_silently_disappear(self):
        for module, key, value in (
            ("sponsorCollaboratorsModule", "collaborators", [{"name": None}]),
            ("sponsorCollaboratorsModule", "collaborators", "Sponsor"),
            ("armsInterventionsModule", "interventions", [None]),
            ("armsInterventionsModule", "interventions", [{"name": ""}])):
            with self.subTest(module=module, value=value):
                row = deepcopy(self.source_row)
                row["protocolSection"][module][key] = value
                with self.assertRaises(ValueError):
                    clinical_trial_version(row)

    def test_study_filter_is_bounded_and_client_rejects_out_of_scope_returned_study(self):
        for value in (["NCT03987919"], ("NCT03987919",) * 2,
                      ("NCT00000001",) * 21, ("AREA[Sponsor]Lilly",), ([],)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ClinicalTrialsClient(study_ids=value)
        with patch("market_checker_app.services.healthcare_scout_service._json",
                   return_value={"totalCount": 1, "studies": [self.source_row]}):
            with self.assertRaisesRegex(ValueError, "exact study scope"):
                ClinicalTrialsClient(study_ids=("NCT04847557",)).page(ISSUER["company_name"], size=1)

    def test_inconsistent_first_page_count_cannot_be_a_complete_query(self):
        for payload in ({"studies": []}, {"totalCount": True, "studies": []},
                        {"totalCount": -1, "studies": []}, {"totalCount": 2, "studies": []},
                        {"totalCount": 0, "studies": [self.source_row]}):
            with self.subTest(payload=payload), patch(
                    "market_checker_app.services.healthcare_scout_service._json", return_value=payload):
                with self.assertRaisesRegex(ValueError, "query count"):
                    ClinicalTrialsClient().page(ISSUER["company_name"], size=1)

    def test_pinned_digest_requires_exact_sha256_and_legacy_keys_remain_supported(self):
        record = deepcopy(self.manifest["records"][0])
        for value in ("x", "A" * 64, "0" * 63):
            bad = deepcopy(record)
            bad["identifiers"]["relationship_snapshot_sha256"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                HealthcareIdentityRegistry([bad])
        record["identifiers"].pop("relationship_snapshot_sha256")
        registry = HealthcareIdentityRegistry([record])
        result = _identity_details(registry, "clinicaltrials", self.detail(), ISSUER, self.clock)
        self.assertTrue(result["issuer_attribution_allowed"])
        self.assertFalse(result["relationship_snapshot_pinned"])

    def test_snapshot_validation_invalidates_prior_completed_clinical_scope(self):
        with TemporaryDirectory() as temporary:
            store = ScoutStore(Path(temporary) / "scout.db")
            store.observe_sec_identity(subject_id="LLY", cik=ISSUER["cik"],
                                       company_name=ISSUER["company_name"], as_of=self.clock)
            empty = HealthcareIdentityRegistry([])
            prior_fingerprint = hashlib.sha256(json.dumps(
                {"resolver_version": "healthcare-object-v1", "records": []}, sort_keys=True).encode()).hexdigest()
            prior = hashlib.sha256(json.dumps({"source": "clinicaltrials", "sec_identity": ISSUER,
                                              "dated_records_sha256": prior_fingerprint}, sort_keys=True).encode()).hexdigest()
            store.record_specialist_check("clinicaltrials", subject_id="LLY",
                identity_key=f"{ISSUER['cik']}:{ISSUER['company_name']}", as_of=self.clock,
                candidate_count=0, truncated=False, identity_version=prior)
            versions = empty.identity_versions(store, "clinicaltrials", as_of=self.clock)
            self.assertNotEqual(prior, versions["LLY"])
            self.assertEqual(1, len(store.specialist_due("clinicaltrials", as_of=self.clock,
                                                        limit=10, identity_versions=versions)))
            self.assertEqual(0, store.specialist_coverage("clinicaltrials", as_of=self.clock,
                                                         identity_versions=versions)["current_complete"])

    def test_exact_study_positive_replay_never_completes_broad_source_check_or_scores(self):
        self.assertEqual(("PARTIAL", 2), (self.report["first_collector_run"]["status"],
                                        self.report["first_collector_run"]["new_findings"]))
        self.assertEqual(0, self.report["idempotent_replay"]["new_findings"])
        self.assertEqual(2, self.report["finding_count"])
        self.assertEqual(0, self.report["broad_source_coverage"]["current_complete"])
        self.assertEqual(1, self.report["broad_source_coverage"]["current_partial"])
        self.assertEqual(["UNVERIFIED", "UNVERIFIED"], self.report["finding_statuses"])
        self.assertEqual(0, self.report["findings_available_before_observation"])
        self.assertFalse(self.report["scoring_applied"])
        for key in ("historical_source_availability_established", "OOS_verified",
                    "user_windows_run_verified", "full_specialist_acceptance"):
            self.assertFalse(self.report[key])

    def test_new_legacy_competing_issuer_invalidates_a_pinned_trial_scope(self):
        own = deepcopy(self.manifest["records"][0])
        competing = deepcopy(own)
        competing.update(record_id="synthetic-competing-date-only-link", ticker="PFE",
                         issuer_cik="0000078003", known_at=(self.clock + timedelta(days=1)).isoformat())
        competing["identifiers"].pop("relationship_snapshot_sha256")
        for evidence in competing["evidence"]:
            if evidence["purpose"] == "ISSUER_IDENTITY":
                evidence["url"] = "https://www.sec.gov/Archives/edgar/data/78003/test/synthetic-issuer.htm"
        original = HealthcareIdentityRegistry([own])
        contested = HealthcareIdentityRegistry([own, competing])
        scope = dict(source="clinicaltrials", ticker="LLY", cik=ISSUER["cik"], as_of=self.clock)
        self.assertEqual(original.fingerprint(**scope), contested.fingerprint(**scope))
        scope["as_of"] += timedelta(days=1)
        self.assertNotEqual(original.fingerprint(**scope), contested.fingerprint(**scope))
        resolved = contested.resolve(kind="clinical_trial_sponsor", entity_name=ISSUER["company_name"],
                                     identifiers=own["identifiers"],
                                     event_date=date.fromisoformat(own["effective_from"]), **scope)
        self.assertEqual("AMBIGUOUS_OBJECT", resolved["identity_resolution"])
        self.assertFalse(resolved["issuer_attribution_allowed"])

    def test_zero_rows_from_an_exact_study_scope_do_not_complete_the_issuer_check(self):
        with TemporaryDirectory() as temporary:
            store = ScoutStore(Path(temporary) / "scout.db")
            store.observe_sec_identity(subject_id="PFE", cik="0000078003",
                                       company_name="Pfizer", as_of=self.clock)
            service = HealthcareNameScoutService(store, client=ClinicalTrialsClient(study_ids=STUDY_IDS),
                                                 identities=self.registry, page_size=2)
            with patch("market_checker_app.services.healthcare_scout_service.urlopen", ReplayTransport()):
                result = service.run(as_of=self.clock, universe={"PFE"})
            self.assertEqual(("PARTIAL", 0, 1), (result["status"], result["new_findings"], result["truncated_issuers"]))
            self.assertEqual(0, store.specialist_coverage("clinicaltrials", as_of=self.clock)["current_complete"])


if __name__ == "__main__":
    unittest.main()
