from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from contextlib import closing, contextmanager, redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from market_checker_app.ir_primary_runner import main
from market_checker_app.models import NewsItem
from market_checker_app.services.ir_primary_scout_service import (
    IRHTTPResponse, IRPrimaryHTTPClient, IRPrimaryScoutService, _has_sensitive_body,
)
from market_checker_app.services.news_scout_service import NewsScoutService
from market_checker_app.storage.scout_store import ScoutStore
from evidence.batch5_ir_20261014.build_review import build_review_artifact, load_captures


NOW = datetime(2026, 10, 14, 0, 5, tzinfo=timezone.utc)
PUBLISHED = NOW - timedelta(days=1)
FEED = "https://publisher.test/press/feed/"
PAGE = "https://publisher.test/announcements/dividend"
AUTHORITY = "https://publisher.test/investor/default"
HOSTS = ("publisher.test",)
TITLE = "Quarterly dividend announcement"
EVIDENCE = Path(__file__).resolve().parents[1] / "evidence/batch5_ir_20261014"


@contextmanager
def _fixture_connection(db_path):
    # SQLite's transaction context does not release its file handle. Close
    # after its normal commit/rollback, including failures inside the fixture.
    with closing(sqlite3.connect(db_path)) as connection:
        with connection:
            yield connection


def responses():
    feed = (f'<rss version="2.0"><channel><title>Publisher</title><link>https://publisher.test</link>'
            f'<description>Explicit synthetic fixture</description><item><title>{TITLE}</title>'
            f'<link>{PAGE}</link><pubDate>{format_datetime(PUBLISHED)}</pubDate></item></channel></rss>').encode()
    page = (f'<html><head><link rel="canonical" href="{PAGE}">'
            f'<meta property="article:published_time" content="{PUBLISHED.isoformat()}"></head>'
            f'<body><article><h1>{TITLE}</h1><p>The publisher announced a quarterly dividend. '
            f'<a href="{AUTHORITY}">Investor Relations</a>.</p>'
            '<p>Microsoft Corp. (Nasdaq MSFT).</p></article></body></html>').encode()
    authority = f'<html><h1>Microsoft Investor Relations</h1><a href="{PAGE}">Announcement</a></html>'.encode()
    return {FEED: IRHTTPResponse(200, feed, NOW, "application/rss+xml"),
            AUTHORITY: IRHTTPResponse(200, authority, NOW), PAGE: IRHTTPResponse(200, page, NOW)}


class Transport:
    def __init__(self, fixtures=None, callback=None):
        self.fixtures = fixtures or responses()
        self.calls = []
        self.callback = callback

    def fetch(self, url, **kwargs):
        self.calls.append(url)
        if self.callback:
            self.callback(url)
        value = self.fixtures[url]
        if isinstance(value, Exception):
            raise value
        return value


