"""Synthetic multi-point contracts plus retained original PFE replay; no LIVE."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import sqlite3
import unittest
from unittest.mock import patch

from market_checker_app.services import fda_applicant_chronology_review as target
from market_checker_app.services import fda_application_product_review as static
from market_checker_app.collectors import fda_legal_applicant_bridge_review as bridge
from tests.test_fda_legal_applicant_bridge_review import fixture as point_fixture, captured, filing_body, exhibit_body, NOW, CHILD, PARENT
from evidence.batch10_healthcare_20261018.replay_sources import inputs


def statement(day):
    return f"SUBSIDIARIES OF THE COMPANY: This is a complete list of all subsidiaries of the Company as of {day}."


def fixture(*, complete=True, second_present=False):
    original, common = point_fixture()
    original["selected_rows"] = []
    captures = {key: common[key] for key in ("application", "company", "issuer")}
    roster_points = []
    for index, (day, literal, accession, present) in enumerate([
        ("2017-12-31", "December 31, 2017", "000007800318000027", True),
        ("2019-12-31", "December 31, 2019", "000007800320000001", second_present),
    ]):
        request = deepcopy(original)
        request.update(filing_key=f"filing{index}", exhibit_key=f"exhibit{index}", roster_as_of=day)
        base = f"https://www.sec.gov/Archives/edgar/data/78003/{accession}/"
        body = exhibit_body([(CHILD if present else "Unrelated Legal Entity", "Ireland")], day=literal)
        locator = None
        if complete:
            body = body.replace(", omitting some subsidiaries", "")
            text = statement(literal)
            body = body.replace("<table>", f"<table><tr><td>{text}</td></tr>")
            locator = {"table_index": 0, "row_index": 0, "exact_text": text}
        captures[request["filing_key"]] = captured(filing_body(day=literal), base + "parent.htm")
        captures[request["exhibit_key"]] = captured(body, base + "ex21.htm")
        roster_points.append({"id": f"point{index}", "requested_roster_as_of": day, "review_request": request, "universe_statement": locator})
    scope = dict(original["scope"], jurisdiction="Ireland")
    return {"scope": scope, "entities": [{"legal_name": CHILD, "jurisdiction": "Ireland"}], "roster_points": roster_points,
            "application_points": [{"id": "applicant", "review_request": deepcopy(original["application_request"])}]}, captures


def actual_inputs():
    point, captures, plan = inputs()
    request = {"scope": dict(point["scope"], jurisdiction="Ireland"),
        "entities": [{"legal_name": point["scope"]["applicant_name"], "jurisdiction": "Ireland"},
                     {"legal_name": "Pfizer Ireland Pharmaceuticals", "jurisdiction": "Ireland"}],
        "roster_points": [
            {"id": "roster2017", "requested_roster_as_of": "2017-12-31", "review_request": point, "universe_statement": None},
            {"id": "approval2019_missing_roster", "requested_roster_as_of": "2019-03-11", "review_request": None, "universe_statement": None},
            {"id": "roster2025_missing_original", "requested_roster_as_of": "2025-12-31", "review_request": None, "universe_statement": None}],
        "application_points": [{"id": "PurpleBook_observed", "review_request": point["application_request"]},
                               {"id": "DrugsFDA_company_observed", "review_request": point["company_request"]}]}
    return request, captures, dict(plan, kind="FDA_APPLICANT_CHRONOLOGY", request=request)


class ApplicantChronologyTests(unittest.TestCase):
    def setUp(self):
        self.request, self.captures = fixture()
        for module in (target, bridge, static):
            timer = patch.object(module, "_physical_clock", return_value=NOW)
            timer.start()
            self.addCleanup(timer.stop)

    def review(self, **kwargs):
        return target.review_fda_applicant_chronology(self.request, self.captures, **kwargs)

    def body(self, key, body):
        metadata, _ = self.captures[key]
        raw = body.encode() if isinstance(body, str) else body
        self.captures[key] = (dict(metadata, body_bytes=len(raw), body_sha256=hashlib.sha256(raw).hexdigest()), raw)

    def test_synthetic_complete_points_report_presence_absence_without_interval(self):
        out = self.review()
        self.assertEqual(out["independent_roster_dates"], 2)
        self.assertEqual(out["data_status"], "DATED_ORIGINAL_POINT_COMPARISON")
        row = out["comparisons"][0]
        self.assertEqual(row["from_state"], "PRESENT_IN_DECLARED_COMPLETE_ROSTER")
        self.assertEqual(row["to_state"], "ABSENT_FROM_DECLARED_COMPLETE_ROSTER")
        self.assertEqual(row["outcome"], "SOURCE_POINT_LISTING_DIFFERENCE_ONLY")
        self.assertFalse(row["ownership_change_or_continuity_verified"])
        self.assertFalse(row["intervening_days_filled"])

    def test_same_positive_endpoints_never_prove_intervening_continuity(self):
        self.request, self.captures = fixture(second_present=True)
        out = self.review()
        self.assertEqual(out["comparisons"][0]["outcome"], "SOURCE_POINT_STATES_MATCH_ONLY")
        self.assertFalse(out["continuous_control_verified"])
        self.assertFalse(out["last_observation_carried_forward"])

    def test_incomplete_roster_literal_row_is_retained_but_universe_state_unknown(self):
        self.request, self.captures = fixture(complete=False)
        out = self.review()
        self.assertEqual(len(out["roster_points"][0]["entities"][0]["literal_listed_rows"]), 1)
        self.assertEqual(out["roster_points"][0]["entities"][0]["universe_state"], "UNKNOWN_IN_INCOMPLETE_ROSTER")
        self.assertEqual(out["roster_points"][1]["entities"][0]["universe_state"], "UNKNOWN_IN_INCOMPLETE_ROSTER")
        self.assertEqual(out["comparisons"][0]["outcome"], "WAIT_DATA")

    def test_no_omission_word_is_not_proof_of_complete_universe(self):
        for point in self.request["roster_points"]:
            point["universe_statement"] = None
        out = self.review()
        self.assertFalse(out["roster_points"][1]["universe"]["complete"])
        self.assertEqual(out["comparisons"][0]["outcome"], "WAIT_DATA")

    def test_borrowed_or_unlocated_universe_declaration_does_not_prove_absence(self):
        for mutate in (lambda p: p["universe_statement"].update(row_index=1),
                       lambda p: p["universe_statement"].update(exact_text=statement("December 31, 2017"))):
            request, captures = fixture()
            mutate(request["roster_points"][1])
            out = target.review_fda_applicant_chronology(request, captures)
            self.assertFalse(out["roster_points"][1]["universe"]["complete"])
            self.assertEqual(out["comparisons"][0]["outcome"], "WAIT_DATA")

    def test_explicit_omission_conflict_overrides_complete_quote(self):
        meta, raw = self.captures["exhibit1"]
        self.body("exhibit1", raw.replace(b"as of December 31, 2019.</p>", b"as of December 31, 2019, omitting some subsidiaries.</p>"))
        out = self.review()
        self.assertEqual(out["roster_points"][1]["universe"]["status"], "CONFLICT")
        self.assertEqual(out["comparisons"][0]["outcome"], "WAIT_DATA")

    def test_missing_endpoint_date_is_query_only_and_never_carries_prior_presence(self):
        point = self.request["roster_points"][1]
        removed = [point["review_request"][k] for k in ("filing_key", "exhibit_key")]
        point.update(review_request=None, universe_statement=None)
        for key in removed:
            self.captures.pop(key)
        out = self.review()
        self.assertIsNone(out["roster_points"][1]["source_roster_as_of"])
        self.assertEqual(out["comparisons"][0]["to_state"], "NOT_OBSERVED_ENDPOINT")
        self.assertEqual(out["data_status"], "WAIT_DATA")

    def test_positive_universe_quote_cannot_override_unrecognized_qualification(self):
        _, raw = self.captures["exhibit1"]
        qualifier = b"<tr><td>SUBSIDIARIES OF THE COMPANY: Certain entries may be omitted under another criterion.</td></tr>"
        self.body("exhibit1", raw.replace(b"</table>", qualifier + b"</table>"))
        out = self.review()
        self.assertFalse(out["roster_points"][1]["universe"]["complete"])
        self.assertEqual(out["comparisons"][0]["to_state"], "UNKNOWN_IN_INCOMPLETE_ROSTER")
        self.assertEqual(out["comparisons"][0]["outcome"], "WAIT_DATA")

    def test_same_source_repeated_under_two_ids_is_not_an_independent_endpoint(self):
        first, second = self.request["roster_points"]
        for key in ("filing_key", "exhibit_key"):
            self.captures.pop(second["review_request"][key])
        second.update(review_request=deepcopy(first["review_request"]), universe_statement=deepcopy(first["universe_statement"]), requested_roster_as_of=first["requested_roster_as_of"])
        out = self.review()
        self.assertEqual(out["independent_roster_versions"], 1)
        self.assertFalse(out["roster_points"][1]["independent_source_version"])
        self.assertEqual(out["comparisons"][0]["outcome"], "WAIT_DATA")

    def test_same_day_conflicting_versions_are_not_a_dated_ownership_change(self):
        second = self.request["roster_points"][1]
        second["requested_roster_as_of"] = "2017-12-31"
        second["review_request"]["roster_as_of"] = "2017-12-31"
        second["universe_statement"]["exact_text"] = statement("December 31, 2017")
        for key in ("filing1", "exhibit1"):
            self.body(key, self.captures[key][1].replace(b"December 31, 2019", b"December 31, 2017"))
        out = self.review()
        self.assertEqual(out["comparisons"][0]["outcome"], "SAME_DAY_SOURCE_VERSION_CONFLICT")
        self.assertEqual(out["data_status"], "WAIT_DATA")

    def test_spelling_country_and_jurisdiction_variants_are_separate_entities(self):
        self.request["entities"].extend([{"legal_name": CHILD.replace(" Unlimited Company", ""), "jurisdiction": "Ireland"},
                                          {"legal_name": CHILD, "jurisdiction": "IE"}])
        out = self.review()
        states = out["roster_points"][0]["entities"]
        self.assertEqual(states[1]["universe_state"], "ABSENT_FROM_DECLARED_COMPLETE_ROSTER")
        self.assertEqual(states[2]["universe_state"], "JURISDICTION_CONFLICT")
        self.assertFalse(out["spelling_aliases_created"])

    def test_duplicate_exact_legal_rows_do_not_establish_unique_presence(self):
        meta, raw = self.captures["exhibit0"]
        row = f"<tr><td>{CHILD}</td><td>Ireland</td></tr>".encode()
        self.body("exhibit0", raw.replace(b"</table>", row + b"</table>"))
        out = self.review()
        self.assertEqual(out["roster_points"][0]["entities"][0]["universe_state"], "AMBIGUOUS_EXACT_LEGAL_NAME_ROWS")
        self.assertEqual(out["comparisons"][0]["outcome"], "WAIT_DATA")

    def test_foreign_section_cannot_be_imported_as_a_roster_point(self):
        meta, raw = self.captures["exhibit1"]
        added = f"<h2>Independent counterparties; not subsidiaries</h2><table><tr><td>{CHILD}</td><td>Ireland</td></tr></table>".encode()
        self.body("exhibit1", raw.replace(b"</body>", added + b"</body>"))
        out = self.review()
        self.assertIsNone(out["roster_points"][1]["source_roster_as_of"])
        self.assertEqual(out["comparisons"][0]["outcome"], "WAIT_DATA")

    def test_actual_Ireland_old_name_and_current_applicant_are_not_an_alias(self):
        request, captures, _ = actual_inputs()
        out = target.review_fda_applicant_chronology(request, captures)
        point = out["roster_points"][0]
        self.assertEqual(point["source_roster_as_of"], "2017-12-31")
        self.assertEqual(point["entities"][0]["literal_listed_rows"], [])
        self.assertEqual(len(point["entities"][1]["literal_listed_rows"]), 1)
        self.assertEqual(point["entities"][1]["universe_state"], "UNKNOWN_IN_INCOMPLETE_ROSTER")
        self.assertEqual(out["independent_roster_dates"], 1)
        self.assertEqual(out["data_status"], "WAIT_DATA")

    def test_actual_PurpleBook_two_products_incomplete_container_and_company_role_remain(self):
        request, captures, _ = actual_inputs()
        out = target.review_fda_applicant_chronology(request, captures)
        applicant, company = out["application_points"]
        self.assertEqual([p["product_number"] for p in applicant["product_rows"]], ["001", "002"])
        self.assertFalse(applicant["original_container_explicitly_closed"])
        self.assertEqual(company["source_party_role"], "COMPANY_AS_DISPLAYED")
        self.assertFalse(company["exact_requested_applicant_name_match"])
        self.assertEqual(applicant["source_review"]["application_facts"]["original_approval_day"], "2019-03-11")
        self.assertFalse(out["approval_date_is_ownership_since"])
        self.assertFalse(out["product_owner_verified"])

    def test_reobserved_application_body_is_not_a_new_version_or_extra_scope(self):
        duplicate = deepcopy(self.request["application_points"][0])
        duplicate["id"] = "reobserved"
        self.request["application_points"].append(duplicate)
        out = self.review()
        self.assertEqual(out["independent_application_versions"], 1)
        self.assertFalse(out["application_points"][1]["independent_source_version"])

    def test_missing_application_endpoint_is_not_filled_from_prior_product(self):
        self.request["application_points"].append({"id": "missing_application", "review_request": None})
        out = self.review()
        self.assertEqual(out["application_points"][1]["status"], "WAIT_DATA")
        self.assertIsNone(out["application_points"][1]["source_observed_at"])

    def test_same_role_application_versions_compare_literal_rows_not_product_absence(self):
        source_request = deepcopy(self.request["application_points"][0]["review_request"])
        source_request["source_key"] = "application_later"
        source_request["product_selection"] = None
        old_meta, old_raw = self.captures["application"]
        new_raw = old_raw.replace(b"420MG", b"421MG")
        self.assertNotEqual(new_raw, old_raw)
        new_meta = dict(old_meta, body_sha256=hashlib.sha256(new_raw).hexdigest(), body_bytes=len(new_raw),
                        requested_at="2026-10-18T10:30:00+00:00", observed_at="2026-10-18T10:30:00+00:00")
        self.captures["application_later"] = (new_meta, new_raw)
        source_request["source_version_sha256"] = new_meta["body_sha256"]
        self.request["application_points"].append({"id": "later_applicant", "review_request": source_request})
        out = self.review()
        row = out["application_comparisons"][0]
        self.assertEqual(row["outcome"], "OBSERVED_LITERAL_ROW_DIFFERENCE_ONLY")
        self.assertEqual(row["only_in_from_capture"][0]["strength"], "420MG")
        self.assertEqual(row["only_in_to_capture"][0]["strength"], "421MG")
        self.assertFalse(row["row_difference_proves_product_absence"])
        self.assertFalse(row["product_universe_complete"])
        self.assertFalse(row["ownership_change_or_continuity_verified"])

    def test_distinct_Company_and_Applicant_roles_are_never_comparable_versions(self):
        request, captures, _ = actual_inputs()
        out = target.review_fda_applicant_chronology(request, captures)
        self.assertEqual(out["application_comparisons"], [])
        self.assertIn("TWO_INDEPENDENT_SAME_ROLE_APPLICATION_ORIGINALS_REQUIRED", out["gaps"])

    def test_same_capture_clock_different_product_versions_remains_explicit_conflict(self):
        source_request = deepcopy(self.request["application_points"][0]["review_request"])
        source_request.update(source_key="application_revision", product_selection=None)
        meta, raw = self.captures["application"]
        revised = raw.replace(b"420MG", b"421MG")
        self.assertNotEqual(revised, raw)
        self.captures["application_revision"] = (dict(meta, body_sha256=hashlib.sha256(revised).hexdigest(), body_bytes=len(revised)), revised)
        source_request["source_version_sha256"] = self.captures["application_revision"][0]["body_sha256"]
        self.request["application_points"].append({"id": "revision", "review_request": source_request})
        out = self.review()
        self.assertEqual(out["application_comparisons"][0]["outcome"], "SAME_OBSERVATION_CLOCK_DIFFERENT_APPLICATION_VERSIONS")

    def test_whole_hash_tamper_metadata_only_and_future_clock_never_prove_absence(self):
        for change in ("hash", "metadata_only", "future"):
            request, captures = fixture()
            meta, raw = captures["exhibit1"]
            if change == "hash": captures["exhibit1"] = (dict(meta, body_sha256="0" * 64), raw)
            elif change == "metadata_only": captures["exhibit1"] = (meta, None)
            else: captures["exhibit1"] = (dict(meta, known_at=(NOW + timedelta(seconds=1)).isoformat()), raw)
            out = target.review_fda_applicant_chronology(request, captures)
            self.assertEqual(out["roster_points"][1]["status"], "WAIT_DATA")
            self.assertEqual(out["comparisons"][0]["to_state"], "NOT_OBSERVED_ENDPOINT")

    def test_all_four_withheld_hashes_are_rejected_before_any_child_parser(self):
        meta, raw = self.captures["application"]
        for hashed in bridge.WITHHELD:
            self.captures["application"] = (dict(meta, body_sha256=hashed), None)
            with patch.object(bridge, "review_fda_legal_applicant_bridge", side_effect=AssertionError("No unavailable whole-body parser")), \
                 patch.object(target, "review_fda_application_product", side_effect=AssertionError("No unavailable whole-body parser")):
                out = self.review()
            self.assertEqual(out["independent_roster_versions"], 0)
            self.assertFalse(out["product_owner_verified"])

    def test_issuer_key_application_or_point_day_mismatch_is_not_rerouted(self):
        for key, value in (("issuer_cik", "0000789019"), ("application_number", "761082")):
            request, captures = fixture()
            request["scope"][key] = value
            with self.assertRaises(ValueError): target.review_fda_applicant_chronology(request, captures)
        self.request["roster_points"][1]["requested_roster_as_of"] = "2020-12-31"
        with self.assertRaises(ValueError): self.review()

    def test_endpoint_request_and_capture_bounds_duplicates_and_types(self):
        for change in ("duplicate_entity", "duplicate_id", "extra_capture", "too_many", "wrong_day"):
            request, captures = fixture()
            if change == "duplicate_entity": request["entities"].append(deepcopy(request["entities"][0]))
            elif change == "duplicate_id": request["application_points"][0]["id"] = "point0"
            elif change == "extra_capture": captures["unselected"] = captures["issuer"]
            elif change == "too_many": request["roster_points"] *= 3
            else: request["roster_points"][0]["requested_roster_as_of"] = "2017-1-1"
            with self.assertRaises(ValueError): target.review_fda_applicant_chronology(request, captures)

    def test_completion_after_all_child_facts_and_source_cutoff_is_not_output_availability(self):
        end = NOW + timedelta(seconds=5)
        cutoff = NOW - timedelta(microseconds=20)
        with patch.object(target, "_physical_clock", side_effect=[NOW, end]):
            out = self.review(reviewed_at=cutoff)
        self.assertEqual(out["review_started_at"], NOW.isoformat())
        self.assertEqual(out["source_read_cutoff"], cutoff.isoformat())
        self.assertEqual(out["known_at"], end.isoformat())
        self.assertEqual(out["review_completed_at"], end.isoformat())

    def test_rollback_future_child_naive_or_future_cutoff_are_rejected(self):
        with patch.object(target, "_physical_clock", side_effect=[NOW, NOW - timedelta(microseconds=1)]):
            with self.assertRaises(ValueError): self.review()
        with patch.object(bridge, "_physical_clock", return_value=NOW + timedelta(seconds=1)):
            with self.assertRaises(ValueError): self.review()
        for cutoff in (NOW.replace(tzinfo=None), NOW + timedelta(microseconds=1)):
            with self.assertRaises(ValueError): self.review(reviewed_at=cutoff)

    def test_no_HTTP_SQL_files_registry_mutation_and_results_detached(self):
        before = deepcopy((self.request, self.captures))
        with patch("socket.socket.connect", side_effect=AssertionError("No HTTP")), \
             patch.object(sqlite3, "connect", side_effect=AssertionError("No SQL")), \
             patch("builtins.open", side_effect=AssertionError("No file IO")):
            out = self.review()
        self.assertEqual(before, (self.request, self.captures))
        out["scope"]["issuer_name"] = "Changed output only"
        self.assertNotEqual(out["scope"], self.request["scope"])
        for key in ("ownership_start_verified", "continuous_control_verified", "product_owner_verified", "issuer_attribution_allowed", "score_or_model_modified", "trading_allowed"):
            self.assertFalse(out[key])
        self.assertEqual(out["registry_adoptions"], 0)


if __name__ == "__main__":
    unittest.main()
