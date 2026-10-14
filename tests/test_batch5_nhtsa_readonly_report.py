"""Read-only UI history regressions; pinned source replay performs zero HTTP."""
from __future__ import annotations

from contextlib import ExitStack, closing, contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from market_checker_app.services.nhtsa_negative_review_service import read_nhtsa_scope_reviews
from market_checker_app.storage.scout_store import ScoutStore


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/batch5_regulatory_20261014"
PINNED = json.loads((EVIDENCE / "live_run.json").read_text(encoding="utf-8"))["result"]["reviews"]
AFTER = datetime(2026, 10, 14, 6, tzinfo=timezone.utc)


def existing_store(path: Path) -> ScoutStore:
    # A report needs only the existing handle's path, never ScoutStore.__init__.
    store = object.__new__(ScoutStore)
    store.db_path = path
    return store


def replay_database(path: Path, rows: list[dict], *, physical_overrides: dict | None = None) -> ScoutStore:
    overrides = physical_overrides or {}
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript("""
            CREATE TABLE scout_nhtsa_scope_reviews (
                review_id TEXT PRIMARY KEY, scope_key TEXT NOT NULL,
                known_at TEXT NOT NULL, review_json TEXT NOT NULL);
            CREATE TABLE quota_sentinel (daily_requests INTEGER NOT NULL);
            INSERT INTO quota_sentinel VALUES (8);
            """)
        for row in rows:
            connection.execute("INSERT INTO scout_nhtsa_scope_reviews VALUES (?, ?, ?, ?)",
                (row["review_id"], "PINNED_REPLAY_ONLY", overrides.get(row["review_id"], row["known_at"]),
                 json.dumps(row, sort_keys=True)))
        connection.commit()
    return existing_store(path)


@contextmanager
def forbid_runtime_mutations_and_http():
    targets = [
        "market_checker_app.storage.scout_store.ScoutStore.__init__",
        "market_checker_app.storage.scout_store.ScoutStore.ensure_schema",
        "market_checker_app.storage.scout_store.ScoutStore._connect",
        "market_checker_app.services.nhtsa_negative_review_service.NhtsaNegativeReviewService.__init__",
        "market_checker_app.services.nhtsa_negative_review_service.NhtsaRawRecallClient.__init__",
        "market_checker_app.services.nhtsa_negative_review_service.build_opener",
        "market_checker_app.services.nhtsa_recall_scout_service.urlopen",
        "market_checker_app.services.public_source_budget.PublicSourceBudget.__init__",
    ]
    with ExitStack() as stack:
        for target in targets:
            stack.enter_context(patch(target, side_effect=AssertionError("Read-only report touched runtime I/O")))
        yield


