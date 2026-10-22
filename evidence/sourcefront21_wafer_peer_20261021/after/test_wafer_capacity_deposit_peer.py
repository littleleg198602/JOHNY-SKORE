"""Independent wafer-asset peer: real retained control and synthetic negatives.

Rehashed in-memory derivatives are adversarial fixtures, never new source
captures, application LIVE, issuer adoption or historical acceptance.
"""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from market_checker_app.services.wafer_capacity_deposit_review import review_wafer_capacity_deposits


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "evidence/sourcefront21_wafer_peer_20261021/before/captured_plan.json"
CLOCK = "market_checker_app.services.wafer_capacity_deposit_review._physical_clock"
NOW = datetime(2026, 10, 21, 8, tzinfo=timezone.utc)
BLOCKS = ("notes_heading", "company_definition", "consolidation_scope", "section_heading",
          "cancellation_qualifiers", "deposit_paragraph")
ACTUAL_CONTROL_REPORT = None
PEER_DIAGNOSTICS = []


def load_originals():
    plan = json.loads(PLAN.read_bytes())
    captures = {}
    for role, entry in plan["captures"].items():
        path = (ROOT / entry["body_ref"]["path"]).resolve()
        if not path.is_relative_to(ROOT):
            raise AssertionError("Peer uses repository originals only")
        body = path.read_bytes()
        if len(body) != entry["body_ref"]["bytes"] or hashlib.sha256(body).hexdigest() != entry["body_ref"]["sha256"]:
            raise AssertionError("Original peer control hash/size changed")
        captures[role] = (deepcopy(entry["metadata"]), body)
    return deepcopy(plan["request"]), captures


def synthetic_replace(request, captures, role, old, new):
    """One explicit mutation, with honest rebinding of every affected block pin."""
    request, captures = deepcopy(request), deepcopy(captures)
    metadata, body = captures[role]
    if body.count(old) != 1:
        raise AssertionError("Synthetic trigger must match exactly one original byte sequence")
    at = body.index(old); end = at + len(old); delta = len(new)-len(old)
    changed = body[:at] + new + body[end:]
    metadata.update(raw_response_bytes=len(changed), raw_response_sha256=hashlib.sha256(changed).hexdigest(),
                    peer_synthetic_derivative=True)
    if role == "issuer":
        for name in BLOCKS:
            entry = request["selection"][name]
            first, last = entry["byte_start"], entry["byte_end"]
            if first >= end:
                first += delta
            elif at < first < end:
                raise AssertionError("Mutation crosses a selected block opening boundary")
            if last >= end:
                last += delta
            elif at < last < end:
                raise AssertionError("Mutation crosses a selected block closing boundary")
            entry.update(byte_start=first, byte_end=last, sha256=hashlib.sha256(changed[first:last]).hexdigest())
    captures[role] = (metadata, changed)
    return request, captures


def change_fact_attribute(request, captures, identifier, attr, old, new):
    body = captures["issuer"][1]
    candidates = [m[0] for m in re.finditer(br"<ix:nonFraction\b[^>]*>", body)
                  if re.search(rb'\bid="' + identifier.encode() + rb'"', m[0])]
    if len(candidates) != 1:
        raise AssertionError("Exact selected original fact must exist once")
    before = candidates[0]; literal = (attr + '="' + old + '"').encode()
    if before.count(literal) != 1:
        raise AssertionError("Exact source attribute must exist once")
    after = before.replace(literal, (attr + '="' + new + '"').encode())
    return synthetic_replace(request, captures, "issuer", before, after)


