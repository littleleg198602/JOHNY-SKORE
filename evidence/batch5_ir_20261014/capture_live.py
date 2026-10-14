"""One explicit shared-ledger capture; refuses a new database/quota reset.

Only original bodies released by the collector's whole-body gate are copied.
The retained authority body, when gated, is represented only by its hash and
safe clocks/URL. No alternative encoding or cleaned body is written.
"""
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import shutil
import sqlite3

from market_checker_app.services.ir_primary_scout_service import IRPrimaryScoutService
from market_checker_app.storage.scout_store import ScoutStore


def capture(db_path, evidence_root):
    db_path, evidence_root = Path(db_path), Path(evidence_root)
    if not db_path.is_file() or (evidence_root / "runtime_report.json").exists():
        raise ValueError("Requires an existing shared DB and no prior evidence run")
    clock = datetime.now(timezone.utc)
    if clock < datetime(2026, 10, 14, tzinfo=timezone.utc):
        raise ValueError("Prospective RSS registration must not be backdated")
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as conn:
        before = conn.execute("SELECT COUNT(*) FROM scout_public_requests WHERE source='rss'").fetchone()[0]
    report = IRPrimaryScoutService(ScoutStore(db_path), max_requests=6).run(
        "MSFT", feed_url="https://news.microsoft.com/source/tag/press-releases/feed/",
        authority_url="https://www.microsoft.com/en-us/investor/default",
        publisher_hosts=("news.microsoft.com", "www.microsoft.com"),
        original_url="https://news.microsoft.com/source/2026/10/07/microsoft-announces-quarterly-earnings-release-date-69/",
        requested_as_of=clock,
    )
    evidence_root.mkdir(parents=True, exist_ok=True)
    captures = evidence_root / "captures"
    captures.mkdir(exist_ok=False)
    archive = Path(report["archive_directory"])
    for receipt in report["capture_receipts"]:
        shutil.copyfile(archive / f"{receipt['sequence']:02d}_receipt.json",
                        captures / f"{receipt['sequence']:02d}_receipt.json")
        if receipt["retention"] == "ORIGINAL_BYTES_ARCHIVED":
            shutil.copyfile(archive / receipt["raw_path"], captures / receipt["raw_path"])
    shutil.copyfile(archive / "run.json", evidence_root / "runtime_report.json")
    # Lease/run tokens are deliberately excluded from the public ledger proof.
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as conn:
        rows = conn.execute("""SELECT source, subject_id, identity_key, attempted_at, outcome
            FROM scout_public_requests WHERE source='rss' ORDER BY rowid""").fetchall()
        active = conn.execute("SELECT COUNT(*) FROM scout_provider_leases WHERE source='rss'").fetchone()[0]
        cooldown = conn.execute("SELECT retry_at, reason FROM scout_provider_cooldowns WHERE source='rss'").fetchall()
    ledger = {"actual_existing_shared_db": True, "prior_rss_request_count": before,
              "after_rss_request_count": len(rows), "active_rss_leases_after_run": active,
              "cooldowns_after_run": cooldown, "prospective_registration": True,
              "prior_october_13_requests_not_reconstructed": True,
              "requests": [dict(zip(("source", "subject_id", "identity_key", "attempted_at", "outcome"), row))
                           for row in rows]}
    with (evidence_root / "ledger_proof.json").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": report["status"], "stop_reason": report.get("stop_reason"),
                      "actual_http_requests": report["attempted_requests"],
                      "captures": [{"role": row["role"], "status": row.get("status"),
                                    "retention": row["retention"], "sha256": row.get("sha256")}
                                   for row in report["capture_receipts"]]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    capture(args.db, Path(__file__).resolve().parent)
