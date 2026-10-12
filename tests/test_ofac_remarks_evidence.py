"""OFAC remarks supplement is evidence, never a weak-name sanctions trigger."""
from datetime import datetime, timedelta, timezone
import csv
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from market_checker_app.services.ofac_scout_service import (
    ALT_URL, COMMENTS_URL, CONSOLIDATED_ALT_URL, CONSOLIDATED_COMMENTS_URL,
    CONSOLIDATED_URL, SDN_URL, OfacConsolidatedClient, OfacConsolidatedScoutService,
    OfacSdnClient, OfacSdnScoutService, OfacSnapshot, attach_sdn_aliases,
    attach_sdn_comments, parse_sdn_csv,
)
from market_checker_app.storage.scout_store import ScoutStore


NOW = datetime(2026, 10, 11, 10, tzinfo=timezone.utc)


def csv_body(rows):
    output = io.StringIO(newline="")
    csv.writer(output).writerows(rows)
    return output.getvalue().encode("utf-8")


def primary_body(remarks="-0-"):
    return csv_body([
        ["1", "Exact Primary Inc.", "-0-", "TEST", *(["-0-"] * 7), remarks],
        ["2", "Person", "individual", "TEST", *(["-0-"] * 8)],
    ])


ALIASES = csv_body([["1", "10", "fka", "Exact Former Inc.", "-0-"],
                    ["2", "20", "aka", "Person Alias", "-0-"]])


def snapshot_with_comments(remarks, supplement=b"\r\n"):
    return attach_sdn_comments(attach_sdn_aliases(parse_sdn_csv(primary_body(remarks)), ALIASES), supplement)


class SnapshotClient:
    def __init__(self, snapshot):
        self.payload, self.calls = snapshot, 0

    def snapshot(self):
        self.calls += 1
        return self.payload


