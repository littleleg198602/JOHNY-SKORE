"""Offline independent basis-join diagnostic; synthetic fixtures, zero source GETs.

Use PYTHONPATH=<contracts candidate root>. Frozen mode replays preserved source
and fixture versions; current mode challenges the current owner implementation.
All outputs are exclusive-created under this peer evidence directory.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

from market_checker_app.collectors import sec_supplier_revenue_denominator_review as current_service
from market_checker_app.services import sec_supplier_concentration_review as current_concentration

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("frozen", "current"), required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if Path(args.output).name != args.output or not args.output.endswith(".json"):
        raise ValueError("Select one new peer-local diagnostic JSON filename")
    frozen = HERE / "producer_versions"
    service = load("peer_frozen_denominator", frozen / "candidate_service.py") if args.mode == "frozen" else current_service
    concentration = load("peer_frozen_concentration", frozen / "concentration_dependency.py") if args.mode == "frozen" else current_concentration
    fixture = load("peer_frozen_denominator_fixture", frozen / "candidate_tests.py")
    base = load("peer_frozen_concentration_fixture", frozen / "base_concentration_tests.py")
    fixture.service, fixture.concentration_fixture, fixture.capture, fixture.pin = service, base.fixture, base.capture, base.pin
    service_review = concentration.review_sec_supplier_concentration
    cases = [
        ("unchanged_policy_control", ""),
        ("explicit_subsidiary_scope_difference", " The 2024 and 2023 net revenue totals in these statements exclude subsidiary sales that remain included in the customer concentration percentages."),
        ("postposed_definition_revision", " The definition of revenue was revised for fiscal 2024 and fiscal 2023."),
        ("adjusted_prior_year_presentation", " Prior-year net revenue amounts were adjusted to conform to the current-year presentation."),
        ("rounding_only_control", " Customer concentration percentages are rounded to the nearest whole percentage point."),
    ]
    results = []
    with patch.object(service, "review_sec_supplier_concentration", service_review), \
            patch.object(service, "_physical_clock", return_value=fixture.NOW), \
            patch.object(concentration, "_physical_clock", return_value=fixture.NOW), \
            patch("socket.socket.connect", side_effect=AssertionError("No source HTTP")), \
            patch("sqlite3.connect", side_effect=AssertionError("No DB")):
        for name, qualification in cases:
            inputs = fixture.fixture()
            if qualification:
                inputs = fixture.changed(inputs, fixture.POLICY.encode(), (fixture.POLICY + qualification).encode())
            request, captures = inputs
            result = service.review_sec_supplier_revenue_denominator(request, captures)
            results.append({"case": name, "qualification": qualification, "request": request,
                            "synthetic_source_pins": {key: {"sha256": hashlib.sha256(pair[1]).hexdigest(), "bytes": len(pair[1])} for key, pair in captures.items()},
                            "result": result})
    version = Path(service.__file__)
    output = {"schema_version": "independent_denominator_basis_peer_v1", "executed_at": datetime.now(timezone.utc).isoformat(),
              "mode": args.mode, "producer_path": str(version), "producer_sha256": hashlib.sha256(version.read_bytes()).hexdigest(),
              "synthetic_fixture_only": True, "synthetic_injected_review_clock": fixture.NOW.isoformat(),
              "synthetic_clock_is_not_a_physical_source_observation": True,
              "new_source_requests": 0, "db_writes": 0, "registry_adoptions": 0,
              "candidate_source_edited": False, "results": results}
    with (HERE / args.output).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"mode": args.mode, "producer_sha256": output["producer_sha256"], "case_count": len(results),
                      "results": [{"case": item["case"], "status": item["result"]["status"],
                                   "join_status": item["result"]["claim"]["concentration_join_status"] if item["result"]["claim"] else None}
                                  for item in results]}))


if __name__ == "__main__":
    main()
