from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evidence.batch4_ir_20261013.build_review import build_review_artifact, lead_for_page, load_captures
from market_checker_app.models import NewsItem
from market_checker_app.services.ir_primary_verification_service import (
    CapturedIRDocument, IRPrimaryVerificationError, verify_primary_ir_lead,
)
from market_checker_app.services.news_scout_service import NewsScoutService
from market_checker_app.storage.scout_store import ScoutStore


NOW = datetime(2026, 10, 13, 13, 0, tzinfo=timezone.utc)
PUBLISHED = NOW - timedelta(days=1)
FEED = "https://publisher.test/press/feed/"
PAGE = "https://publisher.test/announcements/dividend"
AUTHORITY = "https://publisher.test/investor/default"
TITLE = "Quarterly dividend announcement"
POLICY = ("publisher.test",)
ROOT = Path(__file__).resolve().parents[1]


def _fixtures():
    feed = f"""<rss version="2.0"><channel><title>Publisher</title><link>https://publisher.test</link>
    <description>Explicit synthetic fixture</description><item><title>{TITLE}</title><link>{PAGE}</link>
    <guid>{PAGE}</guid><pubDate>{format_datetime(PUBLISHED)}</pubDate></item></channel></rss>""".encode()
    page = f"""<html><head><link rel="canonical" href="{PAGE}">
    <meta property="article:published_time" content="{PUBLISHED.isoformat()}"></head><body>
    <article><h1>{TITLE}</h1><p>The publisher announced a quarterly dividend. Details at
    <a href="{AUTHORITY}">Investor Relations</a>.</p><p>Microsoft Corp. (Nasdaq MSFT).</p></article>
    <footer>Unrelated footer assertion.</footer><script>Invented script-only assertion.</script></body></html>""".encode()
    authority = f'<html><h1>Microsoft Corp. MSFT Investor Relations</h1><a href="{PAGE}">Announcement</a></html>'.encode()
    def capture(url, body, mime="text/html"):
        return CapturedIRDocument(url, url, body, NOW, content_type=mime, capture_origin="SYNTHETIC_TEST_FIXTURE")
    lead = NewsItem("MSFT", FEED, TITLE, "RSS lead alone is unverified", PUBLISHED, 0.7, PAGE,
                    feed_url=FEED, original_url=PAGE, observed_at=NOW)
    return lead, capture(FEED, feed, "application/rss+xml"), capture(PAGE, page), capture(AUTHORITY, authority)


def _verify(lead=None, feed=None, page=None, authority=None, **kwargs):
    defaults = _fixtures()
    return verify_primary_ir_lead(
        lead or defaults[0], feed_capture=feed or defaults[1], primary_capture=page or defaults[2],
        authority_capture=authority or defaults[3], publisher_hosts=POLICY, knowledge_at=kwargs.pop("knowledge_at", NOW),
        **kwargs,
    )