class Batch5NhtsaReadOnlyReportTests(unittest.TestCase):
    def test_missing_database_and_empty_database_create_no_schema(self):
        with TemporaryDirectory() as directory, forbid_runtime_mutations_and_http():
            path = Path(directory) / "missing" / "history.db"
            report = read_nhtsa_scope_reviews(existing_store(path), as_of=AFTER)
            self.assertEqual(("NO_RECORDED_REVIEWS", "DATABASE_NOT_FOUND"),
                             (report["status"], report["reason"]))
            self.assertFalse(path.parent.exists())
            path = Path(directory) / "empty.db"
            with closing(sqlite3.connect(path)):
                pass
            original = path.read_bytes()
            report = read_nhtsa_scope_reviews(existing_store(path), as_of=AFTER)
            self.assertEqual(("NO_RECORDED_REVIEWS", "REVIEW_TABLE_ABSENT"),
                             (report["status"], report["reason"]))
            self.assertEqual([], report["reviews"])
            self.assertEqual((0, 0), (report["complete_negative_model_years"],
                                    report["live_complete_negative_model_years"]))
            self.assertEqual(original, path.read_bytes())
            self.assertEqual(["empty.db"], sorted(p.name for p in Path(directory).iterdir()))

    def test_existing_scout_store_without_review_table_is_read_only(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "existing.db")
            original = store.db_path.read_bytes()
            with forbid_runtime_mutations_and_http():
                report = read_nhtsa_scope_reviews(store, as_of=AFTER)
            self.assertEqual("NO_RECORDED_REVIEWS", report["status"])
            self.assertEqual("REVIEW_TABLE_ABSENT", report["reason"])
            self.assertEqual(original, store.db_path.read_bytes())

    def test_pinned_eight_replay_preserves_exact_fields_bytes_and_false_claims(self):
        with TemporaryDirectory() as directory:
            store = replay_database(Path(directory) / "replay.db", PINNED)
            original = store.db_path.read_bytes()
            files = sorted(p.name for p in Path(directory).iterdir())
            with forbid_runtime_mutations_and_http():
                report = read_nhtsa_scope_reviews(store, as_of=AFTER)
            self.assertEqual("RECORDED_REVIEWS", report["status"])
            self.assertEqual("RETURNED_REVIEW_WINDOW", report["counts_scope"])
            self.assertTrue(report["read_only"])
            expected = sorted(PINNED, key=lambda row: (-datetime.fromisoformat(row["known_at"]).timestamp(),
                                                      row["review_id"]))
            self.assertEqual(expected, report["reviews"])
            self.assertEqual(4, sum(row["status"] == "POSITIVE_EXACT_MODEL_YEAR" for row in report["reviews"]))
            self.assertEqual(4, sum(row["status"] == "PARTIAL" for row in report["reviews"]))
            self.assertEqual((0, 0), (report["complete_negative_model_years"],
                                    report["live_complete_negative_model_years"]))
            for row in report["reviews"]:
                body = (EVIDENCE / "captures" / row["archive_relative_path"]).read_bytes()
                self.assertEqual(row["raw_response_sha256"], hashlib.sha256(body).hexdigest())
                self.assertEqual(row["raw_response_bytes"], len(body))
                for flag in ("complete_negative", "live_complete_negative_verified",
                             "issuer_wide_absence_verified", "issuer_attribution_verified",
                             "manufacturer_ownership_inferred", "historical_availability_reconstructed",
                             "scoring_applied", "prediction_input"):
                    self.assertIs(row[flag], False)
                if row["http_status"] == 400:
                    self.assertEqual("HTTP_400_IS_NOT_COMPLETE_NEGATIVE", row["reason"])
            self.assertEqual(original, store.db_path.read_bytes())
            self.assertEqual(files, sorted(p.name for p in Path(directory).iterdir()))
            with closing(sqlite3.connect(store.db_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
                self.assertEqual(8, connection.execute("SELECT daily_requests FROM quota_sentinel").fetchone()[0])

    def test_cutoff_is_inclusive_and_no_known_reviews_is_explicit(self):
        with TemporaryDirectory() as directory, forbid_runtime_mutations_and_http():
            store = replay_database(Path(directory) / "replay.db", PINNED)
            ordered = sorted(PINNED, key=lambda row: row["known_at"])
            cutoff = datetime.fromisoformat(ordered[3]["known_at"])
            report = read_nhtsa_scope_reviews(store, as_of=cutoff)
            self.assertEqual([row["review_id"] for row in reversed(ordered[:4])],
                             [row["review_id"] for row in report["reviews"]])
            earliest = datetime.fromisoformat(ordered[0]["known_at"]) - timedelta(microseconds=1)
            report = read_nhtsa_scope_reviews(store, as_of=earliest)
            self.assertEqual(("NO_RECORDED_REVIEWS", "NO_REVIEWS_KNOWN_AT_CUTOFF"),
                             (report["status"], report["reason"]))
            self.assertEqual([], report["reviews"])

    def test_physical_column_prevents_backdated_json_from_bypassing_cutoff(self):
        with TemporaryDirectory() as directory, forbid_runtime_mutations_and_http():
            row = PINNED[0]
            future = AFTER + timedelta(days=1)
            store = replay_database(Path(directory) / "replay.db", [row],
                                    physical_overrides={row["review_id"]: future.isoformat()})
            self.assertLess(datetime.fromisoformat(row["known_at"]), AFTER)
            self.assertEqual([], read_nhtsa_scope_reviews(store, as_of=AFTER)["reviews"])
            self.assertEqual([row], read_nhtsa_scope_reviews(store, as_of=future)["reviews"])

    def test_limit_one_to_one_hundred_and_aware_utc_clock(self):
        with TemporaryDirectory() as directory, forbid_runtime_mutations_and_http():
            store = replay_database(Path(directory) / "replay.db", PINNED)
            self.assertEqual(1, len(read_nhtsa_scope_reviews(store, as_of=AFTER, limit=1)["reviews"]))
            self.assertEqual(8, len(read_nhtsa_scope_reviews(store, as_of=AFTER, limit=100)["reviews"]))
            for value in (0, 101, True, 1.5, "8", None):
                with self.subTest(limit=value), self.assertRaises(ValueError):
                    read_nhtsa_scope_reviews(store, as_of=AFTER, limit=value)
            local = AFTER.astimezone(timezone(timedelta(hours=2)))
            self.assertEqual(read_nhtsa_scope_reviews(store, as_of=AFTER),
                             read_nhtsa_scope_reviews(store, as_of=local))
            with self.assertRaises(ValueError):
                read_nhtsa_scope_reviews(store, as_of=AFTER.replace(tzinfo=None))

    def test_connection_enforces_mode_ro_query_only_and_no_ddl(self):
        with TemporaryDirectory() as directory, forbid_runtime_mutations_and_http():
            store = replay_database(Path(directory) / "replay.db", PINNED)
            original_connect = sqlite3.connect
            connections, statements = [], []

            def tracked_connect(*args, **kwargs):
                self.assertTrue(args[0].endswith("?mode=ro"))
                self.assertIs(kwargs.get("uri"), True)
                connection = original_connect(*args, **kwargs)
                connection.set_trace_callback(statements.append)
                connections.append(connection)
                return connection

            with patch("market_checker_app.services.nhtsa_negative_review_service.sqlite3.connect",
                       side_effect=tracked_connect):
                read_nhtsa_scope_reviews(store, as_of=AFTER)
            self.assertEqual(1, len(connections))
            self.assertEqual("PRAGMA query_only=ON", statements[0])
            self.assertFalse(any(sql.lstrip().upper().startswith(("CREATE", "INSERT", "UPDATE", "DELETE"))
                                 for sql in statements))
            with self.assertRaises(sqlite3.ProgrammingError):
                connections[0].execute("SELECT 1")  # Handles are closed after the snapshot.

    def test_offline_negative_flags_are_preserved_without_live_promotion(self):
        # Synthetic stored history tests the reporting contract, never LIVE evidence.
        row = deepcopy(PINNED[0])
        row.update(status="COMPLETE_NEGATIVE_MODEL_YEAR", capture_origin="SYNTHETIC_TEST",
                   complete_negative=True, live_complete_negative_verified=False,
                   review_id="synthetic-report-contract-only")
        with TemporaryDirectory() as directory, forbid_runtime_mutations_and_http():
            store = replay_database(Path(directory) / "replay.db", [row])
            report = read_nhtsa_scope_reviews(store, as_of=AFTER)
            self.assertEqual([row], report["reviews"])
            self.assertEqual((1, 0), (report["complete_negative_model_years"],
                                    report["live_complete_negative_model_years"]))
            self.assertFalse(report["issuer_wide_absence_verified"])
            self.assertFalse(report["prediction_input"])

    def test_corrupt_database_and_invalid_review_json_are_explicit_read_errors(self):
        with TemporaryDirectory() as directory, forbid_runtime_mutations_and_http():
            path = Path(directory) / "corrupt.db"
            path.write_bytes(b"not sqlite")
            report = read_nhtsa_scope_reviews(existing_store(path), as_of=AFTER)
            self.assertEqual(("REVIEW_READ_ERROR", "DATABASE_READ_ERROR"),
                             (report["status"], report["reason"]))
            self.assertEqual(b"not sqlite", path.read_bytes())
            store = replay_database(Path(directory) / "invalid.db", PINNED[:1])
            for bad_json in ("{", "[]"):
                with self.subTest(value=bad_json):
                    with closing(sqlite3.connect(store.db_path)) as connection:
                        connection.execute("UPDATE scout_nhtsa_scope_reviews SET review_json=?", (bad_json,))
                        connection.commit()
                    original = store.db_path.read_bytes()
                    report = read_nhtsa_scope_reviews(store, as_of=AFTER)
                    self.assertEqual(("REVIEW_READ_ERROR", "INVALID_REVIEW_JSON"),
                                     (report["status"], report["reason"]))
                    self.assertEqual([], report["reviews"])
                    self.assertEqual(original, store.db_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
