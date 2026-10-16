"""Reproduce redirect I/O counts offline; read the existing ledger only.

Synthetic envelopes are mechanism diagnostics, never publisher/live acceptance.
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
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.request import Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.test_batch5_nhtsa_redirect_guard import (
    EMPTY, FORD, NOW, OfflineHTTPS, nhtsa, offline_transport, saved_rows,
)
from market_checker_app.storage.scout_store import ScoutStore


def existing_ledger_snapshot(path: Path) -> dict:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        rows = connection.execute("""SELECT subject_id, identity_key, attempted_at, outcome
            FROM scout_public_requests WHERE source='nhtsa' ORDER BY attempted_at, request_id""").fetchall()
    return {"requests": len(rows), "make_subjects": len({row[0] for row in rows}),
            "charged_utc_days": sorted({row[2][:10] for row in rows}),
            "request_history_sha256": hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    if not args.db.is_file():
        raise FileNotFoundError("Existing canonical ledger is required, never created")
    before = existing_ledger_snapshot(args.db)
    url = nhtsa.recall_url(FORD["make"], FORD["model"], FORD["model_year"])
    cases = []
    for status in (302, 307):
        def response_for(requested, number):
            return (status, url, EMPTY) if number == 1 else (200, None, EMPTY)

        # Emulate the superseded default policy using real urllib processing,
        # while replacing socket I/O entirely. This is not a source request.
        old_transport = OfflineHTTPS(response_for)
        with patch("socket.create_connection", side_effect=AssertionError("Actual network forbidden")):
            with build_opener(old_transport).open(Request(url), timeout=20) as response:
                baseline_status = response.getcode()
        assert baseline_status == 200 and old_transport.requests == [url, url]
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "synthetic.db")
            with offline_transport(response_for) as guarded_transport:
                service = nhtsa.NhtsaRecallScoutService(store, client=nhtsa.NhtsaRecallClient(), models=[FORD])
                guarded = service.run(as_of=NOW)
                stored = saved_rows(store)
                retry = nhtsa.NhtsaRecallScoutService(ScoutStore(store.db_path),
                    client=nhtsa.NhtsaRecallClient(), models=[FORD]).run(as_of=NOW)
                assert guarded_transport.requests == [url]
                assert stored == ([], [], ["FAILED"]) and stored == saved_rows(store)
                assert guarded["attempted_requests"] == guarded["daily_requests"] == 1
                assert retry["status"] == "WAIT_QUOTA" and retry["attempted_requests"] == 0
                cases.append({"redirect_status": status, "response_origin": "SYNTHETIC_TEST",
                    "baseline_default_policy_transport_operations": len(old_transport.requests),
                    "baseline_final_http_status": baseline_status,
                    "baseline_is_mechanism_emulation_only": True,
                    "guarded_transport_operations": len(guarded_transport.requests),
                    "guarded_result": guarded, "same_day_retry": retry,
                    "guarded_findings": len(stored[0]), "guarded_complete_checks": len(stored[1]),
                    "guarded_reservation_outcomes": stored[2],
                    "publisher_acceptance_evidence": False})
    after = existing_ledger_snapshot(args.db)
    assert before == after and (before["requests"], before["make_subjects"]) == (8, 3)
    proof = {"schema_version": 1, "kind": "OFFLINE_URLLIB_REDIRECT_RESERVATION_PROBE",
        "recorded_at": datetime.now(timezone.utc).isoformat(), "actual_http_requests": 0,
        "actual_socket_connections": 0, "canonical_store_opened_read_only": True,
        "canonical_nhtsa_state_before": before, "canonical_nhtsa_state_after": after,
        "canonical_nhtsa_state_unchanged": True, "cases": cases,
        "genuine_live_complete_negative_verified": False, "scoring_applied": False,
        "specialist_done_promoted": False}
    Path(__file__).with_name("offline_probe.json").write_text(
        json.dumps(proof, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"actual_http_requests": 0, "cases": len(cases),
        "guarded_transport_operations_per_case": [case["guarded_transport_operations"] for case in cases],
        "canonical_nhtsa_requests_unchanged": after["requests"]}))


if __name__ == "__main__":
    main()