class Batch4IRPrimaryVerificationTests(unittest.TestCase):
    def test_actual_captures_reproduce_source_only_positive_and_truthful_partial_control(self):
        artifact = build_review_artifact()
        self.assertEqual(json.loads((ROOT / "evidence/batch4_ir_20261013/source_review.json").read_bytes()), artifact)
        self.assertEqual(5, artifact["actual_http_requests"])
        self.assertEqual(4, artifact["raw_http_200_captures"])
        for case in artifact["positive_source_only_cases"]:
            report = case["review"]
            self.assertEqual("SOURCE_VERIFIED", report["source_verification_status"])
            self.assertTrue(report["quoted_claim"]["quote_matches_primary_article"])
            self.assertFalse(report["quoted_claim"]["claim_truth_independently_verified"])
            self.assertFalse(report["issuer_identity_verified"])
            self.assertFalse(report["issuer_attribution_allowed"])
            self.assertFalse(report["scoring_applied"])
            self.assertFalse(report["feed_scope"]["all_publisher_history_complete"])
            self.assertEqual(10, report["feed_scope"]["entries_examined"])
        control = artifact["actual_partial_control"]["review"]
        self.assertEqual("PARTIAL", control["source_verification_status"])
        self.assertIn("RSS_ORIGINAL_PRIMARY_URL_MISMATCH", control["reasons"])
        self.assertIn("PRIMARY_ARTICLE_BODY_UNAVAILABLE", control["reasons"])

    def test_actual_ir_short_link_is_captured_provenance_not_invented_alias(self):
        report = build_review_artifact()["positive_source_only_cases"][1]["review"]
        self.assertEqual("https://aka.ms/AA13ep0o", report["captures"]["primary"]["requested_url"])
        self.assertTrue(report["publisher_ir_link"]["authority_cites_exact_requested_alias"])
        self.assertTrue(report["captures"]["primary"]["resolved_url"].endswith("dividend-increase-7/"))
        self.assertFalse(report["issuer_attribution_allowed"])

    def test_actual_publication_clock_is_not_backdated_capture_availability(self):
        report = build_review_artifact()["positive_source_only_cases"][0]["review"]
        self.assertEqual(["Wed, 07 Oct 2026 20:17:43 +0000"], report["publication_clocks"]["rss_raw"])
        self.assertEqual(["2026-10-07T20:17:43+00:00"], report["publication_clocks"]["primary_raw"])
        self.assertTrue(report["source_available_at"].startswith("2026-10-13"))
        self.assertFalse(report["publication_clocks"]["exact_first_dissemination_verified"])

    def test_verifier_does_no_http_or_quota_operations_and_never_mutates_sentiment(self):
        lead, feed, page, authority = _fixtures()
        with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            report = _verify(lead, feed, page, authority)
        self.assertTrue(report["source_verified"])
        self.assertEqual(0, report["network_requests_performed"])
        self.assertFalse(report["quota_or_lease_modified"])
        self.assertEqual(0.7, lead.sentiment_weight)
        self.assertFalse(report["scoring_applied"])

    def test_correct_publisher_page_does_not_verify_wrong_ticker_or_legal_entity(self):
        lead = _fixtures()[0]
        lead.ticker = "AAPL"
        report = _verify(lead=lead)
        self.assertTrue(report["source_verified"])
        self.assertEqual([], report["issuer_as_named_mentions"])
        self.assertEqual("TICKER_HINT_ONLY", report["issuer_identity_status"])
        self.assertFalse(report["issuer_attribution_allowed"])

    def test_name_only_ir_homepage_cannot_be_original_announcement(self):
        authority = _fixtures()[3]
        report = _verify(page=authority)
        self.assertEqual("PARTIAL", report["source_verification_status"])
        self.assertFalse(report["source_verified"])
        self.assertFalse(report["quoted_claim"]["quote_matches_primary_article"])

    def test_exact_title_and_canonical_url_are_required(self):
        lead, feed, page, authority = _fixtures()
        for changed_body, expected in (
            (page.body.replace(TITLE.encode(), b"Another announcement"), "PRIMARY_ARTICLE_HEADLINE_MISMATCH_OR_MISSING"),
            (page.body.replace(f'href="{PAGE}"'.encode(), b'href="https://publisher.test/another"'),
             "PRIMARY_CANONICAL_URL_MISMATCH_OR_MISSING"),
        ):
            with self.subTest(expected=expected):
                report = _verify(page=replace(page, body=changed_body))
                self.assertIn(expected, report["reasons"])
                self.assertFalse(report["source_verified"])

    def test_original_url_mismatch_is_not_repaired_by_matching_name_title_or_host(self):
        page = _fixtures()[2]
        report = _verify(page=replace(page, resolved_url=PAGE + "/other"))
        self.assertIn("RSS_ORIGINAL_PRIMARY_URL_MISMATCH", report["reasons"])
        self.assertFalse(report["issuer_attribution_allowed"])

    def test_unknown_request_alias_does_not_inherit_resolved_page_authority_link(self):
        page = _fixtures()[2]
        report = _verify(page=replace(page, requested_url="https://unrelated.test/redirect"))
        self.assertIn("PRIMARY_REQUEST_ALIAS_NOT_CITED_BY_IR_PORTAL", report["reasons"])
        self.assertFalse(report["source_verified"])

    def test_lookalike_publisher_host_cannot_use_official_page_name(self):
        page = _fixtures()[2]
        report = _verify(page=replace(page, resolved_url="https://publisher.test.evil.test/announcements/dividend"))
        self.assertIn("PRIMARY_HOST_OUTSIDE_PUBLISHER_POLICY", report["reasons"])
        self.assertFalse(report["source_verified"])

    def test_unmatched_bounded_rss_capture_is_partial_not_complete_negative_search(self):
        feed = _fixtures()[1]
        report = _verify(feed=replace(feed, body=feed.body.replace(PAGE.encode(), b"https://publisher.test/another")))
        self.assertIn("RSS_LEAD_NOT_IN_BOUNDED_CAPTURE", report["reasons"])
        self.assertEqual("PARTIAL", report["source_verification_status"])
        self.assertFalse(report["feed_scope"]["all_publisher_history_complete"])

    def test_same_day_different_publication_clock_is_retained_as_conflict(self):
        page = _fixtures()[2]
        raw = PUBLISHED.isoformat()
        changed = (PUBLISHED + timedelta(minutes=1)).isoformat()
        report = _verify(page=replace(page, body=page.body.replace(raw.encode(), changed.encode())))
        self.assertIn("PRIMARY_RSS_PUBLICATION_TIME_CONFLICT", report["reasons"])
        self.assertEqual([changed], report["publication_clocks"]["primary_raw"])
        self.assertFalse(report["source_verified"])

    def test_future_publication_metadata_does_not_become_capture_knowledge(self):
        page = _fixtures()[2]
        raw = (NOW + timedelta(days=1)).isoformat()
        report = _verify(page=replace(page, body=page.body.replace(PUBLISHED.isoformat().encode(), raw.encode())))
        self.assertIn("PRIMARY_PUBLICATION_CAPTURE_CLOCK_CONFLICT", report["reasons"])
        self.assertEqual([raw], report["publication_clocks"]["primary_raw"])
        self.assertFalse(report["source_verified"])

    def test_timezone_missing_and_future_capture_are_rejected_without_exposing_quotes(self):
        page = _fixtures()[2]
        with self.assertRaises(IRPrimaryVerificationError):
            _verify(knowledge_at=NOW.replace(tzinfo=None))
        with self.assertRaisesRegex(IRPrimaryVerificationError, "future capture"):
            _verify(page=replace(page, observed_at=NOW + timedelta(seconds=1)))

    def test_date_only_metadata_and_http_date_cannot_replace_source_publication_time(self):
        page = _fixtures()[2]
        report = _verify(page=replace(page, body=page.body.replace(PUBLISHED.isoformat().encode(), b"2026-10-12"),
                                     http_date=format_datetime(PUBLISHED)))
        self.assertIn("PRIMARY_PUBLICATION_TIME_UNRESOLVED", report["reasons"])
        self.assertFalse(report["source_verified"])
        self.assertTrue(report["publication_clocks"]["http_dates_are_not_publication_clocks"])

    def test_footer_and_script_claims_are_not_article_claim_provenance(self):
        for quote in ("Unrelated footer assertion.", "Invented script-only assertion."):
            with self.subTest(quote=quote):
                report = _verify(claim_quote=quote)
                self.assertIn("CLAIM_QUOTE_ABSENT_FROM_PRIMARY_ARTICLE", report["reasons"])
                self.assertFalse(report["quoted_claim"]["claim_truth_independently_verified"])

    def test_http_errors_wrong_mime_and_unlinked_authority_are_partial(self):
        _, _, page, authority = _fixtures()
        for changed, expected in (
            (replace(page, http_status=404), "PRIMARY_HTTP_NOT_SUCCESSFUL"),
            (replace(page, content_type="application/pdf"), "PRIMARY_CONTENT_TYPE_NOT_HTML"),
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, _verify(page=changed)["reasons"])
        page = replace(page, body=page.body.replace(AUTHORITY.encode(), b"https://publisher.test/other"))
        authority = replace(authority, body=b"<html><p>Microsoft Corp MSFT</p></html>")
        self.assertIn("IR_PORTAL_LINK_SCOPE_UNRESOLVED", _verify(page=page, authority=authority)["reasons"])

    def test_offline_capture_origin_unsafe_urls_size_and_parser_complexity_fail_closed(self):
        page = _fixtures()[2]
        for changed in (replace(page, capture_origin="LIVE"), replace(page, body=b"a" * 2_000_001),
                        replace(page, resolved_url="https://localhost/story"),
                        replace(page, body=b"<div>" * 131 + b"body")):
            with self.subTest(changed=changed.capture_origin), self.assertRaises(ValueError):
                _verify(page=changed)

    def test_feed_dtd_is_rejected_without_external_request(self):
        feed = _fixtures()[1]
        with self.assertRaisesRegex(IRPrimaryVerificationError, "DTD"):
            _verify(feed=replace(feed, body=b'<!DOCTYPE rss SYSTEM "https://unrelated.test/a"><rss/>'))

    def test_scout_persists_source_only_review_unverified_and_preserves_original_lead(self):
        lead, feed, page, authority = _fixtures()
        with tempfile.TemporaryDirectory() as directory, patch(
                "market_checker_app.services.news_scout_service._local_ingestion_clock", return_value=NOW):
            store = ScoutStore(Path(directory) / "scout.db")
            service = NewsScoutService(store)
            service.ingest([lead], allowed_tickers=["MSFT"])
            original = store.findings_as_of("MSFT", as_of=NOW)[0]
            report = service.verify_primary_candidate(
                original["finding_id"], ticker="MSFT", feed_capture=feed, primary_capture=page,
                authority_capture=authority, publisher_hosts=POLICY, as_of=NOW,
            )
            self.assertTrue(report["created"])
            self.assertEqual("SOURCE_VERIFIED", report["primary_source_review"]["source_verification_status"])
            self.assertEqual("UNVERIFIED", report["verification_status"])
            self.assertFalse(store.has_findings(["MSFT"], as_of=NOW))
            self.assertEqual(original, store.finding_by_id(original["finding_id"], subject_id="MSFT"))
            self.assertEqual(1, len(store.open_leads(["MSFT"], as_of=NOW)))
            later = service.verify_primary_candidate(
                original["finding_id"], ticker="MSFT", feed_capture=feed, primary_capture=page,
                authority_capture=authority, publisher_hosts=POLICY, as_of=NOW + timedelta(minutes=1),
            )
            self.assertFalse(later["created"])
            self.assertEqual(report["finding_id"], later["finding_id"])
            saved = json.loads(store.finding_by_id(report["finding_id"], subject_id="MSFT")["details_json"])
            self.assertEqual("ir_primary_source_review", saved["stage"])
            self.assertFalse(saved["issuer_attribution_allowed"])
            self.assertFalse(saved["scoring_applied"])

    def test_scout_rejects_foreign_future_or_nested_review_candidate(self):
        lead, feed, page, authority = _fixtures()
        with tempfile.TemporaryDirectory() as directory, patch(
                "market_checker_app.services.news_scout_service._local_ingestion_clock", return_value=NOW):
            store = ScoutStore(Path(directory) / "scout.db")
            service = NewsScoutService(store)
            service.ingest([lead], allowed_tickers=["MSFT"])
            original = store.findings_as_of("MSFT", as_of=NOW)[0]
            def review(identifier, ticker="MSFT", clock=NOW):
                return service.verify_primary_candidate(identifier, ticker=ticker, feed_capture=feed,
                    primary_capture=page, authority_capture=authority, publisher_hosts=POLICY, as_of=clock)
            with self.assertRaises(ValueError): review(original["finding_id"], ticker="AAPL")
            with self.assertRaises(ValueError): review(original["finding_id"], clock=NOW - timedelta(seconds=1))
            result = review(original["finding_id"])
            with self.assertRaises(ValueError): review(result["finding_id"])

    def test_actual_pinned_capture_can_be_imported_without_changing_provider_ledgers(self):
        _, captures = load_captures()
        page = captures["ir_linked_announcement.html"]
        lead = lead_for_page(captures, page)
        clock = max(row.observed_at for row in captures.values())
        with tempfile.TemporaryDirectory() as directory, patch(
                "market_checker_app.services.news_scout_service._local_ingestion_clock", return_value=clock):
            store = ScoutStore(Path(directory) / "scout.db")
            service = NewsScoutService(store)
            service.ingest([lead], allowed_tickers=["MSFT"])
            candidate = store.findings_as_of("MSFT", as_of=clock)[0]
            report = service.verify_primary_candidate(candidate["finding_id"], ticker="MSFT",
                feed_capture=captures["press_releases_feed.xml"], primary_capture=page,
                authority_capture=captures["ir_authority.html"], publisher_hosts=("www.microsoft.com", "news.microsoft.com"),
                as_of=clock)
            self.assertEqual("SOURCE_VERIFIED", report["primary_source_review"]["source_verification_status"])
            self.assertFalse(store.has_findings(["MSFT"], as_of=clock))
            self.assertFalse(report["primary_source_review"]["quota_or_lease_modified"])

    def test_actual_style_pinned_replay_cutoff_cannot_backdate_local_knowledge(self):
        _, captures = load_captures()
        page = captures["ir_linked_announcement.html"]
        lead = lead_for_page(captures, page)
        cutoff = max(row.observed_at for row in captures.values())
        physical_import = cutoff + timedelta(days=1)
        with tempfile.TemporaryDirectory() as directory, patch(
                "market_checker_app.services.news_scout_service._local_ingestion_clock", return_value=physical_import):
            store = ScoutStore(Path(directory) / "scout.db")
            service = NewsScoutService(store)
            service.ingest([lead], allowed_tickers=["MSFT"])
            original = store.findings_as_of("MSFT", as_of=cutoff)[0]
            imported = service.verify_primary_candidate(original["finding_id"], ticker="MSFT",
                feed_capture=captures["press_releases_feed.xml"], primary_capture=page,
                authority_capture=captures["ir_authority.html"], publisher_hosts=("www.microsoft.com", "news.microsoft.com"),
                as_of=cutoff)
            row = store.finding_by_id(imported["finding_id"], subject_id="MSFT")
            self.assertEqual(physical_import, datetime.fromisoformat(row["available_at"]))
            self.assertEqual(physical_import, datetime.fromisoformat(row["first_observed_at"]))
            report = imported["primary_source_review"]
            self.assertEqual(cutoff.isoformat(), report["reviewed_at"])
            self.assertEqual(physical_import.isoformat(), report["local_import_completed_at"])
            self.assertTrue(report["requested_cutoff_is_not_local_availability"])
            self.assertNotIn(imported["finding_id"],
                             {item["finding_id"] for item in store.findings_as_of("MSFT", as_of=cutoff)})
            self.assertIn(imported["finding_id"],
                          {item["finding_id"] for item in store.findings_as_of("MSFT", as_of=physical_import)})
            self.assertEqual(original, store.finding_by_id(original["finding_id"], subject_id="MSFT"))

    def test_requested_future_cutoff_is_not_first_local_availability_and_repeat_is_idempotent(self):
        lead, feed, page, authority = _fixtures()
        with tempfile.TemporaryDirectory() as directory, patch(
                "market_checker_app.services.news_scout_service._local_ingestion_clock", return_value=NOW):
            store = ScoutStore(Path(directory) / "scout.db")
            service = NewsScoutService(store)
            service.ingest([lead], allowed_tickers=["MSFT"])
            candidate = store.findings_as_of("MSFT", as_of=NOW)[0]
            def import_at(cutoff):
                return service.verify_primary_candidate(candidate["finding_id"], ticker="MSFT", feed_capture=feed,
                    primary_capture=page, authority_capture=authority, publisher_hosts=POLICY, as_of=cutoff)
            first = import_at(NOW + timedelta(days=5))
            self.assertEqual(NOW, datetime.fromisoformat(first["persisted_available_at"]))
            with patch("market_checker_app.services.news_scout_service._local_ingestion_clock",
                       return_value=NOW + timedelta(days=2)):
                repeated = import_at(NOW + timedelta(days=6))
            self.assertFalse(repeated["created"])
            self.assertEqual(first["persisted_available_at"], repeated["persisted_available_at"])
            self.assertEqual(first["finding_id"], repeated["finding_id"])


if __name__ == "__main__":
    unittest.main()
