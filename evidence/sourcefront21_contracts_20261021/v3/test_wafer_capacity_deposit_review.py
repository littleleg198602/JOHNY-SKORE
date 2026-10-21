"""Offline financial-asset controls plus one exact retained SWKS replay.

The compact documents are explicitly synthetic assemblies of retained literal
excerpts. They are parser mechanisms, not HTTP captures or LIVE acceptance.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from market_checker_app.services import wafer_capacity_deposit_review as service


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "evidence/sourcefront21_contracts_20261021/SWKS_zalohy_na_vyrobni_kapacitu.json"
NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
BODY_SHA = "2819e8be711351dedca5abb398811786e7edc0d719d6b0389919a367b4f7eade"
BLOCKS = ("notes_heading", "company_definition", "consolidation_scope", "section_heading",
          "cancellation_qualifiers", "deposit_paragraph")
URL = "https://www.sec.gov/Archives/edgar/data/4127/000000412725000085/swks-20251003.htm"
INDEX_URL = URL.rsplit("/", 1)[0] + "/0000004127-25-000085-index.htm"


def pin(body, literal):
    start = body.index(literal)
    return {"byte_start": start, "byte_end": start + len(literal), "sha256": hashlib.sha256(literal).hexdigest()}


def capture(body, url):
    return {"requested_url": url, "final_url": url, "status": "CAPTURED",
        "origin": "LIVE_PRIMARY_RESEARCH_HTTP", "public_raw_available": True, "http_status": 200,
        "complete_response": True, "automatic_redirects": False,
        "raw_response_bytes": len(body), "raw_response_sha256": hashlib.sha256(body).hexdigest(),
        "started_at": "2026-10-16T12:17:00+00:00", "observed_at": "2026-10-16T12:18:00+00:00"}


def retained_inputs():
    plan = json.loads(PLAN.read_bytes())
    captures = {}
    for key, entry in plan["captures"].items():
        reference = entry["body_ref"]; body = (ROOT / reference["path"]).read_bytes()
        if len(body) != reference["bytes"] or hashlib.sha256(body).hexdigest() != reference["sha256"]:
            raise AssertionError("Whole retained source pin changed")
        captures[key] = (entry["metadata"], body)
    return plan["request"], captures


def fixture():
    # This is a newly assembled test document, never an original publisher body.
    request, original = retained_inputs(); body = original["issuer"][1]
    selected = request["selection"]
    blocks = {key: body[selected[key]["byte_start"]:selected[key]["byte_end"]] for key in BLOCKS}
    contexts = []
    for identifier in ("c-1", "c-100", "c-193", "c-194"):
        start = body.index(f'<xbrli:context id="{identifier}">'.encode())
        end = body.index(b"</xbrli:context>", start) + len(b"</xbrli:context>")
        contexts.append(body[start:end])
    dei = {"EntityRegistrantName": "Skyworks Solutions, Inc.", "EntityCentralIndexKey": "0000004127",
           "DocumentPeriodEndDate": "October 3, 2025", "DocumentFiscalYearFocus": "2025", "DocumentType": "10-K"}
    facts = "".join(f'<ix:nonNumeric name="dei:{name}" contextRef="c-1" id="synthetic-{name}">{value}</ix:nonNumeric>'
                    for name, value in dei.items()).encode()
    prefix = (b'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
        b'xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" '
        b'xmlns:dei="http://xbrl.sec.gov/dei/2025" xmlns:us-gaap="http://fasb.org/us-gaap/2025" '
        b'xmlns:swks="http://www.skyworksinc.com/20251003" xmlns:iso4217="http://www.xbrl.org/2003/iso4217" '
        b'xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"><body>')
    synthetic = (prefix + b"".join(contexts) + b'<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
        + facts + blocks["notes_heading"] + blocks["company_definition"] + blocks["consolidation_scope"]
        + b'<ix:nonNumeric name="us-gaap:CommitmentsAndContingenciesDisclosureTextBlock" contextRef="c-1" id="f-893" escape="true">'
        + blocks["section_heading"] + blocks["cancellation_qualifiers"] + blocks["deposit_paragraph"]
        + b"</ix:nonNumeric></body></html>")
    for key in BLOCKS:
        selected[key] = pin(synthetic, blocks[key])
    index = ('<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN" "http://www.w3.org/TR/html4/loose.dtd">'
        '<html><body><span class="companyName">Skyworks <a href="/cgi-bin/browse-edgar?CIK=0000004127">CIK</a></span>'
        '<div class="infoHead">Filing Date</div><div class="info">2025-11-07</div>'
        '<div class="infoHead">Period of Report</div><div class="info">2025-10-03</div>'
        '<div class="infoHead">Accepted</div><div class="info">2025-11-07 16:08:12</div>'
        f'<table><tr><td>1</td><td>10-K</td><td><a href="{URL}">swks-20251003.htm</a></td><td>10-K</td><td>{len(synthetic)}</td></tr></table></body></html>').encode()
    return request, {"issuer": (capture(synthetic, URL), synthetic), "index": (capture(index, INDEX_URL), index)}


def changed(inputs, before, after, *, source="issuer"):
    request, captures = deepcopy(inputs)
    metadata, body = captures[source]
    if before not in body:
        raise AssertionError("The adversarial replacement did not match the fixture")
    modified = body.replace(before, after)
    if source == "issuer":
        for key in BLOCKS:
            old = request["selection"][key]
            request["selection"][key] = pin(modified, body[old["byte_start"]:old["byte_end"]].replace(before, after))
    captures[source] = (dict(metadata, raw_response_bytes=len(modified),
                             raw_response_sha256=hashlib.sha256(modified).hexdigest()), modified)
    return request, captures


class WaferCapacityDepositReviewTests(unittest.TestCase):
    def setUp(self):
        clock = patch.object(service, "_physical_clock", return_value=NOW)
        clock.start(); self.addCleanup(clock.stop)
        for target in ("urllib.request.urlopen", "sqlite3.connect"):
            guard = patch(target, side_effect=AssertionError("Offline source-only review must not perform I/O"))
            guard.start(); self.addCleanup(guard.stop)

    def review(self, inputs=None, **kwargs):
        return service.review_wafer_capacity_deposits(*(inputs if inputs is not None else fixture()), **kwargs)

    def reject(self, inputs, **kwargs):
        report = self.review(inputs, **kwargs)
        self.assertEqual("REJECTED", report["status"], report)
        self.assertIsNone(report["claim"])
        return report

    def test_six_balances_are_not_twelve_nested_annotations(self):
        report = self.review(); self.assertEqual("CANDIDATE_REVIEWED", report["status"], report)
        self.assertEqual("WAFER_CAPACITY_DEPOSITS", report["review_kind"])
        claim = report["claim"]; audit = claim["annotation_audit"]
        self.assertEqual((6, 12, 6), (audit["unique_balance_components"], audit["asset_numeric_annotation_count"],
                                     audit["equivalent_nested_annotation_count"]))
        self.assertEqual(["f-894"], audit["excluded_impairment_annotation_ids"])
        self.assertIs(False, audit["nested_tags_were_added"])
        self.assertEqual(["2025-10-03", "2024-09-27"], [row["as_of_day"] for row in claim["balances"]])
        self.assertEqual([["26.4", "7.7", "18.7"], ["167.8", "144.7", "23.1"]],
            [[row["components"][role]["value_literal"] for role in ("total", "current", "noncurrent")] for row in claim["balances"]])

    def test_money_precision_context_and_arithmetic_preserve_financial_asset_scope(self):
        claim = self.review()["claim"]
        for row in claim["balances"]:
            self.assertIs(True, row["arithmetic_exact"])
            self.assertIs(True, row["total_and_components_must_not_be_summed"])
            for part in row["components"].values():
                self.assertEqual(("USD", "million", 1), (part["currency"], part["unit_literal"], part["unique_displayed_balance_count"]))
                self.assertIs(False, part["equivalent_annotations_are_additive"])
                self.assertEqual("0000004127", part["context"]["entity_identifier"])
                self.assertTrue(all((fact["scale"], fact["decimals"]) == (6, -5) for fact in part["equivalent_annotations"]))
        self.assertEqual("26400000.0", claim["balances"][0]["components"]["total"]["source_unit_only_usd_conversion"])
        self.assertIn("NOT_PRIOR_AS_FILED", claim["balances"][1]["date_basis"])

    def test_no_named_foundry_volume_purchase_obligation_apple_or_continuity_inference(self):
        report = self.review()
        for key in ("named_foundry_attribution_allowed", "physical_capacity_verified", "physical_volume_verified",
                    "purchases_verified", "outstanding_obligation_amount_verified", "actual_spend_verified", "revenue_amount_verified",
                    "apple_allocation_allowed", "direct_purchase_share_verified", "supplier_identity_continuity_verified",
                    "issuer_identity_adopted", "historical_availability_reconstructed", "historical_as_filed_comparison_verified",
                    "activation_allowed", "model_modified", "scoring_applied", "trading_allowed"):
            self.assertIs(False, report[key], key)
        self.assertIsNone(report["claim"]["named_counterparty"])
        self.assertEqual(0, report["registry_adoptions"])

    def test_actual_whole_retained_filing_preserves_all_six_facts_and_109_byte_conflict(self):
        inputs = retained_inputs(); self.assertEqual(BODY_SHA, hashlib.sha256(inputs[1]["issuer"][1]).hexdigest())
        report = self.review(inputs); self.assertEqual("CANDIDATE_REVIEWED", report["status"], report)
        claim = report["claim"]; filing = claim["filing"]
        self.assertEqual((1708601, 1708710, 109), (filing["index_declared_primary_bytes"], filing["captured_primary_bytes"], claim["publisher_size_difference_bytes"]))
        self.assertIs(True, filing["publisher_size_conflict"])
        self.assertIs(False, filing["exact_historical_as_filed_version_verified"])
        self.assertIsNone(claim["primary_source"]["publisher_publication_at"])
        self.assertEqual("2026-10-16T12:18:16.379894+00:00", claim["primary_source"]["capture_completed_utc"])
        self.assertEqual(NOW.isoformat(), report["knowledge_at"])

    def test_inputs_outputs_and_replay_are_detached(self):
        inputs = fixture(); before = deepcopy(inputs); first = self.review(inputs)
        identifier = first["claim"]["candidate_id"]
        first["claim"]["balances"][0]["components"]["total"]["equivalent_annotations"][0]["value_literal"] = "0"
        self.assertEqual(before, inputs)
        self.assertEqual(identifier, self.review(inputs)["claim"]["candidate_id"])
        self.assertEqual("26.4", self.review(inputs)["claim"]["balances"][0]["components"]["total"]["value_literal"])

    def test_wrong_dei_issuer_or_context_cannot_be_selected(self):
        for changes in ({"issuer_cik": "0000320193"}, {"issuer_name": "Apple Inc."}, {"issuer_context_id": "c-193"}):
            inputs = fixture(); inputs[0]["selection"].update(changes)
            with self.subTest(changes=changes): self.reject(inputs)

    def test_asset_context_other_issuer_does_not_inherit_cover_identity(self):
        request, captures = fixture(); body = captures["issuer"][1]
        start = body.index(b'<xbrli:context id="c-193">'); end = body.index(b"</xbrli:context>", start)
        self.reject(changed((request, captures), body[start:end], body[start:end].replace(b"0000004127", b"0000320193")))

    def test_wrong_archive_routing_or_index_accession_is_rejected(self):
        for key, original, replacement in (("issuer", "/4127/", "/320193/"), ("index", "000000412725000085", "000000412725000086")):
            inputs = fixture(); meta, body = inputs[1][key]
            meta.update(requested_url=meta["requested_url"].replace(original, replacement), final_url=meta["final_url"].replace(original, replacement))
            with self.subTest(source=key): self.reject(inputs)

    def test_cover_or_comparative_date_cannot_be_moved(self):
        for index, day in ((0, "2025-10-02"), (1, "2024-09-28"), (1, "2023-09-29")):
            inputs = fixture(); inputs[0]["selection"]["balances"][index]["as_of_day"] = day
            with self.subTest(index=index, day=day): self.reject(inputs)

    def test_narrative_and_instant_context_dates_must_agree(self):
        for before, after in ((b"<xbrli:instant>2025-10-03", b"<xbrli:instant>2025-10-02"),
                              (b"As of October&#160;3, 2025", b"As of October&#160;2, 2025")):
            with self.subTest(after=after): self.reject(changed(fixture(), before, after))

    def test_wafer_category_is_not_other_asset_or_named_supplier(self):
        for replacement in (b"swks:AppleMember", b"swks:TSMCMember", b"swks:OtherPurchasesMember"):
            with self.subTest(member=replacement): self.reject(changed(fixture(), b"swks:WaferPurchasesMember", replacement))

    def test_extra_dimension_cannot_hide_a_different_scope(self):
        self.reject(changed(fixture(), b"</xbrldi:explicitMember></xbrli:segment>",
            b'</xbrldi:explicitMember><xbrldi:explicitMember dimension="us-gaap:OtherAxis">swks:OtherMember</xbrldi:explicitMember></xbrli:segment>'))

    def test_usd_currency_cannot_be_replaced_by_eur_or_share_ratio(self):
        for replacement in (b"iso4217:EUR", b"xbrli:pure"):
            with self.subTest(replacement=replacement): self.reject(changed(fixture(), b"iso4217:USD</xbrli:measure>", replacement + b"</xbrli:measure>"))
        self.reject(changed(fixture(), b"<xbrli:measure>iso4217:USD</xbrli:measure>",
                            b"<xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator></xbrli:divide>"))

    def test_scale_sign_precision_transform_and_nil_cannot_change_meaning(self):
        for before, after in ((b'scale="6"', b'scale="9"'), (b'decimals="-5"', b'decimals="INF"'),
                              (b'id="f-896"', b'id="f-896" sign="-"'),
                              (b'format="ixt:num-dot-decimal"', b'format="ixt:num-comma-decimal"'),
                              (b'id="f-896"', b'id="f-896" continuedAt="missing"'),
                              (b'id="f-896"', b'id="f-896" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:nil="true"')):
            with self.subTest(after=after): self.reject(changed(fixture(), before, after))

    def test_nonfinite_negative_or_numeric_nonliteral_is_not_a_balance(self):
        for value in ("NaN", "Infinity", "INF", "-26.4", "1e3", "1,000", True, 26.4):
            inputs = fixture(); inputs[0]["selection"]["balances"][0]["total"]["value_literal"] = value
            with self.subTest(value=value): self.reject(inputs)

    def test_recomputed_manifest_cannot_hide_wrong_numeric_body_literal(self):
        self.reject(changed(fixture(), b">26.4</ix:nonFraction>", b">26.5</ix:nonFraction>"))

    def test_literal_total_must_equal_current_plus_noncurrent(self):
        inputs = changed(fixture(), b">26.4</ix:nonFraction>", b">27.4</ix:nonFraction>")
        inputs[0]["selection"]["balances"][0]["total"]["value_literal"] = "27.4"
        self.reject(inputs)

    def test_current_and_noncurrent_annotations_cannot_be_swapped(self):
        inputs = fixture(); period = inputs[0]["selection"]["balances"][0]
        period["current"], period["noncurrent"] = period["noncurrent"], period["current"]
        self.reject(inputs)

    def test_reused_or_omitted_nested_annotation_cannot_create_an_extra_balance(self):
        inputs = fixture(); inputs[0]["selection"]["balances"][0]["current"]["fact_ids"] = ["f-895", "f-896"]
        self.reject(inputs)
        inputs = fixture(); inputs[0]["selection"]["balances"][0]["total"]["fact_ids"] = ["f-896"]
        self.reject(inputs)

    def test_parallel_repeated_values_are_not_equivalent_nested_tags(self):
        inputs = fixture(); body = inputs[1]["issuer"][1]
        match = re.search(rb'(<ix:nonFraction[^>]*id="f-895">)(<ix:nonFraction[^>]*id="f-896">26.4</ix:nonFraction>)</ix:nonFraction>', body)
        self.assertIsNotNone(match)
        inputs = changed(inputs, match[0], match[1] + b"26.4</ix:nonFraction>" + match[2])
        ET.fromstring(inputs[1]["issuer"][1])  # Valid XML with two displayed values, not a parse-error control.
        self.reject(inputs)

    def test_duplicate_original_identifier_is_ambiguous(self):
        self.reject(changed(fixture(), b"</body></html>", b'<div id="f-896">unrelated</div></body></html>'))

    def test_wrong_textblock_context_or_disclosure_concept_cannot_be_stitched(self):
        for before, after in ((b'contextRef="c-1" id="f-893"', b'contextRef="c-193" id="f-893"'),
                              (b"us-gaap:CommitmentsAndContingenciesDisclosureTextBlock", b"us-gaap:RevenueRecognitionPolicyTextBlock")):
            with self.subTest(after=after): self.reject(changed(fixture(), before, after))

    def test_hidden_or_table_passage_cannot_supply_the_visible_asset_claim(self):
        for wrapper in (b'<div hidden="hidden">', b'<div style="display:none">', b"<table><tr><td>"):
            inputs = fixture(); body = inputs[1]["issuer"][1]; selected = inputs[0]["selection"]["deposit_paragraph"]
            paragraph = body[selected["byte_start"]:selected["byte_end"]]
            suffix = b"</td></tr></table>" if wrapper.startswith(b"<table") else b"</div>"
            modified = changed(inputs, paragraph, wrapper + paragraph + suffix)
            modified[0]["selection"]["deposit_paragraph"] = pin(modified[1]["issuer"][1], paragraph)
            with self.subTest(wrapper=wrapper): self.reject(modified)

    def test_cancelability_and_full_paragraph_qualifiers_cannot_be_trimmed(self):
        self.reject(changed(fixture(), b"Some of these purchase commitments are cancelable", b"All of these purchase commitments are non-cancelable"))
        inputs = fixture(); selected = inputs[0]["selection"]["deposit_paragraph"]
        selected["byte_start"] += 1
        self.reject(inputs)

    def test_false_purchase_physical_or_named_allocation_interpretation_is_rejected(self):
        for role in ("ACTUAL_PURCHASES", "OUTSTANDING_PURCHASE_OBLIGATIONS", "PHYSICAL_CAPACITY", "APPLE_REVENUE"):
            inputs = fixture(); inputs[0]["selection"]["claim_role"] = role
            with self.subTest(role=role): self.reject(inputs)
        for key, value in (("named_foundry", "TSMC"), ("apple_allocation", "67%"), ("physical_capacity", "26.4")):
            inputs = fixture(); inputs[0]["selection"][key] = value
            with self.subTest(key=key): self.reject(inputs)

    def test_changed_body_or_whole_source_splice_fails_original_hash(self):
        inputs = fixture(); meta, body = inputs[1]["issuer"]; inputs[1]["issuer"] = (meta, body + b" ")
        self.reject(inputs)
        inputs = fixture(); inputs[1]["issuer"] = (inputs[1]["issuer"][0], inputs[1]["index"][1])
        self.reject(inputs)

    def test_missing_complete_manifest_or_withheld_body_never_enters_parser(self):
        for key, value in (("complete_response", False), ("raw_publication_permitted", False), ("status", "WITHHELD")):
            inputs = fixture(); inputs[1]["issuer"][0][key] = value
            with self.subTest(key=key), patch.object(service, "_issuer", side_effect=AssertionError("Must stop before parsing")):
                self.reject(inputs)

    def test_missing_or_extra_capture_scope_is_not_silently_ignored(self):
        inputs = fixture(); del inputs[1]["index"]; self.reject(inputs)
        inputs = fixture(); inputs[1]["counterparty"] = inputs[1]["issuer"]; self.reject(inputs)

    def test_future_reversed_or_pre_capture_knowledge_clocks_are_rejected(self):
        for changes in ({"observed_at": (NOW + timedelta(seconds=1)).isoformat()},
                        {"started_at": "2026-10-16T12:19:00+00:00"},
                        {"known_at": "2026-10-15T12:18:00+00:00"},
                        {"known_at": (NOW + timedelta(seconds=1)).isoformat()}):
            inputs = fixture(); inputs[1]["issuer"][0].update(changes)
            with self.subTest(changes=changes): self.reject(inputs)

    def test_old_explicit_cutoff_cannot_relabel_new_ingestion_as_history(self):
        with patch.object(service, "_issuer", side_effect=AssertionError("Old cutoff must stop before parsing")):
            result = self.reject(fixture(), as_of=NOW - timedelta(microseconds=1))
        self.assertEqual("AFTER_PHYSICAL_KNOWLEDGE_CUTOFF", result["reason"])

    def test_completion_clock_and_cutoff_are_rechecked_after_validation(self):
        for completion, cutoff in ((NOW + timedelta(seconds=2), NOW + timedelta(seconds=1)), (NOW - timedelta(seconds=1), None)):
            with self.subTest(completion=completion), patch.object(service, "_physical_clock", side_effect=[NOW, completion]):
                self.reject(fixture(), as_of=cutoff)
        with patch.object(service, "_physical_clock", side_effect=[NOW, NOW + timedelta(seconds=2)]):
            report = self.review()
        self.assertEqual((NOW + timedelta(seconds=2)).isoformat(), report["knowledge_at"])
        self.assertEqual(report["knowledge_at"], report["as_of"])

    def test_four_megabyte_whole_body_bound_precedes_xml_parser(self):
        inputs = fixture(); inputs[1]["issuer"] = (inputs[1]["issuer"][0], b"x" * 4_000_001)
        with patch.object(service, "_issuer", side_effect=AssertionError("Bound must stop before parsing")):
            self.reject(inputs)


if __name__ == "__main__":
    unittest.main()
