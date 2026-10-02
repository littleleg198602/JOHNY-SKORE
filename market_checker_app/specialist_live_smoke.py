"""Bounded live evidence for public scouts; never a full-completion claim."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError

from market_checker_app.services.fda_recall_scout_service import OpenFdaRecallClient
from market_checker_app.services.doj_scout_service import (
    DOJ_URL, DojPressReleaseClient, parse_doj_candidate,
)
from market_checker_app.services.fdic_bank_scout_service import (
    FdicBankFindClient, FdicBankScoutService, load_verified_banks,
)
from market_checker_app.services.nhtsa_recall_scout_service import (
    NhtsaRecallClient, NhtsaRecallScoutService, load_verified_models,
)
from market_checker_app.services.healthcare_scout_service import (
    CmsHospitalOwnerClient, ClinicalTrialsClient, _candidate,
)
from market_checker_app.services.ofac_scout_service import ALT_URL, OfacSdnClient, SDN_URL
from market_checker_app.services.usaspending_scout_service import (
    API_URL as USA_AWARDS_URL, UsaSpendingApiClient, UsaSpendingScoutService,
    load_verified_identities,
)
from market_checker_app.services.usaspending_recipient_discovery import (
    RECIPIENT_URL, UsaSpendingRecipientClient,
)
from market_checker_app.storage.scout_store import ScoutStore


PUBLIC_SOURCES = ("fdic", "fda", "nhtsa", "cms", "clinicaltrials", "ofac", "usaspending", "doj")


def run(*, output_path: Path, sources: tuple[str, ...] = PUBLIC_SOURCES,
        fdic_tickers: tuple[str, ...] | None = None) -> dict:
    if not sources or set(sources) - set(PUBLIC_SOURCES):
        raise ValueError("Unsupported live sources")
    bank_identities = load_verified_banks()
    if fdic_tickers is not None:
        if ("fdic" not in sources or not fdic_tickers
                or set(fdic_tickers) - {entry["ticker"] for entry in bank_identities}):
            raise ValueError("FDIC smoke selection needs known manifest tickers and fdic source")
        bank_identities = [entry for entry in bank_identities if entry["ticker"] in fdic_tickers]
    report = {"schema_version": 1,
              "started_at": datetime.now(timezone.utc).isoformat(),
              "platform_scope": "current execution host; not a Windows end-to-end acceptance",
              "full_universe_verified": False, "scoring_applied": False,
              "status": "RUNNING", "sources": list(sources), "cases": {}}
    if fdic_tickers is not None:
        report["fdic_selected_tickers"] = sorted(set(fdic_tickers))

    def check(name, action):
        if name.split("_", 1)[0] not in sources:
            return
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
        for identity in bank_identities:
            def bank_case(identity=identity):
                payload = fdic_client.financials(identity["cert"])
                class CapturedBank:
                    def financials(self, cert):
                        if cert != identity["cert"]:
                            raise ValueError("Unexpected certificate")
                        return payload
                # Captured replays also reserve quota. The smoke is bounded by
                # the selected manifest, while only the initial capture does I/O.
                service = FdicBankScoutService(store, client=CapturedBank(), identities=[identity],
                                               daily_request_budget=max(10, 2 * len(bank_identities)))
                now = datetime.now(timezone.utc)
                result = service.run(as_of=now)
                replay = service.run(as_of=now, recheck=True)
                return (result["status"] == "OK" and result["usable_banks"] == 1
                        and result["new_findings"] >= 1 and replay["checked_banks"] == 1
                        and replay["usable_banks"] == 1 and replay["new_findings"] == 0), {
                    "source_url": identity.get("financial_name_evidence_url", identity["fdic_evidence_url"]),
                    "ticker": identity["ticker"], "cert": identity["cert"],
                    "identity": identity,
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
        doj_client = DojPressReleaseClient()
        def doj_positive():
            rows, total = doj_client.page("Google", size=2, page=0)
            clock = datetime.now(timezone.utc)
            accepted = [candidate for row in rows
                        if (candidate := parse_doj_candidate(row, "Google", clock)) is not None]
            return bool(accepted), {"source_url": DOJ_URL, "query_name": "Google",
                                    "query_page": 0, "page_size": 2, "total_query_results": total,
                                    "scope": "publisher title-query/parser only; no watchlist issuer assigned",
                                    "complete_issuer_legal_risk_coverage": False,
                                    "payload_rows": rows, "parsed_candidates": accepted}
        check("doj_positive_title_schema", doj_positive)

        def doj_negative():
            rows, total = doj_client.page(absent_name, size=2, page=0)
            return rows == [] and total == 0, {"source_url": DOJ_URL, "query_name": absent_name,
                                                "query_page": 0, "page_size": 2,
                                                "total_query_results": total, "payload_rows": rows,
                                                "scope": "absent exact title query, not issuer-wide absence"}
        check("doj_negative_title_name", doj_negative)

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

        for client, positive_name in ((CmsHospitalOwnerClient(), "LIFEPOINT HOLDINGS 2 LLC"),
                                      (ClinicalTrialsClient(), "Pfizer")):
            def healthcare_positive(client=client, name=positive_name):
                rows, cursor = client.page(name, size=2, cursor=None)
                clock = datetime.now(timezone.utc)
                accepted = [candidate for row in rows
                            if (candidate := _candidate(client.source, row, name, clock)) is not None]
                return bool(accepted), {"source_url": client.source_url, "query_name": name,
                                       "scope": "publisher schema and exact name; no watchlist issuer assigned",
                                       "next_cursor_present": cursor is not None,
                                       "parsed_candidates": accepted}
            check(f"{client.source}_positive_name_schema", healthcare_positive)

            def healthcare_negative(client=client):
                rows, cursor = client.page(absent_name, size=2, cursor=None)
                return rows == [] and cursor is None, {"source_url": client.source_url,
                                                        "query_name": absent_name,
                                                        "scope": "absent exact name, not issuer-wide absence",
                                                        "returned_rows": len(rows)}
            check(f"{client.source}_negative_name", healthcare_negative)

        snapshot_holder = []
        def ofac_snapshot():
            if not snapshot_holder:
                snapshot_holder.append(OfacSdnClient().snapshot())
            return snapshot_holder[0]

        def ofac_positive():
            snapshot = ofac_snapshot()
            matches = [entity for entity in snapshot.entities if entity["name"] == "AEROCARIBBEAN AIRLINES"]
            return bool(matches), {"source_url": SDN_URL, "matched_entities": matches,
                                   "snapshot_sha256": snapshot.content_sha256,
                                   "total_rows": snapshot.total_rows, "entity_rows": len(snapshot.entities),
                                   "scope": "SDN publisher parser only; no watchlist issuer assigned"}
        check("ofac_positive_entity_schema", ofac_positive)

        def ofac_alias_positive():
            snapshot = ofac_snapshot()
            matches = [entity for entity in snapshot.entities
                       if entity["sdn_id"] == "36" and entity["name"] == "AEROCARIBBEAN AIRLINES"
                       and any(alias["alias_id"] == "12" and alias["name"] == "AERO-CARIBBEAN"
                               and alias["alias_type"] == "aka" for alias in entity.get("aliases", []))]
            return bool(matches) and bool(snapshot.alias_content_sha256), {
                "source_url": SDN_URL, "alias_source_url": ALT_URL,
                "matched_entities": matches, "query_name": "AERO-CARIBBEAN",
                "snapshot_sha256": snapshot.content_sha256,
                "alias_snapshot_sha256": snapshot.alias_content_sha256,
                "alias_total_rows": snapshot.alias_total_rows,
                "scope": "Publisher ENT_NUM join and exact alternate-name positive; no watchlist issuer assigned"}
        check("ofac_positive_alias_join", ofac_alias_positive)

        def ofac_negative():
            snapshot = ofac_snapshot()
            matches = [entity for entity in snapshot.entities
                       if entity["name"].strip().casefold() == absent_name.casefold()
                       or any(alias["name"].strip().casefold() == absent_name.casefold()
                              for alias in entity.get("aliases", []))]
            return matches == [], {"source_url": SDN_URL, "query_name": absent_name,
                                   "snapshot_sha256": snapshot.content_sha256,
                                   "alias_snapshot_sha256": snapshot.alias_content_sha256,
                                   "scope": "absent exact primary/ALT entity name; no sanctions clearance"}
        check("ofac_negative_name", ofac_negative)

        awards_client = UsaSpendingApiClient()
        def contracts_positive():
            identity = next(e for e in load_verified_identities() if e["uei"] == "UTJWTSLMFNG4")
            now = datetime.now(timezone.utc)
            payload = awards_client.awards(identity["uei"], start=now.date() - timedelta(days=730),
                                           end=now.date(), page=1)
            class CapturedAwards:
                def awards(self, uei, *, start, end, page):
                    if uei != identity["uei"] or page != 1:
                        raise ValueError("Unexpected live replay query")
                    return payload
            service = UsaSpendingScoutService(store, client=CapturedAwards(),
                                               identities=[identity], max_pages=1)
            result = service.run(as_of=now, universe={identity["ticker"]})
            replay = service.run(as_of=now, universe={identity["ticker"]})
            has_next = payload["page_metadata"]["hasNext"]
            passed = (result["new_findings"] > 0 and replay["new_findings"] == 0
                      and result["status"] == ("PARTIAL" if has_next else "OK"))
            return passed, {"source_url": USA_AWARDS_URL, "ticker": identity["ticker"],
                            "uei": identity["uei"], "summary": result, "replay_summary": replay,
                            "scope": "documented Sikorsky UEI pilot; one page, not all LMT awards",
                            "payload": payload}
        check("usaspending_positive_contract_uei", contracts_positive)

        def contracts_negative():
            now = datetime.now(timezone.utc).date()
            absent_uei = "Z9Q8W7E6R5T4"
            payload = awards_client.awards(absent_uei, start=now - timedelta(days=730), end=now, page=1)
            return payload.get("results") == [] and payload.get("page_metadata", {}).get("hasNext") is False, {
                "source_url": USA_AWARDS_URL, "query_uei": absent_uei,
                "scope": "absent UEI within two-year bounded search, not issuer-wide absence", "payload": payload}
        check("usaspending_negative_contract_uei", contracts_negative)

        recipient_client = UsaSpendingRecipientClient()
        def recipient_positive():
            name = "SIKORSKY AIRCRAFT CORPORATION"
            payload = recipient_client.recipients(name, page=1)
            matches = [r for r in payload["results"]
                       if r.get("name") == name and r.get("uei") == "UTJWTSLMFNG4" and r.get("id")]
            return bool(matches) and isinstance(payload["page_metadata"]["hasNext"], bool), {
                "source_url": RECIPIENT_URL, "query_name": name,
                "scope": "publisher schema and exact name/UEI only; no new issuer identity inferred",
                "payload": payload}
        check("usaspending_positive_recipient_schema", recipient_positive)

        def recipient_negative():
            payload = recipient_client.recipients(absent_name, page=1)
            return payload.get("results") == [] and payload.get("page_metadata", {}).get("hasNext") is False, {
                "source_url": RECIPIENT_URL, "query_name": absent_name,
                "scope": "absent name only, not absence of government contracts", "payload": payload}
        check("usaspending_negative_recipient_name", recipient_negative)

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["status"] = "PASS" if all(row["status"] == "PASS" for row in report["cases"].values()) else "PARTIAL"
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output_path)
    return report


def main():
    parser = argparse.ArgumentParser(description="Omezené živé ověření veřejných specialistů.")
    parser.add_argument("--output-path", type=Path, default=Path("outputs/specialist_live_smoke_latest.json"))
    parser.add_argument("--sources", nargs="+", choices=PUBLIC_SOURCES, default=PUBLIC_SOURCES)
    parser.add_argument("--fdic-tickers", nargs="+", help="Only these dated FDIC manifest tickers; absent CERT negative remains enabled")
    args = parser.parse_args()
    report = run(output_path=args.output_path, sources=tuple(args.sources),
                 fdic_tickers=tuple(args.fdic_tickers) if args.fdic_tickers is not None else None)
    print(f"Public specialist smoke: {report['status']}")
    raise SystemExit(0 if report["status"] == "PASS" else 2)


if __name__ == "__main__":
    main()
