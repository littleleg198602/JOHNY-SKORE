"""Offline review of pinned inputs with an actual UTC physical import clock."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from market_checker_app.services.epa_frs_ownership_review import import_frs_ownership_review


def main():
    folder = Path(__file__).resolve().parent
    report = import_frs_ownership_review(
        json.loads((folder/"proposal.json").read_text()),
        json.loads((folder/"capture_plan.json").read_text()), repository_root=ROOT,
        archive_root=Path("evidence/batch6_regulatory_20261014/reviews"))
    (folder/"replay.json").write_text(json.dumps(report, sort_keys=True, indent=2)+"\n",
                                     encoding="utf-8", newline="\n")
    print(json.dumps({"status": report["status"], "issuer_identity_supported": report["issuer_identity_supported"],
        "facility_identity_supported": report["facility_identity_supported"],
        "ownership_statement_supported": report["ownership_statement_supported"],
        "missing_evidence": report["missing_evidence"], "actual_http_requests": 0}))


if __name__ == "__main__":
    main()
