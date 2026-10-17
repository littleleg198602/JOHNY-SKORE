"""Read exact archived evidence; no collection, database initialization or acceptance."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASE = "b81a9084c064dc1a12bf17088855b5ed384ba3dd"
WITHHELD = {
    "84b6ad89bee343819008e22babdf9a87dbfd3034a99022ea68320f0f17edf8b3",
    "deb5642d450c0fd60cd12066a42da8c1905f4fd47562afcef06cf8e428bcbeb1",
    "5130e6c90a0770d32e40f5eafcc87cd980d963934ae6c125963d8b1f2c20eac1",
    "27f03d6bb63f0590a82369556419c4eb8f5fb9ab3b3da44a9c8695d18d7b7532",
}


def read(path):
    return json.loads((ROOT / path).read_bytes())


def pin(path):
    data = (ROOT / path).read_bytes()
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def verify_pins(expected):
    for path, wanted in expected.items():
        assert pin(path) == wanted, path
    return {"files": len(expected), "bytes": sum(row["bytes"] for row in expected.values())}


def verify():
    baseline = read(HERE.relative_to(ROOT) / "baseline_evidence_manifest.json")
    old = verify_pins(baseline)
    assert old == {"files": 1329, "bytes": 68_218_322}
    protected = verify_pins(read(HERE.relative_to(ROOT) / "protected_inputs_baseline.json"))
    assert protected["files"] == 7
    handoffs = []
    evidence_pin_count = 0
    current_producers = {}
    for path in sorted((ROOT / "evidence").glob("batch9_*/handoff.json")):
        handoff = json.loads(path.read_bytes())
        if handoff.get("schema_version") != "batch9_preserved_worker_handoff_v1":
            continue
        assert handoff["public_base"] == BASE
        assert handoff["finalization_new_source_http_requests"] == 0
        assert handoff["registry_adoptions"] == handoff["full_specialist_acceptance"] == 0
        assert not handoff["historical_OOS_verified"]
        assert not handoff["user_windows_end_to_end_verified"]
        evidence_pin_count += verify_pins(handoff["evidence_file_pins"])["files"]
        for kind in ("module", "test"):
            verify_pins(handoff[kind])
            current_producers.update(handoff[kind])
        qualification = handoff["focused_qualification"]
        verify_pins({qualification["log_path"]: qualification["log_pin"]})
        log = (ROOT / qualification["log_path"]).read_text(encoding="utf-8")
        count = qualification["executed"]
        assert count == qualification["discovered"] > 0
        assert re.search(rf"Ran {count} tests in [0-9.]+s\n\nOK\n\Z", log)
        assert all(qualification[key] == 0 for key in (
            "failures", "errors", "skips", "expected_failures", "unexpected_successes"))
        handoffs.append(handoff["development_lane"])
    assert set(handoffs) == {"identity_bulk", "healthcare", "regulatory", "contracts", "sec", "evaluation"}
    peer_root = "evidence/batch9_market_parent_peer_20261017/"
    peer = read(peer_root + "handoff.json")
    peer_pin_count = verify_pins({peer_root + p: value for p, value in peer["artifact_pins"].items()})["files"]
    verify_pins(peer["qualified_source_files"])
    before_peer = read(peer_root + "before_fix_v1/tests.json")
    after_peer = read(peer_root + "after_fix_v2/tests.json")
    assert before_peer["discovered"] == before_peer["executed"] == after_peer["discovered"] == after_peer["executed"] == 7
    assert before_peer["failures"] == 6 and before_peer["errors"] == 0
    assert not before_peer["successful"] and after_peer["successful"]
    assert all(after_peer[key] == 0 for key in ("failures", "errors", "skips", "expected_failures", "unexpected_successes"))
    assert pin("tests/test_batch9_market_parent_peer.py") == pin(peer_root + "original/peer_tests_original.py")
    assert len(before_peer["observations"]) == len(after_peer["observations"]) == 19
    late_before = [r for r in before_peer["observations"] if r["scenario"] == "save_progress_callback"]
    late_after = [r for r in after_peer["observations"] if r["scenario"] == "save_progress_callback"]
    assert len(late_before) == len(late_after) == 6
    assert all(r["synthetic_in_memory_save_calls"] == 1 and not r["raised_WAIT_DATA"] for r in late_before)
    assert all(r["synthetic_in_memory_save_calls"] == 0 and r["raised_WAIT_DATA"] for r in late_after)
    assert all(r["source_http_requests"] == r["actual_database_initializations"] == r["genuine_forecasts"] == 0 for r in after_peer["observations"])
    assert any(r["scenario"] == "positive_control" and r["synthetic_in_memory_save_calls"] == 1 for r in after_peer["observations"])

    dispatch = read(HERE.relative_to(ROOT) / "initial_runtime_dispatch.json")
    assert len(dispatch) == 8
    new_originals = {}
    leaves = {}
    for row in dispatch:
        archive = HERE / "runtime" / row["archive_filename"]
        assert pin(archive.relative_to(ROOT))["sha256"] == row["archive_sha256"]
        report = json.loads(archive.read_bytes())
        plan_path = "evidence/specialist_review_plans/" + row["plan"]
        assert report["plan_sha256"] == pin(plan_path)["sha256"]
        assert report["kind"] == row["kind"]
        assert report["source_http_requests"] == report["registry_adoptions"] == 0
        assert not report["full_specialist_acceptance"] and not report["scoring_applied"]
        assert not report["issuer_attribution_allowed"]
        assert report["recorded_at"] == row["recorded_at"]
        leaves[row["plan"]] = report["review"]
        for capture in read(plan_path)["captures"].values():
            body = capture.get("body_ref")
            if body is None:
                assert not capture["body_available"]
                continue
            verify_pins({body["path"]: {key: body[key] for key in ("bytes", "sha256")}})
            assert body["bytes"] <= 4_000_000 and body["sha256"] not in WITHHELD
            if body["path"].startswith("evidence/batch9_"):
                new_originals[body["sha256"]] = body
    assert len(new_originals) == 13
    assert sum(row["bytes"] for row in new_originals.values()) == 2_027_627

    identity = leaves["P_trida_akcii_a_CUSIP.json"]
    assert identity["registered_source_anchor_count"] == 2
    assert [r["context_anchor_on"] for r in identity["registered_security_facts"]] == ["2026-05-27", "2026-08-26"]
    assert all(r["symbol"] == "P" and r["same_row_cusip"] is None for r in identity["registered_security_facts"])
    assert not identity["automatic_alias_allowed"]
    assert identity["current_q3_verified_instrument_coverage"] == 0
    company = leaves["PFE_puvodni_produkty_DrugsFDA.json"]
    applicant = leaves["PFE_Purple_Book_applicant.json"]
    assert company["application_facts"]["source_party_name"] == "PFIZER INC"
    assert company["application_facts"]["source_party_role"] == "COMPANY_AS_DISPLAYED"
    assert company["product_rows"][0]["product_number"] is None
    assert applicant["application_facts"]["source_party_name"] == "Pfizer Ireland Pharmaceuticals Unlimited Company"
    assert [r["product_number"] for r in applicant["product_rows"]] == ["001", "002"]
    assert [r["strength"] for r in applicant["product_rows"]] == ["420MG", "150MG"]
    assert all(not leaf["legal_owner_verified"] and not leaf["corporate_parent_verified"] for leaf in (company, applicant))
    positive = leaves["CHEVROLET_7500XD_2026_puvodni_rozsah.json"]
    failed = leaves["CHEVROLET_BrightDrop_HTTP400_2026.json"]
    blocked = leaves["CHEVROLET_5500XG_prerusene_overeni_2026.json"]
    assert (positive["status"], positive["http_status"], positive["publisher_count"]) == ("POSITIVE_EXACT_MODEL_YEAR", 200, 2)
    assert failed["reason"] == "HTTP_400_IS_NOT_COMPLETE_NEGATIVE"
    assert blocked.get("http_status") is None and blocked.get("matched_rows") is None
    assert all(not leaf["complete_negative"] and not leaf["live_complete_negative_verified"] for leaf in (positive, failed, blocked))
    supplier = leaves["SWKS_AAPL_podil_na_trzbach.json"]
    assert supplier["claim"]["reporting_scope"] == "CONSOLIDATED_REPORTING_GROUP"
    assert [r["value_literal"] for r in supplier["claim"]["quantities"]] == ["67", "69", "66"]
    assert all(not r["generic_customer_member_renamed"] for r in supplier["claim"]["quantities"])
    assert not supplier["direct_customer_share_verified"] and not supplier["continuous_legal_identity_verified"]
    packet = leaves["MSFT_Form4_uplny_packet.json"]
    assert all(packet[key] for key in ("original_complete_packet_supported", "original_header_scope_supported", "original_primary_xml_exact_binding_supported"))
    assert (packet["transaction_row_count"], packet["holding_row_count"]) == (2, 5)
    assert all(not packet[key] for key in ("natural_person_identity_verified", "cusip_class_validity_verified", "role_continuity_verified", "execution_verified"))

    status = read("market_checker_app/data/specialist_status.json")
    old_status = json.loads(subprocess.check_output(["git", "show", BASE + ":market_checker_app/data/specialist_status.json"], cwd=ROOT))
    fields = lambda rows: [(r["id"], r["code"], r["live"]) for r in rows["specialists"]]
    assert fields(status) == fields(old_status) and len(status["specialists"]) == 21
    assert not any(r["code"] == "DONE" and r["live"] == "VERIFIED" for r in status["specialists"])
    production = read(HERE.relative_to(ROOT) / "production_baseline.json")["production_files"]
    changed = {p: {"before": value, "after": pin(p)} for p, value in production.items() if pin(p) != value}
    allowed = {"market_checker_app/services/captured_identity_review_service.py",
               "market_checker_app/services/captured_market_data_provider.py",
               "market_checker_app/services/pipeline_service.py",
               "market_checker_app/specialist_evidence_ui.py",
               "market_checker_app/data/specialist_status.json"}
    assert set(changed) <= allowed, sorted(set(changed) - allowed)
    # Check the staged/tracked public files only, never private ancestry or ignored bodies.
    for value in subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0"):
        if value:
            assert pin(value)["sha256"] not in WITHHELD, value
    baseline_spec = importlib.util.spec_from_file_location("baseline_originals", ROOT / "evidence/batch9_baseline_ci_20261016/verify_originals.py")
    baseline_module = importlib.util.module_from_spec(baseline_spec)
    baseline_spec.loader.exec_module(baseline_module)
    baseline_ci = baseline_module.verify()
    return {"schema_version": "batch9_content_audit_v1", "audited_at": datetime.now(timezone.utc).isoformat(),
            "public_base": BASE, "old_evidence_unchanged": old, "protected_inputs_unchanged": protected,
            "six_actual_development_handoffs": sorted(handoffs), "handoff_evidence_pins_verified": evidence_pin_count,
            "additional_independent_peer_pins_verified": peer_pin_count,
            "late_persistence_defect_reproduced_then_fixed_with_same_seven_methods": True,
            "peer_fixtures_are_not_real_forecasts_or_database_writes": True,
            "current_worker_source_test_pins": current_producers, "new_original_response_pins": list(new_originals.values()),
            "new_original_response_count": len(new_originals), "new_original_bytes": 2_027_627,
            "eight_actual_runtime_archives_content_verified": True, "new_runtime_http_requests": 0,
            "source_accounting": {"research_original_HTTP200": 12, "proven_NHTSA_HTTP200": 1,
                "charged_NHTSA_reservations": 2, "second_NHTSA_response": "UNKNOWN_NO_REVIEW_OR_BODY",
                "NHTSA_live_continuation": "AUTOMATIC_REVIEW_REJECTED_NO_RETRY_OR_BYPASS",
                "AAPL_transport_failures_without_response": 1, "remaining_market_requests_stopped": 12,
                "new_CMS_Clinical_FDA_USAspending_collector_actions": 0},
            "original_b81_CI_content_verified": baseline_ci, "existing_production_changes": changed,
            "unchanged_existing_production_file_count": len(production) - len(changed),
            "all_21_code_live_statuses_unchanged": True,
            "code_statuses": dict(Counter(r["code"] for r in status["specialists"])),
            "live_statuses": dict(Counter(r["live"] for r in status["specialists"])),
            "whole_withheld_bodies_absent_from_public_files": sorted(WITHHELD),
            "full_specialist_acceptance": 0, "genuine_forecasts_registrations_labels": 0,
            "user_windows_end_to_end_verified": False, "historical_OOS_verified": False,
            "score_fitter_default_model_configuration_changed": False, "trading_allowed": False}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify()
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(encoded)
    else:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
        print(json.dumps({"output": str(args.output), "sha256": hashlib.sha256(encoded.encode()).hexdigest()}))