class WaferCapacityDepositIndependentPeerTests(unittest.TestCase):
    def setUp(self):
        self.request, self.captures = load_originals()

    def invoke(self, request=None, captures=None, *, as_of=None, real_clock=False):
        with ExitStack() as stack:
            stack.enter_context(patch("sqlite3.connect", side_effect=AssertionError("peer permits no SQL")))
            stack.enter_context(patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("peer permits no HTTP")))
            stack.enter_context(patch("socket.create_connection", side_effect=AssertionError("peer permits no network")))
            if not real_clock:
                stack.enter_context(patch(CLOCK, return_value=NOW))
            selected = self.request if request is None else request
            captured = self.captures if captures is None else captures
            report = review_wafer_capacity_deposits(selected, captured, as_of=as_of)
            claim = report.get("claim") or {}
            PEER_DIAGNOSTICS.append({"method": self._testMethodName,
                "request_sha256": hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest(),
                "input_whole_body_sha256": {key: hashlib.sha256(value[1]).hexdigest() for key, value in captured.items()},
                "status": report["status"], "reason": report["reason"], "as_of": report["as_of"],
                "knowledge_at": report["knowledge_at"], "filing": claim.get("filing"),
                "synthetic_input_mutation": any(value[0].get("peer_synthetic_derivative") for value in captured.values())})
            return report

    def assert_rejected(self, report):
        summary = {key: report.get(key) for key in ("status", "reason", "as_of", "knowledge_at")}
        summary["filing"] = (report.get("claim") or {}).get("filing")
        self.assertEqual("REJECTED", report["status"], summary)
        self.assertIsNone(report["claim"])

    def test_actual_whole_original_control_has_six_components_not_twelve_added_balances(self):
        global ACTUAL_CONTROL_REPORT
        report = self.invoke(real_clock=True)
        ACTUAL_CONTROL_REPORT = report
        self.assertEqual("CANDIDATE_REVIEWED", report["status"], report)
        claim = report["claim"]; audit = claim["annotation_audit"]
        self.assertEqual(6, audit["unique_balance_components"])
        self.assertEqual(12, audit["asset_numeric_annotation_count"])
        self.assertEqual(6, audit["equivalent_nested_annotation_count"])
        self.assertFalse(audit["nested_tags_were_added"])
        self.assertEqual(["f-894"], audit["excluded_impairment_annotation_ids"])
        expected = [("2025-10-03", "26.4", "7.7", "18.7"), ("2024-09-27", "167.8", "144.7", "23.1")]
        for balance, (day, total, current, noncurrent) in zip(claim["balances"], expected):
            self.assertEqual(day, balance["as_of_day"])
            self.assertEqual([total, current, noncurrent], [balance["components"][r]["value_literal"] for r in ("total", "current", "noncurrent")])
            self.assertTrue(balance["total_and_components_must_not_be_summed"])
            for part in balance["components"].values():
                self.assertEqual(1, part["unique_displayed_balance_count"])
                self.assertFalse(part["equivalent_annotations_are_additive"])
                self.assertEqual(2, len(part["equivalent_annotations"]))
                for annotation in part["equivalent_annotations"]:
                    self.assertEqual(("USD", 6, -5), (annotation["currency"], annotation["scale"], annotation["decimals"]))
        self.assertEqual(109, claim["publisher_size_difference_bytes"])
        self.assertTrue(claim["filing"]["publisher_size_conflict"])
        self.assertFalse(claim["filing"]["exact_historical_as_filed_version_verified"])
        self.assertEqual("CURRENT_FILING_COMPARATIVE_NOT_PRIOR_AS_FILED", claim["balances"][1]["date_basis"])
        self.assertIsNone(claim["named_counterparty"])
        for key in ("named_foundry_attribution_allowed", "physical_capacity_verified", "physical_volume_verified",
                    "purchases_verified", "outstanding_obligation_amount_verified", "actual_spend_verified",
                    "apple_allocation_allowed", "direct_purchase_share_verified", "supplier_identity_continuity_verified",
                    "issuer_identity_adopted", "current_instrument_or_q3_verified", "out_of_sample_verified",
                    "historical_availability_reconstructed", "activation_allowed", "trading_allowed"):
            self.assertFalse(report[key], key)

    def test_outer_nested_fact_wrong_unit_cannot_inherit_inner_usd(self):
        request, captures = change_fact_attribute(self.request, self.captures, "f-895", "unitRef", "usd", "shares")
        self.assert_rejected(self.invoke(request, captures))

    def test_outer_nested_fact_precision_cannot_inherit_inner_precision(self):
        request, captures = change_fact_attribute(self.request, self.captures, "f-895", "decimals", "-5", "INF")
        self.assert_rejected(self.invoke(request, captures))

    def test_category_context_wrong_entity_cannot_inherit_dei_issuer(self):
        body = self.captures["issuer"][1]
        context = re.search(br'<xbrli:context\b[^>]*\bid="c-193"[\s\S]*?</xbrli:context>', body)
        self.assertIsNotNone(context)
        self.assertEqual(1, context[0].count(b"0000004127"))
        request, captures = synthetic_replace(self.request, self.captures, "issuer", context[0], context[0].replace(b"0000004127", b"0000004128"))
        self.assert_rejected(self.invoke(request, captures))

    def test_current_and_noncurrent_cannot_be_swapped_even_when_sum_is_unchanged(self):
        request = deepcopy(self.request); selected = request["selection"]["balances"][0]
        selected["current"], selected["noncurrent"] = selected["noncurrent"], selected["current"]
        self.assert_rejected(self.invoke(request))

    def test_context_instant_cannot_be_replaced_with_another_day(self):
        body = self.captures["issuer"][1]
        context = re.search(br'<xbrli:context\b[^>]*\bid="c-193"[\s\S]*?</xbrli:context>', body)
        self.assertIsNotNone(context)
        request, captures = synthetic_replace(self.request, self.captures, "issuer", context[0], context[0].replace(b"2025-10-03", b"2025-10-04"))
        request["selection"]["balances"][0]["as_of_day"] = "2025-10-04"
        self.assert_rejected(self.invoke(request, captures))

    def test_wrong_commitments_textblock_context_cannot_inherit_selected_numeric_contexts(self):
        body = self.captures["issuer"][1]
        candidates = [m[0] for m in re.finditer(br'<ix:nonNumeric\b[^>]*>', body)
                      if b"us-gaap:CommitmentsAndContingenciesDisclosureTextBlock" in m[0]]
        self.assertEqual(1, len(candidates))
        opening = candidates[0]
        self.assertIn(b'contextRef="c-1"', opening)
        request, captures = synthetic_replace(self.request, self.captures, "issuer", opening, opening.replace(b'contextRef="c-1"', b'contextRef="c-193"'))
        self.assert_rejected(self.invoke(request, captures))

    def test_index_wrong_basename_is_not_tolerated_as_known_109_byte_body_conflict(self):
        captures = deepcopy(self.captures); metadata, body = captures["index"]
        for key in ("requested_url", "final_url"):
            metadata[key] = metadata[key].replace("0000004127-25-000085-index.htm", "0000004127-25-000086-index.htm")
        self.assert_rejected(self.invoke(captures=captures))

    def test_future_index_acceptance_display_cannot_be_a_qualified_capture_from_2026(self):
        request, captures = synthetic_replace(self.request, self.captures, "index", b"2025-11-07 16:08:12", b"2099-11-07 16:08:12")
        self.assert_rejected(self.invoke(request, captures))

    def test_explicit_future_requested_clock_cannot_publish_a_future_as_of_claim(self):
        self.assert_rejected(self.invoke(as_of=NOW+timedelta(days=1)))

    def test_old_cutoff_and_validation_finishing_after_cutoff_do_not_return_claims(self):
        self.assert_rejected(self.invoke(as_of=NOW-timedelta(seconds=1)))
        with patch(CLOCK, side_effect=(NOW, NOW+timedelta(seconds=2))) as physical:
            report = self.invoke(as_of=NOW, real_clock=True)
        self.assertEqual(2, physical.call_count)
        self.assert_rejected(report)
        self.assertEqual("AFTER_PHYSICAL_KNOWLEDGE_CUTOFF", report["reason"])

    def test_future_input_known_at_cannot_be_accepted_under_current_physical_clock(self):
        captures = deepcopy(self.captures)
        captures["issuer"][0]["known_at"] = (NOW+timedelta(seconds=1)).isoformat()
        self.assert_rejected(self.invoke(captures=captures))


if __name__ == "__main__":
    unittest.main()
