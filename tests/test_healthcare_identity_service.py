from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest

from market_checker_app.services.healthcare_identity_service import (
    DEFAULT_HEALTHCARE_IDENTITIES, HealthcareIdentityRegistry, application_values,
    normalize_application, resolve_fda_crl, resolve_fda_recall,
)
from market_checker_app.services.fda_recall_scout_service import FdaRecallScoutService
from market_checker_app.services.healthcare_scout_service import (
    CMS_URL, TRIALS_URL, HealthcareNameScoutService,
)
from market_checker_app.storage.scout_store import ScoutStore


# Keep original replay cases fixed while the production registry grows.
RECORDS = json.loads((Path(__file__).parent / "fixtures" / "healthcare" /
                      "identity_manifest_captured_20261011.json").read_text())["records"]
NOW = max(datetime.fromisoformat(row["known_at"]) for row in RECORDS) + timedelta(seconds=1)
FIXTURES = Path(__file__).parent / "fixtures" / "healthcare"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


class CapturedFda:
    def recalls(self, product_type, firm_name, *, limit, skip=0):
        return {"meta": {"results": {"total": 0}}, "results": []}

    def complete_response_letters(self, firm_name, *, limit, skip=0):
        payload = fixture("lly_crl_captured_20261011.json")
        if firm_name.strip().casefold() == "eli lilly and company":
            return payload
        return {"meta": {"results": {"total": 0}}, "results": []}


class CapturedHealthcare:
    def __init__(self, source, rows, partial=False):
        self.source = source
        self.source_url = CMS_URL if source == "cms" else TRIALS_URL
        self.rows, self.partial = rows, partial
        self.calls = []

    def page(self, name, *, size, cursor):
        self.calls.append(name)
        expected = "HCA HEALTHCARE INC" if self.source == "cms" else "Eli Lilly and Company"
        if name.strip().casefold() != expected.casefold():
            return [], None
        return deepcopy(self.rows), "next" if self.partial else None


