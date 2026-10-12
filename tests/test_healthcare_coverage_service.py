from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from market_checker_app.services.healthcare_coverage_service import build_healthcare_coverage_report
from market_checker_app.services.healthcare_identity_service import (
    HealthcareIdentityRegistry,
)
from market_checker_app.services.research_profile_service import load_research_profiles
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.utils.ticker_universe import CANONICAL_CSV_SHA256, load_canonical_tickers


# Growing production evidence must not move this suite's historical boundaries.
SEED_IDENTITIES = Path(__file__).parent / "fixtures" / "healthcare" / "reviewed_seed_identities_20261011.json"
RECORDS = json.loads(SEED_IDENTITIES.read_text())["records"]
NOW = max(datetime.fromisoformat(row["known_at"]) for row in RECORDS) + timedelta(seconds=1)


class HealthcareCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.store = ScoutStore(self.directory / "scout.db")
        self.registry = HealthcareIdentityRegistry(RECORDS)

    def observe(self, ticker="LLY", cik="0000059478", name="Eli Lilly and Company", when=NOW):
        self.store.observe_sec_identity(subject_id=ticker, cik=cik, company_name=name, as_of=when)

    def check(self, ticker="LLY", source="fda", when=NOW, partial=False, candidates=0, scoped=True):
        with self.store._connect() as conn:
            row = dict(conn.execute("SELECT subject_id,cik,company_name FROM scout_sec_identities WHERE subject_id=?",
                                    (ticker,)).fetchone())
        version = self.registry.identity_versions(self.store, source, as_of=when).get(ticker)
        self.store.record_specialist_check(source, subject_id=ticker,
            identity_key=f"{row['cik']}:{row['company_name']}", as_of=when,
            candidate_count=candidates, truncated=partial, identity_version=version if scoped else None)

    def report(self, **kwargs):
        return build_healthcare_coverage_report(self.store, as_of=kwargs.pop("as_of", NOW),
                                               identities=kwargs.pop("identities", self.registry), **kwargs)

    def subject(self, report, ticker="LLY", source="fda"):
        return next(row for row in report["sources"][source]["subject_details"] if row["ticker"] == ticker)

    def manifest(self, records, **changes):
        path = self.directory / "manifest.json"
        path.write_text(json.dumps(dict(schema_version=1, records=records, record_count=len(records), **changes)))
        return path

    def test_canonical_687_and_explicit_sector_denominators(self):
        report = self.report()
        self.assertEqual(load_canonical_tickers(), report["universe"]["tickers"])
        self.assertEqual((687, CANONICAL_CSV_SHA256), (report["universe"]["subjects"], report["universe"]["csv_sha256"]))
        for source, expected in (("fda", 111), ("cms", 19), ("clinicaltrials", 49)):
            row = report["sources"][source]
            self.assertEqual(expected, row["applicable_profile_subjects"])
            self.assertEqual(687, len(row["profile_candidate_tickers"]) + len(row["outside_profile_tickers"]))
            self.assertFalse(row["outside_profile_is_no_exposure_proof"])
            self.assertFalse(row["profile_applicability_is_object_verified"])
        self.assertEqual(0, report["sources"]["fda"]["mapped_subjects"])
        self.assertEqual(1, report["sources"]["fda"]["documented_issuer_object_subjects"])
        self.assertFalse(report["historical_replay_supported"])

    def test_registry_records_are_detached(self):
        rows = self.registry.records()
        rows[0]["identifiers"]["application_number"] = "NDA000001"
        rows[0]["evidence"][0]["url"] = "https://bad.example/"
        self.assertEqual(RECORDS[0], self.registry.records()[0])

    def test_issuer_plus_object_and_complete_checks_are_separate(self):
        self.observe()
        self.observe("PFE", "0000078003", "Pfizer Inc.")
        self.check("PFE", candidates=0)
        report = self.report()
        source = report["sources"]["fda"]
        self.assertEqual((1, 2, 1, 1), (source["mapped_subjects"], source["mapped_objects"],
            source["queried_issuers"], source["current_complete_source_checks"]))
        self.assertEqual("NEVER_QUERIED", self.subject(report)["source_check_status"])
        self.assertEqual(0, self.subject(report, "PFE")["issuer_matched_object_records"])
        self.assertEqual(1, source["zero_candidate_complete_name_checks"])
        self.assertEqual(0, source["issuer_wide_complete_checks"])
        self.assertFalse(source["negative_issuer_completeness_verified"])
        self.assertFalse(source["live_verified"])

    def test_versions_match_exact_collector_scope_and_ignore_heartbeat(self):
        self.observe()
        self.observe("HCA", "0000860730", "HCA Healthcare, Inc.")
        first = self.report()
        for source in first["sources"]:
            expected = self.registry.identity_versions(self.store, source, as_of=NOW)
            for ticker, version in first["sources"][source]["identity_versions"].items():
                self.assertEqual(expected[ticker], version)
        self.observe(when=NOW + timedelta(hours=1))
        self.assertEqual(first["sources"]["fda"]["identity_versions"], self.report()["sources"]["fda"]["identity_versions"])

    def test_future_knowledge_cannot_backdate_mapping(self):
        self.observe(when=NOW - timedelta(days=1))
        report = self.report(as_of=NOW - timedelta(seconds=2))
        self.assertEqual("KNOWN_ACTIVE_ISSUER", self.subject(report)["issuer_status"])
        for source in report["sources"].values():
            self.assertEqual(0, source["mapped_subjects"])
        self.assertTrue(all(row["status"] == "NOT_YET_KNOWN" for row in report["object_diagnostics"]))

    def test_future_sec_identity_and_future_checks_are_unavailable(self):
        self.observe(when=NOW + timedelta(days=1))
        self.check(when=NOW + timedelta(days=1))
        report = self.report()
        self.assertEqual("FUTURE_ISSUER", self.subject(report)["issuer_status"])
        source = report["sources"]["fda"]
        self.assertEqual((0, 0), (source["mapped_subjects"], source["queried_issuers"]))
        self.assertEqual(1, source["observations"]["future_checks"])

    def test_historical_event_intervals_remain_distinct_from_current_cms(self):
        self.observe()
        self.observe("HCA", "0000860730", "HCA Healthcare, Inc.")
        records = deepcopy(RECORDS)
        for row in records:
            if row["source"] == "cms":
                row["effective_from"] = "2026-10-10"
                row["effective_to"] = "2026-10-11"
        report = self.report(identities=HealthcareIdentityRegistry(records))
        fda, cms = report["sources"]["fda"], report["sources"]["cms"]
        self.assertEqual((2, 2), (fda["mapped_objects"], fda["historical_event_only_records"]))
        self.assertEqual(0, cms["mapped_objects"])
        self.assertEqual(2, cms["object_status_counts"]["OUTSIDE_EFFECTIVE_PERIOD"])

    def test_future_effective_object_is_not_mapped(self):
        self.observe("HCA", "0000860730", "HCA Healthcare, Inc.")
        records = deepcopy(RECORDS)
        for row in records:
            if row["source"] == "cms":
                row["effective_from"] = "2026-10-12"
        report = self.report(identities=HealthcareIdentityRegistry(records))
        self.assertEqual(2, report["sources"]["cms"]["object_status_counts"]["FUTURE_OBJECT"])
        self.assertEqual(0, report["sources"]["cms"]["mapped_objects"])

    def test_wrong_issuer_never_increases_operational_mapping(self):
        self.observe(cik="0000078003", name="Synthetic different issuer")
        report = self.report()
        self.assertEqual((2, 0, 2), tuple(self.subject(report)[field] for field in
            ("documented_object_records", "issuer_matched_object_records", "issuer_mismatch_object_records")))
        self.assertEqual(0, report["sources"]["fda"]["mapped_subjects"])

    def test_known_cik_conflict_blocks_current_issuer_claims(self):
        self.observe()
        self.check()
        self.observe(cik="0000078003", name="Different issuer", when=NOW + timedelta(minutes=1))
        report = self.report(as_of=NOW + timedelta(minutes=2))
        self.assertEqual("CONFLICTING_ISSUER", self.subject(report)["issuer_status"])
        self.assertEqual(0, report["sources"]["fda"]["mapped_subjects"])
        self.assertEqual(0, report["sources"]["fda"]["current_complete_source_checks"])
        earlier = self.report()
        self.assertEqual("KNOWN_ACTIVE_ISSUER", self.subject(earlier)["issuer_status"])

    def test_old_scope_is_queried_but_never_current_complete(self):
        self.observe()
        self.check(scoped=False)
        report = self.report()
        self.assertEqual("SCOPE_CHANGED", self.subject(report)["source_check_status"])
        self.assertEqual((1, 0, 1), tuple(report["sources"]["fda"][key] for key in
            ("queried_issuers", "current_complete_source_checks", "changed_scope_checks")))

    def test_partial_stale_and_refresh_end_boundary(self):
        # Keep the registry scope unchanged across the refresh window, so this
        # exercises freshness independently of newly-known object activation.
        self.registry = HealthcareIdentityRegistry([])
        self.observe(when=NOW - timedelta(days=35))
        self.check(partial=True)
        self.assertEqual("PARTIAL", self.subject(self.report())["source_check_status"])
        self.check(when=NOW - timedelta(days=30))
        self.assertEqual("STALE", self.subject(self.report())["source_check_status"])
        self.check(when=NOW - timedelta(days=30) + timedelta(microseconds=1))
        self.assertEqual("COMPLETE_NAME_QUERY_SCOPE", self.subject(self.report())["source_check_status"])

    def test_new_competing_object_changes_scope_and_denies_ambiguous_mapping(self):
        self.observe()
        self.check()
        competitor = deepcopy(RECORDS[0])
        competitor.update(record_id="synthetic-competitor", ticker="PFE", issuer_cik="0000078003",
                          known_at=(NOW + timedelta(hours=1)).isoformat())
        competitor["evidence"][1]["url"] = "https://www.sec.gov/Archives/edgar/data/78003/test/filing.htm"
        registry = HealthcareIdentityRegistry(RECORDS + [competitor])
        before = self.report(identities=registry)
        after = self.report(identities=registry, as_of=NOW + timedelta(hours=1))
        self.assertEqual(2, self.subject(before)["issuer_matched_object_records"])
        self.assertEqual(1, self.subject(after)["issuer_matched_object_records"])
        self.assertEqual("SCOPE_CHANGED", self.subject(after)["source_check_status"])
        self.assertNotEqual(before["sources"]["fda"]["identity_versions"]["LLY"],
                            after["sources"]["fda"]["identity_versions"]["LLY"])
        self.assertEqual(2, after["sources"]["fda"]["object_status_counts"]["AMBIGUOUS_OBJECT"])

    def test_name_only_candidate_and_future_finding_never_certify_live_negative(self):
        self.observe()
        for position, when in enumerate((NOW, NOW + timedelta(days=1))):
            self.store.record_finding(source="fda", subject_id="LLY", source_object_id=f"synthetic-{position}",
                content_hash=f"hash-{position}", title="Synthetic name-only recall", source_url="https://api.fda.gov/drug/enforcement.json",
                locator="synthetic", published_at=when, available_at=when, observed_at=when,
                details={"identity_status": "NAME_ONLY", "issuer_attribution_allowed": False}, verification_status="UNVERIFIED")
        report = self.report()
        self.assertTrue(self.subject(report)["stored_name_only_candidates"])
        observations = report["sources"]["fda"]["observations"]
        self.assertEqual((1, 1), (observations["name_only_candidates"], observations["future_findings"]))
        self.assertTrue(all(value is False for value in report["acceptance"].values()))

    def test_malformed_record_fails_entire_manifest_scope_with_explicit_diagnostics(self):
        self.observe()
        self.check()
        records = deepcopy(RECORDS)
        records[0]["identifiers"]["application_number"] = "BLA761215"  # valid different ID
        records[1]["known_at"] = "not-a-date"
        path = self.manifest(records)
        report = build_healthcare_coverage_report(self.store, as_of=NOW, identities_path=path)
        self.assertEqual("INVALID", report["identity_manifest"]["status"])
        self.assertEqual(1, report["identity_manifest"]["malformed_record_count"])
        self.assertEqual("MALFORMED", report["malformed_object_diagnostics"][0]["status"])
        self.assertEqual(0, report["sources"]["fda"]["mapped_subjects"])
        self.assertEqual(0, report["sources"]["fda"]["current_complete_source_checks"])
        self.assertEqual("INVALID_MANIFEST_SCOPE", self.subject(report)["source_check_status"])

    def test_identity_cannot_be_known_before_its_primary_evidence(self):
        records = deepcopy(RECORDS)
        records[0]["evidence"][0]["observed_at"] = (NOW + timedelta(days=1)).isoformat()
        report = build_healthcare_coverage_report(as_of=NOW, identities_path=self.manifest(records))
        self.assertEqual(1, report["identity_manifest"]["malformed_record_count"])
        self.assertEqual(0, report["sources"]["fda"]["documented_issuer_object_subjects"])

    def test_duplicate_and_count_mismatch_manifests_fail_closed(self):
        for mode in ("duplicate", "count"):
            records = deepcopy(RECORDS)
            if mode == "duplicate":
                records.append(deepcopy(records[0]))
            path = self.manifest(records)
            if mode == "count":
                raw = json.loads(path.read_text())
                raw["record_count"] += 1
                path.write_text(json.dumps(raw))
            report = build_healthcare_coverage_report(as_of=NOW, identities_path=path)
            self.assertEqual("INVALID", report["identity_manifest"]["status"])
            self.assertEqual(0, report["sources"]["cms"]["mapped_subjects"])

    def test_missing_inputs_do_not_create_database_or_claim_runtime(self):
        path = self.directory / "missing" / "scout.db"
        report = build_healthcare_coverage_report(SimpleNamespace(db_path=path), as_of=NOW, identities=self.registry)
        self.assertEqual("DATABASE_NOT_FOUND", report["runtime"]["status"])
        self.assertFalse(path.parent.exists())
        path = self.directory / "missing.json"
        report = build_healthcare_coverage_report(as_of=NOW, identities_path=path)
        self.assertEqual("UNAVAILABLE_OR_INVALID", report["identity_manifest"]["status"])
        self.assertFalse(path.exists())

    def test_read_only_report_has_no_schema_or_persisted_effects(self):
        self.observe()
        self.check()
        before = hashlib.sha256(self.store.db_path.read_bytes()).hexdigest()
        with patch.object(self.store, "_connect", side_effect=AssertionError("Report must not use write-capable connection")):
            report = self.report()
        self.assertEqual("READY", report["runtime"]["status"])
        self.assertEqual(before, hashlib.sha256(self.store.db_path.read_bytes()).hexdigest())

    def test_unknown_taxonomy_is_visible_instead_of_not_applicable(self):
        profiles = load_research_profiles()
        narrowed = dict(profiles.by_ticker)
        narrowed.pop("LLY")
        report = self.report(profiles=replace(profiles, by_ticker=narrowed))
        source = report["sources"]["fda"]
        self.assertEqual(["LLY"], source["unknown_applicability_tickers"])
        self.assertEqual(110, source["applicable_profile_subjects"])
        self.assertNotIn("LLY", source["outside_profile_tickers"])

    def test_aware_offsets_are_equal_and_naive_or_unbounded_intervals_rejected(self):
        local = NOW.astimezone(timezone(timedelta(hours=2)))
        self.assertEqual(self.report(), self.report(as_of=local))
        with self.assertRaises(ValueError):
            self.report(as_of=NOW.replace(tzinfo=None))
        for interval in (0, True, 366, 1.5):
            with self.assertRaises(ValueError):
                self.report(refresh_days=interval)

    def test_malformed_issuer_and_check_and_finding_are_explicit(self):
        self.observe()
        self.check()
        with self.store._connect() as conn:
            conn.execute("UPDATE scout_specialist_checks SET checked_at='bad-time' WHERE subject_id='LLY'")
            conn.execute("UPDATE scout_sec_identities SET first_observed_at='bad-time' WHERE subject_id='LLY'")
        report = self.report()
        self.assertEqual("MALFORMED_ISSUER", self.subject(report)["issuer_status"])
        self.assertEqual(1, report["sources"]["fda"]["observations"]["malformed_checks"])
        self.assertEqual(0, report["sources"]["fda"]["mapped_subjects"])


if __name__ == "__main__":
    unittest.main()
