"""Run a bounded saved-publisher-payload replay, with no network requests.

From the repository root:
    python -m evidence.batch3_evaluation_captured_payload_replay_20261012

This uses genuine previously captured FDIC payloads. The new SQLite runs and
archive availability use the current physical clock; they do not backfill
historical source runs or imply new live-provider verification.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import time

from market_checker_app.prediction_contract import canonical_hash
from market_checker_app.services.fdic_bank_scout_service import FdicBankScoutService
from market_checker_app.services.specialist_incremental_archive_service import (
    capture_specialist_source_run, load_specialist_source_run_archive,
    replay_specialist_source_history,
)
from market_checker_app.storage.scout_store import ScoutStore


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "evidence" / "batch3_evaluation_saved_payload_replay_20261012.json"
ARCHIVE_DIRECTORY = ROOT / "evidence" / "batch3_evaluation_saved_payload_archives_20261012"


def main() -> None:
    original_path = ROOT / "evidence" / "fdic_cof_ewbc_live_20261001.json"
    original_bytes = original_path.read_bytes()
    original = json.loads(original_bytes)
    selected = [original["cases"][key] for key in ("fdic_positive_COF", "fdic_positive_EWBC")]
    payloads = {case["cert"]: deepcopy(case["payload"]) for case in selected}

    class CapturedPayloadClient:
        calls = 0

        def financials(self, cert: int) -> dict:
            self.calls += 1
            return deepcopy(payloads[cert])

    client = CapturedPayloadClient()
    snapshots, run_results = [], []
    total_started = time.perf_counter()
    with tempfile.TemporaryDirectory() as temporary:
        store = ScoutStore(Path(temporary) / "current_payload_replay.db")
        replay_directory = Path(temporary) / "current_run_archives"
        service = FdicBankScoutService(store, client=client,
            identities=[deepcopy(case["identity"]) for case in selected], max_banks=1)
        for _ in range(3):
            started = datetime.now(timezone.utc)
            logical = started
            summary = service.run(as_of=logical, universe={"COF", "EWBC"})
            finished = datetime.now(timezone.utc)
            store.record_source_run("fdic", as_of=finished, summary=summary)
            before = hashlib.sha256(store.db_path.read_bytes()).hexdigest()
            capture_started = time.perf_counter()
            path = capture_specialist_source_run(store, source="fdic", summary=summary,
                observed_at=finished, started_at=started, provider_as_of=logical,
                directory=replay_directory,
                maximum_observations=8, observations_per_source_ticker=4,
                maximum_checks=8, maximum_attempts=8,
                evidence_origin="CAPTURED_PUBLISHER_PAYLOAD_REPLAY")
            capture_elapsed = time.perf_counter() - capture_started
            item = load_specialist_source_run_archive(path)
            leaf = ARCHIVE_DIRECTORY / "fdic"
            leaf.mkdir(parents=True, exist_ok=True)
            preserved = leaf / path.name
            if preserved.exists() and preserved.read_bytes() != path.read_bytes():
                raise ValueError("Captured-payload evidence must never overwrite another archive")
            shutil.copyfile(path, preserved)
            snapshots.append(item)
            run_results.append({"observed_at": item["observed_at"], "available_at": item["available_at"],
                "status": summary["status"], "summary": summary, "attempt_semantics": item["attempt_semantics"],
                "source_archive_hash": item["source_archive"]["archive_hash"], "snapshot_hash": item["snapshot_hash"],
                "artifact": str(preserved.relative_to(ROOT)),
                "file_sha256": hashlib.sha256(preserved.read_bytes()).hexdigest(),
                "serialized_bytes": preserved.stat().st_size,
                "capture_elapsed_seconds": round(capture_elapsed, 6),
                "database_sha256_unchanged_by_capture": before == hashlib.sha256(store.db_path.read_bytes()).hexdigest()})
            store = ScoutStore(store.db_path)
            service.store = store
        current = datetime.now(timezone.utc)
        replay = replay_specialist_source_history(replay_directory, source="fdic", as_of=current,
            maximum_archives=20, maximum_observations=20, maximum_checks=20, maximum_attempts=20)
        historical_cutoff = datetime.fromisoformat(original["finished_at"])
        historical = replay_specialist_source_history(replay_directory, source="fdic", as_of=historical_cutoff,
            maximum_archives=20, maximum_observations=20, maximum_checks=20, maximum_attempts=20)
        assert [row["status"] for row in run_results] == ["PARTIAL", "OK", "NO_DUE_WORK"]
        assert len(replay["source_runs"]) == 3 and len(replay["observations"]) == 4
        assert len(replay["checks"]) == 2 and len(replay["attempts"]) == 2
        assert not historical["source_runs"] and not historical["observations"] and not historical["checks"]
        assert client.calls == 2
        assert all(row["database_sha256_unchanged_by_capture"] for row in run_results)

    negative = original["cases"]["fdic_negative_absent_cert"]
    report = {
        "schema_version": 1, "evidence_type": "current_replay_of_existing_genuine_publisher_payloads",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "network_requests_in_this_check": 0, "saved_payload_client_calls": client.calls,
        "new_live_provider_verification": False, "contemporaneous_historical_run_capture": False,
        "source_captures": [{"artifact": str(original_path.relative_to(ROOT)),
            "artifact_sha256": hashlib.sha256(original_bytes).hexdigest(),
            "case": f"fdic_positive_{case['ticker']}", "ticker": case["ticker"],
            "source_observed_at_in_original_report": case["observed_at"],
            "payload_sha256": canonical_hash(case["payload"]),
            "publisher_payload_report_dates": [row["data"]["REPDTE"] for row in case["payload"]["data"]]} for case in selected],
        "runs": run_results,
        "replay": {"scope": "three captures created in this invocation", "replay_hash": replay["replay_hash"], "as_of": replay["as_of"],
            "source_run_count": len(replay["source_runs"]), "observation_versions": len(replay["observations"]),
            "check_versions": len(replay["checks"]), "request_state_versions": len(replay["attempts"]),
            "canonical_ticker_count": replay["universe"]["ticker_count"],
            "source_ticker_cells": len(replay["source_ticker_history"]), "bounds": replay["bounds"],
            "no_due_attempt_state": snapshots[-1]["attempt_semantics"]["state"],
            "prior_original_report_cutoff": historical["as_of"],
            "archives_replayed_at_original_report_cutoff": len(historical["source_runs"]),
            "archives_excluded_at_original_report_cutoff": historical["input"]["excluded_not_yet_available"]},
        "existing_negative_capture_provenance_only": {
            "case": "fdic_negative_absent_cert", "source_observed_at_in_original_report": negative["observed_at"],
            "payload_sha256": canonical_hash(negative["payload"]), "returned_rows": len(negative["payload"]["data"]),
            "scope": "absent CERT in saved report; no issuer attribution and no fabricated source check or source run"},
        "claims": {"source_specific_pipeline_capture_and_replay_verified": True,
            "actual_existing_publisher_payloads_replayed": True, "actual_historical_check_history_available": False,
            "live_relevant_coverage_verified": False, "out_of_sample_evaluation_verified": False,
            "production_scores_changed": False, "prediction_snapshots_changed": False,
            "windows_runtime_verified": False},
        "elapsed_seconds": round(time.perf_counter() - total_started, 6),
        "limitations": [
            "Available local runtime databases contained no source findings/checks/runs. This evidence uses saved official-payload reports, not recovered historical ScoutStore state.",
            "Original report clocks are provenance only. Replaying saved responses now does not establish that an immutable run archive existed on 2026-10-01.",
            "Only two saved CERT responses and four financial-report rows were replayed. These are subsidiary bank reports, not complete issuer-group or universe coverage.",
            "The original absent-CERT negative response remains unassigned to any ticker. No historical issuer negative check was manufactured.",
            "Deterministic zero/partial-overwrite tests are separate unit-test evidence; these saved-payload runs preserve actual bounded collector results with current clocks.",
            "No labels, return outcomes, new live source acceptance or performance comparison were produced.",
        ],
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(REPORT_PATH.relative_to(ROOT)), "run_statuses": [row["status"] for row in run_results],
        "publisher_payload_rows_replayed": 4, "saved_payload_client_calls": client.calls,
        "network_requests": 0, "historical_archives_replayed": len(historical["source_runs"]),
        "elapsed_seconds": report["elapsed_seconds"]}, sort_keys=True))


if __name__ == "__main__":
    main()
