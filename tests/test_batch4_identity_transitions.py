"""Fail-closed instrument transition checks and actual archived-source replay."""
from copy import deepcopy
import unittest

from market_checker_app.services.sec13f_identity_bulk import SourceSnapshot, _action_text, parse_schedule13
from market_checker_app.services.sec13f_transition_review import instrument_transition_review
from tests.test_sec13f_identity_bulk import OBSERVED, PUBLISHED, builder, catalog, cover, instrument, reviewed, securities
from tests.test_sec13f_identity_transitions import ACTION_URL, action_fixture, previous_version, version


def deleted(source, cusip="037833100"):
    lines = source.payload.decode().splitlines()
    payload = "\n".join(line[:67] + "*D*" + line[70:] if line.startswith(cusip) else line
                        for line in lines) + "\n"
    return SourceSnapshot(source.kind, source.url, source.observed_at, payload.encode())


class Batch4IdentityTransitionTests(unittest.TestCase):
    def test_current_schedule13_single_plural_container_matches_exact_legacy_fact(self):
        original = instrument()
        raw = original.payload.replace(b"<issuerCusip>", b"<issuerCusips><issuerCusipNumber>").replace(
            b"</issuerCusip>", b"</issuerCusipNumber></issuerCusips>")
        grouped = SourceSnapshot(original.kind, original.url, OBSERVED, raw, PUBLISHED)
        actual, expected = parse_schedule13(grouped), parse_schedule13(original)
        actual.pop("snapshot_id")
        expected.pop("snapshot_id")
        self.assertEqual(actual, expected)
        self.assertEqual(grouped.payload, raw)
        for modified in (raw.replace(b"</issuerCusips>", b"<issuerCusipNumber>02079K107</issuerCusipNumber></issuerCusips>"),
                         raw.replace(b"</issuerCusips>", b"</issuerCusips><issuerCusip>037833100</issuerCusip>"),
                         raw.replace(b"<issuerCusipNumber>", b'<issuerCusipNumber xmlns="urn:other">')):
            with self.subTest(modified=modified[-50:]), self.assertRaises(ValueError):
                parse_schedule13(SourceSnapshot(original.kind, original.url, OBSERVED, modified, PUBLISHED))

    def test_same_cusip_symbol_rename_cannot_bypass_primary_review(self):
        sources = [catalog(), securities(), instrument(), cover()]
        row = reviewed(sources)
        prior = previous_version(row, ticker="OLD")
        service = builder(sources, existing=[prior])
        with self.assertRaisesRegex(ValueError, "primary identity-transition"):
            service.validate_reviewed([row])
        self.assertEqual(service.existing, [prior])

    def test_same_cusip_rename_preserves_past_identity_and_needs_exact_ticker_quotes(self):
        sources = [catalog(), securities(), instrument(), cover()]
        row = reviewed(sources)
        prior = previous_version(row, ticker="OLD")
        action, claim = action_fixture(prior, row, old_title="Common Stock", new_title="Common Stock")
        row["review"]["identity_transitions"] = [claim]
        before = deepcopy(prior)
        service = builder([*sources, action], existing=[prior])
        self.assertEqual(service.validate_reviewed([row]), [version(row)])
        self.assertEqual(service.existing, [before])
        claim["from_identity"]["ticker_text"] = "AAPL"
        with self.assertRaisesRegex(ValueError, "exact old/new"):
            service.validate_reviewed([row])

    def test_different_share_cusips_under_one_cik_do_not_invent_a_symbol_transition(self):
        sources = [catalog(), securities(), instrument(), cover()]
        row = reviewed(sources)
        separate = previous_version(row, ticker="OTHER", cusip="02079K107")
        self.assertEqual(builder(sources, existing=[separate]).validate_reviewed([row]), [version(row)])

    def test_symbol_rename_requires_review_even_if_unknown_dates_leave_a_gap(self):
        sources = [catalog(), securities(), instrument(), cover()]
        row = reviewed(sources)
        prior = previous_version(row, ticker="OLD", effective_to="2026-02-28")
        with self.assertRaisesRegex(ValueError, "primary identity-transition"):
            builder(sources, existing=[prior]).validate_reviewed([row])
        self.assertEqual(prior["effective_to"], "2026-02-28")

    def test_current_deleted_cusip_cannot_be_already_reviewed_or_exported(self):
        sources = [catalog(), deleted(securities()), instrument(), cover()]
        row = reviewed(sources)
        service = builder(sources, existing=[version(row)])
        self.assertNotEqual(service.candidate("AAPL")["status"], "ALREADY_REVIEWED")
        report = instrument_transition_review(service, tickers=["AAPL"])
        record = report["records"][0]
        self.assertIn("DELETED_BASELINE_CUSIP", record["blockers"])
        self.assertEqual(record["historical_versions"][0]["selected_list_status"], "DELETED_IN_SELECTED_LIST")
        with self.assertRaisesRegex(ValueError, "active exact SEC list row"):
            builder(sources).validate_reviewed([row])

    def test_current_list_class_change_cannot_renew_an_old_review(self):
        existing = version(reviewed([catalog(), securities(), instrument(), cover()]))
        different = securities([("037833100", "APPLE INC", "CL A")])
        service = builder([catalog(), different], existing=[existing])
        self.assertNotEqual(service.candidate("AAPL")["status"], "ALREADY_REVIEWED")
        record = instrument_transition_review(service, tickers=["AAPL"])["records"][0]
        self.assertEqual(record["historical_versions"][0]["selected_list_status"], "SELECTED_LIST_CLASS_DIFFERS")

    def test_unresolved_catalog_is_not_filled_from_an_old_ticker_context_or_same_cik_symbol(self):
        source = catalog([{"ticker": "NEW", "cik_str": 320193, "title": "APPLE INC"}])
        service = builder([source, securities(), instrument(), cover()])
        before = deepcopy(service.catalog)
        report = instrument_transition_review(service, tickers=["AAPL"])
        record = report["records"][0]
        self.assertEqual(record["preparation_status"], "WAIT_TICKER_CIK")
        self.assertEqual(record["observed_current_catalog_matches"], [])
        self.assertEqual(record["same_cik_other_catalog_symbols"][0]["ticker"], "NEW")
        self.assertIn("UNRESOLVED_CURRENT_TICKER_CIK", record["blockers"])
        self.assertFalse(record["automatic_symbol_alias_allowed"])
        self.assertFalse(record["new_identity_approved"])
        self.assertEqual(service.catalog, before)
        self.assertEqual(report, instrument_transition_review(service, tickers=["AAPL"]))
        record["same_ticker_dated_filing_contexts"][0]["ticker"] = "MUTATED"
        self.assertEqual(service.covers[0]["ticker"], "AAPL")

    def test_action_statement_does_not_approve_an_instrument_or_extend_its_interval(self):
        sources = [catalog(), securities(), instrument(), cover()]
        row = reviewed(sources)
        prior = previous_version(row, issuer_cik="0000123456")
        action, claim = action_fixture(prior, row)
        service = builder([*sources, action], existing=[prior])
        statements = [{"ticker": "AAPL", "source_snapshot_id": action.snapshot_id,
                       "excerpt": claim["action_excerpt"], "effective_on": "2026-04-01",
                       "effective_date_text": "2026-04-01"}]
        report = instrument_transition_review(service, tickers=["AAPL"], primary_statements=statements)
        record = report["records"][0]
        self.assertIn("CATALOG_CIK_CHANGED_REQUIRES_PRIMARY_TRANSITION", record["blockers"])
        self.assertFalse(record["primary_statements"][0]["identity_transition_approved"])
        self.assertEqual(record["historical_versions"][0]["identity"], prior)
        self.assertFalse(record["historical_versions"][0]["covers_selected_quarter"])
        self.assertEqual(record["same_ticker_dated_filing_contexts"][0]["source_observed_at"], OBSERVED.isoformat())
        self.assertEqual(record["same_ticker_dated_filing_contexts"][0]["source_published_at"], PUBLISHED.isoformat())
        self.assertFalse(record["same_ticker_dated_filing_contexts"][0]["interval_extension_allowed"])
        statements[0]["effective_on"] = "2026-04-02"
        with self.assertRaisesRegex(ValueError, "effective date"):
            instrument_transition_review(service, tickers=["AAPL"], primary_statements=statements)

    def test_hidden_or_unarchived_statements_are_rejected(self):
        sources = [catalog(), securities(), instrument(), cover()]
        row = reviewed(sources)
        prior = previous_version(row)
        action, claim = action_fixture(prior, row, hidden=True)
        service = builder([*sources, action])
        statements = [{"ticker": "AAPL", "source_snapshot_id": action.snapshot_id, "excerpt": claim["action_excerpt"]}]
        with self.assertRaisesRegex(ValueError, "visible source"):
            instrument_transition_review(service, tickers=["AAPL"], primary_statements=statements)
        statements[0]["source_snapshot_id"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "unarchived"):
            instrument_transition_review(service, tickers=["AAPL"], primary_statements=statements)

    def test_visible_statement_by_an_unrelated_issuer_cannot_be_attached_to_the_ticker(self):
        sources = [catalog(), securities(), instrument(), cover()]
        row = reviewed(sources)
        action, claim = action_fixture(previous_version(row), row,
                                      archive_url=ACTION_URL.replace("/data/320193/", "/data/999999/"))
        service = builder([*sources, action])
        with self.assertRaisesRegex(ValueError, "unrelated issuer CIK"):
            instrument_transition_review(service, tickers=["AAPL"], primary_statements=[{
                "ticker": "AAPL", "source_snapshot_id": action.snapshot_id, "excerpt": claim["action_excerpt"],
            }])

    def test_review_scope_is_bounded_canonical_and_cannot_duplicate_a_ticker(self):
        for tickers in ([], ["AAPL", "AAPL"], ["UNLISTED"], ["AAPL"] * 26):
            with self.subTest(tickers=tickers), self.assertRaisesRegex(ValueError, "canonical ticker selection"):
                instrument_transition_review(builder(), tickers=tickers)

    def test_a_different_registered_class_does_not_clear_the_equity_context_blocker(self):
        service = builder([catalog(), securities(), instrument(), cover(title="Preferred Stock")])
        record = instrument_transition_review(service, tickers=["AAPL"])["records"][0]
        self.assertEqual(record["same_ticker_dated_filing_contexts"][0]["class_title"], "Preferred Stock")
        self.assertIn("NO_SAME_CONTEXT_TICKER_CIK_CLASS_IN_SELECTED_QUARTER", record["blockers"])

    def test_edgar_single_document_envelope_preserves_visible_source_and_raw_bytes(self):
        passage = "Effective 2026-04-01, the common shares changed from OLD to AAPL."
        raw = ("<DOCUMENT>\n<TYPE>8-K12B\n<SEQUENCE>1\n<FILENAME>synthetic-action.htm\n"
               "<DESCRIPTION>Fixture only\n<TEXT>\n<HTML><HEAD><TITLE>Hidden title</TITLE></HEAD>"
               f"<BODY><P>{passage}</P><DIV style='display:none'>Hidden body</DIV></BODY></HTML>"
               "\n</TEXT>\n</DOCUMENT>\n").encode()
        snapshot = SourceSnapshot("corporate_action_filing", ACTION_URL, OBSERVED, raw, PUBLISHED)
        before = snapshot.sha256
        self.assertEqual(_action_text(snapshot), passage)
        self.assertEqual(snapshot.sha256, before)
        self.assertEqual(snapshot.payload, raw)
        for modified in (raw.replace(b"synthetic-action.htm", b"other.htm"),
                         raw.replace(b"</TEXT>", b""), raw + raw):
            with self.subTest(modified=modified[-20:]), self.assertRaises(ValueError):
                _action_text(SourceSnapshot(snapshot.kind, snapshot.url, OBSERVED, modified, PUBLISHED))

    def test_real_q3_transition_review_replays_exact_captures_without_registry_promotion(self):
        from evidence.batch4_identity_20261013.replay_review import build_review
        report, diagnostics = build_review()
        self.assertEqual(report["canonical_universe_size"], 687)
        self.assertEqual(report["selected_tickers"], ["XOM", "LEG", "PSTG"])
        records = {row["ticker"]: row for row in report["records"]}
        self.assertIn("CATALOG_CIK_CHANGED_REQUIRES_PRIMARY_TRANSITION", records["XOM"]["blockers"])
        self.assertIn("DELETED_BASELINE_CUSIP", records["XOM"]["blockers"])
        self.assertEqual(records["XOM"]["historical_versions"][0]["identity"]["effective_to"], "2026-06-30")
        self.assertEqual(records["LEG"]["preparation_status"], "WAIT_TICKER_CIK")
        self.assertEqual(records["PSTG"]["preparation_status"], "WAIT_TICKER_CIK")
        self.assertEqual(records["PSTG"]["same_cik_other_catalog_symbols"][0]["ticker"], "P")
        self.assertTrue(any(row["ticker"] == "P" and row["anchor_date"] == "2026-08-02"
                            for row in records["PSTG"]["same_cik_other_dated_filing_contexts"]))
        self.assertEqual(report["new_verified_identities"], 0)
        self.assertFalse(report["production_registry_changed"])
        self.assertGreaterEqual(diagnostics["raw_capture_count"], 7)
        self.assertTrue(diagnostics["original_bytes_verified"])
        self.assertTrue(any(row["status"] == "NO_COMPLETE_VISIBLE_ACTION_PROOF"
                            and row.get("original_bytes_not_repaired") is True
                            for row in diagnostics["filing_parse_results"]))
        self.assertEqual(report, build_review()[0])


if __name__ == "__main__":
    unittest.main()
