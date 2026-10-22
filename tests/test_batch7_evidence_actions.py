"""Integration of explicit evidence actions without changing model cohorts."""
from pathlib import Path
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch

from market_checker_app.config import AppConfig
from market_checker_app.release_manifest import config_hash
from market_checker_app.services import captured_identity_review_service as reviews
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
PLANS = "evidence/specialist_review_plans/"


class ProspectiveAuditConfigTests(unittest.TestCase):
    def test_archiving_opt_in_and_directory_do_not_change_model_fingerprint(self):
        default = AppConfig()
        enabled = AppConfig(prospective_history_enabled=True,
                            prospective_audit_archive_root=Path("selected-evidence"))
        self.assertFalse(default.prospective_history_enabled)
        self.assertIsNone(default.prospective_audit_archive_root)
        self.assertEqual(config_hash(default), config_hash(enabled))
        enabled.prospective_audit_archive_root = Path("another-selected-evidence")
        self.assertEqual(config_hash(default), config_hash(enabled))
        enabled.decision_thresholds.buy_min_spread += 1
        self.assertNotEqual(config_hash(default), config_hash(enabled))

    def test_mapping_model_settings_still_affect_hash_after_audit_opt_in(self):
        plain = {"decision_threshold": 8, "source_enabled": True}
        audit = {**plain, "prospective_history_enabled": True,
                 "prospective_audit_archive_root": "explicit-selected-archive"}
        self.assertEqual(config_hash(plain), config_hash(audit))
        self.assertNotEqual(config_hash(plain), config_hash({**audit, "decision_threshold": 9}))


class ProspectiveAuditUISmokeTests(unittest.TestCase):
    def test_enabling_capture_does_not_register_or_download_until_forecast_is_saved(self):
        from streamlit.testing.v1 import AppTest
        from market_checker_app.services.agent_runtime_service import AgentRuntimeSettings
        app_path = Path(__file__).resolve().parents[1] / "market_checker_app/app.py"
        with tempfile.TemporaryDirectory() as directory, \
             patch("market_checker_app.services.agent_runtime_service.AgentRuntimeService.load",
                   return_value=(AgentRuntimeSettings(), None)), \
             patch("market_checker_app.services.prospective_specialist_history.ProspectiveSpecialistHistory.register_stored_snapshot") as register, \
             patch("socket.socket.connect", side_effect=AssertionError("Unexpected HTTP")):
            app = AppTest.from_file(str(app_path)).run(timeout=30)
            checkbox = next(x for x in app.checkbox
                            if x.label == "Zachovávat nové předpovědi pro historické ověření")
            self.assertFalse(checkbox.value)
            field = next(x for x in app.text_input if x.label == "Složka vybraných důkazů předpovědí")
            self.assertTrue(field.disabled)
            checkbox.set_value(True)
            app.run(timeout=30)
            next(x for x in app.text_input if x.label == "Složka vybraných důkazů předpovědí").set_value(directory)
            app.run(timeout=30)
            self.assertEqual([], list(app.exception))
            self.assertFalse(next(x for x in app.text_input
                                  if x.label == "Složka vybraných důkazů předpovědí").disabled)
            register.assert_not_called()
            self.assertEqual([], list(Path(directory).iterdir()))

    def test_source_selection_requires_prepare_then_explicit_activation_with_exact_prior_hash(self):
        from streamlit.testing.v1 import AppTest
        target = "market_checker_app.services.prospective_source_manifest_service.ProspectiveSourceManifestService"
        prior = "e" * 64
        prepared = {"source_pins": [{"path": "selected/source.json"}], "label_pins": [],
                    "prepared_at": "2026-10-15T07:00:00+00:00", "current_manifest_sha256": prior}
        saved = {"active_manifest_path": None, "status": "VERSION_PREPARED"}
        with tempfile.TemporaryDirectory() as directory, \
             patch(target + ".prepare", return_value=prepared) as prepare, \
             patch(target + ".publish", return_value=saved) as publish, \
             patch("socket.socket.connect", side_effect=AssertionError("Unexpected HTTP")), \
             patch("sqlite3.connect", side_effect=AssertionError("Unexpected database")):
            script = ("from pathlib import Path\nimport streamlit as st\n"
                      "from market_checker_app.specialist_evidence_ui import render_prospective_source_selection\n"
                      f"render_prospective_source_selection(st, output_dir=Path({directory!r}))\n")
            app = AppTest.from_string(script).run(timeout=30)
            prepare.assert_not_called()
            publish.assert_not_called()
            next(x for x in app.text_input if x.label == "Akcie pro tento výběr zdrojů").set_value("NVDA")
            next(x for x in app.text_area if x.label == "Vybrané původní cenové a zdrojové soubory").set_value("selected/source.json")
            next(x for x in app.button if x.label == "Ověřit tento výběr zdrojů").click().run(timeout=30)
            self.assertEqual([], list(app.exception))
            prepare.assert_called_once_with(forecast_sources=[{"ticker": "NVDA", "benchmark_ticker": "SPY",
                "source_archive_paths": ["selected/source.json"]}], label_observations=[])
            publish.assert_not_called()
            next(x for x in app.button if x.label == "Uložit tuto verzi výběru zdrojů").click().run(timeout=30)
            publish.assert_called_once_with(prepared, activate=False, expected_current_sha256=None)
            next(x for x in app.checkbox if x.label == "Použít tento výběr při příštím ukládání předpovědí").set_value(True).run(timeout=30)
            next(x for x in app.button if x.label == "Uložit tuto verzi výběru zdrojů").click().run(timeout=30)
            self.assertEqual([], list(app.exception))
            self.assertEqual({"activate": True, "expected_current_sha256": prior}, publish.call_args.kwargs)
            self.assertEqual([], list(Path(directory).iterdir()))


