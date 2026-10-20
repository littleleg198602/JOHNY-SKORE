"""Independent page identity challenges; every generated page is synthetic."""
from copy import deepcopy
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from market_checker_app.services import usaspending_prime_scope_review as service
from tests.test_usaspending_prime_scope_review import NOW, encoded, inputs, mutate, row, sha


def two_pages():
    first, second = row("PEER1"), row("PEER2")
    first["internal_id"], second["internal_id"] = 101, 202
    data = inputs([([first], True, 2), ([second], False, 2)], mode="LIVE")
    for page, identifier in ((1, 101), (2, 202)):
        data = mutate(data, page=page, payload=lambda payload, value=identifier: payload["page_metadata"].update(last_record_unique_id=value))
    return data


def spliced_second_body_into_first_manifest():
    request, captures = deepcopy(two_pages())
    first, second = request["pages"]
    body = captures[second["body_capture"]][1]
    meta = json.loads(captures[first["manifest_capture"]][1])
    # All byte pins are made internally consistent. The original request still
    # identifies page1 while the swapped whole source response identifies page2.
    meta.update(response_sha256=sha(body), response_bytes=len(body), body_file=f"body-{sha(body)}.response",
                response_headers={"Content-Length": str(len(body))})
    manifest = encoded(meta)
    captures[first["manifest_capture"]] = ({}, manifest)
    captures[first["body_capture"]] = ({}, body)
    first.update(manifest_sha256=sha(manifest), body_sha256=sha(body))
    return request, captures


class PrimeScopeIndependentPeerTests(TestCase):
    def review(self, data):
        request, captures = data
        with patch.object(service, "_physical_clock", return_value=NOW), \
                patch("socket.socket.connect", side_effect=AssertionError("No HTTP")), \
                patch("sqlite3.connect", side_effect=AssertionError("No SQL")), \
                patch.object(Path, "open", side_effect=AssertionError("No file access")):
            return service.review_usaspending_prime_scope(request, captures)

    def assert_no_query_or_history_upgrade(self, report):
        for key in ("query_complete", "pagination_snapshot_guaranteed", "publisher_revision_semantics_verified",
                    "source_revision_history_complete", "first_or_last_revision_verified", "genuine_negative_verified",
                    "selected_award_absence_verified", "recipient_legal_identity_verified", "earlier_uei_relationship_inferred",
                    "issuer_coverage_complete", "historical_availability_reconstructed"):
            self.assertFalse(report[key], key)
        for key in ("source_http_requests", "collector_requests", "db_operations", "quota_operations", "registry_adoptions"):
            self.assertEqual(0, report[key], key)

    def test_valid_two_page_whole_manifest_response_lineage_is_only_a_traversal_control(self):
        data = two_pages(); before = deepcopy(data)
        report = self.review(data)
        self.assertEqual(before, data)
        self.assertEqual("TRAVERSAL_REVIEWED", report["status"])
        self.assertTrue(report["observed_page_chain_complete"])
        self.assertEqual([1, 2], [entry["source_page"] for entry in report["award_observations"]])
        self.assertEqual(2, report["reported_unique_awards"])
        self.assertTrue(report["reported_totals_stable"])
        self.assert_no_query_or_history_upgrade(report)

    def test_hash_valid_whole_response_from_page2_cannot_inherit_page1_manifest_identity(self):
        report = self.review(spliced_second_body_into_first_manifest())
        self.assertEqual("REJECTED", report["status"])
        self.assertEqual("INVALID_PRIME_RESPONSE_PAGINATION", report["reason"])
        self.assertFalse(report["observed_page_chain_complete"])
        self.assertEqual([], report["award_observations"])
        self.assert_no_query_or_history_upgrade(report)

    def test_cross_page_snapshot_labels_never_upgrade_query_history_or_absence(self):
        for labels in (("same-unverified-label", "same-unverified-label"), ("source-snapshot-A", "source-snapshot-B")):
            with self.subTest(labels=labels):
                data = two_pages()
                for page, label in enumerate(labels, 1):
                    data = mutate(data, page=page, payload=lambda payload, value=label: payload.update(snapshot_id=value))
                report = self.review(data)
                self.assertEqual("TRAVERSAL_REVIEWED", report["status"])
                self.assertTrue(report["observed_page_chain_complete"])
                self.assert_no_query_or_history_upgrade(report)
        empty = self.review(inputs([([], False, 0)], mode="LIVE"))
        self.assertEqual("TRAVERSAL_REVIEWED", empty["status"])
        self.assertEqual(0, empty["reported_unique_awards"])
        self.assertFalse(empty["selected_award_presence"][0]["absence_verified"])
        self.assert_no_query_or_history_upgrade(empty)

    def test_each_page_cursor_binds_its_own_last_row_not_the_previous_page(self):
        data = mutate(two_pages(), page=2, payload=lambda payload: payload["page_metadata"].update(last_record_unique_id=101))
        report = self.review(data)
        self.assertEqual("REJECTED", report["status"])
        self.assertEqual("PRIME_LAST_RECORD_CURSOR_CONFLICT", report["reason"])
        self.assertFalse(report["observed_page_chain_complete"])
        self.assert_no_query_or_history_upgrade(report)