class OfacRemarksEvidenceTests(unittest.TestCase):
    def test_explicit_weak_akas_are_corroboration_only_and_other_quotes_are_not_aliases(self):
        remarks = ("Registration: 'Not An Alias'; a.k.a. 'COMMON'; "
                   'a.k.a. "WEAK COMPANY"; a.k.a. \'O\'BRIEN COMPANY\'; '
                   "a.k.a. 'COMMON'; Linked To: 'OTHER INC.'.")
        snapshot = snapshot_with_comments(remarks)
        entity = snapshot.entities[0]
        self.assertEqual(remarks, entity["remarks"])
        self.assertEqual(["COMMON", "WEAK COMPANY", "O'BRIEN COMPANY"],
                         [a["name"] for a in entity["weak_aliases"]])
        self.assertTrue(all(a["alias_quality"] == "weak" and not a["screening_eligible"]
                            for a in entity["weak_aliases"]))
        self.assertEqual(["Exact Former Inc."], [a["name"] for a in entity["aliases"]])

    def test_spillover_join_preserves_character_1001_and_alias_crossing_boundary(self):
        suffix = "; a.k.a. 'WEAK I"
        primary = "X" * (1000 - len(suffix)) + suffix
        spillover = "NC.'; Linked To: OTHER INC."
        body = csv_body([["1", spillover], ["2", "Person-only continuation"]])
        snapshot = snapshot_with_comments(primary, body)
        entity = snapshot.entities[0]
        self.assertEqual(primary + spillover, entity["remarks"])
        self.assertEqual(primary, entity["remarks_primary"])
        self.assertEqual(spillover, entity["remarks_spillover"])
        self.assertEqual("WEAK INC.", entity["weak_aliases"][0]["name"])
        self.assertEqual(("COMPLETE", 2), (snapshot.comments_status, snapshot.comments_total_rows))
        self.assertEqual(hashlib.sha256(body).hexdigest(), snapshot.comments_content_sha256)
        self.assertEqual(1, len(snapshot.entities))

    def test_duplicate_orphan_bad_shape_empty_and_embedded_eof_supplements_fail_closed(self):
        primary = "X" * 1000
        base = attach_sdn_aliases(parse_sdn_csv(primary_body(primary)), ALIASES)
        body = csv_body([["1", "rest"]])
        invalids = (b"", body + body, csv_body([["99", "rest"]]),
                    csv_body([["0", "rest"]]), csv_body([["1", "rest", "extra"]]),
                    csv_body([["1", "-0-"]]), csv_body([["1", "  "]]),
                    csv_body([["1", "A" * 100_001]]), csv_body([["1", "bad\x1arest"]]))
        for invalid in invalids:
            with self.subTest(invalid=invalid[:40]), self.assertRaises((ValueError, csv.Error)):
                attach_sdn_comments(base, invalid)
        self.assertEqual("NOT_FETCHED", base.comments_status)
        self.assertEqual(primary, base.entities[0]["remarks"])
        self.assertEqual("COMPLETE", attach_sdn_comments(base, body + b"\x1a\r\n").comments_status)
        with self.assertRaisesRegex(ValueError, "truncated primary"):
            attach_sdn_comments(parse_sdn_csv(primary_body("short")), body)

    def test_inconsistent_primary_remarks_are_not_silently_truncated(self):
        with self.assertRaises(ValueError):
            parse_sdn_csv(primary_body("X" * 1001))

    def test_both_live_clients_fetch_exact_bounded_supplement_and_keep_protocol(self):
        for cls, urls, limits in (
            (OfacSdnClient, [SDN_URL, ALT_URL, COMMENTS_URL], [8_000_000, 2_000_000, 2_000_000]),
            (OfacConsolidatedClient, [CONSOLIDATED_URL, CONSOLIDATED_ALT_URL,
                                      CONSOLIDATED_COMMENTS_URL], [1_000_000, 1_000_000, 2_000_000]),
        ):
            client = cls()
            with self.subTest(client=cls.__name__), patch.object(
                    client, "_download", side_effect=[primary_body(), ALIASES, b"\r\n"]) as download:
                snapshot = client.snapshot()
                self.assertEqual("COMPLETE", snapshot.comments_status)
                self.assertEqual(urls, [c.args[0] for c in download.call_args_list])
                self.assertEqual(limits, [c.kwargs["byte_budget"] for c in download.call_args_list])

    def test_weak_name_and_linked_name_alone_cannot_trigger_issuer_candidate(self):
        snapshot = snapshot_with_comments("a.k.a. 'Weak Named Inc.'; Linked To: LINKED INC.")
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            for ticker, name in (("PRIMARY", "Exact Primary Inc."), ("WEAK", "Weak Named Inc."),
                                 ("LINKED", "LINKED INC."), ("PERSON", "Person Alias")):
                store.observe_sec_identity(subject_id=ticker, cik="0000000001", company_name=name, as_of=NOW)
            result = OfacSdnScoutService(store, client=SnapshotClient(snapshot)).run(as_of=NOW)
            self.assertEqual(("OK", 4, 1), (result["status"], result["checked_issuers"], result["new_findings"]))
            self.assertEqual([], store.latest_findings(["WEAK", "LINKED", "PERSON"], as_of=NOW, source="ofac"))
            saved = store.findings_as_of("PRIMARY", as_of=NOW)[0]
            details = json.loads(saved["details_json"])
            self.assertEqual("UNVERIFIED", saved["verification_status"])
            for field in ("weak_aliases_screened", "weak_alias_extraction_complete", "issuer_identity_verified",
                          "ownership_look_through_performed", "scoring_applied", "publication_time_verified",
                          "snapshot_release_atomicity_verified"):
                self.assertFalse(details[field], field)
            self.assertTrue(details["publisher_remarks_complete"])
            self.assertEqual("Weak Named Inc.", details["weak_aliases"][0]["name"])
            self.assertEqual(COMMENTS_URL, details["comments_snapshot_source"])
            self.assertEqual(NOW.isoformat(), saved["available_at"])
            self.assertEqual([], store.findings_as_of("PRIMARY", as_of=NOW - timedelta(seconds=1)))

    def test_unavailable_or_invalid_supplement_preserves_primary_candidate_as_partial(self):
        for error in (OSError("unavailable"), csv_body([["99", "orphan"]]), b"<html>unavailable</html>"):
            with self.subTest(error=type(error).__name__), TemporaryDirectory() as directory:
                store = ScoutStore(Path(directory) / "test.db")
                store.observe_sec_identity(subject_id="ONE", cik="0000000001",
                                           company_name="Exact Primary Inc.", as_of=NOW)
                client = OfacSdnClient()
                with patch.object(client, "_download", side_effect=[primary_body(), ALIASES, error]):
                    result = OfacSdnScoutService(store, client=client).run(as_of=NOW)
                self.assertEqual(("PARTIAL", 1), (result["status"], result["new_findings"]))
                self.assertEqual("UNAVAILABLE_OR_INVALID", result["remarks_supplement_status"])
                self.assertEqual("", result["comments_snapshot_sha256"])
                self.assertEqual(0, store.specialist_coverage("ofac", as_of=NOW)["current_complete"])
                self.assertEqual(1, store.specialist_coverage("ofac", as_of=NOW)["current_partial"])
                details = json.loads(store.findings_as_of("ONE", as_of=NOW)[0]["details_json"])
                self.assertFalse(details["publisher_remarks_complete"])

    def test_unavailable_supplement_does_not_create_complete_negative(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            store.observe_sec_identity(subject_id="ABSENT", cik="0000000001",
                                       company_name="Nonexistent Inc.", as_of=NOW)
            client = OfacConsolidatedClient()
            with patch.object(client, "_download", side_effect=[primary_body(), ALIASES, OSError("offline")]):
                result = OfacConsolidatedScoutService(store, client=client).run(as_of=NOW)
            self.assertEqual(("PARTIAL", 0), (result["status"], result["new_findings"]))
            self.assertEqual(0, store.specialist_coverage("ofac_non_sdn", as_of=NOW)["current_complete"])

    def test_non_sdn_supplement_cannot_become_sdn_blocking_or_ownership_proof(self):
        snapshot = snapshot_with_comments("Program-specific restriction; a.k.a. 'COMMON'")
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            store.observe_sec_identity(subject_id="ONE", cik="0000000001", company_name="Exact Former Inc.", as_of=NOW)
            service = OfacConsolidatedScoutService(store, client=SnapshotClient(snapshot))
            self.assertEqual(1, service.run(as_of=NOW)["new_findings"])
            details = json.loads(store.findings_as_of("ONE", as_of=NOW)[0]["details_json"])
            self.assertEqual("NON_SDN", details["list_kind"])
            self.assertFalse(details["blocking_status_determined"])
            self.assertFalse(details["issuer_identity_verified"])
            self.assertTrue(details["restrictions_require_program_review"])
            self.assertEqual(CONSOLIDATED_COMMENTS_URL, details["comments_snapshot_source"])
            self.assertEqual([], store.latest_findings(["ONE"], as_of=NOW, source="ofac"))

    def test_remarks_change_is_a_new_revision_but_identical_replay_is_idempotent(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "test.db"
            store = ScoutStore(path)
            store.observe_sec_identity(subject_id="ONE", cik="0000000001", company_name="Exact Primary Inc.", as_of=NOW)
            client = SnapshotClient(snapshot_with_comments("a.k.a. 'FIRST'"))
            service = OfacSdnScoutService(store, client=client)
            self.assertEqual(1, service.run(as_of=NOW)["new_findings"])
            self.assertEqual(0, service.run(as_of=NOW + timedelta(days=1))["new_findings"])
            client.payload = snapshot_with_comments("a.k.a. 'SECOND'")
            self.assertEqual(1, service.run(as_of=NOW + timedelta(days=2))["new_findings"])
            old = json.loads(store.findings_as_of("ONE", as_of=NOW)[0]["details_json"])
            self.assertEqual("FIRST", old["weak_aliases"][0]["name"])
            reopened = ScoutStore(path)
            self.assertEqual(0, OfacSdnScoutService(reopened, client=client).run(
                as_of=NOW + timedelta(days=3))["new_findings"])

    def test_preupgrade_snapshot_still_works_but_never_claims_complete_remarks(self):
        snapshot = OfacSnapshot((dict(sdn_id="1", name="Exact Primary Inc.", programs="TEST"),),
                                "a" * 64, 1, frozenset({"1"}), "b" * 64, 1)
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            store.observe_sec_identity(subject_id="ONE", cik="0000000001", company_name="Exact Primary Inc.", as_of=NOW)
            result = OfacSdnScoutService(store, client=SnapshotClient(snapshot)).run(as_of=NOW)
            self.assertEqual(("PARTIAL", "NOT_FETCHED"), (result["status"], result["remarks_supplement_status"]))
            self.assertEqual(1, result["new_findings"])

    def test_future_identity_and_naive_observation_do_not_download_or_backdate_evidence(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            store.observe_sec_identity(subject_id="ONE", cik="0000000001", company_name="Exact Primary Inc.",
                                       as_of=NOW + timedelta(seconds=1))
            client = SnapshotClient(snapshot_with_comments("a.k.a. 'COMMON'"))
            service = OfacSdnScoutService(store, client=client)
            self.assertEqual("WAIT_IDENTITY", service.run(as_of=NOW)["status"])
            self.assertEqual(0, client.calls)
            with self.assertRaises(ValueError):
                service.run(as_of=NOW.replace(tzinfo=None))


if __name__ == "__main__":
    unittest.main()
