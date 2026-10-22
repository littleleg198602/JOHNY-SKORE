"""Explicit whole-byte replay of two retained original charters; zero collection."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from market_checker_app.services.sec13f_identity_bulk import Sec13fIdentityBulk, SourceSnapshot
from market_checker_app.services.sec_corporate_action_bridge import review_charter_class_disclosure

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CASES = {"LEG": "LEG_pravni_kapital_po_fuzi.json", "XOM": "XOM_pravni_kapital_nastupce.json"}


def load_case(ticker, *, source_cutoff=None):
    plan = json.loads((HERE / CASES[ticker]).read_bytes())
    sources = []
    for item in plan["captures"].values():
        ref, meta = item["body_ref"], item["metadata"]
        raw = (ROOT / ref["path"]).read_bytes()
        if (item["body_available"] is not True or len(raw) != ref["bytes"]
                or hashlib.sha256(raw).hexdigest() != ref["sha256"] or ref["sha256"] != meta["sha256"]):
            raise ValueError("Retained whole original no longer matches its explicit pin")
        source = SourceSnapshot(meta["kind"], meta["url"], meta["observed_at"], raw, meta["published_at"])
        if source.snapshot_id != meta["snapshot_id"]:
            raise ValueError("Retained source metadata pin differs")
        sources.append(source)
    cutoff = source_cutoff or datetime.now(timezone.utc)
    service = Sec13fIdentityBulk(snapshots=sources, universe=["LEG", "XOM"], quarter=plan["request"]["quarter"],
                               as_of=cutoff, existing=[])
    return service, plan["request"]["claim"]


def replay():
    reports = {}
    for ticker in CASES:
        service, claim = load_case(ticker)
        reports[ticker] = review_charter_class_disclosure(service, claim)
    return reports


if __name__ == "__main__":
    reports = replay()
    for ticker, report in reports.items():
        path = HERE / (ticker + "_actual_charter_review.json")
        body = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        if path.exists():
            raise ValueError("Original review is immutable; use a new named diagnostic/version")
        path.write_bytes(body)
    print(json.dumps({ticker: {key: value[key] for key in (
        "status", "authorized_class_supported", "authorized_common_shares", "charter_effective_on",
        "charter_execution_on", "blockers", "new_verified_identities", "current_quarter_verified_coverage")}
                     for ticker, value in reports.items()}, indent=2))
