from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from market_checker_app.collectors.sec_edgar_client import SecAccessBlockedError, SecEdgarClient, SecEdgarError
from market_checker_app.sec13f_identity_bulk import capture_source, export_reviewed, main, run_preparation
from market_checker_app.services.sec13f_identity_bulk import (
    EXCHANGE_URL, PRODUCTION_DATA_DIRECTORY, SourceSnapshot, Sec13fIdentityBulk, TICKER_URL, atomic_json, list_quarter, list_url,
    load_snapshots, parse_13f_list, parse_schedule13, parse_ticker_catalog,
    parse_ticker_filing, save_snapshot, share_class,
)
from market_checker_app.utils.ticker_universe import load_canonical_tickers


NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
OBSERVED = NOW - timedelta(hours=2)
PUBLISHED = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
INSTRUMENT_URL = "https://www.sec.gov/Archives/edgar/data/102909/000010290926000630/primary_doc.xml"
COVER_URL = "https://www.sec.gov/Archives/edgar/data/320193/000032019326000045/aapl-20260630.htm"


def catalog(rows=None):
    rows = rows or [
        {"ticker": "AAPL", "cik_str": 320193, "title": "APPLE INC"},
        {"ticker": "GOOG", "cik_str": 1652044, "title": "ALPHABET INC"},
        {"ticker": "GOOGL", "cik_str": 1652044, "title": "ALPHABET INC"},
    ]
    return SourceSnapshot("ticker_catalog", TICKER_URL, OBSERVED,
                          json.dumps(dict(enumerate(rows))).encode())


def securities(rows=None):
    rows = rows or [("037833100", "APPLE INC", "COM"),
                    ("02079K107", "ALPHABET INC", "CAP STK CL C"),
                    ("02079K305", "ALPHABET INC", "CAP STK CL A")]
    content = "\n".join(f"{cusip}*{name:<30}{title:<27}{'':12}E" for cusip, name, title in rows) + "\n"
    return SourceSnapshot("13f_list", list_url("2026q2"), OBSERVED, content.encode())


def instrument(*, cik="0000320193", cusip="037833100", title="Common Stock",
               event="06/30/2026", xml_extra="", url=INSTRUMENT_URL):
    payload = f'''<edgarSubmission xmlns="http://www.sec.gov/edgar/schedule13g">
      <headerData><submissionType>SCHEDULE 13G</submissionType></headerData>
      <formData><coverPageHeader><securitiesClassTitle>{title}</securitiesClassTitle>
      <eventDateRequiresFilingThisStatement>{event}</eventDateRequiresFilingThisStatement>
      <issuerInfo><issuerCik>{cik}</issuerCik><issuerName>Apple Inc</issuerName>
      <issuerCusip>{cusip}</issuerCusip>{xml_extra}</issuerInfo>
      </coverPageHeader></formData></edgarSubmission>'''
    return SourceSnapshot("schedule13", url, OBSERVED, payload.encode(), PUBLISHED)


def cover(*, ticker="AAPL", cik="0000320193", title="Common Stock, $0.01 par value per share",
          context_title="Security", extra="", url=COVER_URL):
    payload = f'''<html xmlns:dei="http://xbrl.sec.gov/dei/2026"
      xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">
      <xbrli:context id="Security"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{cik}</xbrli:identifier></xbrli:entity>
      <xbrli:period><xbrli:instant>2026-06-30</xbrli:instant></xbrli:period></xbrli:context>
      <ix:nonNumeric name="dei:TradingSymbol" contextRef="Security">{ticker}</ix:nonNumeric>
      <ix:nonNumeric name="dei:Security12bTitle" contextRef="{context_title}">{title}</ix:nonNumeric>{extra}</html>'''
    return SourceSnapshot("ticker_filing", url, OBSERVED, payload.encode(), PUBLISHED)


def reviewed(sources, *, ticker="AAPL", cik="0000320193", cusip="037833100", name="APPLE INC", title="COM"):
    ids = {source.kind: source for source in sources}
    return {"ticker": ticker, "issuer_cik": cik, "cusip": cusip,
            "issuer_name": name, "class_description": title,
            "effective_from": "2026-04-01", "effective_to": "2026-06-30",
            "known_at": (NOW - timedelta(minutes=10)).isoformat(),
            "cusip_evidence_url": ids["13f_list"].url,
            "instrument_evidence_url": ids["schedule13"].url,
            "ticker_evidence_url": ids["ticker_filing"].url,
            "review": {"approved": True, "reviewer": "fixture-reviewer",
                       "reviewed_at": (NOW - timedelta(minutes=20)).isoformat(),
                       "dated_binding_note": "Synthetic test review of this exact quarter; not live evidence.",
                       "list_snapshot_id": ids["13f_list"].snapshot_id,
                       "instrument_snapshot_id": ids["schedule13"].snapshot_id,
                       "ticker_snapshot_id": ids["ticker_filing"].snapshot_id}}


def builder(sources=None, *, universe=None, existing=None, as_of=NOW):
    return Sec13fIdentityBulk(snapshots=sources or [catalog(), securities(), instrument(), cover()],
                             universe=universe or ["AAPL", "GOOG", "GOOGL"],
                             quarter="2026q2", as_of=as_of, existing=existing)


