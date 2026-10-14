from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import runpy
import shutil
import tempfile
import unittest
import warnings
from unittest.mock import patch

from evidence.batch4_ir_20261013.build_review import (
    ROOT as OLD_ROOT, WITHHOLDING_RECORD, build_review_artifact, lead_for_page, load_captures,
)
from market_checker_app.services.ir_primary_verification_service import (
    IRPrimaryVerificationError, UnavailableIRDocument, capture_available_at, verify_primary_ir_lead,
)


REPO = OLD_ROOT.parents[1]
NEW_ROOT = WITHHOLDING_RECORD.parent
POLICY = ("news.microsoft.com", "www.microsoft.com")
AUTHORITY_SHA = "deb5642d450c0fd60cd12066a42da8c1905f4fd47562afcef06cf8e428bcbeb1"
SAFE_SHA = {
    "press_releases_feed.xml": "16812d284a40fede0aea1480b401f0b465ba54eb5c9bf228a14f0facefa0cb3a",
    "announcement.html": "fec64697d008ec0de911cf0eac538a76d3b11ecc79036bfe4053ef3aa82d74d9",
    "ir_linked_announcement.html": "eef8b3825373114c3932df57236a34fefb6ddb5e93d873aabf776ddf2c31fa5b",
}


class Batch5IRPriorWithholdingTests(unittest.TestCase):
    def test_exact_original_metadata_is_preserved_while_entire_public_body_is_absent(self):
        record = json.loads(WITHHOLDING_RECORD.read_bytes())
        manifest = json.loads((OLD_ROOT / "captures.json").read_bytes())
        original = next(row for row in manifest["captures"] if row["path"] == "primary/ir_authority.html")
        withdrawal = record["withdrawals"][0]
        self.assertEqual(original, withdrawal["original_capture_metadata"])
        self.assertEqual(AUTHORITY_SHA, original["sha256"])
        self.assertEqual(286802, original["size_bytes"])
        self.assertEqual("2026-10-13T13:11:18.872702+00:00", original["observed_at"])
        self.assertTrue(record["whole_body_gate_positive"])
        self.assertFalse((OLD_ROOT / "primary/ir_authority.html").exists())
        self.assertFalse(withdrawal["current_public_body_present"])
        self.assertTrue(withdrawal["entire_original_body_private_preservation_verified"])
        self.assertFalse(withdrawal["sanitized_encoded_compressed_or_replacement_body_created"])
        self.assertEqual(0, record["http_requests"])

    def test_loader_never_opens_known_withheld_body_and_strictly_checks_three_safe_hashes(self):
        original_read = Path.read_bytes
        reads = []
        def read(path):
            reads.append(path)
            if path.name == "ir_authority.html":
                raise AssertionError("known rejected body must not be opened")
            return original_read(path)
        with patch.object(Path, "read_bytes", read):
            _, captures = load_captures()
        authority = captures["ir_authority.html"]
        self.assertIsInstance(authority, UnavailableIRDocument)
        self.assertFalse(hasattr(authority, "body"))
        self.assertEqual(AUTHORITY_SHA, authority.summary()["sha256"])
        self.assertFalse(authority.summary()["body_available"])
        self.assertNotIn(OLD_ROOT / "primary/ir_authority.html", reads)
        for name, digest in SAFE_SHA.items():
            self.assertEqual(digest, hashlib.sha256(captures[name].body).hexdigest())

    def test_accidental_path_restoration_cannot_recover_verification_or_trigger_body_io(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "batch4"
            shutil.copytree(OLD_ROOT, root)
            (root / "primary/ir_authority.html").write_bytes(b"A substitute must never be consumed")
            original_read = Path.read_bytes
            def read(path):
                if path.name == "ir_authority.html":
                    raise AssertionError("restored/replaced rejected body cannot be opened")
                return original_read(path)
            with patch.object(Path, "read_bytes", read):
                _, captures = load_captures(root)
            self.assertIsInstance(captures["ir_authority.html"], UnavailableIRDocument)

    def test_missing_retention_record_returns_missing_required_capture_before_body_open(self):
        with tempfile.TemporaryDirectory() as directory:
            _, captures = load_captures(withholding_path=Path(directory) / "missing.json")
            authority = captures["ir_authority.html"]
            self.assertEqual("MISSING_CAPTURE_BODY", authority.reason)
            self.assertEqual(AUTHORITY_SHA, authority.sha256)
            report = self.review(captures)
            self.assertEqual("PARTIAL", report["source_verification_status"])
            self.assertIn("AUTHORITY_CAPTURE_MISSING_CAPTURE_BODY", report["reasons"])

    def test_missing_or_withheld_input_returns_partial_without_rss_or_html_parser(self):
        _, captures = load_captures()
        # Build the lead from safe RSS bytes before disabling both parsers.
        lead = lead_for_page(captures, captures["announcement.html"])
        with patch("market_checker_app.services.ir_primary_verification_service.feedparser.parse", side_effect=AssertionError("no feed parser")), \
                patch("market_checker_app.services.ir_primary_verification_service._Page", side_effect=AssertionError("no page parser")):
            report = self.review(captures, lead=lead)
        self.assertEqual("PARTIAL", report["source_verification_status"])
        self.assertEqual([], report["issuer_as_named_mentions"])
        self.assertEqual("", report["quoted_claim"]["quote"])
        self.assertFalse(report["issuer_attribution_allowed"])
        self.assertFalse(report["scoring_applied"])

    def test_withholding_knowledge_cannot_be_backdated_to_original_capture_cutoff(self):
        _, captures = load_captures()
        original_cutoff = max(row.observed_at for row in captures.values())
        with self.assertRaisesRegex(IRPrimaryVerificationError, "future capture"):
            self.review(captures, clock=original_cutoff)
        report = self.review(captures)
        self.assertGreater(datetime.fromisoformat(report["source_available_at"]), original_cutoff)
        self.assertEqual(captures["ir_authority.html"].available_at.isoformat(), report["source_available_at"])

    def test_safe_original_tampering_and_withholding_metadata_drift_fail_strictly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "batch4"
            shutil.copytree(OLD_ROOT, root)
            safe = root / "primary/announcement.html"
            original = safe.read_bytes()
            safe.write_bytes(original + b"\n")
            with self.assertRaisesRegex(ValueError, "bytes/hash conflict"):
                load_captures(root)
            safe.write_bytes(original)
            record = json.loads(WITHHOLDING_RECORD.read_bytes())
            record["withdrawals"][0]["original_capture_metadata"]["sha256"] = "f" * 64
            retained = Path(directory) / "changed.json"
            retained.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, "metadata conflicts"):
                load_captures(root, withholding_path=retained)

    def test_unavailable_metadata_cannot_use_an_unpinned_hash_or_naive_clock(self):
        _, captures = load_captures()
        authority = captures["ir_authority.html"]
        for changed in (replace(authority, sha256="not-a-hash"),
                        replace(authority, unavailable_at=authority.unavailable_at.replace(tzinfo=None)),
                        replace(authority, unavailable_at=authority.observed_at - timedelta(seconds=1)),
                        replace(authority, size_bytes=2_000_001)):
            with self.assertRaises(IRPrimaryVerificationError):
                changed.summary()

    def test_historical_documents_stay_byte_identical_and_current_report_has_no_positives(self):
        record = json.loads(WITHHOLDING_RECORD.read_bytes())
        for name, pin in record["immutable_historical_documents"].items():
            raw = (REPO / name).read_bytes()
            self.assertEqual(pin["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(pin["size_bytes"], len(raw))
        artifact = build_review_artifact()
        self.assertEqual(json.loads((NEW_ROOT / "current_review.json").read_bytes()), artifact)
        self.assertEqual([], artifact["positive_source_only_cases"])
        self.assertEqual(2, len(artifact["current_partial_source_only_cases"]))
        self.assertFalse(artifact["historical_positive_reports_are_current_acceptance"])
        # CLI must refuse an overwrite of the already retained additive report.
        with patch("urllib.request.urlopen", side_effect=AssertionError("no HTTP")), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            with self.assertRaises(FileExistsError):
                runpy.run_module("evidence.batch4_ir_20261013.build_review", run_name="__main__")
        for name, pin in record["immutable_historical_documents"].items():
            self.assertEqual(pin["sha256"], hashlib.sha256((REPO / name).read_bytes()).hexdigest())

    @staticmethod
    def review(captures, *, lead=None, clock=None):
        page = captures["announcement.html"]
        return verify_primary_ir_lead(
            lead or lead_for_page(captures, page), feed_capture=captures["press_releases_feed.xml"],
            primary_capture=page, authority_capture=captures["ir_authority.html"], publisher_hosts=POLICY,
            knowledge_at=clock or max(capture_available_at(row) for row in captures.values()),
        )


if __name__ == "__main__":
    unittest.main()
