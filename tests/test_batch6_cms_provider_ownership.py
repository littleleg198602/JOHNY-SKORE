"""CMS original-byte contracts; all positive CCN/issuer fixtures are synthetic.

The retained real HCA fixture contains projected rows without a CCN/provider
crosswalk. Nothing in these tests adopts an identity or certifies LIVE scope.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from market_checker_app.services.cms_provider_ownership_review import (
    CMS_ENROLLMENTS_URL, CMS_OWNERS_URL, CapturedSource, CmsProviderOwnerScope,
    review_cms_provider_ownership,
)


ROOT = Path(__file__).resolve().parents[1]
REVIEWED = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
ENROLLMENT = "O20000101000001"  # SYNTHETIC; no real CCN mapping is claimed.
SCOPE = CmsProviderOwnerScope(ENROLLMENT, "1052593506", "35", expected_ccn="000001")


def cms_url(base, *, size=100, offset=0, enrollment=ENROLLMENT, **extra):
    return base + "?" + urlencode({"size": size, "offset": offset,
                                  "filter[ENROLLMENT ID]": enrollment, **extra})


def captured(payload, url, observed, **meta_changes):
    body = json.dumps(payload, separators=(",", ":")).encode()
    metadata = {"capture_form": "ORIGINAL_HTTP_RESPONSE", "http_status": 200,
                "complete_response_body": True, "raw_publication_permitted": True,
                "body_bytes": len(body), "body_sha256": hashlib.sha256(body).hexdigest(),
                "response_url": url, "requested_url": url,
                "requested_at": observed, "observed_at": observed}
    metadata.update(meta_changes)
    return CapturedSource(metadata, body)


def fixtures():
    owner = {"ENROLLMENT ID": ENROLLMENT, "ASSOCIATE ID": "0000000001",
             "ORGANIZATION NAME": "SYNTHETIC HOSPITAL LEGAL ENTITY",
             "ASSOCIATE ID - OWNER": SCOPE.owner_associate_id, "TYPE - OWNER": "O",
             "ORGANIZATION NAME - OWNER": "HCA HEALTHCARE INC", "ROLE CODE - OWNER": "35",
             "ROLE TEXT - OWNER": "5% OR GREATER INDIRECT OWNERSHIP INTEREST",
             "ASSOCIATION DATE - OWNER": "2007-06-28", "PERCENTAGE OWNERSHIP": "100"}
    provider = {"ENROLLMENT ID": ENROLLMENT, "ASSOCIATE ID": "0000000001",
                "ORGANIZATION NAME": "SYNTHETIC HOSPITAL LEGAL ENTITY",
                "DOING BUSINESS AS NAME": "SYNTHETIC BRAND NOT USED FOR IDENTITY",
                "CCN": "000001", "PROVIDER TYPE CODE": "00-09"}
    issuer = {"cik": "0000860730", "name": "HCA HEALTHCARE INC", "tickers": ["HCA"]}
    return {"owner_capture": captured([owner], cms_url(CMS_OWNERS_URL), "2026-10-14T07:02:00+00:00"),
            "provider_capture": captured([provider], cms_url(CMS_ENROLLMENTS_URL), "2026-10-14T07:01:00+00:00"),
            "issuer_capture": captured(issuer, "https://data.sec.gov/submissions/CIK0000860730.json",
                                       "2026-10-14T07:00:00+00:00")}


def changed_body(capture, change):
    payload = json.loads(capture.body)
    change(payload)
    body = json.dumps(payload, separators=(",", ":")).encode()
    return CapturedSource(dict(capture.metadata, body_bytes=len(body),
                               body_sha256=hashlib.sha256(body).hexdigest()), body)


class CmsProviderOwnershipReviewTests(unittest.TestCase):
    def setUp(self):
        self.captures = fixtures()

    def review(self, **changes):
        args = dict(self.captures, scope=SCOPE, reviewed_at=REVIEWED)
        args.update(changes)
        return review_cms_provider_ownership(**args)

    def reject(self, expected, **changes):
        result = self.review(**changes)
        self.assertEqual("WITHHOLD", result["decision"])
        self.assertIn(expected, result["reasons"])
        self.assertIsNone(result["proposed_record"])
        self.assertFalse(result["issuer_attribution_allowed"])
        return result

    def test_synthetic_original_positive_retains_three_exact_body_pins_ccn_and_current_ingestion(self):
        result = self.review()
        self.assertEqual("PROPOSE_FOR_REGISTRY_OWNER_REVIEW", result["decision"])
        row = result["proposed_record"]
        self.assertEqual("000001", row["identifiers"]["ccn"])
        self.assertEqual("0000000001", row["identifiers"]["provider_associate_id"])
        self.assertEqual("2007-06-28", row["identifiers"]["association_date"])
        self.assertEqual("100", row["identifiers"]["ownership_percentage"])
        self.assertEqual("INDIRECT_OWNER", row["relationship"])
        self.assertEqual("OBSERVED_SNAPSHOT", row["time_basis"])
        self.assertEqual("2026-10-14", row["effective_from"])
        self.assertEqual("2026-10-15", row["effective_to"])
        self.assertEqual(REVIEWED.isoformat(), row["known_at"])
        self.assertEqual(REVIEWED.isoformat(), row["ingested_at"])
        self.assertEqual(self.captures["owner_capture"].metadata["body_sha256"],
                         row["source_captures"]["owners"]["body_sha256"])
        self.assertEqual(3, len(row["source_captures"]))
        self.assertFalse(row["publisher_snapshot_date_available"])
        self.assertIsNone(row["publisher_publication_at"])
        for flag in ("issuer_attribution_allowed", "historical_ownership_attribution_allowed",
                     "issuer_wide_scope", "ultimate_parent_inferred", "product_ownership_inferred",
                     "negative_issuer_completeness_verified", "live_acceptance_verified", "score_changes",
                     "registry_modified"):
            self.assertIs(False, result[flag])

    def test_archived_cohort_review_does_not_become_current_or_historical_ownership(self):
        result = self.review(reviewed_at=REVIEWED + timedelta(days=3))
        self.assertFalse(result["current_source_snapshot"])
        self.assertEqual("2026-10-14", result["proposed_record"]["observed_snapshot_day"])
        self.assertEqual("2026-10-17T12:00:00+00:00", result["ingested_at"])
        self.assertFalse(result["historical_ownership_attribution_allowed"])
        self.assertFalse(result["association_date_is_publication_or_knowledge_time"])

    def test_outputs_are_detached_and_input_metadata_body_and_source_rows_are_unchanged(self):
        before = deepcopy(self.captures)
        first = self.review()
        first["proposed_record"]["identifiers"]["ccn"] = "999999"
        first["original_capture_pins"]["owners"]["body_sha256"] = "0" * 64
        self.assertEqual(before, self.captures)
        self.assertEqual("000001", self.review()["proposed_record"]["identifiers"]["ccn"])

    def test_real_projected_hca_rows_are_not_complete_original_cms_evidence(self):
        path = ROOT / "tests/fixtures/healthcare/hca_cms_captured_20261011.json"
        body = path.read_bytes()
        self.assertEqual("5645620e60368e6f76bec5c13f84c7a5f88e4ca782c7f247b898b8fe25f9d3a2",
                         hashlib.sha256(body).hexdigest())
        original_rows = json.loads(body)
        self.assertEqual(2, len(original_rows))
        self.assertTrue(all("CCN" not in row and "ASSOCIATE ID" not in row for row in original_rows))
        meta = {"capture_form": "PROJECTED_PUBLISHER_FIELDS", "raw_publication_permitted": True,
                "body_sha256": hashlib.sha256(body).hexdigest(), "body_bytes": len(body)}
        result = self.reject("OWNERS_PROJECTED_OR_NONORIGINAL_BYTES",
                             owner_capture=CapturedSource(meta, body), provider_capture=None, issuer_capture=None)
        self.assertIn("PROVIDER_ORIGINAL_CAPTURE_MISSING", result["reasons"])
        self.assertIn("ISSUER_ORIGINAL_CAPTURE_MISSING", result["reasons"])
        self.assertEqual(body, path.read_bytes())

    def test_missing_raw_fields_cannot_be_replaced_by_current_projection_or_claimed_hash(self):
        for key in ("capture_form", "complete_response_body", "body_sha256", "body_bytes"):
            capture = self.captures["owner_capture"]
            meta = dict(capture.metadata)
            del meta[key]
            with self.subTest(key=key):
                self.assertEqual("WITHHOLD", self.review(owner_capture=CapturedSource(meta, capture.body))["decision"])

    def test_changed_original_byte_or_size_fails_pinned_hash(self):
        source = self.captures["owner_capture"]
        self.reject("OWNERS_ORIGINAL_BODY_HASH_OR_SIZE_MISMATCH",
                    owner_capture=CapturedSource(source.metadata, source.body + b"\n"))
        self.reject("OWNERS_ORIGINAL_BODY_HASH_OR_SIZE_MISMATCH", owner_capture=CapturedSource(
            dict(source.metadata, body_sha256="0" * 64), source.body))

    def test_withheld_or_unsafe_entire_body_is_never_parsed_or_exposed(self):
        for metadata, body, code in (({"raw_publication_permitted": False}, None, "BODY_WITHHELD_OR_UNAVAILABLE"),
                                     ({"raw_publication_permitted": True, "status": "WITHHELD_ACCESS"}, b"invalid", "BODY_WITHHELD_OR_UNAVAILABLE")):
            capture = CapturedSource(metadata, body)
            with self.subTest(code=code), patch("market_checker_app.services.cms_provider_ownership_review.json.loads",
                                              side_effect=AssertionError("Unavailable body reached parser")):
                result = self.review(owner_capture=capture, provider_capture=capture, issuer_capture=capture)
                self.assertEqual({}, result["original_capture_pins"])
                self.assertTrue(all(code in reason for reason in result["reasons"]))
        body = b'{"access_token":"synthetic_access_value_123456789"}'
        source = self.captures["owner_capture"]
        capture = CapturedSource(dict(source.metadata, body_bytes=len(body), body_sha256=hashlib.sha256(body).hexdigest()), body)
        with patch("market_checker_app.services.cms_provider_ownership_review.json.loads",
                   side_effect=AssertionError("Unsafe body reached parser")):
            result = self.review(owner_capture=capture, provider_capture=capture, issuer_capture=capture)
        self.assertTrue(all("UNSAFE_WHOLE_BODY_WITHHELD" in reason for reason in result["reasons"]))
        self.assertNotIn("synthetic_access_value", json.dumps(result))

    def test_http400_empty_and_http_redirect_body_are_partial_rejections(self):
        for status in (400, 302, 403, 429, True, 200.0):
            capture = captured([], cms_url(CMS_OWNERS_URL), "2026-10-14T07:02:00+00:00", http_status=status)
            with self.subTest(status=status):
                self.reject("OWNERS_HTTP_200_REQUIRED", owner_capture=capture)

    def test_projection_broad_name_filter_duplicate_query_and_unsafe_url_fail_before_pin_export(self):
        source = self.captures["owner_capture"]
        urls = [cms_url(CMS_OWNERS_URL, fields="ENROLLMENT ID,CCN"),
                CMS_OWNERS_URL + "?" + urlencode({"size": 100, "offset": 0, "filter[ORGANIZATION NAME - OWNER]": "HCA HEALTHCARE INC"}),
                cms_url(CMS_OWNERS_URL) + "&size=1", cms_url(CMS_OWNERS_URL) + "&access_token=synthetic_blocked_value",
                cms_url(CMS_OWNERS_URL).replace("data.cms.gov", "untrusted.example")]
        for url in urls:
            meta = dict(source.metadata, response_url=url, requested_url=url)
            with self.subTest(url=url):
                result = self.reject("OWNERS_INVALID_ORIGINAL_JSON_OR_SOURCE_REFERENCE",
                                     owner_capture=CapturedSource(meta, source.body))
                self.assertNotIn("owners", result["original_capture_pins"])
                self.assertNotIn("synthetic_blocked_value", json.dumps(result))

    def test_redirected_capture_cannot_inherit_original_primary_url(self):
        source = self.captures["issuer_capture"]
        self.reject("ISSUER_SOURCE_REFERENCE_INVALID", issuer_capture=CapturedSource(
            dict(source.metadata, requested_url="https://untrusted.example/source"), source.body))

    def test_full_first_page_or_nonzero_cursor_is_not_complete_scope(self):
        source = self.captures["owner_capture"]
        for url, code in ((cms_url(CMS_OWNERS_URL, size=1), "OWNERS_PAGING_NOT_PROVEN_COMPLETE"),
                          (cms_url(CMS_OWNERS_URL, offset=100), "OWNERS_EXACT_UNPROJECTED_ENROLLMENT_QUERY_REQUIRED")):
            meta = dict(source.metadata, response_url=url, requested_url=url)
            with self.subTest(code=code):
                result = self.reject(code, owner_capture=CapturedSource(meta, source.body))
                self.assertFalse(result["exact_enrollment_query_complete"])

    def test_empty_complete_exact_query_is_only_a_scoped_absence_without_issuer_negative(self):
        capture = captured([], cms_url(CMS_OWNERS_URL), "2026-10-14T07:02:00+00:00")
        result = self.reject("EXACT_OWNER_ROLE_NOT_IN_CAPTURE", owner_capture=capture)
        self.assertTrue(result["exact_enrollment_query_complete"])
        self.assertFalse(result["negative_issuer_completeness_verified"])
        self.assertFalse(result["live_acceptance_verified"])

    def test_server_filter_leakage_invalidates_scope_even_when_target_row_exists(self):
        capture = changed_body(self.captures["owner_capture"], lambda rows: rows.append(dict(rows[0], **{"ENROLLMENT ID": "O20000101000002"})))
        self.reject("OWNERS_QUERY_RETURNED_OUT_OF_SCOPE_ENROLLMENT", owner_capture=capture)

    def test_missing_ccn_numeric_ccn_multivalue_and_alias_ccn_are_not_guessed(self):
        for value in (None, 1, "1", "000001,000002", "000002", "٠٠٠٠٠١"):
            capture = changed_body(self.captures["provider_capture"], lambda rows: rows[0].update(CCN=value))
            with self.subTest(value=value):
                self.assertEqual("WITHHOLD", self.review(provider_capture=capture)["decision"])

    def test_provider_crosswalk_duplicates_and_conflicting_legal_entities_are_ambiguous(self):
        for change in (lambda rows: rows.append(deepcopy(rows[0])),
                       lambda rows: rows.append(dict(rows[0], CCN="000002"))):
            self.reject("AMBIGUOUS_PROVIDER_OR_CCN_MAPPING",
                        provider_capture=changed_body(self.captures["provider_capture"], change))

    def test_enrollment_pac_and_legal_name_all_match_without_brand_inference(self):
        for changes in ({"ASSOCIATE ID": "0000000002"}, {"ORGANIZATION NAME": "SYNTHETIC BRAND NOT USED FOR IDENTITY"}):
            capture = changed_body(self.captures["owner_capture"], lambda rows: rows[0].update(changes))
            with self.subTest(changes=changes):
                self.reject("OWNER_PROVIDER_JOIN_OR_OWNER_KEYS_INVALID", owner_capture=capture)

    def test_provider_type_and_absent_provider_mapping_do_not_fall_back_to_owner_name(self):
        capture = changed_body(self.captures["provider_capture"], lambda rows: rows[0].update({"PROVIDER TYPE CODE": "00-18"}))
        self.reject("SUPPORTED_HOSPITAL_PROVIDER_TYPE_NOT_CAPTURED", provider_capture=capture)
        self.reject("EXACT_PROVIDER_ENROLLMENT_NOT_IN_CAPTURE", provider_capture=captured(
            [], cms_url(CMS_ENROLLMENTS_URL), "2026-10-14T07:01:00+00:00"))

    def test_duplicate_owner_role_dates_conflict_and_do_not_select_a_convenient_row(self):
        capture = changed_body(self.captures["owner_capture"], lambda rows: rows.append(dict(rows[0], **{"ASSOCIATION DATE - OWNER": "2011-08-05"})))
        self.reject("AMBIGUOUS_OWNER_ROLE_OR_ASSOCIATION_DATE", owner_capture=capture)

    def test_exact_owner_pac_and_role_selection_does_not_roll_up_other_owners(self):
        for changes in ({"ASSOCIATE ID - OWNER": "0000000002"}, {"ROLE CODE - OWNER": "34"}):
            capture = changed_body(self.captures["owner_capture"], lambda rows: rows[0].update(changes))
            with self.subTest(changes=changes):
                self.reject("EXACT_OWNER_ROLE_NOT_IN_CAPTURE", owner_capture=capture)

    def test_individual_manager_and_conflicting_role_text_are_not_organizational_ownership(self):
        for changes, code in (({"TYPE - OWNER": "I"}, "ORGANIZATIONAL_LEGAL_OWNER_REQUIRED"),
                              ({"ROLE TEXT - OWNER": "OPERATIONAL/MANAGERIAL CONTROL"}, "OWNER_ROLE_CODE_TEXT_CONFLICT")):
            capture = changed_body(self.captures["owner_capture"], lambda rows: rows[0].update(changes))
            self.reject(code, owner_capture=capture)
        with self.assertRaises(ValueError):
            self.review(scope=CmsProviderOwnerScope(ENROLLMENT, SCOPE.owner_associate_id, "43"))

    def test_percentage_unknown_nonfinite_or_below_role_threshold_remains_withheld(self):
        for value in (None, "", "NaN", "Infinity", "-1", "101", "4.99", True, "1e2"):
            capture = changed_body(self.captures["owner_capture"], lambda rows: rows[0].update({"PERCENTAGE OWNERSHIP": value}))
            with self.subTest(value=value):
                self.reject("OWNER_PERCENTAGE_MISSING_OR_ROLE_CONFLICT", owner_capture=capture)

    def test_source_association_day_must_be_original_actual_iso_day_no_future_clock(self):
        for value in (None, "20070628", "06/28/2007", "2026-10-15", "2007-02-30"):
            capture = changed_body(self.captures["owner_capture"], lambda rows: rows[0].update({"ASSOCIATION DATE - OWNER": value}))
            with self.subTest(value=value):
                self.reject("OWNER_ASSOCIATION_DATE_INVALID_OR_FUTURE", owner_capture=capture)

    def test_issuer_brand_subsidiary_and_punctuation_aliases_need_independent_bridge(self):
        for name in ("HCA", "HCA Healthcare, Inc.", "HCA HEALTHONE LLC"):
            capture = changed_body(self.captures["issuer_capture"], lambda payload: payload.update(name=name))
            with self.subTest(name=name):
                self.reject("EXACT_LEGAL_OWNER_ISSUER_NAME_BRIDGE_MISSING", issuer_capture=capture)

    def test_issuer_cik_ticker_and_full_own_submissions_are_mandatory(self):
        for changes in ({"cik": "0000070318"}, {"cik": True}, {"tickers": ["THC"]}, {"tickers": "HCA"}):
            capture = changed_body(self.captures["issuer_capture"], lambda payload: payload.update(changes))
            with self.subTest(changes=changes):
                self.reject("DIRECT_SEC_ISSUER_CIK_TICKER_BRIDGE_MISSING", issuer_capture=capture)
        source = self.captures["issuer_capture"]
        for url in ("https://www.sec.gov/files/company_tickers.json", "https://data.sec.gov/submissions/CIK0000070318.json"):
            self.reject("ISSUER_INVALID_ORIGINAL_JSON_OR_SOURCE_REFERENCE", issuer_capture=CapturedSource(
                dict(source.metadata, requested_url=url, response_url=url), source.body))

    def test_later_current_issuer_body_cannot_backdate_an_older_cms_snapshot(self):
        source = self.captures["issuer_capture"]
        for observed in ("2026-10-14T07:01:01+00:00", "2026-10-13T07:00:00+00:00"):
            with self.subTest(observed=observed):
                self.reject("ISSUER_BRIDGE_NOT_AVAILABLE_IN_ORIGINAL_CMS_OBSERVED_COHORT",
                            issuer_capture=CapturedSource(dict(source.metadata, requested_at=observed, observed_at=observed), source.body))

    def test_provider_and_owner_snapshots_on_different_days_cannot_be_joined(self):
        source = self.captures["provider_capture"]
        self.reject("ISSUER_BRIDGE_NOT_AVAILABLE_IN_ORIGINAL_CMS_OBSERVED_COHORT", provider_capture=CapturedSource(
            dict(source.metadata, requested_at="2026-10-13T07:01:00+00:00", observed_at="2026-10-13T07:01:00+00:00"), source.body))

    def test_aware_offsets_preserve_instants_and_naive_or_conflicting_capture_clocks_fail(self):
        source = self.captures["issuer_capture"]
        capture = CapturedSource(dict(source.metadata, requested_at="2026-10-14T09:00:00+02:00",
                                      observed_at="2026-10-14T09:00:00+02:00"), source.body)
        self.assertTrue(self.review(issuer_capture=capture)["candidate_bridge_complete"])
        for changes in ({"observed_at": "2026-10-14T07:00:00"}, {"requested_at": "2026-10-14T07:03:00+00:00"}):
            with self.subTest(changes=changes):
                self.assertEqual("WITHHOLD", self.review(issuer_capture=CapturedSource(dict(source.metadata, **changes), source.body))["decision"])
        self.reject("OWNERS_CAPTURE_CLOCK_ORDER_INVALID", reviewed_at=REVIEWED.replace(hour=7, minute=1))
        with self.assertRaises(ValueError):
            self.review(reviewed_at=datetime(2026, 10, 14))

    def test_duplicate_json_keys_and_nan_do_not_override_original_scope(self):
        source = self.captures["issuer_capture"]
        for body, code in ((b'{"cik":860730,"cik":70318}', "DUPLICATE_JSON_FIELD"),
                           (b'{"cik":NaN}', "NONFINITE_JSON_VALUE")):
            capture = CapturedSource(dict(source.metadata, body_bytes=len(body), body_sha256=hashlib.sha256(body).hexdigest()), body)
            self.reject(code, issuer_capture=capture)

    def test_malformed_rows_and_excess_page_rows_do_not_become_complete_negative(self):
        for payload in ({"data": []}, [None], ["unknown"], [[]]):
            capture = captured(payload, cms_url(CMS_OWNERS_URL), "2026-10-14T07:02:00+00:00")
            with self.subTest(payload=payload):
                result = self.reject("OWNERS_MALFORMED_RESPONSE_ROWS", owner_capture=capture)
                self.assertFalse(result["negative_issuer_completeness_verified"])

    def test_review_constructs_no_network_store_or_registry_mutation(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Unexpected HTTP")), \
                patch("market_checker_app.storage.scout_store.ScoutStore.__init__", side_effect=AssertionError("Unexpected DB")), \
                patch("pathlib.Path.write_bytes", side_effect=AssertionError("Unexpected file mutation")), \
                patch("pathlib.Path.write_text", side_effect=AssertionError("Unexpected file mutation")):
            self.assertTrue(self.review()["candidate_bridge_complete"])

    def test_supported_scope_denies_noncanonical_or_name_only_inputs(self):
        for scope in (CmsProviderOwnerScope(ENROLLMENT, "short", "35"),
                      CmsProviderOwnerScope(ENROLLMENT, SCOPE.owner_associate_id, "35", ticker="THC"),
                      CmsProviderOwnerScope("HCA BRAND", SCOPE.owner_associate_id, "35"),
                      CmsProviderOwnerScope(ENROLLMENT, SCOPE.owner_associate_id, "35", expected_ccn=1)):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                self.review(scope=scope)

    def test_direct_role_is_distinct_and_does_not_change_group_or_historical_scope(self):
        scope = CmsProviderOwnerScope(ENROLLMENT, SCOPE.owner_associate_id, "34", expected_ccn="000001")
        capture = changed_body(self.captures["owner_capture"], lambda rows: rows[0].update({
            "ROLE CODE - OWNER": "34", "ROLE TEXT - OWNER": "5% OR GREATER DIRECT OWNERSHIP INTEREST"}))
        result = self.review(scope=scope, owner_capture=capture)
        self.assertEqual("DIRECT_OWNER", result["proposed_record"]["relationship"])
        self.assertFalse(result["ultimate_parent_inferred"])
        self.assertFalse(result["historical_ownership_attribution_allowed"])

    def test_original_capture_and_query_limits_are_enforced_before_completeness(self):
        source = self.captures["owner_capture"]
        with patch("market_checker_app.services.cms_provider_ownership_review.MAX_CAPTURE_BYTES", 16):
            self.reject("OWNERS_ORIGINAL_BODY_HASH_OR_SIZE_MISMATCH")
        url = cms_url(CMS_OWNERS_URL, size=101)
        result = self.reject("OWNERS_EXACT_UNPROJECTED_ENROLLMENT_QUERY_REQUIRED", owner_capture=CapturedSource(
            dict(source.metadata, response_url=url, requested_url=url), source.body))
        self.assertFalse(result["exact_enrollment_query_complete"])

    def test_same_original_snapshot_keeps_stable_id_with_later_actual_review_clock(self):
        first = self.review()["proposed_record"]
        later = self.review(reviewed_at=REVIEWED + timedelta(days=1))["proposed_record"]
        self.assertEqual(first["record_id"], later["record_id"])
        self.assertEqual(first["relationship_snapshot_sha256"], later["relationship_snapshot_sha256"])
        self.assertNotEqual(first["known_at"], later["known_at"])
        self.assertEqual(first["effective_to"], later["effective_to"])

    def test_actual_current_sec_bytes_still_require_an_exact_legal_owner_bridge(self):
        root = ROOT / "evidence/batch6_healthcare_20261014"
        meta = json.loads((root / "sec_hca_submissions.metadata.json").read_bytes())
        body = (root / "sec_hca_submissions.body.json").read_bytes()
        self.assertEqual("98daa6128f1910ae147855f6fde55cc658561730757a7810f886cd059d494f87", hashlib.sha256(body).hexdigest())
        self.assertEqual(meta["body_sha256"], hashlib.sha256(body).hexdigest())
        payload = json.loads(body)
        self.assertEqual("HCA Healthcare, Inc.", payload["name"])
        self.assertEqual("0000860730", payload["cik"])
        self.assertIn("HCA", payload["tickers"])
        self.reject("EXACT_LEGAL_OWNER_ISSUER_NAME_BRIDGE_MISSING",
                    issuer_capture=CapturedSource(meta, body), reviewed_at=datetime.fromisoformat(meta["observed_at"]))

    def test_actual_legacy_review_is_reproducible_zero_proposals_and_zero_runtime_mutations(self):
        from evidence.batch6_healthcare_20261014.review_sources import build_review_report
        stored = json.loads((ROOT / "evidence/batch6_healthcare_20261014/source_review.json").read_bytes())
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Unexpected HTTP")), \
                patch("market_checker_app.storage.scout_store.ScoutStore.__init__", side_effect=AssertionError("Unexpected DB")), \
                patch("pathlib.Path.write_bytes", side_effect=AssertionError("Unexpected mutation")), \
                patch("pathlib.Path.write_text", side_effect=AssertionError("Unexpected mutation")):
            reproduced = build_review_report(ingested_at=datetime.fromisoformat(stored["ingested_at"]))
        self.assertEqual(stored, reproduced)
        self.assertEqual(0, reproduced["proposal_count"])
        self.assertEqual(687, reproduced["canonical_subject_count"])
        self.assertEqual("WAIT_QUOTA_HISTORY", reproduced["source_history_status"])
        self.assertEqual(0, reproduced["new_cms_or_clinical_http_requests"])
        self.assertEqual(2, len(reproduced["cases"]))
        self.assertTrue(all(row["review"]["decision"] == "WITHHOLD" for row in reproduced["cases"]))
        self.assertTrue(all(row["exact_legacy_scope"]["ccn"] is None for row in reproduced["cases"]))
        self.assertFalse(reproduced["production_registry_modified"])


if __name__ == "__main__":
    unittest.main()
