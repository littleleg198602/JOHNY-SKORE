# COMPANY INTELLIGENCE — předávací přehled

Aktualizováno: **2026-09-15**  
Repozitář: **littleleg198602/JOHNY-SKORE**  
Zdroj pravdy pro otevřené body: `COMPANY_INTELLIGENCE_OPL.md`.

Tento dokument je krátký provozní handoff. Původní audit v OPL zůstává zmrazeným důkazem stavu při auditu; tento soubor říká, co bylo od té doby skutečně implementováno, otestováno a sloučeno.

## Stav dodaných oprav

| OPL | Stav | Main / důkaz | Co je dodané |
| --- | --- | --- | --- |
| OPL-002 | DONE | PR #107, merge `6b205e7099ce4740af958518d0f17939207578cb` | NYSE session kalendář, finite/unique close, uzavřené seance, holiday/DST/early-close validace, R01–R04 regresní testy |
| OPL-003 | DONE | PR #107, merge `6b205e7099ce4740af958518d0f17939207578cb` | target přes přesné seance, zákaz future/evaluation look-ahead, R06/R07 testy |
| OPL-005 | DONE | PR #108, merge `448d5cdeb732b80ef8cc55ee553acfa10504e316` | verzovaná cache, strict corporate-action history, split-adjusted price return bez dividend, přesný session range, retry/backoff |
| OPL-004 | DONE | PR #109, merge `167fe03d32913120be4846caa930940adf978c84` | persistentní fair queue, retry_after/cursor, 1000/run, stránky po 120, backlog metriky, automatické Windows/GitHub spuštění, 700-snapshot starvation test |
| OPL-001 | IN PROGRESS | branch `feat/opl-001-version-manifest-20260915` | release manifest, aktivní versus legacy verze, code/config/model/target/feature identita; čeká na CI a merge |

Všechny PR #107, #108 a #109 před merge prošly: **687 ticker scale gate, Streamlit gate, deterministic test suite a deterministic release gate**.

## Aktivní verze po OPL-001

Nové výsledky používají samostatnou aktivní identitu; historický v2.1 baseline zůstává zmrazený pro srovnání a staré snapshoty se nepřepisují.

- scoring: `v2.2_session_aware_consensus`
- model id: `heuristic_consensus`
- model version: `v2.2_session_aware_consensus`
- feature set: `features_v2_session_aware_target_v3`
- target: `excess_return_5d_nyse_split_price_v3`
- legacy model id: `legacy_v2.1_heuristic`
- legacy model version: `v2.1_guarded_consensus`
- release manifest schema: `release_manifest_v1`

Každý nový snapshot nese release manifest s code SHA, config hashem, model/scoring/feature/target verzí a datem sestavení. Finální ranked output je před uložením označen aktivní scoring verzí; legacy identifikátor nesmí být vydáván za aktivní scoring.

## Co je další na řadě

Nejvyšší zbývající technická priorita je:

1. **OPL-006 — srovnatelná období tržních faktorů.** Relative return musí používat stejné session endpointy a začátky; krátká historie nesmí předstírat roční faktor.
2. **OPL-007 — použitelný ranking a report.** Ranking eligibility, usable versus row coverage, per-ticker source/status contract a úplná 687tickerová traceability.
3. Poté pokračovat dalšími P1/P2 body podle pořadí v OPL.

Živá data, která potřebují nasbírat čas/OOS vzorky, zůstávají `WAIT_DATA`; absence dat se nesmí označovat za implementační chybu ani za hotový predikční důkaz.

## Co se nesmí dělat znovu

- **PR #104 je CLOSED, NOT MERGED.** Není součástí dodané implementace a nemá se zpětně započítávat.
- **AMAT identity fix se neduplikuje.** Úzká oprava identity již existuje v produkčním `live_source_smoke.py` a má vlastní pozitivní/negativní testy.
- OPL-002/003/004/005 se znovu neimplementují paralelní cestou; další změny musí navazovat na jejich současné kontrakty.
- Automatické obchodování se nezavádí; projekt zůstává analytický a rozhodnutí je ruční.

## Pravidlo pro stav DONE

Bod lze v tomto handoffu označit jako `DONE` teprve když je změna v `main` a její akceptační/regresní testy prošly release gate. `IMPLEMENTED` v branchi bez zeleného CI a merge není `DONE`. `WAIT_DATA` znamená, že kódová část může být hotová, ale statistický nebo živý důkaz ještě objektivně nemohl vzniknout.
