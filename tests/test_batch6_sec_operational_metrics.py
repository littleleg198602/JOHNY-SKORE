"""Generated mechanism fixtures, never LIVE or historical acceptance evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import unittest
from unittest.mock import patch

from market_checker_app.services import sec_operational_metrics_review as review


CAPTURED = datetime(2026, 10, 14, 13, 28, tzinfo=timezone.utc)
INGESTED = datetime(2026, 10, 14, 16, tzinfo=timezone.utc)
METRIC = "Collectively, the Company operates 91&#160;manufacturing sites in 29&#160;countries."
SCOPE = ('This Annual Report on Form 10-K is a combined report being filed by Dow Inc. and '
         'The Dow Chemical Company and its consolidated subsidiaries, together called the Company.')


def _facts(context, name, cik):
    values = {"EntityRegistrantName": name, "EntityCentralIndexKey": cik,
              "DocumentPeriodEndDate": "December 31, 2025", "DocumentFiscalYearFocus": "2025",
              "DocumentType": "10-K"}
    return "".join(f'<ix:nonNumeric name="dei:{field}" contextRef="{context}" id="{context}-{field}">{value}</ix:nonNumeric>'
                   for field, value in values.items())


def _context(identifier, *, dimensional=False):
    segment = ('<xbrli:segment><xbrldi:explicitMember dimension="dei:LegalEntityAxis">'
               'dow:TheDowChemicalCompanyMember</xbrldi:explicitMember></xbrli:segment>') if dimensional else ""
    return (f'<xbrli:context id="{identifier}"><xbrli:entity>'
            f'<xbrli:identifier scheme="http://www.sec.gov/CIK">0001751788</xbrli:identifier>{segment}'
            '</xbrli:entity><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate>'
            '<xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>')


def _span(body, text):
    encoded = text.encode()
    start = body.index(encoded)
    return {"byte_start": start, "byte_end": start + len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}


def _fixture(*, metric=METRIC, scope=SCOPE, cofiling=True, hidden=False):
    body = (f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
            'xmlns:dei="http://xbrl.sec.gov/dei/2025" xmlns:xbrli="http://www.xbrl.org/2003/instance" '
            'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:dow="http://www.dow.com/20251231">'
            f'<body><ix:header><ix:resources>{_context("c-2")}'
            f'{_context("c-1", dimensional=True) if cofiling else ""}</ix:resources></ix:header>'
            f'{_facts("c-2", "Dow Inc.", "0001751788")}'
            f'{_facts("c-1", "The Dow Chemical Company", "0000029915") if cofiling else ""}'
            f'<p id="scope">{scope}</p><div{chr(32) + "hidden=\"hidden\"" if hidden else ""}>'
            f'<p id="metric">{metric}</p></div></body></html>').encode()
    metadata = {"archive_path": "evidence/synthetic_fixture.html", "origin": "LIVE_PRIMARY_RESEARCH_HTTP",
                "status": "CAPTURED", "public_raw_available": True, "http_status": 200,
                "raw_response_sha256": hashlib.sha256(body).hexdigest(), "raw_response_bytes": len(body),
                "requested_url": "https://www.sec.gov/Archives/edgar/data/1751788/000175178826000018/dow-20251231.htm",
                "final_url": "https://www.sec.gov/Archives/edgar/data/1751788/000175178826000018/dow-20251231.htm",
                "started_at": (CAPTURED - timedelta(seconds=3)).isoformat(), "observed_at": CAPTURED.isoformat()}
    selection = {"issuer_context_id": "c-2", "issuer_name": "Dow Inc.", "issuer_cik": "0001751788",
                 "fiscal_period_end": "2025-12-31", "metric_kind": "MANUFACTURING_SITE_COUNT",
                 "value_literal": "91", "unit_literal": "manufacturing sites", "scope_kind": "COMBINED_REPORT_GROUP",
                 "metric_passage": _span(body, metric), "scope_passage": _span(body, scope)}
    return body, metadata, selection


class SecOperationalMetricTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch.object(review, "_physical_clock", return_value=INGESTED)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def _review(self, fixture=None, **changes):
        body, metadata, selection = fixture or _fixture()
        kwargs = {"capture_metadata": metadata, "expected_body_sha256": metadata["raw_response_sha256"],
                  "selection": selection, **changes}
        return review.review_sec_operational_metric(body, **kwargs)

    def _modified(self, old, new, **fixture_kwargs):
        body, metadata, selection = _fixture(**fixture_kwargs)
        body = body.replace(old.encode(), new.encode())
        metadata.update(raw_response_sha256=hashlib.sha256(body).hexdigest(), raw_response_bytes=len(body))
        metric, scope = fixture_kwargs.get("metric", METRIC), fixture_kwargs.get("scope", SCOPE)
        selection.update(metric_passage=_span(body, metric), scope_passage=_span(body, scope))
        return body, metadata, selection

    def test_literal_quantity_is_only_combined_report_group_with_distinct_context_pairs(self):
        result = self._review()
        self.assertEqual("CANDIDATE_REVIEWED", result["status"])
        candidate = result["candidate"]
        self.assertEqual("91", candidate["value"])
        self.assertEqual("manufacturing sites", candidate["unit_literal"])
        self.assertEqual("c-2", candidate["issuer_anchor"]["context_id"])
        self.assertEqual("0001751788", candidate["issuer_anchor"]["issuer_cik"])
        pairs = {row["context_id"]: row for row in candidate["cofiler_dei_context_pairs"]}
        self.assertEqual("0000029915", pairs["c-1"]["issuer_cik"])
        self.assertEqual("The Dow Chemical Company", pairs["c-1"]["issuer_name"])
        self.assertTrue(pairs["c-1"]["context_dimensions"])
        self.assertFalse(candidate["individual_issuer_quantity_attribution_verified"])
        self.assertFalse(candidate["metric_measurement_date_verified"])
        self.assertEqual("2025-12-31", candidate["fiscal_reporting_period"]["end"])
        self.assertEqual("COMBINED_REPORT_GROUP", candidate["scope_kind"])
        self.assertEqual(CAPTURED.isoformat(), result["source"]["source_capture_completed_at"])
        self.assertEqual(INGESTED.isoformat(), result["ingested_at"])
        self.assertEqual(INGESTED.isoformat(), result["knowledge_at"])
        self.assertIsNone(result["source"]["publisher_publication_at"])
        for field in ("issuer_identity_promoted", "current_instrument_identity_verified", "current_q3_identity_verified",
                      "asset_ownership_inferred", "quantity_or_revenue_share_inferred", "market_price_inference",
                      "forward_looking_inference", "historical_availability_reconstructed", "scoring_applied",
                      "prediction_input", "out_of_sample_evaluation_verified", "fixtures_count_as_live"):
            self.assertFalse(result[field])

    def test_cross_context_parent_name_and_chemical_cik_are_not_joined(self):
        fixture = _fixture()
        for changes in ({"issuer_context_id": "c-1"}, {"issuer_cik": "0000029915"},
                        {"issuer_name": "The Dow Chemical Company"}):
            with self.subTest(changes=changes):
                selection = {**fixture[2], **changes}
                result = self._review((fixture[0], fixture[1], selection))
                self.assertEqual("DEI_NAME_CIK_SAME_CONTEXT_MISMATCH", result["reason"])
                self.assertIsNone(result["candidate"])

    def test_explicit_chemical_context_needs_dimension_review_not_parent_promotion(self):
        body, metadata, selection = _fixture()
        selection.update(issuer_context_id="c-1", issuer_name="The Dow Chemical Company", issuer_cik="0000029915")
        result = self._review((body, metadata, selection))
        self.assertEqual("CONTEXT_ENTITY_OR_DIMENSIONS_NEED_SEPARATE_REVIEW", result["reason"])

    def test_combined_report_cannot_be_relabeled_as_single_issuer_scope(self):
        body, metadata, selection = _fixture()
        selection["scope_kind"] = "SINGLE_ISSUER_GROUP"
        self.assertEqual("INDIVIDUAL_ATTRIBUTION_AMBIGUOUS_IN_COFILING", self._review((body, metadata, selection))["reason"])

    def test_missing_or_conflicting_same_context_dei_facts_fail_closed(self):
        original = '<ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="c-2" id="c-2-EntityCentralIndexKey">0001751788</ix:nonNumeric>'
        absent = self._modified(original, "")
        self.assertEqual("MISSING_OR_CONFLICTING_SAME_CONTEXT_DEI_FACT", self._review(absent)["reason"])
        added = '<ix:nonNumeric name="dei:EntityRegistrantName" contextRef="c-2">Other Company</ix:nonNumeric>'
        conflicting = self._modified("</body>", added + "</body>")
        self.assertEqual("MISSING_OR_CONFLICTING_SAME_CONTEXT_DEI_FACT", self._review(conflicting)["reason"])

    def test_namespace_spoof_and_duplicate_context_are_rejected(self):
        spoof = self._modified('xmlns:dei="http://xbrl.sec.gov/dei/2025"', 'xmlns:dei="https://example.com/fake"')
        self.assertEqual("MISSING_OR_CONFLICTING_SAME_CONTEXT_DEI_FACT", self._review(spoof)["reason"])
        duplicate = self._modified("</ix:resources>", _context("c-2") + "</ix:resources>")
        self.assertEqual("DUPLICATE_OR_MISSING_XBRL_CONTEXT", self._review(duplicate)["reason"])

    def test_unknown_segment_is_not_an_undimensioned_selected_issuer_context(self):
        marker = '<xbrli:identifier scheme="http://www.sec.gov/CIK">0001751788</xbrli:identifier>'
        segment = '<xbrli:segment><unexpected>unresolved entity scope</unexpected></xbrli:segment>'
        modified = self._modified(marker, marker + segment)
        self.assertEqual("CONTEXT_ENTITY_OR_DIMENSIONS_NEED_SEPARATE_REVIEW", self._review(modified)["reason"])

    def test_fiscal_period_year_and_document_type_must_match_same_context(self):
        for old, new in ((">December 31, 2025<", ">December 31, 2024<"),
                         (">2025</ix:nonNumeric>", ">2024</ix:nonNumeric>"),
                         (">10-K</ix:nonNumeric>", ">10-Q</ix:nonNumeric>")):
            with self.subTest(new=new):
                self.assertEqual("FISCAL_PERIOD_OR_DOCUMENT_TYPE_MISMATCH", self._review(self._modified(old, new))["reason"])

    def test_body_hash_length_http_capture_status_and_source_authority_are_verified(self):
        body, metadata, selection = _fixture()
        cases = [{"raw_response_bytes": len(body) - 1}, {"http_status": 400}, {"status": "PARTIAL"},
                 {"origin": "FIXTURE"}, {"body_transformed": "sanitized"},
                 {"requested_url": "https://example.com/filing.htm"},
                 {"final_url": metadata["final_url"] + "?value=x"}]
        for update in cases:
            with self.subTest(update=update):
                self.assertEqual("REJECTED", self._review((body, {**metadata, **update}, selection))["status"])
        self.assertEqual("ORIGINAL_BODY_HASH_LENGTH_OR_BOUND_MISMATCH", self._review(expected_body_sha256="0" * 64)["reason"])

    def test_withheld_metadata_rejects_before_body_decode_or_parse(self):
        body, metadata, selection = _fixture()
        metadata["public_raw_available"] = False
        with patch.object(review.ET, "iterparse", side_effect=AssertionError("parser reached")):
            result = self._review((b"\xffnot-public", metadata, selection))
        self.assertEqual("WITHHELD_OR_UNAVAILABLE_WHOLE_BODY", result["reason"])

    def test_whole_unsafe_body_is_rejected_without_secret_echo_or_replacement(self):
        unsafe = self._modified("</body>", '<script>access_token="short";</script></body>')
        body_before = unsafe[0]
        result = self._review(unsafe)
        self.assertEqual("UNSAFE_WHOLE_SOURCE_OR_METADATA", result["reason"])
        self.assertNotIn("short", str(result))
        self.assertEqual(body_before, unsafe[0])
        self.assertIsNone(result["candidate"])

    def test_physical_ingestion_and_knowledge_cannot_be_backdated_by_cutoff(self):
        result = self._review(as_of=CAPTURED)
        self.assertEqual("AFTER_PHYSICAL_KNOWLEDGE_CUTOFF", result["reason"])
        self.assertEqual(INGESTED.isoformat(), result["ingested_at"])
        self.assertIsNone(result["candidate"])
        self.assertEqual("CANDIDATE_REVIEWED", self._review(as_of=INGESTED)["status"])
        self.assertEqual("CANDIDATE_REVIEWED", self._review(as_of=None)["status"])

    def test_naive_reversed_future_capture_clocks_reject(self):
        body, metadata, selection = _fixture()
        for changes in ({"started_at": "2026-10-14T13:28:00"},
                        {"started_at": INGESTED.isoformat()},
                        {"observed_at": (INGESTED + timedelta(days=1)).isoformat()}):
            with self.subTest(changes=changes):
                self.assertEqual("REJECTED", self._review((body, {**metadata, **changes}, selection))["status"])
        self.assertEqual("REJECTED", self._review(as_of=INGESTED.replace(tzinfo=None))["status"])

    def test_metric_value_unit_operator_and_scope_must_be_literal(self):
        fixture = _fixture()
        for changes in ({"value_literal": "29"}, {"unit_literal": "countries"}, {"metric_kind": "REVENUE_SHARE"},
                        {"value_literal": "91%"}, {"scope_kind": "INFERRED_OWNERSHIP"}):
            with self.subTest(changes=changes):
                self.assertEqual("REJECTED", self._review((fixture[0], fixture[1], {**fixture[2], **changes}))["status"])
        guessed_scope = _fixture(scope="Dow Inc. has broad business operations.")
        self.assertEqual("COMBINED_REPORT_SCOPE_NOT_LITERAL", self._review(guessed_scope)["reason"])

    def test_forward_looking_financial_and_negated_operation_are_not_current_counts(self):
        for metric in ("The Company will operate 91 manufacturing sites.",
                       "The Company expects it operates 91 manufacturing sites.",
                       "The Company operates 91 manufacturing sites and sales of $40 billion.",
                       "The Company no longer operates 91 manufacturing sites.",
                       "The Company said another company operates 91 manufacturing sites."):
            with self.subTest(metric=metric):
                self.assertEqual("REJECTED", self._review(_fixture(metric=metric))["status"])

    def test_different_literal_metric_year_is_not_the_filings_fiscal_period(self):
        metric = "The Company operated 91 manufacturing sites in 2020."
        self.assertEqual("METRIC_PERIOD_REFERENCE_CONFLICT", self._review(_fixture(metric=metric))["reason"])

    def test_literal_approximation_and_commas_do_not_become_inferred_scaling(self):
        body, metadata, selection = _fixture(metric="The Company employed approximately 18,400 employees.")
        selection.update(metric_kind="EMPLOYEE_COUNT", value_literal="18,400", unit_literal="employees")
        result = self._review((body, metadata, selection))
        self.assertEqual("CANDIDATE_REVIEWED", result["status"])
        self.assertEqual("18,400", result["candidate"]["value_literal"])
        self.assertEqual("18400", result["candidate"]["value"])
        self.assertTrue(result["candidate"]["approximate_in_source"])
        self.assertFalse(result["quantity_or_revenue_share_inferred"])

    def test_original_byte_offsets_hashes_and_duplicate_passages_are_strict(self):
        fixture = _fixture()
        for span in ({**fixture[2]["metric_passage"], "sha256": "0" * 64},
                     {**fixture[2]["metric_passage"], "byte_start": -1},
                     {**fixture[2]["metric_passage"], "byte_end": len(fixture[0]) + 1}):
            with self.subTest(span=span):
                selection = {**fixture[2], "metric_passage": span}
                self.assertEqual("REJECTED", self._review((fixture[0], fixture[1], selection))["status"])
        duplicated = self._modified("</body>", f"<p>{METRIC}</p></body>")
        self.assertEqual("AMBIGUOUS_REPEATED_PASSAGE", self._review(duplicated)["reason"])

    def test_comments_scripts_and_hidden_ancestors_are_not_visible_metric_passages(self):
        hidden = _fixture(hidden=True)
        self.assertEqual("NONVISIBLE_SELECTED_PASSAGE", self._review(hidden)["reason"])
        comment = self._modified(f'<p id="metric">{METRIC}</p>', f"<!--{METRIC}-->")
        self.assertEqual("PASSAGE_NOT_ORIGINAL_VISIBLE_CONTENT", self._review(comment)["reason"])
        script = self._modified(f'<p id="metric">{METRIC}</p>', f"<script>{METRIC}</script>")
        self.assertEqual("NONVISIBLE_SELECTED_PASSAGE", self._review(script)["reason"])

    def test_xml_entity_depth_and_body_bounds_fail_closed(self):
        body, metadata, selection = _fixture()
        dangerous = self._modified('<html ', '<!DOCTYPE html [<!ENTITY x "text">]><html ')
        self.assertEqual("UNSUPPORTED_XML_ENTITY_OR_DTD", self._review(dangerous)["reason"])
        with patch.object(review, "MAX_XML_ELEMENTS", 5):
            self.assertEqual("XML_STRUCTURE_BOUND_EXCEEDED", self._review()["reason"])
        with patch.object(review, "MAX_BODY_BYTES", len(body) - 1):
            self.assertEqual("ORIGINAL_BODY_HASH_LENGTH_OR_BOUND_MISMATCH", self._review()["reason"])

    def test_identical_replay_is_deterministic_and_does_not_mutate_inputs_or_collect(self):
        fixture = _fixture()
        original = deepcopy(fixture)
        with patch("urllib.request.urlopen", side_effect=AssertionError("HTTP")), \
                patch("sqlite3.connect", side_effect=AssertionError("database")):
            first = self._review(fixture)
            second = self._review(fixture)
        self.assertEqual(first, second)
        self.assertEqual(original, fixture)
        self.assertEqual(0, first["http_attempts"])
        first["candidate"]["issuer_anchor"]["issuer_name"] = "changed callback"
        self.assertEqual("Dow Inc.", self._review(fixture)["candidate"]["issuer_anchor"]["issuer_name"])


if __name__ == "__main__":
    unittest.main()