class Batch5IRPrimaryScoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ScoutStore(self.root / "shared.db")
        self.clock = patch("market_checker_app.services.ir_primary_scout_service._physical_clock", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.ingestion = patch("market_checker_app.services.news_scout_service._local_ingestion_clock", return_value=NOW)
        self.ingestion.start()
        self.addCleanup(self.ingestion.stop)

    def run_service(self, *, client=None, ticker="MSFT", **kwargs):
        service = IRPrimaryScoutService(self.store, client=client or Transport(), **kwargs)
        return service.run(ticker, feed_url=FEED, authority_url=AUTHORITY,
                           publisher_hosts=HOSTS, original_url=PAGE, requested_as_of=PUBLISHED)

    def request_rows(self):
        with _fixture_connection(self.store.db_path) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute("SELECT * FROM scout_public_requests ORDER BY rowid")]

    def test_positive_retains_exact_original_bytes_but_never_verifies_issuer_or_scores(self):
        report = self.run_service()
        self.assertEqual("SOURCE_VERIFIED", report["status"])
        self.assertEqual("TEST_TRANSPORT", report["runtime_mode"])
        self.assertEqual(3, report["attempted_requests"])
        self.assertEqual(3, report["daily_requests"])
        self.assertFalse(report["issuer_identity_verified"])
        self.assertFalse(report["issuer_attribution_allowed"])
        self.assertFalse(report["scoring_applied"])
        self.assertEqual("UNVERIFIED", report["database_verification_status"])
        self.assertEqual(["RETURNED"] * 3, [row["outcome"] for row in self.request_rows()])
        archive = Path(report["archive_directory"])
        self.assertEqual(report, json.loads((archive / "run.json").read_bytes()))
        fixtures = responses()
        for capture in report["capture_receipts"]:
            raw = (archive / capture["raw_path"]).read_bytes()
            self.assertEqual(fixtures[capture["requested_url"]].body, raw)
            self.assertEqual(capture["sha256"], hashlib.sha256(raw).hexdigest())
        for row in self.store.findings_as_of("MSFT", as_of=NOW):
            self.assertEqual("UNVERIFIED", row["verification_status"])

    def test_physical_reservation_and_import_are_unavailable_at_requested_old_cutoff(self):
        report = self.run_service()
        self.assertEqual(PUBLISHED.isoformat(), report["requested_as_of"])
        self.assertEqual(NOW.isoformat(), report["persisted_available_at"])
        self.assertEqual([], self.store.findings_as_of("MSFT", as_of=PUBLISHED))
        self.assertEqual([NOW.isoformat()] * 3, [row["attempted_at"] for row in self.request_rows()])

    def test_existing_lead_is_preserved_and_new_review_remains_a_sibling(self):
        news = NewsScoutService(self.store)
        news.ingest([NewsItem("MSFT", FEED, TITLE, "lead", PUBLISHED, .8, PAGE,
                            feed_url=FEED, original_url=PAGE, observed_at=PUBLISHED)], allowed_tickers=["MSFT"])
        lead = self.store.findings_as_of("MSFT", as_of=PUBLISHED)[0]
        report = IRPrimaryScoutService(self.store, client=Transport()).run(
            "MSFT", feed_url=FEED, authority_url=AUTHORITY, publisher_hosts=HOSTS,
            candidate_id=lead["finding_id"], requested_as_of=PUBLISHED)
        self.assertEqual("SOURCE_VERIFIED", report["status"])
        self.assertEqual(lead, self.store.finding_by_id(lead["finding_id"], subject_id="MSFT"))
        self.assertNotEqual(lead["finding_id"], report["review_id"])

    def test_restart_and_changed_scope_cannot_reset_same_day_subject_attempt(self):
        first = self.run_service()
        client = Transport()
        reloaded = ScoutStore(self.store.db_path)
        second = IRPrimaryScoutService(reloaded, client=client).run(
            "MSFT", feed_url=FEED, authority_url=AUTHORITY, publisher_hosts=HOSTS,
            original_url="https://publisher.test/another")
        self.assertEqual("WAIT_QUOTA", second["status"])
        self.assertEqual([], client.calls)
        self.assertEqual(3, second["daily_requests"])
        self.assertEqual(0, second["attempted_requests"])
        self.assertEqual(1, len(list((self.root / "ir_primary_captures").iterdir())))
        self.assertTrue(Path(first["archive_directory"]).is_dir())

    def test_reservation_exists_before_transport_and_provider_is_exclusive(self):
        def inspect(_):
            rows = self.request_rows()
            self.assertEqual("RUNNING", rows[-1]["outcome"])
            self.assertIsNone(self.store.claim_provider("rss", as_of=NOW, seconds=60))
        report = self.run_service(client=Transport(callback=inspect))
        self.assertEqual("SOURCE_VERIFIED", report["status"])

    def test_busy_provider_performs_no_io(self):
        self.store.claim_provider("rss", as_of=NOW, seconds=60)
        client = Transport()
        report = self.run_service(client=client)
        self.assertEqual("BUSY", report["status"])
        self.assertEqual([], client.calls)
        self.assertEqual([], self.request_rows())

    def test_shared_daily_request_ceiling_is_persistent_across_subjects(self):
        self.assertEqual("SOURCE_VERIFIED", self.run_service(ticker="MSFT")["status"])
        self.assertEqual("SOURCE_VERIFIED", self.run_service(ticker="AAPL")["status"])
        client = Transport()
        report = self.run_service(client=client, ticker="NVDA")
        self.assertEqual("WAIT_QUOTA", report["status"])
        self.assertEqual(6, report["daily_requests"])
        self.assertEqual([], client.calls)
        self.assertFalse(report["issuer_attribution_allowed"])

    def test_http_failure_consumes_request_and_preserves_prior_completed_capture(self):
        fixture = responses()
        fixture[AUTHORITY] = replace(fixture[AUTHORITY], status=404, body=b"Not found")
        report = self.run_service(client=Transport(fixture))
        self.assertEqual("PARTIAL", report["status"])
        self.assertEqual("HTTP_404", report["stop_reason"])
        self.assertEqual(["RETURNED", "FAILED"], [row["outcome"] for row in self.request_rows()])
        self.assertEqual(2, len(report["capture_receipts"]))
        self.assertEqual([], self.store.findings_as_of("MSFT", as_of=NOW))

    def test_429_establishes_persistent_cooldown_and_retry_never_fetches(self):
        fixture = responses()
        fixture[FEED] = replace(fixture[FEED], status=429, body=b"Retry later", retry_after="7200")
        report = self.run_service(client=Transport(fixture))
        self.assertEqual("RATE_LIMITED", report["status"])
        self.assertEqual((NOW + timedelta(hours=2)).isoformat(), report["retry_at"])
        client = Transport()
        blocked = self.run_service(client=client, ticker="AAPL")
        self.assertEqual("RATE_LIMITED", blocked["status"])
        self.assertEqual([], client.calls)
        self.assertEqual(1, len(self.request_rows()))

    def test_redirect_hops_are_separately_charged_before_io(self):
        fixture = responses()
        destination = FEED + "original"
        fixture[destination] = fixture[FEED]
        fixture[FEED] = replace(fixture[FEED], status=302, body=b"Redirect", location=destination)
        report = self.run_service(client=Transport(fixture))
        self.assertEqual(4, report["attempted_requests"])
        self.assertEqual("SOURCE_VERIFIED", report["status"])
        self.assertEqual([302, 200, 200, 200], [r["status"] for r in report["capture_receipts"]])
        self.assertEqual(destination, report["primary_source_review"]["captures"]["feed"]["resolved_url"])

    def test_unsafe_redirect_is_not_requested_or_exported(self):
        fixture = responses()
        forbidden = "https://publisher.test/else?access_token=DO_NOT_EXPORT_THIS_VALUE"
        fixture[FEED] = replace(fixture[FEED], status=302, body=b"Redirect", location=forbidden)
        client = Transport(fixture)
        report = self.run_service(client=client)
        self.assertEqual([FEED], client.calls)
        self.assertEqual("PARTIAL", report["status"])
        self.assertNotIn("DO_NOT_EXPORT", json.dumps(report))

    def test_entire_sensitive_body_is_withheld_no_cleaned_or_encoded_copy(self):
        fixture = responses()
        marker = "SYNTHETIC_PRIVATE_VALUE_12345678"
        raw = b'<html><p>public prefix</p><script>clientSecret="' + marker.encode() + b'";</script></html>'
        fixture[AUTHORITY] = replace(fixture[AUTHORITY], body=raw)
        report = self.run_service(client=Transport(fixture))
        capture = next(row for row in report["capture_receipts"] if row["role"] == "authority")
        self.assertEqual("WITHHELD_SENSITIVE_BODY", capture["retention"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), capture["sha256"])
        self.assertNotIn("raw_path", capture)
        self.assertEqual(3, report["attempted_requests"])
        self.assertTrue(report["authority_review_blocked"])
        self.assertEqual("ORIGINAL_BYTES_ARCHIVED", report["capture_receipts"][-1]["retention"])
        self.assertEqual("PARTIAL", report["partial_control"]["review"]["source_verification_status"])
        self.assertIn("IR_PORTAL_LINK_SCOPE_UNRESOLVED", report["partial_control"]["review"]["reasons"])
        self.assertNotIn(marker, json.dumps(report))
        for file in Path(report["archive_directory"]).iterdir():
            self.assertNotIn(marker.encode(), file.read_bytes())
        self.assertEqual([], self.store.findings_as_of("MSFT", as_of=NOW))

    def test_encoded_body_is_withheld_without_decoding_to_evade_body_gate(self):
        fixture = responses()
        fixture[AUTHORITY] = replace(fixture[AUTHORITY], body=b"compressed-original", content_encoding="gzip")
        report = self.run_service(client=Transport(fixture))
        capture = next(row for row in report["capture_receipts"] if row["role"] == "authority")
        self.assertEqual("WITHHELD_ENCODED_BODY", capture["retention"])
        self.assertNotIn("raw_path", capture)
        self.assertEqual(hashlib.sha256(b"compressed-original").hexdigest(), capture["sha256"])

    def test_lease_loss_after_complete_capture_keeps_bytes_but_prevents_import(self):
        def lose(url):
            if url == PAGE:
                with _fixture_connection(self.store.db_path) as connection:
                    connection.execute("DELETE FROM scout_provider_leases WHERE source='rss'")
        report = self.run_service(client=Transport(callback=lose))
        self.assertEqual("LEASE_LOST", report["status"])
        self.assertEqual(3, len(report["capture_receipts"]))
        self.assertEqual([], self.store.findings_as_of("MSFT", as_of=NOW))

    def test_fixture_connections_close_after_real_ledger_read_and_lease_loss_write(self):
        original_connect = sqlite3.connect
        held_connections = []
        class TrackingConnection(sqlite3.Connection):
            close_calls = 0
            def close(self):
                self.close_calls += 1
                super().close()
        def connect(*args, **kwargs):
            if "factory" in kwargs:
                # Production ScoutStore already supplies its closing factory.
                return original_connect(*args, **kwargs)
            connection = original_connect(*args, **kwargs, factory=TrackingConnection)
            held_connections.append(connection)
            return connection
        with patch("sqlite3.connect", side_effect=connect):
            self.assertEqual([], self.request_rows())
            # Exercise the actual fixture callback and retain every connection
            # strongly; garbage collection cannot make this regression pass.
            self.test_lease_loss_after_complete_capture_keeps_bytes_but_prevents_import()
        self.assertEqual(2, len(held_connections))
        for connection in held_connections:
            self.assertEqual(1, connection.close_calls)
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_fixture_connection_closes_after_commit_and_exception_rollback(self):
        original_connect = sqlite3.connect
        held_connections = []
        class TrackingConnection(sqlite3.Connection):
            close_calls = 0
            def close(self):
                self.close_calls += 1
                super().close()
        def connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs, factory=TrackingConnection)
            held_connections.append(connection)
            return connection
        with patch("sqlite3.connect", side_effect=connect):
            with _fixture_connection(self.store.db_path) as connection:
                connection.execute("CREATE TABLE fixture_close_guard(value TEXT)")
                connection.execute("INSERT INTO fixture_close_guard VALUES('committed')")
            with self.assertRaisesRegex(RuntimeError, "synthetic transaction failure"):
                with _fixture_connection(self.store.db_path) as connection:
                    connection.execute("INSERT INTO fixture_close_guard VALUES('rolled-back')")
                    raise RuntimeError("synthetic transaction failure")
            with _fixture_connection(self.store.db_path) as connection:
                self.assertEqual([("committed",)], connection.execute("SELECT value FROM fixture_close_guard").fetchall())
        self.assertEqual(3, len(held_connections))
        for connection in held_connections:
            self.assertEqual(1, connection.close_calls)
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_request_limit_stops_before_io_and_retains_completed_raw(self):
        client = Transport()
        report = self.run_service(client=client, max_requests=2)
        self.assertEqual("RUN_REQUEST_LIMIT", report["stop_reason"])
        self.assertEqual([FEED, AUTHORITY], client.calls)
        self.assertEqual(2, report["attempted_requests"])
        self.assertEqual(2, len(report["capture_receipts"]))
        self.assertEqual([], self.store.findings_as_of("MSFT", as_of=NOW))

    def test_feed_missing_selected_link_is_only_a_bounded_partial(self):
        fixture = responses()
        fixture[FEED] = replace(fixture[FEED], body=fixture[FEED].body.replace(PAGE.encode(), b"https://publisher.test/other"))
        report = self.run_service(client=Transport(fixture))
        self.assertEqual("SELECTED_URL_NOT_UNIQUE_IN_BOUNDED_FEED", report["stop_reason"])
        self.assertFalse(report["scope"]["complete_negative_issuer_search"])
        self.assertFalse(report["scope"]["all_publisher_history_complete"])
        self.assertEqual(1, report["attempted_requests"])

    def test_actual_byte_reuse_control_is_partial_and_does_no_extra_requests(self):
        report = self.run_service()
        control = report["partial_control"]
        self.assertEqual(0, control["additional_http_requests"])
        self.assertEqual("PARTIAL", control["review"]["source_verification_status"])
        self.assertIn("RSS_ORIGINAL_PRIMARY_URL_MISMATCH", control["review"]["reasons"])
        self.assertFalse(control["review"]["issuer_attribution_allowed"])
        self.assertEqual(3, report["attempted_requests"])

    def test_midnight_reservations_follow_each_physical_utc_day(self):
        before = NOW.replace(hour=23, minute=59, second=59) - timedelta(days=1)
        midnight = NOW.replace(hour=0, minute=0, second=0)
        clock = [before]
        fixture = {url: replace(response, observed_at=midnight) for url, response in responses().items()}
        def cross(url):
            if url == FEED:
                clock[0] = midnight
        with patch("market_checker_app.services.ir_primary_scout_service._physical_clock", side_effect=lambda: clock[0]), \
                patch("market_checker_app.services.news_scout_service._local_ingestion_clock", return_value=midnight):
            report = self.run_service(client=Transport(fixture, callback=cross))
        self.assertEqual("SOURCE_VERIFIED", report["status"])
        rows = self.request_rows()
        self.assertEqual(before.isoformat(), rows[0]["attempted_at"])
        self.assertEqual([midnight.isoformat()] * 2, [row["attempted_at"] for row in rows[1:]])
        self.assertEqual(1, self.store.public_daily_usage("rss", as_of=before)["daily_requests"])
        self.assertEqual(2, self.store.public_daily_usage("rss", as_of=NOW)["daily_requests"])

    def test_noncanonical_hint_is_rejected_before_lease_quota_or_http(self):
        client = Transport()
        with self.assertRaisesRegex(ValueError, "canonical 687"):
            self.run_service(client=client, ticker="NOT_IN_THE_UNIVERSE")
        self.assertEqual([], client.calls)
        self.assertEqual([], self.request_rows())

    def test_incomplete_transport_failure_consumes_quota_but_archives_no_partial_body(self):
        fixture = responses()
        fixture[AUTHORITY] = TimeoutError("sensitive exception text never exported")
        report = self.run_service(client=Transport(fixture))
        self.assertEqual("NO_COMPLETE_BODY", report["capture_receipts"][-1]["retention"])
        self.assertEqual("FAILED", self.request_rows()[-1]["outcome"])
        self.assertNotIn("sensitive exception text", json.dumps(report))
        self.assertNotIn("raw_path", report["capture_receipts"][-1])

    def test_production_client_bounds_original_bytes_and_does_not_auto_redirect(self):
        class Response:
            code = 200
            headers = {"Content-Type": "text/html", "Content-Length": "10"}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, size): return b"short" if not hasattr(self, "read_done") else b""
        response = Response()
        original = response.read
        def read(size):
            body = original(size)
            response.read_done = True
            return body
        response.read = read
        with patch("market_checker_app.services.ir_primary_scout_service.build_opener") as opener:
            opener.return_value.open.return_value = response
            with self.assertRaisesRegex(ValueError, "complete declared body"):
                IRPrimaryHTTPClient().fetch(FEED, timeout=1, max_bytes=100)
            handlers = opener.call_args.args
            self.assertEqual(["_NoRedirect"], [type(handler).__name__ for handler in handlers])
            self.assertEqual(1, opener.return_value.open.call_args.kwargs["timeout"])

    def test_streaming_deadline_stops_before_another_body_read(self):
        class Response:
            code = 200
            headers = {"Content-Type": "text/html"}
            fp = None
            reads = 0
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, size):
                self.reads += 1
                return b"streamed"
        response = Response()
        with patch("market_checker_app.services.ir_primary_scout_service.build_opener") as opener, \
                patch("market_checker_app.services.ir_primary_scout_service.time.perf_counter", side_effect=[0, .2, 1.1]):
            opener.return_value.open.return_value = response
            with self.assertRaisesRegex(TimeoutError, "deadline"):
                IRPrimaryHTTPClient().fetch(FEED, timeout=1, max_bytes=100)
        self.assertEqual(1, response.reads)

    def test_unsafe_feed_entity_declaration_stops_after_its_single_charged_capture(self):
        fixture = responses()
        fixture[FEED] = replace(fixture[FEED], body=b'<!DOCTYPE rss [<!ENTITY secret SYSTEM "file:///private">]><rss/>')
        client = Transport(fixture)
        report = self.run_service(client=client)
        self.assertEqual("UNSAFE_FEED_ENTITY_DECLARATION", report["stop_reason"])
        self.assertEqual([FEED], client.calls)
        self.assertEqual(1, report["attempted_requests"])
        self.assertEqual([], self.store.findings_as_of("MSFT", as_of=NOW))

    def test_secret_gate_includes_html_entity_assignment_and_signed_urls(self):
        for raw in (b'apiKey=&quot;SECRET_ASSIGNED_VALUE123&quot;',
                    b'https://example.test/object?X-Amz-Signature=abcdefghijklmnopqrst',
                    b'Authorization: Bearer abcdefghijklmnopqrst',
                    b'-----BEGIN PRIVATE KEY-----'):
            self.assertTrue(_has_sensitive_body(raw))
        self.assertFalse(_has_sensitive_body(b"Public article mentions password policy, API keys, and token security."))

    def test_cli_refuses_new_ledger_before_any_network(self):
        with patch("market_checker_app.services.ir_primary_scout_service.IRPrimaryHTTPClient.fetch", side_effect=AssertionError("no network")), \
                redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                main(["--db", str(self.root / "missing.db"), "--ticker", "MSFT", "--feed", FEED,
                      "--authority", AUTHORITY, "--publisher-host", HOSTS[0], "--original-url", PAGE])
        self.assertEqual(2, result.exception.code)

    def test_cli_uses_coordinator_wrapper_and_preserves_retry_exit_two(self):
        # Lazy import must reach the wrapper that also archives the source run.
        with patch("market_checker_app.scout_runner.run_primary_ir", return_value={"status": "WAIT_QUOTA"}) as wrapper, \
                redirect_stdout(io.StringIO()) as output:
            code = main(["--db", str(self.store.db_path), "--ticker", "MSFT", "--feed", FEED,
                         "--authority", AUTHORITY, "--publisher-host", HOSTS[0], "--original-url", PAGE])
        self.assertEqual(2, code)
        self.assertEqual("WAIT_QUOTA", json.loads(output.getvalue())["status"])
        self.assertEqual(self.store.db_path, wrapper.call_args.kwargs["db_path"])
        self.assertEqual(PAGE, wrapper.call_args.kwargs["original_url"])
        self.assertEqual(6, wrapper.call_args.kwargs["max_requests"])

    def test_actual_captures_and_ledger_reproduce_zero_live_source_acceptance(self):
        artifact = build_review_artifact()
        self.assertEqual(json.loads((EVIDENCE / "source_review.json").read_bytes()), artifact)
        self.assertEqual(3, artifact["actual_http_requests"])
        self.assertEqual(2, artifact["safe_original_bodies"])
        self.assertEqual(0, artifact["source_verified_live_cases"])
        self.assertEqual("5130e6c90a0770d32e40f5eafcc87cd980d963934ae6c125963d8b1f2c20eac1",
                         artifact["withheld_authority_sha256"])
        runtime, ledger, captures = load_captures()
        self.assertEqual("16812d284a40fede0aea1480b401f0b465ba54eb5c9bf228a14f0facefa0cb3a",
                         hashlib.sha256(captures["feed"].body).hexdigest())
        self.assertEqual("e06b8309536582128b3b58dd1b18879920df7bdc358a8414e18e058fd7273a3c",
                         hashlib.sha256(captures["primary"].body).hexdigest())
        self.assertEqual(["RETURNED", "FAILED", "RETURNED"], artifact["quota_request_outcomes"])
        self.assertEqual(0, ledger["prior_rss_request_count"])
        self.assertEqual(0, runtime["new_findings"])
        control = artifact["captured_only_offline_control"]["review"]
        self.assertEqual("PARTIAL", control["source_verification_status"])
        self.assertIn("IR_PORTAL_LINK_SCOPE_UNRESOLVED", control["reasons"])
        self.assertTrue(control["quoted_claim"]["quote_matches_primary_article"])
        self.assertFalse(control["issuer_identity_verified"])
        self.assertFalse(control["issuer_attribution_allowed"])
        self.assertEqual("Wed, 07 Oct 2026 13:17:44 GMT", artifact["http_last_modified_is_not_publication"])

    def test_replay_rejects_changed_original_and_any_published_authority_body(self):
        copy = self.root / "evidence"
        shutil.copytree(EVIDENCE, copy)
        runtime, _, _ = load_captures(copy)
        original = copy / "captures" / runtime["capture_receipts"][0]["raw_path"]
        raw = original.read_bytes()
        original.write_bytes(raw + b"\n")
        with self.assertRaisesRegex(ValueError, "body/hash mismatch"):
            load_captures(copy)
        original.write_bytes(raw)
        (copy / "captures" / "fabricated_authority.raw").write_bytes(b"No authority body may be substituted")
        with self.assertRaisesRegex(ValueError, "remain withheld"):
            load_captures(copy)


if __name__ == "__main__":
    unittest.main()