class NewReviewBudgetTests(unittest.TestCase):
    def test_new_source_review_cap_is_checked_before_original_body_io(self):
        item = {"metadata": {}, "body_available": True,
                "body_ref": {"path": "not-opened.body", "sha256": "0" * 64, "bytes": 4_000_001}}
        for kind in ("SEC_PERSON_KIND", "DOJ_ENFORCEMENT_SCOPE", "SEC_SUPPLY_CHAIN_SCOPE",
                     "HEALTHCARE_PRODUCT_SCOPE", "SEC_CONTROL_INSTRUMENT", "SEC_IR_DISCLOSURE_AUTHORITY"):
            with self.subTest(kind=kind), \
                 patch.object(Path, "open", side_effect=AssertionError("Over-budget source was opened")), \
                 self.assertRaises(ValueError):
                reviews._load_captures({"kind": kind, "captures": {"source": item}},
                                      Path("."), datetime.now(timezone.utc))


class ActualCapturedReviewIntegrationTests(unittest.TestCase):
    def _run(self, name):
        with tempfile.TemporaryDirectory() as directory, \
             patch("socket.socket.connect", side_effect=AssertionError("Unexpected HTTP")):
            before = datetime.now(timezone.utc)
            result = reviews.run_captured_identity_review(source_root=ROOT,
                plan_name=PLANS + name, archive_root=Path(directory))
            raw = (Path(directory) / result["archive_filename"]).read_bytes()
            self.assertEqual(result["archive_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(result["review"], json.loads(raw)["review"])
        self.assertGreaterEqual(datetime.fromisoformat(result["recorded_at"]), before)
        self.assertEqual(0, result["source_http_requests"])
        self.assertEqual(0, result["registry_adoptions"])
        self.assertFalse(result["issuer_attribution_allowed"])
        self.assertFalse(result["full_specialist_acceptance"])
        self.assertFalse(result["scoring_applied"])
        return result["review"]

    def test_actual_legal_control_conflict_survives_runtime_without_q3_adoption(self):
        leaf = self._run("LEG_kontrola_a_instrument.json")
        self.assertEqual("PRIMARY_CONTROL_INSTRUMENT_PARTIAL", leaf["status"])
        self.assertTrue(leaf["control_edges"])
        self.assertTrue(leaf["control_conflicts"])
        self.assertTrue(leaf["baseline_blockers_preserved"])
        self.assertFalse(leaf["identity_transition_approved"])
        self.assertEqual(0, leaf["current_quarter_verified_coverage"])

    def test_actual_fda_application_does_not_gain_product_ownership_on_runtime_ingestion(self):
        leaf = self._run("PFE_FDA_produkt_a_vlastnictvi.json")
        self.assertEqual("WITHHOLD", leaf["decision"])
        self.assertTrue(leaf["source_object_facts"])
        self.assertIsNone(leaf["proposed_record"])
        self.assertFalse(leaf["product_ownership_verified"])
        self.assertFalse(leaf["live_acceptance_verified"])

    def test_actual_person_fact_keeps_110_byte_version_gap_and_account_identity_withheld(self):
        leaf = self._run("MSFT_datovany_udaj_osoby.json")
        self.assertEqual("PARTIAL", leaf["status"])
        self.assertTrue(leaf["natural_person_fact_supported"])
        self.assertFalse(leaf["person_identity_verified"])
        self.assertFalse(leaf["source_account_to_person_cik_verified"])
        self.assertFalse(leaf["historical_event_validity_proven"])
        document = leaf["source_document"]
        self.assertEqual(110, document["captured_primary_bytes"] - document["index_declared_primary_bytes"])
        self.assertFalse(document["exact_as_filed_body_version_verified"])

    def test_actual_supplier_runtime_uses_physical_ingestion_and_retains_unknown_quantity(self):
        leaf = self._run("NVDA_dodavatel_TSMC.json")
        self.assertEqual("CANDIDATE_REVIEWED", leaf["status"])
        claim = leaf["claim"]
        self.assertEqual("semiconductor wafers", claim["product_or_input"])
        self.assertTrue(claim["relationship_statement_supported"])
        self.assertTrue(claim["counterparty_legal_identity"]["independent_source_snapshot_supported"])
        self.assertFalse(claim["counterparty_legal_identity"]["continuous_legal_validity_verified"])
        self.assertIsNone(claim["quantified_exposure"]["value"])
        self.assertEqual(leaf["knowledge_at"], leaf["ingested_at"])
        self.assertFalse(leaf["counterparty_identity_verified"])

    def test_actual_doj_complaint_remains_allegations_and_case_date_identity_gap(self):
        leaf = self._run("AAPL_DOJ_konkretni_stiznost.json")
        self.assertEqual("PARTIAL", leaf["status"])
        self.assertTrue(leaf["exact_case_entity_supported"])
        self.assertTrue(leaf["document_stage_supported"])
        self.assertEqual("ALLEGATIONS_ONLY", leaf["recorded_case_stage"])
        self.assertFalse(leaf["adjudication_verified"])
        self.assertFalse(leaf["company_wide_liability_verified"])
        self.assertFalse(leaf["registry_adoption_allowed"])

    def test_actual_ir_disclosure_keeps_http_literal_version_conflict_and_authority_gap(self):
        leaf = self._run("NVDA_IR_web_v_podani_SEC.json")
        self.assertEqual("PARTIAL", leaf["status"])
        self.assertTrue(leaf["issuer_disclosed_ir_website_fact_supported"])
        self.assertEqual("http://investor.nvidia.com", leaf["issuer_disclosed_website_fact"]["ir_url_literal"])
        self.assertFalse(leaf["publisher_authority_verified"])
        self.assertFalse(leaf["feed_activation_allowed"])
        self.assertFalse(leaf["live_verified"])
        self.assertEqual(111, leaf["filing"]["captured_primary_bytes"] - leaf["filing"]["index_declared_primary_bytes"])


if __name__ == "__main__":
    unittest.main()
