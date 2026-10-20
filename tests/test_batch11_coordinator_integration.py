"""Explicit current-source reviews and native analysis capture across CLI/UI."""
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
    "SEC_DATED_INSTRUMENT_POINT": "P_instrument_ke_konkretnimu_dni.json",
    "FDA_APPLICANT_CHRONOLOGY": "PFE_datovane_zaznamy_applicant.json",
    "EPA_FRS_PROGRAM_SCOPE": "DOW_program_a_provozovna_EPA.json",
    "USASPENDING_PRIME_SCOPE": "LMT_uplnost_puvodnich_stranek_zakazek.json",
    "SEC_PERSON_ROLE_TEMPORAL": "MSFT_role_k_datu_transakce.json",
}


class Batch11SourceActionTests(unittest.TestCase):
    def review(self, kind):
        with tempfile.TemporaryDirectory() as folder, \
             patch("socket.socket.connect", side_effect=AssertionError("No review network")), \
             patch("sqlite3.connect", side_effect=AssertionError("No review SQL")):
            archive = Path(folder).resolve()
            result = reviews.run_captured_identity_review(source_root=ROOT,
                plan_name="evidence/specialist_review_plans/" + PLANS[kind], archive_root=archive)
            original = (archive / result["archive_filename"]).read_bytes()
            self.assertEqual(hashlib.sha256(original).hexdigest(), result["archive_sha256"])
            self.assertEqual(result["review"], json.loads(original)["review"])
        self.assertEqual(kind, result["kind"])
        self.assertEqual(0, result["source_http_requests"])
        self.assertEqual(0, result["registry_adoptions"])
        self.assertFalse(result["issuer_attribution_allowed"])
        self.assertFalse(result["full_specialist_acceptance"])
        self.assertFalse(result["scoring_applied"])
        return result["review"]

    def test_original_instrument_point_does_not_extend_missing_dated_class_bridge(self):
        leaf = self.review("SEC_DATED_INSTRUMENT_POINT")
        self.assertFalse(leaf["point_supported"])
        self.assertIsNone(leaf["point"])
        self.assertTrue(leaf["blockers"])
        self.assertEqual(leaf["knowledge_at"], leaf["review_recorded_at"])
        self.assertLessEqual(leaf["review_started_at"], leaf["knowledge_at"])

    def test_applicant_chronology_keeps_original_roster_date_and_missing_endpoints(self):
        leaf = self.review("FDA_APPLICANT_CHRONOLOGY")
        self.assertTrue(leaf["roster_points"])
        self.assertEqual("2017-12-31", leaf["roster_points"][0]["source_roster_as_of"])
        self.assertTrue(leaf["application_points"])
        self.assertFalse(leaf["roster_points"][0]["universe"]["complete"])
        self.assertEqual("UNKNOWN", leaf["roster_points"][0]["universe"]["status"])
        self.assertFalse(leaf["product_owner_verified"])
        self.assertFalse(leaf["last_observation_carried_forward"])

    def test_missing_original_frs_body_cannot_become_genuine_program_fact(self):
        leaf = self.review("EPA_FRS_PROGRAM_SCOPE")
        self.assertEqual("EPA_FRS_PROGRAM_SCOPE", leaf["review_kind"])
        self.assertFalse(leaf["facility_identity_supported"])
        self.assertFalse(leaf["program_inventory_supported"])
        self.assertIsNone(leaf["publisher_query_rows"])
        self.assertIsNone(leaf["observed_program_rows"])
        self.assertIn("MISSING_OR_WHOLE_WITHHELD_ORIGINAL", leaf["missing_evidence"])

    def test_embedded_prime_tail_cannot_replace_whole_original_page_chain(self):
        leaf = self.review("USASPENDING_PRIME_SCOPE")
        self.assertEqual("WAIT", leaf["status"])
        self.assertIsNone(leaf["query_scope"])
        self.assertIsNone(leaf["reported_unique_awards"])
        self.assertFalse(leaf["query_complete"])
        self.assertFalse(leaf["genuine_negative_verified"])
        self.assertEqual(5, len(leaf["missing_evidence"]))

    def test_old_proxy_present_does_not_supply_role_for_later_form4_transaction(self):
        leaf = self.review("SEC_PERSON_ROLE_TEMPORAL")
        self.assertTrue(leaf["requested_transaction_binding_supported"])
        self.assertEqual("UNVERIFIED", leaf["role_at_transaction_day_status"])
        self.assertFalse(leaf["selected_role_temporal_assertion"]["source_assertion_temporally_compatible"])
        self.assertFalse(leaf["continuous_role_verified"])
        self.assertEqual(leaf["known_at"], leaf["review_available_at"])

    def test_each_new_kind_refuses_future_or_oversized_capture_before_body_io(self):
        cutoff = datetime.now(timezone.utc)
        for kind in PLANS:
            for metadata, size in (({}, 4_000_001),
                    ({"known_at": (cutoff + timedelta(days=1)).isoformat()}, 1)):
                with self.subTest(kind=kind, metadata=metadata), \
                     patch.object(Path, "open", side_effect=AssertionError("No invalid body read")):
                    with self.assertRaises(ValueError):
                        reviews._load_captures({"kind": kind, "captures": {"source": {
                            "metadata": metadata, "body_available": True,
                            "body_ref": {"path": "never-opened.body", "sha256": "0" * 64,
                                         "bytes": size}}}}, ROOT, cutoff)


