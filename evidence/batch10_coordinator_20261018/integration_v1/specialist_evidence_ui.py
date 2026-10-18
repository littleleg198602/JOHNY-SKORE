"""Explicit retained-evidence actions used by the specialist page."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path

from market_checker_app.services.captured_identity_review_service import read_review_plan
from market_checker_app.specialist_evidence_runner import (
    run_captured_identity_review, run_prime_archive_recovery, run_prospective_history_action,
    run_user_windows_readiness_capture,
)

_LABELS = {
    "CMS_PROVIDER_OWNER": "Vlastnictví nemocnice",
    "EPA_FRS_OWNER": "Vlastnictví provozovny EPA",
    "FORM4_PERSON": "Účet a osoba ve Form 4",
    "STOCK_MERGER_EXCHANGE": "Výměna instrumentu při fúzi",
    "SEC_OPERATIONAL_METRIC": "Provozní rozsah podle SEC",
    "SEC_CONTROL_INSTRUMENT": "Kontrola firmy a datovaný instrument",
    "HEALTHCARE_PRODUCT_SCOPE": "Žádost FDA a vlastnictví produktu",
    "DOJ_ENFORCEMENT_SCOPE": "Rozsah konkrétního případu DOJ",
    "SEC_SUPPLY_CHAIN_SCOPE": "Doložený dodavatelský vztah",
    "SEC_PERSON_KIND": "Datovaný údaj o fyzické osobě v SEC",
    "SEC_IR_DISCLOSURE_AUTHORITY": "Web investorů uvedený v podání SEC",
    "SEC_SYMBOL_ACTION_SCOPE": "Datovaná změna tickeru a instrumentu",
    "FDA_STRUCTURED_PRODUCT": "Strukturovaný produkt v žádosti FDA",
    "DOJ_CASE_POSTURE": "Pozdější procesní stav soudního případu",
    "SEC_PURCHASE_OBLIGATION_SCOPE": "Rozsah nákupních závazků podle SEC",
    "SEC_OWNER_INSTRUMENT_SCOPE": "Účet vlastníka a instrument v novém podání",
    "SEC_INSTRUMENT_CLASS_SCOPE": "Třída akcií a datovaný identifikátor v SEC",
    "FDA_APPLICATION_PRODUCT": "Původní produkt v konkrétní žádosti FDA",
    "NHTSA_NEGATIVE_SCOPE": "Úplnost konkrétního dotazu NHTSA",
    "SEC_SUPPLIER_CONCENTRATION": "Podíl konkrétního zákazníka v tržbách dodavatele",
    "SEC_COMPLETE_PACKET": "Úplný SEC dokument a jeho hlavička",
    "SEC_CERTIFICATE_INSTRUMENT": "Třída a CUSIP na původním certifikátu",
    "FDA_LEGAL_APPLICANT_BRIDGE": "Žadatel FDA a datovaný seznam dceřiných firem",
    "DOJ_CASE_CAPTION": "Strany a data v původním soudním dokumentu",
    "SEC_SUPPLIER_REVENUE_DENOMINATOR": "Skutečné konsolidované tržby dodavatele",
    "SEC_PERSON_ROLE_CLAIM": "Role a časové údaje v původním životopisu SEC",
}


def available_review_plans(source_root: Path) -> dict[str, str]:
    directory = source_root / "evidence" / "specialist_review_plans"
    if not directory.is_dir() or any(p.is_symlink() for p in (directory, *directory.parents)):
        return {}
    result = {}
    for path in islice(directory.iterdir(), 101):
        if len(result) >= 100:
            break
        if path.suffix != ".json" or path.is_symlink() or not path.is_file():
            continue
        name = path.relative_to(source_root).as_posix()
        try:
            plan, _ = read_review_plan(source_root, name)
        except (ValueError, OSError, TypeError):
            continue
        result[name] = _LABELS[plan["kind"]] + " — " + path.stem.replace("_", " ")
    return dict(sorted(result.items()))


def _display(st, result: dict, *, name: str) -> None:
    st.json(result, expanded=False)
    st.download_button("Stáhnout tuto kontrolu", data=json.dumps(result, ensure_ascii=False,
        indent=2, allow_nan=False), file_name=name, mime="application/json", key=name)


def render_captured_market_readiness(st, *, manifest_path: Path, archive_root: Path,
                                     tickers: list[str]) -> None:
    st.info("Ceny se načtou jen z vybraných archivů. Síťové zdroje, MT5 a cenové/metadata cache jsou pro tento běh vypnuté.")
    if st.button("Ověřit připravenost vybraných cenových archivů"):
        from market_checker_app.services.captured_market_data_provider import CapturedMarketDataProvider
        report = CapturedMarketDataProvider(manifest_path=manifest_path,
            archive_root=archive_root).readiness(tickers, include_benchmarks=True)
        if report["status"] != "READY":
            st.warning("Chybí úplné aktuální cenové vstupy. Běh zůstává zastavený před uložením předpovědí.")
        st.json(report, expanded=False)


def render_market_input_acquisition(st, *, output_dir: Path) -> None:
    st.write("**Původní cenové vstupy pro ověřovací běh**")
    st.caption("Dávka obsahuje AAPL a12 tržních a odvětvových benchmarků. Nejdřív zobraz plán; "
               "stažení spustíš samostatným tlačítkem. Výsledný manifest lze použít ve výběru cenových archivů.")
    from market_checker_app.services.captured_market_input_acquisition import CapturedMarketInputAcquisition
    root = Path(output_dir) / "market_inputs"
    context = str(root.resolve())
    enabled = st.checkbox("Povolit toto omezené stažení z Yahoo Finance", value=False,
        key="market_input_acquisition_enabled")
    if st.button("Připravit plán cenových vstupů"):
        try:
            plan = CapturedMarketInputAcquisition(archive_root=root).plan()
            st.session_state["market_input_acquisition_plan"] = {"context": context, "plan": plan}
        except (ValueError, OSError, KeyError, TypeError):
            st.session_state.pop("market_input_acquisition_plan", None)
            st.error("Pro aktuální dokončené seance nelze připravit cenový plán.")
    prepared = st.session_state.get("market_input_acquisition_plan")
    if prepared and prepared["context"] == context:
        st.json(prepared["plan"], expanded=False)
        execute = st.button("Stáhnout tuto dávku cenových vstupů", disabled=not enabled)
        if execute and enabled:
            # A click consumes this displayed selection even when the source fails.
            st.session_state.pop("market_input_acquisition_plan", None)
            try:
                result = CapturedMarketInputAcquisition(archive_root=root, enabled=True).acquire(
                    prepared["plan"], execute=True)
                st.session_state["market_input_acquisition_result"] = {"context": context, "report": result}
            except (ValueError, OSError, KeyError, TypeError):
                st.session_state.pop("market_input_acquisition_result", None)
                st.error("Dávku nelze provést. Původní pokusy zůstávají v archivu; nové stažení vyžaduje nový plán.")
    saved = st.session_state.get("market_input_acquisition_result")
    if saved and saved["context"] == context:
        if saved["report"]["status"] != "READY":
            st.warning("Vstupy nejsou úplné. Předpovědi čekají na připravený manifest.")
        _display(st, saved["report"], name="market_input_acquisition.json")


def render_prospective_source_selection(st, *, output_dir: Path) -> None:
    st.write("**Výběr zdrojů pro příští předpovědi**")
    st.caption("Vyber existující cenové a zdrojové archivy pro jednu akcii a její benchmark. "
               "Nejdřív ověř výběr; uloženou verzi lze výslovně použít při příštím ukládání předpovědí. "
               "Starší verze výběru zůstanou zachované.")
    with st.form("prospective_source_selection"):
        audit_root = st.text_input("Složka zdrojových důkazů", value=str(output_dir))
        ticker = st.text_input("Akcie pro tento výběr zdrojů", value="")
        benchmark = st.text_input("Benchmark tohoto výběru", value="SPY")
        sources = st.text_area("Vybrané původní cenové a zdrojové soubory", value="",
                               help="Jeden relativní název existujícího archivu na řádek.")
        snapshot = st.text_input("Omezit výběr na přesné ID předpovědi (volitelné)", value="")
        label_id = st.text_input("ID předpovědi s doloženým výsledkem (volitelné)", value="")
        label_path = st.text_input("Soubor doloženého výsledku (volitelné)", value="")
        prepare = st.form_submit_button("Ověřit tento výběr zdrojů")
    rows, labels = [], []
    if ticker.strip() or sources.strip():
        row = {"ticker": ticker.strip(), "benchmark_ticker": benchmark.strip(),
               "source_archive_paths": [name.strip() for name in sources.splitlines() if name.strip()]}
        if snapshot.strip():
            row["snapshot_id"] = snapshot.strip()
        rows.append(row)
    if label_id.strip() or label_path.strip():
        labels.append({"snapshot_id": label_id.strip(), "path": label_path.strip()})
    selection = {"root": audit_root, "forecast_sources": rows, "label_observations": labels}
    if prepare:
        try:
            from market_checker_app.services.prospective_source_manifest_service import ProspectiveSourceManifestService
            prepared = ProspectiveSourceManifestService(Path(audit_root)).prepare(
                forecast_sources=rows, label_observations=labels)
            st.session_state["prospective_source_selection_prepared"] = {"selection": selection, "prepared": prepared}
            st.session_state.pop("prospective_source_selection_saved", None)
        except (ValueError, OSError, KeyError, TypeError, OverflowError):
            st.session_state.pop("prospective_source_selection_prepared", None)
            st.session_state.pop("prospective_source_selection_saved", None)
            st.error("Vybrané původní zdroje, jejich čas nebo vazbu na akcii nelze ověřit.")
    cached = st.session_state.get("prospective_source_selection_prepared")
    if cached and cached["selection"] == selection:
        prepared = cached["prepared"]
        st.caption(f"Ověřeno zdrojových souborů: {len(prepared['source_pins'])}; "
                   f"výsledků: {len(prepared['label_pins'])}. Čas ověření: {prepared['prepared_at']}.")
        st.info("Tato verze obsahuje pouze právě zobrazený výběr. Při použití nahradí aktivní výběr "
                "v této složce; jeho původní verze zůstane uložená.")
        activate = st.checkbox("Použít tento výběr při příštím ukládání předpovědí", value=False)
        if st.button("Uložit tuto verzi výběru zdrojů"):
            try:
                from market_checker_app.services.prospective_source_manifest_service import ProspectiveSourceManifestService
                result = ProspectiveSourceManifestService(Path(audit_root)).publish(prepared,
                    activate=activate,
                    expected_current_sha256=prepared["current_manifest_sha256"] if activate else None)
                st.session_state["prospective_source_selection_saved"] = {"selection": selection, "report": result}
            except (ValueError, OSError, KeyError, TypeError, OverflowError):
                st.session_state.pop("prospective_source_selection_saved", None)
                st.error("Zdroje nebo aktivní výběr se změnily. Znovu ověř právě vybraný obsah.")
    saved = st.session_state.get("prospective_source_selection_saved")
    if saved and saved["selection"] == selection:
        result = saved["report"]
        st.success("Výběr zdrojů je připraven pro příští ukládání předpovědí."
                   if result["active_manifest_path"] else "Verze výběru zdrojů je uložená.")
        _display(st, result, name="prospective_source_selection.json")


def render_user_windows_readiness(st, *, db_path: Path, output_dir: Path) -> None:
    st.write("**Záznam připravenosti uživatelského Windows**")
    st.caption("Zachytí systém, dostupné cenové archivy a uloženou historii bez spuštění analýzy. "
               "Záznam připravenosti nenahrazuje dokončený ověřovací běh.")
    context = str(db_path.resolve()) + "|" + str(output_dir.resolve())
    enabled = st.checkbox("Povolit uložení tohoto záznamu připravenosti", value=False)
    with st.form("user_windows_readiness_capture"):
        subjects = st.text_input("Tickery pro záznam připravenosti", value="",
                                 help="Přesné tickery oddělené mezerou, například AAPL SPY.")
        manifest = st.text_input("Manifest cenových archivů pro záznam (volitelné)", value="")
        archive_root = st.text_input("Kořen cenových archivů pro záznam (volitelné)", value="")
        submitted = st.form_submit_button("Uložit záznam připravenosti", disabled=not enabled)
    if submitted and enabled:
        try:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            report = run_user_windows_readiness_capture(enabled=True,
                output_path=output_dir / "user_windows_readiness" / (stamp + ".json"),
                tickers=subjects.split(), db_path=db_path,
                market_manifest_path=Path(manifest.strip()) if manifest.strip() else None,
                market_archive_root=Path(archive_root.strip()) if archive_root.strip() else None)
            st.session_state["user_windows_readiness_capture"] = {"context": context, "report": report}
        except (ValueError, OSError, KeyError, TypeError):
            st.session_state.pop("user_windows_readiness_capture", None)
            st.error("Záznam nelze uložit. Ověř vybrané tickery, cesty a původní podklady.")
    cached = st.session_state.get("user_windows_readiness_capture")
    if cached and cached["context"] == context:
        st.info("Uložený záznam popisuje připravenost tohoto prostředí. Úplný uživatelský běh zůstává k doložení.")
        _display(st, cached["report"], name="user_windows_readiness_capture.json")


def render_specialist_evidence_actions(st, *, source_root: Path, db_path: Path, output_dir: Path) -> None:
    render_market_input_acquisition(st, output_dir=output_dir)
    render_user_windows_readiness(st, db_path=db_path, output_dir=output_dir)
    st.write("**Kontrola uložených primárních dokladů**")
    st.caption("Kontrola porovná přesné uložené doklady a čas jejich získání. Výsledek ukáže "
               "doloženou vazbu i chybějící důkazy; přijetí identity vyžaduje samostatné posouzení.")
    context = str(db_path.resolve()) + "|" + str(source_root.resolve())
    plans = available_review_plans(source_root)
    if plans:
        selected = st.selectbox("Uložená vazba k posouzení", list(plans), format_func=plans.get,
                                key="captured_identity_plan")
        if st.button("Zkontrolovat vybrané uložené doklady"):
            try:
                report = run_captured_identity_review(source_root=source_root, plan_name=selected,
                    archive_root=output_dir / "captured_identity_reviews")
                st.session_state["captured_identity_review"] = {"context": context, "report": report}
            except (ValueError, OSError, KeyError, TypeError):
                st.session_state.pop("captured_identity_review", None)
                st.error("Původní doklady nebo jejich časové údaje nelze ověřit.")
    else:
        st.info("Zatím není uložená žádná úplně popsaná vazba k posouzení.")
    cached = st.session_state.get("captured_identity_review")
    if cached and cached["context"] == context:
        st.caption("Poslední uložená kontrola: " + cached["report"]["recorded_at"])
        leaf = cached["report"]["review"]
        candidate = leaf.get("candidate") or {}
        claim = leaf.get("claim") or {}
        person = leaf.get("dated_person_fact") or {}
        if leaf.get("literal_specimen_class_cusip_scope_observed"):
            st.caption("Původní vzor certifikátu uvádí Class A a CUSIP " + leaf["observed_cusip"] +
                       ". Datum vydání je prázdné; návaznost na změnu tickeru čeká na doložení.")
        if leaf.get("selected_roster_facts"):
            point = leaf["selected_roster_facts"][0]
            st.caption("Seznam dceřiných firem k " + point["roster_as_of"] + " uvádí „" +
                       point["legal_entity_name_as_displayed"] + "“. Přesná vazba na současného žadatele FDA čeká na doložení.")
        if leaf.get("review_kind") == "DOJ_CASE_CAPTION":
            st.caption("Původní soudní dokumenty: " + str(leaf["supported_case_documents"]) +
                       "; samostatně doložená záhlaví se stranami: " + str(leaf["supported_caption_blocks"]) +
                       ". Současný výsledek řízení čeká na doložení.")
        if claim.get("measure") == "NET_REVENUE" and claim.get("quantities"):
            values = ", ".join(str(row["fiscal_year"]) + ": " + row["source_literal"] + " mil. USD"
                               for row in claim["quantities"])
            st.caption("Konsolidované tržby dodavatele podle tohoto podání: " + values +
                       ". Samy neurčují částku nákupů zákazníka.")
        if leaf.get("source_role_claims_supported"):
            st.caption("Životopis dokládá " + str(leaf["role_claim_count"]) +
                       " údajů o rolích. Roky nejsou přesnými dny nástupu; výraz „present“ platí vůči datu tohoto podání.")
        if leaf.get("application_facts"):
            application = leaf["application_facts"]
            st.caption(application["source_label"] + ": " + application["source_party_name"] +
                       "; produktové řádky v tomto zdroji: " + str(len(leaf.get("product_rows", []))) + ".")
        if leaf.get("registered_source_anchor_count"):
            st.caption("Datované záznamy symbolu a třídy: " + str(leaf["registered_source_anchor_count"]) +
                       ". Spojení s CUSIP čeká na doložení.")
        if claim.get("claim_role") == "CUSTOMER_REVENUE_CONCENTRATION":
            values = ", ".join(str(row["fiscal_year"]) + ": " + row["value_literal"] + "%"
                               for row in claim["quantities"])
            st.caption("Podíl zákazníka v konsolidovaných tržbách dodavatele: " + values + ".")
        if leaf.get("original_complete_packet_supported"):
            st.caption("Úplný původní packet souhlasí s XML: transakce " + str(leaf["transaction_row_count"]) +
                       ", pozice " + str(leaf["holding_row_count"]) + ".")
        if leaf.get("status") == "POSITIVE_EXACT_MODEL_YEAR":
            st.caption("Původní dotaz pro konkrétní model a rok zachytil " +
                       str(leaf["matched_rows"]) + " svolávací záznamy.")
        if claim.get("relationship_statement_supported"):
            st.caption("Dodavatelský doklad: " + claim["counterparty_name"] +
                       "; vstup: " + claim["product_or_input"] + ".")
            if claim.get("quantified_exposure", {}).get("value") is None:
                st.caption("Množství a podíl nákupů čekají na doložení.")
        if leaf.get("natural_person_fact_supported"):
            st.caption("Zachycený životopisný údaj: " + person["name_as_published"] +
                       "; datum podání: " + person["source_document_filing_day"] + ".")
        if leaf.get("recorded_case_stage") == "ALLEGATIONS_ONLY":
            st.caption("Doklad zachycuje tvrzení v konkrétní žalobě. Výsledek řízení čeká na samostatný doklad.")
        if leaf.get("control_conflicts"):
            st.caption(f"Nevyřešené rozdíly v popisu kontroly firmy: {len(leaf['control_conflicts'])}.")
        if leaf.get("issuer_disclosed_ir_website_fact_supported"):
            st.caption("Podání uvádí web pro investory: " +
                       leaf["issuer_disclosed_website_fact"]["ir_url_literal"] +
                       ". Současná autorita vydavatele a aktivace zdroje čekají na doložení.")
        if candidate.get("scope_kind") == "COMBINED_REPORT_GROUP":
            st.info("Doklad uvádí " + candidate["value_literal"] +
                    " výrobních provozů ve společně vykazované skupině. "
                    "Přiřazení tohoto množství jedné konkrétní firmě zůstává k doložení.")
        elif leaf.get("decision") == "WITHHOLD" or leaf.get("status") == "PARTIAL":
            st.info("Uložené doklady zatím neposkytují úplnou vazbu pro přijetí identity.")
        _display(st, cached["report"], name="captured_identity_review.json")

    st.write("**Obnovení zakázek z původních archivů**")
    st.caption("Obnovení vyžaduje původní úplné stránky jednoho doloženého rozsahu dotazu. "
               "Chybějící stránka nebo samotný souhrn neprokazuje úplnost zakázek.")
    with st.form("prime_archive_recovery"):
        plan_name = st.text_input("Uložený plán původního archivu zakázek", value="")
        import_targets = st.checkbox("Uložit doložené zakázky do této databáze", value=False)
        submitted = st.form_submit_button("Prověřit původní archiv zakázek")
    if submitted:
        try:
            result = run_prime_archive_recovery(db_path=db_path, source_root=source_root,
                plan_name=plan_name, archive_root=output_dir / "prime_archive_reviews",
                validate_only=not import_targets)
            st.session_state["prime_archive_recovery"] = {"context": context, "report": result}
        except (ValueError, OSError, KeyError, TypeError):
            st.session_state.pop("prime_archive_recovery", None)
            st.error("Úplný původní archiv zakázek není dostupný nebo nesouhlasí jeho důkazy.")
    cached = st.session_state.get("prime_archive_recovery")
    if cached and cached["context"] == context:
        _display(st, cached["report"], name="prime_archive_recovery.json")

    render_prospective_source_selection(st, output_dir=output_dir)

    st.write("**Průběžná historie skutečných předpovědí**")
    st.caption("Registrace zachová již uloženou předpověď před otevřením první navazující burzovní "
               "seance. Pozdější výsledek musí odpovídat jejím přesným seancím a doloženým cenám. "
               "Dodatečná registrace minulou dostupnost nevytvoří.")
    history_dir = db_path.parent / "prospective_specialist_history"
    with st.form("prospective_specialist_action"):
        action_label = st.selectbox("Úkon s historií", ["Zobrazit uloženou historii",
            "Registrovat uloženou předpověď", "Připojit doložený výsledek"])
        snapshot_id = st.text_input("Přesné ID uložené předpovědi", value="")
        source_names = st.text_area("Původní zdrojové archivy předpovědi", value="",
                                   help="Jeden relativní název souboru ve složce výstupů na řádek.")
        label_name = st.text_input("Uložené pozorování výsledku", value="",
                                   help="Relativní název souboru u této databáze.")
        history_submitted = st.form_submit_button("Provést vybraný úkon s historií")
    if history_submitted:
        action = {"Zobrazit uloženou historii": "report", "Registrovat uloženou předpověď": "register",
                  "Připojit doložený výsledek": "attach"}[action_label]
        try:
            args = {"action": action, "history_directory": history_dir}
            if action != "report":
                args.update(db_path=db_path, snapshot_id=snapshot_id)
            if action == "register":
                args.update(source_root=output_dir,
                            source_archive_names=tuple(name.strip() for name in source_names.splitlines() if name.strip()))
            if action == "attach":
                args.update(source_root=db_path.parent, label_observation_name=label_name)
            result = run_prospective_history_action(**args)
            st.session_state["prospective_specialist_report"] = {"context": context, "report": result}
        except (ValueError, OSError, KeyError, TypeError):
            st.session_state.pop("prospective_specialist_report", None)
            st.error("Předpověď, přesné seance nebo doložená dostupnost výsledku nesplňují podmínky historie.")
    cached = st.session_state.get("prospective_specialist_report")
    if cached and cached["context"] == context:
        report = cached["report"]
        if "registered_forecast_count" in report:
            st.caption(f"Zachovaných předpovědí: {report['registered_forecast_count']}; "
                       f"způsobilých skutečných výsledků: {report['eligible_real_label_count']}.")
        _display(st, cached["report"], name="prospective_specialist_history.json")
