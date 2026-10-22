"""Completion-clock regressions; synthetic delayed callbacks are not benchmarks."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import unittest
from unittest.mock import patch

from market_checker_app.collectors import doj_case_caption_review as target
from tests.test_doj_case_caption_review import pdf, synthetic_lines

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    plan = json.loads((ROOT / "evidence/batch10_regulatory_20261018/actual_review_plan.json").read_text())
    request = deepcopy(plan["request"])
    request["documents"] = [row for row in request["documents"] if row["capture_id"] == "govinfo_case_opinion"]
    body = pdf([synthetic_lines()])
    metadata = deepcopy(plan["captures"]["govinfo_case_opinion"]["metadata"])
    metadata.update(raw_response_bytes=len(body), raw_response_sha256=hashlib.sha256(body).hexdigest(),
                    synthetic_contract_only=True)
    return request, {"govinfo_case_opinion": (metadata, body)}


class CaptionCompletionClockTests(unittest.TestCase):
    def review(self, request, captures, **kwargs):
        with patch("socket.socket.connect", side_effect=AssertionError("No source HTTP")), \
             patch.object(sqlite3, "connect", side_effect=AssertionError("No database")):
            return target.review_doj_case_caption(request, captures, **kwargs)

    def test_known_at_follows_a_real_delayed_decoder_callback(self):
        request, captures = fixture()
        decoder = target._decode
        completed = []
        def delayed(body):
            result = decoder(body)
            time.sleep(0.01)  # Contract boundary only, never a performance claim.
            completed.append(datetime.now(timezone.utc))
            return result
        with patch.object(target, "_decode", side_effect=delayed):
            report = self.review(request, captures)
        known = datetime.fromisoformat(report["known_at"])
        self.assertGreaterEqual(known, completed[0])
        self.assertEqual(report["known_at"], report["review_completed_at"])
        self.assertLessEqual(datetime.fromisoformat(report["review_started_at"]), completed[0])
        self.assertTrue(report["completion_clock_verified"])

    def test_future_source_read_cutoff_is_rejected_before_decoder(self):
        request, captures = fixture()
        future = datetime.now(timezone.utc)+timedelta(hours=1)
        with patch.object(target, "_decode", wraps=target._decode) as decoder:
            with self.assertRaises(ValueError):
                self.review(request, captures, reviewed_at=future)
        decoder.assert_not_called()

    def test_completion_and_source_read_clocks_are_distinct_and_monotonic(self):
        request, captures = fixture()
        start = datetime.now(timezone.utc); end = start+timedelta(seconds=3)
        with patch.object(target, "_physical_clock", side_effect=[start, end]) as clock:
            report = self.review(request, captures)
        self.assertEqual(2, clock.call_count)
        self.assertEqual(start.isoformat(), report["review_started_at"])
        self.assertEqual(start.isoformat(), report["source_read_cutoff"])
        self.assertEqual(end.isoformat(), report["known_at"])
        self.assertFalse(report["source_read_cutoff_is_output_availability"])
        self.assertEqual(1, report["supported_case_documents"])

    def test_physical_clock_rollback_rejects_the_whole_derived_report(self):
        request, captures = fixture(); start = datetime.now(timezone.utc)
        with patch.object(target, "_physical_clock", side_effect=[start, start-timedelta(microseconds=1)]):
            with self.assertRaisesRegex(ValueError, "CLOCK_ROLLBACK"):
                self.review(request, captures)

    def test_missing_original_partial_path_still_passes_completion_guard(self):
        request, captures = fixture(); meta, _ = captures["govinfo_case_opinion"]
        captures["govinfo_case_opinion"] = (dict(meta, public_raw_available=False), None)
        start = datetime.now(timezone.utc); end = start+timedelta(seconds=2)
        with patch.object(target, "_physical_clock", side_effect=[start, end]) as clock, \
             patch.object(target, "_decode", side_effect=AssertionError("No missing-body decode")):
            report = self.review(request, captures)
        self.assertEqual(2, clock.call_count)
        self.assertEqual(0, report["supported_case_documents"])
        self.assertEqual(end.isoformat(), report["known_at"])
        self.assertEqual("MISSING_OR_WHOLE_WITHHELD_ORIGINAL", report["source_reviews"][0]["reason"])

    def test_missing_selector_partial_path_cannot_escape_clock_rollback(self):
        request, captures = fixture(); request["documents"][0]["capture_id"] = "missing_original"
        start = datetime.now(timezone.utc)
        with patch.object(target, "_physical_clock", side_effect=[start, start-timedelta(seconds=1)]):
            with self.assertRaisesRegex(ValueError, "CLOCK_ROLLBACK"):
                self.review(request, captures)

    def test_supported_source_clock_after_completion_is_rejected(self):
        request, captures = fixture(); start = datetime.now(timezone.utc); end = start+timedelta(seconds=1)
        original = target._source
        def derived(*args):
            info, pages, reason = original(*args)
            info["source_known_at"] = (end+timedelta(microseconds=1)).isoformat()
            return info, pages, reason
        with patch.object(target, "_physical_clock", side_effect=[start, end]), patch.object(target, "_source", side_effect=derived):
            with self.assertRaisesRegex(ValueError, "PRECEDES_SOURCE_OR_DERIVED_CLOCK"):
                self.review(request, captures)

    def test_nested_review_times_cannot_postdate_the_parent_known_at(self):
        for key in ("known_at", "ingested_at", "reviewed_at", "review_started_at", "review_completed_at"):
            request, captures = fixture(); start = datetime.now(timezone.utc); end = start+timedelta(seconds=1)
            original = target._document
            def derived(*args):
                result = original(*args)
                result["nested_review"] = [{key: (end+timedelta(microseconds=1)).isoformat()}]
                return result
            with self.subTest(key=key), patch.object(target, "_physical_clock", side_effect=[start, end]), \
                 patch.object(target, "_document", side_effect=derived):
                with self.assertRaisesRegex(ValueError, "PRECEDES_SOURCE_OR_DERIVED_CLOCK"):
                    self.review(request, captures)

    def test_explicit_past_cutoff_limits_sources_without_backdating_output(self):
        request, captures = fixture(); start = datetime.now(timezone.utc); end = start+timedelta(seconds=2)
        cutoff = datetime(2026, 10, 16, tzinfo=timezone.utc)
        with patch.object(target, "_physical_clock", side_effect=[start, end]):
            report = self.review(request, captures, reviewed_at=cutoff)
        self.assertEqual(cutoff.isoformat(), report["source_read_cutoff"])
        self.assertEqual(cutoff.isoformat(), report["reviewed_at"])
        self.assertEqual(end.isoformat(), report["known_at"])
        self.assertFalse(report["historical_availability_reconstructed"])
        self.assertEqual(1, report["supported_case_documents"])

    def test_naive_end_clock_and_nested_clock_fail_closed(self):
        request, captures = fixture(); start = datetime.now(timezone.utc)
        with patch.object(target, "_physical_clock", side_effect=[start, start.replace(tzinfo=None)]):
            with self.assertRaises(ValueError):
                self.review(request, captures)
        original = target._document
        def derived(*args):
            result = original(*args); result["nested_review"] = {"known_at": "2026-10-18T08:00:00"}
            return result
        with patch.object(target, "_document", side_effect=derived):
            with self.assertRaises(ValueError):
                self.review(request, captures)


if __name__ == "__main__":
    unittest.main()
