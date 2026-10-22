"""Verify the four new real captures without refetching or replaying a DB write."""
from datetime import datetime, timedelta, timezone
import hashlib
from html import unescape
import io
import json
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from market_checker_app.services.doj_enforcement_scope_review import _SENSITIVE
from market_checker_app.services.nhtsa_negative_review_service import (
    NhtsaResponseCapture, documented_model_scope, read_nhtsa_scope_reviews, review_capture,
)

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / "evidence/batch7_nhtsa_20261015"
DB = Path("/workspace/scratch/334b3a149083/JOHNY-batch4-integration-20261013/outputs/market_checker_history.db")
PINS = {
    ("CADILLAC", "Escalade IQL"): (200, 2966, "295857f5ca97bee46efc80d3623a85534dcdda18221847120d6e76b99a45726a", "POSITIVE_EXACT_MODEL_YEAR", 2),
    ("FORD", "Mustang GTD"): (200, 1134, "3b9206dc989f515dcd08af56f800896d9566e5bea919c2cccbca86d7ec0485c9", "POSITIVE_EXACT_MODEL_YEAR", 1),
    ("CHEVROLET", "BrightDrop"): (400, 66, "74147c27e8c00a68fa4708025f0aa95a3dbb3bea68b4d5417efb2bef19d1e786", "PARTIAL", None),
    ("CHEVROLET", "Suburban HD"): (400, 66, "74147c27e8c00a68fa4708025f0aa95a3dbb3bea68b4d5417efb2bef19d1e786", "PARTIAL", None),
}


def digest(body):
    return hashlib.sha256(body).hexdigest()


