"""Retained capacity-deposit facts through the real shared CLI and UI actions."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from market_checker_app.specialist_evidence_runner import main


ROOT = Path(__file__).resolve().parents[1]
PLAN = "evidence/specialist_review_plans/SWKS_zalohy_na_vyrobni_kapacitu.json"


class Sourcefront21CoordinatorTests(unittest.TestCase):
    def test_cli_archives_actual_deposit_review_without_collection_or_adoption(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()) as out, \
                patch("socket.socket.connect", side_effect=AssertionError("No review HTTP")), \
                patch("sqlite3.connect", side_effect=AssertionError("No review SQL")):
            main(["review", "--source-root", str(ROOT), "--plan", PLAN, "--archive-root", folder])
            report = json.loads(out.getvalue())
            saved = json.loads((Path(folder) / report["archive_filename"]).read_bytes())
            self.assertEqual(report["review"], saved["review"])
        self.assertEqual("WAFER_CAPACITY_DEPOSITS", report["kind"])
        self.assertEqual("WAFER_CAPACITY_DEPOSITS", report["review"]["review_kind"])
        self.assertEqual("CANDIDATE_REVIEWED", report["review"]["status"])
        claim = report["review"]["claim"]
        self.assertEqual(["2025-10-03", "2024-09-27"], [row["as_of_day"] for row in claim["balances"]])
        self.assertEqual(6, claim["annotation_audit"]["unique_balance_components"])
        self.assertEqual(12, claim["annotation_audit"]["asset_numeric_annotation_count"])
        self.assertEqual(109, claim["publisher_size_difference_bytes"])
        self.assertFalse(report["review"]["purchases_verified"])
        self.assertFalse(report["review"]["named_foundry_attribution_allowed"])
        self.assertFalse(report["review"]["apple_allocation_allowed"])
        self.assertEqual(0, report["source_http_requests"])
        self.assertEqual(0, report["registry_adoptions"])
        self.assertFalse(report["issuer_attribution_allowed"])
        self.assertFalse(report["scoring_applied"])
        self.assertFalse(report["full_specialist_acceptance"])

    def test_ui_selects_real_plan_and_preserves_original_review_archive(self):
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as folder, \
                patch("socket.socket.connect", side_effect=AssertionError("No UI HTTP")), \
                patch("sqlite3.connect", side_effect=AssertionError("No UI SQL")):
            target = Path(folder)
            source = ("from pathlib import Path\nimport streamlit as st\n"
                      "from market_checker_app.specialist_evidence_ui import render_specialist_evidence_actions\n"
                      f"render_specialist_evidence_actions(st,source_root=Path({str(ROOT)!r}),"
                      f"db_path=Path({str(target / 'never-opened.db')!r}),output_dir=Path({str(target)!r}))\n")
            app = AppTest.from_string(source).run(timeout=30)
            self.assertEqual([], list(app.exception))
            choice = next(item for item in app.selectbox if item.label == "Uložená vazba k posouzení")
            choice.set_value(PLAN)
            app.run(timeout=30)
            next(item for item in app.button if item.label == "Zkontrolovat vybrané uložené doklady").click().run(timeout=30)
            self.assertEqual([], list(app.exception))
            files = list((target / "captured_identity_reviews").glob("review-*.json"))
            self.assertEqual(1, len(files))
            report = json.loads(files[0].read_bytes())
            self.assertEqual("WAFER_CAPACITY_DEPOSITS", report["kind"])
            self.assertFalse(report["full_specialist_acceptance"])
            self.assertFalse((target / "never-opened.db").exists())


if __name__ == "__main__":
    unittest.main()
