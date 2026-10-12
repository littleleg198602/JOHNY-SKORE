from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class StreamlitUISmokeTests(unittest.TestCase):
    def test_transaction_button_targets_only_selected_verified_award(self) -> None:
        from datetime import datetime, timezone
        from market_checker_app.services.agent_runtime_service import AgentRuntimeSettings
        from market_checker_app.storage.scout_store import ScoutStore

        app_path = Path(__file__).resolve().parents[1] / "market_checker_app" / "app.py"
        with tempfile.TemporaryDirectory() as directory, \
             patch("market_checker_app.services.agent_runtime_service.AgentRuntimeService.load",
                   return_value=(AgentRuntimeSettings(), None)), \
             patch("market_checker_app.scout_runner.run_award_transactions",
                   return_value={"status": "COMPLETE", "accepted_rows": 14, "new_findings": 14}) as collect, \
             patch("socket.socket.connect", side_effect=AssertionError("UI test attempted live network")):
            database = Path(directory) / "history.db"
            clock = datetime(2026, 10, 1, tzinfo=timezone.utc)
            finding_id = ScoutStore(database).record_finding(
                source="usaspending", subject_id="LMT", source_object_id="test-prime",
                content_hash="test-primary", title="Selected test award",
                source_url="https://api.usaspending.gov/api/v2/search/spending_by_award/",
                locator="test", published_at=clock, available_at=clock, observed_at=clock,
                details={"stage": "prime_contract"},
            )[0]
            app = AppTest.from_file(str(app_path)).run(timeout=30)
            next(field for field in app.text_input if field.label == "DB soubor").set_value(str(database))
            next(field for field in app.text_area
                 if field.label == "Ruční watchlist (jeden ticker na řádek)").set_value("LMT")
            app.run(timeout=30)
            self.assertEqual([], list(app.exception))
            collect.assert_not_called()
            next(button for button in app.button if button.label == "Načíst transakce vybrané zakázky").click()
            app.run(timeout=30)
            self.assertEqual([], list(app.exception))
            collect.assert_called_once_with(db_path=database, subject_id="LMT", award_finding_id=finding_id)

    def test_source_archive_export_is_explicit_immutable_and_not_acceptance(self) -> None:
        import json
        from market_checker_app.services.agent_runtime_service import AgentRuntimeSettings
        from market_checker_app.prediction_contract import canonical_hash

        app_path = Path(__file__).resolve().parents[1] / "market_checker_app" / "app.py"
        with tempfile.TemporaryDirectory() as directory, \
             patch("market_checker_app.services.agent_runtime_service.AgentRuntimeService.load", return_value=(AgentRuntimeSettings(), None)), \
             patch("socket.socket.connect", side_effect=AssertionError("Archive export attempted live network")):
            app = AppTest.from_file(str(app_path)).run(timeout=30)
            database = Path(directory) / "history.db"
            for field in app.text_input:
                if field.label == "DB soubor": field.set_value(str(database))
                if field.label == "Output directory": field.set_value(directory)
            app.run(timeout=30)
            self.assertEqual([], list(app.exception))
            self.assertNotIn("specialist_source_archive", app.session_state)
            next(button for button in app.button if button.label == "Uložit archiv jednotlivých zdrojů").click()
            app.run(timeout=30)
            self.assertEqual([], list(app.exception))
            cached = app.session_state["specialist_source_archive"]
            archive = cached["archive"]
            self.assertEqual(str(database.resolve()), cached["db_path"])
            self.assertEqual(687, archive["universe"]["ticker_count"])
            self.assertFalse(archive["completion_verified"])
            self.assertFalse(archive["out_of_sample_evaluation_verified"])
            self.assertEqual(0, archive["input"]["exported_observations"])
            path = Path(directory) / "specialist_source_archives" / cached["filename"]
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(archive, saved)
            self.assertEqual(saved["archive_hash"], canonical_hash({k: v for k, v in saved.items() if k != "archive_hash"}))

    def test_specialist_history_is_explicit_and_empty_archive_stays_insufficient(self) -> None:
        from market_checker_app.services.agent_runtime_service import AgentRuntimeSettings

        app_path = Path(__file__).resolve().parents[1] / "market_checker_app" / "app.py"
        with tempfile.TemporaryDirectory() as directory, \
             patch("market_checker_app.services.agent_runtime_service.AgentRuntimeService.load", return_value=(AgentRuntimeSettings(), None)), \
             patch("socket.socket.connect", side_effect=AssertionError("History report attempted live network")):
            app = AppTest.from_file(str(app_path)).run(timeout=30)
            database = Path(directory) / "history.db"
            next(field for field in app.text_input if field.label == "DB soubor").set_value(str(database))
            app.run(timeout=30)
            self.assertEqual([], list(app.exception))
            self.assertNotIn("specialist_history_report", app.session_state)
            next(button for button in app.button
                 if button.label == "Vyhodnotit uloženou historii specialistů").click()
            app.run(timeout=30)
            self.assertEqual([], list(app.exception))
            cached = app.session_state["specialist_history_report"]
            self.assertEqual(str(database.resolve()), cached["db_path"])
            report = cached["report"]
            self.assertEqual("INSUFFICIENT_DATA", report["status"])
            self.assertEqual(0, report["input"]["snapshot_rows"])
            self.assertEqual(21, len(report["specialist_coverage"]))
            self.assertFalse(report["completion_verified"])
            self.assertFalse(report["activation_allowed"])
            self.assertIsNone(app.session_state["last_result"])

    def test_invalid_optional_manifest_is_quarantined_without_blocking_analysis(self) -> None:
        from tests.test_runtime_integration import _FakeYahooClient
        from market_checker_app.services.agent_runtime_service import AgentRuntimeSettings

        app_path = Path(__file__).resolve().parents[1] / "market_checker_app" / "app.py"
        settings = AgentRuntimeSettings(macro_observations_text="BROKEN OPTIONAL ROW")
        with tempfile.TemporaryDirectory() as directory, \
             patch("market_checker_app.services.pipeline_service.YahooClient", return_value=_FakeYahooClient()), \
             patch("market_checker_app.services.agent_runtime_service.AgentRuntimeService.load", return_value=(settings, None)), \
             patch("market_checker_app.services.agent_runtime_service.AgentRuntimeService.save"), \
             patch("socket.socket.connect", side_effect=AssertionError("UI test attempted live network")):
            app = AppTest.from_file(str(app_path)).run(timeout=30)
            for field in app.text_input:
                if field.label == "DB soubor":
                    field.set_value(str(Path(directory) / "history.db"))
                if field.label == "Output directory":
                    field.set_value(directory)
            for field in app.checkbox:
                if field.label in {"Export do Excelu", "Použít RSS zprávy"} or "MT5" in field.label:
                    field.uncheck()
            next(
                field
                for field in app.text_area
                if field.label == "Ruční watchlist (jeden ticker na řádek)"
            ).set_value("AAPL")
            next(
                button for button in app.button if button.label == "Spustit analýzu"
            ).click()
            app.run(timeout=30)

        self.assertEqual([], list(app.exception))
        self.assertIsNotNone(app.session_state["last_result"])
        errors = "\n".join(str(element.value) for element in app.error)
        warnings = "\n".join(str(element.value) for element in app.warning)
        self.assertNotIn("Analýza nebyla spuštěna", errors)
        self.assertIn("karantény", warnings)

    def test_analysis_button_persists_and_displays_shared_reports(self) -> None:
        from tests.test_runtime_integration import _FakeYahooClient
        from market_checker_app.services.agent_runtime_service import AgentRuntimeSettings
        from market_checker_app.storage.sqlite_store import SQLiteStore
        app_path = Path(__file__).resolve().parents[1] / "market_checker_app" / "app.py"
        with tempfile.TemporaryDirectory() as directory, \
             patch("market_checker_app.services.pipeline_service.YahooClient", return_value=_FakeYahooClient()), \
             patch("market_checker_app.services.agent_runtime_service.AgentRuntimeService.load", return_value=(AgentRuntimeSettings(), None)), \
             patch("socket.socket.connect", side_effect=AssertionError("UI test attempted live network")):
            app = AppTest.from_file(str(app_path)).run(timeout=30)
            for field in app.text_input:
                if field.label == "DB soubor":
                    field.set_value(str(Path(directory) / "history.db"))
                if field.label == "Output directory":
                    field.set_value(directory)
            for field in app.checkbox:
                if field.label in {"Export do Excelu", "Použít RSS zprávy"} or "MT5" in field.label:
                    field.uncheck()
            next(field for field in app.text_area if field.label == "Ruční watchlist (jeden ticker na řádek)").set_value("AAPL")
            next(button for button in app.button if button.label == "Spustit analýzu").click()
            app.run(timeout=30)
            self.assertEqual([], list(app.exception))
            summary = app.session_state["last_result"]["analysis_summary"]
            self.assertEqual(1, summary["point_in_time_snapshot_count"])
            self.assertEqual(1, len(SQLiteStore(Path(directory) / "history.db").read_prediction_snapshots()))
            displayed = "\n".join(str(element.value) for element in app.markdown)
            self.assertIn("Makro a sektorový režim", displayed)
            self.assertIn("Kandidátní model", displayed)
            self.assertTrue((Path(directory) / "weekly_shadow_latest.json").exists())

    def test_app_starts_and_exposes_yahoo_workflow(self) -> None:
        app_path = Path(__file__).resolve().parents[1] / "market_checker_app" / "app.py"
        app = AppTest.from_file(str(app_path)).run(timeout=30)

        self.assertEqual([], list(app.exception))
        labels = [button.label for button in app.button]
        self.assertIn("Načíst watchlist z MT5", labels)
        self.assertIn("Doplnit Yahoo cache", labels)
        self.assertIn("Spustit analýzu", labels)
        self.assertIn("Uložit nastavení agentů", labels)
        self.assertIn("Stáhnout provozní přehled specialistů",
                      [element.proto.label for element in app.get("download_button")])
        number_labels = [field.label for field in app.number_input]
        self.assertIn("Yahoo tickerů v jedné automatické dávce", number_labels)
        checkbox_labels = [field.label for field in app.checkbox]
        self.assertIn("Načíst SEC výkazy (Etapa 2)", checkbox_labels)
        self.assertNotIn(
            "Načíst evropské regulatorní dokumenty (Etapa 5.1)",
            checkbox_labels,
        )
        self.assertIn(
            "Spustit finanční forenzní screening (Etapa 2)",
            checkbox_labels,
        )
        self.assertIn("Načíst short reporty (Etapa 2)", checkbox_labels)
        self.assertIn(
            "Automaticky hledat short reporty v RSS",
            checkbox_labels,
        )
        self.assertIn(
            "Ověřit tvrzení reportů proti SEC datům",
            checkbox_labels,
        )
        self.assertIn(
            "Načíst vztahy dodavatelů a odběratelů (Etapa 3)",
            checkbox_labels,
        )
        self.assertIn(
            "Automaticky hledat koncentrace dodavatelů a zákazníků v SEC 10-K",
            checkbox_labels,
        )
        self.assertIn(
            "Načíst expozice na materiály a energie (Etapa 3)",
            checkbox_labels,
        )
        self.assertIn(
            "Automaticky hledat materiály a energie v SEC 10-K",
            checkbox_labels,
        )
        self.assertIn(
            "Načíst regulační a kontraktní události (Etapa 3)",
            checkbox_labels,
        )
        self.assertIn(
            "Automaticky hledat regulační a kontraktní události v RSS",
            checkbox_labels,
        )
        self.assertIn(
            "Spustit DecisionAgent a OOS evaluaci (Etapa 4, shadow)",
            checkbox_labels,
        )
        stage4 = next(
            field
            for field in app.checkbox
            if field.label
            == "Spustit DecisionAgent a OOS evaluaci (Etapa 4, shadow)"
        )
        self.assertTrue(stage4.value)
        text_labels = [field.label for field in app.text_input]
        self.assertNotIn("SEC User-Agent (aplikace + kontaktní e-mail)", text_labels)
        text_area_labels = [field.label for field in app.text_area]
        self.assertFalse(
            any(label.startswith("Evropské filingy:") for label in text_area_labels)
        )
        self.assertFalse(
            any(label.startswith("Evropské feedy:") for label in text_area_labels)
        )
        self.assertIn(
            "Short reporty: TICKER | vydavatel | datum | HTTPS URL",
            text_area_labels,
        )
        self.assertIsNotNone(app.text_area(key="supply_chain_manifest"))
        self.assertIsNotNone(app.text_area(key="commodity_energy_manifest"))
        self.assertIn(
            "Regulace/kontrakty: TICKER | typ | stav | název | protistrana/úřad | hodnota/- | měna/- | vydavatel | datum | HTTPS URL",
            text_area_labels,
        )


if __name__ == "__main__":
    unittest.main()