class HealthcareIdentityTests(unittest.TestCase):
    def setUp(self):
        self.registry = HealthcareIdentityRegistry(RECORDS)

    def resolve(self, record, **changes):
        args = dict(source=record["source"], kind=record["kind"], ticker=record["ticker"],
                    cik=record["issuer_cik"], entity_name=record["entity_name"],
                    identifiers=deepcopy(record["identifiers"]),
                    event_date=date.fromisoformat(record["effective_from"]), as_of=NOW)
        args.update(changes)
        return self.registry.resolve(**args)

    def test_all_seed_links_resolve_only_their_documented_object_and_role(self):
        self.assertEqual(5, len(RECORDS))
        for record in RECORDS:
            with self.subTest(record=record["record_id"]):
                result = self.resolve(record)
                self.assertTrue(result["issuer_attribution_allowed"])
                self.assertEqual(record["known_at"], result["identity_evidence"]["known_at"])
                self.assertEqual(record["identifiers"], result["identity_evidence"]["identifiers"])
                self.assertFalse(self.resolve(record, cik="0000000001")["issuer_attribution_allowed"])
                for key in record["identifiers"]:
                    changed = dict(record["identifiers"], **{key: "different"})
                    self.assertFalse(self.resolve(record, identifiers=changed)["issuer_attribution_allowed"])

    def test_effective_period_and_first_knowledge_are_independent(self):
        record = RECORDS[0]
        before = datetime.fromisoformat(record["known_at"]) - timedelta(microseconds=1)
        self.assertEqual("NOT_YET_KNOWN", self.resolve(record, as_of=before)["identity_resolution"])
        self.assertEqual("OUTSIDE_EFFECTIVE_PERIOD", self.resolve(
            record, event_date=date.fromisoformat(record["effective_to"]))["identity_resolution"])
        self.assertFalse(self.resolve(record, event_date=NOW.date()+timedelta(days=1))["issuer_attribution_allowed"])
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.resolve(record, as_of=NOW.replace(tzinfo=None))

    def test_ambiguous_overlap_is_denied_even_when_query_issuer_matches_one_link(self):
        first = deepcopy(RECORDS[0])
        other = deepcopy(first)
        other.update(record_id="other-issuer", ticker="PFE", issuer_cik="0000078003")
        other["evidence"][1]["url"] = "https://www.sec.gov/Archives/edgar/data/78003/test/filing.htm"
        registry = HealthcareIdentityRegistry([first, other])
        self.registry = registry
        self.assertEqual("AMBIGUOUS_OBJECT", self.resolve(first)["identity_resolution"])

    def test_names_only_trim_and_casefold_never_infer_spelling_or_group_alias(self):
        record = RECORDS[0]
        self.assertTrue(self.resolve(record, entity_name="  ELI LILLY AND COMPANY  ")["issuer_attribution_allowed"])
        for name in ("Lilly", "Eli Lilly & Co", "Lilly USA LLC", "Eli Lilly and Company Europe"):
            self.assertFalse(self.resolve(record, entity_name=name)["issuer_attribution_allowed"])

    def test_primary_evidence_validation_fails_closed(self):
        for field, changed in (
            ("identifiers", {"application_number": "BLA761215"}),
            ("issuer_cik", "59478"), ("source", "cms"),
            ("known_at", "2020-01-01T00:00:00+00:00"),
            ("effective_to", "2000-01-01"), ("time_basis", "NAME_ONLY"),
        ):
            record = deepcopy(RECORDS[0]); record[field] = changed
            with self.subTest(field=field), self.assertRaises(ValueError):
                HealthcareIdentityRegistry([record])
        for url in ("https://example.org/filing", "https://www.sec.gov/Archives/edgar/data/1/test/filing.htm"):
            record = deepcopy(RECORDS[0]); record["evidence"][1]["url"] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                HealthcareIdentityRegistry([record])
        record = deepcopy(RECORDS[0]); record["evidence"] = record["evidence"][:1]
        with self.assertRaises(ValueError):
            HealthcareIdentityRegistry([record])

    def test_fda_array_contract_and_supplement_scope(self):
        self.assertEqual(("BLA 761215/Original 2", "BLA 761215"),
                         application_values(["BLA 761215/Original 2", "BLA 761215"]))
        self.assertEqual(("BLA 761248",), application_values("BLA 761248"))
        self.assertEqual("BLA761215/ORIGINAL2", normalize_application("BLA 761215/Original 2"))
        self.assertEqual("NDA215866/S041", normalize_application("NDA 215866/S-041"))
        for value in ([], None, True, 761248, [""], ["BLA 761248", None], ["x"]*21):
            with self.subTest(value=value), self.assertRaises(ValueError):
                application_values(value)
        for value in ("761248", "BLA761248 trailing", "BLA761248/Original", "NDA 761248-2"):
            self.assertIsNone(normalize_application(value))

    def test_crl_original_scope_and_file_date_are_not_shared_across_letters(self):
        row = fixture("lly_crl_captured_20261011.json")["results"][0]
        signed = datetime.strptime(row["letter_date"], "%m/%d/%Y").date()
        result = resolve_fda_crl(self.registry, ticker="LLY", cik="0000059478", row=row, signed=signed, as_of=NOW)
        self.assertTrue(result["product_attribution_allowed"])
        self.assertEqual("BLA761215/ORIGINAL2", result["identity_evidence"]["identifiers"]["application_number"])
        changed = dict(row, application_number=["BLA 761215/Original 1", "BLA 761215"])
        self.assertFalse(resolve_fda_crl(self.registry, ticker="LLY", cik="0000059478",
                                        row=changed, signed=signed, as_of=NOW)["product_attribution_allowed"])

    def test_equivalent_crl_application_values_do_not_create_ambiguity(self):
        row = fixture("lly_crl_captured_20261011.json")["results"][1]
        signed = datetime.strptime(row["letter_date"], "%m/%d/%Y").date()
        row["application_number"] = ["BLA 761248", "BLA761248", "BLA 761248"]
        result = resolve_fda_crl(self.registry, ticker="LLY", cik="0000059478",
                                 row=row, signed=signed, as_of=NOW)
        self.assertTrue(result["product_attribution_allowed"])
        self.assertEqual("BLA761248", result["identity_evidence"]["identifiers"]["application_number"])

    def test_multiple_documented_crl_products_stay_name_only(self):
        first = deepcopy(RECORDS[0])
        other = deepcopy(first)
        other["record_id"] = "synthetic-other-application"
        other["identifiers"]["application_number"] = "BLA761215"
        registry = HealthcareIdentityRegistry([first, other])
        row = fixture("lly_crl_captured_20261011.json")["results"][0]
        signed = datetime.strptime(row["letter_date"], "%m/%d/%Y").date()
        result = resolve_fda_crl(registry, ticker="LLY", cik="0000059478",
                                 row=row, signed=signed, as_of=NOW)
        self.assertFalse(result["product_attribution_allowed"])
        self.assertEqual("AMBIGUOUS_APPLICATIONS", result["identity_resolution"])

    def test_malformed_crl_arrays_are_rejected_without_complete_negative_coverage(self):
        class MalformedFda(CapturedFda):
            def complete_response_letters(self, firm_name, *, limit, skip=0):
                row = deepcopy(fixture("lly_crl_captured_20261011.json")["results"][0])
                row["application_number"] = ["BLA 761215", None]
                return {"results": [row], "meta": {"results": {"total": 1}}}
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory)/"test.db")
            store.observe_sec_identity(subject_id="LLY", cik="0000059478",
                                       company_name="Eli Lilly and Company", as_of=NOW)
            result = FdaRecallScoutService(store, client=MalformedFda(), identities=self.registry).run(as_of=NOW)
            self.assertEqual(("PARTIAL", 1, 0),
                             (result["status"], result["rejected_rows"], result["new_findings"]))
            versions = self.registry.identity_versions(store, "fda", as_of=NOW)
            coverage = store.specialist_coverage("fda", as_of=NOW, identity_versions=versions)
            self.assertEqual((0, 1), (coverage["current_complete"], coverage["current_partial"]))

    def test_recall_requires_specific_recall_product_and_unambiguous_ndc(self):
        record = deepcopy(RECORDS[0])
        record.update(record_id="synthetic-recall", kind="fda_recall_product", relationship="RECALLING_PRODUCT_ENTITY",
                      effective_from=NOW.date().isoformat(), effective_to=None,
                      identifiers={"recall_number": "D-1-2026", "product_type": "drug",
                                   "application_number": "BLA761248", "product_ndc": "0002-1000"})
        registry = HealthcareIdentityRegistry([record])
        row = {"recall_number": "D-1-2026", "recalling_firm": record["entity_name"],
               "openfda": {"application_number": ["BLA 761248"], "product_ndc": ["0002-1000"]}}
        args = dict(ticker="LLY", cik="0000059478", product_type="drug", reported=NOW.date(), as_of=NOW)
        self.assertTrue(resolve_fda_recall(registry, row=row, **args)["product_attribution_allowed"])
        for metadata in ({}, {"application_number": ["BLA761248"], "product_ndc": ["0002-1000", "0002-1001"]}):
            self.assertFalse(resolve_fda_recall(registry, row=dict(row, openfda=metadata), **args)["product_attribution_allowed"])
        self.assertFalse(resolve_fda_recall(registry, row=dict(row, recall_number="D-2-2026"), **args)["product_attribution_allowed"])

    def test_fingerprint_tracks_known_records_only_and_is_full_sha256(self):
        future = deepcopy(RECORDS[0]); future["record_id"] = "future-link"
        future["known_at"] = (NOW+timedelta(days=1)).isoformat()
        extended = HealthcareIdentityRegistry(RECORDS+[future])
        args = dict(source="fda", ticker="LLY", cik="0000059478", as_of=NOW)
        self.assertEqual(self.registry.fingerprint(**args), extended.fingerprint(**args))
        self.assertEqual(64, len(extended.fingerprint(**args)))
        args["as_of"] += timedelta(days=2)
        self.assertNotEqual(self.registry.fingerprint(**args), extended.fingerprint(**args))

    def test_future_effective_link_invalidates_scope_when_it_becomes_queryable(self):
        record = deepcopy(next(r for r in RECORDS if r["source"] == "cms"))
        record["effective_from"] = (NOW+timedelta(days=2)).date().isoformat()
        registry = HealthcareIdentityRegistry([record])
        empty = HealthcareIdentityRegistry([])
        args = dict(source="cms", ticker="HCA", cik="0000860730", as_of=NOW)
        self.assertEqual(empty.fingerprint(**args), registry.fingerprint(**args))
        self.assertEqual((), registry.query_names(**args))
        args["as_of"] += timedelta(days=2)
        self.assertNotEqual(empty.fingerprint(**args), registry.fingerprint(**args))
        self.assertEqual(("HCA HEALTHCARE INC",), registry.query_names(**args))

    def test_identity_versions_use_known_active_sec_rows_and_ignore_heartbeats(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory)/"test.db")
            for ticker, cik, name, when in (
                ("LLY", "0000059478", "Eli Lilly and Company", NOW),
                ("PFE", "0000078003", "Pfizer Inc.", NOW),
                ("HCA", "0000860730", "HCA Healthcare, Inc.", NOW+timedelta(days=1)),
                ("Q", "0000000001", "Quarantined synthetic entity", NOW),
            ):
                store.observe_sec_identity(subject_id=ticker, cik=cik, company_name=name, as_of=when)
            with store._connect() as conn:
                conn.execute("UPDATE scout_sec_identities SET status='QUARANTINED' WHERE subject_id='Q'")
            versions = self.registry.identity_versions(store, "fda", as_of=NOW)
            self.assertEqual({"LLY", "PFE"}, set(versions))
            self.assertTrue(all(re.fullmatch("[a-f0-9]{64}", value) for value in versions.values()))
            store.observe_sec_identity(subject_id="LLY", cik="0000059478",
                                       company_name="Eli Lilly and Company", as_of=NOW+timedelta(hours=1))
            self.assertEqual(versions, self.registry.identity_versions(store, "fda", as_of=NOW))
            with store._connect() as conn:
                conn.execute("UPDATE scout_sec_identities SET company_name='Changed exact name' WHERE subject_id='LLY'")
            changed = self.registry.identity_versions(store, "fda", as_of=NOW)
            self.assertNotEqual(versions["LLY"], changed["LLY"])
            self.assertEqual(versions["PFE"], changed["PFE"])
            with self.assertRaises(ValueError):
                self.registry.identity_versions(store, "ofac", as_of=NOW)

    def test_newly_known_competing_object_link_invalidates_check_across_restart(self):
        other = deepcopy(RECORDS[0])
        other.update(record_id="synthetic-competitor", ticker="PFE", issuer_cik="0000078003",
                     known_at=(NOW+timedelta(days=1)).isoformat())
        other["evidence"][1]["url"] = "https://www.sec.gov/Archives/edgar/data/78003/test/filing.htm"
        registry = HealthcareIdentityRegistry(RECORDS+[other])
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory)/"test.db")
            for ticker, cik, name in (("LLY", "0000059478", "Eli Lilly and Company"),
                                      ("HCA", "0000860730", "HCA Healthcare, Inc.")):
                store.observe_sec_identity(subject_id=ticker, cik=cik, company_name=name, as_of=NOW)
            versions = registry.identity_versions(store, "fda", as_of=NOW)
            for issuer in store.specialist_due("fda", as_of=NOW, limit=10, identity_versions=versions):
                store.record_specialist_check("fda", subject_id=issuer["subject_id"],
                    identity_key=f"{issuer['cik']}:{issuer['company_name']}", as_of=NOW,
                    candidate_count=0, truncated=False, identity_version=versions[issuer["subject_id"]])
            self.assertEqual(2, store.specialist_coverage("fda", as_of=NOW,
                                                        identity_versions=versions)["current_complete"])
            reopened = ScoutStore(store.db_path)
            later = NOW+timedelta(days=1)
            changed = registry.identity_versions(reopened, "fda", as_of=later)
            self.assertNotEqual(versions["LLY"], changed["LLY"])
            self.assertEqual(versions["HCA"], changed["HCA"])
            self.assertEqual(["LLY"], [row["subject_id"] for row in reopened.specialist_due(
                "fda", as_of=later, limit=10, identity_versions=changed)])
            self.assertEqual(1, reopened.specialist_coverage("fda", as_of=later,
                                                           identity_versions=changed)["current_complete"])
            self.registry = registry
            self.assertEqual("AMBIGUOUS_OBJECT", self.resolve(RECORDS[0], as_of=later)["identity_resolution"])

    def test_cms_current_snapshot_does_not_backdate_chain_to_old_association(self):
        record = next(r for r in RECORDS if r["source"] == "cms")
        self.assertFalse(self.resolve(record, event_date=date.fromisoformat(
            record["identifiers"]["association_date"]))["issuer_attribution_allowed"])
        self.assertTrue(self.resolve(record)["issuer_attribution_allowed"])

    def test_collector_replays_real_crl_arrays_with_identity_provenance_and_no_score(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory)/"test.db")
            store.observe_sec_identity(subject_id="LLY", cik="0000059478", company_name="Eli Lilly and Company", as_of=NOW)
            service = FdaRecallScoutService(store, client=CapturedFda(), identities=self.registry)
            result = service.run(as_of=NOW, universe={"LLY"})
            self.assertEqual(("OK", 2, 2), (result["status"], result["new_findings"], result["identity_linked_candidates"]))
            self.assertEqual(0, service.run(as_of=NOW+timedelta(days=31))["new_findings"])
            with store._connect() as conn:
                rows = conn.execute("SELECT published_at,verification_status,details_json FROM scout_findings").fetchall()
            self.assertEqual(2, len(rows))
            for row in rows:
                details = json.loads(row["details_json"])
                self.assertIsInstance(details["application_number"], list)
                self.assertEqual("UNVERIFIED", row["verification_status"])
                self.assertEqual(NOW.isoformat(), row["published_at"])
                self.assertFalse(details["letter_date_is_publication_time"])
                self.assertTrue(details["product_attribution_allowed"])
                self.assertFalse(details["scoring_applied"])
                self.assertFalse(details["issuer_financial_impact_verified"])
            self.assertEqual([], store.latest_findings(["LLY"], as_of=NOW-timedelta(microseconds=1)))

    def test_collector_uses_documented_cms_name_retains_unmapped_provider_and_deduplicates(self):
        rows = fixture("hca_cms_captured_20261011.json")
        unknown = dict(rows[0], **{"ENROLLMENT ID": "O99999999999999", "ORGANIZATION NAME": "Other hospital"})
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory)/"test.db")
            store.observe_sec_identity(subject_id="HCA", cik="0000860730", company_name="HCA Healthcare, Inc.", as_of=NOW)
            client = CapturedHealthcare("cms", rows+[unknown], partial=True)
            service = HealthcareNameScoutService(store, client=client, identities=self.registry, max_pages=1)
            result = service.run(as_of=NOW, universe={"HCA"})
            self.assertEqual(("PARTIAL", 3, 2), (result["status"], result["new_findings"], result["identity_linked_candidates"]))
            self.assertIn("HCA HEALTHCARE INC", client.calls)
            self.assertEqual(0, service.run(as_of=NOW+timedelta(days=1))["new_findings"])
            with store._connect() as conn:
                details = [json.loads(r[0]) for r in conn.execute("SELECT details_json FROM scout_findings")]
            self.assertEqual(2, sum(d["issuer_attribution_allowed"] for d in details))
            self.assertTrue(all(not d["historical_association_attribution_allowed"] for d in details))
            self.assertTrue(all(not d["scoring_applied"] for d in details))
            self.assertEqual("NAME_ONLY", next(d for d in details if d["ENROLLMENT ID"] == unknown["ENROLLMENT ID"])["identity_status"])

    def test_clinical_trial_specific_intervention_and_role_are_required(self):
        trial = fixture("lly_trial_captured_20261011.json")
        for change, allowed in ((None, True), ("collaborator", False), ("other_trial", False),
                                ("other_version", False), ("no_intervention", False),
                                ("duplicate_intervention", True)):
            row = deepcopy(trial)
            if change == "collaborator":
                row["protocolSection"]["sponsorCollaboratorsModule"] = {
                    "leadSponsor": {"name": "Other company"}, "collaborators": [{"name": "Eli Lilly and Company"}]}
            elif change == "other_trial":
                row["protocolSection"]["identificationModule"]["nctId"] = "NCT00000001"
            elif change == "no_intervention":
                row["protocolSection"].pop("armsInterventionsModule")
            elif change == "other_version":
                row["protocolSection"]["statusModule"]["lastUpdatePostDateStruct"]["date"] = "2021-10-21"
            elif change == "duplicate_intervention":
                interventions = row["protocolSection"]["armsInterventionsModule"]["interventions"]
                interventions.append(deepcopy(interventions[0]))
            with self.subTest(change=change), TemporaryDirectory() as directory:
                store = ScoutStore(Path(directory)/"test.db")
                store.observe_sec_identity(subject_id="LLY", cik="0000059478", company_name="Eli Lilly and Company", as_of=NOW)
                client = CapturedHealthcare("clinicaltrials", [row])
                summary = HealthcareNameScoutService(store, client=client, identities=self.registry).run(as_of=NOW)
                self.assertEqual(int(allowed), summary["identity_linked_candidates"])
                with store._connect() as conn:
                    details = json.loads(conn.execute("SELECT details_json FROM scout_findings").fetchone()[0])
                self.assertEqual(allowed, details["issuer_attribution_allowed"])
                self.assertFalse(details["scoring_applied"])


if __name__ == "__main__":
    unittest.main()
