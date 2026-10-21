"""Offline exact retained SWKS plan/review; does not fetch or rewrite sources."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent
PLAN = FOLDER / "SWKS_zalohy_na_vyrobni_kapacitu.json"
sys.path.insert(0, str(ROOT))

from market_checker_app.services.wafer_capacity_deposit_review import review_wafer_capacity_deposits


def pin(body, start, end):
    return {"byte_start": start, "byte_end": end, "sha256": hashlib.sha256(body[start:end]).hexdigest()}


def make_plan():
    original = json.loads((ROOT / "evidence/specialist_review_plans/SWKS_AAPL_podil_na_trzbach.json").read_bytes())
    discovery = json.loads((ROOT / "evidence/sourcefront_coordinator_20261020/discovery/contracts.json").read_bytes())
    body = (ROOT / original["captures"]["issuer"]["body_ref"]["path"]).read_bytes()
    if hashlib.sha256(body).hexdigest() != discovery["retained_corpus"]["primary"]["sha256"]:
        raise ValueError("Whole original SWKS source differs from the retained discovery")
    company = body.index(b"Skyworks Solutions, Inc., together with its consolidated subsidiaries")
    start, end = body.rfind(b"<div", 0, company), body.index(b"</div>", company) + len(b"</div>")
    selected = {key: original["request"]["selection"][key] for key in (
        "issuer_ticker", "issuer_context_id", "issuer_name", "issuer_cik", "fiscal_period_end")}
    selected.update(claim_role="CAPACITY_RESERVATION_REFUNDABLE_DEPOSIT_AND_PREPAYMENT_ASSET",
        reporting_scope="CONSOLIDATED_REPORTING_GROUP", notes_heading=pin(body, 980143, 980341),
        company_definition=pin(body, start, end),
        consolidation_scope=original["request"]["selection"]["scope_passage"],
        section_heading=pin(body, 1423638, 1423945),
        cancellation_qualifiers=pin(body, 1428061, 1428947), deposit_paragraph=pin(body, 1429003, 1432698),
        balances=[
            {"as_of_day": "2025-10-03", "context_id": "c-193",
             "total": {"value_literal": "26.4", "fact_ids": ["f-895", "f-896"]},
             "current": {"value_literal": "7.7", "fact_ids": ["f-897", "f-898"]},
             "noncurrent": {"value_literal": "18.7", "fact_ids": ["f-899", "f-900"]}},
            {"as_of_day": "2024-09-27", "context_id": "c-194",
             "total": {"value_literal": "167.8", "fact_ids": ["f-901", "f-902"]},
             "current": {"value_literal": "144.7", "fact_ids": ["f-903", "f-904"]},
             "noncurrent": {"value_literal": "23.1", "fact_ids": ["f-905", "f-906"]}},
        ])
    return {"schema_version": "captured_identity_review_plan_v1", "kind": "WAFER_CAPACITY_DEPOSITS",
        "request": {"selection": selected}, "captures": {key: original["captures"][key] for key in ("issuer", "index")}}


def inputs(plan):
    captures = {}
    for key, entry in plan["captures"].items():
        reference = entry["body_ref"]
        body = (ROOT / reference["path"]).read_bytes()
        if len(body) != reference["bytes"] or hashlib.sha256(body).hexdigest() != reference["sha256"]:
            raise ValueError("Exact retained whole-source pin changed")
        captures[key] = (entry["metadata"], body)
    return plan["request"], captures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create-plan", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.create_plan:
        with PLAN.open("x", encoding="utf-8") as out:
            json.dump(make_plan(), out, indent=2, sort_keys=True); out.write("\n")
    if args.output is not None:
        result = review_wafer_capacity_deposits(*inputs(json.loads(PLAN.read_bytes())))
        with args.output.open("x", encoding="utf-8") as out:
            json.dump(result, out, indent=2, sort_keys=True); out.write("\n")
        print(json.dumps({"recorded_at": datetime.now(timezone.utc).isoformat(), "status": result["status"],
            "reason": result["reason"], "source_http_requests": 0, "output": str(args.output),
            "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest()}, indent=2))


if __name__ == "__main__":
    main()
