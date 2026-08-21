# Company Intelligence / Forensic – changelog oprav

Tento soubor je chronologický, lidsky čitelný log implementačních změn.
Aktuální stav a návaznost úkolů zůstává v `COMPANY_INTELLIGENCE_TASKS.md`; Git
historie je technický zdroj pravdy pro přesný diff každého commitu.

Každý další implementační PR má doplnit datum, rozsah, bezpečnostní dopad,
ověřovací testy a otevřené provozní podmínky. Historické záznamy se nemažou.

## 2026-08-21 – dokončení kódových oprav 1–4

- Větev: `agent/company-intelligence-audit-status`
- Pull request: draft PR #77
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
