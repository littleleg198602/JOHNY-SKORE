"""A literal issuer-disclosed IR website never activates publisher authority."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import unittest
from unittest.mock import patch

from evidence.batch7_sec_ir_authority_20261015.build_review import (
    ORIGINALS, RETAINED, ROOT as EVIDENCE, URL_LITERAL,
    build_review_artifact, build_review_plan, load_inputs,
)
from market_checker_app.services.sec_ir_disclosure_authority_review import (
    IRDisclosureReviewError, MAX_BODY_BYTES, SECIRDisclosureCapture, review_sec_ir_disclosure_authority,
)


def changed(capture, body, **fields):
    return replace(capture, body=body, sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body), **fields)


def index_bytes(source, *, cik="0001045810", form="10-K", filing_day="2026-02-25", report_day="2026-01-25", duplicate=False):
    row = (f'<tr><td>1</td><td>10-K</td><td><a href="{source.source_url}">nvda-20260125.htm</a></td>'
           f'<td>{form}</td><td>{source.size_bytes}</td></tr>')
    return (f'<html><div class="infoHead">Filing Date</div><div class="info">{filing_day}</div>'
        f'<div class="infoHead">Accepted</div><div class="info">2026-02-25 16:42:19</div>'
        f'<div class="infoHead">Period of Report</div><div class="info">{report_day}</div>'
        f'<table>{row}{row if duplicate else ""}</table><span class="companyName">NVIDIA CORP (Filer)'
        f'<a href="/cgi-bin/browse-edgar?CIK={cik}&amp;action=getcompany">{cik}</a></span></html>').encode()


class SECIRAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.actual_source, cls.actual_index, cls.actual_selection, cls.receipts = load_inputs()
        cls.clock = max(capture.available_at for capture in (cls.actual_source, cls.actual_index))

    def setUp(self):
        self.quote = "Our investor relations website, http://investor.nvidia.com."
        self.body_prefix = b'''<html xmlns:dei="http://xbrl.sec.gov/dei/2026" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
          xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:issuer="https://example.test/issuer">
          <xbrli:context id="c-1"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0001045810</xbrli:identifier></xbrli:entity>
          <xbrli:period><xbrli:startDate>2025-01-27</xbrli:startDate><xbrli:endDate>2026-01-25</xbrli:endDate></xbrli:period></xbrli:context>
          <ix:nonNumeric name="dei:EntityRegistrantName" contextRef="c-1" id="name">NVIDIA CORP</ix:nonNumeric>
          <ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="c-1" id="cik">0001045810</ix:nonNumeric>
          <ix:nonNumeric name="dei:DocumentType" contextRef="c-1" id="form">10-K</ix:nonNumeric>
          <ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="c-1" id="period">January 25, 2026</ix:nonNumeric>'''
        self.source, self.index, self.selection = self.fixture(self.quote)

    def fixture(self, quote, *, uri=URL_LITERAL, prefix=None, div_start=b"<div>", before=b"", after=b""):
        body = (self.body_prefix if prefix is None else prefix) + before + div_start + b"<span>" + quote.encode() + b"</span></div>" + after + b"</html>"
        source = changed(self.actual_source, body, record_id="synthetic-ir-disclosure")
        index = changed(self.actual_index, index_bytes(source), record_id="synthetic-ir-index")
        start = body.index(quote.encode()); end = start + len(quote.encode())
        selection = {"issuer_context_id": "c-1", "issuer_name": "NVIDIA CORP", "issuer_cik": "0001045810",
            "document_type": "10-K", "document_period_end": "2026-01-25", "ir_url_literal": uri,
            "ir_context_locator": "div:1", "ir_context_quote": quote,
            "ir_context_span": {"byte_start": start, "byte_end": end, "sha256": hashlib.sha256(body[start:end]).hexdigest()}}
        return source, index, selection

    def review(self, *, source=None, index=None, selection=None, clock=None):
        return review_sec_ir_disclosure_authority(source_capture=self.source if source is None else source,
            index_capture=self.index if index is None else index,
            selection=self.selection if selection is None else selection, knowledge_at=self.clock if clock is None else clock)

    def test_actual_whole_original_pins_and_complete_replay_match_without_new_http(self):
        artifact = build_review_artifact()
        self.assertEqual(json.loads((EVIDENCE / "ir_disclosure_review.json").read_bytes()), artifact)
        self.assertEqual(0, artifact["actual_new_http_requests"])
        self.assertEqual(2, artifact["existing_http200_original_bodies_used"])
        self.assertEqual(0, artifact["new_disclosures_collected"])
        self.assertEqual(1, artifact["underlying_independent_sec_disclosures"])
        for filename, (size, digest) in ORIGINALS.items():
            raw = (RETAINED / filename).read_bytes()
            self.assertEqual(size, len(raw)); self.assertEqual(digest, hashlib.sha256(raw).hexdigest())
        self.assertFalse(artifact["whole_withheld_bodies_restored_or_substituted"])

    def test_actual_dei_issuer_and_literal_http_ir_uri_support_only_disclosed_website_fact(self):
        report = self.review(source=self.actual_source, index=self.actual_index, selection=self.actual_selection)
        self.assertEqual("PARTIAL", report["status"])
        self.assertTrue(report["issuer_disclosed_ir_website_fact_supported"])
        fact = report["issuer_disclosed_website_fact"]
        self.assertEqual(("NVIDIA CORP", "0001045810", "c-1"), (fact["issuer_name"], fact["issuer_cik"], fact["context_id"]))
        self.assertEqual("2026-01-25", fact["document_period_end"])
        self.assertEqual(URL_LITERAL, fact["ir_url_literal"])
        self.assertEqual("div:818", fact["paragraph_locator"])
        self.assertFalse(fact["uri_scheme_rewritten"])
        span = fact["url_original_span"]
        self.assertEqual(URL_LITERAL.encode(), self.actual_source.body[span["byte_start"]:span["byte_end"]])
        for key in ("publisher_authority_verified", "live_verified", "issuer_continuity_verified", "feed_activation_allowed",
                    "source_alias_approved", "hostname_is_issuer_identity", "issuer_attribution_allowed", "scoring_applied"):
            self.assertFalse(report[key])

    def test_actual_index_clock_and_111_byte_version_disagreement_are_preserved(self):
        report = build_review_artifact()["review"]
        source = report["filing"]
        self.assertEqual((1967816, 1967927), (source["index_declared_primary_bytes"], source["captured_primary_bytes"]))
        self.assertTrue(source["publisher_size_conflict"])
        self.assertFalse(source["exact_historical_as_filed_version_verified"])
        self.assertEqual("2026-02-25", source["filing_day"])
        self.assertEqual("2026-02-25 16:42:19", source["acceptance_datetime_unqualified_raw"])
        self.assertFalse(source["acceptance_timezone_inferred"])

    def test_exact_dei_name_cik_and_period_cannot_be_replaced_by_ticker_or_hostname(self):
        for fields in ({"issuer_name": "NVIDIA CORPORATION"}, {"issuer_cik": "0000789019"},
                       {"issuer_name": "NVDA"}, {"document_period_end": "2026-02-25"}):
            with self.assertRaisesRegex(IRDisclosureReviewError, "same exact source context"):
                self.review(selection={**self.selection, **fields})

    def test_cik_and_name_in_separate_contexts_cannot_be_joined(self):
        prefix = self.body_prefix.replace(b'name="dei:EntityCentralIndexKey" contextRef="c-1"', b'name="dei:EntityCentralIndexKey" contextRef="other"')
        source, index, selection = self.fixture(self.quote, prefix=prefix)
        with self.assertRaisesRegex(ValueError, "SAME_CONTEXT_DEI_FACT"):
            self.review(source=source, index=index, selection=selection)

    def test_ambiguous_or_transformed_dei_name_cik_is_rejected(self):
        for before, after in ((b'NVIDIA CORP</ix:nonNumeric>', b'NVIDIA CORP</ix:nonNumeric><ix:nonNumeric name="dei:EntityRegistrantName" contextRef="c-1">OTHER CORP</ix:nonNumeric>'),
                              (b'id="name"', b'id="name" continuedAt="continued"'),
                              (b'id="cik"', b'id="cik" format="issuer:format"'),
                              (b'id="name"', b'id="name" escape="true"')):
            source, index, selection = self.fixture(self.quote, prefix=self.body_prefix.replace(before, after))
            with self.assertRaises(ValueError): self.review(source=source, index=index, selection=selection)

    def test_cofilers_our_statement_cannot_be_attributed_to_one_selected_issuer(self):
        child = b'''<xbrli:context id="child"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000123456</xbrli:identifier></xbrli:entity>
          <xbrli:period><xbrli:startDate>2025-01-27</xbrli:startDate><xbrli:endDate>2026-01-25</xbrli:endDate></xbrli:period></xbrli:context>
          <ix:nonNumeric name="dei:EntityRegistrantName" contextRef="child">OTHER CORP</ix:nonNumeric>
          <ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="child">0000123456</ix:nonNumeric>'''
        source, index, selection = self.fixture(self.quote, before=child)
        with self.assertRaisesRegex(IRDisclosureReviewError, "Co-filer"):
            self.review(source=source, index=index, selection=selection)

    def test_dimensional_context_does_not_supply_whole_issuer_website_scope(self):
        prefix = self.body_prefix.replace(b'</xbrli:entity>', b'<xbrli:segment><xbrldi:explicitMember dimension="dei:LegalEntityAxis">issuer:ChildMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>', 1)
        source, index, selection = self.fixture(self.quote, prefix=prefix)
        with self.assertRaisesRegex(IRDisclosureReviewError, "same exact source context"):
            self.review(source=source, index=index, selection=selection)

    def test_ordinary_website_or_investor_hostname_without_explicit_own_ir_role_is_insufficient(self):
        for quote, uri in (("Our website, http://www.nvidia.com.", "http://www.nvidia.com"),
                           ("We publish news at http://investor.nvidia.com.", URL_LITERAL),
                           ("Investor Relations (investor.nvidia.com)", URL_LITERAL)):
            source, index, selection = self.fixture(quote, uri=uri)
            report = self.review(source=source, index=index, selection=selection)
            self.assertFalse(report["issuer_disclosed_ir_website_fact_supported"])
            self.assertFalse(report["publisher_authority_verified"])

    def test_third_party_quoted_former_or_negated_claims_do_not_supply_issuer_scope(self):
        for quote in ('Example Corp said "our investor relations website, http://investor.nvidia.com."',
                      "Our investor relations website, http://investor.nvidia.com, is no longer used.",
                      "Our investor relations website, http://investor.nvidia.com, is only an example.",
                      "Our subsidiary said our investor relations website, http://investor.nvidia.com."):
            source, index, selection = self.fixture(quote)
            report = self.review(source=source, index=index, selection=selection)
            self.assertFalse(report["issuer_disclosed_ir_website_fact_supported"])

    def test_literal_uri_cannot_be_upgraded_extended_or_truncated(self):
        for uri in ("https://investor.nvidia.com", "http://investor.nvidia", "http://investor.nvidia.co",
                    "http://investor.nvidia.com/", "http://investor.nvidia.com/wider-scope"):
            report = self.review(selection={**self.selection, "ir_url_literal": uri})
            self.assertFalse(report["issuer_disclosed_ir_website_fact_supported"])

    def test_literal_http_uri_does_not_promote_same_hostname_to_https_or_any_feed(self):
        report = self.review()
        self.assertTrue(report["issuer_disclosed_ir_website_fact_supported"])
        self.assertEqual("http://investor.nvidia.com", report["issuer_disclosed_website_fact"]["ir_url_literal"])
        self.assertFalse(report["feed_activation_allowed"])
        self.assertFalse(report["source_alias_approved"])
        self.assertFalse(report["live_verified"])

    def test_original_index_basename_must_match_its_accession_directory(self):
        wrong = self.actual_index.source_url.replace("0001045810-26-000021-index.htm", "0001045810-26-000022-index.htm")
        with self.assertRaisesRegex(IRDisclosureReviewError, "index basename conflicts"):
            self.review(source=self.actual_source, index=replace(self.actual_index, source_url=wrong), selection=self.actual_selection)

    def test_index_filing_day_cannot_follow_original_observation_even_with_later_knowledge(self):
        index = changed(self.actual_index, self.actual_index.body.replace(b"2026-02-25", b"2099-02-25"))
        for clock in (self.clock, self.clock.replace(year=2100)):
            with self.assertRaisesRegex(IRDisclosureReviewError, "date or declared-body scope"):
                self.review(source=replace(self.actual_source, known_at=clock),
                            index=replace(index, known_at=clock), selection=self.actual_selection, clock=clock)

    def test_index_filer_form_primary_row_and_report_period_must_agree(self):
        for raw in (index_bytes(self.source, cik="0000789019"), index_bytes(self.source, form="8-K"),
                    index_bytes(self.source, duplicate=True), index_bytes(self.source, report_day="2025-01-25"),
                    index_bytes(self.source).replace(b'nvda-20260125.htm', b'other-document.htm')):
            with self.assertRaises(IRDisclosureReviewError): self.review(index=changed(self.index, raw))

    def test_index_and_source_must_have_the_same_exact_issuer_and_accession(self):
        for url in (self.index.source_url.replace("/1045810/", "/789019/"),
                    self.index.source_url.replace("000104581026000021", "000104581026000022")):
            with self.assertRaisesRegex(IRDisclosureReviewError, "issuer/accession differ"):
                self.review(index=replace(self.index, source_url=url))

    def test_whole_context_quote_and_byte_offsets_are_checked(self):
        for selection in ({**self.selection, "ir_context_quote": self.quote + " extra"},
                          {**self.selection, "ir_context_locator": "div:2"},
                          {**self.selection, "ir_context_span": {**self.selection["ir_context_span"], "byte_start": 1}},
                          {**self.selection, "ir_context_span": {**self.selection["ir_context_span"], "sha256": "0" * 64}}):
            with self.assertRaises(ValueError): self.review(selection=selection)

    def test_partial_paragraph_or_a_different_div_cannot_borrow_the_ir_ownership_phrase(self):
        quote = "We publish reports. Our investor relations website, http://investor.nvidia.com."
        source, index, selection = self.fixture(quote)
        partial = "Our investor relations website, http://investor.nvidia.com."
        start = source.body.index(partial.encode()); end = start + len(partial)
        selection.update(ir_context_quote=partial,
            ir_context_span={"byte_start":start,"byte_end":end,"sha256":hashlib.sha256(source.body[start:end]).hexdigest()})
        with self.assertRaisesRegex(IRDisclosureReviewError, "complete exact original paragraph"):
            self.review(source=source, index=index, selection=selection)

    def test_hidden_or_commented_original_paragraph_cannot_supply_visible_evidence(self):
        for div in (b'<div style="display:none">', b'<div hidden="hidden">', b'<div style="visibility:hidden">'):
            source, index, selection = self.fixture(self.quote, div_start=div)
            with self.assertRaises(ValueError): self.review(source=source, index=index, selection=selection)
        source, index, selection = self.fixture(self.quote, before=b"<!--", after=b"-->")
        with self.assertRaises(ValueError): self.review(source=source, index=index, selection=selection)

    def test_repeated_identical_passage_is_ambiguous_and_not_an_independent_source(self):
        source, index, selection = self.fixture(self.quote, after=b"<div><span>" + self.quote.encode() + b"</span></div>")
        with self.assertRaisesRegex(ValueError, "AMBIGUOUS_REPEATED_PASSAGE"):
            self.review(source=source, index=index, selection=selection)

    def test_whole_body_sensitive_or_unavailable_capture_is_not_opened_by_any_parser(self):
        secret = b'<html>clientSecret="SYNTHETIC_NO_ACCESS_VALUE_12345"</html>'
        for source in (changed(self.source, secret), replace(self.source, body=None, retention="WITHHELD_SENSITIVE_BODY"),
                       replace(self.source, body=None, retention="MISSING_BODY")):
            with patch("market_checker_app.services.sec_ir_disclosure_authority_review._dei_and_contexts", side_effect=AssertionError("no parser")), \
                    patch("market_checker_app.services.sec_ir_disclosure_authority_review._Index", side_effect=AssertionError("no parser")):
                report = self.review(source=source)
            self.assertFalse(report["issuer_disclosed_ir_website_fact_supported"])
            self.assertEqual(["REQUIRED_WHOLE_ORIGINAL_UNAVAILABLE"], report["reasons"])
            self.assertNotIn("SYNTHETIC_NO_ACCESS_VALUE", json.dumps(report))
            self.assertNotIn("issuer_disclosed_website_fact", report)

    def test_unavailable_whole_body_cannot_be_replaced_by_empty_or_cleaned_bytes(self):
        for raw in (b"", b"<html>cleaned</html>"):
            with self.assertRaisesRegex(ValueError, "replacement body"):
                self.review(source=replace(self.source, body=raw, retention="WITHHELD_SENSITIVE_BODY"))

    def test_source_hash_size_cap_and_timezone_are_strict(self):
        for source in (replace(self.source, sha256="0" * 64), replace(self.source, body=self.source.body+b"\n"),
                       replace(self.source, size_bytes=MAX_BODY_BYTES+1), replace(self.source, size_bytes=True),
                       replace(self.source, observed_at=self.source.observed_at.replace(tzinfo=None)),
                       replace(self.source, known_at=self.source.observed_at-timedelta(seconds=1))):
            with self.assertRaises(ValueError): self.review(source=source)

    def test_future_capture_or_input_knowledge_is_withheld_before_any_quoted_fact(self):
        for source, cutoff in ((self.source, self.clock-timedelta(microseconds=1)),
                               (replace(self.source, known_at=self.clock+timedelta(seconds=1)), self.clock)):
            with patch("market_checker_app.services.sec_ir_disclosure_authority_review._Index", side_effect=AssertionError("no parser")):
                report = self.review(source=source, clock=cutoff)
            self.assertEqual(["REQUIRED_ORIGINAL_CAPTURE_NOT_YET_KNOWN"], report["reasons"])
            self.assertFalse(report["issuer_disclosed_ir_website_fact_supported"])
            self.assertNotIn("issuer_disclosed_website_fact", report)

    def test_dtd_entity_or_unbounded_selection_cannot_reach_fact_review(self):
        source = changed(self.source, b'<!DOCTYPE html [<!ENTITY bad "expanded">]>' + self.source.body)
        with self.assertRaisesRegex(IRDisclosureReviewError, "entity or DTD"):
            self.review(source=source, index=changed(self.index, index_bytes(source)))
        with self.assertRaisesRegex(IRDisclosureReviewError, "exceeds its bound"):
            self.review(selection={**self.selection, "extra": "x" * 16001})
        with self.assertRaisesRegex(IRDisclosureReviewError, "bounded whole original"):
            self.review(selection={**self.selection, "ir_context_locator": "div:0"})

    def test_narrow_whole_div_bound_does_not_accept_a_broad_nested_document(self):
        source, index, selection = self.fixture(self.quote, div_start=b'<div><div>', after=b'</div>')
        with self.assertRaisesRegex(IRDisclosureReviewError, "broad nested div"):
            self.review(source=source, index=index, selection=selection)

    def test_pure_api_does_no_http_sqlite_registry_write_or_input_mutation(self):
        before = deepcopy(self.selection)
        with patch("socket.socket.connect", side_effect=AssertionError("no HTTP")), \
                patch("sqlite3.connect", side_effect=AssertionError("no SQLite")):
            report = self.review()
        self.assertEqual(before, self.selection)
        self.assertEqual(0, report["network_requests_performed"])
        for key in ("registry_modified", "quota_or_lease_modified", "historical_website_validity_verified",
                    "historical_machine_availability_inferred", "scoring_applied", "trading_allowed"):
            self.assertFalse(report[key])

    def test_plan_uses_only_exact_retained_originals_with_posix_repository_references(self):
        plan = build_review_plan()
        self.assertEqual(json.loads((EVIDENCE/"actual_review_plan.json").read_bytes()), plan)
        self.assertEqual("SEC_IR_DISCLOSURE_AUTHORITY", plan["kind"])
        self.assertEqual({"source","index"}, set(plan["captures"]))
        self.assertEqual({"selection"}, set(plan["request"]))
        repo = EVIDENCE.parents[1]
        for capture in plan["captures"].values():
            ref = capture["body_ref"]
            self.assertEqual(PurePosixPath(ref["path"]).as_posix(), PureWindowsPath(ref["path"]).as_posix())
            raw = repo.joinpath(*PurePosixPath(ref["path"]).parts).read_bytes()
            self.assertEqual(ref["bytes"], len(raw)); self.assertLessEqual(ref["bytes"], MAX_BODY_BYTES)
            self.assertEqual(ref["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(ref["sha256"], capture["metadata"]["sha256"])


if __name__ == "__main__":
    unittest.main()
