"""Focused offline tests and explicit original-capture readiness; no forecasts."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from io import StringIO
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from market_checker_app.services.captured_market_data_provider import (
    CapturedMarketDataProvider, MANIFEST_SCHEMA_VERSION, build_captured_market_data_entry,
)
from market_checker_app.services.ohlc_quality import assess_daily_ohlc
from market_checker_app.services.yahoo_price_provenance_service import load_observation

BASE="e252ec62796b0b02e733c55ebef713e0f29d4b44"
CAPTURE_IDS=(
    "0033e6addad97e140df75194388a0244b36dae8bc4af5bc6e8803e6d614dc63f",
    "00dacf9bfa283dcfcf2e63d20aca14c20c8244f96d3e8a3eae356b0a79fac572",
    "0481c60af18666e9b9860de90bef93fc3c37967a140c005fccc9cf462385a63a",
    "0be595592cbb8a7fe4379341e04dd3f293a1786b22c3d1ade860ad5648d7cf39",
    "2ea75ef8154a0568a0de7139b3a5e9b03563188bb989697e27a9c7dfb1167d2d",
    "338018656289c9d517ba7b1af6ea649ec2d65f18d2ed437666546eb90d07f0b3",
    "3ca28ea80343e3c0203eb99a404268c18f32ca41b02fecee3ca1c67be2869142",
    "3e64161b31226e1ab5cb1784492786a65b2b60f32b534d92ea96cebd0b76d666",
    "789feed9c33cc8f2433a61c83038f79907d0808c694b578f38df3e7064a5c859",
    "8be10af5009d0f6910cce5438729e3ff324a4607e9266226ec67fddaf71ff3dc",
    "a14d7d956e01a7b2e333f27870719f7d78a12a44241cbb1125bd17485a4afe22",
    "ba4cd6a116f56b20ab5c04ee6cf04b93c2dbf203882dbb5e359ef245398e2fbb",
    "c14f628d9fabb1b0908d738488db41ef07744fa2f0d331f9ca17715247056861",
    "e91e4454f7bac193c609247b0335b1a036662c2da0c62a3f78a5bfbc9cab43aa",
    "ec5eac7ffdcf115da7d8f373a545569a24738236e96170926044c253258f5893",
    "f53c3f5b277b4700f822dd95072fb165f13164364478f135c4fc057619b433a3")
OWNED=("market_checker_app/services/captured_market_data_provider.py",
       "tests/test_batch8_captured_market_data_provider.py",
       "evidence/batch8_evaluation_20261015/README.md",
       "evidence/batch8_evaluation_20261015/reproduce.py")
UNCHANGED=("market_checker_app/config.py","market_checker_app/services/pipeline_service.py",
    "market_checker_app/weekly_shadow_runner.py","market_checker_app/prediction_contract.py",
    "market_checker_app/services/candidate_model_service.py",
    "market_checker_app/services/prediction_label_service.py",
    "market_checker_app/services/prospective_specialist_history.py")

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write(path,body):
    with Path(path).open("x",encoding="utf-8",newline="\n") as stream:
        stream.write(json.dumps(body,sort_keys=True,indent=2,allow_nan=False)+"\n")

def prior_evidence():
    manifest={}
    listing=subprocess.check_output(["git","ls-tree","-rz",BASE,"--","evidence"],cwd=ROOT)
    for entry in listing.split(b"\0"):
        if not entry:continue
        meta,name=entry.split(b"\t",1);_,kind,expected=meta.split()
        if kind!=b"blob":continue
        manifest[name.decode()]=expected.decode()
    # Git compares tracked paths to the exact base; no broad source/body scan
    # or attempted recovery of any withheld/untracked payload is performed.
    changes=subprocess.check_output(["git","diff","--name-only",BASE,"--","evidence"],cwd=ROOT,text=True).splitlines()
    failures=[name for name in changes if name in manifest]
    return {"files_checked":len(manifest),"mismatches_or_missing":failures,
        "all_byte_identical":not failures,"method":"git tracked-content diff against exact base; original blob IDs inventoried without exporting prior bodies",
        "base_blob_id_manifest_sha256":hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output-directory",type=Path,required=True)
    args=parser.parse_args();out=args.output_directory.resolve()
    out.mkdir(parents=True,exist_ok=True)
    if any((out/name).exists() for name in ("tests.log","actual_readiness.json","handoff.json","actual_plans")):
        raise FileExistsError("Existing evidence is preserved; choose a new output directory")
    before={path:sha(ROOT/path) for path in OWNED}
    suite=unittest.defaultTestLoader.loadTestsFromName("tests.test_batch8_captured_market_data_provider")
    discovered=suite.countTestCases();started=time.perf_counter()
    with patch("socket.socket.connect",side_effect=AssertionError("Strictly offline")), \
         patch("sqlite3.connect",side_effect=AssertionError("No DB access")), \
         (out/"tests.log").open("x",encoding="utf-8",newline="\n") as log:
        tested=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
        plans=out/"actual_plans";plans.mkdir()
        actual=[]
        for identifier in CAPTURE_IDS:
            relative=f"evidence/batch5_prices_20261014/observations/yahoo_price_{identifier}.json"
            path=ROOT/relative;source_hash=sha(path)
            entry=build_captured_market_data_entry(archive_root=ROOT,source_archive_path=relative)
            manifest_path=plans/f"manifest_{identifier}.json"
            write(manifest_path,{"schema_version":MANIFEST_SCHEMA_VERSION,"instruments":[entry]})
            provider=CapturedMarketDataProvider(manifest_path=manifest_path,archive_root=ROOT)
            readiness=provider.readiness([entry["ticker"]],include_benchmarks=False)
            record=load_observation(path)
            frame=pd.read_json(StringIO(record["frame_json"]),orient="split")
            frame.index=pd.to_datetime(frame.index,utc=True)
            clock=datetime.now(timezone.utc);quality=assess_daily_ohlc(frame,as_of=clock)
            actual.append({"source_path":relative,"source_sha256_before":source_hash,"source_sha256_after":sha(path),
                "plan_path":str(manifest_path.relative_to(out)),"plan_sha256":sha(manifest_path),
                "ticker":entry["ticker"],"source_kind":entry["source"]["kind"],
                "original_observed_at":entry["source"]["observed_at"],"original_captured_at":entry["source"]["captured_at"],
                "last_original_frame_session":entry["source"]["last_session"],"original_frame_rows":entry["source"]["row_count"],
                "publisher_revision_timing_status":record["publisher_revision_timing_status"],
                "readiness":readiness,"independent_current_quality":{"as_of":clock.isoformat(),
                    "price_usable":quality.price_usable,"history_usable":quality.history_usable,
                    "completed_rows":quality.observation_count,"missing_lookbacks":list(quality.missing_lookbacks)}})
    elapsed=time.perf_counter()-started
    actual_result={"observed_at":datetime.now(timezone.utc).isoformat(),"evidence_origin":"ACTUAL_RETAINED_CAPTURE_READINESS_ONLY",
        "selected_versions":len(actual),"usable_versions":sum(row["readiness"]["usable_count"] for row in actual),
        "wait_versions":sum(row["readiness"]["status"]=="WAIT_DATA" for row in actual),
        "actual_forecasts_created":0,"actual_labels_created":0,"source_http_requests":0,"db_accesses":0,
        "publisher_revision_upgrades":0,"withheld_bodies_opened_or_recovered":0,"rows":actual}
    write(out/"actual_readiness.json",actual_result)
    preservation=prior_evidence();after={path:sha(ROOT/path) for path in OWNED}
    unchanged={}
    for relative in UNCHANGED:
        original=subprocess.check_output(["git","show",BASE+":"+relative],cwd=ROOT)
        unchanged[relative]={"sha256":sha(ROOT/relative),"byte_identical_to_base":(ROOT/relative).read_bytes()==original}
    artifacts={str(path.relative_to(out)):sha(path) for path in [out/"tests.log",out/"actual_readiness.json",*sorted(plans.iterdir())]}
    initial=ROOT/"evidence/batch8_evaluation_20261015/development_tests_1.log"
    report={"schema_version":1,"generated_at":datetime.now(timezone.utc).isoformat(),"base":BASE,
        "deliverable":"Explicit fail-closed captured-market provider for normal inputs; coordinator owns runtime integration",
        "api":{"class":"CapturedMarketDataProvider(manifest_path=...,archive_root=...)",
            "readiness":"readiness(tickers,include_benchmarks=True): READY/WAIT_DATA",
            "persistence_gate":"revalidate_for_persistence(tickers,expected_readiness=initial_report)",
            "provenance":"frame.attrs[captured_market_data_provenance]; provenance_for(ticker) fresh-checks same immutable selection; selection_provenance() is not readiness",
            "client_methods":["fetch_ohlc","fetch_ohlc_only","fetch_ohlc_batch","fetch_snapshots","fetch_metadata"],
            "metadata":"YahooSnapshot(normalized_symbol,{},missing), WAIT_DATA:CAPTURED_METADATA_NOT_SUPPLIED; no invented price/sector/analyst values",
            "manifest":"captured_market_data_manifest_v1; exact pinned entries; no discovery or source version replacement",
            "root_integration_requirements":["Explicit opt-in only; bypass persistent metadata/OHLC cache initialization, reads and writes.",
                "Require all requested assets and all12 benchmarks READY before any normal run persistence.",
                "Revalidate same pinned sources and physical freshness immediately before prediction persistence; stop on any failure.",
                "Keep other live source-producing agents/RSS/MT5 disabled for an explicitly offline run; do not auto-enable prospective auditing."]},
        "tests":{"discovered":discovered,"executed":tested.testsRun,"failures":len(tested.failures),"errors":len(tested.errors),
            "skips":len(tested.skipped),"expected_failures":len(tested.expectedFailures),"unexpected_successes":len(tested.unexpectedSuccesses),
            "successful":tested.wasSuccessful(),"elapsed_seconds_including_actual_readiness":round(elapsed,6),
            "positive_cases_synthetic_only":True,"no_forecast_or_db_fixture_created":True,"network_and_sqlite_connections_forbidden":True},
        "initial_focused_capture":{"path":str(initial.relative_to(ROOT)),"sha256":sha(initial),"executed":31,"failures":0,"errors":0,"current_acceptance":False},
        "actual_result":{"selected_versions":len(actual),"wait_versions":actual_result["wait_versions"],"usable_versions":actual_result["usable_versions"],
            "actual_forecasts":0,"actual_labels":0,"strict_pit_eligible_labels":0,"acceptance_claimed":False},
        "owned_file_sha256":after,"owned_files_stable_through_qualification":before==after,
        "artifact_sha256":artifacts,"preserved_prior_evidence":preservation,"unchanged_shared_files":unchanged,
        "claims":{"source_http_requests":0,"db_reads_or_writes":0,"quota_writes":0,"forecasts_or_labels_created":0,
            "old_source_or_evidence_rewritten":False,"withheld_bodies_opened":False,"source_clocks_rebased":False,
            "ohlcv_actions_or_sessions_synthesized":False,"model_score_fitter_budget_changed":False,"trading_enabled":False,
            "publisher_revision_inferred":False,"prospective_audit_implicitly_activated":False,"oos_acceptance_verified":False,
            "current_windows_verified":False,"full_previous_baseline_repeated":False},
        "remaining_real_data_requirements":["Supply original supported raw OHLCV captures for all requested assets plus12 benchmark inputs with current completed close and at least66 contiguous sessions.",
            "Preserve missing longer lookbacks, metadata, instrument/source identity limitations and original physical availability.",
            "Use current physical clocks and exact NYSE window when a genuine separately authorized normal run becomes possible; no historical forecast backfill.",
            "UNKNOWN publisher revisions remain excluded from strict later PIT/OOS labels; this provider is only honest normal-input provenance."]}
    write(out/"handoff.json",report)
    okay=(tested.wasSuccessful() and before==after and preservation["all_byte_identical"]
        and all(row["byte_identical_to_base"] for row in unchanged.values())
        and actual_result["wait_versions"]==16 and actual_result["usable_versions"]==0
        and all(row["source_sha256_before"]==row["source_sha256_after"] for row in actual))
    print(json.dumps({"tests":report["tests"],"actual":report["actual_result"],"prior_evidence_unchanged":preservation["all_byte_identical"],"handoff_sha256":sha(out/"handoff.json")},sort_keys=True))
    return 0 if okay else 1

if __name__=="__main__":raise SystemExit(main())
