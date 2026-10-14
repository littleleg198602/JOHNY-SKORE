"""Explicit retained-evidence actions used by the specialist page."""
from __future__ import annotations

import json
from itertools import islice
from pathlib import Path

from market_checker_app.services.captured_identity_review_service import read_review_plan
from market_checker_app.specialist_evidence_runner import (
    run_captured_identity_review, run_prime_archive_recovery, run_prospective_history_action,
)

_LABELS = {
    "CMS_PROVIDER_OWNER": "Vlastnictví nemocnice",
    "EPA_FRS_OWNER": "Vlastnictví provozovny EPA",
    "FORM4_PERSON": "Účet a osoba ve Form 4",
    "STOCK_MERGER_EXCHANGE": "Výměna instrumentu při fúzi",
    "SEC_OPERATIONAL_METRIC": "Provozní rozsah podle SEC",
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


def render_specialist_evidence_actions(st, *, source_root: Path, db_path: Path, output_dir: Path) -> None:
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
