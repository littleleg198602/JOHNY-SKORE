"""Explicit controls for bounded input acquisition; injected results are fixtures only."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

from market_checker_app.market_input_runner import main
from market_checker_app.services import captured_identity_review_service as reviews

ROOT = Path(__file__).resolve().parents[1]


class ActualOriginalScopeTests(unittest.TestCase):
    def review(self, name):
        with tempfile.TemporaryDirectory() as directory, \
             patch("socket.socket.connect", side_effect=AssertionError("No review HTTP")), \
             patch("sqlite3.connect", side_effect=AssertionError("No review database")):
            result = reviews.run_captured_identity_review(source_root=ROOT,
                plan_name="evidence/specialist_review_plans/" + name, archive_root=Path(directory).resolve())
        self.assertEqual(0, result["source_http_requests"])
        self.assertEqual(0, result["registry_adoptions"])
        self.assertFalse(result["full_specialist_acceptance"])
        self.assertFalse(result["scoring_applied"])
        return result["review"]

    def test_actual_registered_class_and_symbol_keep_unproved_cusip_bridge(self):
        leaf = self.review("P_trida_akcii_a_CUSIP.json")
        self.assertEqual(2, leaf["registered_source_anchor_count"])
        self.assertEqual(["2026-05-27", "2026-08-26"],
            [row["context_anchor_on"] for row in leaf["registered_security_facts"]])
        self.assertTrue(all(row["symbol"] == "P" and row["same_row_cusip"] is None
            for row in leaf["registered_security_facts"]))
        self.assertEqual(0, leaf["current_q3_verified_instrument_coverage"])
        self.assertFalse(leaf["automatic_alias_allowed"])
        self.assertIn("EXACT_DATED_CLASS_CUSIP_SYMBOL_BRIDGE_UNRESOLVED", leaf["blockers"])

    def test_actual_fda_company_and_applicant_rows_stay_separate(self):
        company = self.review("PFE_puvodni_produkty_DrugsFDA.json")
        applicant = self.review("PFE_Purple_Book_applicant.json")
        self.assertEqual("PFIZER INC", company["application_facts"]["source_party_name"])
        self.assertEqual("COMPANY_AS_DISPLAYED", company["application_facts"]["source_party_role"])
        self.assertIsNone(company["product_rows"][0]["product_number"])
        self.assertEqual("Pfizer Ireland Pharmaceuticals Unlimited Company",
            applicant["application_facts"]["source_party_name"])
        self.assertEqual(["001", "002"], [row["product_number"] for row in applicant["product_rows"]])
        self.assertEqual(["420MG", "150MG"], [row["strength"] for row in applicant["product_rows"]])
        for leaf in (company, applicant):
            self.assertFalse(leaf["legal_owner_verified"])
            self.assertFalse(leaf["corporate_parent_verified"])
            self.assertFalse(leaf["live_acceptance_verified"])

    def test_actual_nhtsa_positive_http400_and_unknown_response_never_become_negative(self):
        positive = self.review("CHEVROLET_7500XD_2026_puvodni_rozsah.json")
        failed = self.review("CHEVROLET_BrightDrop_HTTP400_2026.json")
        blocked = self.review("CHEVROLET_5500XG_prerusene_overeni_2026.json")
        self.assertEqual(("POSITIVE_EXACT_MODEL_YEAR", 200, 2),
            (positive["status"], positive["http_status"], positive["publisher_count"]))
        self.assertEqual("HTTP_400_IS_NOT_COMPLETE_NEGATIVE", failed["reason"])
        self.assertIsNone(blocked.get("http_status"))
        self.assertIsNone(blocked.get("matched_rows"))
        for leaf in (positive, failed, blocked):
            self.assertFalse(leaf["complete_negative"])
            self.assertFalse(leaf["live_complete_negative_verified"])
            self.assertFalse(leaf["issuer_wide_absence_verified"])

    def test_actual_apple_share_remains_swks_net_revenue_with_generic_xbrl_member(self):
        leaf = self.review("SWKS_AAPL_podil_na_trzbach.json")
        self.assertEqual("CANDIDATE_REVIEWED", leaf["status"])
        self.assertEqual("CONSOLIDATED_REPORTING_GROUP", leaf["claim"]["reporting_scope"])
        self.assertEqual(["67", "69", "66"], [row["value_literal"] for row in leaf["claim"]["quantities"]])
        self.assertTrue(all(not row["generic_customer_member_renamed"]
            for row in leaf["claim"]["quantities"]))
        self.assertFalse(leaf["counterparty_financials_attribution_allowed"])
        self.assertFalse(leaf["direct_customer_share_verified"])
        self.assertFalse(leaf["continuous_legal_identity_verified"])

    def test_actual_complete_packet_binds_xml_but_does_not_promote_person_or_instrument(self):
        leaf = self.review("MSFT_Form4_uplny_packet.json")
        for key in ("original_complete_packet_supported", "original_header_scope_supported",
                    "original_primary_xml_exact_binding_supported"):
            self.assertTrue(leaf[key], key)
        self.assertEqual((2, 5), (leaf["transaction_row_count"], leaf["holding_row_count"]))
        self.assertFalse(leaf["natural_person_identity_verified"])
        self.assertFalse(leaf["cusip_class_validity_verified"])
        self.assertFalse(leaf["role_continuity_verified"])
        self.assertFalse(leaf["execution_verified"])

    def test_new_review_kinds_keep_original_four_megabyte_cap_before_body_io(self):
        source = {"metadata": {}, "body_available": True,
            "body_ref": {"path": "never-opened.body", "sha256": "0"*64, "bytes": 4_000_001}}
        for kind in ("SEC_INSTRUMENT_CLASS_SCOPE", "FDA_APPLICATION_PRODUCT", "NHTSA_NEGATIVE_SCOPE",
                     "SEC_SUPPLIER_CONCENTRATION", "SEC_COMPLETE_PACKET"):
            with self.subTest(kind=kind), patch.object(Path, "open", side_effect=AssertionError("No body read")):
                with self.assertRaises(ValueError):
                    reviews._load_captures({"kind": kind, "captures": {"source": source}},
                        ROOT, datetime.now(timezone.utc))


class MarketInputActionTests(unittest.TestCase):
    def test_default_cli_prepares_only_bounded_plan_without_http_database_or_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "not-created"
            output = io.StringIO()
            with patch("socket.socket.connect", side_effect=AssertionError("No HTTP")), \
                 patch("sqlite3.connect", side_effect=AssertionError("No database")), redirect_stdout(output):
                main(["--archive-root", str(root)])
            plan = json.loads(output.getvalue())
            self.assertEqual(13, plan["request_budget"])
            self.assertEqual(13, len(plan["requests"]))
            self.assertEqual(0, plan["retries"])
            self.assertEqual(0, plan["redirects"])
            self.assertFalse(root.exists())

    def test_execute_requires_existing_explicit_plan_before_collector_construction(self):
        with patch("market_checker_app.services.captured_market_input_acquisition.CapturedMarketInputAcquisition") as collector, \
             redirect_stdout(io.StringIO()), patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit):
                main(["--archive-root", "unused", "--execute"])
            collector.assert_not_called()

    def test_cli_execution_passes_exact_selected_plan_and_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            selected = {"explicit_fixture_selection": "not-a-live-plan"}
            plan = root / "plan.json"
            plan.write_text(json.dumps(selected), encoding="utf-8")
            target = "market_checker_app.services.captured_market_input_acquisition.CapturedMarketInputAcquisition"
            with patch(target) as collector, redirect_stdout(io.StringIO()):
                collector.return_value.acquire.return_value = {"status": "WAIT_DATA"}
                main(["--archive-root", str(root), "--plan-file", str(plan), "--execute"])
            collector.assert_called_once_with(archive_root=root, enabled=True)
            collector.return_value.acquire.assert_called_once_with(selected, execute=True)

    def test_ui_preview_never_executes_and_execution_consumes_the_displayed_plan(self):
        from streamlit.testing.v1 import AppTest
        target = "market_checker_app.services.captured_market_input_acquisition.CapturedMarketInputAcquisition"
        selected = {"plan_id": "fixture-display-only", "request_budget": 13}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = ("from pathlib import Path\nimport streamlit as st\n"
                      "from market_checker_app.specialist_evidence_ui import render_market_input_acquisition\n"
                      f"render_market_input_acquisition(st,output_dir=Path({str(root)!r}))\n")
            with patch(target) as collector, \
                 patch("socket.socket.connect", side_effect=AssertionError("No UI HTTP")), \
                 patch("sqlite3.connect", side_effect=AssertionError("No UI database")):
                collector.return_value.plan.return_value = selected
                collector.return_value.acquire.return_value = {"status": "WAIT_DATA", "source_http_requests": 1}
                app = AppTest.from_string(source).run(timeout=20)
                collector.return_value.acquire.assert_not_called()
                self.assertFalse(app.checkbox[0].value)
                next(button for button in app.button if button.label == "Připravit plán cenových vstupů").click()
                app.run(timeout=20)
                self.assertTrue(next(button for button in app.button
                    if button.label == "Stáhnout tuto dávku cenových vstupů").disabled)
                collector.return_value.acquire.assert_not_called()
                app.checkbox[0].set_value(True)
                app.run(timeout=20)
                next(button for button in app.button if button.label == "Stáhnout tuto dávku cenových vstupů").click()
                app.run(timeout=20)
                self.assertEqual([], list(app.exception))
                collector.return_value.acquire.assert_called_once_with(selected, execute=True)
                self.assertNotIn("market_input_acquisition_plan", app.session_state)
                app.run(timeout=20)
                collector.return_value.acquire.assert_called_once()
                self.assertFalse((root / "market_inputs").exists())


if __name__ == "__main__":
    unittest.main()
