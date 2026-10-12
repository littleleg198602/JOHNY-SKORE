"""Synthetic archived transition fixtures; no production identities or live proof."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from market_checker_app.sec13f_identity_bulk import capture_source, export_reviewed
from market_checker_app.services.sec13f_identity_bulk import (
    IDENTITY_FIELDS, SourceSnapshot, identity_version_sha256, save_snapshot,
)
from market_checker_app.services.sec13f_scout_service import load_verified_securities
from tests.test_sec13f_identity_bulk import (
    COVER_URL, NOW, OBSERVED, PUBLISHED, builder, catalog, cover, instrument, reviewed, securities,
)


ACTION_URL = "https://www.sec.gov/Archives/edgar/data/320193/000032019326000046/synthetic-action.htm"


def version(row):
    return {key: row[key] for key in IDENTITY_FIELDS}


def previous_version(row, **updates):
    old = version(row)
    old.update(effective_from="2026-01-01", effective_to="2026-03-31",
               known_at=(NOW - timedelta(days=2)).isoformat())
    old.update(updates)
    return old


def action_fixture(old, new, *, effective="2026-04-01", hidden=False, archive_url=ACTION_URL,
                   old_title=None, new_title=None, observed=OBSERVED):
    # Exact passage text is fabricated solely to exercise provenance checks.
    old_title = old_title or old["class_description"]
    new_title = new_title or new["class_description"]
    before = f'prior ticker {old["ticker"]}, CIK {old["issuer_cik"]}, CUSIP {old["cusip"]}, security class {old_title}'
    after = f'next ticker {new["ticker"]}, CIK {new["issuer_cik"]}, CUSIP {new["cusip"]}, security class {new_title}'
    passage = f"Effective {effective}, the identity changes from {before} to {after}."
    body = f'<div style="display:none">{passage}</div>' if hidden else f"<p>{passage}</p>"
    snapshot = SourceSnapshot("corporate_action_filing", archive_url, observed,
                              f"<html><head><title>Fixture only</title></head><body>{body}</body></html>".encode(), PUBLISHED)
    transition = {
        "from_identity_sha256": identity_version_sha256(old),
        "to_identity_sha256": identity_version_sha256(new),
        "effective_on": effective,
        "source_snapshot_id": snapshot.snapshot_id,
        "action_excerpt": passage,
        "effective_date_text": effective,
        "from_identity": {"excerpt": before, "ticker_text": old["ticker"],
                          "issuer_cik_text": old["issuer_cik"], "class_title": old_title},
        "to_identity": {"excerpt": after, "ticker_text": new["ticker"],
                        "issuer_cik_text": new["issuer_cik"], "class_title": new_title},
    }
    return snapshot, transition


def changed_fixture(**old_updates):
    sources = [catalog(), securities(), instrument(), cover()]
    row = reviewed(sources)
    old = previous_version(row, issuer_cik="0000123456", **old_updates)
    action, transition = action_fixture(old, row, old_title="Common Stock", new_title="Common Stock")
    row["review"]["identity_transitions"] = [transition]
    return [*sources, action], old, row


class Sec13fIdentityTransitionTests(unittest.TestCase):
    def test_dated_cik_change_preserves_exact_past_version_and_clock(self):
        sources, old, row = changed_fixture()
        before = deepcopy(old)
        service = builder(sources, existing=[old])
        accepted = service.validate_reviewed([row])
        self.assertEqual(accepted, [version(row)])
        self.assertEqual(service.existing, [before])
        self.assertNotIn("review", accepted[0])
        self.assertEqual(old["effective_to"], "2026-03-31")
        self.assertEqual(old["known_at"], before["known_at"])

    def test_cusip_change_requires_primary_action_even_with_unchanged_cik(self):
        sources = [catalog(), securities(), instrument(), cover()]
        row = reviewed(sources)
        old = previous_version(row, cusip="02079K107")
        with self.assertRaisesRegex(ValueError, "primary identity-transition"):
            builder(sources, existing=[old]).validate_reviewed([row])
        action, transition = action_fixture(old, row, old_title="Common Stock", new_title="Common Stock")
        row["review"]["identity_transitions"] = [transition]
        self.assertEqual(builder([*sources, action], existing=[old]).validate_reviewed([row]), [version(row)])

    def test_share_class_change_is_separate_despite_same_cik_and_cusip(self):
        sources = [catalog(), securities(), instrument(), cover()]
        row = reviewed(sources)
        old = previous_version(row, class_description="CL A")
        with self.assertRaisesRegex(ValueError, "security-class change"):
            builder(sources, existing=[old]).validate_reviewed([row])
        action, transition = action_fixture(old, row, old_title="Class A Common Stock", new_title="Common Stock")
        row["review"]["identity_transitions"] = [transition]
        self.assertEqual(len(builder([*sources, action], existing=[old]).validate_reviewed([row])), 1)

    def test_same_identity_renewal_uses_new_evidence_without_rewriting_the_past(self):
        sources = [catalog(), securities(), instrument(), cover()]
        row = reviewed(sources)
        old = previous_version(row, class_description="Common Stock")
        old["issuer_name"] = "APPLE INCORPORATED"
        self.assertEqual(len(builder(sources, existing=[old]).validate_reviewed([row])), 1)

    def test_overlapping_cik_cusip_and_class_changes_are_rejected(self):
        for update in ({"issuer_cik": "0000123456"}, {"cusip": "02079K107"}, {"class_description": "CL A"}):
            sources = [catalog(), securities(), instrument(), cover()]
            row = reviewed(sources)
            old = previous_version(row, effective_to="2026-04-01", **update)
            with self.subTest(update=update):
                with self.assertRaisesRegex(ValueError, "Overlapping incompatible"):
                    builder(sources, existing=[old]).validate_reviewed([row])

    def test_existing_manifest_conflicts_cannot_be_carried_into_a_proposal(self):
        sources, old, row = changed_fixture()
        current = version(row)
        conflicting = deepcopy(current)
        conflicting["issuer_cik"] = old["issuer_cik"]
        with self.assertRaisesRegex(ValueError, "Overlapping incompatible"):
            builder(sources, existing=[current, conflicting])

    def test_canonical_aliases_cannot_hide_an_overlap(self):
        row = reviewed([catalog(), securities(), instrument(), cover()])
        a, b = version(row), version(row)
        a.update(ticker="BRKB", cusip="084670702")
        b.update(ticker="BRK.B", cusip="084670702", class_description="CL A")
        with self.assertRaisesRegex(ValueError, "Overlapping incompatible"):
            builder(existing=[a, b])

    def test_changed_predecessor_dates_or_knowledge_invalidate_hash_bound_review(self):
        for update in ({"effective_to": "2026-03-30"}, {"known_at": (NOW - timedelta(days=1)).isoformat()},
                       {"ticker_evidence_url": COVER_URL.replace("aapl-", "prior-aapl-")}):
            sources, old, row = changed_fixture()
            old.update(update)
            with self.subTest(update=update):
                with self.assertRaisesRegex(ValueError, "exact chronological neighbour"):
                    builder(sources, existing=[old]).validate_reviewed([row])

    def test_primary_source_cannot_be_replaced_by_binding_note_or_schedule13(self):
        sources, old, row = changed_fixture()
        row["review"]["identity_transitions"] = []
        with self.assertRaisesRegex(ValueError, "primary identity-transition"):
            builder(sources, existing=[old]).validate_reviewed([row])
        sources, old, row = changed_fixture()
        row["review"]["identity_transitions"][0]["source_snapshot_id"] = sources[2].snapshot_id
        with self.assertRaisesRegex(ValueError, "primary corporate-action"):
            builder(sources, existing=[old]).validate_reviewed([row])

    def test_action_must_be_primary_filing_of_one_of_the_exact_issuers(self):
        sources, old, row = changed_fixture()
        action, transition = action_fixture(old, row, archive_url=ACTION_URL.replace("/data/320193/", "/data/999999/"))
        row["review"]["identity_transitions"] = [transition]
        with self.assertRaisesRegex(ValueError, "exact issuer CIK"):
            builder([*sources[:-1], action], existing=[old]).validate_reviewed([row])

    def test_transition_action_must_be_observed_before_review(self):
        sources, old, row = changed_fixture()
        action, transition = action_fixture(old, row, observed=NOW - timedelta(minutes=5))
        row["review"]["identity_transitions"] = [transition]
        with self.assertRaisesRegex(ValueError, "backdated knowledge"):
            builder([*sources[:-1], action], existing=[old]).validate_reviewed([row])

    def test_a_new_identity_cannot_rely_on_a_later_known_predecessor_version(self):
        sources, old, row = changed_fixture()
        old["known_at"] = (NOW - timedelta(minutes=1)).isoformat()
        action, transition = action_fixture(old, row)
        row["review"]["identity_transitions"] = [transition]
        with self.assertRaisesRegex(ValueError, "backdated knowledge"):
            builder([*sources[:-1], action], existing=[old]).validate_reviewed([row])

    def test_action_date_must_fit_the_gap_without_extending_either_interval(self):
        for effective in ("2026-03-31", "2026-04-02"):
            sources, old, row = changed_fixture()
            action, transition = action_fixture(old, row, effective=effective)
            row["review"]["identity_transitions"] = [transition]
            with self.subTest(effective=effective):
                with self.assertRaisesRegex(ValueError, "preserved validity"):
                    builder([*sources[:-1], action], existing=[old]).validate_reviewed([row])

    def test_nonadjacent_intervals_preserve_unknown_gap(self):
        sources, old, row = changed_fixture(effective_to="2026-02-28")
        self.assertEqual(len(builder(sources, existing=[old]).validate_reviewed([row])), 1)
        self.assertEqual(old["effective_to"], "2026-02-28")

    def test_hidden_script_or_changed_passage_is_not_primary_evidence(self):
        for mode in ("hidden", "script", "changed"):
            sources, old, row = changed_fixture()
            action, transition = action_fixture(old, row, hidden=mode == "hidden")
            if mode == "script":
                action = SourceSnapshot(action.kind, action.url, action.observed_at,
                                        f'<html><body><script>{transition["action_excerpt"]}</script></body></html>'.encode(), PUBLISHED)
                transition["source_snapshot_id"] = action.snapshot_id
            if mode == "changed":
                transition["action_excerpt"] += " Unarchived conclusion."
            row["review"]["identity_transitions"] = [transition]
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(ValueError, "visible primary"):
                    builder([*sources[:-1], action], existing=[old]).validate_reviewed([row])

    def test_both_identity_quotes_require_exact_cusip_and_normalized_class(self):
        for mode in ("cusip", "cik", "class", "ticker"):
            sources, old, row = changed_fixture()
            claim = row["review"]["identity_transitions"][0]
            if mode == "cusip":
                claim["from_identity"]["excerpt"] = claim["from_identity"]["excerpt"].replace(old["cusip"], "037833101")
            elif mode == "cik":
                claim["from_identity"]["issuer_cik_text"] = "0000320193"
            elif mode == "class":
                claim["from_identity"]["class_title"] = "Class A Common Stock"
            else:
                claim["from_identity"]["ticker_text"] = "GOOG"
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(ValueError, "exact old/new"):
                    builder(sources, existing=[old]).validate_reviewed([row])

    def test_unrelated_or_duplicate_transition_claim_is_rejected(self):
        for mode in ("unrelated", "duplicate"):
            sources, old, row = changed_fixture()
            claims = row["review"]["identity_transitions"]
            if mode == "unrelated":
                claims[0]["from_identity_sha256"] = "0" * 64
            else:
                claims.append(deepcopy(claims[0]))
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(ValueError, "duplicate, unrelated"):
                    builder(sources, existing=[old]).validate_reviewed([row])

    def test_batch_order_cannot_hide_a_cusip_transition(self):
        ticker_source = catalog()
        list_source = securities([("037833100", "APPLE INC", "COM"), ("02079K107", "APPLE INC", "COM")])
        old_instrument = instrument(cusip="02079K107", event="05/31/2026", url=instrument().url.replace("primary_doc", "old_primary_doc"))
        old_cover = cover(url=COVER_URL.replace("aapl-", "old-aapl-"))
        old_cover = SourceSnapshot(old_cover.kind, old_cover.url, OBSERVED,
                                   old_cover.payload.replace(b"2026-06-30", b"2026-05-31"), PUBLISHED)
        new_instrument, new_cover = instrument(), cover()
        old = reviewed([ticker_source, list_source, old_instrument, old_cover], cusip="02079K107")
        old["effective_to"] = "2026-05-31"
        new = reviewed([ticker_source, list_source, new_instrument, new_cover])
        new["effective_from"] = "2026-06-01"
        action, transition = action_fixture(old, new, effective="2026-06-01")
        new["review"]["identity_transitions"] = [transition]
        sources = [ticker_source, list_source, old_instrument, old_cover, new_instrument, new_cover, action]
        for rows in ([old, new], [new, old]):
            with self.subTest(order=[row["cusip"] for row in rows]):
                self.assertEqual(builder(sources).validate_reviewed(rows), [version(row) for row in rows])

    def test_inserting_history_must_review_both_changed_temporal_boundaries(self):
        sources = [catalog(), securities(), instrument(), cover()]
        row = reviewed(sources)
        old = previous_version(row, cusip="02079K305")
        future = previous_version(row, cusip="02079K107", effective_from="2026-07-01", effective_to="2026-09-30")
        before, before_claim = action_fixture(old, row)
        after, after_claim = action_fixture(row, future, effective="2026-07-01", archive_url=ACTION_URL.replace("synthetic-action", "synthetic-next"))
        row["review"]["identity_transitions"] = [before_claim]
        service = builder([*sources, before, after], existing=[future, old])
        with self.assertRaisesRegex(ValueError, "primary identity-transition"):
            service.validate_reviewed([row])
        row["review"]["identity_transitions"].append(after_claim)
        self.assertEqual(service.validate_reviewed([row]), [version(row)])
        self.assertEqual(service.existing, [future, old])

    def test_export_archives_transition_provenance_and_keeps_baseline_bytes(self):
        sources, old, row = changed_fixture()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for source in sources:
                save_snapshot(root, source)
            baseline, reviews, output = root / "baseline.json", root / "reviews.json", root / "proposal.json"
            baseline_bytes = (json.dumps([old], indent=3) + "\n").encode()
            baseline.write_bytes(baseline_bytes)
            reviews.write_text(json.dumps([row]), encoding="utf-8")
            result = export_reviewed(workspace=root, quarter="2026q2", review_path=reviews,
                                     output=output, existing_path=baseline, as_of=NOW)
            self.assertEqual(baseline.read_bytes(), baseline_bytes)
            self.assertEqual(load_verified_securities(output), [old, version(row)])
            self.assertEqual(result["baseline_registry_sha256"], hashlib.sha256(baseline_bytes).hexdigest())
            self.assertEqual(result["primary_identity_transitions"], 1)
            self.assertEqual(result["preserved_identity_versions"], 1)
            self.assertIn(sources[-1].snapshot_id, result["sources"])
            self.assertEqual(result["reviews"][0]["review"]["identity_transitions"], row["review"]["identity_transitions"])

    def test_no_proposal_is_written_if_baseline_changes_during_validation(self):
        sources, old, row = changed_fixture()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for source in sources:
                save_snapshot(root, source)
            baseline, reviews, output = root / "baseline.json", root / "reviews.json", root / "proposal.json"
            baseline.write_text(json.dumps([old]), encoding="utf-8")
            reviews.write_text(json.dumps([row]), encoding="utf-8")
            original = load_verified_securities
            def load_and_modify(path):
                value = original(path)
                if path == baseline:
                    baseline.write_text(json.dumps([old]) + "\n", encoding="utf-8")
                return value
            with patch("market_checker_app.sec13f_identity_bulk.load_verified_securities", side_effect=load_and_modify):
                with self.assertRaisesRegex(ValueError, "changed during review"):
                    export_reviewed(workspace=root, quarter="2026q2", review_path=reviews,
                                    output=output, existing_path=baseline, as_of=NOW)
            self.assertFalse(output.exists())

    def test_action_capture_requires_actual_publication_time_before_network(self):
        with TemporaryDirectory() as directory:
            with patch("market_checker_app.sec13f_identity_bulk._configured_client") as client:
                with self.assertRaisesRegex(ValueError, "publication time"):
                    capture_source(workspace=Path(directory), kind="corporate_action_filing", url=ACTION_URL)
                client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
