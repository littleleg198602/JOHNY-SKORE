"""Directed dated control scopes and exact retained LEG/SGI original bytes."""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from market_checker_app.services.sec13f_identity_bulk import SourceSnapshot
from market_checker_app.services.sec_corporate_action_bridge import Form25Snapshot
from market_checker_app.services.sec_control_instrument_review import control_text, review_control_instrument_bridge
from tests.test_batch5_identity_bridge import named_cover
from tests.test_batch6_leg_action_bridge import fixture as merger_fixture, rebuild
from tests.test_sec13f_identity_bulk import OBSERVED, PUBLISHED, cover


def fixture():
    service, stock = merger_fixture()
    replacements = {}
    for source in service.snapshots.values():
        raw = source.payload
        if source.snapshot_id == stock["from_instrument_snapshot_id"]:
            raw = raw.replace(b"03/31/2026", b"04/01/2026")
        elif source.snapshot_id == stock["from_ticker_snapshot_id"]:
            raw = raw.replace(b"2026-03-31", b"2026-04-01")
        elif source.kind == "corporate_action_filing":
            raw = raw.replace(b"2026-04-01", b"2026-04-15")
        elif source.kind == "form25_nse":
            raw = raw.replace(b"2026-04-02", b"2026-04-16")
        if raw != source.payload:
            cls = Form25Snapshot if source.kind == "form25_nse" else SourceSnapshot
            revised = (cls(source.url, source.observed_at, raw, source.published_at) if cls is Form25Snapshot else
                       cls(source.kind, source.url, source.observed_at, raw, source.published_at))
            replacements[source.snapshot_id] = revised
    for field, value in list(stock.items()):
        if field.endswith("snapshot_id") and value in replacements:
            stock[field] = replacements[value].snapshot_id
        elif isinstance(value, str) and "2026-04-01" in value:
            stock[field] = value.replace("2026-04-01", "2026-04-15")
    service = rebuild(service, replacements)
    controls = []
    additions = []
    for index, issuer_role in enumerate(("from", "to")):
        source, edge = disclosure(stock, index=index, issuer_role=issuer_role)
        additions.append(source)
        controls.append(edge)
    service = rebuild(service, extra=additions)
    request = {"stock_merger": stock, "control_disclosures": controls,
               "instrument_disclosures": [{"role": role, "anchor_on": "2026-04-01" if role == "from" else "2026-06-30",
                    "instrument_snapshot_id": stock[f"{role}_instrument_snapshot_id"],
                    "ticker_snapshot_id": stock[f"{role}_ticker_snapshot_id"]} for role in ("from", "to")]}
    return service, request


def disclosure(stock, *, index=0, issuer_role="to", relation="DIRECT", subject_role="SURVIVING_ISSUER",
               planned=False, body_relation=None, name_anchor="2026-04-15"):
    parent_name = stock["to_action_name_text"]
    child_name = stock["from_action_name_text"] if subject_role == "SURVIVING_ISSUER" else "Sparrow Merger Corporation"
    relation_quote = body_relation or f"Child {'will become' if planned else 'became'} a {relation.lower()}, wholly owned subsidiary of Parent"
    text = (f'On 2026-04-15, {parent_name} ("Parent"), and {child_name} ("Child"), '
            f'{"announced a proposed merger" if planned else "consummated the merger"}. {relation_quote}.')
    identity = stock[f"{issuer_role}_identity"]
    number = f"000032019326000{61+index:03d}" if issuer_role == "to" else f"000012345626000{61+index:03d}"
    url = f"https://www.sec.gov/Archives/edgar/data/{int(identity['issuer_cik'])}/{number}/control.htm"
    base = named_cover(cover(cik=identity["issuer_cik"], url=url), identity["issuer_name"], name_anchor)
    raw = base.payload.replace(b"</html>", f"<p>{text}</p></html>".encode())
    source = SourceSnapshot("corporate_action_filing", url, OBSERVED, raw, PUBLISHED)
    edge = {"source_snapshot_id": source.snapshot_id, "subject_role": subject_role,
            "scope": "PLANNED" if planned else "COMPLETED", "relation": relation,
            "effective_on": None if planned else "2026-04-15", "effective_date_text": None if planned else "2026-04-15",
            "excerpt": text, "relation_excerpt": relation_quote, "child_name_text": child_name,
            "parent_name_text": parent_name, "child_reference_text": "Child", "parent_reference_text": "Parent"}
    return source, edge


