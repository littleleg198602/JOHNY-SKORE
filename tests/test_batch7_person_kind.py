"""Original-byte dated bio evidence does not identify a reporting-owner CIK."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import unittest
from unittest.mock import patch

from evidence.batch7_sec_20261015.build_review import (
    ACCOUNT_PIN, EVENT_DAYS, FACT, PINNED, ROOT as PACKET_ROOT,
    build_review_artifact, build_review_plan, load_inputs,
)
from market_checker_app.services.sec_person_kind_review import (
    MAX_BYTES, PersonKindCapture, PersonKindReviewError, review_sec_person_kind,
)


REPO = Path(__file__).resolve().parents[1]


def changed(capture, body, **fields):
    return replace(capture, body=body, sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body), **fields)


def index_bytes(primary, *, cik="0000789019", form="DEF 14A", declared_size=None, duplicate=False):
    row = (f'<tr><td>1</td><td>PROXY STATEMENT</td><td><a href="{primary.source_url}">proxy.htm</a></td>'
           f'<td>{form}</td><td>{primary.size_bytes if declared_size is None else declared_size}</td></tr>')
    return (f'<html><div class="infoHead">Filing Date</div><div class="info">2025-10-21</div>'
            f'<div class="infoHead">Accepted</div><div class="info">2025-10-21 16:16:13</div>'
            f'<table>{row}{row if duplicate else ""}</table><span class="companyName">MICROSOFT CORP (Filer)'
            f'<a href="/cgi-bin/browse-edgar?CIK={cik}&amp;action=getcompany">{cik}</a></span></html>').encode()


class PersonKindReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.actual_descriptor, cls.account, cls.actual_fact, cls.actual_primary, cls.actual_index, cls.receipts = load_inputs()
        cls.clock = max(item.available_at for item in (cls.account, cls.actual_primary, cls.actual_index))

    def setUp(self):
        self.descriptor = deepcopy(self.actual_descriptor)
        self.fact = {**FACT, "context_locator": "table:1"}
        self.primary = changed(self.actual_primary, f'<html><table><tr><td>{FACT["context_quote"]}</td></tr></table></html>'.encode(),
                               record_id="synthetic-bio-only")
        self.index = changed(self.actual_index, index_bytes(self.primary), record_id="synthetic-index-only")

    def review(self, *, descriptor=None, account=None, fact=None, primary=None, index=None, clock=None, days=EVENT_DAYS):
        return review_sec_person_kind(account_descriptor=self.descriptor if descriptor is None else descriptor,
            account_capture=self.account if account is None else account, person_fact=self.fact if fact is None else fact,
            primary_capture=self.primary if primary is None else primary, index_capture=self.index if index is None else index,
            knowledge_at=self.clock if clock is None else clock, requested_event_days=days)

    def context(self, text, **fact_fields):
        primary = changed(self.primary, f"<html><table><tr><td>{text}</td></tr></table></html>".encode())
        return self.review(primary=primary, index=changed(self.index, index_bytes(primary)),
                           fact={**self.fact, "context_quote": text, **fact_fields})

    def test_actual_reserved_primary_originals_and_exact_replay_match_without_regeneration(self):
        artifact = build_review_artifact()
        self.assertEqual(json.loads((PACKET_ROOT / "person_kind_review.json").read_bytes()), artifact)
        self.assertEqual(2, artifact["actual_new_primary_http_requests"])
        self.assertEqual(2, artifact["original_http200_bodies"])
        self.assertEqual(1, artifact["new_person_disclosure_count"])
        self.assertTrue(artifact["index_and_primary_are_same_disclosure"])
        for name, (size, digest) in PINNED.items():
            body = (PACKET_ROOT / name).read_bytes()
            self.assertEqual(size, len(body)); self.assertEqual(digest, hashlib.sha256(body).hexdigest())
        self.assertEqual(ACCOUNT_PIN, (self.account.size_bytes, self.account.sha256))
        self.assertEqual(0, artifact["person_cik_verified_rows"])
        self.assertFalse(artifact["quota_managed_application_live_claim"])
        self.assertFalse(artifact["historical_machine_availability_or_oos_proven"])

    def test_actual_named_bio_is_supported_without_account_name_order_alias_or_person_cik(self):
        report = self.review(primary=self.actual_primary, index=self.actual_index, fact=self.actual_fact)
        self.assertEqual("PARTIAL", report["status"])
        self.assertTrue(report["natural_person_fact_supported"])
        self.assertEqual("Satya Nadella", report["dated_person_fact"]["name_as_published"])
        self.assertEqual(58, report["dated_person_fact"]["age_as_published"])
        self.assertEqual(FACT["context_quote"], report["dated_person_fact"]["context_quote"])
        self.assertEqual("2025-10-21", report["dated_person_fact"]["source_document_filing_day"])
        self.assertEqual("Nadella Satya", report["source_account_descriptor"]["owner_name"])
        self.assertTrue(report["source_account_descriptor"]["validated_against_original_account_packet"])
        self.assertFalse(report["account_name_exactly_matches_person_name"])
        for field in ("person_identity_verified", "source_account_to_person_cik_verified", "approved_alias",
                      "historical_event_validity_proven", "instrument_identity_verified"):
            self.assertFalse(report[field])

    def test_actual_size_and_unqualified_publisher_clock_conflicts_are_preserved(self):
        artifact = build_review_artifact()
        source = artifact["review"]["source_document"]
        self.assertEqual(2430412, source["index_declared_primary_bytes"])
        self.assertEqual(2430522, source["captured_primary_bytes"])
        self.assertTrue(source["publisher_size_conflict"])
        self.assertFalse(source["exact_as_filed_body_version_verified"])
        self.assertEqual("2025-10-21 16:16:13", source["acceptance_datetime_unqualified_raw"])
        self.assertFalse(source["acceptance_timezone_inferred"])
        self.assertEqual("2025-10-21T20:16:13.000Z", artifact["discovery_pointer_only"]["acceptance_datetime_raw"])
        self.assertFalse(artifact["discovery_pointer_only"]["profile_clock_reconciled_to_unqualified_index_clock"])

    def test_original_form3_not_descriptor_hash_alone_must_match_exact_owner_cik_and_name(self):
        for field, value in (("owner_cik", "0000789019"), ("owner_name", "Satya Nadella"),
                             ("owner_name", "Nadella SATYA"), ("owner_name", "Nadella Satya ")):
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(PersonKindReviewError, "original header/XML owner"):
                    self.review(descriptor={**self.descriptor, field: value})

    def test_descriptor_url_or_digest_drift_does_not_bind_account_even_when_whole_document_is_valid(self):
        for field, value in (("source_url", self.descriptor["source_url"].replace("/1513142/", "/789019/")),
                             ("sha256", "a" * 64)):
            with self.assertRaisesRegex(PersonKindReviewError, "pointer or hash conflict"):
                self.review(descriptor={**self.descriptor, field: value})

    def test_rehashed_changed_account_does_not_approve_name_order_or_person_identity(self):
        body = self.account.body.replace(b"Nadella Satya", b"Satya Nadella")
        account = changed(self.account, body)
        descriptor = {**self.descriptor, "owner_name": "Satya Nadella", "sha256": account.sha256}
        report = self.review(account=account, descriptor=descriptor)
        self.assertTrue(report["account_name_exactly_matches_person_name"])
        self.assertFalse(report["source_account_to_person_cik_verified"])
        self.assertFalse(report["approved_alias"])
        self.assertFalse(report["person_identity_verified"])

    def test_rehashed_request_cannot_hide_original_account_name_conflict(self):
        account = changed(self.account, self.account.body + b"\n")
        descriptor = {**self.descriptor, "owner_name": "Satya Nadella", "sha256": account.sha256}
        with self.assertRaisesRegex(PersonKindReviewError, "original header/XML owner"):
            self.review(account=account, descriptor=descriptor)

    def test_account_header_and_xml_disagreement_is_rejected_not_adopted(self):
        account = changed(self.account, self.account.body.replace(b"<rptOwnerCik>0001513142", b"<rptOwnerCik>0000789019"))
        with self.assertRaisesRegex(ValueError, "reporting-owner conflict"):
            self.review(account=account, descriptor={**self.descriptor, "sha256": account.sha256})

    def test_original_account_capture_is_required_not_a_user_dictionary(self):
        for account in ({"body": self.account.body}, None):
            if account is None:
                with self.assertRaises(TypeError):
                    review_sec_person_kind(account_descriptor=self.descriptor, person_fact=self.fact,
                        primary_capture=self.primary, index_capture=self.index, knowledge_at=self.clock)
            else:
                with self.assertRaisesRegex(PersonKindReviewError, "whole original captures"):
                    self.review(account=account)

    def test_name_only_entity_type_signature_or_role_contexts_do_not_support_person_kind(self):
        for text in ("Satya Nadella", "Satya Nadella entityType other", "Satya Nadella entityType individual",
                     "Keith R. Dolliver, Attorney-in-Fact for Satya Nadella", "Satya Nadella Chairman and CEO"):
            with self.subTest(text=text):
                report = self.context(text)
                self.assertFalse(report["natural_person_fact_supported"])
                self.assertNotIn("dated_person_fact", report)

    def test_other_persons_age_or_broad_mixed_table_cannot_support_selected_name(self):
        for text in ("Another Person Age: 58 | Director Since: 2014 | Birthplace: India",
                     "Satya Nadella; Another Person Age: 58 | Director Since: 2014 | Birthplace: India",
                     FACT["context_quote"] + " Other Person Age: 60 | Director Since: 2016 | Birthplace: India"):
            self.assertFalse(self.context(text)["natural_person_fact_supported"])

    def test_organization_label_is_not_promoted_by_repeated_bio_style_fields(self):
        for name in ("Nadella LLC", "Microsoft Corp.", "Example Limited", "Example LP"):
            report = self.context(f"{name} Age: 58 | Director Since: 2014 | Birthplace: India", person_name=name)
            self.assertFalse(report["natural_person_fact_supported"])

    def test_age_and_birthplace_must_be_exact_explicit_fields_with_a_bounded_human_header(self):
        cases = [
            (FACT["context_quote"].replace("Age: 58", "Age: 0"), {"age_quote": "Age: 0"}),
            (FACT["context_quote"].replace("Age: 58", "Age: 131"), {"age_quote": "Age: 131"}),
            (FACT["context_quote"].replace("Birthplace: India", "Birthplace: Unknown 123"), {"birthplace_quote": "Birthplace: Unknown 123"}),
            (FACT["context_quote"].replace("Director Since: 2014", "Director Since: 2026"), {}),
            (FACT["context_quote"].replace("Age: 58", "Years employed: 58"), {}),
        ]
        for text, fields in cases:
            self.assertFalse(self.context(text, **fields)["natural_person_fact_supported"])

    def test_exact_context_quote_and_exact_locator_are_not_search_hints(self):
        for fields in ({"context_quote": FACT["context_quote"] + " extra"}, {"context_locator": "table:2"},
                       {"context_quote": "Satya Nadella Age: 58"}):
            with self.assertRaisesRegex(PersonKindReviewError, "selected context"):
                self.review(fact={**self.fact, **fields})
        for locator in ("table:0", "table:1//row:1", "contains:Satya", "table:1;table:2"):
            with self.assertRaisesRegex(PersonKindReviewError, "exact bounded original table"):
                self.review(fact={**self.fact, "context_locator": locator})

    def test_age_or_birthplace_quote_prefix_cannot_change_the_exact_published_field(self):
        for fields in ({"age_quote": "Age: 5"}, {"birthplace_quote": "Birthplace: Ind"},
                       {"age_quote": "Age: 058"}, {"birthplace_quote": "Birthplace: India Other"}):
            report = self.review(fact={**self.fact, **fields})
            self.assertFalse(report["natural_person_fact_supported"])
            self.assertNotIn("dated_person_fact", report)

    def test_row_and_cell_locators_keep_the_same_single_original_context(self):
        for locator in ("table:1/row:1", "table:1/row:1/cell:1"):
            self.assertTrue(self.review(fact={**self.fact, "context_locator": locator})["natural_person_fact_supported"])

    def test_age_from_hidden_script_or_name_from_another_table_cannot_fill_a_bio(self):
        body = b'<html><table><tr><td>Satya Nadella<script> Age: 58 | Director Since: 2014 | Birthplace: India</script></td></tr></table></html>'
        primary = changed(self.primary, body)
        with self.assertRaisesRegex(PersonKindReviewError, "exact quotation conflicts"):
            self.review(primary=primary, index=changed(self.index, index_bytes(primary)))

    def test_current_capture_is_unavailable_at_every_older_historical_cutoff(self):
        for cutoff in (self.account.observed_at, self.clock - timedelta(microseconds=1)):
            report = self.review(clock=cutoff)
            self.assertEqual(["REQUIRED_ORIGINAL_CAPTURE_NOT_YET_KNOWN"], report["reasons"])
            self.assertFalse(report["natural_person_fact_supported"])
            self.assertFalse(report["source_account_descriptor"]["validated_against_original_account_packet"])
            self.assertNotIn("dated_person_fact", report); self.assertNotIn("captures", report)

    def test_later_input_knowledge_is_not_backdated_to_body_capture(self):
        for key in ("account", "primary", "index"):
            capture = getattr(self, key)
            report = self.review(**{key: replace(capture, known_at=self.clock + timedelta(seconds=1))})
            self.assertFalse(report["natural_person_fact_supported"])
            self.assertEqual(["REQUIRED_ORIGINAL_CAPTURE_NOT_YET_KNOWN"], report["reasons"])

    def test_machine_knowledge_is_latest_capture_not_proxy_filing_date(self):
        report = self.review()
        self.assertEqual(self.clock.isoformat(), report["source_fact_available_at"])
        self.assertEqual("2026-10-14", self.clock.date().isoformat())
        self.assertFalse(report["historical_event_validity_proven"])
        self.assertEqual([False, True, False], [item["matches_disclosure_point_day"] for item in report["requested_events"]])
        self.assertTrue(all(not item["account_person_identity_at_event_verified"] for item in report["requested_events"]))

    def test_whole_access_bearing_body_is_withheld_before_any_parser_or_quote(self):
        raw = b'<html>clientSecret="SYNTHETIC_NO_ACCESS_TEST_VALUE_12345"</html>'
        for key in ("account", "primary", "index"):
            secret = changed(getattr(self, key), raw)
            with patch("market_checker_app.services.sec_person_kind_review._packet", side_effect=AssertionError("no body parser")), \
                    patch("market_checker_app.services.sec_person_kind_review._Index", side_effect=AssertionError("no body parser")):
                report = self.review(**{key: secret})
            self.assertEqual(["REQUIRED_ORIGINAL_BODY_UNAVAILABLE"], report["reasons"])
            self.assertIn("WITHHELD_SENSITIVE_BODY", [item["retention"] for item in report["captures"]])
            self.assertNotIn("SYNTHETIC_NO_ACCESS", json.dumps(report)); self.assertNotIn("dated_person_fact", report)

    def test_explicit_missing_or_whole_withheld_captures_do_not_accept_substitute_bytes(self):
        for retention in ("WITHHELD_SENSITIVE_BODY", "MISSING_BODY"):
            missing = replace(self.primary, body=None, retention=retention)
            with patch("market_checker_app.services.sec_person_kind_review._Index", side_effect=AssertionError("no parser")):
                report = self.review(primary=missing)
            self.assertFalse(report["natural_person_fact_supported"])
            with self.assertRaisesRegex(PersonKindReviewError, "replacement body"):
                self.review(primary=replace(missing, body=b""))

    def test_exact_whole_hash_size_and_four_mb_cap_are_enforced(self):
        for capture in (replace(self.primary, body=self.primary.body + b"\n"),
                        replace(self.primary, sha256="a" * 64), replace(self.primary, size_bytes=True),
                        replace(self.primary, size_bytes=MAX_BYTES + 1)):
            with self.assertRaises(PersonKindReviewError): self.review(primary=capture)

    def test_known_clock_cannot_precede_capture_and_every_clock_is_aware(self):
        for capture in (replace(self.primary, known_at=self.primary.observed_at - timedelta(seconds=1)),
                        replace(self.primary, observed_at=self.primary.observed_at.replace(tzinfo=None)),
                        replace(self.primary, known_at="")):
            with self.assertRaises(PersonKindReviewError): self.review(primary=capture)
        with self.assertRaisesRegex(PersonKindReviewError, "timezone"):
            self.review(clock=self.clock.replace(tzinfo=None))

    def test_index_filer_cik_form_and_exact_primary_row_are_required(self):
        for body in (index_bytes(self.primary, cik="0001513142"), index_bytes(self.primary, form="10-K"),
                     index_bytes(self.primary, duplicate=True), index_bytes(self.primary).replace(b"d908201ddef14a.htm", b"different.htm")):
            with self.assertRaises(PersonKindReviewError): self.review(index=changed(self.index, body))

    def test_index_and_primary_must_share_accession_and_issuer(self):
        for url in (self.index.source_url.replace("/789019/", "/1513142/"),
                    self.index.source_url.replace("000119312525245150", "000119312525245151")):
            with self.assertRaisesRegex(PersonKindReviewError, "accession or issuer scope conflict"):
                self.review(index=replace(self.index, source_url=url))

    def test_actual_index_basename_cannot_claim_another_accession_in_the_same_directory(self):
        changed_url = self.actual_index.source_url.replace("0001193125-25-245150-index.html", "0001193125-25-245151-index.html")
        with self.assertRaisesRegex(PersonKindReviewError, "index basename conflicts"):
            self.review(primary=self.actual_primary, index=replace(self.actual_index, source_url=changed_url), fact=self.actual_fact)

    def test_rehashed_future_filing_day_cannot_follow_original_capture_even_if_known_later(self):
        future_body = self.actual_index.body.replace(b"2025-10-21", b"2099-10-21")
        index = changed(self.actual_index, future_body)
        for later_knowledge in (False, True):
            with self.subTest(later_knowledge=later_knowledge):
                clock = self.clock.replace(year=2100) if later_knowledge else self.clock
                primary = replace(self.actual_primary, known_at=clock)
                candidate = replace(index, known_at=clock)
                with self.assertRaisesRegex(PersonKindReviewError, "filing day cannot follow original capture"):
                    self.review(primary=primary, index=candidate, fact=self.actual_fact, clock=clock, days=("2099-10-21",))

    def test_bio_issuer_differs_from_source_account_is_partial_without_fact(self):
        primary = replace(self.primary, source_url=self.primary.source_url.replace("/789019/", "/1513142/"))
        index = changed(self.index, index_bytes(primary, cik="0001513142"),
                        source_url=self.index.source_url.replace("/789019/", "/1513142/"))
        report = self.review(primary=primary, index=index)
        self.assertEqual(["PERSON_BIO_ISSUER_SCOPE_DIFFERS_FROM_ACCOUNT_DISCLOSURE"], report["reasons"])
        self.assertNotIn("dated_person_fact", report)

    def test_index_declared_size_conflict_is_preserved_not_replaced_or_accepted_as_version(self):
        report = self.review(index=changed(self.index, index_bytes(self.primary, declared_size=self.primary.size_bytes + 1)))
        self.assertTrue(report["natural_person_fact_supported"])
        self.assertTrue(report["source_document"]["publisher_size_conflict"])
        self.assertFalse(report["source_document"]["exact_as_filed_body_version_verified"])
        for size in (0, -1):
            with self.assertRaisesRegex(PersonKindReviewError, "positive declared primary size"):
                self.review(index=changed(self.index, index_bytes(self.primary, declared_size=size)))

    def test_same_accession_as_account_never_becomes_independent_person_disclosure(self):
        primary = replace(self.primary, source_url=self.primary.source_url.replace("000119312525245150", "000158273714000015"))
        index = changed(self.index, index_bytes(primary),
                        source_url=self.index.source_url.replace("000119312525245150", "000158273714000015")
                            .replace("0001193125-25-245150-index.html", "0001582737-14-000015-index.html"))
        report = self.review(primary=primary, index=index)
        self.assertEqual(["PERSON_FACT_NOT_INDEPENDENT_OF_ACCOUNT_DISCLOSURE"], report["reasons"])
        self.assertFalse(report["natural_person_fact_supported"])

    def test_noncanonical_urls_redirect_wrappers_and_encoded_aliases_are_rejected(self):
        for url in ("http://www.sec.gov/Archives/edgar/data/789019/000119312525245150/proxy.htm",
                    "https://www.sec.gov/ix?doc=/Archives/edgar/data/789019/000119312525245150/proxy.htm",
                    self.primary.source_url + "?apikey=SYNTHETIC_TEST", self.primary.source_url + "#bio",
                    self.primary.source_url.replace("/d908", "/%64908"),
                    self.primary.source_url.replace("www.sec.gov", "www.sec.gov@example.test")):
            with self.assertRaises(PersonKindReviewError): self.review(primary=replace(self.primary, source_url=url))

    def test_bounded_event_selections_and_historical_days_never_create_validity(self):
        for days in (list(EVENT_DAYS) * 6, "2014-02-04", ["not-a-day"], [True]):
            with self.assertRaises(PersonKindReviewError): self.review(days=days)
        report = self.review(days=["1900-01-01", "2025-10-21", "2099-12-31"])
        self.assertFalse(report["historical_event_validity_proven"])
        self.assertTrue(all(not row["account_person_identity_at_event_verified"] for row in report["requested_events"]))

    def test_bounded_html_depth_context_text_node_and_index_row_work(self):
        for body in (b"<table>" * 66 + b"x" + b"</table>" * 66,
                     b"<table><tr><td>" + b"x" * 16001 + b"</td></tr></table>",
                     b"<html>" + b"<br>" * 200001 + b"</html>"):
            primary = changed(self.primary, body)
            with self.assertRaisesRegex(PersonKindReviewError, "bounded complexity|text bound"):
                self.review(primary=primary, index=changed(self.index, index_bytes(primary)))
        body = b"<html><table>" + b"<tr><td>x</td></tr>" * 258 + b"</table></html>"
        with self.assertRaisesRegex(PersonKindReviewError, "index exceeds bounded complexity"):
            self.review(index=changed(self.index, body))

    def test_pure_review_does_no_http_store_registry_write_or_input_mutation(self):
        before_descriptor, before_fact = deepcopy(self.descriptor), deepcopy(self.fact)
        with patch("socket.socket.connect", side_effect=AssertionError("no HTTP")), \
                patch("market_checker_app.storage.scout_store.ScoutStore.__init__", side_effect=AssertionError("no store")):
            report = self.review()
        self.assertEqual(before_descriptor, self.descriptor); self.assertEqual(before_fact, self.fact)
        self.assertEqual(0, report["network_requests_performed"])
        for field in ("quota_or_lease_modified", "registry_modified", "scoring_applied", "trading_allowed"):
            self.assertFalse(report[field])

    def test_actual_runtime_plan_pins_account_primary_and_index_with_platform_independent_paths(self):
        plan = build_review_plan()
        self.assertEqual(json.loads((PACKET_ROOT / "actual_review_plan.json").read_bytes()), plan)
        self.assertEqual("SEC_PERSON_KIND", plan["kind"])
        self.assertEqual({"account", "primary", "index"}, set(plan["captures"]))
        self.assertEqual({"account_descriptor", "person_fact", "requested_event_days"}, set(plan["request"]))
        for capture in plan["captures"].values():
            ref = capture["body_ref"]; metadata = capture["metadata"]
            self.assertEqual(PurePosixPath(ref["path"]).as_posix(), PureWindowsPath(ref["path"]).as_posix())
            self.assertNotIn("\\", ref["path"])
            raw = REPO.joinpath(*PurePosixPath(ref["path"]).parts).read_bytes()
            self.assertEqual(ref["bytes"], len(raw)); self.assertLessEqual(ref["bytes"], MAX_BYTES)
            self.assertEqual(ref["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(ref["sha256"], metadata["sha256"])
            self.assertEqual(ref["bytes"], metadata["size_bytes"])


if __name__ == "__main__":
    unittest.main()
