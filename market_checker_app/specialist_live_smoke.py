"""Bounded live evidence for public scouts; never a full-completion claim."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError

from market_checker_app.services.fda_recall_scout_service import OpenFdaRecallClient
from market_checker_app.services.fdic_bank_scout_service import (
    FdicBankFindClient, FdicBankScoutService, load_verified_banks,
)
from market_checker_app.services.nhtsa_recall_scout_service import (
    NhtsaRecallClient, NhtsaRecallScoutService, load_verified_models,
)
from market_checker_app.storage.scout_store import ScoutStore


def run(*, output_path: Path) -> dict:
    report = {"schema_version": 1,
              "started_at": datetime.now(timezone.utc).isoformat(),
              "platform_scope": "current execution host; not a Windows end-to-end acceptance",
              "full_universe_verified": False, "scoring_applied": False,
              "status": "RUNNING", "cases": {}}

    def check(name, action):
        observed = datetime.now(timezone.utc).isoformat()
        try:
            passed, detail = action()
            result = {"status": "PASS" if passed else "FAIL", "observed_at": observed, **detail}
        except HTTPError as exc:
            result = {"status": "ERROR", "observed_at": observed,
                      "error": type(exc).__name__, "http_status": exc.code}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result = {"status": "ERROR", "observed_at": observed, "error": type(exc).__name__}
        report["cases"][name] = result
        # A stopped later API cannot erase already completed checks.
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(output_path.suffix + ".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output_path)
        print(f"{name}: {result['status']}", flush=True)

    with TemporaryDirectory() as directory:
        store = ScoutStore(Path(directory) / "smoke.db")
        fdic_client = FdicBankFindClient()
        for identity in load_verified_banks():
            def bank_case(identity=identity):
                payload = fdic_client.financials(identity["cert"])
                class CapturedBank:
                    def financials(self, cert):
                        if cert != identity["cert"]:
                            raise ValueError("Unexpected certificate")
                        return payload
                service = FdicBankScoutService(store, client=CapturedBank(), identities=[identity])
                now = datetime.now(timezone.utc)
                result = service.run(as_of=now)
                replay = service.run(as_of=now)
                return (result["status"] == "OK" and result["usable_banks"] == 1
                        and result["new_findings"] >= 1 and replay["new_findings"] == 0), {
                    "source_url": identity.get("financial_name_evidence_url", identity["fdic_evidence_url"]),
                    "ticker": identity["ticker"], "cert": identity["cert"],
                    "summary": result, "replay_summary": replay, "payload": payload}
            check(f"fdic_positive_{identity['ticker']}", bank_case)

        def bank_negative():
            payload = fdic_client.financials(99999999)
            rows = payload.get("data")
            return isinstance(rows, list) and rows == [] and payload.get("meta", {}).get("total") == 0, {
                "source_url": "https://api.fdic.gov/banks/financials?filters=CERT%3A99999999&limit=2",
                "scope": "absent certificate, not an issuer-wide absence claim", "payload": payload}
        check("fdic_negative_absent_cert", bank_negative)

        fda_client = OpenFdaRecallClient()
        def fda_positive():
            payload = fda_client.recalls("drug", "Pfizer Inc.", limit=1)
            rows = payload.get("results", [])
            return any(row.get("recalling_firm") == "Pfizer Inc." and row.get("recall_number")
                       for row in rows if isinstance(row, dict)), {
                "source_url": "https://api.fda.gov/drug/enforcement.json?search=recalling_firm%3A%22Pfizer+Inc.%22&limit=1",
                "scope": "name candidate only; no product-to-issuer attribution", "payload": payload}
        check("fda_positive_drug_name", fda_positive)

        absent_name = "JOHNY SKORE NONEXISTENT ENTITY 9AE78462"
        for product in ("drug", "device", "food"):
            def fda_negative(product=product):
                payload = fda_client.recalls(product, absent_name, limit=1)
                return payload.get("results") == [] and payload.get("meta", {}).get("results", {}).get("total") == 0, {
                    "source_url": f"https://api.fda.gov/{product}/enforcement.json",
                    "query_firm": absent_name, "scope": "absent name only", "payload": payload}
            check(f"fda_negative_{product}_name", fda_negative)

        def crl_positive():
            payload = fda_client._query("transparency/crl.json", 'letter_type:"COMPLETE RESPONSE"', 1, 0)
            rows = payload.get("results", [])
            return any(row.get("letter_type") == "COMPLETE RESPONSE" and row.get("company_name")
                       and row.get("application_number") and row.get("file_name")
                       for row in rows if isinstance(row, dict)), {
                "source_url": "https://api.fda.gov/transparency/crl.json",
                "scope": "live CRL schema only; no issuer/product attribution", "payload": payload}
        check("fda_positive_crl_schema", crl_positive)

        def crl_negative():
            payload = fda_client.complete_response_letters(absent_name, limit=1)
            return payload.get("results") == [] and payload.get("meta", {}).get("results", {}).get("total") == 0, {
                "source_url": "https://api.fda.gov/transparency/crl.json", "query_firm": absent_name,
                "scope": "absent name only", "payload": payload}
        check("fda_negative_crl_name", crl_negative)

        nhtsa_client = NhtsaRecallClient()
        for model in load_verified_models():
            def model_positive(model=model):
                payload = nhtsa_client.recalls(model["make"], model["model"], model["model_year"])
                class CapturedModel:
                    def recalls(self, make, name, year):
                        return payload
                service = NhtsaRecallScoutService(store, client=CapturedModel(), models=[model])
                now = datetime.now(timezone.utc)
                result = service.run(as_of=now)
                replay = service.run(as_of=now)
                return (result["status"] == "OK" and result["new_findings"] >= 1
                        and replay["new_findings"] == 0), {
                    "source_url": "https://api.nhtsa.gov/recalls/recallsByVehicle",
                    "make": model["make"], "model": model["model"], "year": model["model_year"],
                    "summary": result, "replay_summary": replay, "payload": payload}
            check(f"nhtsa_positive_{model['ticker']}_{model['model_year']}", model_positive)

        def model_negative():
            try:
                payload = nhtsa_client.recalls("JOHNY_SKORE_NONEXISTENT", "NONEXISTENT", 2026)
            except HTTPError as exc:
                if exc.code != 400:
                    raise
                return True, {"source_url": "https://api.nhtsa.gov/recalls/recallsByVehicle",
                              "scope": "invalid make/model is rejected, not interpreted as no recalls",
                              "http_status": 400}
            return payload.get("Count") == 0 and payload.get("results") == [], {
                "source_url": "https://api.nhtsa.gov/recalls/recallsByVehicle",
                "scope": "absent make/model only", "payload": payload}
        check("nhtsa_negative_absent_model", model_negative)

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["status"] = "PASS" if all(row["status"] == "PASS" for row in report["cases"].values()) else "PARTIAL"
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output_path)
    return report


def main():
    parser = argparse.ArgumentParser(description="Omezené živé ověření veřejných specialistů.")
    parser.add_argument("--output-path", type=Path, default=Path("outputs/specialist_live_smoke_latest.json"))
    report = run(output_path=parser.parse_args().output_path)
    print(f"Public specialist smoke: {report['status']}")
    raise SystemExit(0 if report["status"] == "PASS" else 2)


if __name__ == "__main__":
    main()