class Sec13fBulkTests(unittest.TestCase):
    def test_current_sec_q3_endpoint_and_legacy_quarter_urls_keep_exact_citations(self):
        self.assertEqual(list_url("2026q3"), "https://www.sec.gov/files/investment/13flist2026q3.txt")
        self.assertEqual(list_url("2026q2"), "https://www.sec.gov/files/investment/13flist2026q2-txt.txt")
        self.assertEqual(list_quarter(list_url("2026q3")), "2026q3")
        self.assertEqual(list_quarter(list_url("2026q2")), "2026q2")
        for url in ("https://example.test/files/investment/13flist2026q3.txt",
                    "https://www.sec.gov/files/investment/13flist2026q3.txt?x=1",
                    "https://www.sec.gov/files/investment/13flist2026q3-txt.txt/extra"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                list_quarter(url)

    def test_preparation_selects_latest_official_list_variant_without_redownload_or_quarter_mix(self):
        # Synthetic payloads/times exercise the real Q3 URL spelling change.
        # The Q2 source is retained in the same archive, but cannot serve Q3.
        current = SourceSnapshot("13f_list", list_url("2026q3"), OBSERVED, securities().payload)
        older_variant = SourceSnapshot("13f_list", "https://www.sec.gov/files/investment/13flist2026q3-txt.txt",
                                       OBSERVED - timedelta(seconds=1), securities().payload)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for source in [catalog(), securities(), older_variant, current]:
                save_snapshot(root, source)
            registry = root / "existing.json"
            registry.write_text("[]", encoding="utf-8")
            with patch("market_checker_app.sec13f_identity_bulk._configured_client", side_effect=AssertionError("no redownload")):
                result = run_preparation(workspace=root, quarter="2026q3", checkpoint=root / "candidates.json",
                                         existing_path=registry, batch_size=1, one_batch=True,
                                         capture_global=True, as_of=NOW)
            self.assertEqual(result["quarter"], "2026q3")
            self.assertEqual(result["snapshot_ids"], sorted([catalog().snapshot_id, current.snapshot_id]))
            self.assertEqual(result["results"][0]["list_snapshot_id"], current.snapshot_id)

    def test_official_option_rows_with_synthetic_check_digits_never_become_identities(self):
        # Three rows from the official 2026q2 list, with a synthetic fixture
        # timestamp. This is a format regression test, not a live capture.
        payload = (b"B38564108*CMB.TECH NV                   SHS                                    E\n"
                   b"B38564908 CMB.TECH NV                   CALL                                   E\n"
                   b"B38564958 CMB.TECH NV                   PUT                                    E\n")
        source = SourceSnapshot("13f_list", list_url("2026q2"), OBSERVED, payload)
        self.assertEqual(len(parse_13f_list(source)), 3)
        service = builder([catalog([{"ticker": "CMBT", "cik_str": 1443799, "title": "CMB.TECH NV"}]), source],
                          universe=["CMBT"])
        self.assertEqual([row["cusip"] for row in service.candidate("CMBT")["candidate_instruments"]], ["B38564108"])
        invalid_equity = securities([("037833101", "APPLE INC", "COM")])
        with self.assertRaisesRegex(ValueError, "Invalid CUSIP"):
            parse_13f_list(invalid_equity)

    def test_malformed_quarterly_deletion_status_is_not_treated_as_active(self):
        source = securities()
        payload = source.payload.replace(b"COM                        ", b"COM                        *D?")
        invalid = SourceSnapshot("13f_list", source.url, source.observed_at, payload)
        with self.assertRaisesRegex(ValueError, "Malformed"):
            parse_13f_list(invalid)

    def test_exact_names_and_identifiers_stay_candidates_until_explicit_review(self):
        row = builder().candidate("AAPL")
        self.assertEqual(row["status"], "NEEDS_REVIEW")
        self.assertEqual(row["identity_status"], "CANDIDATE_ONLY")
        self.assertFalse(row["scoring_allowed"])
        self.assertEqual(row["candidate_instruments"][0]["match_basis"], "EXACT_CIK_CUSIP_CLASS_CANDIDATE")

    def test_abbreviated_names_are_only_suggestions(self):
        source = catalog([{"ticker": "AAPL", "cik_str": 320193, "title": "APPLE COMPANY INC"}])
        row = builder([source, securities()]).candidate("AAPL")
        self.assertEqual(row["candidate_instruments"][0]["match_basis"], "NAME_SUGGESTION")
        self.assertFalse(row["scoring_allowed"])

    def test_wrong_classes_are_separate_even_for_one_issuer(self):
        rows = builder().candidate("GOOG")["candidate_instruments"]
        self.assertEqual({row["cusip"] for row in rows}, {"02079K107", "02079K305"})
        self.assertNotEqual(share_class("CAP STK CL C"), share_class("Class A Common Stock"))
        self.assertIsNone(share_class("Preferred Stock"))
        self.assertIsNone(share_class("SPONSORED ADS"))

    def test_reused_catalog_ticker_keeps_both_ciks_and_refuses_attribution(self):
        source = catalog([{"ticker": "AAPL", "cik_str": 320193, "title": "APPLE INC"},
                          {"ticker": "AAPL", "cik_str": 123456, "title": "APPLE INC"}])
        parsed = parse_ticker_catalog(source)
        self.assertEqual(len(parsed["AAPL"]), 2)
        row = builder([source, securities()]).candidate("AAPL")
        self.assertEqual(row["status"], "AMBIGUOUS_CATALOG")
        self.assertEqual(row["candidate_instruments"], [])

    def test_identical_catalog_rows_are_deduplicated_without_losing_ambiguity(self):
        rows = [{"ticker": "AAPL", "cik_str": 320193, "title": "APPLE INC"}] * 2
        self.assertEqual(len(parse_ticker_catalog(catalog(rows))["AAPL"]), 1)

    def test_duplicate_cusip_list_fails_closed(self):
        source = securities([("037833100", "APPLE INC", "COM")] * 2)
        with self.assertRaisesRegex(ValueError, "duplicate CUSIP"):
            parse_13f_list(source)

    def test_repeated_official_etf_option_identifiers_preserve_each_line_without_equity_attribution(self):
        # Actual Q3 TXT rows 3009/3013, with synthetic observation time.
        payload = (b"00768Y905 ADVISORSHARES TR              CALL                                   E\n"
                   b"00768Y905 ADVISORSHARES TR              CALL                       *D*         E\n")
        source = SourceSnapshot("13f_list", list_url("2026q3"), OBSERVED, payload)
        rows = parse_13f_list(source)
        self.assertEqual([row["line_number"] for row in rows], [1, 2])
        self.assertEqual([row["flags"] for row in rows], ["E", "*D*         E"])
        company = catalog([{"ticker": "ADVS", "cik_str": 123456, "title": "ADVISORSHARES TR"}])
        service = Sec13fIdentityBulk(snapshots=[company, source], universe=["ADVS"],
                                    quarter="2026q3", as_of=NOW)
        self.assertEqual(service.candidate("ADVS")["candidate_instruments"], [])

    def test_option_duplicate_exception_cannot_hide_equity_or_different_option_class(self):
        for classes in [("COM", "CALL"), ("CALL", "COM"), ("CALL", "PUT")]:
            with self.subTest(classes=classes), self.assertRaisesRegex(ValueError, "duplicate CUSIP"):
                parse_13f_list(securities([("037833100", "APPLE INC", title) for title in classes]))

    def test_catalog_exchange_format_preserves_canonical_punctuation(self):
        source = SourceSnapshot("ticker_catalog", "https://www.sec.gov/files/company_tickers_exchange.json", OBSERVED,
                                json.dumps({"fields": ["cik", "name", "ticker", "exchange"],
                                            "data": [[320193, "APPLE INC", "BRK-B", "NYSE"]]}).encode())
        row = builder([source, securities()], universe=["BRK.B"]).candidate("BRK.B")
        self.assertEqual(row["ticker"], "BRK.B")
        self.assertEqual(row["catalog_matches"][0]["ticker"], "BRK-B")

    def test_reviewed_brkb_alias_preserves_canonical_registry_key(self):
        sources = [catalog([{"ticker": "BRK-B", "cik_str": 1067983, "title": "BERKSHIRE HATHAWAY INC"}]),
                   securities([("084670702", "BERKSHIRE HATHAWAY INC", "CL B")]),
                   instrument(cik="0001067983", cusip="084670702", title="Class B Common Stock"),
                   cover(ticker="BRK-B", cik="0001067983", title="Class B Common Stock",
                         url=COVER_URL.replace("/data/320193/", "/data/1067983/"))]
        service = builder(sources, universe=["BRKB"])
        self.assertEqual(service.candidate("BRKB")["status"], "NEEDS_REVIEW")
        row = reviewed(sources, ticker="BRKB", cik="0001067983", cusip="084670702",
                       name="BERKSHIRE HATHAWAY INC", title="CL B")
        self.assertEqual(service.validate_reviewed([row])[0]["ticker"], "BRKB")
        with self.assertRaisesRegex(ValueError, "conflicting ticker aliases"):
            builder(sources, universe=["BRKB", "BRK.B"])

    def test_future_snapshot_is_not_historical_evidence(self):
        with self.assertRaisesRegex(ValueError, "future knowledge"):
            builder(as_of=OBSERVED - timedelta(seconds=1))

    def test_snapshot_digest_detects_modified_source_bytes(self):
        with TemporaryDirectory() as directory:
            source = securities()
            save_snapshot(Path(directory), source)
            (Path(directory) / "snapshots" / f"{source.sha256}.bin").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "digest"):
                load_snapshots(Path(directory))

    def test_snapshot_identity_and_content_roundtrip(self):
        with TemporaryDirectory() as directory:
            source = securities()
            saved = save_snapshot(Path(directory), source)
            self.assertEqual(saved, load_snapshots(Path(directory))[0].snapshot_id)
            save_snapshot(Path(directory), source)
            self.assertEqual(len(load_snapshots(Path(directory))), 1)

    def test_interrupted_snapshot_publication_can_be_retried_without_partial_source(self):
        with TemporaryDirectory() as directory:
            root, source = Path(directory), securities()
            with patch("market_checker_app.services.sec13f_identity_bulk.os.link", side_effect=OSError("interrupted publication")):
                with self.assertRaises(OSError):
                    save_snapshot(root, source)
            self.assertFalse((root / "snapshots" / f"{source.sha256}.bin").exists())
            self.assertEqual(list((root / "snapshots").iterdir()), [])
            save_snapshot(root, source)
            self.assertEqual(load_snapshots(root)[0].payload, source.payload)

    def test_snapshot_times_are_normalized_before_latest_source_selection(self):
        source = SourceSnapshot("ticker_catalog", TICKER_URL, OBSERVED.astimezone(timezone(timedelta(hours=4))), catalog().payload)
        self.assertEqual(source.observed_at, OBSERVED)
        self.assertEqual(source.observed_at.tzinfo, timezone.utc)

    def test_schedule_wrong_issuer_or_duplicate_cusip_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            parse_schedule13(instrument(xml_extra="<issuerCusip>037833100</issuerCusip>"))
        sources = [catalog(), securities(), instrument(cik="0000123456"), cover()]
        with self.assertRaisesRegex(ValueError, "wrong CIK"):
            builder(sources).validate_reviewed([reviewed(sources)])

    def test_schedule_raw_xml_only_and_entities_rejected(self):
        with self.assertRaises(ValueError):
            instrument(url=INSTRUMENT_URL.replace("primary_doc", "xslSCHEDULE_13G_X02/primary_doc"))
        source = SourceSnapshot("schedule13", INSTRUMENT_URL, OBSERVED,
                                b'<!DOCTYPE edgarSubmission [<!ENTITY a "x">]><edgarSubmission/>', PUBLISHED)
        with self.assertRaisesRegex(ValueError, "DTD"):
            parse_schedule13(source)

    def test_schedule_foreign_child_namespace_and_form_conflicts_are_rejected(self):
        source = instrument()
        for payload in [source.payload.replace(b"<issuerCusip>", b'<issuerCusip xmlns="urn:other">'),
                        source.payload.replace(b"SCHEDULE 13G", b"SCHEDULE 13D")]:
            with self.subTest(payload=payload):
                altered = SourceSnapshot("schedule13", source.url, OBSERVED, payload, PUBLISHED)
                with self.assertRaises(ValueError):
                    parse_schedule13(altered)

    def test_inline_proof_requires_same_context_for_symbol_and_class(self):
        self.assertEqual(parse_ticker_filing(cover(context_title="OtherClass")), [])
        sources = [catalog(), securities(), instrument(), cover(context_title="OtherClass")]
        with self.assertRaisesRegex(ValueError, "same-context"):
            builder(sources).validate_reviewed([reviewed(sources)])

    def test_inline_context_identifier_must_match_issuer_archive_cik(self):
        self.assertEqual(parse_ticker_filing(cover(cik="0000123456")), [])

    def test_duplicate_inline_context_is_rejected(self):
        source = cover(extra='<xbrli:context id="Security"></xbrli:context>')
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            parse_ticker_filing(source)

    def test_multiple_symbols_or_classes_in_one_context_are_ambiguous(self):
        for name, value in [("TradingSymbol", "GOOG"), ("Security12bTitle", "Class A Common Stock")]:
            source = cover(extra=f'<ix:nonNumeric name="dei:{name}" contextRef="Security">{value}</ix:nonNumeric>')
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "Ambiguous"):
                    parse_ticker_filing(source)

    def test_inline_identity_requires_real_namespaces_entity_and_period_structure(self):
        source = cover()
        for payload in [source.payload.replace(b"http://xbrl.sec.gov/dei/2026", b"urn:fake-dei"),
                        source.payload.replace(b"xbrli:entity", b"xbrli:scenario"),
                        source.payload.replace(b"xbrli:period", b"xbrli:scenario")]:
            altered = SourceSnapshot("ticker_filing", source.url, OBSERVED, payload, PUBLISHED)
            with self.subTest(payload=payload):
                try:
                    self.assertEqual(parse_ticker_filing(altered), [])
                except ValueError:
                    pass

    def test_nil_continued_or_excluded_inline_identity_facts_require_separate_review(self):
        source = cover()
        for payload in [source.payload.replace(b'name="dei:TradingSymbol"', b'name="dei:TradingSymbol" xsi:nil="true"'),
                        source.payload.replace(b'name="dei:TradingSymbol"', b'name="dei:TradingSymbol" continuedAt="Other"'),
                        source.payload.replace(b">AAPL</ix:nonNumeric>", b">AAPL<ix:exclude>extra</ix:exclude></ix:nonNumeric>")]:
            altered = SourceSnapshot("ticker_filing", source.url, OBSERVED, payload, PUBLISHED)
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    parse_ticker_filing(altered)

    def test_duration_context_needs_one_consistent_start_and_end(self):
        source = cover()
        payload = source.payload.replace(b"<xbrli:instant>2026-06-30</xbrli:instant>",
                                         b"<xbrli:startDate>2026-04-01</xbrli:startDate><xbrli:endDate>2026-06-30</xbrli:endDate>")
        valid = SourceSnapshot("ticker_filing", source.url, OBSERVED, payload, PUBLISHED)
        self.assertEqual(parse_ticker_filing(valid)[0]["anchor_date"], "2026-06-30")
        invalid = SourceSnapshot("ticker_filing", source.url, OBSERVED, payload.replace(b"2026-04-01", b"2026-07-01"), PUBLISHED)
        with self.assertRaisesRegex(ValueError, "period"):
            parse_ticker_filing(invalid)

    def test_conflicting_same_ticker_classes_across_contexts_cannot_be_selected_away(self):
        extra = '''<xbrli:context id="OtherSecurity"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">320193</xbrli:identifier></xbrli:entity>
          <xbrli:period><xbrli:instant>2026-06-30</xbrli:instant></xbrli:period></xbrli:context>
          <ix:nonNumeric name="dei:TradingSymbol" contextRef="OtherSecurity">AAPL</ix:nonNumeric>
          <ix:nonNumeric name="dei:Security12bTitle" contextRef="OtherSecurity">Class A Common Stock</ix:nonNumeric>'''
        sources = [catalog(), securities(), instrument(), cover(extra=extra)]
        with self.assertRaisesRegex(ValueError, "unique same-context"):
            builder(sources).validate_reviewed([reviewed(sources)])

    def test_explicit_exact_review_exports_production_schema(self):
        sources = [catalog(), securities(), instrument(), cover()]
        result = builder(sources).validate_reviewed([reviewed(sources)])
        self.assertEqual(result[0]["cusip"], "037833100")
        self.assertNotIn("review", result[0])
        self.assertEqual(result[0]["ticker_evidence_url"], COVER_URL)

    def test_unapproved_candidates_cannot_be_imported(self):
        sources = [catalog(), securities(), instrument(), cover()]
        row = reviewed(sources)
        row["review"]["approved"] = False
        with self.assertRaisesRegex(ValueError, "unreviewed"):
            builder(sources).validate_reviewed([row])

    def test_reviewed_catalog_reuse_or_mismatch_fails_closed(self):
        for rows in [[{"ticker": "AAPL", "cik_str": 123456, "title": "APPLE INC"}],
                     [{"ticker": "AAPL", "cik_str": 320193, "title": "APPLE INC"},
                      {"ticker": "AAPL", "cik_str": 123456, "title": "APPLE INC"}]]:
            sources = [catalog(rows), securities(), instrument(), cover()]
            with self.subTest(rows=rows):
                with self.assertRaisesRegex(ValueError, "catalog CIK"):
                    builder(sources).validate_reviewed([reviewed(sources)])

    def test_review_cannot_precede_the_catalog_observation_used_for_export(self):
        newer_catalog = SourceSnapshot("ticker_catalog", TICKER_URL, NOW - timedelta(minutes=5), catalog().payload)
        sources = [newer_catalog, securities(), instrument(), cover()]
        with self.assertRaisesRegex(ValueError, "knowledge"):
            builder(sources).validate_reviewed([reviewed(sources)])

    def test_reviewer_cannot_swap_common_share_class(self):
        sources = [catalog(), securities(), instrument(cik="0001652044", cusip="02079K305", title="Class A Common Stock"),
                   cover(ticker="GOOG", cik="0001652044", title="Class C Capital Stock",
                         url=COVER_URL.replace("/data/320193/", "/data/1652044/"))]
        row = reviewed(sources, ticker="GOOG", cik="0001652044", cusip="02079K305", name="ALPHABET INC", title="CAP STK CL A")
        with self.assertRaisesRegex(ValueError, "same-context"):
            builder(sources).validate_reviewed([row])

    def test_known_at_cannot_precede_observation_or_be_in_the_future(self):
        sources = [catalog(), securities(), instrument(), cover()]
        for known in [OBSERVED - timedelta(minutes=1), NOW + timedelta(minutes=1)]:
            with self.subTest(known=known):
                row = reviewed(sources)
                row["known_at"] = known.isoformat()
                with self.assertRaisesRegex(ValueError, "knowledge"):
                    builder(sources).validate_reviewed([row])

    def test_instrument_date_must_be_in_the_reviewed_binding_interval(self):
        sources = [catalog(), securities(), instrument(event="03/31/2026"), cover()]
        with self.assertRaisesRegex(ValueError, "date"):
            builder(sources).validate_reviewed([reviewed(sources)])

    def test_reused_ticker_cik_change_needs_nonoverlapping_dates_and_primary_transition(self):
        sources = [catalog(), securities(), instrument(), cover()]
        row = reviewed(sources)
        previous = {key: value for key, value in row.items() if key != "review"}
        previous["issuer_cik"] = "0000123456"
        with self.assertRaisesRegex(ValueError, "CIK"):
            builder(sources, existing=[previous]).validate_reviewed([row])
        previous["effective_to"] = "2026-05-31"
        row["effective_from"] = "2026-06-01"
        with self.assertRaisesRegex(ValueError, "primary identity-transition"):
            builder(sources, existing=[previous]).validate_reviewed([row])

    def test_duplicate_import_batch_is_rejected(self):
        sources = [catalog(), securities(), instrument(), cover()]
        row = reviewed(sources)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            builder(sources).validate_reviewed([row, deepcopy(row)])

    def test_future_registry_row_does_not_claim_coverage(self):
        sources = [catalog(), securities(), instrument(), cover()]
        row = reviewed(sources)
        row["known_at"] = (NOW + timedelta(days=1)).isoformat()
        self.assertNotEqual(builder(sources, existing=[row]).candidate("AAPL")["status"], "ALREADY_REVIEWED")

    def test_partial_or_changed_cik_registry_row_does_not_claim_full_quarter_coverage(self):
        sources = [catalog(), securities(), instrument(), cover()]
        row = reviewed(sources)
        row["effective_from"] = "2026-06-01"
        self.assertNotEqual(builder(sources, existing=[row]).candidate("AAPL")["status"], "ALREADY_REVIEWED")
        row["effective_from"] = "2026-04-01"
        row["issuer_cik"] = "0000123456"
        self.assertNotEqual(builder(sources, existing=[row]).candidate("AAPL")["status"], "ALREADY_REVIEWED")

    def test_candidates_resume_from_every_persisted_ticker(self):
        with TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "candidates.json"
            service = builder()
            original = service.candidate
            def interrupt_after_first(ticker):
                if ticker == "GOOG":
                    raise TimeoutError("synthetic interruption")
                return original(ticker)
            with patch.object(service, "candidate", side_effect=interrupt_after_first):
                with self.assertRaises(TimeoutError):
                    service.run_batch(checkpoint, batch_size=3)
            self.assertEqual(json.loads(checkpoint.read_text())["next_index"], 1)
            result = builder().run_batch(checkpoint, batch_size=2)
            self.assertEqual([row["ticker"] for row in result["results"]], ["AAPL", "GOOG", "GOOGL"])
            self.assertEqual(result["status"], "COMPLETE_REVIEW_QUEUE")

    def test_resume_rejects_changed_source_or_clock(self):
        with TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "candidates.json"
            builder().run_batch(checkpoint, batch_size=1)
            with self.assertRaisesRegex(ValueError, "Checkpoint"):
                builder(as_of=NOW + timedelta(seconds=1)).run_batch(checkpoint)

    def test_checkpoint_cursor_and_candidate_flags_cannot_be_tampered(self):
        with TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "candidates.json"
            initial = builder().run_batch(checkpoint, batch_size=1)
            variants = []
            for key, value in [("total_tickers", 1), ("next_index", True),
                               ("verified_registry_changed", True), ("identity_status", "VERIFIED")]:
                state = deepcopy(initial)
                state[key] = value
                variants.append(state)
            state = deepcopy(initial)
            state["results"][0]["scoring_allowed"] = True
            variants.append(state)
            for state in variants:
                checkpoint.write_text(json.dumps(state), encoding="utf-8")
                before = checkpoint.read_bytes()
                with self.subTest(state=state):
                    with self.assertRaisesRegex(ValueError, "Checkpoint"):
                        builder().run_batch(checkpoint)
                    self.assertEqual(checkpoint.read_bytes(), before)

    def test_cli_checkpoint_bound_is_checked_before_loading_or_capturing_sources(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / "candidates.json"
            checkpoint.write_text("{}", encoding="utf-8")
            with patch("market_checker_app.services.sec13f_identity_bulk.MAX_CHECKPOINT_BYTES", 1), \
                    patch("market_checker_app.sec13f_identity_bulk.load_snapshots", side_effect=AssertionError("no source load")), \
                    patch("market_checker_app.sec13f_identity_bulk.capture_source", side_effect=AssertionError("no source capture")):
                with self.assertRaisesRegex(ValueError, "Unbounded"):
                    run_preparation(workspace=root, quarter="2026q2", checkpoint=checkpoint)

    def test_full_687_universe_is_prepared_without_any_per_ticker_network_calls(self):
        with TemporaryDirectory() as directory:
            service = builder([catalog(), securities()], universe=load_canonical_tickers())
            path = Path(directory) / "candidates.json"
            with patch("urllib.request.urlopen", side_effect=AssertionError("no network during preparation")):
                while True:
                    result = service.run_batch(path, batch_size=100)
                    if result["next_index"] == 687:
                        break
            self.assertEqual(len(result["results"]), 687)
            self.assertFalse(result["verified_registry_changed"])
            self.assertTrue(all(not row["scoring_allowed"] for row in result["results"]))

    def test_reviewed_export_keeps_original_registry_unchanged(self):
        sources = [catalog(), securities(), instrument(), cover()]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for source in sources:
                save_snapshot(root, source)
            existing = root / "existing.json"
            existing.write_text("[]\n", encoding="utf-8")
            reviews = root / "reviews.json"
            reviews.write_text(json.dumps([reviewed(sources)]), encoding="utf-8")
            output = root / "proposal.json"
            audit = export_reviewed(workspace=root, quarter="2026q2", review_path=reviews,
                                    output=output, existing_path=existing, as_of=NOW)
            self.assertEqual(audit["new_identities"], 1)
            self.assertEqual(existing.read_text(encoding="utf-8"), "[]\n")
            self.assertTrue(output.with_name("proposal.review.json").exists())
            self.assertEqual(audit["proposal_sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
            with self.assertRaisesRegex(ValueError, "overwritten"):
                export_reviewed(workspace=root, quarter="2026q2", review_path=reviews,
                                output=existing, existing_path=existing, as_of=NOW)

    def test_export_audit_cannot_overwrite_the_existing_registry_or_review_input(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "proposal.review.json"
            registry.write_text("[]\n", encoding="utf-8")
            reviews = root / "reviews.json"
            reviews.write_text("[]\n", encoding="utf-8")
            for output in [root / "proposal.json", reviews, root / "snapshots" / "source.json"]:
                with self.subTest(output=output):
                    with self.assertRaisesRegex(ValueError, "overwritten"):
                        export_reviewed(workspace=root, quarter="2026q2", review_path=reviews,
                                        output=output, existing_path=registry, as_of=NOW)
            self.assertEqual(registry.read_text(encoding="utf-8"), "[]\n")
            self.assertEqual(reviews.read_text(encoding="utf-8"), "[]\n")

    def test_preparation_checkpoint_cannot_overwrite_the_existing_registry_or_snapshot(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "existing.json"
            registry.write_text("[]\n", encoding="utf-8")
            for checkpoint in [registry, root / "snapshots" / "source.json"]:
                with self.subTest(checkpoint=checkpoint):
                    with self.assertRaisesRegex(ValueError, "overwritten"):
                        run_preparation(workspace=root, quarter="2026q2", checkpoint=checkpoint,
                                        existing_path=registry)
            self.assertEqual(registry.read_text(encoding="utf-8"), "[]\n")

    def test_all_production_registries_are_protected_from_cli_output_and_checkpoint_writes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            reviews = root / "reviews.json"
            reviews.write_text("[]", encoding="utf-8")
            for filename in ["verified_13f_securities.json", "verified_fdic_banks.json",
                             "verified_nhtsa_models.json", "verified_usaspending_uei.json"]:
                registry = PRODUCTION_DATA_DIRECTORY / filename
                before = registry.read_bytes()
                with self.subTest(registry=filename):
                    with patch("sys.stdout", new_callable=StringIO):
                        self.assertEqual(main(["prepare", "--workspace", str(root), "--checkpoint", str(registry)]), 1)
                        self.assertEqual(main(["export-reviewed", "--workspace", str(root),
                                               "--reviews", str(reviews), "--output", str(registry)]), 1)
                    with self.assertRaisesRegex(ValueError, "Production data"):
                        atomic_json(registry, [])
                    self.assertEqual(registry.read_bytes(), before)

    def test_capture_and_preparation_workspace_cannot_mutate_any_production_data(self):
        with TemporaryDirectory() as directory:
            with patch("market_checker_app.sec13f_identity_bulk._configured_client", side_effect=AssertionError("no network client")), \
                    patch("sys.stdout", new_callable=StringIO):
                self.assertEqual(main(["capture", "--workspace", str(PRODUCTION_DATA_DIRECTORY),
                                       "--kind", "ticker_catalog", "--url", TICKER_URL]), 1)
                self.assertEqual(main(["prepare", "--workspace", str(PRODUCTION_DATA_DIRECTORY),
                                       "--checkpoint", str(Path(directory) / "candidates.json"), "--capture-global"]), 1)
            with self.assertRaisesRegex(ValueError, "Production data"):
                save_snapshot(PRODUCTION_DATA_DIRECTORY, catalog())

    def test_audit_symlink_to_another_production_registry_is_rejected_before_export(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry = PRODUCTION_DATA_DIRECTORY / "verified_fdic_banks.json"
            before = registry.read_bytes()
            audit = root / "proposal.review.json"
            try:
                audit.symlink_to(registry)
            except OSError as exc:
                self.skipTest(f"Test environment does not permit symlinks: {exc}")
            reviews = root / "reviews.json"
            reviews.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Production data"):
                export_reviewed(workspace=root, quarter="2026q2", review_path=reviews,
                                output=root / "proposal.json", as_of=NOW)
            self.assertFalse((root / "proposal.json").exists())
            self.assertEqual(registry.read_bytes(), before)

    def test_workspace_and_checkpoint_symlinks_resolve_before_production_write_guards(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            alias, checkpoint = root / "data_alias", root / "checkpoint.json"
            registry = PRODUCTION_DATA_DIRECTORY / "verified_nhtsa_models.json"
            before = registry.read_bytes()
            try:
                alias.symlink_to(PRODUCTION_DATA_DIRECTORY, target_is_directory=True)
                checkpoint.symlink_to(registry)
            except OSError as exc:
                self.skipTest(f"Test environment does not permit symlinks: {exc}")
            with patch("sys.stdout", new_callable=StringIO):
                self.assertEqual(main(["capture", "--workspace", str(alias), "--kind", "ticker_catalog", "--url", TICKER_URL]), 1)
                self.assertEqual(main(["prepare", "--workspace", str(root), "--checkpoint", str(checkpoint)]), 1)
            self.assertEqual(registry.read_bytes(), before)

    def test_entry_point_pins_resume_clock_and_uses_cached_global_snapshots(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for source in [catalog(), securities()]:
                save_snapshot(root, source)
            existing = root / "existing.json"
            existing.write_text("[]", encoding="utf-8")
            checkpoint = root / "candidates.json"
            with patch("market_checker_app.sec13f_identity_bulk.capture_source", side_effect=AssertionError("unexpected network")):
                first = run_preparation(workspace=root, quarter="2026q2", checkpoint=checkpoint,
                                        existing_path=existing, batch_size=3, one_batch=True,
                                        as_of=NOW, capture_global=True)
                second = run_preparation(workspace=root, quarter="2026q2", checkpoint=checkpoint,
                                         existing_path=existing, batch_size=3, one_batch=True,
                                         capture_global=True)
            self.assertEqual(first["next_index"], 3)
            self.assertEqual(second["next_index"], 6)
            self.assertEqual(second["as_of"], NOW.isoformat())

    def test_cached_exchange_catalog_does_not_trigger_an_extra_global_download(self):
        source = SourceSnapshot("ticker_catalog", EXCHANGE_URL, OBSERVED,
                                json.dumps({"fields": ["cik", "name", "ticker"],
                                            "data": [[320193, "APPLE INC", "AAPL"]]}).encode())
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for item in [source, securities()]:
                save_snapshot(root, item)
            registry = root / "existing.json"
            registry.write_text("[]", encoding="utf-8")
            with patch("market_checker_app.sec13f_identity_bulk.capture_source", side_effect=AssertionError("no extra download")):
                result = run_preparation(workspace=root, quarter="2026q2", checkpoint=root / "candidates.json",
                                         existing_path=registry, batch_size=1, one_batch=True,
                                         capture_global=True, as_of=NOW)
            self.assertEqual(result["next_index"], 1)

    def test_equal_observation_times_with_conflicting_catalogs_require_source_selection(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            changed = catalog([{"ticker": "AAPL", "cik_str": 123456, "title": "APPLE INC"}])
            for source in [catalog(), changed, securities()]:
                save_snapshot(root, source)
            registry = root / "existing.json"
            registry.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Ambiguous latest"):
                run_preparation(workspace=root, quarter="2026q2", checkpoint=root / "candidates.json",
                                existing_path=registry, as_of=NOW)

    def test_capture_requires_filing_publication_time_before_any_network_call(self):
        with TemporaryDirectory() as directory:
            client = SecEdgarClient(user_agent="fixture fixture@example.test")
            with patch.object(client, "_request_bytes", side_effect=AssertionError("no fetch before publication metadata")):
                with self.assertRaisesRegex(ValueError, "publication time"):
                    capture_source(workspace=Path(directory), kind="schedule13", url=INSTRUMENT_URL, client=client)

    def test_interrupted_global_capture_reuses_completed_source_and_only_downloads_missing_one(self):
        calls, fail_list = [], True
        def transport(url, _headers, _timeout):
            nonlocal fail_list
            calls.append(url)
            if url == TICKER_URL:
                return catalog().payload
            if fail_list:
                raise TimeoutError("fixture global-list interruption")
            return securities().payload
        client = SecEdgarClient(user_agent="fixture fixture@example.test", text_transport=transport,
                                max_attempts=1, sleep=lambda _: None)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "existing.json"
            registry.write_text("[]", encoding="utf-8")
            args = dict(workspace=root, quarter="2026q2", checkpoint=root / "candidates.json",
                        existing_path=registry, capture_global=True, batch_size=1, one_batch=True)
            with patch("market_checker_app.sec13f_identity_bulk._configured_client", return_value=client):
                with self.assertRaises(SecEdgarError):
                    run_preparation(**args)
                self.assertEqual([source.kind for source in load_snapshots(root)], ["ticker_catalog"])
                fail_list = False
                result = run_preparation(**args)
            self.assertEqual(calls, [TICKER_URL, list_url("2026q2"), list_url("2026q2")])
            self.assertEqual(result["next_index"], 1)

    def test_entry_point_reports_sec_access_block_and_interrupt_without_traceback(self):
        for error, code in [(SecAccessBlockedError("fixture HTTP 403"), 1), (KeyboardInterrupt(), 130)]:
            with self.subTest(error=type(error).__name__):
                with patch("market_checker_app.sec13f_identity_bulk.run_preparation", side_effect=error), \
                        patch("sys.stdout", new_callable=StringIO) as output:
                    self.assertEqual(main(["prepare"]), code)
                    self.assertNotIn("Traceback", output.getvalue())

    def test_terminated_cli_process_resumes_saved_cursor_to_exactly_687(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for source in [catalog(), securities()]:
                save_snapshot(root, source)
            command = [sys.executable, "-m", "market_checker_app.sec13f_identity_bulk", "prepare",
                       "--workspace", str(root), "--as-of", NOW.isoformat()]
            cwd = Path(__file__).resolve().parents[1]
            process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            checkpoint, stopped_at = root / "candidates.json", 0
            deadline = time.monotonic() + 20
            try:
                while time.monotonic() < deadline and process.poll() is None:
                    if checkpoint.exists():
                        state = json.loads(checkpoint.read_text(encoding="utf-8"))
                        if state["next_index"] >= 3:
                            process.terminate()
                            break
                    time.sleep(0.01)
                stdout, stderr = process.communicate(timeout=10)
                state = json.loads(checkpoint.read_text(encoding="utf-8"))
                stopped_at = state["next_index"]
                self.assertGreaterEqual(stopped_at, 3, stderr or stdout)
                self.assertLess(stopped_at, 687)
                self.assertNotEqual(process.returncode, 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=10)
            resumed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=30)
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            result = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual([row["ticker"] for row in result["results"]], load_canonical_tickers())
            self.assertEqual(result["next_index"], 687)
            self.assertEqual(result["status"], "COMPLETE_REVIEW_QUEUE")
            self.assertTrue(all(row["scoring_allowed"] is False for row in result["results"]))
            self.assertIn("687/687 saved", resumed.stdout)

    def test_atomic_checkpoint_recovers_temporary_windows_reader_locks(self):
        original_replace = os.replace
        for winerror in [5, 32, 33]:
            with self.subTest(winerror=winerror), TemporaryDirectory() as directory:
                root = Path(directory)
                checkpoint = root / "candidates.json"
                before = b'{"next_index": 2}\n'
                checkpoint.write_bytes(before)
                attempts = []

                def replace(source, destination):
                    attempts.append(1)
                    if len(attempts) < 3:
                        self.assertEqual(checkpoint.read_bytes(), before)
                        error = PermissionError(13, "temporary fixture reader lock")
                        error.winerror = winerror
                        raise error
                    return original_replace(source, destination)

                with patch("market_checker_app.services.sec13f_identity_bulk.os.replace", side_effect=replace), \
                        patch("market_checker_app.services.sec13f_identity_bulk.time.sleep"):
                    atomic_json(checkpoint, {"next_index": 3})
                self.assertEqual(json.loads(checkpoint.read_text(encoding="utf-8")), {"next_index": 3})
                self.assertEqual(len(attempts), 3)
                self.assertEqual(list(root.iterdir()), [checkpoint])

    def test_atomic_checkpoint_preserves_previous_state_when_windows_lock_persists(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / "candidates.json"
            before = b'{"next_index": 2}\n'
            checkpoint.write_bytes(before)
            error = PermissionError(13, "persistent fixture reader lock")
            error.winerror = 32
            with patch("market_checker_app.services.sec13f_identity_bulk.os.replace", side_effect=error) as replace, \
                    patch("market_checker_app.services.sec13f_identity_bulk.time.sleep"):
                with self.assertRaises(PermissionError) as caught:
                    atomic_json(checkpoint, {"next_index": 3})
            self.assertIs(caught.exception, error)
            self.assertGreater(replace.call_count, 1)
            self.assertLessEqual(replace.call_count, 25)
            self.assertEqual(checkpoint.read_bytes(), before)
            self.assertEqual(list(root.iterdir()), [checkpoint])

    def test_atomic_checkpoint_does_not_retry_other_permission_failures(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / "candidates.json"
            before = b'{"next_index": 2}\n'
            checkpoint.write_bytes(before)
            error = PermissionError(13, "fixture permissions denied")
            with patch("market_checker_app.services.sec13f_identity_bulk.os.replace", side_effect=error) as replace, \
                    patch("market_checker_app.services.sec13f_identity_bulk.time.sleep") as sleep:
                with self.assertRaises(PermissionError) as caught:
                    atomic_json(checkpoint, {"next_index": 3})
            self.assertIs(caught.exception, error)
            replace.assert_called_once()
            sleep.assert_not_called()
            self.assertEqual(checkpoint.read_bytes(), before)
            self.assertEqual(list(root.iterdir()), [checkpoint])


if __name__ == "__main__":
    unittest.main()
