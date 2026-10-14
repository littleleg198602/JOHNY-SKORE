"""Independent same-CIK symbol reviews; synthetic proofs and retained replay."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import unittest

from market_checker_app.services.sec13f_identity_bulk import SourceSnapshot
from market_checker_app.services.sec_corporate_action_bridge import review_corporate_action_bridge, review_symbol_rename_bridge
from market_checker_app.services.sec13f_transition_review import instrument_transition_review
from tests.test_batch5_identity_bridge import named_cover
from tests.test_sec13f_identity_bulk import OBSERVED, PUBLISHED, NOW, builder, catalog, cover, instrument, reviewed, securities
from tests.test_sec13f_identity_transitions import ACTION_URL, previous_version


def fixture():
    title = "Class A Common Stock, $0.01 par value per share"
    new_catalog = catalog([{"ticker": "APX", "cik_str": 320193, "title": "APPLE INC"}])
    current_list = securities([("037833100", "APPLE INC", "CL A")])
    old_instrument = instrument(title=title, event="03/31/2026",
                                url="https://www.sec.gov/Archives/edgar/data/320193/000032019326000044/old.xml")
    new_instrument = instrument(title=title)
    old_cover = named_cover(cover(title=title, url="https://www.sec.gov/Archives/edgar/data/320193/000032019326000043/old.htm"),
                            "Apple Inc", "2026-03-31")
    new_cover = named_cover(cover(ticker="APX", title=title), "Apple Inc")
    passage = (f"Effective 2026-04-01, Apple Inc changed its ticker symbol from AAPL to APX. "
               f"The unchanged {title} has CUSIP 037833100.")
    action = SourceSnapshot("corporate_action_filing", ACTION_URL, OBSERVED,
                            f"<html><body><p>{passage}</p></body></html>".encode(), PUBLISHED)
    sources = [new_catalog, current_list, old_instrument, new_instrument, old_cover, new_cover, action]
    claim = {"bridge_kind": "SAME_CIK_SYMBOL_RENAME", "ticker": "AAPL",
             "from_identity": {"ticker": "AAPL", "issuer_cik": "0000320193", "cusip": "037833100",
                               "issuer_name": "Apple Inc", "class_description": "CL A"},
             "to_identity": {"ticker": "APX", "issuer_cik": "0000320193", "cusip": "037833100",
                             "issuer_name": "APPLE INC", "class_description": "CL A"},
             "effective_on": "2026-04-01", "effective_date_text": "2026-04-01",
             "action_snapshot_id": action.snapshot_id, "action_excerpt": passage,
             "from_name_text": "Apple Inc", "to_name_text": "Apple Inc",
             "from_class_title": title, "to_class_title": title,
             "from_instrument_snapshot_id": old_instrument.snapshot_id, "to_instrument_snapshot_id": new_instrument.snapshot_id,
             "from_ticker_snapshot_id": old_cover.snapshot_id, "to_ticker_snapshot_id": new_cover.snapshot_id,
             "cusip_bridge_snapshot_id": action.snapshot_id, "cusip_bridge_excerpt": passage}
    return builder(sources), claim


def rebuild(service, extra=(), existing=None, replacements=None):
    replacements = replacements or {}
    return builder([replacements.get(source.snapshot_id, source) for source in service.snapshots.values()] + list(extra),
                   existing=existing if existing is not None else service.existing)


def revise_action(service, claim, old, new):
    action = service.snapshots[claim["action_snapshot_id"]]
    revised = SourceSnapshot(action.kind, action.url, action.observed_at, action.payload.replace(old.encode(), new.encode()),
                             action.published_at)
    claim["action_snapshot_id"] = revised.snapshot_id
    claim["action_excerpt"] = claim["action_excerpt"].replace(old, new)
    claim["cusip_bridge_snapshot_id"] = revised.snapshot_id
    claim["cusip_bridge_excerpt"] = claim["cusip_bridge_excerpt"].replace(old, new)
    return rebuild(service, replacements={action.snapshot_id: revised})


class SameCikSymbolBridgeTests(unittest.TestCase):
    def test_independent_exact_bridge_is_reviewable_without_alias_or_canonical_changes(self):
        service, claim = fixture()
        before = deepcopy((service.universe, service.existing, service.snapshots))
        report = review_symbol_rename_bridge(service, claim)
        self.assertEqual("PRIMARY_BRIDGE_READY_FOR_EXPLICIT_REVIEW", report["status"])
        self.assertEqual([], report["blockers"])
        self.assertEqual("UNVERIFIED_PRIMARY_CANDIDATES", report["tuple_status"])
        self.assertEqual([], report["preserved_prior_identities"])
        self.assertNotIn("APX", service.universe_set)
        self.assertFalse(report["automatic_alias_allowed"])
        self.assertFalse(report["identity_transition_approved"])
        self.assertFalse(report["interval_extension_allowed"])
        self.assertEqual(before, (service.universe, service.existing, service.snapshots))
        self.assertEqual(report, review_corporate_action_bridge(service, claim))
        integrated = instrument_transition_review(service, tickers=["AAPL"], corporate_action_bridges=[claim])
        self.assertEqual(report, integrated["corporate_action_bridge_reviews"][0])
        claimed_hash = report.pop("bridge_sha256")
        self.assertEqual(claimed_hash, hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":")).encode()).hexdigest())

    def test_a_prior_registry_interval_and_knowledge_clock_are_preserved_exactly(self):
        service, claim = fixture()
        sources = list(service.snapshots.values())
        prior = previous_version(reviewed(sources, title="CL A"))
        service = rebuild(service, existing=[prior])
        before = deepcopy(prior)
        report = review_symbol_rename_bridge(service, claim)
        self.assertEqual([before], report["preserved_prior_identities"])
        self.assertEqual([before], service.existing)
        self.assertEqual("2026-03-31", service.existing[0]["effective_to"])

    def test_same_cik_and_catalog_names_cannot_supply_an_effective_day(self):
        service, claim = fixture()
        service = revise_action(service, claim, "Effective 2026-04-01", "In April 2026")
        claim["effective_on"] = claim["effective_date_text"] = None
        claim["cusip_bridge_snapshot_id"] = None
        report = review_symbol_rename_bridge(service, claim)
        self.assertIn("SYMBOL_ACTION_EFFECTIVE_DAY_MISSING", report["blockers"])
        self.assertIsNone(report["effective_on"])
        self.assertEqual({"latest_old_anchor": "2026-03-31", "earliest_new_anchor": "2026-06-30",
                          "establishes_effective_day": False}, report["observed_symbol_anchor_bracket"])

    def test_claimed_first_day_of_a_month_does_not_become_a_proven_effective_day(self):
        service, claim = fixture()
        service = revise_action(service, claim, "Effective 2026-04-01", "In April 2026")
        report = review_symbol_rename_bridge(service, claim)
        self.assertIn("SYMBOL_ACTION_EFFECTIVE_DAY_NOT_PROVEN", report["blockers"])
        self.assertEqual("2026-04-01", report["proposed_effective_on"])
        self.assertIsNone(report["effective_on"])
        self.assertIn("DATED_SYMBOL_CUSIP_ACTION_BRIDGE_NOT_PROVEN", report["blockers"])

    def test_reversed_or_disconnected_symbols_do_not_prove_a_directed_rename(self):
        for replacement in ("ticker symbol from APX to AAPL", "ticker symbols AAPL and APX",
                            "ticker symbol from AAPL to APX.A", "ticker symbol from AAPL to APXX"):
            service, claim = fixture()
            service = revise_action(service, claim, "ticker symbol from AAPL to APX", replacement)
            report = review_symbol_rename_bridge(service, claim)
            self.assertIn("DIRECTED_OLD_NEW_SYMBOL_ACTION_MISSING", report["blockers"])

    def test_changed_cik_cusip_or_unchanged_symbol_requires_a_separate_transition_contract(self):
        for field, value in (("issuer_cik", "0000123456"), ("cusip", "02079K107"), ("ticker", "AAPL")):
            service, claim = fixture()
            claim["to_identity"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "unchanged exact CIK/CUSIP"):
                review_symbol_rename_bridge(service, claim)

    def test_one_generic_class_schedule_cannot_supply_exact_class_a_binding(self):
        service, claim = fixture()
        source = service.snapshots[claim["from_instrument_snapshot_id"]]
        revised = SourceSnapshot(source.kind, source.url, source.observed_at,
                                 source.payload.replace(claim["from_class_title"].encode(), b"Common Stock"), source.published_at)
        claim["from_instrument_snapshot_id"] = revised.snapshot_id
        service = rebuild(service, replacements={source.snapshot_id: revised})
        report = review_symbol_rename_bridge(service, claim)
        self.assertIn("FROM_STRUCTURED_INSTRUMENT_CONFLICT", report["blockers"])
        self.assertEqual("Common Stock", report["proofs"]["from"]["instrument"]["class_title"])

    def test_two_versions_of_one_document_url_are_not_independent_old_new_sources(self):
        service, claim = fixture()
        old = service.snapshots[claim["from_instrument_snapshot_id"]]
        new = service.snapshots[claim["to_instrument_snapshot_id"]]
        same_url = SourceSnapshot(old.kind, new.url, old.observed_at, old.payload, old.published_at)
        claim["from_instrument_snapshot_id"] = same_url.snapshot_id
        report = review_symbol_rename_bridge(rebuild(service, replacements={old.snapshot_id: same_url}), claim)
        self.assertIn("OLD_NEW_INDEPENDENT_DOCUMENTS_MISSING", report["blockers"])

    def test_duplicate_same_context_issuer_names_cannot_be_selected_by_matching_one(self):
        service, claim = fixture()
        source = service.snapshots[claim["to_ticker_snapshot_id"]]
        duplicate = b'<ix:nonNumeric name="dei:EntityRegistrantName" contextRef="Security">Competing Issuer Inc</ix:nonNumeric>'
        revised = SourceSnapshot(source.kind, source.url, source.observed_at,
                                 source.payload.replace(b"</html>", duplicate+b"</html>"), source.published_at)
        claim["to_ticker_snapshot_id"] = revised.snapshot_id
        report = review_symbol_rename_bridge(rebuild(service, replacements={source.snapshot_id: revised}), claim)
        self.assertIn("TO_INDEPENDENT_ISSUER_NAME_BINDING_MISSING", report["blockers"])
        self.assertEqual([], report["proofs"]["to"]["independent_issuer_names"])

    def test_competing_name_and_par_terms_are_preserved_despite_good_selected_cover(self):
        for name, title in (("Competing Issuer Inc", "Class A Common Stock, $0.01 par value per share"),
                            ("Apple Inc", "Class A Common Stock, $0.001 par value per share")):
            service, claim = fixture()
            competing = named_cover(cover(ticker="APX", title=title,
                                           url="https://www.sec.gov/Archives/edgar/data/320193/000032019326000042/other.htm"),
                                    name, "2026-06-15")
            report = review_symbol_rename_bridge(rebuild(service, extra=[competing]), claim)
            self.assertIn("CONFLICTING_TO_NAME_OR_CLASS_SOURCE", report["blockers"])
            self.assertEqual(competing.url, report["proofs"]["to"]["competing_name_or_class_sources"][0]["url"])

    def test_competing_symbol_date_after_the_action_is_not_discarded(self):
        service, claim = fixture()
        competing = named_cover(cover(title=claim["from_class_title"],
                                       url="https://www.sec.gov/Archives/edgar/data/320193/000032019326000042/late-old.htm"),
                                "Apple Inc", "2026-06-15")
        report = review_symbol_rename_bridge(rebuild(service, extra=[competing]), claim)
        self.assertIn("CONFLICTING_FROM_SYMBOL_DATE_SOURCE", report["blockers"])
        self.assertEqual("2026-06-15", report["observed_symbol_anchor_bracket"]["latest_old_anchor"])

    def test_old_and_new_symbols_at_the_same_anchor_are_an_order_conflict(self):
        service, claim = fixture()
        competing = named_cover(cover(ticker="APX", title=claim["to_class_title"],
                                       url="https://www.sec.gov/Archives/edgar/data/320193/000032019326000042/early-new.htm"),
                                "Apple Inc", "2026-03-31")
        report = review_symbol_rename_bridge(rebuild(service, extra=[competing]), claim)
        self.assertIn("COMPETING_SYMBOL_ANCHOR_ORDER", report["blockers"])
        self.assertIn("CONFLICTING_TO_SYMBOL_DATE_SOURCE", report["blockers"])

    def test_issuer_name_in_a_different_context_cannot_bind_the_symbol(self):
        service, claim = fixture()
        source = service.snapshots[claim["to_ticker_snapshot_id"]]
        raw = source.payload.replace(b'name="dei:EntityRegistrantName" contextRef="Security"',
                                     b'name="dei:EntityRegistrantName" contextRef="Other"')
        context = ('<xbrli:context id="Other"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">'
                   '0000320193</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2026-06-30'
                   '</xbrli:instant></xbrli:period></xbrli:context>')
        revised = SourceSnapshot(source.kind, source.url, source.observed_at, raw.replace(b"</html>", context.encode()+b"</html>"),
                                 source.published_at)
        claim["to_ticker_snapshot_id"] = revised.snapshot_id
        report = review_symbol_rename_bridge(rebuild(service, replacements={source.snapshot_id: revised}), claim)
        self.assertIn("TO_INDEPENDENT_ISSUER_NAME_BINDING_MISSING", report["blockers"])

    def test_observation_and_publication_clocks_cannot_be_backdated_to_the_action_day(self):
        service, claim = fixture()
        report = review_symbol_rename_bridge(service, claim)
        self.assertEqual(OBSERVED.isoformat(), report["evidence_known_at"])
        self.assertNotEqual(report["effective_on"], report["evidence_known_at"][:10])
        self.assertFalse(report["historical_knowledge_reconstructed"])
        service.as_of = OBSERVED-timedelta(seconds=1)
        with self.assertRaisesRegex(ValueError, "future source knowledge"):
            review_symbol_rename_bridge(service, claim)

    def test_future_prior_identity_knowledge_is_not_used(self):
        service, claim = fixture()
        prior = previous_version(reviewed(list(service.snapshots.values()), title="CL A"), known_at=(NOW+timedelta(days=1)).isoformat())
        with self.assertRaisesRegex(ValueError, "future prior-identity knowledge"):
            review_symbol_rename_bridge(rebuild(service, existing=[prior]), claim)

    def test_later_prior_registry_knowledge_is_included_in_the_evidence_clock(self):
        service, claim = fixture()
        later_known = NOW-timedelta(minutes=5)
        prior = previous_version(reviewed(list(service.snapshots.values()), title="CL A"), known_at=later_known.isoformat())
        report = review_symbol_rename_bridge(rebuild(service, existing=[prior]), claim)
        self.assertEqual(later_known.isoformat(), report["evidence_known_at"])
        self.assertEqual(prior["known_at"], report["preserved_prior_identities"][0]["known_at"])

    def test_effective_day_after_action_publication_is_a_source_clock_conflict(self):
        service, claim = fixture()
        service = revise_action(service, claim, "2026-04-01", "2026-07-21")
        claim["effective_on"] = claim["effective_date_text"] = "2026-07-21"
        report = review_symbol_rename_bridge(service, claim)
        self.assertIn("SYMBOL_ACTION_EFFECTIVE_DAY_CLOCK_CONFLICT", report["blockers"])
        self.assertIsNone(report["effective_on"])

    def test_current_catalog_collision_does_not_resolve_from_name_or_same_cik_assumption(self):
        service, claim = fixture()
        old_catalog = service.catalog_snapshot
        collision = catalog([{"ticker": "APX", "cik_str": 320193, "title": "APPLE INC"},
                             {"ticker": "APX", "cik_str": 123456, "title": "APPLE INC"}])
        report = review_symbol_rename_bridge(rebuild(service, replacements={old_catalog.snapshot_id: collision}), claim)
        self.assertIn("TO_OBSERVED_CATALOG_CIK_UNRESOLVED", report["blockers"])

    def test_actual_retained_sources_are_partial_and_first_xom_artifacts_replay_unchanged(self):
        from evidence.batch5_identity_symbol_20261014.replay_symbol import build_inputs
        from evidence.batch5_identity_20261014.replay_bridge import build_inputs as old_inputs
        root = Path(__file__).resolve().parents[1]
        service, claim, report, diagnostics = build_inputs()
        evidence_root = root / "evidence" / "batch5_identity_symbol_20261014"
        self.assertEqual(claim, json.loads((evidence_root / "symbol_proposal.json").read_bytes()))
        self.assertEqual(report, json.loads((evidence_root / "review.json").read_bytes()))
        self.assertEqual(diagnostics, json.loads((evidence_root / "replay_diagnostics.json").read_bytes()))
        bridge = report["corporate_action_bridge_reviews"][0]
        self.assertEqual("PRIMARY_BRIDGE_PARTIAL", bridge["status"])
        self.assertIn("SYMBOL_ACTION_EFFECTIVE_DAY_MISSING", bridge["blockers"])
        self.assertIn("FROM_STRUCTURED_INSTRUMENT_CONFLICT", bridge["blockers"])
        self.assertIn("TO_CIK_CUSIP_CLASS_BINDING_MISSING", bridge["blockers"])
        self.assertEqual([], bridge["preserved_prior_identities"])
        self.assertIsNone(bridge["effective_on"])
        self.assertEqual(0, diagnostics["new_http_requests"])
        self.assertEqual(687, len(service.universe))
        self.assertNotIn("P", service.universe_set)
        self.assertFalse(bridge["automatic_alias_allowed"])
        original = json.loads((root / "evidence" / "batch5_identity_20261014" / "review.json").read_bytes())
        self.assertEqual(original, old_inputs()[2])


if __name__ == "__main__":
    unittest.main()
