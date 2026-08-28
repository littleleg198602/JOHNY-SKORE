# Company Intelligence / Forensic – changelog oprav

## 2026-08-28 – lokální audit výstupu a Streamlit dashboardu

- Lokální weekly shadow běh vytvořil očekávané artefakty
  `outputs/weekly_shadow_latest.json` a `outputs/market_checker_history.db`.
  Běh obsahuje 36/36 tickerů, 36 rozhodnutí, `pipeline_status=SUCCESS`,
  `quality_gate_decision=PASS`, 0 chyb a zůstává bezpečně v shadow režimu.
- Ověřený problém byl ve zobrazovací vrstvě: Streamlit ukazoval správnou cestu
  k databázi, ale po otevření pouze konfigurační stránku a existující
  `weekly_shadow_latest.json` automaticky nenačítal.
- `OPS-804` je nyní implementovaný v PR #84. Streamlit při startu načte
  poslední shadow JSON, zobrazí stav pipeline, QualityGate, aktivaci, live-lock
  a tabulku všech tickerových rozhodnutí. Chybějící nebo poškozený JSON ohlásí
  srozumitelně a zobrazení nespouští novou analýzu.
- Důkaz: CI run
  [33164344396](https://github.com/littleleg198602/JOHNY-SKORE/actions/runs/33164344396)
  skončil `success`; deterministic test suite, scale agent i Streamlit UI agent
  prošly.
- Oprava lokálního SEC User-Agentu z PR #83 je již sloučená do `main`;
  launcher se po prvním zadání e-mailu ptá znovu až při chybějící hodnotě.

Tento soubor je chronologický, lidsky čitelný log implementačních změn.
Aktuální stav a návaznost úkolů zůstává v `COMPANY_INTELLIGENCE_TASKS.md`; Git
historie je technický zdroj pravdy pro přesný diff každého commitu.

Každý další implementační PR má doplnit datum, rozsah, bezpečnostní dopad,
ověřovací testy a otevřené provozní podmínky. Historické záznamy se nemažou.

## 2026-08-21 – první úspěšný živý production-shadow pilot v PR #79

- Produkční universe je nově explicitně uložený v
  `market_checker_app/production_watchlist.txt`: 687 unikátních tickerů ve
  stejném pořadí jako export `market_checker_20260818_213623.xlsx`. Kontrolní
  porovnání s předanou SQLite historií potvrdilo shodnou množinu bez chybějících
  nebo přebývajících tickerů.
- Weekly workflow vybírá 36tickerový pilot z tohoto souboru; nepoužívá už
  náhradní ručně zapsaný seznam. Deset přesných SEC identit je součástí runtime
  manifestu a každý živý canary ticker musí být členem produkčního universe.
- První pokus odhalil, že původní externí canary Kerrisdale vrací z GitHub
  Actions HTTP 403. Canary byl proto zdrojovaně přesunut na report Spruce Point
  o MSCI a jeho konfigurace je načítaná ze stejného runtime manifestu jako
  agentní pipeline, nikoliv z hardcoded hodnoty ve smoke testu.
- Další pokus odhalil kolizi instrumentů GOOG/GOOGL, které sdílejí jeden CIK a
  SEC accession. Dokumentové a canonical identity jsou proto nově scoped také
  tickerem/instrumentem. Form 3/4/5 navíc odstraňuje SEC XSL prefix a stahuje
  přímo vlastnické XML.
- Živý GitHub Actions run
  [32490389851](https://github.com/littleleg198602/JOHNY-SKORE/actions/runs/32490389851)
  následně skončil `success`: 36/36 tickerů, Yahoo, Google News RSS, SEC EDGAR i
  externí short-report canary prošly, QualityGate skončil `PASS`, artefakt
  obsahuje SQLite DB a oba auditní JSON soubory.
- Pipeline zůstala `PARTIAL` pouze kvůli chybějícím bezpečně extrahovaným
  tvrzením z canary reportu a jedné firmě s nízkým pokrytím forenzních metrik.
  Nejde o zdrojovou nebo databázovou chybu.
- Bezpečnostní stav je správný: 36 rozhodnutí, 0 aplikovaných změn,
  `activation_state=INSUFFICIENT_DATA`, `accuracy_improvement_proven=false` a
  `live_buy_sell_enabled=false`.
- Navazující run
  [32491052650](https://github.com/littleleg198602/JOHNY-SKORE/actions/runs/32491052650)
  také skončil `success`. Kroky vyhledání, stažení a obnovy předchozího
  artefaktu všechny prošly před novým Stage 4 během.
- Obnovená DB obsahuje pipeline runy 1–3 a dva úspěšné orchestrační běhy 2–3.
  Dokumentové observations vzrostly z 673 na 1 347 a resolverové observations
  na 1 197; nebyla nalezena žádná duplicitní observation se stejným
  orchestration/agent/document klíčem. GOOG a GOOGL mají společnou právní
  entitu, ale samostatné instrumentové dokumenty a canonical události.
- `ENTITY-101` a `OPS-801` tím splnily všechna svá akceptační kritéria. Aktuální
  součet roadmapy je 15 `DONE`, 9 `PARTIAL`, 11 `TODO` a 1 `BLOCKED` z 36
  úkolů. Evropské regionální canary, ochrana `main` a statistický OOS důkaz
  zůstávají otevřené.
- Dočasný branch `push` trigger použitý pouze k živému ověření byl po druhém
  úspěšném běhu odstraněn; produkční workflow se znovu spouští jen plánem nebo
  ručně přes `workflow_dispatch`.

## 2026-08-21 – post-merge audit po PR #77

- Auditovaný základ: `main` commit `5c04845` po sloučení PR #77.
- PR #77 je skutečně sloučený; jeho deterministický GitHub Actions run
  `32478364244` skončil `success`.
- Strom sloučeného `main` prošel `compileall` a celou lokální sadou 169/169
  deterministických testů.
- Opakovaně potvrzený stav roadmapy: 13 úkolů `DONE`, 10 `PARTIAL`, 11 `TODO`
  a 2 `BLOCKED` z celkových 36 sledovaných úkolů.
- Workflow `Market Checker weekly production shadow` má stále 0 běhů; živý
  canary, obnova SQLite mezi dvěma běhy a skutečná OOS historie tedy nejsou
  prokázané.
- Větev `main` zůstává nechráněná a nemá povinný status check.
- Audit nemění žádný obchodní signál ani policy. Zvýšení přesnosti predikce
  stále není statisticky prokázané.

## 2026-08-21 – dokončení kódových oprav 1–4

- Větev: `agent/company-intelligence-audit-status`
- Pull request: PR #77 (v době implementace draft, následně sloučen do `main`)
- Režim: výhradně shadow; ostré BUY/SELL nebyly povoleny

### 1. Runtime identity manifest a fail-closed identita

- Přidán striktní parser identity manifestu pro ticker, právní název, CIK,
  ISIN, LEI, MIC, zemi, burzu a zdrojovou HTTPS URL.
- Manifest je zapojen do `AgentRuntimeSettings`, Streamlit UI,
  `autonomous_runtime.json` a bezobslužného weekly runneru.
- Není povolené name-only ani fuzzy přiřazení. Záznam vyžaduje alespoň jeden
  přesný CIK/ISIN/LEI.
- Identity-dependent evropský nebo primární regulační zdroj bez manifestu se
  odmítne ještě před síťovým požadavkem.
- `EntityRegistryAgent` publikuje seznam nevyřešených tickerů a QualityGate je
  pro identity-dependent komponenty odmítá kódem `unresolved_identity`.
- Source-health audit nově ukazuje počet nakonfigurovaných, nevyřešených a
  konfliktních identit.

### 2. Evropské filingy v UI a weekly runtime

- Direct-URL evropský ingest je zapojen do stejné běžné konfigurace jako SEC.
- Přidán bezpečný RSS/Atom discovery klient s limitem velikosti, kontrolou MIME,
  kontrolou původního i finálního hostu a point-in-time datem.
- Položka feedu je přijata pouze při přesném výskytu nakonfigurovaného LEI nebo
  ISIN; podobnost názvu firmy se nepoužívá.
- UI a weekly runner podporují přímé dokumenty, feedy a explicitní allowlist
  lokálních burz/IR hostů.
- Evropský dokument bez shody s registry entitou je odmítnut nebo při konfliktu
  identifikátoru umístěn do karantény.

### 3. Globální canonical event a preference zdroje

- Přidán `SourceResolutionAgent`, který běží po dokumentových producentech,
  zachová všechny důkazy a deterministicky vybere preferovaný dokument.
- SEC a evropské výkazy bez ručního klíče používají společný canonical klíč:
  právní/issuer identita + rodina dokumentu + reportované období.
- Přidány kontrakty `DocumentSourceResolution` a pole
  `DocumentRecord.canonical_event_key`.
- SQLite ukládá canonical klíč do dokumentů a observations a přidává tabulky
  `document_source_resolutions` a
  `document_source_resolution_observations`, včetně předchozí preference.
- QualityGate kontroluje unikátnost resolveru, úplnost skupiny, existenci všech
  dokumentů, shodu tickeru/právní entity a správného vítěze podle hierarchie.
- `DecisionAgent` u canonical události používá pouze dokument, který globální
  resolver označil jako preferovaný.

### 4. RegulatoryContractAgent → DecisionAgent

- Regulační manifest podporuje volitelné sloupce `source_type`,
  `source_authority` a `canonical_event_key`.
- Starý desetisloupcový formát a automatický RSS discovery zůstávají záměrně
  `media_article`, takže je nelze omylem povýšit na primární potvrzení.
- Primární regulační/burzovní zdroj dostane odpovídající `source_priority`, ale
  pouze s vyřešenou právní identitou emitenta.
- Právní, issuer a instrument identita se propisují do dokumentu i regulační
  události a ukládají do SQLite.
- `DecisionAgent` přijme jen dostatečně jistou, point-in-time, primárně
  potvrzenou událost se shodnou právní identitou a preferovaným dokumentem.
- Výsledkem může být pouze zachování původní akce nebo shadow návrh
  `NO_TRADE`; nová vrstva neumí vytvořit ani obrátit BUY/SELL.

### Kontrolní mechanismy a testy

- `python -m compileall -q market_checker_app tests`: PASS.
- `git diff --check`: PASS.
- Cílené integrační a bezpečnostní testy: 50/50 PASS.
- Celý deterministický test suite ve čistém prostředí s uzamčenými verzemi
  závislostí: 169/169 PASS.
- Součástí testů jsou negativní scénáře: chybějící identita před sítí,
  nevyřešená identita v QualityGate, nesouvisející položka evropského feedu,
  podvržená globální preference, media-only legacy regulační zdroj a zákaz
  aplikace změny do ostré predikce.

### Změněné hlavní části

- Runtime/UI: `app.py`, `agent_runtime_service.py`, `weekly_shadow_runner.py`,
  `autonomous_runtime.json`.
- Identity a evropské zdroje: `company_intelligence_manifest_service.py`,
  `european_filing_feed_client.py`, `european_filings_agent.py`.
- Globální zdroje: `source_policy.py`, `source_resolution_agent.py`,
  `contracts.py`, `sqlite_store.py`, `quality_gate_agent.py`.
- Regulatory/Decision: `stage3_manifest_service.py`,
  `regulatory_contract_agent.py`, `decision_agent.py`, `pipeline_service.py`.
- Akceptační testy: `test_company_intelligence_runtime_completion.py` a
  navazující runtime/UI/regresní testy.

### Co tento záznam ještě neprokazuje

- Produkční identity manifest zatím není naplněn skutečnými firmami.
- Chybí live canary konkrétních regionálních evropských feedů.
- GitHub weekly production-shadow stále musí být spuštěn s reálným SEC
  kontaktním secret a následně obnovit databázi v druhém běhu.
- Není k dispozici 200 uzavřených OOS vzorků ani 12 nezávislých týdnů.
- Tato implementace proto neprokazuje zvýšení přesnosti predikce a nepovoluje
  ostrou aktivaci.
