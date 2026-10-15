"""Prepare explicit retained configuration using today's physical clock only.

No active manifest is installed. Source files are not copied or altered, and
the helper creates no forecasts/labels or application history registration.
"""
import hashlib
import json
from pathlib import Path

from market_checker_app.services.prospective_source_manifest_service import ProspectiveSourceManifestService, _encoded

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ASSET = "evidence/batch5_prices_20261014/observations/yahoo_price_338018656289c9d517ba7b1af6ea649ec2d65f18d2ed437666546eb90d07f0b3.json"
BENCHMARK = "evidence/batch5_prices_20261014/observations/yahoo_price_00dacf9bfa283dcfcf2e63d20aca14c20c8244f96d3e8a3eae356b0a79fac572.json"
RSS = "evidence/batch5_coordinator_20261014/source_runs/rss/specialist_source_run_9947758362370d6e8dac1763eb956ae3ddab0555844a507a213e26d95aadfd27_47ed52005d8796380d2472d593f0d74ea72c63da085ca0b7d23f78da124bd151.json"
SELECTION = [{"ticker": "AAPL", "benchmark_ticker": "SPY", "source_archive_paths": [ASSET, BENCHMARK]},
             {"ticker": "MSFT", "benchmark_ticker": "SPY", "source_archive_paths": [RSS, BENCHMARK]}]


def digest(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def build_preview():
    before = [digest(ROOT / name) for name in (ASSET, BENCHMARK, RSS)]
    service = ProspectiveSourceManifestService(ROOT)
    prepared = service.prepare(forecast_sources=SELECTION, label_observations=[])
    after = [digest(ROOT / name) for name in (ASSET, BENCHMARK, RSS)]
    if before != after:
        raise ValueError("Retained source bytes changed during read-only preparation")
    return prepared, {"selected_original_sources": before, "sources_unchanged": True,
        "source_http_requests": 0, "source_discovery_performed": False, "database_opened": False,
        "new_forecasts": 0, "new_labels": 0, "registrations": 0, "active_manifest_written": False,
        "observation_clock_injected": False, "prior_capture_clocks_rebased": False,
        "historical_acceptance_verified": False, "scoring_changed": False,
        "scope": "VALIDATED_CONFIGURATION_PREVIEW_ONLY; FEATURE_COVERAGE_UNVERIFIED"}


if __name__ == "__main__":
    names = ("retained_preparation.json", "retained_manifest_preview.json", "retained_diagnostics.json")
    if any((HERE / name).exists() for name in names):
        raise SystemExit("Physical preparations have their own clocks; retain this version and choose new diagnostic names")
    prepared, diagnostics = build_preview()
    for name, value in zip(names, (prepared, prepared["manifest"], diagnostics)):
        with (HERE / name).open("xb") as output:
            output.write(_encoded(value))
    print(json.dumps({"manifest_sha256": prepared["manifest_sha256"], "prepared_at": prepared["prepared_at"],
        "forecast_source_rows": len(prepared["manifest"]["forecast_sources"]), "source_files": len(prepared["source_pins"]),
        "selected_bytes": prepared["selected_total_file_bytes"], "future_or_historical_forecasts_created": 0,
        "active_manifest_written": False, "network_requests": 0}))