def save(name, value):
    with (FOLDER / name).open("xb") as stream:
        stream.write((json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


class ActualCaptureReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = json.loads((FOLDER / "precommitted_plan.json").read_text())
        cls.run = json.loads((FOLDER / "live_run.json").read_text())
        cls.before = json.loads((FOLDER / "retained_ledger_before.json").read_text())
        cls.after = json.loads((FOLDER / "retained_ledger_after.json").read_text())
        cls.replays = []

    def replay(self, make, model):
        entry = next(e for e in self.plan["scopes"] if (e["make"], e["model"]) == (make, model))
        live = next(r for r in self.run["reviews"] if (r["scope"]["make"], r["scope"]["model"]) == (make, model))
        scope = documented_model_scope(make=make, model=model, model_year=2026,
            catalog_body=(ROOT / entry["catalog_path"]).read_bytes(),
            catalog_metadata=json.loads((ROOT / entry["catalog_metadata_path"]).read_text()))
        body = (FOLDER / "captures" / live["archive_relative_path"]).read_bytes()
        status, size, sha, expected, count = PINS[make, model]
        self.assertEqual((status, size, sha), (live["http_status"], len(body), digest(body)))
        self.assertEqual((size, sha), (live["raw_response_bytes"], live["raw_response_sha256"]))
        self.assertIsNone(_SENSITIVE.search(unescape(body.decode()).encode()))
        capture = NhtsaResponseCapture(live["requested_url"], live["final_url"], status,
            live["content_type"], body, datetime.fromisoformat(live["started_at"]),
            datetime.fromisoformat(live["source_observed_at"]), origin="ARCHIVED_RESPONSE_REPLAY")
        now = datetime.now(timezone.utc)
        with patch("socket.create_connection", side_effect=AssertionError("Offline replay cannot fetch")), \
             patch("market_checker_app.services.nhtsa_negative_review_service.NhtsaRawRecallClient.__init__",
                   side_effect=AssertionError("Offline replay cannot initialize live client")):
            replay = review_capture(scope, capture, reviewed_at=now)
        self.assertEqual(expected, replay["status"])
        self.assertEqual(count, replay["publisher_count"])
        self.assertEqual(live["matched_rows"], replay["matched_rows"])
        self.assertEqual(live["reason"], replay["reason"])
        self.assertEqual(sha, replay["raw_response_sha256"])
        self.assertEqual(now.isoformat(), replay["known_at"])
        self.assertFalse(replay["complete_negative"])
        self.assertFalse(replay["live_complete_negative_verified"])
        for flag in ("issuer_wide_absence_verified", "issuer_attribution_verified", "manufacturer_ownership_inferred",
                     "historical_availability_reconstructed", "scoring_applied", "prediction_input"):
            self.assertFalse(replay[flag])
        self.replays.append(replay)

    def test_actual_cadillac_escalade_iql_2026_original(self):
        self.replay("CADILLAC", "Escalade IQL")

    def test_actual_ford_mustang_gtd_2026_original(self):
        self.replay("FORD", "Mustang GTD")

    def test_actual_chevrolet_brightdrop_400_is_partial(self):
        self.replay("CHEVROLET", "BrightDrop")

    def test_actual_chevrolet_suburban_hd_400_is_partial(self):
        self.replay("CHEVROLET", "Suburban HD")

    def test_one_shared_reservation_per_actual_request_and_unchanged_history(self):
        old = {r["request_id"]: r["row_sha256"] for r in self.before["requests"]}
        post = {r["request_id"]: r["row_sha256"] for r in self.after["requests"]}
        self.assertEqual(11, len(old))
        self.assertEqual(15, len(post))
        self.assertTrue(all(post.get(key) == sha for key, sha in old.items()))
        self.assertTrue(self.after["all_prior_scope_review_rows_unchanged"])
        new = [r for r in self.after["requests"] if r["request_id"] not in old]
        self.assertEqual(4, len(new))
        self.assertEqual({"nhtsa"}, {r["source"] for r in new})
        self.assertEqual(3, len({r["subject_id"] for r in new}))
        self.assertEqual({"RETURNED": 2, "FAILED": 2}, {state: sum(r["outcome"] == state for r in new) for state in ("RETURNED", "FAILED")})
        self.assertTrue(all(r["attempted_at"].startswith("2026-10-15") for r in new))
        self.assertEqual(4, self.after["remaining_daily_requests"])
        self.assertEqual(1, self.after["remaining_daily_make_slots"])
        self.assertEqual([], self.after["leases_without_tokens"])
        for path, sha in self.plan["source_code_pins"].items():
            self.assertEqual(sha, digest((ROOT / path).read_bytes()), path)

    def test_physical_known_at_cutoff_excludes_all_four_new_reviews(self):
        first = min(datetime.fromisoformat(r["known_at"]) for r in self.run["reviews"])
        with patch("socket.create_connection", side_effect=AssertionError("Report cannot fetch")):
            report = read_nhtsa_scope_reviews(SimpleNamespace(db_path=DB), as_of=first-timedelta(microseconds=1), limit=100)
        self.assertEqual("RECORDED_REVIEWS", report["status"])
        self.assertTrue(report["read_only"])
        old = {r["review_id"] for r in self.before["reviews"]}
        self.assertEqual(old, {r["review_id"] for r in report["reviews"]})
        self.assertEqual(8, len(report["reviews"]))
        self.assertFalse(report["issuer_wide_absence_verified"])
        self.assertEqual(0, report["live_complete_negative_model_years"])
        save("readonly_before_new_reviews.json", report)


if __name__ == "__main__":
    start = datetime.now(timezone.utc).isoformat()
    clock = time.perf_counter()
    output = io.StringIO()
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ActualCaptureReplayTests))
    log = output.getvalue()
    with (FOLDER / "offline_tests.log").open("xb") as stream:
        stream.write(log.encode())
    save("offline_tests.json", {"started_at": start, "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": time.perf_counter()-clock, "discovered": result.testsRun, "executed": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors), "skips": len(result.skipped),
        "expected_failures": len(result.expectedFailures), "unexpected_successes": len(result.unexpectedSuccesses),
        "plain_ok": result.wasSuccessful(), "new_http": 0, "new_db_rows": 0,
        "log_sha256": digest(log.encode()), "verifier_sha256": digest(Path(__file__).read_bytes()),
        "runtime_changed": False})
    save("offline_replays.json", {"physical_recorded_at": datetime.now(timezone.utc).isoformat(),
        "capture_origin": "ARCHIVED_RESPONSE_REPLAY", "live_acceptance": False, "reviews": ActualCaptureReplayTests.replays})
    print(log)
    raise SystemExit(0 if result.wasSuccessful() else 1)
