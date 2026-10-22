"""Synthetic independent merger gates plus exact actual LEG original-byte replay."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from market_checker_app.services.sec13f_identity_bulk import SourceSnapshot
from market_checker_app.services.sec_corporate_action_bridge import (
    Form25Snapshot, class_terms, parse_form25, review_corporate_action_bridge, review_stock_merger_bridge,
)
from market_checker_app.services.sec13f_transition_review import instrument_transition_review
from tests.test_batch5_identity_bridge import named_cover
from tests.test_sec13f_identity_bulk import OBSERVED, PUBLISHED, NOW, builder, catalog, cover, instrument, reviewed, securities
from tests.test_sec13f_identity_transitions import previous_version

OLD_CIK, OLD_CUSIP, OLD_NAME = "0000123456", "02079K107", "Former Apple Corporation"
FORM25_URL = "https://www.sec.gov/Archives/edgar/data/123456/000087666126000001/primary_doc.xml"


def withdrawal(*, raw=None, signed="2026-04-02", observed=OBSERVED, published=PUBLISHED):
    raw = raw or (f'<notificationOfRemoval><schemaVersion>X0203</schemaVersion><exchange><cik>0000876661</cik>'
                  '<entityName>NEW YORK STOCK EXCHANGE LLC</entityName></exchange>'
                  f'<issuer><cik>{OLD_CIK}</cik><entityName>{OLD_NAME}</entityName></issuer>'
                  '<descriptionClassSecurity>Common Stock</descriptionClassSecurity>'
                  '<ruleProvision>17 CFR 240.12d2-2(a)(3)</ruleProvision>'
                  f'<signatureData><signatureDate>{signed}</signatureDate></signatureData></notificationOfRemoval>').encode()
    return Form25Snapshot(FORM25_URL, observed, raw, published)


def fixture():
    old_title, new_title = "Common Stock, without par value", "Common Stock, $0.01 par value"
    old_instrument = instrument(cik=OLD_CIK, cusip=OLD_CUSIP, title=old_title, event="03/31/2026",
                                url="https://www.sec.gov/Archives/edgar/data/123456/000012345626000044/old.xml")
    old_instrument = SourceSnapshot(old_instrument.kind, old_instrument.url, old_instrument.observed_at,
                                    old_instrument.payload.replace(b"Apple Inc", OLD_NAME.encode()), old_instrument.published_at)
    new_instrument = instrument(title=new_title)
    old_cover = named_cover(cover(cik=OLD_CIK, title=old_title,
                                  url="https://www.sec.gov/Archives/edgar/data/123456/000012345626000045/old.htm"),
                            OLD_NAME, "2026-03-31")
    new_cover = named_cover(cover(ticker="APX", title=new_title), "Apple Inc")
    exchange = (f'Each share of {OLD_NAME} {old_title} CUSIP {OLD_CUSIP} was automatically converted into the '
                f'right to receive 0.1455 shares of Apple Inc {new_title} CUSIP 037833100, '
                'with cash paid in lieu of any fractional shares.')
    text = f'On 2026-04-01, Apple Inc consummated the merger with {OLD_NAME}. {exchange}'
    action = SourceSnapshot("corporate_action_filing", "https://www.sec.gov/Archives/edgar/data/123456/000012345626000046/action.htm",
                            OBSERVED, f"<html><body><p>{text}</p></body></html>".encode(), PUBLISHED)
    completion = SourceSnapshot("corporate_action_filing", "https://www.sec.gov/Archives/edgar/data/320193/000032019326000046/complete.htm",
                                OBSERVED, f"<html><body><p>{text}</p></body></html>".encode(), PUBLISHED)
    form25 = withdrawal()
    sources = [catalog([{"ticker": "APX", "cik_str": 320193, "title": "APPLE INC"}]),
               securities([(OLD_CUSIP, OLD_NAME.upper(), "COM"), ("037833100", "APPLE INC", "COM")]),
               old_instrument, new_instrument, old_cover, new_cover, action, completion, form25]
    claim = {"bridge_kind": "STOCK_MERGER_EXCHANGE", "ticker": "AAPL",
             "from_identity": {"ticker": "AAPL", "issuer_cik": OLD_CIK, "cusip": OLD_CUSIP,
                               "issuer_name": OLD_NAME.upper(), "class_description": "COM"},
             "to_identity": {"ticker": "APX", "issuer_cik": "0000320193", "cusip": "037833100",
                             "issuer_name": "APPLE INC", "class_description": "COM"},
             "effective_on": "2026-04-01", "effective_date_text": "2026-04-01", "exchange_ratio": "0.1455",
             "fractional_cash_only": True, "from_action_name_text": OLD_NAME, "to_action_name_text": "Apple Inc",
             "from_class_title": old_title, "to_class_title": new_title,
             "action_snapshot_id": action.snapshot_id, "action_excerpt": text, "exchange_excerpt": exchange,
             "completion_snapshot_id": completion.snapshot_id, "completion_excerpt": text,
             "from_instrument_snapshot_id": old_instrument.snapshot_id, "to_instrument_snapshot_id": new_instrument.snapshot_id,
             "from_ticker_snapshot_id": old_cover.snapshot_id, "to_ticker_snapshot_id": new_cover.snapshot_id,
             "withdrawal_snapshot_id": form25.snapshot_id, "cusip_bridge_snapshot_id": action.snapshot_id, "cusip_bridge_excerpt": text}
    return builder(sources), claim


def rebuild(service, replacements=None, extra=(), existing=None):
    replacements = replacements or {}
    return builder([replacements.get(source.snapshot_id, source) for source in service.snapshots.values()]+list(extra),
                   existing=service.existing if existing is None else existing)


class LegActionBridgeTests(unittest.TestCase):
    def test_independent_merger_proof_is_reviewable_without_identity_or_delisting_inference(self):
        service, claim = fixture()
        before = deepcopy((service.existing, service.universe, service.snapshots))
        report = review_stock_merger_bridge(service, claim)
        self.assertEqual("PRIMARY_BRIDGE_READY_FOR_EXPLICIT_REVIEW", report["status"])
        self.assertEqual([], report["blockers"])
        self.assertEqual("2026-04-01", report["merger_effective_on"])
        self.assertEqual("0.1455", report["exchange_ratio"])
        self.assertIsNone(report["actual_delisting_effective_on"])
        self.assertEqual("UNKNOWN", report["delisting_effective_date_status"])
        self.assertFalse(report["identity_transition_approved"])
        self.assertFalse(report["automatic_alias_allowed"])
        self.assertFalse(report["interval_extension_allowed"])
        self.assertEqual(before, (service.existing, service.universe, service.snapshots))
        self.assertEqual(report, review_corporate_action_bridge(service, claim))
        integrated = instrument_transition_review(service, tickers=["AAPL"], corporate_action_bridges=[claim])
        self.assertEqual(report, integrated["corporate_action_bridge_reviews"][0])
        digest = report.pop("bridge_sha256")
        self.assertEqual(digest, hashlib.sha256(json.dumps(report,sort_keys=True,separators=(",", ":")).encode()).hexdigest())

    def test_deleted_or_absent_old_list_does_not_prove_merger_or_delisting(self):
        for absent in (False, True):
            service, claim = fixture()
            if absent:
                listing = securities([("037833100", "APPLE INC", "COM")])
            else:
                raw = service.list_snapshot.payload.splitlines()
                raw[0] = raw[0][:-13] + b"*D*         E"
                listing = SourceSnapshot("13f_list", service.list_snapshot.url, OBSERVED, b"\n".join(raw)+b"\n")
            service = rebuild(service, {service.list_snapshot.snapshot_id: listing})
            claim["action_snapshot_id"] = claim["completion_snapshot_id"] = claim["withdrawal_snapshot_id"] = None
            claim["cusip_bridge_snapshot_id"] = None
            report = review_stock_merger_bridge(service, claim)
            self.assertEqual("PRIMARY_BRIDGE_PARTIAL", report["status"])
            self.assertIsNone(report["merger_effective_on"])
            self.assertIsNone(report["actual_delisting_effective_on"])
            self.assertFalse(report["deleted_list_is_delisting_proof"])
            self.assertFalse(report["absent_catalog_is_delisting_proof"])

    def test_exact_leading_decimal_par_spelling_is_supported_without_collapsing_values(self):
        self.assertEqual(class_terms("Common Stock, $.01 par value"), class_terms("Common Stock, $0.01 par value"))
        self.assertNotEqual(class_terms("Common Stock, $.01 par value"), class_terms("Common Stock, $.001 par value"))
        self.assertIsNone(class_terms("Common Stock, without par value, $.01 par value"))

    def test_fractional_cash_does_not_become_all_cash_or_a_different_share_ratio(self):
        service, claim = fixture()
        claim["exchange_ratio"] = "0.1456"
        report = review_stock_merger_bridge(service, claim)
        self.assertIn("EXACT_STOCK_RATIO_OR_FRACTIONAL_CASH_SCOPE_UNPROVEN", report["blockers"])
        self.assertFalse(report["fractional_cash_only"])
        service, claim = fixture()
        original = service.snapshots[claim["action_snapshot_id"]]
        raw = original.payload.replace(b"with cash paid in lieu of any fractional shares", b"with cash consideration and cash in lieu of fractional shares")
        revised = SourceSnapshot(original.kind,original.url,OBSERVED,raw,PUBLISHED)
        claim["action_snapshot_id"] = revised.snapshot_id
        claim["action_excerpt"] = claim["action_excerpt"].replace("with cash paid in lieu of any fractional shares", "with cash consideration and cash in lieu of fractional shares")
        claim["exchange_excerpt"] = claim["exchange_excerpt"].replace("with cash paid in lieu of any fractional shares", "with cash consideration and cash in lieu of fractional shares")
        claim["cusip_bridge_snapshot_id"] = revised.snapshot_id
        claim["cusip_bridge_excerpt"] = claim["action_excerpt"]
        report = review_stock_merger_bridge(rebuild(service,{original.snapshot_id:revised}),claim)
        self.assertIn("EXACT_STOCK_RATIO_OR_FRACTIONAL_CASH_SCOPE_UNPROVEN",report["blockers"])

    def test_same_recipient_completion_file_is_not_independent_ticker_proof(self):
        service, claim = fixture()
        cover_source = service.snapshots[claim["to_ticker_snapshot_id"]]
        completion = service.snapshots[claim["completion_snapshot_id"]]
        same_url = SourceSnapshot(cover_source.kind,completion.url,OBSERVED,cover_source.payload,PUBLISHED)
        claim["to_ticker_snapshot_id"] = same_url.snapshot_id
        report = review_stock_merger_bridge(rebuild(service,{cover_source.snapshot_id:same_url}),claim)
        self.assertIn("TO_INDEPENDENT_PRIMARY_DOCUMENTS_MISSING",report["blockers"])

    def test_a_later_post_conversion_old_schedule_is_retained_without_active_identity_inference(self):
        service, claim = fixture()
        original = service.snapshots[claim["from_instrument_snapshot_id"]]
        later = SourceSnapshot(original.kind,original.url.replace("old.xml","later.xml"),OBSERVED,
                               original.payload.replace(b"03/31/2026",b"04/30/2026"),PUBLISHED)
        report = review_stock_merger_bridge(rebuild(service,extra=[later]),claim)
        self.assertEqual("POST_CONVERSION_OLD_INSTRUMENT_ANCHOR",report["proofs"]["from"]["competing_sources"][0]["scope"])
        self.assertFalse(report["identity_transition_approved"])

    def test_different_anchor_days_cannot_be_extended_to_an_exact_dated_tuple(self):
        service,claim=fixture()
        original=service.snapshots[claim["to_instrument_snapshot_id"]]
        revised=SourceSnapshot(original.kind,original.url,OBSERVED,original.payload.replace(b"06/30/2026",b"06/29/2026"),PUBLISHED)
        claim["to_instrument_snapshot_id"]=revised.snapshot_id
        report=review_stock_merger_bridge(rebuild(service,{original.snapshot_id:revised}),claim)
        self.assertIn("TO_DATED_TUPLE_ANCHORS_DO_NOT_ALIGN",report["blockers"])
        self.assertEqual("DIFFERENT_DATED_ANCHORS",report["proofs"]["to"]["anchor_alignment"])

    def test_direct_indirect_control_relation_conflict_is_preserved(self):
        service,claim=fixture()
        replacements={}
        for role,relation in (("action","indirect"),("completion","direct")):
            source=service.snapshots[claim[role+"_snapshot_id"]]
            suffix=f" {OLD_NAME} became a {relation}, wholly owned subsidiary of Apple Inc."
            revised=SourceSnapshot(source.kind,source.url,OBSERVED,source.payload.replace(b"</p>",suffix.encode()+b"</p>"),PUBLISHED)
            replacements[source.snapshot_id]=revised;claim[role+"_snapshot_id"]=revised.snapshot_id;claim[role+"_excerpt"]+=suffix
            if role == "action":
                claim["cusip_bridge_snapshot_id"] = revised.snapshot_id
        report=review_stock_merger_bridge(rebuild(service,replacements),claim)
        self.assertIn("DIRECT_INDIRECT_CONTROL_RELATION_CONFLICT",report["blockers"])

    def test_announced_future_completion_is_not_a_completed_merger_action(self):
        service, claim = fixture()
        source = service.snapshots[claim["action_snapshot_id"]]
        revised = SourceSnapshot(source.kind, source.url, OBSERVED,
                                 source.payload.replace(b"consummated the merger", b"announced a plan for completion of the merger"), PUBLISHED)
        claim["action_snapshot_id"] = claim["cusip_bridge_snapshot_id"] = revised.snapshot_id
        claim["action_excerpt"] = claim["cusip_bridge_excerpt"] = claim["action_excerpt"].replace(
            "consummated the merger", "announced a plan for completion of the merger")
        report = review_stock_merger_bridge(rebuild(service, {source.snapshot_id: revised}), claim)
        self.assertIn("ACTION_COMPLETION_LANGUAGE_MISSING", report["blockers"])
        self.assertEqual("PRIMARY_BRIDGE_PARTIAL", report["status"])
        self.assertIsNone(report["merger_effective_on"])

    def test_report_date_or_form25_signature_cannot_fill_a_missing_action_day(self):
        service, claim = fixture()
        claim["effective_on"] = claim["effective_date_text"] = None
        report = review_stock_merger_bridge(service, claim)
        self.assertIn("ACTION_MERGER_EFFECTIVE_DAY_UNPROVEN", report["blockers"])
        self.assertIsNone(report["merger_effective_on"])
        self.assertIsNone(report["actual_delisting_effective_on"])

    def test_a_header_day_cannot_date_an_action_without_both_exact_named_issuers(self):
        service, claim = fixture()
        claim["from_action_name_text"] = "Unrelated Company Inc"
        report = review_stock_merger_bridge(service, claim)
        self.assertIn("ACTION_NAMED_ISSUERS_NOT_BOUND", report["blockers"])
        self.assertIsNone(report["merger_effective_on"])

    def test_form25_original_bytes_issuer_class_and_clock_are_separate_from_effective_delisting(self):
        source=withdrawal();fact=parse_form25(source)
        self.assertEqual(OLD_CIK,fact["issuer_cik"])
        self.assertEqual("2026-04-02",fact["signature_date"])
        self.assertEqual("FILED_WITHDRAWAL_NOTIFICATION_ONLY",fact["source_scope"])
        self.assertIsNone(fact["actual_delisting_effective_on"])
        self.assertEqual(hashlib.sha256(source.payload).hexdigest(),source.sha256)
        with self.assertRaisesRegex(ValueError,"precedes"):
            withdrawal(observed=PUBLISHED-timedelta(seconds=1))
        service,claim=fixture();signed_early=withdrawal(signed="2026-03-31")
        claim["withdrawal_snapshot_id"]=signed_early.snapshot_id
        report=review_stock_merger_bridge(rebuild(service,{source.snapshot_id:signed_early}),claim)
        self.assertIn("FORM25_ISSUER_CLASS_OR_SIGNATURE_CONFLICT",report["blockers"])

    def test_form25_namespace_duplicate_issuer_dtd_and_wrong_archive_are_rejected(self):
        source=withdrawal()
        for raw in (source.payload.replace(b"<notificationOfRemoval>",b'<notificationOfRemoval xmlns="urn:untrusted">'),
                    source.payload.replace(b"</issuer>",b'<cik xmlns="urn:untrusted">320193</cik></issuer>'),
                    source.payload.replace(b"</issuer>",b"</issuer><issuer><cik>123456</cik></issuer>"),
                    b'<!DOCTYPE notificationOfRemoval [<!ENTITY hostile "untrusted">]>'+source.payload,
                    source.payload.replace(OLD_CIK.encode(),b"0000320193")):
            with self.subTest(raw_sha256=hashlib.sha256(raw).hexdigest()),self.assertRaises(ValueError):
                parse_form25(withdrawal(raw=raw))

    def test_wrong_recipient_cik_is_rejected_and_future_knowledge_is_not_used(self):
        service,claim=fixture()
        claim["completion_snapshot_id"]=claim["action_snapshot_id"]
        with self.assertRaisesRegex(ValueError,"unrelated exact issuer"):
            review_stock_merger_bridge(service,claim)
        service,claim=fixture();service.as_of=OBSERVED-timedelta(seconds=1)
        with self.assertRaisesRegex(ValueError,"future knowledge"):
            review_stock_merger_bridge(service,claim)

    def test_prior_intervals_and_later_knowledge_are_preserved_without_export(self):
        service,claim=fixture()
        row=previous_version(reviewed(list(service.snapshots.values())),issuer_cik=OLD_CIK,cusip=OLD_CUSIP,
                             issuer_name=OLD_NAME,class_description="COM",known_at=(NOW-timedelta(minutes=5)).isoformat())
        report=review_stock_merger_bridge(rebuild(service,existing=[row]),claim)
        self.assertEqual([row],report["preserved_prior_identities"])
        self.assertEqual(row["known_at"],report["evidence_known_at"])
        self.assertEqual("2026-03-31",report["preserved_prior_identities"][0]["effective_to"])

    def test_whole_withheld_capture_cannot_be_read_or_used_as_a_form25_source(self):
        from evidence.batch6_identity_20261014.replay_review import new_captures
        with TemporaryDirectory() as temporary:
            directory=Path(temporary);(directory/"captures").mkdir()
            (directory/"captures"/"withheld.metadata.json").write_text(json.dumps({"status":"WITHHELD_SENSITIVE_BODY",
                                                                                 "body_path":"absent-private-body.bin"}))
            with self.assertRaisesRegex(ValueError,"WITHHELD"):
                new_captures(directory)

    def test_actual_leg_review_matches_original_bytes_and_never_promotes_deleted_stock(self):
        from evidence.batch6_identity_20261014.replay_review import build_inputs
        root=Path(__file__).resolve().parents[1];here=root/"evidence/batch6_identity_20261014"
        service,claim,report,diagnostics=build_inputs()
        self.assertEqual(report,json.loads((here/"review.json").read_bytes()))
        self.assertEqual(claim,json.loads((here/"bridge_proposal.json").read_bytes()))
        self.assertEqual(diagnostics,json.loads((here/"replay_diagnostics.json").read_bytes()))
        bridge=report["corporate_action_bridge_reviews"][0]
        self.assertEqual("PRIMARY_BRIDGE_PARTIAL",bridge["status"])
        self.assertEqual("2026-08-26",bridge["merger_effective_on"])
        self.assertEqual("2026-08-27",bridge["proofs"]["withdrawal_notification"]["signature_date"])
        self.assertIsNone(bridge["actual_delisting_effective_on"])
        self.assertEqual("2026-08-25",bridge["proofs"]["to"]["ticker_class"]["anchor_date"])
        self.assertEqual("2026-07-31",bridge["proofs"]["from"]["instrument"]["event_date"])
        self.assertIn("DIRECT_INDIRECT_CONTROL_RELATION_CONFLICT",bridge["blockers"])
        self.assertIn("DATED_OLD_NEW_CUSIP_ACTION_BRIDGE_MISSING",bridge["blockers"])
        self.assertEqual([],bridge["preserved_prior_identities"])
        self.assertEqual(6,diagnostics["new_primary_http_requests"])
        self.assertFalse(diagnostics["app_quota_managed_live_claimed"])
        self.assertFalse(diagnostics["network_requested_during_replay"])
        self.assertEqual(687,len(service.universe))
        self.assertFalse(bridge["production_registry_changed"])
        for entry in diagnostics["source_inventory"]:
            raw=(root/entry["path"]).read_bytes()
            self.assertEqual(entry["sha256"],hashlib.sha256(raw).hexdigest())


if __name__=="__main__":
    unittest.main()
