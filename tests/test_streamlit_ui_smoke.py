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
        number_labels = [field.label for field in app.number_input]
        self.assertIn("Yahoo tickerů v jedné automatické dávce", number_labels)


if __name__ == "__main__":
    unittest.main()
