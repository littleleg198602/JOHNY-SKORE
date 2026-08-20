from __future__ import annotations

from pathlib import Path
import unittest

from streamlit.testing.v1 import AppTest


class StreamlitUISmokeTests(unittest.TestCase):
    def test_app_starts_and_exposes_yahoo_workflow(self) -> None:
        app_path = Path(__file__).resolve().parents[1] / "market_checker_app" / "app.py"
        app = AppTest.from_file(str(app_path)).run(timeout=30)

        self.assertEqual([], list(app.exception))
        labels = [button.label for button in app.button]
        self.assertIn("Načíst watchlist z MT5", labels)
        self.assertIn("Doplnit Yahoo cache", labels)
        self.assertIn("Spustit analýzu", labels)
        self.assertIn("Uložit nastavení agentů", labels)
        number_labels = [field.label for field in app.number_input]
        self.assertIn("Yahoo tickerů v jedné automatické dávce", number_labels)
        checkbox_labels = [field.label for field in app.checkbox]
        self.assertIn("Načíst SEC výkazy (Etapa 2)", checkbox_labels)
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
        self.assertIn(
            "SEC User-Agent (aplikace + kontaktní e-mail)",
            text_labels,
        )
        text_area_labels = [field.label for field in app.text_area]
        self.assertIn(
            "Short reporty: TICKER | vydavatel | datum | HTTPS URL",
            text_area_labels,
        )
        self.assertIn(
            "Síť firem: TICKER | protistrana | typ | podíl %/- | vydavatel | datum | HTTPS URL",
            text_area_labels,
        )
        self.assertIn(
            "Materiály/energie: TICKER | zdroj | typ | podíl %/- | vydavatel | datum | HTTPS URL",
            text_area_labels,
        )
        self.assertIn(
            "Regulace/kontrakty: TICKER | typ | stav | název | protistrana/úřad | hodnota/- | měna/- | vydavatel | datum | HTTPS URL",
            text_area_labels,
        )


if __name__ == "__main__":
    unittest.main()
