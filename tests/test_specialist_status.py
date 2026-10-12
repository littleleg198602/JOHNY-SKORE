import json
from pathlib import Path
import tempfile
import unittest

from market_checker_app.services.specialist_status_service import load_specialist_status


class SpecialistStatusTests(unittest.TestCase):
    def test_inventory_has_unique_scope_and_no_unverified_done(self):
        inventory = load_specialist_status()
        rows = inventory["specialists"]
        self.assertGreaterEqual(len(rows), 20)
        self.assertTrue({
            "identity", "sec", "suppliers", "commodities", "contracts",
            "fda", "bank_fdic", "institutions", "regulatory", "evaluation",
        }.issubset({row["id"] for row in rows}))
        self.assertTrue(all(row["code"] != "DONE" for row in rows))

    def test_inventory_rejects_claim_of_done_without_live_evidence(self):
        payload = load_specialist_status()
        payload["specialists"][0]["code"] = "DONE"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cannot be DONE"):
                load_specialist_status(path)

    def test_inventory_rejects_done_without_identity_evidence(self):
        payload = load_specialist_status()
        row = payload["specialists"][0]
        row.update({
            "code": "DONE", "live": "VERIFIED",
            "positive_live_evidence": "evidence/positive.json",
            "negative_live_evidence": "evidence/negative.json",
            "windows_run_evidence": "evidence/windows.json",
            "coverage_evidence": "evidence/coverage.json",
            "historical_evaluation_evidence": "evidence/history.json",
        })
        row.pop("identity_evidence", None)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cannot be DONE"):
                load_specialist_status(path)
