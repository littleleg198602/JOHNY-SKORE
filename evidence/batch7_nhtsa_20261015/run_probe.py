"""One authorized bounded source run; never rerun or replace its retained DB."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from market_checker_app.services.nhtsa_negative_review_service import (
    NhtsaNegativeReviewService, NhtsaRawRecallClient, documented_model_scope,
)
from market_checker_app.storage.scout_store import ScoutStore

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / "evidence/batch7_nhtsa_20261015"
DB = Path("/workspace/scratch/334b3a149083/JOHNY-batch4-integration-20261013/outputs/market_checker_history.db")
PARENT_PROOF = Path("/workspace/scratch/334b3a149083/JOHNY-batch7-integration-20261015/evidence/batch7_coordinator_20261015/retained_quota_before_new_utc_day.json")
DAY = "2026-10-15"


def sha(body):
    return hashlib.sha256(body).hexdigest()


def save(name, data):
    with (FOLDER / name).open("xb") as stream:
        stream.write((json.dumps(data, indent=2, sort_keys=True) + "\n").encode())


def snapshot():
    if not DB.is_file():
        raise RuntimeError("Original retained quota database unavailable; no new store permitted")
    with closing(sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"scout_public_requests", "scout_provider_leases", "scout_provider_cooldowns", "scout_nhtsa_scope_reviews"} <= tables:
            raise RuntimeError("Retained quota/review schema missing; do not initialize/reconstruct history")
        rows = [dict(r) for r in db.execute("SELECT * FROM scout_public_requests ORDER BY request_id")]
        safe = [{"request_id": r["request_id"], "source": r["source"], "subject_id": r["subject_id"],
                 "identity_key": r["identity_key"], "attempted_at": r["attempted_at"], "outcome": r["outcome"],
                 "row_sha256": sha(json.dumps(r, sort_keys=True, separators=(",", ":")).encode())} for r in rows]
        leases = [{"source": r["source"], "lease_until": r["lease_until"]}
                  for r in db.execute("SELECT source,lease_until FROM scout_provider_leases")]
        reviews = [{"review_id": r["review_id"], "known_at": r["known_at"],
                    "row_sha256": sha(json.dumps(dict(r), sort_keys=True, separators=(",", ":")).encode())}
                   for r in db.execute("SELECT * FROM scout_nhtsa_scope_reviews ORDER BY review_id")]
    today = [r for r in safe if r["source"] == "nhtsa" and r["attempted_at"].startswith(DAY)]
    return {"recorded_at": datetime.now(timezone.utc).isoformat(), "read_only_query": True,
            "original_database_path": str(DB), "requests": safe, "leases_without_tokens": leases,
            "reviews": reviews, "today_nhtsa_requests": len(today),
            "today_nhtsa_make_subjects": len({r["subject_id"] for r in today}),
            "db_sha256_at_snapshot": sha(DB.read_bytes()), "db_created_copied_reconstructed_or_reset": False}


def run():
    now = datetime.now(timezone.utc)
    if now.date().isoformat() != DAY or (FOLDER / "run_started.json").exists():
        raise RuntimeError("One-shot actual UTC15 probe already started or day changed; no retry")
    plan = json.loads((FOLDER / "precommitted_plan.json").read_text())
    if (plan["maximum_requests"] != 4 or plan["maximum_source_subjects"] != 3
            or datetime.fromisoformat(plan["precommitted_at"]) > now):
        raise RuntimeError("Precommitted bounded plan is invalid")
    before = snapshot()
    parent = json.loads(PARENT_PROOF.read_text())
    row_hashes = {r["request_id"]: r["row_sha256"] for r in before["requests"]}
    if any(row_hashes.get(r["request_id"]) != r["row_sha256"] for r in parent["prior_rows"]):
        save("blocked_prior_row_mismatch.json", {"status": "BLOCKED", "new_http": 0,
                                               "reason": "PARENT_PRIOR_ROW_HASH_MISMATCH"})
        raise RuntimeError("Prior row mismatch; no source I/O permitted")
    if before["today_nhtsa_requests"] or before["today_nhtsa_make_subjects"]:
        save("blocked_new_day_concurrent_usage.json", {"status": "BLOCKED", "new_http": 0,
                                                       "reason": "PLAN_EXPECTED_UNCONSUMED_NEW_UTC_DAY"})
        raise RuntimeError("New-day NHTSA usage changed; do not overwrite or retry")
    before["parent_beforeproof"] = {"path": "evidence/batch7_coordinator_20261015/retained_quota_before_new_utc_day.json",
        "sha256": sha(PARENT_PROOF.read_bytes()), "prior_rows_verified": len(parent["prior_rows"])}
    save("retained_ledger_before.json", before)
    scopes = []
    for entry in plan["scopes"]:
        body = (ROOT / entry["catalog_path"]).read_bytes()
        meta = json.loads((ROOT / entry["catalog_metadata_path"]).read_text())
        if sha(body) != entry["catalog_sha256"] or sha((ROOT / entry["catalog_metadata_path"]).read_bytes()) != entry["catalog_metadata_sha256"]:
            raise RuntimeError("Exact previously captured catalog pin changed")
        scope = documented_model_scope(make=entry["make"], model=entry["model"], model_year=entry["model_year"],
                                       catalog_body=body, catalog_metadata=meta)
        if scope.record() != entry["documented_scope"]:
            raise RuntimeError("Exact independent model/year scope changed")
        scopes.append(scope)
    save("run_started.json", {"started_at": datetime.now(timezone.utc).isoformat(),
                              "plan_sha256": sha((FOLDER / "precommitted_plan.json").read_bytes()),
                              "actual_store": str(DB), "maximum_requests": 4, "maximum_source_subjects": 3})
    service = NhtsaNegativeReviewService(ScoutStore(DB), client=NhtsaRawRecallClient(), scopes=scopes,
        archive_root=FOLDER / "captures", max_requests=4, max_run_seconds=120)
    result = service.run()
    save("live_run.json", result)
    after = snapshot()
    after_hashes = {r["request_id"]: r["row_sha256"] for r in after["requests"]}
    review_hashes = {r["review_id"]: r["row_sha256"] for r in after["reviews"]}
    prior_unchanged = all(after_hashes.get(r["request_id"]) == r["row_sha256"] for r in before["requests"])
    prior_reviews_unchanged = all(review_hashes.get(r["review_id"]) == r["row_sha256"] for r in before["reviews"])
    after.update(all_prior_public_request_rows_unchanged=prior_unchanged,
                 all_prior_scope_review_rows_unchanged=prior_reviews_unchanged,
                 remaining_daily_requests=max(0, 8-after["today_nhtsa_requests"]),
                 remaining_daily_make_slots=max(0, 4-after["today_nhtsa_make_subjects"]))
    save("retained_ledger_after.json", after)
    save("readonly_report.json", service.report(as_of=datetime.now(timezone.utc), limit=100))
    if not prior_unchanged or not prior_reviews_unchanged:
        raise RuntimeError("Prior durable history changed; stop and preserve the diagnostic")
    if after["today_nhtsa_requests"] > 4 or after["today_nhtsa_make_subjects"] > 3:
        raise RuntimeError("Observed daily usage exceeds this plan; retain evidence and stop")
    print(json.dumps({"status": result["status"], "attempted_requests": result["attempted_requests"],
        "reviews": [{"make": r["scope"]["make"], "model": r["scope"]["model"], "status": r["status"],
                     "http_status": r.get("http_status"), "reason": r["reason"]} for r in result["reviews"]],
        "prior_rows_unchanged": prior_unchanged, "prior_reviews_unchanged": prior_reviews_unchanged,
        "remaining_daily_requests": after["remaining_daily_requests"], "remaining_make_slots": after["remaining_daily_make_slots"]}, indent=2))


if __name__ == "__main__":
    run()
