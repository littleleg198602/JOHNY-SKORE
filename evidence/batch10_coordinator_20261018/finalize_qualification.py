"""Create an immutable pre-hosted qualification from original completed outputs."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BASE = "32adb8edd6aa6ccc1748d2cfbc228f7db3a16fd4"
CODE = "4a081a857b5fe282f410fdee958b6918a9bfad4c"


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def pin(name):
    body = (ROOT / name).read_bytes()
    return {"path": name, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def save(name, record):
    with (ROOT / name).open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")


def main():
    full = json.loads((ROOT / "evidence/batch10_linux_20261018.json").read_text())
    assert full["successful"] is True and full["tests_run"] == full["discovered_tests"]
    assert all(full[k] == 0 for k in ("failures", "errors", "skipped", "expected_failures", "unexpected_successes"))
    count = full["tests_run"]
    console = (ROOT / "evidence/batch10_linux_console_20261018.log").read_bytes()
    text = console.decode("utf-8")
    footer = re.search(r"(?m)^Ran (\d+) tests in ([\d.]+)s\n\nOK\n?\Z", text)
    assert footer and int(footer[1]) == count
    assert git("rev-parse", "HEAD").decode().strip() == CODE
    names = git("diff", "--name-only", "-z", BASE, CODE, "--", "market_checker_app", "tests").decode().split("\0")
    sources = {}
    for name in filter(None, names):
        body = (ROOT / name).read_bytes()
        assert body == git("show", CODE + ":" + name), name
        sources[name] = pin(name)
    test_modules = {Path(name).stem for name in sources if name.startswith("tests/test_") and name.endswith(".py")}
    starts = list(re.finditer(r"(?m)^(test_[^\r\n ]+) \(([^\r\n]+)\) \.\.\. ", text))
    assert len(starts) == count
    methods = []
    for index, match in enumerate(starts):
        method, qualified = match.group(1, 2)
        module, cls, method_id = qualified.rsplit(".", 2)
        assert method == method_id
        if module not in test_modules:
            continue
        end = starts[index + 1].start() if index + 1 < len(starts) else text.index("\n----------------------------------------------------------------------", match.start())
        block = text[match.start():end].strip()
        assert re.search(r"(?:\.\.\. |\r?\n)ok\Z", block), qualified
        methods.append({"module": module, "class": cls, "method": method,
            "original_ok_block_sha256": hashlib.sha256(block.encode()).hexdigest()})
    assert len(methods) == count - 2231
    assert len({(m["module"], m["class"], m["method"]) for m in methods}) == len(methods)
    methods_report = {"tested_code_snapshot": CODE, "full_discovered_and_executed": count,
        "new_exact_method_count": len(methods), "methods": methods,
        "console_pin": pin("evidence/batch10_linux_console_20261018.log"),
        "strict_test_boundary_blocks_used": True, "original_log_rewritten": False}
    save("evidence/batch10_linux_methods_20261018.json", methods_report)
    audit_path = "evidence/batch10_coordinator_20261018/content_audit_final.json"
    subprocess.run(["/workspace/scratch/334b3a149083/johny-tests-20261011/bin/python",
        str(ROOT / "evidence/batch10_coordinator_20261018/audit_content.py"), "--output", str(ROOT / audit_path)], check=True)
    audit = json.loads((ROOT / audit_path).read_text())
    runtime_name = "evidence/batch10_coordinator_20261018/runtime/runtime_capture.json"
    runtime = json.loads((ROOT / runtime_name).read_text())
    assert len(runtime["actual_source_review_actions"]) == 10 and runtime["actual_readiness_actions"] == 2
    assert all(v == 0 for v in runtime["counters"].values())
    runtime_pins = {p.relative_to(ROOT).as_posix(): pin(p.relative_to(ROOT).as_posix())
        for p in sorted((ROOT / "evidence/batch10_coordinator_20261018/runtime").rglob("*.json"))}
    status_name = "market_checker_app/data/specialist_status.json"
    status = json.loads((ROOT / status_name).read_text())
    old_status = json.loads(git("show", BASE + ":" + status_name))
    assert [(r["id"], r["code"], r["live"]) for r in status["specialists"]] == [(r["id"], r["code"], r["live"]) for r in old_status["specialists"]]
    workers = {
        "identity_bulk": "ab38e341130efe0187cef6a6cefa8ee24d2206dc",
        "healthcare": "1e28f6d2a8a431b252dbe106304c02cdfbf4cae5",
        "regulatory": "bca703329150bbe2478afabffec1590505429504",
        "contracts": "ebe86793eb417041d6c23c28823d5a30d8389786",
        "sec": "941624455dc7cc0463aad89e2fb94e4fc1534d93",
        "evaluation": "3fc689f3a7270f1b8a9c6179c87e8a55bc3449cf"}
    fixes = {"denominator_basis": "7fa91e89cef7651d5e1ef233496f4f7a0d83bc3e",
        "role_scope_and_cutoff": "d12d2a4bae18cd2c7a2d5e6fb52af4dad5df7958",
        "certificate_completion_clock": "568372b3497e1b950e8b6097c8525a9c1486838c",
        "applicant_completion_clock": "335a855b2caccd68917d11c6fedd499f7b7ad938",
        "caption_completion_clock": "a7f51c643cbc666fc4b924052faa751f9ba82ae0"}
    proof = {"schema_version": "batch10_combined_pre_hosted_qualification_v1",
        "physical_recorded_at": datetime.now(timezone.utc).isoformat(), "repository": "littleleg198602/JOHNY-SKORE",
        "pull_request": 162, "public_base_head": BASE, "public_base_tree": "256af88cf9074e128a0305c79aa814fba29ec980",
        "main_restored": "a7aa1fec2d1241ca5a8c2c87ae62e42b9dd510e5", "tested_code_snapshot": CODE,
        "combined_source_test_pins": sources, "actual_isolated_development_commits": workers,
        "actual_owner_repair_commits": fixes, "independent_peer_method_counts": {"denominator": 3, "role": 3, "applicant": 7, "readiness": 7},
        "original_failures_and_historical_producers_preserved": True,
        "local_full_suite": {**full, "plain_OK": True, "seconds_from_original_footer": float(footer[2]),
            "JSON": pin("evidence/batch10_linux_20261018.json"), "console": pin("evidence/batch10_linux_console_20261018.log"),
            "runner": pin("evidence/batch10_linux_runner_20261018.log")},
        "new_exact_methods": pin("evidence/batch10_linux_methods_20261018.json"),
        "content_audit": pin(audit_path), "current_or_immutable_handoff_pins": len(audit["current_or_immutable_file_pins"]),
        "historical_producer_pins_are_not_current_qualification": len(audit["preserved_historical_producer_pins"]),
        "actual_runtime_dispatch": pin(runtime_name), "actual_runtime_JSON_pins": runtime_pins,
        "new_source_accounting": audit["new_source_http"], "retained_database": audit["retained_quota_database"],
        "baseline_hosted_CI": {"qualified_head_only": BASE, "run_id": 39093644927, "required_checks": "ALL_FIVE_SUCCESS_CONTENT_VERIFIED",
            "actual_windows_artifact_id": 12124438357, "full_test_count": 2231,
            "originals": "evidence/batch10_baseline_ci_20261018/"},
        "current_candidate_hosted_CI": "UNVERIFIED_NOT_YET_PUBLISHED", "later_hosted_qualification_location": "PR162 plus actual exact-new-head GitHub artifact; do not rewrite this pre-CI version",
        "default_modelconfig_hash": "e8e0a4b5e010122a7d7fa2f8d41eb9174e25a6a1a57e206cb315b1a552d0d5f2",
        "default_modelconfig_hash_basis": "AppConfig/release_manifest/default fitter source bytes unchanged, independently pinned in public32 baseline audit",
        "score_fitter_default_model_configuration_changed": False, "original_687_60second_budget_changed": False,
        "automatic_trading_allowed": False, "registry_adoptions": 0, "canonical_tickers": 687,
        "historical_Q2_instrument_coverage": 150, "current_Q3_instrument_coverage": 0,
        "healthcare_identities": 10, "NHTSA_model_identities": 3, "legal_identities": 5,
        "all_21_code_live_statuses_unchanged": True, "genuine_forecasts_registrations_labels_created": 0,
        "current_database_forecast_counts": "UNKNOWN_UNOBSERVED_DUE_TO_ORIGINAL_JOURNAL",
        "genuine_complete_HTTP200_NHTSA_negatives": 0, "full_specialist_acceptance": "0/21",
        "user_windows_end_to_end_verified": False, "historical_OOS_verified": False,
        "whole_withheld_bodies_absent": 4, "public_parent_only_safe_tree_publication_required": True, "merge_authorized": False}
    save("evidence/batch10_integration_20261018.json", proof)
    paragraph = f'''## Desátá dávka: původní rozsah a čas dokončení — 18. 10. 2026

Šest skutečných izolovaných vývojových větví a čtyři nezávislé peer kontroly jsou
spojené do explicitních CLI/UI akcí. Místní combined sada: **{count} discovered=executed,
plain OK, {footer[2]}s**, všechny failure/error/skip/expected/unexpected počty0.
Testovaný code snapshot `{CODE}`; všech {len(methods)} nových exact module/class/method
má původní ok blok v úplném logu. Neměnný pre-hosted-CI důkaz je
`evidence/batch10_integration_20261018.json`; původní JSON/console/runner a přesné
method blocks jsou `evidence/batch10_linux*_20261018.*`. Nový veřejný HEAD musí
mít vlastní hosted Linux/UI/687/Windows ZIP+originalJSON/log/digest a release gate;
pozdější výsledek patří do PR a skutečného artefaktu, nikoli přepisem tohoto důkazu.

- Původní specimen certifikátu: Class A, $0.0001 a CUSIP74624M102 přes explicitní
  vizuální anotaci. Dated/holder/shares/number jsou prázdné; vzor není vydaný
  instrument ani datovaná změna tickeru. Devět blockerů, žádný alias/Q3 adoption.
- Původní roster2017: kratší Pfizer Ireland Pharmaceuticals/Ireland,468 řádků
  v11 continuation tabulkách; přesný současný Unlimited Company applicant chybí.
  Cizí sekce nesmí dodat subsidiary-point fakt. Product001/002 a neuzavřený
  Purple Book kontejner zůstávají samostatné PARTIAL zdroje.
- DOJ dvě původní PDF:95 page stamps,3 document/page-local captions,686 field
  evidence records. Literal party variants nejsou aliasy; separate Order,
  financial issuer identity, aktuální docket/disposition a merits nejsou ověřené.
- SWKS skutečné net revenue4,086.9/4,178.0/4,772.4 USDmillion FY2025/24/23.
  Source entity/period/unit fakta zůstávají, exact denominator basis je WITHHELD;
  regex bez nalezené výhrady nikdy neprokazuje kompatibilitu. Žádné Appleamount,
  purchases, direct-channel share nebo starší as-filed srovnatelnost.
- Nezávislý proxy životopis:7 named role claims/8 career assertions s YEAR
  precision; present platí vůči2025-10-21. Donor-person row, skrytý filing date
  a completion za explicitním cutoff se odmítají. Account/alias/continuity false.
- Výchozí vypnutý readiness recorder uloží fyzické hodiny, kód a dostupné podklady.
  Skutečný Linux CLI/UI je UNSUPPORTED_PLATFORM/WAIT. Původní journal zabránil SQL
  otevření; table/count metadata jsou null/UNOBSERVED, nikoli prokázané nuly.
  Journal ani DB se nepřehrává, nekopíruje, nezakládá ani nemění. Readiness není
  same-process userWindows end-to-end důkaz a acceptance zůstává vždy false.

Čtyři skutečné peer reprodukce před opravami jsou zachované: denominator3 failure
entries ve3 methods, role3/3 failures, applicant4/7 failures a readiness6/7 failures.
Stejné assertions po opravách prošly. Tři další completion-clock opravy drží
novou znalost až na fyzickém konci po všech supporting facts; source-read cutoff
je samostatný. Původní producer/test/report bytes a dřívější kvalifikace se nemění.
Root původní9-method focus měl1 UI failure a2 fixture-contract errors; whole source,
log a diagnostic jsou v coordinator/integration_v1. Opravený10-method focus a celá
combined sada prošly; nepoužívají se nuly nebo názvy cest jako náhrada obsahu.

Nový source HTTP je přesně1: původní917915B SEC JPEG, SHA
`c05da755dd60ea349f6368eb97e74cd07d93a3a6a176bd1d427467a9f7481f45`.
Ostatní větve0 source/collector HTTP; retained replays nejsou nové odpovědi.
NHTSA live zůstává approval-blocked, původní market failed plan consumed;
žádný retry/alternate transport/refund/lease rewrite/new quotaDB.
Deset skutečných source CLI/UI akcí a dva readiness captures mají0 HTTP/SQL/adopcí.
Všech1578 starších veřejných evidencefiles/78,105,622bytes,188 původních testfiles/
2,440,692bytes a7 chráněných registrů/universe vstupů je byte-identical. Historické
source piny jsou výslovně oddělené od současných producerů. Všechna4 withheld
whole bodies zůstávají absent, žádný substitute ani private ancestry push.

Obsahově ověřený32 baseline/run39093644927 má2231plainOK na Linuxu i Windows a
všech5checks success; původní ZIP/JSON/log jsou v `evidence/batch10_baseline_ci_20261018/`.
Tato kvalifikace patří pouze32. Canonical687/Q2instrument150/currentQ3=0,
healthcare10/NHTSA3/legal5/verifiedadopt0/**fullacceptance0/21**. Všech21 code/live
stavů, default modelconfig/fitter/skóre a687/60s budget zůstává beze změny.
Žádný merge ani oznámení úplného dokončení před skutečnou akceptací všech21.

### Aktuální konkrétní fronta po desáté dávce

- identity_bulk: nezávislý datovaný ClassA/CUSIP/symbol/action bridge, LEG
  direct/indirect control, XOM July7/newCUSIP. Specimen nerozšiřuje Q2 do Q3.
- healthcare: přesná Ireland applicant/legal/product ownership kontinuita;
  původní CMS CCN/PAC/name, širší clinical object scope a durable source historie.
- regulatory: pouze offline bez NHTSA retry; FRS/program/dated ownership,
  separate original DOJ Order/current docket/financial identity/disposition.
- contracts: whole prime pages/earlier UEI/durable quota/wider awards/revisions;
  independent denominator basis, dated customer continuity a physical quantities.
- sec: independently dated person/account/legal-role/instrument identity,
  safe original current publisher IR authority. Literal YEAR claims nestačí.
- evaluation: genuine66-session market originals a publisher revision,
  skutečná uložená předpověď před nově přepočteným first-forward open, pozdější
  labels, same-process native userWindows run, broader coverage/history/OOS.
  Žádný fakeforecast/backdate/slide/activation nebo opakování consumed planu.

<!-- Starší oddíly jsou nezměněné kvalifikace vlastních verzí. -->

'''
    for name in ("SCOUT_PROGRESS.md", "SPECIALIST_COMPLETION.md", "SPECIALIST_IMPLEMENTATION_CHECKPOINT_20261001.md", "PARALLEL_SPECIALIST_WORK.md"):
        path = ROOT / name
        original = path.read_text()
        first, rest = original.split("\n", 1)
        path.write_text(first + "\n\n" + paragraph + rest, encoding="utf-8")
    print(json.dumps({"full_tests": count, "new_exact_methods": len(methods), "seconds": footer[2],
        "pre_CI_proof": pin("evidence/batch10_integration_20261018.json"), "source_test_files": len(sources)}))


if __name__ == "__main__":
    main()
