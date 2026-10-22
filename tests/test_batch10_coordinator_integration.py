"""Current original-byte reviews and explicit readiness actions across CLI/UI."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from market_checker_app.services import captured_identity_review_service as reviews
from market_checker_app.specialist_evidence_runner import main

ROOT = Path(__file__).resolve().parents[1]
PLANS = {
    "SEC_CERTIFICATE_INSTRUMENT": "P_puvodni_certifikat_ClassA_CUSIP.json",
    "FDA_LEGAL_APPLICANT_BRIDGE": "PFE_datovany_seznam_dcer_a_applicant.json",
    "DOJ_CASE_CAPTION": "AAPL_strany_datovanych_soudnich_dokumentu.json",
    "SEC_SUPPLIER_REVENUE_DENOMINATOR": "SWKS_puvodni_konsolidovane_trzby.json",
    "SEC_PERSON_ROLE_CLAIM": "MSFT_datovane_zivotopisne_role.json",
}


class CurrentOriginalReviewIntegrationTests(unittest.TestCase):
    def review(self, kind):
        with tempfile.TemporaryDirectory() as folder, \
             patch("socket.socket.connect", side_effect=AssertionError("No review network")), \
             patch("sqlite3.connect", side_effect=AssertionError("No review database")):
            archive = Path(folder).resolve()
            result = reviews.run_captured_identity_review(source_root=ROOT,
                plan_name="evidence/specialist_review_plans/" + PLANS[kind], archive_root=archive)
            body = (archive / result["archive_filename"]).read_bytes()
            self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
            stored = json.loads(body)
            self.assertEqual(stored["review"], result["review"])
        self.assertEqual(0, result["source_http_requests"])
        self.assertEqual(0, result["registry_adoptions"])
        self.assertFalse(result["full_specialist_acceptance"])
        self.assertFalse(result["scoring_applied"])
        return result["review"]

    def test_original_certificate_literal_fact_keeps_blank_date_and_specimen_limits(self):
        leaf = self.review("SEC_CERTIFICATE_INSTRUMENT")
        self.assertTrue(leaf["literal_specimen_class_cusip_scope_observed"])
        self.assertEqual("74624M102", leaf["observed_cusip"])
        self.assertEqual("EQUITY:A", leaf["observed_class_terms"]["share_class"])
        self.assertIsNone(leaf["effective_on"])
        self.assertIsNone(leaf["issued_share_count"])
        self.assertIsNone(leaf["registered_holder"])
        for key in ("issued_instrument_validity_verified", "symbol_cusip_continuity_verified",
                    "automated_pixel_text_authentication", "automatic_alias_allowed"):
            self.assertFalse(leaf[key], key)
        self.assertEqual(0, leaf["current_q3_verified_instrument_coverage"])
        self.assertLessEqual(leaf["review_started_at"], leaf["knowledge_at"])
        self.assertEqual(leaf["knowledge_at"], leaf["review_recorded_at"])

    def test_original_2017_roster_does_not_supply_current_exact_fda_applicant_ownership(self):
        leaf = self.review("FDA_LEGAL_APPLICANT_BRIDGE")
        self.assertEqual("PARTIAL", leaf["status"])
        self.assertEqual(0, leaf["registry_adoptions"])
        self.assertEqual([], leaf["exact_applicant_roster_rows"])
        self.assertEqual("2017-12-31", leaf["selected_roster_facts"][0]["roster_as_of"])
        self.assertEqual("Pfizer Ireland Pharmaceuticals", leaf["selected_roster_facts"][0]["legal_entity_name_as_displayed"])
        self.assertEqual(["001", "002"], [row["product_number"] for row in leaf["applicant_source"]["product_rows"]])
        self.assertFalse(leaf["applicant_source"]["application_facts"]["detail_container_explicitly_closed"])
        self.assertLessEqual(leaf["review_started_at"], leaf["known_at"])
        self.assertEqual(leaf["known_at"], leaf["review_completed_at"])
        for child in (leaf["applicant_source"], leaf["company_source"]):
            self.assertLessEqual(child["known_at"], leaf["known_at"])
        for key in ("exact_applicant_name_match_supported", "current_applicant_issuer_control_verified",
                    "direct_legal_parent_verified", "product_owner_verified", "similar_name_is_approved_alias",
                    "approval_date_is_ownership_start", "continuous_control_inferred", "issuer_attribution_allowed"):
            self.assertFalse(leaf[key], key)
        self.assertIn("Pfizer Ireland Pharmaceuticals", json.dumps(leaf))
        self.assertIn("Pfizer Ireland Pharmaceuticals Unlimited Company", json.dumps(leaf))

    def test_original_case_captions_remain_document_local_without_issuer_or_disposition_bridge(self):
        leaf = self.review("DOJ_CASE_CAPTION")
        self.assertEqual("2:24-cv-04055", leaf["case_scope"]["case_docket"])
        self.assertEqual((2, 3), (leaf["supported_case_documents"], leaf["supported_caption_blocks"]))
        self.assertLessEqual(leaf["review_started_at"], leaf["known_at"])
        self.assertEqual(leaf["review_completed_at"], leaf["known_at"])
        self.assertFalse(leaf["source_read_cutoff_is_output_availability"])
        self.assertEqual(["2024-09-12", "2025-06-30"],
            [row["selected_filed_on"] for row in leaf["document_reviews"]])
        for key in ("issuer_identity_verified", "financial_cik_identity_verified", "legal_continuity_verified",
                    "original_separate_order_present", "current_disposition_verified", "merits_adjudication_verified"):
            self.assertFalse(leaf[key], key)
        self.assertTrue(all(not row["caption_variants_unified_as_aliases"] for row in leaf["document_reviews"]))

    def test_original_revenue_totals_preserve_source_scale_without_customer_amount(self):
        leaf = self.review("SEC_SUPPLIER_REVENUE_DENOMINATOR")
        self.assertEqual("CANDIDATE_REVIEWED", leaf["status"])
        self.assertEqual(["4086900000", "4178000000", "4772400000"],
            [row["scaled_total_net_revenue_usd"] for row in leaf["claim"]["quantities"]])
        self.assertEqual("UNKNOWN_NOT_RECONSTRUCTED", leaf["claim"]["definition_comparability_across_prior_filings"])
        self.assertEqual("WITHHELD_DENOMINATOR_BASIS_UNVERIFIED", leaf["claim"]["concentration_join_status"])
        self.assertTrue(leaf["claim"]["same_original_issuer_and_period_supported"])
        self.assertFalse(leaf["claim"]["denominator_basis_identity_verified"])
        for key in ("customer_amount_calculated", "counterparty_purchases_verified", "direct_purchase_share_verified",
                    "physical_quantity_denominator_verified", "historical_as_filed_comparison_verified"):
            self.assertFalse(leaf[key], key)

    def test_original_biography_year_precision_never_becomes_role_interval_or_account_alias(self):
        leaf = self.review("SEC_PERSON_ROLE_CLAIM")
        self.assertTrue(leaf["source_role_claims_supported"])
        self.assertEqual(7, leaf["role_claim_count"])
        self.assertEqual("2025-10-21", leaf["source_document"]["filing_day"])
        for row in leaf["role_claims"]:
            self.assertEqual("YEAR", row["start_assertion"]["precision"])
            self.assertIsNone(row["start_assertion"]["exact_day"])
            self.assertFalse(row["continuous_role_verified"])
            if row["end_assertion"] and row["end_assertion"]["literal"] == "present":
                self.assertEqual("2025-10-21", row["end_assertion"]["anchor_filing_day"])
        for key in ("approved_name_alias", "natural_person_account_binding_verified", "continuous_role_verified",
                    "cusip_class_validity_verified", "issuer_attribution_allowed"):
            self.assertFalse(leaf[key], key)

    def test_new_kind_body_limit_and_future_knowledge_reject_before_any_body_io(self):
        cutoff = datetime.now(timezone.utc)
        for kind in PLANS:
            for metadata, size in (({}, 4_000_001), ({"known_at": (cutoff + timedelta(days=1)).isoformat()}, 1)):
                source = {"metadata": metadata, "body_available": True,
                    "body_ref": {"path": "never-opened.body", "sha256": "0" * 64, "bytes": size}}
                with self.subTest(kind=kind, metadata=metadata), \
                     patch.object(Path, "open", side_effect=AssertionError("No invalid body read")):
                    with self.assertRaises(ValueError):
                        reviews._load_captures({"kind": kind, "captures": {"source": source}}, ROOT, cutoff)


class ExplicitReadinessActionIntegrationTests(unittest.TestCase):
    def test_benchmark_outside_canonical_subjects_is_refused_before_reading_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder).resolve() / "readiness.json"
            with patch("market_checker_app.services.user_windows_readiness_capture._file_pin",
                       side_effect=AssertionError("Invalid subject cannot read inputs")), \
                 redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as error:
                main(["windows-readiness", "--enable", "--output", str(output), "--tickers", "SPY"])
            self.assertEqual(1, error.exception.code)
            self.assertFalse(output.exists())

    def test_default_cli_does_not_read_inputs_or_create_a_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder).resolve() / "absent" / "readiness.json"
            stream = io.StringIO()
            with patch("socket.socket.connect", side_effect=AssertionError("No network")), \
                 patch("sqlite3.connect", side_effect=AssertionError("No database")), \
                 patch("market_checker_app.services.user_windows_readiness_capture._file_pin",
                       side_effect=AssertionError("Default off cannot read source files")), redirect_stdout(stream):
                main(["windows-readiness", "--output", str(output), "--tickers", "AAPL"])
            result = json.loads(stream.getvalue())
            self.assertEqual("DISABLED", result["status"])
            self.assertFalse(result["receipt_created"])
            self.assertFalse(output.parent.exists())

    def test_explicit_cli_publishes_readiness_only_and_rejects_overwriting_its_receipt(self):
        from market_checker_app.services.user_windows_readiness_capture import load_user_windows_readiness_receipt
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder).resolve() / "readiness.json"
            argv = ["windows-readiness", "--enable", "--output", str(output), "--tickers", "AAPL", "MSFT"]
            with patch("socket.socket.connect", side_effect=AssertionError("No network")), \
                 patch("sqlite3.connect", side_effect=AssertionError("No database")), redirect_stdout(io.StringIO()):
                main(argv)
                before = output.read_bytes()
                with self.assertRaises(SystemExit) as error:
                    main(argv)
            self.assertEqual(1, error.exception.code)
            self.assertEqual(before, output.read_bytes())
            receipt = load_user_windows_readiness_receipt(output)
            self.assertEqual("READINESS_CAPTURE", receipt["scope"])
            self.assertEqual("WAIT", receipt["readiness_status"])
            self.assertFalse(receipt["user_windows_end_to_end_verified"])
            self.assertFalse(receipt["full_specialist_acceptance"])
            self.assertEqual("ABSENT", receipt["same_process_production_run_receipt"])

    def test_ui_default_render_never_captures_and_explicit_submit_selects_exact_inputs_once(self):
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            source = ("from pathlib import Path\nimport streamlit as st\n"
                "from market_checker_app.specialist_evidence_ui import render_user_windows_readiness\n"
                f"render_user_windows_readiness(st,db_path=Path({str(root / 'existing.db')!r}),output_dir=Path({str(root)!r}))\n")
            with patch("market_checker_app.specialist_evidence_ui.run_user_windows_readiness_capture") as capture, \
                 patch("socket.socket.connect", side_effect=AssertionError("No UI network")), \
                 patch("sqlite3.connect", side_effect=AssertionError("No UI database")):
                capture.return_value = {"status": "WAIT", "scope": "READINESS_CAPTURE", "receipt_created": True}
                app = AppTest.from_string(source).run(timeout=20)
                self.assertFalse(app.checkbox[0].value)
                self.assertEqual("", app.checkbox[0].proto.form_id)
                self.assertTrue(app.button[0].disabled)
                capture.assert_not_called()
                app.checkbox[0].set_value(True)
                app.text_input[0].set_value("AAPL MSFT")
                app.text_input[1].set_value(str(root / "selected.json"))
                app.text_input[2].set_value(str(root / "originals"))
                app.run(timeout=20)
                app.button[0].click().run(timeout=20)
                self.assertEqual([], list(app.exception))
                capture.assert_called_once()
                selected = capture.call_args.kwargs
                self.assertTrue(selected["enabled"])
                self.assertEqual(["AAPL", "MSFT"], selected["tickers"])
                self.assertEqual(root / "selected.json", selected["market_manifest_path"])
                self.assertEqual(root / "originals", selected["market_archive_root"])
                app.run(timeout=20)
                capture.assert_called_once()
                self.assertFalse((root / "user_windows_readiness").exists())


if __name__ == "__main__":
    unittest.main()
