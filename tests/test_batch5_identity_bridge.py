"""Synthetic boundary checks plus exact actual XOM primary-source replay."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from market_checker_app.services.sec_corporate_action_bridge import (
    action_text, class_terms, review_corporate_action_bridge,
)
from market_checker_app.services.sec13f_identity_bulk import SourceSnapshot, identity_version_sha256, parse_ticker_filing
from market_checker_app.services.sec13f_transition_review import instrument_transition_review
from tests.test_sec13f_identity_bulk import NOW, OBSERVED, PUBLISHED, builder, catalog, cover, instrument, reviewed, securities
from tests.test_sec13f_identity_transitions import ACTION_URL, previous_version


def named_cover(source, name, anchor=None):
    raw = source.payload.replace(b"</html>", f'<ix:nonNumeric name="dei:EntityRegistrantName" contextRef="Security">{name}</ix:nonNumeric></html>'.encode())
    if anchor:
        raw = raw.replace(b"2026-06-30", anchor.encode())
    return SourceSnapshot(source.kind, source.url, source.observed_at, raw, source.published_at)


def fixture():
    old_title, new_title = "Common Stock, without par value", "Common Stock, $0.01 par value"
    new_instrument = instrument(title=new_title)
    new_cover = named_cover(cover(title=new_title), "Apple Inc")
    row = reviewed([catalog(), securities(), new_instrument, new_cover])
    old = previous_version(row, issuer_cik="0000123456", cusip="02079K107", issuer_name="Former Apple Corporation")
    old_instrument = instrument(cik=old["issuer_cik"], cusip=old["cusip"], title=old_title, event="03/31/2026",
                                url="https://www.sec.gov/Archives/edgar/data/123456/000012345626000044/old_doc.xml")
    old_instrument = SourceSnapshot(old_instrument.kind, old_instrument.url, old_instrument.observed_at,
                                    old_instrument.payload.replace(b"Apple Inc", b"Former Apple Corporation"), old_instrument.published_at)
    old_cover = named_cover(cover(cik=old["issuer_cik"], title=old_title,
                                  url="https://www.sec.gov/Archives/edgar/data/123456/000012345626000045/old-cover.htm"),
                            "Former Apple Corporation", "2026-03-31")
    passage = (f'Effective 2026-04-01, Former Apple Corporation {old_title} CUSIP {old["cusip"]} '
               f'changes to Apple Inc {new_title} CUSIP {row["cusip"]}.')
    action = SourceSnapshot("corporate_action_filing", ACTION_URL, OBSERVED,
                            f"<html><body><p>{passage}</p></body></html>".encode(), PUBLISHED)
    sources = [catalog(), securities(), old_instrument, old_cover, new_instrument, new_cover, action]
    service = builder(sources, existing=[old])
    claim = {"ticker": "AAPL", "from_identity_sha256": identity_version_sha256(old),
             "to_identity": {field: row[field] for field in ("ticker", "issuer_cik", "cusip", "issuer_name", "class_description")},
             "action_snapshot_id": action.snapshot_id, "effective_on": "2026-04-01", "effective_date_text": "2026-04-01",
             "action_excerpt": passage, "from_name_text": "Former Apple Corporation", "to_name_text": "Apple Inc",
             "from_class_title": old_title, "to_class_title": new_title,
             "from_instrument_snapshot_id": old_instrument.snapshot_id, "to_instrument_snapshot_id": new_instrument.snapshot_id,
             "from_ticker_snapshot_id": old_cover.snapshot_id, "to_ticker_snapshot_id": new_cover.snapshot_id,
             "cusip_bridge_snapshot_id": action.snapshot_id, "cusip_bridge_excerpt": passage}
    return service, claim


class CorporateActionBridgeTests(unittest.TestCase):
    def test_complete_independent_fixture_is_reviewable_but_never_exports_or_extends_identity(self):
        service, claim = fixture()
        before = deepcopy(service.existing)
        report = review_corporate_action_bridge(service, claim)
        self.assertEqual("PRIMARY_BRIDGE_READY_FOR_EXPLICIT_REVIEW", report["status"])
        self.assertEqual([], report["blockers"])
        self.assertFalse(report["identity_transition_approved"])
        self.assertFalse(report["interval_extension_allowed"])
        self.assertFalse(report["automatic_alias_allowed"])
        self.assertEqual(before, service.existing)
        integrated = instrument_transition_review(service, tickers=["AAPL"], corporate_action_bridges=[claim])
        self.assertEqual(report, integrated["corporate_action_bridge_reviews"][0])
        claimed_hash = integrated.pop("review_sha256")
        self.assertEqual(claimed_hash, hashlib.sha256(json.dumps(integrated, sort_keys=True, separators=(",", ":")).encode()).hexdigest())

    def test_classification_keeps_no_par_and_different_exact_par_values_distinct(self):
        self.assertEqual({"share_class": "EQUITY:UNCLASSIFIED", "par_term": "NO_PAR"}, class_terms("Common Stock (no par value)"))
        self.assertEqual("USD:0.001", class_terms("Common Stock, par value $0.001 per share")["par_term"])
        self.assertNotEqual(class_terms("Common Stock, $0.01 par value"), class_terms("Common Stock, $0.001 par value"))
        self.assertIsNone(class_terms("Common Stock, without par value, $0.01 par value"))
        self.assertIsNone(class_terms("0.524% Notes due 2028"))

    def test_missing_new_instrument_and_cusip_action_stay_explicit_gaps(self):
        service, claim = fixture()
        claim["to_instrument_snapshot_id"] = None
        claim["cusip_bridge_snapshot_id"] = None
        report = review_corporate_action_bridge(service, claim)
        self.assertEqual("PRIMARY_BRIDGE_PARTIAL", report["status"])
        self.assertIn("TO_CIK_CUSIP_CLASS_BINDING_MISSING", report["blockers"])
        self.assertIn("DATED_OLD_NEW_CUSIP_ACTION_BRIDGE_MISSING", report["blockers"])

    def test_wrong_cik_instrument_is_a_conflict_not_a_name_based_identity(self):
        service, claim = fixture()
        claim["to_instrument_snapshot_id"] = claim["from_instrument_snapshot_id"]
        report = review_corporate_action_bridge(service, claim)
        self.assertIn("TO_STRUCTURED_INSTRUMENT_CONFLICT", report["blockers"])

    def test_later_correct_cover_cannot_discard_an_earlier_conflicting_class_claim(self):
        service, claim = fixture()
        conflicting = named_cover(cover(title="Common Stock, without par value", url=cover().url.replace("aapl-", "earlier-aapl-")), "Apple Inc")
        service.covers.extend(parse_ticker_filing(conflicting))
        service.snapshots[conflicting.snapshot_id] = conflicting
        report = review_corporate_action_bridge(service, claim)
        self.assertIn("CONFLICTING_TO_CLASS_SOURCE", report["blockers"])
        self.assertEqual("NO_PAR", class_terms(report["proofs"]["to"]["other_class_conflicts"][0]["class_title"])["par_term"])

    def test_wrong_primary_role_kind_or_unrelated_action_is_rejected(self):
        service, claim = fixture()
        claim["action_snapshot_id"] = claim["to_instrument_snapshot_id"]
        with self.assertRaisesRegex(ValueError, "wrong primary document kind"):
            review_corporate_action_bridge(service, claim)
        service, claim = fixture()
        action = service.snapshots[claim["action_snapshot_id"]]
        unrelated = SourceSnapshot(action.kind, action.url.replace("/data/320193/", "/data/999999/"), OBSERVED, action.payload, PUBLISHED)
        service.snapshots[unrelated.snapshot_id] = unrelated
        claim["action_snapshot_id"] = unrelated.snapshot_id
        with self.assertRaisesRegex(ValueError, "unrelated issuer"):
            review_corporate_action_bridge(service, claim)

    def test_action_day_and_exact_immutable_prior_hash_cannot_be_shifted(self):
        service, claim = fixture()
        claim["effective_on"] = "2026-04-02"
        with self.assertRaisesRegex(ValueError, "Action date"):
            review_corporate_action_bridge(service, claim)
        service, claim = fixture()
        service.existing[0]["effective_to"] = "2026-03-30"
        with self.assertRaisesRegex(ValueError, "exact immutable prior"):
            review_corporate_action_bridge(service, claim)

    def test_future_prior_knowledge_is_rejected(self):
        service, claim = fixture()
        service.existing[0]["known_at"] = (NOW + timedelta(seconds=1)).isoformat()
        claim["from_identity_sha256"] = identity_version_sha256(service.existing[0])
        with self.assertRaisesRegex(ValueError, "not yet known"):
            review_corporate_action_bridge(service, claim)

    def test_nil_issuer_name_cannot_complete_the_independent_name_binding(self):
        service, claim = fixture()
        original = service.snapshots[claim["to_ticker_snapshot_id"]]
        changed = SourceSnapshot(original.kind, original.url, original.observed_at,
                                 original.payload.replace(b'name="dei:EntityRegistrantName"', b'name="dei:EntityRegistrantName" xsi:nil="true"'),
                                 original.published_at)
        service.snapshots[changed.snapshot_id] = changed
        claim["to_ticker_snapshot_id"] = changed.snapshot_id
        with self.assertRaisesRegex(ValueError, "independent issuer-name fact"):
            review_corporate_action_bridge(service, claim)

    def test_exact_exhibit_type_envelope_preserves_hidden_exclusion_filename_and_raw_bytes(self):
        url = ACTION_URL.replace("synthetic-action.htm", "exhibit.htm")
        raw = (b"<DOCUMENT>\n<TYPE>EX-4.(VI)\n<SEQUENCE>5\n<FILENAME>exhibit.htm\n<DESCRIPTION>Exhibit\n<TEXT>\n"
               b"<HTML><BODY><P>Visible class statement.</P><DIV style='display:none'>Hidden identifier</DIV></BODY></HTML>"
               b"\n</TEXT>\n</DOCUMENT>\n")
        source = SourceSnapshot("corporate_action_filing", url, OBSERVED, raw, PUBLISHED)
        self.assertEqual("Visible class statement.", action_text(source))
        self.assertEqual(raw, source.payload)
        for changed in [raw.replace(b"exhibit.htm", b"other.htm"), raw.replace(b"</BODY>", b""), raw + raw]:
            with self.subTest(changed=changed[-20:]), self.assertRaises(ValueError):
                action_text(SourceSnapshot(source.kind, source.url, OBSERVED, changed, PUBLISHED))

    def test_withheld_new_body_metadata_cannot_be_loaded_as_a_bridge_proof(self):
        from evidence.batch5_identity_20261014.replay_bridge import new_captures
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "captures").mkdir()
            (root / "captures" / "blocked.metadata.json").write_text(json.dumps({"status": "WITHHELD_ACCESS_VALUE_BODY"}))
            with self.assertRaisesRegex(ValueError, "WITHHELD"):
                new_captures(root)

    def test_actual_xom_bridge_replays_exact_original_sources_and_preserves_old_interval(self):
        from evidence.batch5_identity_20261014.replay_bridge import build_inputs
        service, claim, report, diagnostics = build_inputs()
        bridge = report["corporate_action_bridge_reviews"][0]
        self.assertEqual(687, report["canonical_universe_size"])
        baseline = Path(__file__).resolve().parents[1] / "evidence" / "batch3_identity_bulk_20261012" / "registry_review.json"
        for historical in json.loads(baseline.read_bytes())["records"]:
            self.assertIn(historical["baseline_identity"], service.existing)
        self.assertEqual("2026-06-30", bridge["preserved_prior_identity"]["effective_to"])
        self.assertEqual("2026-06-30", bridge["proofs"]["from"]["instrument"]["event_date"])
        self.assertEqual("30231G102", bridge["proofs"]["from"]["instrument"]["cusip"])
        self.assertEqual("USD:0.001", bridge["proofs"]["to"]["ticker_class"]["class_terms"]["par_term"])
        self.assertEqual("2026-07-31", bridge["proofs"]["to"]["ticker_class"]["anchor_date"])
        self.assertIn("CONFLICTING_TO_CLASS_SOURCE", bridge["blockers"])
        self.assertEqual(6, diagnostics["new_primary_requests"])
        self.assertEqual(0, report["new_verified_identities"])
        self.assertFalse(diagnostics["network_requested_during_replay"])
        self.assertEqual(report, build_inputs()[2])


if __name__ == "__main__":
    unittest.main()
