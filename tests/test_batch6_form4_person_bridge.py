from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from evidence.batch6_sec_20261014.build_review import build_review_artifact, load_inputs
from market_checker_app.services.sec_form4_person_bridge import (
    Form4PersonBridgeError, SECPersonCapture, review_form4_person_bridge,
)


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_NEW_HASHES = {
    "nadella_2014_form3.txt": "9123343610f3b95c84a0be6d172fea451ccc45139ad1d9d88f5ad91a343a5053",
    "nadella_2014_form3.xml": "5c118f5e7a6f68bbb5d46d0eea956d8cbabb2c3ff935af12cfa5d31aafccce74",
    "nadella_2014_form3_index.html": "2bf75c1d69b113408d1da2d863504f1d6f18ce28f6f9d425ac2609c58e434026",
}


def modified(capture, body, **kwargs):
    return replace(capture, body=body, sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body), **kwargs)


class Batch6Form4PersonBridgeTests(unittest.TestCase):
    def setUp(self):
        self.observation, self.source, self.evidence, self.manifest = load_inputs()
        self.profile, self.packet = self.evidence
        self.clock = max(item.available_at for item in (self.source, *self.evidence))

    def review(self, *, observation=None, source=None, evidence=None, clock=None, mode="EVENT_DATE"):
        return review_form4_person_bridge(
            observation or self.observation, source_capture=source or self.source,
            evidence=self.evidence if evidence is None else evidence,
            knowledge_at=clock or self.clock, scope_mode=mode,
        )

    def test_actual_original_captures_and_exact_review_are_reproducible(self):
        artifact = build_review_artifact()
        self.assertEqual(json.loads((ROOT / "evidence/batch6_sec_20261014/person_bridge_review.json").read_bytes()), artifact)
        self.assertEqual(3, artifact["actual_new_primary_http_requests"])
        self.assertEqual(1, artifact["new_disclosure_count"])
        self.assertTrue(artifact["same_accession_documents_not_independent_sources"])
        for filename, digest in EXPECTED_NEW_HASHES.items():
            raw = (ROOT / "evidence/batch6_sec_20261014/primary" / filename).read_bytes()
            self.assertEqual(digest, hashlib.sha256(raw).hexdigest())
        self.assertEqual(0, artifact["person_verified_rows"])
        self.assertFalse(artifact["quota_managed_application_live_claim"])

    def test_actual_independent_header_and_xml_corroborate_owner_account_without_person_promotion(self):
        report = self.review()
        packet = next(row for row in report["independent_evidence"] if row["scope_mode"] == "DATED_DISCLOSURE_POINT")
        self.assertEqual([{"cik": "0001513142", "name": "Nadella Satya"}], packet["owners"])
        self.assertEqual("0000789019", packet["issuer_as_filed"]["cik"])
        self.assertEqual("2014-02-04", packet["report_day"])
        self.assertEqual("2014-02-07", packet["filing_day"])
        self.assertEqual(8, report["source_row_count"])
        self.assertEqual(8, report["source_only_account_corroborated_rows"])
        for row in report["row_reviews"]:
            self.assertEqual("WITHHELD", row["status"])
            self.assertIn("NATURAL_PERSON_KIND_AND_LEGAL_NAME_UNRESOLVED", row["reasons"])
            self.assertIn("NO_DATED_BRIDGE_FOR_REQUESTED_EVENT", row["reasons"])
            self.assertFalse(row["person_identity_independently_verified"])
            self.assertFalse(row["instrument_identity_independently_verified"])
            self.assertFalse(row["issuer_attribution_allowed"])

    def test_current_other_profile_is_an_observed_snapshot_not_historical_person_kind(self):
        report = self.review()
        profile = next(row for row in report["independent_evidence"] if row["scope_mode"] == "OBSERVED_SNAPSHOT")
        self.assertEqual("other", profile["publisher_entity_type"])
        self.assertEqual("2026-10-13", profile["snapshot_day"])
        self.assertFalse(profile["natural_person_kind_proven"])
        self.assertFalse(profile["historical_effective_date_proven"])
        for row in report["row_reviews"]:
            self.assertFalse(any(match["requested_event_day_matches"] for match in row["exact_source_account_matches"]))

    def test_observed_mode_corroborates_account_without_extending_event_dates_or_person_identity(self):
        report = self.review(mode="OBSERVED_SNAPSHOT")
        self.assertEqual("OBSERVED_SNAPSHOT", report["scope_mode"])
        self.assertEqual(0, report["person_verified_row_count"])
        self.assertFalse(report["historical_disclosure_capture_is_historical_knowledge"])
        for row in report["row_reviews"]:
            self.assertTrue(row["independent_account_source_corroborated"])
            self.assertTrue(all(match["requested_scope_matches"] for match in row["exact_source_account_matches"]))
            self.assertFalse(any(match["requested_event_day_matches"] for match in row["exact_source_account_matches"]))
            self.assertEqual("WITHHELD", row["status"])

    def test_attorney_and_name_order_spelling_are_unapproved_aliases_not_person_kind(self):
        report = self.review()
        packet = report["independent_evidence"][1]
        self.assertEqual("Keith R. Dolliver, Attorney-in-Fact for Satya Nadella", packet["signatures"][0]["signature_name_raw"])
        self.assertFalse(packet["signature_names_are_approved_aliases"])
        self.assertFalse(packet["natural_person_kind_proven"])
        self.assertFalse(report["role_or_signature_aliases_approved"])

    def test_raw_acceptance_clock_remains_unqualified_and_discovery_z_clock_distinct(self):
        artifact = build_review_artifact()
        packet = artifact["event_date_review"]["independent_evidence"][1]
        self.assertEqual("20140207180916", packet["acceptance_datetime_unqualified_raw"])
        self.assertIsNone(packet["published_at"])
        self.assertFalse(packet["acceptance_timezone_inferred"])
        self.assertEqual("2014-02-07T23:09:16.000Z", artifact["discovery_pointer_only"]["profile_acceptance_datetime_raw"])
        self.assertFalse(artifact["acceptance_timezone_reconciled_or_inferred"])

    def test_reader_does_no_http_store_registry_write_or_mutation_of_source_rows(self):
        before = deepcopy(self.observation)
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("no HTTP")), \
                patch("market_checker_app.storage.scout_store.ScoutStore.__init__", side_effect=AssertionError("no store")):
            report = self.review()
        self.assertEqual(before, self.observation)
        self.assertEqual(0, report["network_requests_performed"])
        self.assertFalse(report["quota_or_lease_modified"])
        self.assertFalse(report["registry_modified"])
        self.assertFalse(report["scoring_applied"])
        self.assertFalse(report["trading_allowed"])

    def test_same_accession_owner_directory_xsl_or_index_alias_never_counts_as_independent(self):
        accession = "000078901926000161"
        for directory, filename in (("1513142", "copy.txt"), ("789019", "xslF345X06/form4.xml"), ("1513142", "index.html")):
            candidate = replace(self.source, record_id="same-accession-alias", purpose="DATED_OWNERSHIP_PACKET",
                source_url=f"https://www.sec.gov/Archives/edgar/data/{directory}/{accession}/{filename}")
            report = self.review(evidence=[candidate])
            self.assertEqual([], report["independent_evidence"])
            self.assertEqual(["SAME_FORM4_DISCLOSURE"], report["unavailable_evidence"][0]["reasons"])
            self.assertEqual(0, report["source_only_account_corroborated_rows"])

    def test_duplicate_independent_accession_copy_is_one_source_not_additional_corrobation(self):
        copy = replace(self.packet, record_id="same-independent-packet-copy",
                       source_url=self.packet.source_url.replace("/1513142/", "/789019/"))
        report = self.review(evidence=[self.packet, copy])
        self.assertEqual(1, report["independent_dated_disclosure_count"])
        self.assertEqual(1, len(report["independent_evidence"]))
        self.assertEqual(["DUPLICATE_CAPTURE_VERSION"], report["unavailable_evidence"][0]["reasons"])

    def test_unchanged_profile_bytes_captured_on_distinct_days_remain_separate_snapshots(self):
        later = replace(self.profile, record_id="later-profile-snapshot", observed_at=self.profile.observed_at + timedelta(days=1))
        report = self.review(evidence=[self.profile, later], clock=max(self.clock, later.available_at))
        self.assertEqual(2, len(report["independent_evidence"]))
        self.assertEqual(["2026-10-13", "2026-10-14"], [row["snapshot_day"] for row in report["independent_evidence"]])
        self.assertEqual(0, report["person_verified_row_count"])

    def test_current_profile_before_knowledge_cutoff_is_withheld_without_quoted_facts(self):
        profile = replace(self.profile, known_at=self.clock + timedelta(days=1))
        report = self.review(evidence=[profile])
        self.assertEqual([], report["independent_evidence"])
        self.assertIsNone(report["unavailable_evidence"][0]["capture"])
        self.assertEqual(["NOT_YET_KNOWN"], report["unavailable_evidence"][0]["reasons"])
        self.assertNotIn("Nadella Satya", json.dumps(report["unavailable_evidence"]))

    def test_original_form4_cannot_be_read_at_an_older_machine_knowledge_cutoff(self):
        with self.assertRaisesRegex(Form4PersonBridgeError, "not available"):
            self.review(clock=self.source.available_at - timedelta(seconds=1))

    def test_profile_source_url_cik_and_original_hash_are_exact(self):
        with self.assertRaisesRegex(Form4PersonBridgeError, "bytes/hash conflict"):
            self.review(evidence=[replace(self.profile, body=self.profile.body + b"\n")])
        changed = replace(self.profile, source_url="https://data.sec.gov/submissions/CIK0000789019.json")
        with self.assertRaisesRegex(Form4PersonBridgeError, "URL/CIK conflict"):
            self.review(evidence=[changed])

    def test_issuer_cik_cannot_be_substituted_for_owner_cik_from_profile(self):
        profile = json.loads(self.profile.body)
        profile["cik"], profile["name"] = "0000789019", "MICROSOFT CORP"
        changed = modified(self.profile, json.dumps(profile).encode(), source_url="https://data.sec.gov/submissions/CIK0000789019.json")
        report = self.review(evidence=[changed])
        self.assertEqual(0, report["source_only_account_corroborated_rows"])
        self.assertFalse(report["issuer_identity_from_reporting_owner_allowed"])

    def test_exact_cik_with_different_name_blocks_alias_and_corroboration(self):
        profile = json.loads(self.profile.body)
        profile["name"] = "Satya Nadella"
        changed = modified(self.profile, json.dumps(profile).encode())
        report = self.review(evidence=[changed, self.packet])
        self.assertEqual(0, report["source_only_account_corroborated_rows"])
        for row in report["row_reviews"]:
            self.assertIn("SAME_CIK_NAME_CONFLICT_ALIAS_UNRESOLVED", row["reasons"])
            self.assertEqual([changed.record_id], row["conflicting_capture_ids"])

    def test_header_xml_owner_conflict_and_duplicate_owners_are_rejected(self):
        changed = self.packet.body.replace(b"<rptOwnerCik>0001513142", b"<rptOwnerCik>0000789019")
        with self.assertRaisesRegex(Form4PersonBridgeError, "owner conflict"):
            self.review(evidence=[modified(self.packet, changed)])
        owner = self.packet.body.split(b"<reportingOwner>", 1)[1].split(b"</reportingOwner>", 1)[0]
        changed = self.packet.body.replace(b"</reportingOwner>", b"</reportingOwner><reportingOwner>" + owner + b"</reportingOwner>", 1)
        with self.assertRaisesRegex(Form4PersonBridgeError, "duplicate/conflicting"):
            self.review(evidence=[modified(self.packet, changed)])

    def test_profile_organization_or_kind_labels_do_not_verify_a_natural_person(self):
        for entity_type in ("operating", "investment", "individual"):
            profile = json.loads(self.profile.body)
            profile["entityType"] = entity_type
            report = self.review(evidence=[modified(self.profile, json.dumps(profile).encode())], mode="OBSERVED_SNAPSHOT")
            self.assertFalse(report["independent_evidence"][0]["natural_person_kind_proven"])
            self.assertEqual(0, report["person_verified_row_count"])

    def test_unavailable_or_access_valued_whole_body_is_metadata_only_and_never_parsed(self):
        hidden = replace(self.packet, body=None, retention="WITHHELD_SENSITIVE_BODY")
        report = self.review(evidence=[hidden])
        self.assertEqual([], report["independent_evidence"])
        self.assertEqual(hidden.sha256, report["unavailable_evidence"][0]["capture"]["sha256"])
        raw = b'<html>clientSecret="SYNTHETIC_ACCESS_VALUE_123456"</html>'
        report = self.review(evidence=[modified(self.packet, raw)])
        self.assertEqual([], report["independent_evidence"])
        self.assertEqual("WITHHELD_SENSITIVE_BODY", report["unavailable_evidence"][0]["capture"]["retention"])
        self.assertNotIn("SYNTHETIC_ACCESS_VALUE", json.dumps(report))
        with self.assertRaisesRegex(Form4PersonBridgeError, "replacement body"):
            self.review(evidence=[replace(hidden, body=b"")])

    def test_known_clock_cannot_precede_capture_and_clock_requires_timezone(self):
        with self.assertRaisesRegex(Form4PersonBridgeError, "before original capture"):
            self.review(evidence=[replace(self.packet, known_at=self.packet.observed_at - timedelta(seconds=1))])
        with self.assertRaisesRegex(Form4PersonBridgeError, "timezone"):
            self.review(clock=self.clock.replace(tzinfo=None))

    def test_source_date_and_row_count_cannot_be_relaxed_to_obtain_a_bridge(self):
        observation = deepcopy(self.observation)
        observation["transactions"][0]["transaction_date"] = "2014-02-04"
        with self.assertRaisesRegex(Form4PersonBridgeError, "transaction date conflict"):
            self.review(observation=observation)
        observation = deepcopy(self.observation)
        observation["transactions"].pop()
        with self.assertRaisesRegex(Form4PersonBridgeError, "row count mismatch"):
            self.review(observation=observation)

    def test_header_accession_and_xml_entity_injection_do_not_supply_identity_evidence(self):
        changed = modified(self.packet, self.packet.body.replace(b"0001582737-14-000015", b"0001582737-14-000016"))
        with self.assertRaisesRegex(Form4PersonBridgeError, "accession URL conflict"):
            self.review(evidence=[changed])
        changed = modified(self.packet, self.packet.body.replace(b"<XML>", b'<XML><!DOCTYPE ownershipDocument [<!ENTITY secret SYSTEM "file:///private">]>', 1))
        with self.assertRaisesRegex(Form4PersonBridgeError, "entity declaration"):
            self.review(evidence=[changed])

    def test_reversed_filing_chronology_is_retained_and_cannot_match_event_scope(self):
        raw = self.packet.body.replace(b"20140207", b"20140201")
        report = self.review(evidence=[modified(self.packet, raw)], mode="OBSERVED_SNAPSHOT")
        packet = report["independent_evidence"][0]
        self.assertIn("REPORT_DAY_AFTER_FILED_DAY", packet["clock_conflicts"])
        self.assertTrue(all(not row["exact_source_account_matches"][0]["requested_event_day_matches"] for row in report["row_reviews"]))

    def test_oversized_documents_duplicate_evidence_and_scope_aliases_are_rejected(self):
        with self.assertRaisesRegex(Form4PersonBridgeError, "bounded exact original"):
            self.review(evidence=[replace(self.packet, size_bytes=2_000_001)])
        with self.assertRaisesRegex(Form4PersonBridgeError, "IDs must be distinct"):
            self.review(evidence=[self.profile, self.profile])
        for url in (self.packet.source_url + "?access_token=synthetic", self.packet.source_url.replace("www.sec.gov", "www.sec.gov.evil.test"),
                    self.packet.source_url.replace("/edgar/", "/edgar/../edgar/")):
            with self.assertRaisesRegex(Form4PersonBridgeError, "noncanonical"):
                self.review(evidence=[replace(self.packet, source_url=url)])


if __name__ == "__main__":
    unittest.main()
