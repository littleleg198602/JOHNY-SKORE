"""Standalone exact-day source joins; synthetic positives are not acceptance."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from market_checker_app.services import sec_dated_instrument_point_review as module
from tests.test_batch9_instrument_class_scope import fixture as class_fixture, registered_selection, replace_body, capture

NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
CLASS = "Class A Common Stock, $0.0001 par value per share"


def fixture():
    request, captures = class_fixture()
    scope = request["scope"]
    selected = {"anchor_on": "2026-08-26",
                "identity": {"issuer_cik": scope["issuer_cik"], "issuer_name": "Everpure, Inc.", "ticker": scope["symbol"],
                             "class_title": scope["class_title"], "cusip": scope["cusip"]},
                "instrument": {"source_key": "schedule", "index_key": "schedule_index", "form": "SCHEDULE 13G/A"},
                "cover": {"source_key": "cover", "proof_key": "submissions", "proof_kind": "SUBMISSIONS", "form": "8-K",
                          "registered_claim": request["registered_claims"][0]}}
    return selected, captures


def change_schedule(request, captures, old, new):
    previous = captures["schedule"][1]
    current = previous.replace(old, new)
    if current == previous:
        raise AssertionError("Synthetic Schedule mutation did not apply")
    replace_body(captures, "schedule", current)
    index = captures["schedule_index"][1]
    replace_body(captures, "schedule_index", index.replace(("<td>" + str(len(previous)) + "</td>").encode(),
                                                          ("<td>" + str(len(current)) + "</td>").encode()))


def change_cover(request, captures, old, new):
    body = captures["cover"][1]
    current = body.replace(old, new)
    if current == body:
        raise AssertionError("Synthetic cover mutation did not apply")
    replace_body(captures, "cover", current)
    request["cover"]["registered_claim"] = registered_selection(captures)


class DatedInstrumentPointTests(unittest.TestCase):
    def setUp(self):
        clock = patch.object(module, "_physical_clock", return_value=NOW)
        clock.start(); self.addCleanup(clock.stop)
        self.request, self.captures = fixture()

    def review(self, **kwargs):
        return module.review_sec_dated_instrument_point(self.request, self.captures, **kwargs)

    def assert_partial(self, blocker):
        report = self.review()
        self.assertFalse(report["point_supported"])
        self.assertIsNone(report["point"])
        self.assertIn(blocker, report["blockers"])
        return report

    def test_synthetic_exact_point_is_a_read_only_source_assertion_not_action_or_identity_adoption(self):
        original = deepcopy((self.request, self.captures))
        with patch("socket.socket.connect", side_effect=AssertionError("No HTTP")), patch("sqlite3.connect", side_effect=AssertionError("No DB")), patch.object(Path, "glob", side_effect=AssertionError("No discovery")):
            report = self.review()
        self.assertEqual(original, (self.request, self.captures))
        self.assertTrue(report["point_supported"])
        self.assertEqual([], report["blockers"])
        self.assertEqual("2026-08-26", report["point"]["anchor_on"])
        self.assertEqual("ONE_SOURCE_ASSERTED_DAY_ONLY", report["point_scope"])
        self.assertIsNone(report["action_effective_on"])
        self.assertFalse(report["continuity_verified"])
        self.assertFalse(report["issued_validity_verified"])
        self.assertEqual(4, len(report["acceptance_blockers"]))
        for key in ("registry_adoptions", "new_verified_identities", "current_q3_verified_instrument_coverage", "network_requests_performed"):
            self.assertEqual(0, report[key])
        for key in ("production_registry_changed", "automatic_alias_allowed", "interval_extension_allowed", "historical_availability_inferred", "scoring_allowed", "trading_allowed"):
            self.assertFalse(report[key])
        digest = report.pop("review_sha256")
        self.assertEqual(digest, hashlib.sha256(module._encoded(report)).hexdigest())

    def test_requested_day_cannot_be_replaced_by_a_quarter_or_nearby_source_day(self):
        self.request["anchor_on"] = "2026-08-27"
        report = self.assert_partial("SCHEDULE_EVENT_DAY_DIFFERS_FROM_REQUESTED_ANCHOR")
        self.assertIn("COVER_CONTEXT_DAY_DIFFERS_FROM_REQUESTED_ANCHOR", report["blockers"])
        self.assertEqual("2026-08-26", report["schedule_instrument_fact"]["event_on"])
        self.assertEqual("2026-08-26", report["cover_security_fact"]["context_anchor_on"])
        self.request["anchor_on"] = "2026q3"
        with self.assertRaises(ValueError): self.review()

    def test_unknown_requested_anchor_remains_unknown(self):
        self.request["anchor_on"] = None
        report = self.assert_partial("EXACT_REQUESTED_ANCHOR_UNKNOWN")
        self.assertIsNone(report["requested_anchor_on"])
        self.assertIsNone(report["point"])
        self.assertEqual("2026-08-26", report["schedule_instrument_fact"]["event_on"])

    def test_generic_or_missing_par_schedule_class_is_not_filled_by_the_cover(self):
        for title in (b"Common Stock", b"Class A Common Stock"):
            with self.subTest(title=title):
                self.request, self.captures = fixture()
                change_schedule(self.request, self.captures, CLASS.encode(), title)
                report = self.assert_partial("SCHEDULE_EXACT_PAR_TERM_UNKNOWN")
                self.assertEqual(title.decode(), report["schedule_instrument_fact"]["class_title"])
                self.assertEqual(CLASS, report["cover_security_fact"]["class_title"])

    def test_schedule_class_or_par_and_cusip_disagreements_remain_literal_conflicts(self):
        for before, after, blocker in ((b"$0.0001", b"$0.001", "SCHEDULE_CLASS_OR_PAR_CONFLICT"),
                                       (b"Class A", b"Class B", "SCHEDULE_CLASS_OR_PAR_CONFLICT"),
                                       (b"74624M102", b"037833100", "SCHEDULE_CUSIP_CONFLICT")):
            with self.subTest(after=after):
                self.request, self.captures = fixture()
                change_schedule(self.request, self.captures, before, after)
                self.assert_partial(blocker)

    def test_cover_name_symbol_or_class_cannot_be_relabelled_to_match_the_proposal(self):
        for before, after, blocker in ((b">P</ix:nonNumeric>", b">Q</ix:nonNumeric>", "COVER_SYMBOL_CONFLICT"),
                                       (b"Class A", b"Class B", "COVER_CLASS_OR_PAR_CONFLICT"),
                                       (b"Everpure, Inc.", b"Different Company", "COVER_ISSUER_CIK_OR_NAME_CONFLICT")):
            with self.subTest(after=after):
                self.request, self.captures = fixture()
                change_cover(self.request, self.captures, before, after)
                if b"Different Company" in after:
                    self.request["cover"]["registered_claim"]["issuer_name"] = "Different Company"
                self.assert_partial(blocker)

    def test_same_accession_cannot_supply_two_independent_dated_disclosures(self):
        previous = "000010290926001203"
        current = "000147443226000084"
        for key in ("schedule", "schedule_index"):
            metadata, body = self.captures[key]
            url = metadata["requested_url"].replace(previous, current).replace("0000102909-26-001203", "0001474432-26-000084")
            body = body.replace(previous.encode(), current.encode())
            self.captures[key] = capture(url, body)
        self.assert_partial("INDEPENDENT_SCHEDULE_AND_COVER_FILINGS_MISSING")

    def test_subject_archive_route_is_not_an_alias_for_the_indexed_reporting_filer_route(self):
        metadata, body = self.captures["schedule"]
        self.captures["schedule"] = capture(metadata["requested_url"].replace("/data/102909/", "/data/1474432/"), body)
        report = self.assert_partial("CAPTURED_SUBJECT_ROUTE_DIFFERS_FROM_INDEX_FILER_ROUTE")
        self.assertEqual("0001474432", report["schedule_instrument_fact"]["issuer_cik"])
        self.assertIn("/data/102909/", report["schedule_publication_proof"]["index_raw_xml_url"])

    def test_exact_original_filedby_and_subject_roles_remain_separate(self):
        report = self.review()
        self.assertTrue(report["point_supported"])
        companies = report["schedule_publication_proof"]["index_companies"]
        self.assertEqual(["Filed by", "Subject"], [row["role"] for row in companies])
        self.assertEqual(["0000102909", "0001474432"], [row["cik"] for row in companies])
        self.assertEqual("0001474432", report["schedule_index_subject"]["cik"])

    def test_wrong_cover_archive_cik_cannot_be_repaired_by_source_or_catalog_hints(self):
        metadata, body = self.captures["cover"]
        self.captures["cover"] = capture(metadata["requested_url"].replace("/data/1474432/", "/data/29915/"), body)
        with self.assertRaises(ValueError): self.review()
        self.request["observed_catalog"] = {"P": "0001474432"}
        with self.assertRaisesRegex(ValueError, "dated-point request"): self.review()

    def test_index_size_and_unqualified_clock_conflicts_are_preserved_without_join(self):
        body = self.captures["schedule_index"][1]
        current_length = str(len(self.captures["schedule"][1])).encode()
        changed = body.replace(b"<td>" + current_length + b"</td>", b"<td>9999</td>").replace(b"2026-09-01 16:00:00", b"2026-08-31 16:00:00")
        replace_body(self.captures, "schedule_index", changed)
        report = self.assert_partial("INDEX_DECLARED_SERVED_BYTE_CONFLICT")
        self.assertIn("INDEX_ACCEPTANCE_CALENDAR_DAY_DIFFERS_FROM_FILING_DAY", report["blockers"])
        proof = report["schedule_publication_proof"]
        self.assertEqual(9999, proof["index_declared_bytes"])
        self.assertEqual("2026-08-31 16:00:00", proof["acceptance_unqualified_raw"])
        self.assertIsNone(proof["publication_at"])
        self.assertFalse(proof["acceptance_timezone_inferred"])

    def test_missing_schedule_event_or_invalid_cusip_is_not_filled_from_requested_anchor(self):
        for before, after in ((b"eventDateRequiresFilingThisStatement", b"unrecognizedEventDate"),
                              (b"74624M102", b"74624M103")):
            with self.subTest(after=after):
                self.request, self.captures = fixture()
                change_schedule(self.request, self.captures, before, after)
                report = self.assert_partial("ORIGINAL_SCHEDULE_FACT_FIELDS_UNRESOLVED")
                self.assertIsNone(report["schedule_instrument_fact"])
                self.assertIsNone(report["point"])

    def test_cover_without_qualified_publication_timestamp_stays_partial(self):
        metadata, cover_body = self.captures["cover"]
        body = f'''<html><span class="companyName">Everpure <a href="/cgi-bin/browse-edgar?CIK=0001474432">CIK</a></span>
        <div class="infoHead">Filing Date</div><div class="info">2026-09-01</div><div class="infoHead">Accepted</div><div class="info">2026-09-01 16:00:00</div>
        <table><tr><td>1</td><td>8-K</td><td><a href="{metadata['requested_url']}">cover.htm</a></td><td>8-K</td><td>{len(cover_body)}</td></tr></table></html>'''.encode()
        self.captures["submissions"] = capture("https://www.sec.gov/Archives/edgar/data/1474432/000147443226000084/0001474432-26-000084-index.html", body)
        self.request["cover"]["proof_kind"] = "INDEX"
        report = self.assert_partial("COVER_PUBLICATION_TIMESTAMP_UNKNOWN")
        self.assertIsNone(report["cover_security_fact"])
        self.assertIsNone(report["cover_publication_proof"]["publication_at"])

    def test_missing_and_withheld_whole_source_never_gets_a_substitute(self):
        metadata, body = self.captures["schedule"]
        self.captures["schedule"] = ({**metadata, "status": "WITHHELD_SENSITIVE_BODY"}, None)
        self.assert_partial("ORIGINAL_SCHEDULE_OR_INDEX_UNAVAILABLE")
        self.captures["schedule"] = ({**metadata, "status": "WITHHELD_SENSITIVE_BODY"}, body)
        with self.assertRaisesRegex(ValueError, "restored or substituted"): self.review()

    def test_unsafe_whole_body_or_request_is_rejected_before_projection(self):
        replace_body(self.captures, "schedule", self.captures["schedule"][1] + b' api_key="EXAMPLEONLY000000"')
        with self.assertRaisesRegex(ValueError, "access-bearing"): self.review()
        self.request, self.captures = fixture(); self.request["identity"]["issuer_name"] = "api_key=EXAMPLEONLY000000"
        with self.assertRaisesRegex(ValueError, "safe dated-point"): self.review()

    def test_whole_byte_and_local_registered_row_pins_are_enforced(self):
        self.captures["cover"][0]["raw_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "source bytes/hash/status conflict"): self.review()
        self.request, self.captures = fixture()
        self.request["cover"]["registered_claim"]["security_pin"]["sha256"] = "0" * 64
        report = self.assert_partial("REGISTERED_COVER_LOCAL_ROW_BINDING_UNRESOLVED")
        self.assertTrue(report["original_cover_contexts"])

    def test_specimen_extra_capture_and_weakened_identity_scope_are_not_point_evidence(self):
        self.request["instrument"]["form"] = "EX-4.1"
        with self.assertRaisesRegex(ValueError, "original Schedule13"): self.review()
        self.request, self.captures = fixture(); self.captures["specimen"] = self.captures["cover"]
        with self.assertRaisesRegex(ValueError, "four explicit original"): self.review()
        self.request, self.captures = fixture(); self.request["identity"]["class_title"] = "Common Stock"
        with self.assertRaisesRegex(ValueError, "exact issuer/name/symbol/class/par"): self.review()

    def test_future_deadline_does_not_allow_future_source_selection(self):
        metadata = self.captures["schedule"][0]
        metadata["observed_at"] = (NOW + timedelta(seconds=1)).isoformat()
        with self.assertRaisesRegex(ValueError, "future original capture"): self.review(as_of=NOW + timedelta(seconds=5))

    def test_new_interpretation_clocks_record_end_and_reject_rollback_and_cutoff_overrun(self):
        end = NOW + timedelta(seconds=2)
        with patch.object(module, "_physical_clock", side_effect=[NOW, end]):
            report = self.review(as_of=NOW + timedelta(seconds=3))
        self.assertEqual(NOW.isoformat(), report["source_as_of"])
        self.assertEqual(NOW.isoformat(), report["review_started_at"])
        self.assertEqual(end.isoformat(), report["knowledge_at"])
        with patch.object(module, "_physical_clock", side_effect=[NOW, NOW - timedelta(microseconds=1)]):
            with self.assertRaisesRegex(ValueError, "clock moved backwards"): self.review()
        with patch.object(module, "_physical_clock", side_effect=[NOW, end]):
            with self.assertRaisesRegex(ValueError, "completed after the explicit cutoff"): self.review(as_of=NOW + timedelta(seconds=1))
        with self.assertRaisesRegex(ValueError, "cannot be backdated"): self.review(as_of=NOW - timedelta(seconds=1))

    def test_completion_is_sampled_after_original_row_and_derived_point_review(self):
        clock, events = [NOW], []
        original = module._cover

        def physical():
            events.append("clock"); return clock[0]

        def slow_cover(*args):
            result = original(*args)
            events.append("cover_finished"); clock[0] += timedelta(seconds=2)
            return result

        with patch.object(module, "_physical_clock", side_effect=physical), patch.object(module, "_cover", side_effect=slow_cover):
            report = self.review()
        self.assertEqual(["clock", "cover_finished", "clock"], events)
        self.assertEqual((NOW + timedelta(seconds=2)).isoformat(), report["knowledge_at"])

    def test_schedule13d_and_single_plural_cusip_do_not_need_a_merger_role(self):
        change_schedule(self.request, self.captures, b"http://www.sec.gov/edgar/schedule13g", b"http://www.sec.gov/edgar/schedule13D")
        change_schedule(self.request, self.captures, b"SCHEDULE 13G/A", b"SCHEDULE 13D/A")
        change_schedule(self.request, self.captures, b"eventDateRequiresFilingThisStatement", b"dateOfEvent")
        change_schedule(self.request, self.captures, b"<issuerCusip>74624M102</issuerCusip>", b"<issuerCusips><issuerCusipNumber>74624M102</issuerCusipNumber></issuerCusips>")
        replace_body(self.captures, "schedule_index", self.captures["schedule_index"][1].replace(b"SCHEDULE 13G/A", b"SCHEDULE 13D/A"))
        self.request["instrument"]["form"] = "SCHEDULE 13D/A"
        self.assertTrue(self.review()["point_supported"])

    def test_original_p_case_preserves_march_schedule_and_august_cover_conflict(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("actual_point_replay", root / "evidence/batch11_identity_20261018/replay_point.py")
        replay = importlib.util.module_from_spec(spec); spec.loader.exec_module(replay)
        request, captures, plan = replay.build_inputs()
        report = module.review_sec_dated_instrument_point(request, captures)
        self.assertEqual("SEC_DATED_INSTRUMENT_POINT", plan["kind"])
        self.assertEqual(4, len(captures))
        self.assertEqual(201828, sum(len(pair[1]) for pair in captures.values()))
        self.assertFalse(report["point_supported"])
        self.assertEqual("2026-03-13", report["schedule_instrument_fact"]["event_on"])
        self.assertEqual("Common Stock", report["schedule_instrument_fact"]["class_title"])
        self.assertEqual("2026-08-26", report["cover_security_fact"]["context_anchor_on"])
        self.assertEqual("P", report["cover_security_fact"]["symbol"])
        self.assertEqual("2026-10-16T12:18:16.901780+00:00", report["source_knowledge_at"])
        self.assertEqual(5, len(report["blockers"]))
        self.assertIn("CAPTURED_SUBJECT_ROUTE_DIFFERS_FROM_INDEX_FILER_ROUTE", report["blockers"])
        self.assertEqual(0, report["registry_adoptions"])
        self.assertEqual(0, report["current_q3_verified_instrument_coverage"])
