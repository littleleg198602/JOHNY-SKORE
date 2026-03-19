import unittest
from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from decision_engine_validation_report import build_calibration_analysis, build_driver_checks, build_validation_rows


class DecisionEngineValidationReportTests(unittest.TestCase):
    def test_all_validation_rows_pass_expected_band(self):
        rows = build_validation_rows()
        failed = [row for row in rows if not row["pass"]]
        self.assertEqual([], failed, f"Validation failures: {failed}")

    def test_driver_checks_confirm_role_assumptions(self):
        checks = build_driver_checks()
        failed = [name for name, ok in checks.items() if not ok]
        self.assertEqual([], failed, f"Driver-role check failures: {failed}")

    def test_calibration_analysis_has_expected_sections(self):
        calibration = build_calibration_analysis()
        self.assertIn("hold_diagnostics", calibration)
        self.assertIn("hold_concentration", calibration)
        self.assertIn("sensitivity_simulation", calibration)
        self.assertIn("confidence_sanity_check", calibration)
        self.assertIn("technical_driver_effectiveness", calibration)
        self.assertIn("baseline_hold_band_7", calibration["sensitivity_simulation"])


if __name__ == "__main__":
    unittest.main()
