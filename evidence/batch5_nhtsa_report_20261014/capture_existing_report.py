"""Capture a read-only report from an explicitly supplied existing local DB.

No ScoutStore/service constructor, schema creation, quota reservation or HTTP.
This script is evidence capture, not a source collector or acceptance runner.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from market_checker_app.services.nhtsa_negative_review_service import read_nhtsa_scope_reviews


def readonly_nhtsa_state(path: Path, day: str) -> dict:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        requests, subjects = connection.execute("""SELECT COUNT(*), COUNT(DISTINCT subject_id)
            FROM scout_public_requests WHERE source='nhtsa' AND substr(attempted_at,1,10)=?""",
            (day,)).fetchone()
        outcomes = dict(connection.execute("""SELECT outcome, COUNT(*) FROM scout_public_requests
            WHERE source='nhtsa' AND substr(attempted_at,1,10)=? GROUP BY outcome""", (day,)).fetchall())
        history = connection.execute("""SELECT review_id, known_at, review_json
            FROM scout_nhtsa_scope_reviews ORDER BY review_id""").fetchall()
    return {"charged_utc_day": day, "daily_requests": requests, "daily_make_subjects": subjects,
            "request_outcomes": outcomes, "physical_review_rows": len(history),
            "physical_review_history_sha256": hashlib.sha256(
                json.dumps(history, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    if not args.db.is_file():
        raise FileNotFoundError("Explicit existing task database is required")
    folder = Path(__file__).resolve().parent
    clock = datetime.now(timezone.utc)
    before = readonly_nhtsa_state(args.db, clock.date().isoformat())
    report = read_nhtsa_scope_reviews(SimpleNamespace(db_path=args.db), as_of=clock, limit=8)
    after = readonly_nhtsa_state(args.db, clock.date().isoformat())
    pinned = json.loads((ROOT / "evidence/batch5_regulatory_20261014/live_run.json").read_text())["result"]["reviews"]
    expected = sorted(pinned, key=lambda row: (-datetime.fromisoformat(row["known_at"]).timestamp(), row["review_id"]))
    if before != after or report["reviews"] != expected:
        raise ValueError("Existing NHTSA ledger changed or report differs from the eight pinned reviews")
    if (before["daily_requests"], before["daily_make_subjects"]) != (8, 3):
        raise ValueError("Expected exhausted prospective NHTSA daily ledger was not observed")
    proof = {"schema_version": 1, "kind": "READ_ONLY_EXISTING_NHTSA_SCOPE_REPORT",
        "recorded_at": clock.isoformat(), "existing_database_reused": True,
        "fresh_or_reset_database": False, "http_requests": 0,
        "source_quota_reserved": False, "network_client_initialized": False,
        "constructor_schema_created": False, "rejected_documentation_refetched": False,
        "nhtsa_state_before": before, "nhtsa_state_after": after,
        "nhtsa_state_unchanged": True, "report_equals_actual_pinned_reviews": True,
        "report": report, "out_of_sample_acceptance_verified": False,
        "windows_acceptance_verified": False, "specialist_done_promoted": False}
    (folder / "existing_report.json").write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n",
                                                encoding="utf-8", newline="\n")
    print(json.dumps({"reported_reviews": len(report["reviews"]), "http_requests": 0,
        "daily_requests": before["daily_requests"], "daily_make_subjects": before["daily_make_subjects"],
        "complete_negative_model_years": report["complete_negative_model_years"],
        "live_complete_negative_model_years": report["live_complete_negative_model_years"]}))


if __name__ == "__main__":
    main()
