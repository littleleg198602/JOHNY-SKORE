"""Synthetic resolver controls; these records are not acceptance evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from market_checker_app.services.regulatory_identity_service import RegulatoryIdentityRegistry
from market_checker_app.storage.scout_store import ScoutStore


NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


def synthetic_record(**changes):
    row = {
        "record_id": "SYNTHETIC-one-facility", "source": "epa", "kind": "epa_frs_facility_owner",
        "ticker": "ONE", "issuer_cik": "0000000001", "facility_name": "EXAMPLE ENTITY INC",
        "frs_registry_id": "110018431295", "owner_entity_name": "Example Entity Inc.",
        "relationship": "DIRECT_OWNER", "time_basis": "OBSERVED_SNAPSHOT",
        "effective_from": "2026-01-01", "effective_to": None,
        "known_at": "2026-10-10T12:00:00+00:00", "reviewed_at": "2026-10-10T12:00:00+00:00",
        "reviewer": "SYNTHETIC TEST REVIEW", "verification_status": "VERIFIED",
        "evidence": [
            {"purpose": "FACILITY_IDENTITY", "url": "https://echo.epa.gov/facilities/example",
             "locator": "SYNTHETIC FRS:110018431295", "supports": "SYNTHETIC ID and facility-name assertion",
             "observed_at": "2026-10-10T10:00:00+00:00"},
            {"purpose": "OWNERSHIP_RELATIONSHIP", "url": "https://www.sec.gov/Archives/edgar/data/1/test/test.htm",
             "locator": "SYNTHETIC ownership paragraph", "supports": "SYNTHETIC dated facility-to-owner assertion",
             "observed_at": "2026-10-10T10:00:00+00:00"},
            {"purpose": "ISSUER_IDENTITY", "url": "https://www.sec.gov/Archives/edgar/data/1/test/test.htm",
             "locator": "SYNTHETIC filing cover", "supports": "SYNTHETIC ticker and CIK assertion",
             "observed_at": "2026-10-10T10:00:00+00:00"},
        ],
    }
    row.update(changes)
    return row


def resolve(registry, **changes):
    args = dict(ticker="ONE", cik="0000000001", frs_registry_id="110018431295",
                facility_name="EXAMPLE ENTITY INC", observation_date=NOW.date(), as_of=NOW)
    args.update(changes)
    return registry.resolve(**args)


class RegulatoryIdentityTests(unittest.TestCase):
    def test_empty_input_is_an_unmapped_facility_not_ownership(self):
        registry = RegulatoryIdentityRegistry()
        result = resolve(registry)
        self.assertEqual("UNMAPPED_FACILITY", result["identity_resolution"])
        self.assertFalse(result["issuer_facility_relationship_verified"])
        self.assertEqual((), registry.query_names("ONE", "0000000001", as_of=NOW))

    def test_exact_reviewed_link_is_only_snapshot_ownership(self):
        registry = RegulatoryIdentityRegistry([synthetic_record()])
        result = resolve(registry)
        self.assertEqual("EXACT_DATED_FACILITY", result["identity_resolution"])
        self.assertTrue(result["issuer_facility_relationship_verified"])
        self.assertTrue(result["issuer_attribution_allowed"])
        self.assertEqual("OBSERVED_SNAPSHOT", result["identity_evidence"]["time_basis"])
        for flag in ("environmental_liability_verified", "complete_facility_group_verified",
                     "facility_regulatory_action_attribution_allowed", "issuer_conviction_verified", "issuer_fine_verified"):
            self.assertFalse(result[flag], flag)
        result["identity_evidence"]["facility_name"] = "mutated copy"
        self.assertTrue(resolve(registry)["issuer_attribution_allowed"])

    def test_name_id_and_issuer_must_all_match_without_alias_inference(self):
        registry = RegulatoryIdentityRegistry([synthetic_record()])
        self.assertTrue(resolve(registry, facility_name="  example entity inc  ")["issuer_attribution_allowed"])
        for changes in ({"frs_registry_id": "110018431296"}, {"facility_name": "EXAMPLE ENTITY INC."},
                        {"facility_name": "EXAMPLE ENTITY PLANT"}, {"ticker": "TWO"}, {"cik": "0000000002"}):
            with self.subTest(changes=changes):
                self.assertFalse(resolve(registry, **changes)["issuer_attribution_allowed"])

    def test_known_and_effective_boundaries_are_separate_and_expiry_exclusive(self):
        registry = RegulatoryIdentityRegistry([synthetic_record(effective_to="2026-10-12")])
        self.assertEqual("NOT_YET_KNOWN", resolve(registry, as_of=NOW-timedelta(days=2),
                                                  observation_date=(NOW-timedelta(days=2)).date())["identity_resolution"])
        self.assertEqual("OUTSIDE_EFFECTIVE_PERIOD", resolve(registry,
                         observation_date=datetime(2025, 12, 31).date())["identity_resolution"])
        self.assertEqual("OUTSIDE_EFFECTIVE_PERIOD", resolve(registry, as_of=NOW+timedelta(days=1),
                         observation_date=(NOW+timedelta(days=1)).date())["identity_resolution"])
        self.assertFalse(resolve(registry, observation_date=(NOW+timedelta(days=1)).date())["issuer_attribution_allowed"])
        with self.assertRaisesRegex(ValueError, "timezone"):
            resolve(registry, as_of=NOW.replace(tzinfo=None))

    def test_unreviewed_link_can_only_provide_a_query_lead(self):
        row = synthetic_record(verification_status="UNVERIFIED", evidence=[])
        row.pop("reviewed_at"); row.pop("reviewer")
        registry = RegulatoryIdentityRegistry([row])
        self.assertEqual("UNREVIEWED_OWNERSHIP", resolve(registry)["identity_resolution"])
        self.assertEqual((row["facility_name"],), registry.query_names("ONE", "0000000001", as_of=NOW))
        self.assertEqual(frozenset(), registry.reviewed_facility_ids("ONE", "0000000001", as_of=NOW))

    def test_competing_owner_blocks_even_when_another_name_or_unreviewed(self):
        own = synthetic_record()
        competitor = synthetic_record(record_id="SYNTHETIC-other-owner", ticker="TWO", issuer_cik="0000000002",
                                      facility_name="SAME FRS DIFFERENT NAME", verification_status="UNVERIFIED", evidence=[])
        registry = RegulatoryIdentityRegistry([own, competitor])
        self.assertEqual("AMBIGUOUS_FACILITY", resolve(registry)["identity_resolution"])

    def test_sequential_sale_resolves_each_owner_only_in_its_interval(self):
        previous = synthetic_record(effective_to="2026-10-11")
        current = synthetic_record(record_id="SYNTHETIC-new-owner", ticker="TWO", issuer_cik="0000000002",
                                   effective_from="2026-10-11")
        for item in current["evidence"]:
            if "www.sec.gov" in item["url"]:
                item["url"] = item["url"].replace("/data/1/", "/data/2/")
        registry = RegulatoryIdentityRegistry([previous, current])
        self.assertTrue(resolve(registry, observation_date=(NOW-timedelta(days=1)).date())["issuer_attribution_allowed"])
        self.assertEqual("WRONG_ISSUER", resolve(registry)["identity_resolution"])
        self.assertTrue(resolve(registry, ticker="TWO", cik="0000000002")["issuer_attribution_allowed"])

    def test_reviewed_input_requires_all_three_primary_evidence_purposes(self):
        for changes in ({"issuer_cik": "1"}, {"frs_registry_id": "110018431"}, {"verification_status": "NAME_ONLY"},
                        {"effective_to": "2026-01-01"}, {"known_at": "2020-01-01T00:00:00+00:00"},
                        {"reviewed_at": "2026-10-10T09:00:00+00:00"}, {"evidence": []}, {"time_basis": "EVENT_DATE"}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, TypeError)):
                RegulatoryIdentityRegistry([synthetic_record(**changes)])
        for purpose in ("FACILITY_IDENTITY", "OWNERSHIP_RELATIONSHIP", "ISSUER_IDENTITY"):
            row = synthetic_record(); row["evidence"] = [item for item in row["evidence"] if item["purpose"] != purpose]
            with self.subTest(purpose=purpose), self.assertRaises(ValueError):
                RegulatoryIdentityRegistry([row])

    def test_nonprimary_wrong_issuer_credentials_and_wrong_frs_fail_closed(self):
        for url in ("https://example.org/claim", "https://www.sec.gov/Archives/edgar/data/2/test/filing.htm",
                    "https://secret@www.sec.gov/Archives/edgar/data/1/test/filing.htm",
                    "https://www.sec.gov/Archives/edgar/data/1/test/filing.htm#ownership"):
            row = synthetic_record(); row["evidence"][1]["url"] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                RegulatoryIdentityRegistry([row])
        row = synthetic_record(); row["evidence"][0]["locator"] = "FRS:110018431296"
        with self.assertRaises(ValueError):
            RegulatoryIdentityRegistry([row])

    def test_manifest_schema_duplicate_id_and_byte_budget(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/"identities.json"
            path.write_text(json.dumps({"schema_version":1,"records":[synthetic_record()]}))
            self.assertTrue(resolve(RegulatoryIdentityRegistry.load(path))["issuer_attribution_allowed"])
            path.write_text(json.dumps({"schema_version":2,"records":[]}))
            with self.assertRaises(ValueError): RegulatoryIdentityRegistry.load(path)
            path.write_bytes(b" "*5_000_001)
            with self.assertRaisesRegex(ValueError,"byte budget"): RegulatoryIdentityRegistry.load(path)
        with self.assertRaisesRegex(ValueError,"Duplicate"):
            RegulatoryIdentityRegistry([synthetic_record(),synthetic_record()])

    def test_fingerprint_is_order_stable_and_includes_knowledge_competitors_and_expiry(self):
        own = synthetic_record(effective_to="2026-10-12")
        other = synthetic_record(record_id="SYNTHETIC-unrelated", ticker="TWO", issuer_cik="0000000002",
                                 frs_registry_id="110018431299", verification_status="UNVERIFIED", evidence=[])
        args = dict(ticker="ONE",cik="0000000001",as_of=NOW)
        original = RegulatoryIdentityRegistry([own]).fingerprint(**args)
        self.assertEqual(original, RegulatoryIdentityRegistry([own,other]).fingerprint(**args))
        self.assertEqual(RegulatoryIdentityRegistry([own,other]).fingerprint(**args),
                         RegulatoryIdentityRegistry([other,own]).fingerprint(**args))
        self.assertNotEqual(original, RegulatoryIdentityRegistry([own]).fingerprint(
            **dict(args,as_of=NOW+timedelta(days=1))))
        competitor = deepcopy(other); competitor["frs_registry_id"] = own["frs_registry_id"]
        competitor["known_at"] = (NOW+timedelta(days=1)).isoformat()
        registry = RegulatoryIdentityRegistry([own,competitor])
        self.assertEqual(original, registry.fingerprint(**args))
        self.assertNotEqual(original, registry.fingerprint(**dict(args,as_of=NOW+timedelta(days=1))))
        self.assertEqual(64,len(original))

    def test_future_effective_records_are_invisible_until_their_effective_day(self):
        row = synthetic_record(effective_from="2026-10-12")
        registry, empty = RegulatoryIdentityRegistry([row]), RegulatoryIdentityRegistry()
        args = dict(ticker="ONE",cik="0000000001",as_of=NOW)
        self.assertEqual(empty.fingerprint(**args),registry.fingerprint(**args))
        self.assertEqual((),registry.query_names(**args))
        args["as_of"] += timedelta(days=1)
        self.assertNotEqual(empty.fingerprint(**args),registry.fingerprint(**args))
        self.assertEqual((row["facility_name"],),registry.query_names(**args))

    def test_identity_versions_ignore_sec_heartbeats_and_include_the_stored_sec_name(self):
        registry = RegulatoryIdentityRegistry([synthetic_record()])
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory)/"test.db")
            store.observe_sec_identity(subject_id="ONE",cik="0000000001",company_name="Example Entity Inc.",as_of=NOW)
            before = registry.identity_versions(store,as_of=NOW)
            store.observe_sec_identity(subject_id="ONE",cik="0000000001",company_name="Example Entity Inc.",
                                       as_of=NOW+timedelta(hours=1))
            self.assertEqual(before,registry.identity_versions(store,as_of=NOW+timedelta(hours=1)))
            renamed = ScoutStore(Path(directory)/"other.db")
            renamed.observe_sec_identity(subject_id="ONE",cik="0000000001",company_name="New Legal Name",as_of=NOW)
            self.assertNotEqual(before,registry.identity_versions(renamed,as_of=NOW))


if __name__ == "__main__":
    unittest.main()