class ControlInstrumentTests(unittest.TestCase):
    def test_independent_exact_scopes_are_reviewable_and_have_no_identity_side_effect(self):
        service, request = fixture()
        before = deepcopy((service.existing, service.universe, service.snapshots, request))
        with patch("urllib.request.urlopen", side_effect=AssertionError("HTTP forbidden")):
            report = review_control_instrument_bridge(service, request)
        self.assertEqual("PRIMARY_READY_FOR_EXPLICIT_REVIEW", report["status"])
        self.assertEqual([], report["blockers"])
        self.assertEqual(2, report["independent_completed_surviving_disclosures"])
        self.assertEqual(before, (service.existing, service.universe, service.snapshots, request))
        for key in ("identity_transition_approved", "production_registry_changed", "automatic_alias_allowed",
                    "interval_extension_allowed", "historical_knowledge_reconstructed", "scoring_allowed"):
            self.assertFalse(report[key])
        self.assertEqual(0, report["new_verified_identities"])
        self.assertEqual(0, report["current_quarter_verified_coverage"])
        digest = report.pop("review_sha256")
        self.assertEqual(digest, hashlib.sha256(json.dumps(report,sort_keys=True,separators=(",", ":")).encode()).hexdigest())

    def test_completed_direct_and_indirect_claims_remain_conflicting_after_a_newer_agreement(self):
        service, request = fixture()
        indirect, edge = disclosure(request["stock_merger"], index=3, relation="INDIRECT")
        plan, planned_edge = disclosure(request["stock_merger"], index=4, planned=True)
        service = rebuild(service, extra=[indirect, plan])
        request["control_disclosures"].extend([edge, planned_edge])
        report = review_control_instrument_bridge(service, request)
        self.assertEqual(1, len(report["control_conflicts"]))
        self.assertEqual(["DIRECT", "INDIRECT"], report["control_conflicts"][0]["relations"])
        self.assertIn("EXACT_SURVIVING_ISSUER_DIRECT_INDIRECT_CONFLICT", report["blockers"])
        self.assertIsNone(report["control_edges"][3]["effective_on"])

    def test_merger_sub_direct_control_does_not_become_surviving_issuer_control(self):
        service, request = fixture()
        survivor, indirect = disclosure(request["stock_merger"], index=3, relation="INDIRECT")
        merger_sub, direct = disclosure(request["stock_merger"], index=4, subject_role="MERGER_SUB")
        service = rebuild(service, extra=[survivor, merger_sub])
        request["control_disclosures"] = [indirect, direct]
        report = review_control_instrument_bridge(service, request)
        self.assertEqual([], report["control_conflicts"])
        self.assertEqual(1, report["independent_completed_surviving_disclosures"])
        self.assertIsNone(report["control_edges"][1]["child_cik_proposal"])
        self.assertEqual("UNKNOWN", report["control_edges"][1]["merger_sub_cik_status"])
        self.assertFalse(report["merger_sub_is_surviving_issuer"])

    def test_parent_and_child_order_is_directed(self):
        service, request = fixture()
        reversed_source, edge = disclosure(request["stock_merger"], index=3,
                                           body_relation="Parent became a direct, wholly owned subsidiary of Child")
        service = rebuild(service, extra=[reversed_source])
        request["control_disclosures"] = [edge]
        report = review_control_instrument_bridge(service, request)
        self.assertFalse(report["control_edges"][0]["control_text_matched"])
        self.assertIn("DIRECTED_CONTROL_RELATION_TEXT_UNPROVEN", report["control_edges"][0]["blockers"])

    def test_indirect_word_is_not_a_direct_match(self):
        service, request = fixture()
        source, edge = disclosure(request["stock_merger"], index=3,
                                  body_relation="Child became a wholly owned indirect subsidiary of Parent")
        service = rebuild(service, extra=[source]); request["control_disclosures"] = [edge]
        report = review_control_instrument_bridge(service, request)
        self.assertFalse(report["control_edges"][0]["control_text_matched"])

    def test_planned_language_cannot_supply_a_completed_control_day(self):
        service, request = fixture()
        source, edge = disclosure(request["stock_merger"], index=3, planned=True)
        edge.update(scope="COMPLETED", effective_on="2026-04-15", effective_date_text="2026-04-15")
        service = rebuild(service, extra=[source]); request["control_disclosures"] = [edge]
        report = review_control_instrument_bridge(service, request)
        self.assertIn("COMPLETED_CONTROL_EVENT_UNPROVEN", report["control_edges"][0]["blockers"])
        self.assertIsNone(report["control_edges"][0]["effective_on"])

    def test_unknown_control_day_remains_unknown(self):
        service, request = fixture()
        request["control_disclosures"][0].update(effective_on=None, effective_date_text=None)
        report = review_control_instrument_bridge(service, request)
        self.assertIn("CONTROL_EFFECTIVE_DAY_UNKNOWN", report["control_edges"][0]["blockers"])
        self.assertIsNone(report["control_edges"][0]["effective_on"])

    def test_future_control_day_cannot_be_backdated_from_a_capture(self):
        service, request = fixture()
        request["control_disclosures"][0].update(effective_on="2027-04-15", effective_date_text="2027-04-15")
        report = review_control_instrument_bridge(service, request)
        self.assertIn("CONTROL_EFFECTIVE_DAY_CLOCK_CONFLICT", report["control_edges"][0]["blockers"])
        self.assertIsNone(report["control_edges"][0]["effective_on"])

    def test_different_merger_and_control_days_do_not_align(self):
        service, request = fixture()
        request["control_disclosures"][0].update(effective_on="2026-04-16", effective_date_text="2026-04-16")
        report = review_control_instrument_bridge(service, request)
        self.assertIn("CONTROL_AND_MERGER_DAY_CONFLICT", report["control_edges"][0]["blockers"])

    def test_conflicting_context_names_do_not_fall_back_to_archive_cik(self):
        service, request = fixture()
        edge = request["control_disclosures"][0]; source = service.snapshots[edge["source_snapshot_id"]]
        raw = source.payload.replace(b"</html>", b'<ix:nonNumeric name="dei:EntityRegistrantName" contextRef="Security">Different Company</ix:nonNumeric></html>')
        altered = SourceSnapshot(source.kind, source.url, source.observed_at, raw, source.published_at)
        service = rebuild(service,{source.snapshot_id:altered});edge["source_snapshot_id"] = altered.snapshot_id
        report = review_control_instrument_bridge(service, request)
        self.assertEqual([], report["control_edges"][0]["declarant_issuer_names"])
        self.assertIsNone(report["control_edges"][0]["effective_on"])

    def test_identity_context_day_is_not_automatically_the_action_day(self):
        service, request = fixture()
        source, edge = disclosure(request["stock_merger"], index=3, name_anchor="2026-04-14")
        service = rebuild(service,extra=[source]);request["control_disclosures"]=[edge]
        report = review_control_instrument_bridge(service,request)
        self.assertIn("CONTROL_DECLARANT_IDENTITY_DATE_CONFLICT",report["control_edges"][0]["blockers"])

    def test_multiple_files_in_one_accession_are_one_independent_disclosure(self):
        service, request = fixture()
        source = service.snapshots[request["control_disclosures"][0]["source_snapshot_id"]]
        other = SourceSnapshot(source.kind,source.url.replace("control.htm","exhibit.htm"),source.observed_at,source.payload,source.published_at)
        service = rebuild(service,extra=[other])
        edge = deepcopy(request["control_disclosures"][0]);edge["source_snapshot_id"] = other.snapshot_id
        request["control_disclosures"]=[request["control_disclosures"][0],edge]
        report = review_control_instrument_bridge(service,request)
        self.assertEqual(1,report["independent_completed_surviving_disclosures"])
        self.assertIn("TWO_INDEPENDENT_COMPLETED_SURVIVING_CONTROL_DISCLOSURES_MISSING",report["blockers"])

    def test_party_name_alone_or_unrelated_archive_does_not_supply_identity(self):
        service, request = fixture();edge=request["control_disclosures"][0]
        edge["child_name_text"]="Similar Former Apple Corporation"
        with self.assertRaises(ValueError):review_control_instrument_bridge(service,request)
        service, request = fixture();edge=request["control_disclosures"][0];source=service.snapshots[edge["source_snapshot_id"]]
        altered=SourceSnapshot(source.kind,source.url.replace("/123456/","/999999/"),source.observed_at,source.payload,source.published_at)
        service=rebuild(service,extra=[altered]);edge["source_snapshot_id"]=altered.snapshot_id
        with self.assertRaises(ValueError):review_control_instrument_bridge(service,request)

    def test_merger_sub_cannot_be_relabelled_as_issuer(self):
        service, request = fixture();request["control_disclosures"][0]["subject_role"]="MERGER_SUB"
        with self.assertRaises(ValueError):review_control_instrument_bridge(service,request)

    def test_exact_control_passage_must_exist_in_original_visible_text(self):
        service,request=fixture();request["control_disclosures"][0]["relation_excerpt"]+=" fabricated"
        with self.assertRaises(ValueError):review_control_instrument_bridge(service,request)

    def test_tuple_class_par_and_anchor_conflicts_are_preserved(self):
        for old,new,expected in [(b"$0.01",b"$0.001","EXACT_CLASS_OR_PAR_VALUE_CONFLICT"),
                                  (b"2026-06-30",b"2026-06-29","TICKER_CONTEXT_DATE_CONFLICT")]:
            with self.subTest(expected=expected):
                service,request=fixture();item=request["instrument_disclosures"][1];source=service.snapshots[item["ticker_snapshot_id"]]
                altered=SourceSnapshot(source.kind,source.url.replace("aapl-20260630.htm","other.htm"),source.observed_at,source.payload.replace(old,new),source.published_at)
                service=rebuild(service,extra=[altered]);item["ticker_snapshot_id"]=altered.snapshot_id
                report=review_control_instrument_bridge(service,request)
                self.assertIn(expected,report["dated_instrument_evidence"][1]["blockers"])

    def test_nested_duplicate_name_facts_are_partial_without_changing_the_binder(self):
        service,request=fixture();item=request["instrument_disclosures"][1];source=service.snapshots[item["ticker_snapshot_id"]]
        raw=source.payload.replace(b'>Apple Inc</ix:nonNumeric>',b'><ix:nonNumeric name="dei:EntityRegistrantName" contextRef="Security">Apple Inc</ix:nonNumeric></ix:nonNumeric>')
        altered=SourceSnapshot(source.kind,source.url.replace("aapl-20260630.htm","nested.htm"),source.observed_at,raw,source.published_at)
        service=rebuild(service,extra=[altered]);item["ticker_snapshot_id"]=altered.snapshot_id
        report=review_control_instrument_bridge(service,request)
        self.assertIn("COVER_LEGAL_NAME_UNSUPPORTED",report["dated_instrument_evidence"][1]["blockers"])

    def test_explicit_instrument_no_par_and_decimal_par_terms_are_not_interchangeable(self):
        service,request=fixture();item=request["instrument_disclosures"][0];source=service.snapshots[item["instrument_snapshot_id"]]
        raw=source.payload.replace(b"Common Stock, without par value",b"Common Stock, $0.01 par value")
        altered=SourceSnapshot(source.kind,source.url.replace("old.xml","conflict.xml"),source.observed_at,raw,source.published_at)
        service=rebuild(service,extra=[altered]);item["instrument_snapshot_id"]=altered.snapshot_id
        report=review_control_instrument_bridge(service,request)
        self.assertIn("EXACT_INSTRUMENT_CLASS_OR_PAR_VALUE_CONFLICT",report["dated_instrument_evidence"][0]["blockers"])

    def test_same_accession_cannot_supply_two_independent_tuple_documents(self):
        service,request=fixture();item=request["instrument_disclosures"][0]
        source,cover_source=(service.snapshots[item[key]] for key in ("instrument_snapshot_id","ticker_snapshot_id"))
        altered=SourceSnapshot(source.kind,cover_source.url.rsplit("/",1)[0]+"/instrument.xml",source.observed_at,source.payload,source.published_at)
        service=rebuild(service,extra=[altered]);item["instrument_snapshot_id"]=altered.snapshot_id
        report=review_control_instrument_bridge(service,request)
        self.assertIn("INDEPENDENT_DATED_TUPLE_DISCLOSURES_MISSING",report["dated_instrument_evidence"][0]["blockers"])

    def test_bounded_schema_missing_roles_and_future_source_knowledge_fail_closed(self):
        service,request=fixture();request["control_disclosures"]*=13
        with self.assertRaises(ValueError):review_control_instrument_bridge(service,request)
        service,request=fixture();request["extra"]=True
        with self.assertRaises(ValueError):review_control_instrument_bridge(service,request)
        service,request=fixture();service.as_of=OBSERVED-timedelta(seconds=1)
        with self.assertRaises(ValueError):review_control_instrument_bridge(service,request)

    def test_original_single_ex21_envelope_and_hidden_text_guards(self):
        service,request=fixture();source=service.snapshots[request["control_disclosures"][0]["source_snapshot_id"]]
        filename=source.url.rsplit("/",1)[1]
        envelope=(f'<DOCUMENT>\n<TYPE>EX-2.1\n<SEQUENCE>2\n<FILENAME>{filename}\n<DESCRIPTION>EX-2.1\n<TEXT>\n'.encode()+source.payload+b'\n</TEXT>\n</DOCUMENT>\n')
        altered=SourceSnapshot(source.kind,source.url,source.observed_at,envelope,source.published_at)
        self.assertEqual(control_text(source),control_text(altered))
        mismatch=SourceSnapshot(source.kind,source.url,source.observed_at,envelope.replace(filename.encode(),b'other.htm'),source.published_at)
        with self.assertRaises(ValueError):control_text(mismatch)
        hidden=SourceSnapshot(source.kind,source.url,source.observed_at,source.payload.replace(b'<p>',b'<p style="display:none">'),source.published_at)
        self.assertNotIn(request["control_disclosures"][0]["relation_excerpt"],control_text(hidden))

    def test_actual_original_sources_preserve_conflict_dates_unknowns_and_q2_scope(self):
        from evidence.batch7_identity_20261015.replay_review import HERE,build_inputs
        with patch("urllib.request.urlopen",side_effect=AssertionError("Fresh HTTP forbidden")):
            service,request,report,diagnostics=build_inputs()
        self.assertEqual(json.loads((HERE/"review.json").read_bytes()),report)
        self.assertEqual(json.loads((HERE/"request.json").read_bytes()),request)
        self.assertEqual("PRIMARY_CONTROL_INSTRUMENT_PARTIAL",report["status"])
        self.assertEqual(4,len(report["control_edges"]))
        self.assertEqual(1,len(report["control_conflicts"]))
        self.assertEqual(["DIRECT","INDIRECT"],report["control_conflicts"][0]["relations"])
        self.assertEqual(5,len(report["baseline_blockers_preserved"]))
        self.assertEqual("2026-08-26",report["control_edges"][0]["effective_on"])
        for edge in report["control_edges"][1:]:self.assertIsNone(edge["effective_on"])
        for item in report["dated_instrument_evidence"]:
            self.assertTrue(item["aligned_source_dates"])
            self.assertEqual("2026-06-30",item["anchor_on"])
            self.assertFalse(item["anchor_in_selected_quarter"])
            self.assertFalse(item["verified_identity"])
        self.assertIn("COVER_LEGAL_NAME_UNSUPPORTED",report["dated_instrument_evidence"][1]["blockers"])
        self.assertEqual(6,diagnostics["new_primary_http_requests"])
        self.assertFalse(diagnostics["network_requested_during_replay"])
        self.assertEqual(687,len(service.universe))
        self.assertEqual(150,len(service.existing))
        self.assertEqual(0,report["current_quarter_verified_coverage"])

    def test_withheld_capture_is_rejected_before_any_body_read(self):
        from evidence.batch7_identity_20261015.replay_review import HERE,captures
        with TemporaryDirectory() as directory:
            target=Path(directory);(target/"captures").mkdir()
            paths=sorted((HERE/"captures").glob("*.metadata.json"))
            for index,path in enumerate(paths):
                meta=json.loads(path.read_bytes())
                if index==0:meta["status"]="WITHHELD_SENSITIVE_BODY"
                (target/"captures"/path.name).write_text(json.dumps(meta))
            original=Path.read_bytes
            def checked_read(path):
                if path.suffix==".bin":raise AssertionError("Withheld body read attempted")
                return original(path)
            with patch.object(Path,"read_bytes",checked_read):
                with self.assertRaisesRegex(ValueError,"withheld"):
                    captures(target)

    def test_actual_runtime_manifest_roundtrips_only_explicit_pinned_whole_bodies(self):
        from evidence.batch7_identity_20261015.replay_review import HERE,ROOT,build_inputs
        from market_checker_app.services.captured_identity_review_service import _load_captures
        from market_checker_app.services.sec13f_identity_bulk import Sec13fIdentityBulk
        plan=json.loads((HERE/"captured_review_plan.json").read_bytes())
        self.assertEqual("captured_identity_review_plan_v1",plan["schema_version"])
        self.assertEqual("SEC_CONTROL_INSTRUMENT",plan["kind"])
        self.assertEqual(16,len(plan["captures"]))
        self.assertLess(len((HERE/"captured_review_plan.json").read_bytes()),65_536)
        self.assertLess(sum(item["body_ref"]["bytes"] for item in plan["captures"].values()),24_000_000)
        self.assertTrue(all(item["body_ref"]["bytes"]<=4_000_000 for item in plan["captures"].values()))
        captures=_load_captures(plan,ROOT,datetime.fromisoformat(plan["as_of"]))
        sources=[]
        for metadata,body in captures.values():
            fields={key:metadata[key] for key in ("url","observed_at","published_at")}
            fields["payload"]=body
            source=(Form25Snapshot(**fields) if metadata["kind"]=="form25_nse" else
                    SourceSnapshot(kind=metadata["kind"],**fields))
            self.assertEqual(metadata["snapshot_id"],source.snapshot_id)
            self.assertEqual(metadata["sha256"],source.sha256)
            sources.append(source)
        service,_,expected,_=build_inputs()
        reconstructed=Sec13fIdentityBulk(snapshots=sources,universe=service.universe,quarter=plan["request"]["quarter"],
                                         as_of=datetime.fromisoformat(plan["as_of"]),existing=service.existing)
        self.assertEqual(expected,review_control_instrument_bridge(reconstructed,plan["request"]["review_request"]))


if __name__ == "__main__":
    unittest.main()
