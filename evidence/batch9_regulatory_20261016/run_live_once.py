"""One bounded normal NHTSA review run on the original retained quota store.

Plan creation is read-only. --run is allowed once, on actual UTC16, only if the
original 15 request rows and 12 review rows still match the original database.
No new/copy/reset/reconstructed quota database and no direct transport bypass.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

from market_checker_app.services.nhtsa_negative_review_service import (
    NhtsaNegativeReviewService, NhtsaRawRecallClient, documented_model_scope,
)
from market_checker_app.storage.scout_store import ScoutStore

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent
DB = Path("/workspace/scratch/334b3a149083/JOHNY-batch4-integration-20261013/outputs/market_checker_history.db")
EXPECTED_DB_SHA = "0a6c669c3b5977dc5e884e313715b7c49dc9d033eef1f7d2af121aa2223b0963"
DAY = "2026-10-16"
SELECTIONS = (("FORD", "F-650"), ("FORD", "F-750"),
              ("CHEVROLET", "7500XD"), ("CHEVROLET", "5500XG"))


def sha(body):
    return hashlib.sha256(body).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def save(name, value):
    with (FOLDER / name).open("xb") as stream:
        stream.write((json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def snapshot():
    if not DB.is_file():
        raise RuntimeError("Original quota database missing; stop without initializing a store")
    with closing(sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        schema = [dict(r) for r in conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]
        tables = {r["name"] for r in schema if r["type"] == "table"}
        required = {"scout_public_requests", "scout_provider_leases", "scout_provider_cooldowns", "scout_nhtsa_scope_reviews"}
        if not required <= tables:
            raise RuntimeError("Original quota/review schema is incomplete; no source request allowed")
        requests = []
        for row in conn.execute("SELECT * FROM scout_public_requests ORDER BY request_id"):
            full = dict(row)
            requests.append({**{key: full[key] for key in ("request_id", "source", "subject_id", "identity_key", "attempted_at", "outcome")},
                             "full_row_sha256": sha(canonical(full))})
        reviews = [{"review_id": row["review_id"], "known_at": row["known_at"],
                    "full_row_sha256": sha(canonical(dict(row)))}
                   for row in conn.execute("SELECT * FROM scout_nhtsa_scope_reviews ORDER BY review_id")]
        leases = [{"source": r["source"], "lease_until": r["lease_until"]}
                  for r in conn.execute("SELECT source,lease_until FROM scout_provider_leases ORDER BY source")]
        cooldowns = [{"source": r["source"], "retry_at": r["retry_at"], "reason": r["reason"]}
                     for r in conn.execute("SELECT * FROM scout_provider_cooldowns ORDER BY source")]
    today = [r for r in requests if r["source"] == "nhtsa" and r["attempted_at"].startswith(DAY)]
    return {"recorded_at": datetime.now(timezone.utc).isoformat(), "mode": "SQLITE_READ_ONLY_QUERY_ONLY",
            "original_database_path": str(DB), "database_sha256_at_snapshot": sha(DB.read_bytes()),
            "schema_sha256": sha(canonical(schema)), "requests": requests, "reviews": reviews,
            "leases_without_tokens": leases, "cooldowns": cooldowns,
            "today_nhtsa_requests": len(today), "today_nhtsa_make_subjects": len({r["subject_id"] for r in today}),
            "database_created_copied_reconstructed_or_reset": False}


def documented_selections():
    rows = []
    for make, model in SELECTIONS:
        prefix = "evidence/batch4_regulatory_20261013/raw/nhtsa_models_" + make.lower() + "_2026"
        body_path, metadata_path = prefix + ".json", prefix + ".metadata.json"
        body = (ROOT / body_path).read_bytes()
        metadata_raw = (ROOT / metadata_path).read_bytes()
        scope = documented_model_scope(make=make, model=model, model_year=2026,
            catalog_body=body, catalog_metadata=json.loads(metadata_raw))
        rows.append({"make": make, "model": model, "model_year": 2026,
            "catalog_path": body_path, "catalog_sha256": sha(body), "catalog_bytes": len(body),
            "catalog_metadata_path": metadata_path, "catalog_metadata_sha256": sha(metadata_raw),
            "documented_scope": scope.record(), "scope_key": scope.key})
    return rows


def plan():
    before = snapshot()
    if (before["database_sha256_at_snapshot"] != EXPECTED_DB_SHA or len(before["requests"]) != 15
            or len(before["reviews"]) != 12 or before["today_nhtsa_requests"]
            or before["leases_without_tokens"] or before["cooldowns"]):
        save("blocked_before_plan.json", {"status": "BLOCKED", "new_http_requests": 0,
                                        "reason": "ORIGINAL_LEDGER_OR_EXPECTED_UNUSED_DAY_MISMATCH", "snapshot": before})
        raise RuntimeError("Original ledger changed or is ambiguous; no live source request permitted")
    save("retained_ledger_before.json", before)
    value = {"schema_version": 1, "precommitted_at": datetime.now(timezone.utc).isoformat(), "actual_utc_day": DAY,
        "original_database_path": str(DB), "expected_original_database_sha256": EXPECTED_DB_SHA,
        "prior_request_rows": 15, "prior_review_rows": 12, "maximum_requests": 4, "maximum_make_subjects": 2,
        "maximum_run_seconds": 120, "maximum_response_bytes": 4_000_000, "per_call_timeout_seconds": 20,
        "runtime": "NhtsaNegativeReviewService + NhtsaRawRecallClient + PublicSourceBudget + original ScoutStore",
        "redirects_retries_or_direct_unreserved_endpoint_calls_permitted": False,
        "no_new_copy_reset_or_reconstructed_database": True,
        "selection_reason": "Previously unqueried exact catalog names, including the documented F-650 spelling rather than earlier F650SD alias. Catalogue membership does not establish issuer ownership, current production or source-wide recall coverage.",
        "scopes": documented_selections(), "http400_empty_policy": "PARTIAL_NEVER_COMPLETE_NEGATIVE",
        "acceptance_rule": "Exact documented model/year and original successful HTTP200 complete publisher empty envelope only",
        "all_four_whole_withheld_bodies_must_remain_absent": True,
        "score_trading_registry_or_done_changes_permitted": False}
    save("precommitted_plan.json", value)
    print(json.dumps({"status": "PLAN_FROZEN_NO_NETWORK", "scopes": [[r["make"], r["model"], r["model_year"]] for r in value["scopes"]],
                      "prior_request_rows": 15, "prior_review_rows": 12, "maximum_requests": 4, "maximum_makes": 2}))


def run():
    if (FOLDER / "automatic_review_block.json").exists():
        raise RuntimeError("Automatic approval rejected this run; no retry, refund or replacement transport permitted")
    if datetime.now(timezone.utc).date().isoformat() != DAY or (FOLDER / "run_started.json").exists():
        raise RuntimeError("The single actual UTC16 run already started or day changed; no retry")
    value = json.loads((FOLDER / "precommitted_plan.json").read_text())
    before = json.loads((FOLDER / "retained_ledger_before.json").read_text())
    current = snapshot()
    if (current["database_sha256_at_snapshot"] != before["database_sha256_at_snapshot"]
            or current["requests"] != before["requests"] or current["reviews"] != before["reviews"]
            or current["today_nhtsa_requests"] or current["leases_without_tokens"] or current["cooldowns"]):
        save("blocked_before_live.json", {"status": "BLOCKED", "new_http_requests": 0,
                                        "reason": "LEDGER_CHANGED_AFTER_PRECOMMIT", "snapshot": current})
        raise RuntimeError("Retained history changed; no live request performed")
    if value["scopes"] != documented_selections() or value["maximum_requests"] != 4 or value["maximum_make_subjects"] != 2:
        raise RuntimeError("Precommitted exact source scope or limits changed")
    scopes = [documented_model_scope(make=row["make"], model=row["model"], model_year=row["model_year"],
        catalog_body=(ROOT / row["catalog_path"]).read_bytes(),
        catalog_metadata=json.loads((ROOT / row["catalog_metadata_path"]).read_text())) for row in value["scopes"]]
    save("run_started.json", {"started_at": datetime.now(timezone.utc).isoformat(), "original_store": str(DB),
                              "plan_sha256": sha((FOLDER / "precommitted_plan.json").read_bytes()),
                              "maximum_requests": 4, "maximum_makes": 2})
    service = NhtsaNegativeReviewService(ScoutStore(DB), client=NhtsaRawRecallClient(), scopes=scopes,
        archive_root=FOLDER / "captures", max_requests=4, max_run_seconds=120)
    result = service.run()
    save("live_run.json", result)
    after = snapshot()
    request_hashes = {r["request_id"]: r["full_row_sha256"] for r in after["requests"]}
    review_hashes = {r["review_id"]: r["full_row_sha256"] for r in after["reviews"]}
    prior_requests_ok = all(request_hashes.get(r["request_id"]) == r["full_row_sha256"] for r in before["requests"])
    prior_reviews_ok = all(review_hashes.get(r["review_id"]) == r["full_row_sha256"] for r in before["reviews"])
    after.update(all_15_prior_request_rows_unchanged=prior_requests_ok, all_12_prior_review_rows_unchanged=prior_reviews_ok,
        original_schema_unchanged=before["schema_sha256"] == after["schema_sha256"],
        remaining_daily_requests=max(0, 8-after["today_nhtsa_requests"]),
        remaining_daily_make_slots=max(0, 4-after["today_nhtsa_make_subjects"]))
    save("retained_ledger_after.json", after)
    save("readonly_scope_report.json", service.report(as_of=datetime.now(timezone.utc), limit=100))
    if not prior_requests_ok or not prior_reviews_ok or after["today_nhtsa_requests"] > 4 or after["today_nhtsa_make_subjects"] > 2:
        raise RuntimeError("Prior history or batch bounds violated; retain evidence and stop")
    print(json.dumps({"status": result["status"], "attempted_requests": result["attempted_requests"],
        "reviews": [{"make": r["scope"]["make"], "model": r["scope"]["model"], "status": r["status"],
                     "http_status": r.get("http_status"), "reason": r["reason"]} for r in result["reviews"]],
        "prior_requests_unchanged": prior_requests_ok, "prior_reviews_unchanged": prior_reviews_ok,
        "remaining_daily_requests": after["remaining_daily_requests"], "remaining_make_slots": after["remaining_daily_make_slots"]}, sort_keys=True))


if __name__ == "__main__":
    if sys.argv[1:] == ["--plan"]:
        plan()
    elif sys.argv[1:] == ["--run"]:
        run()
    else:
        raise SystemExit("Choose one --plan or --run; neither operation may overwrite its evidence")
