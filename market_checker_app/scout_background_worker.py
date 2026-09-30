"""Continue the queued SEC scan outside Streamlit's request lifecycle."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from market_checker_app.services.sec_scout_service import SecScoutService
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.utils.ticker_universe import load_canonical_ticker_records


def start_sec_background_scan(
    store: ScoutStore, *, db_path: Path, user_agent: str,
) -> str:
    """Start one detached worker; progress and failures are persisted in SQLite."""
    if not user_agent or "@" not in user_agent:
        return "WAIT_ACCESS"
    run_id = store.begin_background_worker("sec", as_of=datetime.now(timezone.utc))
    if run_id is None:
        return "BUSY"
    command = [sys.executable, "-m", "market_checker_app.scout_background_worker",
               "--db-path", str(db_path.resolve()), "--run-id", run_id]
    env = os.environ.copy()
    env["JOHNY_SKORE_SEC_USER_AGENT"] = user_agent
    kwargs: dict[str, object] = {"cwd": str(Path(__file__).resolve().parents[1]),
                                 "env": env, "stdin": subprocess.DEVNULL,
                                 "stdout": subprocess.DEVNULL,
                                 "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(command, **kwargs)
    except (OSError, ValueError) as exc:
        store.update_background_worker(
            "sec", run_id, as_of=datetime.now(timezone.utc), status="ERROR",
            processed=0, new_findings=0, failed=0,
            message=f"Spuštění selhalo: {type(exc).__name__}: {exc}",
        )
        return "ERROR"
    return "STARTED"


def drain_sec_queue(
    store: ScoutStore, *, run_id: str, scout: SecScoutService,
    tickers: list[str], batch_size: int = 25, busy_wait_seconds: int = 10,
) -> dict[str, object]:
    """Schedule once, then consume batches until no job is currently due.

    Deferred failures and provider cooldown remain in SQLite for the next
    scheduled run. Never re-schedule DONE rows between batches.
    """
    processed = new_findings = failed = 0
    try:
        scheduled = scout.schedule(tickers, as_of=datetime.now(timezone.utc))
        busy_since: float | None = None
        while True:
            result = scout.run_batch(limit=batch_size)
            status = str(result["status"])
            processed += int(result.get("processed", 0))
            new_findings += int(result.get("new_findings", 0))
            failed += int(result.get("failed", 0))
            if status == "BUSY":
                if busy_since is None:
                    busy_since = time.monotonic()
                if time.monotonic() - busy_since >= 1800:
                    final, message = "PAUSED", "Jiný běh drží SEC déle než 30 minut"
                    break
                store.update_background_worker(
                    "sec", run_id, as_of=datetime.now(timezone.utc), status="RUNNING",
                    processed=processed, new_findings=new_findings, failed=failed,
                    message="Čekám na probíhající SEC běh",
                )
                time.sleep(busy_wait_seconds)
                continue
            busy_since = None
            if status in {"WAIT_ACCESS", "ACCESS_BLOCKED", "RATE_LIMITED", "LEASE_LOST"}:
                final = "PAUSED"
                message = f"{status}; další pokus: {result.get('retry_at', 'denní plánovač')}"
                break
            if int(result.get("processed", 0)) == 0:
                next_due = store.next_job_due("sec")
                final = "PAUSED" if next_due else "COMPLETED"
                message = f"Odložené úlohy do {next_due}" if next_due else "Fronta SEC dokončena"
                break
            if not store.update_background_worker(
                "sec", run_id, as_of=datetime.now(timezone.utc), status="RUNNING",
                processed=processed, new_findings=new_findings, failed=failed,
                message="Pokračuji další dávkou SEC",
            ):
                return {"status": "LEASE_LOST", "processed": processed}
        store.update_background_worker(
            "sec", run_id, as_of=datetime.now(timezone.utc), status=final,
            processed=processed, new_findings=new_findings, failed=failed,
            message=message,
        )
        return {"status": final, "scheduled_subjects": scheduled,
                "processed": processed, "new_findings": new_findings,
                "failed": failed, "message": message}
    except Exception as exc:
        store.update_background_worker(
            "sec", run_id, as_of=datetime.now(timezone.utc), status="ERROR",
            processed=processed, new_findings=new_findings, failed=failed,
            message=f"{type(exc).__name__}: {exc}",
        )
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Dokončit připravenou frontu SEC na pozadí")
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    store = ScoutStore(args.db_path)
    worker = store.background_worker_status("sec")
    if not worker or worker["run_id"] != args.run_id:
        raise SystemExit("SEC worker run ID does not match")
    try:
        records = load_canonical_ticker_records()
    except Exception as exc:
        store.update_background_worker(
            "sec", args.run_id, as_of=datetime.now(timezone.utc), status="ERROR",
            processed=0, new_findings=0, failed=0,
            message=f"{type(exc).__name__}: {exc}",
        )
        raise
    scout = SecScoutService(store, user_agent=os.getenv("JOHNY_SKORE_SEC_USER_AGENT", ""))
    result = drain_sec_queue(store, run_id=args.run_id, scout=scout,
                             tickers=[record["ticker"] for record in records])
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