class ExplicitWindowsAnalysisActionTests(unittest.TestCase):
    def test_cli_default_does_not_initialize_database_pipeline_or_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            output = root / "absent" / "run.json"
            stream = io.StringIO()
            with patch("socket.socket.connect", side_effect=AssertionError("No default network")), \
                 patch("sqlite3.connect", side_effect=AssertionError("No default SQL")), \
                 patch("market_checker_app.services.pipeline_service.PipelineService",
                       side_effect=AssertionError("No default pipeline")), redirect_stdout(stream):
                main(["windows-analysis", "--output", str(output), "--tickers", "AAPL",
                    "--db-path", str(root / "existing.db"), "--market-manifest", str(root / "missing.json"),
                    "--market-archive-root", str(root / "missing-originals")])
            report = json.loads(stream.getvalue())
            self.assertEqual("DISABLED", report["status"])
            self.assertFalse(output.parent.exists())

    def test_ui_default_and_explicit_run_are_separate_with_exact_selected_scope(self):
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            source = ("from pathlib import Path\nimport streamlit as st\n"
                "from market_checker_app.specialist_evidence_ui import render_user_windows_analysis\n"
                f"render_user_windows_analysis(st,db_path=Path({str(root / 'selected.db')!r}),output_dir=Path({str(root)!r}))\n")
            with patch("market_checker_app.specialist_evidence_ui.run_user_windows_analysis_capture") as run, \
                 patch("socket.socket.connect", side_effect=AssertionError("No UI network")), \
                 patch("sqlite3.connect", side_effect=AssertionError("No UI SQL")):
                run.return_value = {"status": "WAIT", "full_specialist_acceptance": False}
                app = AppTest.from_string(source).run(timeout=20)
                self.assertEqual([], list(app.exception))
                self.assertFalse(app.checkbox[0].value)
                self.assertEqual("", app.checkbox[0].proto.form_id)
                self.assertTrue(app.button[0].disabled)
                run.assert_not_called()
                app.checkbox[0].set_value(True)
                app.text_input[0].set_value("AAPL MSFT")
                app.text_input[1].set_value(str(root / "selected.json"))
                app.text_input[2].set_value(str(root / "originals"))
                app.run(timeout=20)
                app.button[0].click().run(timeout=20)
                self.assertEqual([], list(app.exception))
                run.assert_called_once()
                selected = run.call_args.kwargs
                self.assertTrue(selected["enabled"])
                self.assertEqual(["AAPL", "MSFT"], selected["tickers"])
                self.assertEqual(root / "selected.db", selected["db_path"])
                self.assertEqual(root / "selected.json", selected["market_manifest_path"])
                self.assertEqual(root / "originals", selected["market_archive_root"])
                app.run(timeout=20)
                run.assert_called_once()
                self.assertFalse((root / "user_windows_analysis").exists())


if __name__ == "__main__":
    unittest.main()
