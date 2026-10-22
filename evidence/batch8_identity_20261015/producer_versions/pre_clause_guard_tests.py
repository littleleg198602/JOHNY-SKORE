"""Synthetic scope boundaries plus the six original batch8 SEC responses."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from market_checker_app.services import sec_symbol_action_scope_review as module
from market_checker_app.services.sec_symbol_action_scope_review import review_symbol_action_scope, symbol_action_passages

NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
OBSERVED = NOW-timedelta(days=1)
CLASS = "Class A Common Stock, par value $0.0001 per share"
CIK = "0001474432"


def capture(url, body):
    return ({"requested_url": url, "final_url": url, "http_status": 200, "status": "PUBLIC_ORIGINAL_BYTES_CAPTURED",
             "raw_sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
             "started_at": (OBSERVED-timedelta(seconds=1)).isoformat(), "observed_at": OBSERVED.isoformat()}, body)


def html(text):
    return ("<html><body>"+text+"</body></html>").encode()


def fixture():
    captures, rows, descriptors = {}, [], []
    def add(key, number, filename, body, form):
        accession = f"000147443226{number:06d}"
        url = f"https://www.sec.gov/Archives/edgar/data/1474432/{accession}/{filename}"
        captures[key] = capture(url, body)
        rows.append({"accessionNumber": f"0001474432-26-{number:06d}", "primaryDocument": filename,
                     "form": form, "filingDate": "2026-04-10", "acceptanceDateTime": "2026-04-10T10:00:00Z"})
        descriptors.append({"source_key": key, "proof_key": "submissions", "proof_kind": "SUBMISSIONS", "form": form})
    symbol = "On 2026-04-01, Everpure, Inc. changed its ticker symbol from PSTG to P."
    continuity = symbol+f" CUSIP 74624M102 remained unchanged for its {CLASS}."
    add("first", 101, "first.htm", html(f"<p>{symbol}</p><p>{continuity}</p>"), "8-K")
    add("second", 102, "second.htm", html("<p>Effective 2026-04-01, Everpure, Inc. changed its ticker symbol from PSTG to P.</p>"), "8-K")
    for key, number, ticker, anchor in (("old_cover", 103, "PSTG", "2026-03-31"), ("new_cover", 104, "P", "2026-04-01")):
        body = f'''<html xmlns:dei="http://xbrl.sec.gov/dei/2026" xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">
          <xbrli:context id="Security"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{CIK}</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>{anchor}</xbrli:instant></xbrli:period></xbrli:context>
          <ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="Security">{CIK}</ix:nonNumeric>
          <ix:nonNumeric name="dei:EntityRegistrantName" contextRef="Security">Everpure, Inc.</ix:nonNumeric>
          <ix:nonNumeric name="dei:TradingSymbol" contextRef="Security">{ticker}</ix:nonNumeric>
          <ix:nonNumeric name="dei:Security12bTitle" contextRef="Security">{CLASS}</ix:nonNumeric></html>'''.encode()
        add(key, number, key+".htm", body, "10-Q")
    for key, number, day in (("old_instrument", 105, "03/31/2026"), ("new_instrument", 106, "04/01/2026")):
        body = f'''<edgarSubmission xmlns="http://www.sec.gov/edgar/schedule13g"><headerData><submissionType>SCHEDULE 13G</submissionType></headerData><formData><coverPageHeader><securitiesClassTitle>{CLASS}</securitiesClassTitle><eventDateRequiresFilingThisStatement>{day}</eventDateRequiresFilingThisStatement><issuerInfo><issuerCik>{CIK}</issuerCik><issuerName>Everpure, Inc.</issuerName><issuerCusip>74624M102</issuerCusip></issuerInfo></coverPageHeader></formData></edgarSubmission>'''.encode()
        add(key, number, key+".xml", body, "SCHEDULE 13G")
    data = {"cik": "1474432", "filings": {"recent": {key: [row[key] for row in rows] for key in rows[0]}}}
    captures["submissions"] = capture("https://data.sec.gov/submissions/CIK0001474432.json", json.dumps(data).encode())
    def action(key, index=0, **changes):
        return {"source_key": key, "event_kind": "SYMBOL_CHANGE", "asserted_phase": "EFFECTIVE", "day": "2026-04-01",
                "issuer_source_key":"old_cover","issuer_context_ref":"Security","issuer_name_text":"Everpure, Inc.","issuer_reference_text":"Everpure, Inc.",
                "date_text": "2026-04-01", "date_role": "EFFECTIVE", "selection": symbol_action_passages(captures[key][1])[index], **changes}
    request = {"scope": {"issuer_cik": CIK, "from_symbol": "PSTG", "to_symbol": "P", "class_title": CLASS, "cusip": "74624M102"},
               "filings": descriptors,
               "action_claims": [action("first"), action("second"), action("first", 1, event_kind="CUSIP_CONTINUITY")],
               "cover_claims": [{"source_key": key, "role": role, "context_ref": "Security", "issuer_name": "Everpure, Inc."}
                                for key, role in (("old_cover", "FROM"), ("new_cover", "TO"))],
               "instrument_claims": [{"source_key": key+"_instrument", "role": role, "cover_source_key": key+"_cover", "cover_context_ref": "Security"}
                                     for key, role in (("old", "FROM"), ("new", "TO"))]}
    return request, captures


def replace_body(captures, key, body):
    captures[key] = capture(captures[key][0]["requested_url"], body)


def one_action(request, captures, text, **fields):
    replace_body(captures, "first", html(f"<p>{text}</p>"))
    request["action_claims"] = [{**request["action_claims"][0], "selection": symbol_action_passages(captures["first"][1])[0], **fields}]


class SymbolActionScopeTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch.object(module, "_physical_clock", return_value=NOW)
        self.clock.start(); self.addCleanup(self.clock.stop)
        self.request, self.captures = fixture()

    def review(self, **kwargs):
        return review_symbol_action_scope(self.request, self.captures, **kwargs)

    def test_exact_independent_scopes_are_reviewable_without_any_adoption_or_side_effect(self):
        before = deepcopy((self.request, self.captures))
        with patch("socket.socket.connect", side_effect=AssertionError("No HTTP")), \
                patch("sqlite3.connect", side_effect=AssertionError("No DB")), \
                patch.object(Path,"glob",side_effect=AssertionError("No discovery")), \
                patch.object(Path,"write_bytes",side_effect=AssertionError("No writes")):
            report = self.review()
        self.assertEqual("PRIMARY_SCOPE_READY_FOR_EXPLICIT_REVIEW", report["status"])
        self.assertEqual([], report["blockers"])
        self.assertEqual("2026-04-01", report["effective_on"])
        self.assertEqual(2, report["independent_effective_disclosures"])
        self.assertEqual(before, (self.request, self.captures))
        self.assertEqual(NOW.isoformat(), report["knowledge_at"])
        for key in ("production_registry_changed", "automatic_alias_allowed", "interval_extension_allowed", "scoring_allowed",
                    "historical_machine_availability_inferred", "historical_as_filed_versions_verified", "quota_or_lease_modified", "trading_allowed"):
            self.assertFalse(report[key])
        self.assertEqual(0, report["new_verified_identities"])
        self.assertEqual(0, report["current_q3_verified_coverage"])
        sha = report.pop("review_sha256")
        self.assertEqual(sha,hashlib.sha256(module._encoded(report)).hexdigest())

    def test_planned_action_never_becomes_completed_when_its_day_passes(self):
        one_action(self.request,self.captures,"Effective 2026-10-20, Everpure, Inc. will change its ticker symbol from PSTG to P.",
                   asserted_phase="PLANNED",day="2026-10-20",date_text="2026-10-20")
        for physical in (NOW, NOW+timedelta(days=10)):
            with self.subTest(physical=physical),patch.object(module,"_physical_clock",return_value=physical):
                report=self.review();action=report["action_claims"][0]
                self.assertEqual("PLANNED",action["literal_phase"])
                self.assertEqual("2026-10-20",action["planned_effective_on"])
                self.assertIsNone(action["effective_on"])
                self.assertIsNone(report["effective_on"])
        self.request["action_claims"][0]["asserted_phase"]="EFFECTIVE"
        self.assertIn("ASSERTED_PHASE_NOT_SUPPORTED_BY_WHOLE_PASSAGE",self.review()["blockers"])

    def test_announcement_day_remains_separate_from_the_effective_day(self):
        one_action(self.request,self.captures,"On 2026-03-25, Everpure, Inc. announced a ticker symbol change from PSTG to P.",
                   asserted_phase="ANNOUNCED",day="2026-03-25",date_text="2026-03-25",date_role="ANNOUNCEMENT")
        report=self.review();action=report["action_claims"][0]
        self.assertEqual("2026-03-25",action["announced_on"])
        self.assertIsNone(action["effective_on"])
        self.assertIsNone(report["effective_on"])

    def test_legal_name_action_is_not_a_symbol_change(self):
        replace_body(self.captures,"old_cover",self.captures["old_cover"][1].replace(b"Everpure, Inc.",b"Pure Storage, Inc."))
        one_action(self.request,self.captures,"On 2026-02-23, Pure Storage, Inc. changed its corporate name to Everpure, Inc.",
                   event_kind="LEGAL_NAME_CHANGE",from_name="Pure Storage, Inc.",to_name="Everpure, Inc.",
                   issuer_name_text="Pure Storage, Inc.",issuer_reference_text="Pure Storage, Inc.",
                   day="2026-02-23",date_text="2026-02-23")
        report=self.review()
        self.assertTrue(report["action_claims"][0]["effective_action_supported"])
        self.assertIsNone(report["effective_on"])
        self.assertIn("EXACT_SYMBOL_EFFECTIVE_DAY_UNKNOWN",report["blockers"])

    def test_brand_name_and_continued_trading_do_not_prove_a_legal_or_symbol_action(self):
        cases=[("On 2026-04-01, Pure Storage transitioned its corporate brand to Everpure.",
                {"event_kind":"LEGAL_NAME_CHANGE","from_name":"Pure Storage","to_name":"Everpure"}),
               ('On 2026-04-01, Everpure, Inc. will continue to trade under the ticker symbol "PSTG".',{})]
        for text,fields in cases:
            with self.subTest(text=text):
                one_action(self.request,self.captures,text,**fields)
                report=self.review()
                self.assertFalse(report["action_claims"][0]["effective_action_supported"])
                self.assertIsNone(report["effective_on"])

    def test_cover_bracket_and_equal_cusips_cannot_supply_a_missing_action_day(self):
        self.request["action_claims"]=[]
        report=self.review()
        self.assertTrue(all(row["supported"] for row in report["instrument_claims"]))
        self.assertIsNone(report["effective_on"])
        self.assertIn("EXACT_SYMBOL_EFFECTIVE_DAY_UNKNOWN",report["blockers"])
        self.assertIn("DATED_EXACT_CLASS_CUSIP_ACTION_CONTINUITY_MISSING",report["blockers"])

    def test_a_month_cannot_be_expanded_into_an_exact_day(self):
        one_action(self.request,self.captures,"In April 2026, Everpure, Inc. changed its ticker symbol from PSTG to P.",day=None,date_text=None,date_role="UNKNOWN")
        self.assertIn("EXACT_EVENT_DAY_UNKNOWN",self.review()["blockers"])
        self.request["action_claims"][0].update(day="2026-04-01",date_text="April 2026",date_role="EFFECTIVE")
        with self.assertRaisesRegex(ValueError,"complete exact source day"):
            self.review()

    def test_different_day_and_unrelated_meeting_date_cannot_be_borrowed(self):
        self.request["action_claims"]=self.request["action_claims"][:1]
        self.request["action_claims"][0].update(day="2026-04-02",date_text="2026-04-02")
        self.assertIn("EXACT_EVENT_DATE_ABSENT_FROM_PASSAGE",self.review()["blockers"])
        one_action(self.request,self.captures,"A meeting occurred on 2026-04-01. Everpure, Inc. changed its ticker symbol from PSTG to P later.",day="2026-04-01",date_text="2026-04-01")
        self.assertIn("DATE_NOT_EXPLICITLY_BOUND_TO_EFFECTIVE_ACTION",self.review()["blockers"])

    def test_an_on_date_prefix_for_another_event_is_not_the_symbol_action_date(self):
        one_action(self.request,self.captures,"On 2026-04-01, the board met. Everpure, Inc. changed its ticker symbol from PSTG to P later.")
        report=self.review()
        self.assertIn("DATE_NOT_EXPLICITLY_BOUND_TO_EFFECTIVE_ACTION",report["blockers"])
        self.assertIsNone(report["effective_on"])

    def test_reverse_symbols_and_symbol_prefixes_are_not_the_requested_direction(self):
        for changed in ("P to PSTG","PSTG to P2"):
            with self.subTest(changed=changed):
                one_action(self.request,self.captures,f"On 2026-04-01, Everpure, Inc. changed its ticker symbol from {changed}.")
                self.assertIn("DIRECTED_EXACT_OLD_NEW_ACTION_BINDING_MISSING",self.review()["blockers"])

    def test_reported_completion_cannot_follow_its_disclosure(self):
        one_action(self.request,self.captures,"On 2026-10-20, Everpure, Inc. changed its ticker symbol from PSTG to P.",day="2026-10-20",date_text="2026-10-20")
        self.assertIn("REPORTED_EVENT_DAY_AFTER_DISCLOSURE",self.review()["blockers"])

    def test_competing_effective_days_are_preserved_without_picking_the_latest(self):
        replace_body(self.captures,"second",self.captures["second"][1].replace(b"2026-04-01",b"2026-04-02"))
        self.request["action_claims"][1].update(day="2026-04-02",date_text="2026-04-02",selection=symbol_action_passages(self.captures["second"][1])[0])
        report=self.review()
        self.assertEqual(["2026-04-01","2026-04-02"],report["supported_effective_symbol_days"])
        self.assertIsNone(report["effective_on"])
        self.assertIn("COMPETING_SYMBOL_EFFECTIVE_DAYS",report["blockers"])

    def test_two_claims_from_one_accession_are_one_independent_disclosure(self):
        self.request["action_claims"]=[self.request["action_claims"][0],deepcopy(self.request["action_claims"][0]),self.request["action_claims"][2]]
        report=self.review()
        self.assertEqual(1,report["independent_effective_disclosures"])
        self.assertIn("TWO_INDEPENDENT_EFFECTIVE_SYMBOL_DISCLOSURES_MISSING",report["blockers"])

    def test_generic_class_or_a_different_par_value_cannot_supply_the_exact_instrument(self):
        for title in ("Common Stock","Class A Common Stock, par value $0.01 per share"):
            with self.subTest(title=title):
                request,captures=fixture()
                replace_body(captures,"new_instrument",captures["new_instrument"][1].replace(CLASS.encode(),title.encode()))
                report=review_symbol_action_scope(request,captures)
                self.assertIn("EXACT_INSTRUMENT_CLASS_OR_PAR_TERM_MISSING",report["blockers"])
                self.assertFalse(report["instrument_claims"][1]["supported"])

    def test_a_correct_name_and_cik_cannot_bridge_different_instrument_event_days(self):
        replace_body(self.captures,"new_instrument",self.captures["new_instrument"][1].replace(b"04/01/2026",b"04/02/2026"))
        self.assertIn("SAME_DAY_NAME_CLASS_SYMBOL_CUSIP_BRIDGE_MISSING",self.review()["blockers"])

    def test_different_cusip_is_preserved_as_a_structured_conflict(self):
        replace_body(self.captures,"new_instrument",self.captures["new_instrument"][1].replace(b"74624M102",b"037833100"))
        self.assertIn("EXACT_STRUCTURED_CIK_CUSIP_CONFLICT",self.review()["blockers"])

    def test_context_name_is_not_moved_to_another_issuer_or_inferred_as_a_legal_effective_day(self):
        replace_body(self.captures,"new_cover",self.captures["new_cover"][1].replace(CIK.encode(),b"0000029915"))
        self.assertIn("EXACT_COVER_CIK_NAME_SYMBOL_CLASS_CONTEXT_MISSING",self.review()["blockers"])
        self.request,self.captures=fixture()
        self.request["cover_claims"][1]["role"]="NAME_ONLY"
        report=self.review()
        self.assertFalse(report["dated_issuer_name_snapshots"][0]["context_anchor_proves_legal_name_effective_day"])
        self.assertFalse(report["dated_issuer_name_snapshots"][0]["supplies_instrument_symbol_identity"])
        self.assertIn("TO_EXACT_DATED_INSTRUMENT_SYMBOL_TUPLE_MISSING",report["blockers"])

    def test_equal_old_and_new_cusips_still_need_a_dated_cusip_action_statement(self):
        self.request["action_claims"]=self.request["action_claims"][:2]
        self.assertIn("DATED_EXACT_CLASS_CUSIP_ACTION_CONTINUITY_MISSING",self.review()["blockers"])

    def test_a_counterparty_action_cannot_be_assigned_to_the_filing_issuer(self):
        one_action(self.request,self.captures,"On 2026-04-01, Different Company changed its ticker symbol from PSTG to P. Everpure, Inc. described this competitor.")
        report=self.review()
        self.assertIn("DIRECTED_ACTION_SUBJECT_NOT_BOUND_TO_VERB",report["blockers"])
        self.assertIsNone(report["effective_on"])
        one_action(self.request,self.captures,"On 2026-04-01, DifferentEverpure, Inc. changed its ticker symbol from PSTG to P.")
        self.assertIn("DIRECTED_ACTION_SUBJECT_NOT_BOUND_TO_VERB",self.review()["blockers"])

    def test_a_brand_short_name_needs_its_local_legal_name_definition(self):
        one_action(self.request,self.captures,"On 2026-04-01, Everpure changed its ticker symbol from PSTG to P.",issuer_reference_text="Everpure")
        self.assertIn("ACTION_SUBJECT_LOCAL_REFERENCE_UNPROVEN",self.review()["blockers"])
        one_action(self.request,self.captures,'On 2026-04-01, Everpure, Inc. ("Everpure") changed its ticker symbol from PSTG to P.',issuer_reference_text="Everpure")
        self.assertTrue(self.review()["action_claims"][0]["effective_action_supported"])

    def test_the_scope_cannot_be_weakened_to_a_generic_or_unpriced_class(self):
        for title in ("Common Stock", "Class A Common Stock", "Common Stock, par value $0.0001 per share"):
            with self.subTest(title=title):
                self.request["scope"]["class_title"]=title
                with self.assertRaisesRegex(ValueError,"Exact separate"):
                    self.review()

    def test_different_legal_names_need_the_exact_directed_issuer_name_action(self):
        for key in ("old_cover","old_instrument"):
            replace_body(self.captures,key,self.captures[key][1].replace(b"Everpure, Inc.",b"Pure Storage, Inc."))
        self.request["cover_claims"][0]["issuer_name"]="Pure Storage, Inc."
        for claim in self.request["action_claims"]:claim["issuer_source_key"]="new_cover"
        self.assertIn("INDEPENDENT_EXACT_LEGAL_NAME_CHANGE_BRIDGE_MISSING",self.review()["blockers"])
        name_text="On 2026-04-01, Pure Storage, Inc. changed its corporate name to Everpure, Inc."
        replace_body(self.captures,"first",self.captures["first"][1].replace(b"</body>",f"<p>{name_text}</p></body>".encode()))
        self.request["action_claims"].append({**self.request["action_claims"][0],"event_kind":"LEGAL_NAME_CHANGE",
            "issuer_source_key":"old_cover","issuer_name_text":"Pure Storage, Inc.","issuer_reference_text":"Pure Storage, Inc.",
            "from_name":"Pure Storage, Inc.","to_name":"Everpure, Inc.","selection":symbol_action_passages(self.captures["first"][1])[-1]})
        report=self.review()
        self.assertNotIn("INDEPENDENT_EXACT_LEGAL_NAME_CHANGE_BRIDGE_MISSING",report["blockers"])
        self.assertEqual("PRIMARY_SCOPE_READY_FOR_EXPLICIT_REVIEW",report["status"])

    def test_instrument_from_and_to_days_cannot_be_swapped_around_the_action(self):
        replace_body(self.captures,"old_cover",self.captures["old_cover"][1].replace(b"2026-03-31",b"2026-04-02"))
        replace_body(self.captures,"old_instrument",self.captures["old_instrument"][1].replace(b"03/31/2026",b"04/02/2026"))
        self.assertIn("FROM_INSTRUMENT_NOT_STRICTLY_PRIOR_TO_SYMBOL_ACTION",self.review()["blockers"])

    def test_future_source_or_reversed_knowledge_clocks_fail_before_parsing(self):
        for changes in ({"observed_at":(NOW+timedelta(seconds=1)).isoformat()},
                        {"known_at":(OBSERVED-timedelta(seconds=1)).isoformat()},
                        {"started_at":(OBSERVED+timedelta(seconds=1)).isoformat()}):
            with self.subTest(changes=changes):
                request,captures=fixture();captures["first"][0].update(changes)
                with patch.object(module,"symbol_action_passages",side_effect=AssertionError("Must reject before parse")),self.assertRaisesRegex(ValueError,"clocks"):
                    review_symbol_action_scope(request,captures)

    def test_default_review_knowledge_is_physical_and_explicit_past_cannot_backdate_it(self):
        report=self.review()
        self.assertEqual(NOW.isoformat(),report["knowledge_at"])
        self.assertEqual(OBSERVED.isoformat(),report["source_knowledge_at"])
        with self.assertRaisesRegex(ValueError,"AFTER_PHYSICAL_KNOWLEDGE_CUTOFF"):
            self.review(as_of=NOW-timedelta(microseconds=1))
        with self.assertRaisesRegex(ValueError,"future physical"):
            self.review(as_of=NOW+timedelta(microseconds=1))
        self.assertEqual(NOW.isoformat(),self.review(as_of=NOW)["as_of"])

    def test_withheld_body_stays_unavailable_and_any_restored_substitute_is_refused(self):
        self.captures["first"][0]["status"]="WITHHELD_SENSITIVE_BODY"
        body=self.captures["first"][1]
        self.captures["first"]=(self.captures["first"][0],None)
        self.request["action_claims"]=self.request["action_claims"][:1]
        with patch.object(module,"symbol_action_passages",side_effect=AssertionError("No unavailable-body parser")):
            report=self.review()
        self.assertIn("ACTION_OR_PUBLICATION_ORIGINAL_UNAVAILABLE",report["blockers"])
        self.assertFalse(report["capture_pins"]["first"]["body_available"])
        self.captures["first"]=(self.captures["first"][0],body)
        with self.assertRaisesRegex(ValueError,"cannot be restored"):
            self.review()

    def test_access_bearing_whole_body_is_rejected_instead_of_projected(self):
        replace_body(self.captures,"first",html('<p>access_token="synthetic-fixture-value"</p><p>Safe action</p>'))
        with self.assertRaisesRegex(ValueError,"Whole access-bearing"):
            self.review()

    def test_whole_hash_text_and_locator_must_all_match_the_original_paragraph(self):
        for changes in ({"sha256":"0"*64},{"text":"Replacement text"},{"byte_start":1},{"locator":"p:999"}):
            with self.subTest(changes=changes):
                request,captures=fixture();request["action_claims"][0]["selection"].update(changes)
                with self.assertRaisesRegex(ValueError,"Paragraph locator"):
                    review_symbol_action_scope(request,captures)

    def test_hidden_ancestor_cannot_supply_action_proof(self):
        body=html('<div hidden><p>On 2026-04-01, Everpure, Inc. changed its ticker symbol from PSTG to P.</p></div>')
        self.assertEqual([],symbol_action_passages(body))
        replace_body(self.captures,"first",body)
        start=body.index(b'<p>');end=body.index(b'</p>')+4
        self.request["action_claims"]=self.request["action_claims"][:1]
        self.request["action_claims"][0]["selection"]={"locator":"p:1","byte_start":start,"byte_end":end,"sha256":hashlib.sha256(body[start:end]).hexdigest(),"text":"On 2026-04-01, Everpure, Inc. changed its ticker symbol from PSTG to P."}
        with self.assertRaisesRegex(ValueError,"Paragraph locator"):
            self.review()

    def test_a_cropped_future_qualifier_is_not_the_whole_original_paragraph(self):
        body=html('<p>Everpure, Inc. will change its ticker symbol from PSTG to P effective 2026-04-01.</p>')
        replace_body(self.captures,"first",body)
        selection=symbol_action_passages(body)[0]
        cropped=deepcopy(selection);cropped["byte_start"]=body.index(b"change")
        cropped["sha256"]=hashlib.sha256(body[cropped["byte_start"]:cropped["byte_end"]]).hexdigest()
        cropped["text"]="change its ticker symbol from PSTG to P effective 2026-04-01."
        self.request["action_claims"]=self.request["action_claims"][:1]
        self.request["action_claims"][0]["selection"]=cropped
        with self.assertRaisesRegex(ValueError,"Paragraph locator"):
            self.review()

    def test_capture_bytes_transform_flags_redirects_and_wrong_scopes_are_refused(self):
        changes=({"body_transformed":True},{"http_status":403},{"final_url":"https://www.sec.gov/elsewhere"},
                 {"requested_url":"https://example.test/filing.htm","final_url":"https://example.test/filing.htm"},
                 {"bytes":5},{"raw_sha256":"0"*64})
        for fields in changes:
            with self.subTest(fields=fields):
                request,captures=fixture();captures["first"][0].update(fields)
                with self.assertRaises(ValueError):review_symbol_action_scope(request,captures)

    def test_submissions_row_path_form_and_publication_clock_are_exact(self):
        for key,value in (("primaryDocument","different.htm"),("form","10-K"),("acceptanceDateTime","2027-01-01T00:00:00Z")):
            with self.subTest(key=key):
                request,captures=fixture();data=json.loads(captures["submissions"][1]);data["filings"]["recent"][key][0]=value
                replace_body(captures,"submissions",json.dumps(data).encode())
                with self.assertRaises(ValueError):review_symbol_action_scope(request,captures)

    def test_capture_and_request_bounds_are_enforced_without_discovery(self):
        request,captures=fixture()
        for i in range(10):captures["extra"+str(i)]=captures["first"]
        with self.assertRaisesRegex(ValueError,"sixteen"):
            review_symbol_action_scope(request,captures)
        self.request["action_claims"]*=9
        with self.assertRaisesRegex(ValueError,"claim budget"):
            self.review()
        request,captures=fixture();metadata,body=captures["first"];metadata["bytes"]=4_000_001
        with self.assertRaisesRegex(ValueError,"bounded original"):
            review_symbol_action_scope(request,captures)

    def test_actual_six_primary_responses_keep_missing_day_class_and_118_byte_conflict(self):
        from evidence.batch8_identity_20261015.replay_symbol_scope import build_inputs
        request,captures,plan=build_inputs()
        physical=datetime(2026,10,15,23,tzinfo=timezone.utc)
        with patch.object(module,"_physical_clock",return_value=physical):
            report=review_symbol_action_scope(request,captures)
        self.assertEqual(6,len(captures))
        self.assertNotIn("as_of",plan)
        self.assertEqual("PRIMARY_SCOPE_PARTIAL",report["status"])
        self.assertIsNone(report["effective_on"])
        self.assertEqual(0,report["independent_effective_disclosures"])
        release=report["filings"]["release"]
        self.assertEqual(319527,release["index_declared_bytes"])
        self.assertEqual(319645,captures["release"][0]["bytes"])
        self.assertTrue(release["publisher_size_conflict"])
        self.assertIsNone(release["publication_at"])
        self.assertEqual("2026-05-27 16:06:45",release["acceptance_unqualified_raw"])
        self.assertEqual("PSTG",report["dated_cover_tuples"][0]["ticker"])
        self.assertEqual("2026-02-01",report["dated_cover_tuples"][0]["anchor_date"])
        self.assertFalse(report["dated_cover_tuples"][0]["context_anchor_proves_legal_name_effective_day"])
        fact=report["instrument_claims"][0]
        self.assertEqual((CIK,"74624M102","Common Stock","2026-03-31"),tuple(fact[key] for key in ("issuer_cik","cusip","class_title","event_date")))
        self.assertFalse(fact["supported"])
        for reason in ("EXACT_EVENT_DAY_UNKNOWN","DIRECTED_EXACT_OLD_NEW_ACTION_BINDING_MISSING","RAW_XML_RENDERER_PATH_DIFFERS_NO_ORIGINAL_INDEX","EXACT_INSTRUMENT_CLASS_OR_PAR_TERM_MISSING","INDEX_DECLARED_SERVED_BYTE_CONFLICT"):
            self.assertIn(reason,report["blockers"])
        self.assertEqual("2026-10-15T13:40:58.490517+00:00",report["source_knowledge_at"])
        self.assertEqual(0,report["new_verified_identities"])
        self.assertEqual(0,report["current_q3_verified_coverage"])
