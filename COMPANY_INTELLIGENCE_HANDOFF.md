# COMPANY INTELLIGENCE — předávací přehled

Aktualizováno: **2026-09-16**
Repozitář: **littleleg198602/JOHNY-SKORE**  
Zdroj pravdy pro otevřené body: `COMPANY_INTELLIGENCE_OPL.md`.

Tento dokument je krátký provozní handoff. Původní audit v OPL zůstává zmrazeným důkazem stavu při auditu; tento soubor říká, co bylo od té doby skutečně implementováno, otestováno a sloučeno.

## Stav dodaných oprav

| OPL | Stav | Main / důkaz | Co je dodané |
| --- | --- | --- | --- |
| OPL-002 | DONE | PR #111, merge `5f3a897` | NYSE session kalendář, finite/unique close, uzavřené seance a lookback coverage; auditní regresní oprava je v main. |
| OPL-003 | DONE | PR #111, merge `5f3a897` | target přes přesné seance a zákaz future/evaluation look-ahead; resolver labelů je znovu importovatelný a testovaný. |
| OPL-005 | DONE | PR #108, merge `448d5cdeb732b80ef8cc55ee553acfa10504e316` | verzovaná cache, strict corporate-action history, split-adjusted price return bez dividend, přesný session range, retry/backoff |
| OPL-004 | DONE | PR #109, merge `167fe03d32913120be4846caa930940adf978c84` | persistentní fair queue, retry_after/cursor, 1000/run, stránky po 120, backlog metriky, automatické Windows/GitHub spuštění, 700-snapshot starvation test |
| OPL-001 | DONE | merge `97729f1` (PR #110) | release manifest, aktivní versus legacy verze, code/config/model/target/feature identita |
| OPL-007 | CODE COMPLETE / live report pending | PR #111 + follow-up branch | způsobilost rankingu, neprůhledné řádky bez pořadí, per-ticker status ceny a technické vrstvy, auditovatelné exportní pole; nově atomická per-ticker traceability pipeline → SQLite → JSON → UI (requested/attempted/usable/partial/failed/not-attempted), testovaná na 687 tickerů. Zbývá jedině skutečný produkční 687tickerový report. |
| OPL-008 | DONE | PR #115, merge `3eab604` | normalizované a per-ticker dohledatelné degradace zdrojů: rate limit, 403/401, timeout, parser, identita, konfigurace, data a retry; evidence se ukládá atomicky do SQLite, shadow JSON a UI. Korekce zahrnuje agentní chyby, `STALE_DATA`, skutečný per-ticker backoff a počet/čas retry. |
| OPL-009 | CODE COMPLETE / live report pending | PR #116, merge `9d08c94` + integrity follow-up | live smoke ukládá pro všech 687 tickerů explicitní identity stav `RESOLVED`, `QUARANTINED` nebo `UNRESOLVED` s reason code a detail. Přesných 36 manifestových identit se ověřuje registry; chybějící záznamy se nevymýšlejí a zůstávají v karanténě. Kontrakt je v atomickém JSON i UI. Zbývá skutečný produkční smoke report. |
| OPL-010 | CODE COMPLETE / live evidence pending | integrity follow-up branch | RSS odděluje feed transport od původního vydavatele, exact i přepsané titulky slučuje do konzervativních kanonických eventů, používá tokenovou ticker relevanci a drží `no news` jako neutrální missingness. Změna má vlastní release/feature verzi; živý důkaz zůstává pending. |
| OPL-011 | CODE COMPLETE / live report pending | PR #118, merge `59bd311` | SEC raw facts se převádějí do neměnných, verzovaných snapshotů pro správné `QUARTER`/`ANNUAL`/`YTD`/`INSTANT` období. Snapshot eviduje source fact IDs, accession, URL, missingness a lineage revizí; restatement po cutoffu nemění historický snapshot. Filing date se bezpečně považuje za dostupný až následující UTC den, protože zdroj neposkytuje accepted-at. Faktory zatím nemění scoring. |
| OPL-012 | CODE COMPLETE / OOS evidence pending | shadow candidate model layer | Neměnný `momentum_relative_baseline v1` a deterministický `pit_logistic_regression v1` se učí pouze z dříve uzavřených point-in-time snapshotů stejného targetu. Artefakt nese interval, snapshot IDs, imputaci, škálování a koeficienty; při nedostatečné historii je výsledek `INSUFFICIENT_DATA`. Kandidát se exportuje samostatně do SQLite/weekly JSON a nikdy nemění produkční ranking ani ruční rozhodnutí. |
| OPL-013 | CODE COMPLETE / OOS evidence pending | walk-forward evaluation layer | `candidate_walk_forward_evaluation_v1` pro každý prediction týden znovu trénuje jen na tehdy známých kompatibilních labelech, vyřadí překrývající se ticker/label horizonty a porovnává momentum baseline s kandidátem. Report ukládá Brier score/kalibraci, ranking IC, top-decile excess return a directional accuracy celkem, po týdnech a sektorech; intervaly bootstrapuje přes týdny. Výsledek pod 200 vzorky či 12 týdny je viditelně `INSUFFICIENT_DATA`, nikdy aktivace modelu. |
| OPL-015 | CODE COMPLETE / live evidence pending | supplier/customer evidence layer | Každá Stage 3 vazba má orientaci `protistrana → firma` nebo `firma → protistrana`, identitu/anonymitu partnera, produkt/vstup, zemi, období, kontext koncentrace/single-source/disruption, citaci, zdroj, úroveň důkazu, čerstvost a explicitní missingness. Anonymní customer se nikdy nespáruje s firmou. Supply a customer se zobrazují odděleně; evidence nikdy nemění score ani ranking. |
| OPL-017 | CODE COMPLETE / live evidence pending | resource margin scenario layer | Materiál/energie může nést datované cenové body s jednotkou, měnou, URL a `available_at`; bod po cutoffu se vyřadí. Pouze při doloženém nákladovém podílu, hedge, fixaci, pass-through a explicitním scénáři se vypočte reprodukovatelná citlivost do marže. Jinak `INSUFFICIENT_DATA`; scénář není cenová predikce, nemění score ani ranking. |
| OPL-018 | CODE COMPLETE / live evidence pending | macro/sector vintage layer | Malý makro report pracuje s VIX, 10Y, křivkou, dolarem, ropou, CPI, průmyslem a sektorovou relativní silou. Každé pozorování má reference period, `available_at` a `vintage_at`; budoucí revize se v replayi vyřadí. Režim je report-only, při chybějící sadě `INSUFFICIENT_DATA`, nikdy nemění firmní ranking nebo rozhodnutí. |

Všechny PR #107, #108 a #109 před merge prošly: **687 ticker scale gate, Streamlit gate, deterministic test suite a deterministic release gate**.

## Aktivní verze po OPL-001

Nové výsledky používají samostatnou aktivní identitu; historický v2.1 baseline zůstává zmrazený pro srovnání a staré snapshoty se nepřepisují.

- scoring: `v2.3_canonical_news_consensus`
- model id: `heuristic_consensus`
- model version: `v2.3_canonical_news_consensus`
- feature set: `features_v3_canonical_news_target_v3`
- target: `excess_return_5d_nyse_split_price_v3`
- legacy model id: `legacy_v2.1_heuristic`
- legacy model version: `v2.1_guarded_consensus`
- release manifest schema: `release_manifest_v1`

Každý nový snapshot nese release manifest s code SHA, config hashem, model/scoring/feature/target verzí a datem sestavení. Finální ranked output je před uložením označen aktivní scoring verzí; legacy identifikátor nesmí být vydáván za aktivní scoring.

## Co je další na řadě

Nejvyšší zbývající technická priorita je:

1. OPL-016: zhodnotit zdraví jen identifikovaných protistran z OPL-015, bez fiktivního ratingu soukromých firem.
2. Provést skutečné OPL-007/008/009/010/011/012/013/015/017/018 provozní a OOS reporty na aktuální verzi; absence reportu je `WAIT_DATA`, nikoli důvod vymýšlet výsledek.

Živá data, která potřebují nasbírat čas/OOS vzorky, zůstávají `WAIT_DATA`; absence dat se nesmí označovat za implementační chybu ani za hotový predikční důkaz.

## Co se nesmí dělat znovu

- **PR #104 je CLOSED, NOT MERGED.** Není součástí dodané implementace a nemá se zpětně započítávat.
- **AMAT identity fix se neduplikuje.** Úzká oprava identity již existuje v produkčním `live_source_smoke.py` a má vlastní pozitivní/negativní testy.
- OPL-002/003/004/005 se znovu neimplementují paralelní cestou; další změny musí navazovat na jejich současné kontrakty.
- Automatické obchodování se nezavádí; projekt zůstává analytický a rozhodnutí je ruční.

## Pravidlo pro stav DONE

Bod lze v tomto handoffu označit jako `DONE` teprve když je změna v `main` a její akceptační/regresní testy prošly release gate. `IMPLEMENTED` v branchi bez zeleného CI a merge není `DONE`. `WAIT_DATA` znamená, že kódová část může být hotová, ale statistický nebo živý důkaz ještě objektivně nemohl vzniknout.
