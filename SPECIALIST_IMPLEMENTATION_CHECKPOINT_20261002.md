# Specialisté — checkpoint 2. 10. 2026

Větev: `fix/specialist-daily-capacity`, PR #160. Tento dokument navazuje
na checkpoint z 1. 10.; starší důkazy ani neuzavřené akceptace nenahrazuje.
Autoritativní inventář je `market_checker_app/data/specialist_status.json`.

## Dokončené změny

- USAspending registr přidal NOC → 100% dceru Northrop Grumman Systems
  Corporation → UEI `LCV2N9FVV739`. Doložené období začíná 31. 12. 2025;
  starší vztah se neodvozuje. Omezená jedna stránka uložila devět eligible
  awardů, zůstala PARTIAL kvůli další stránce, replay nepřidal duplicity a
  absent-UEI kontrola vrátila nulu. Findings nyní zachovávají vztahové
  effective-from/to i knowledge time. Cílená sada prošla 25/25 a celá místní
  deterministická sada 528/528; test evidence zachovává i první clean-host
  diagnostiku chybějících závislostí. Navazující GitHub-hosted Windows job
  `110990378505` a release gate prošly 528/528; stažený artifact
  `11248265909` byl obsahově i hashově ověřen. Jde pouze o kompatibilitu,
  nikoli živý uživatelský Windows end-to-end. Je to druhý pilot, ne úplná
  coverage.
- Druhý SEC 13F identity krok přidal GOOGL, TSLA, AVGO, AMD a JPM. Registr
  tak obsahuje deset kanonických tickerů z archivu 687, s přesným issuer CIK,
  CUSIP, názvem a třídou pro 2026 Q2 a s konkrétním Schedule 13G pro každý
  nový instrument. Nejde o plnou coverage, živý ZIP běh, amendments ani
  historickou evaluaci; stav institutions zůstává PILOT/PENDING.
- SEC 13F registry byl rozšířen z AAPL na pět přesně citovaných 2026 Q2
  instrumentů: AAPL, MSFT, NVDA, AMZN a META. Záznam vyžaduje platný CUSIP
  check digit, desetimístný issuer CIK, shodu issuer name i security class,
  čtvrtletní efektivní okno a knowledge time. Evidence odkazuje jen na
  oficiální SEC 13F list, Schedule 13G a ticker/CIK publikaci. Chybějící
  deklarovaný SEC User-Agent na tomto hostu se nezastupuje vymyšlenou
  kontaktní identitou, takže live ZIP akceptace zůstává otevřená.
- Zachována a ověřena rozpracovaná mapování RF a WAL včetně zachycených
  skutečných odpovědí a testů znalostního času. Jejich starší důkazy
  `fdic_rf_wal_identity_20261001.json` a `fdic_rf_wal_live_20261001.json`
  jsou součástí checkpointu.
- FDIC registr nyní mapuje všech **22 z 22 BANK profilů**: 20 bankovních
  dcer, OZK jako přímého FDIC-reportujícího emitenta a ZION jako přímého
  SEC-reportujícího emitenta. ZION/CERT 2270 má doložený common ZION,
  samostatný SEC CIK, přesný zkrácený název z financials a žádný vymyšlený
  vztah matka–dcera. Přísná validace schvaluje konkrétní dokument/CERT.
- PNFP/CERT 35583 používá nástupnický CIK **0002082866**. SEC 8-K12B
  dokládá fúzi a přechod stejného tickeru na NYSE; historický CIK
  0001115055 není automaticky přejímán. Dceřiná vazba z aktuálního 10-Q
  je omezena datem 31. 3. 2026. Datum zveřejnění tohoto 10-Q není
  ověřeno a není vydáváno za datum znalosti. Knowledge time je tento běh.
  Historické hodnoty před tímto datem nové mapování odmítá.
- PNFP a ZION prošly skutečnými omezenými pozitivními kontrolami,
  opětovným zpracováním zachycených dat bez duplicit a negativní kontrolou
  neexistujícího CERT. Identitní důkazy a odpovědi jsou v
  `evidence/fdic_pnfp_zion_identity_20261002.json` a
  `evidence/fdic_pnfp_zion_live_20261002.json`. V tomto kroku bylo sedm
  FDIC API požadavků: dvě institutions, dvě preliminary financials a tři
  smoke financials; replay neprovádí síťová volání.
- FDA má 60sekundový rozpočet pro zahajování požadavků, zastavení po
  třech selháních a 4MB limit odpovědi. Jeden již zahájený požadavek může
  doběhnout do svého 20sekundového timeoutu. Přerušený emitent není
  dokončený; dřívější dokončené kontroly zůstávají uložené. Pouze přesný
  dokumentovaný no-match JSON 404 je prázdný výsledek. Neplatný či
  budoucí záznam přesného jména vytváří PARTIAL a jednodenní retry.
- Nový samostatný OFAC Non-SDN sběr používá oficiální CONS_PRIM.CSV a
  CONS_ALT.CSV, nejvýše 1 MB na soubor a 20sekundové timeouty. ID, typy,
  aliasy a rodiče se validují stejným přísným kontraktem; ukládají se
  pouze organizační entity. Program a remarks zůstávají u kandidáta.
  Non-SDN shoda neurčuje blokaci majetku ani identitu emitenta. SDN běh
  a stejné číselné ID nemohou dokončit Non-SDN kontrolu.
- Non-SDN je zapojen do denního runneru, zdrojových pravidel, historie,
  UI a přenosného provozního přehledu. Neprovedený zdroj zůstává NEVER_RUN.
  Živé publisher/alias/absence důkazy jsou v
  `evidence/ofac_non_sdn_live_20261002.json`; nejde o přiřazení akcii.
- GitHub workflow nově vyžaduje samostatnou deterministickou sadu na
  `windows-latest`, ukládá její log a zahrnuje ji do release gate.
  To je ověření kompatibility na Windows runneru, nikoli živý koncový běh
  uživatelova PC. Výsledek CI musí být ověřen na konkrétním commitu.
- Samostatný DOJ collector ukládá přesné titulkové shody z oficiálního API
  jako `UNVERIFIED` leads. Má 2MB odpověď, 20sekundový request timeout,
  maximálně dvě stránky / 25 položek, tři chyby a 60 sekund na běh. Datum
  vydavatele je oddělené od prvního pozorování; shoda nezakládá identitu,
  odpovědnost, úplnost právních událostí ani skóre. Skutečné pozitivní a
  absent-title případy jsou v `evidence/doj_live_20261002.json`.
- EPA ECHO collector vytváří jen přesné facility-name leads pro 104 tickerů
  pěti relevantních profilů. Má deset emitentů, 60 sekund, tři chyby,
  20sekundový request timeout a 2MB odpověď. FRS ID dokládá zařízení, nikoli
  jeho vztah k emitentovi, úplnost skupiny nebo odpovědnost. DOW INC a
  absent-name publisher smoke jsou v `evidence/epa_echo_live_20261002.json`.
- První Windows CI na commitu `714bb97` skončilo timeoutem po řadě chyb při
  práci s dočasnými SQLite databázemi. Vlastní `sqlite3.Connection` context
  manager spojení nezavírá. Pět aplikačních store proto používá closing
  connection, které po commit/rollback vždy zavře file handle. Nové testy
  ověřují uzavření a přejmenování databáze se stále živým Python objektem.
  CI runner nyní průběžně vypisuje traceback a uloží strojový JSON souhrn.
- Následující Windows CI na commitu `aaa5197` spustilo všech 518 testů a
  potvrdilo odstranění SQLite lock chyb. Selhal jediný test chronologie:
  dvě identity verze se shodným timestampem byly řazené podle hash ID.
  Čtení nyní při shodném čase používá monotónní `agent_run_id`; nový test
  přesně reprodukuje Windows podmínku. Nový Windows CI průchod je otevřený.
- Běh s opravou na `58990b3` neměl zaznamenanou testovací chybu, ale byl
  zrušen 15minutovým job limitem v pozdní části celé sady. Předchozí samotná
  sada trvala 747 sekund; setup a instalace jsou ve stejném limitu. Windows
  job má proto pevně 25 minut. Test scope, artefakty a release gate zůstávají
  stejné a timeout není úspěch.
- Navazující GitHub `windows-latest` běh na `2945cc9` dokončil **519/519**
  testů za 709,826 sekundy bez selhání a chyb. Workflow `36995388024`,
  job `110800809096` uložil log i JSON jako artifact `11222061817`; prošel
  také společný deterministic release gate. Tím je uzavřená tato CI
  regresní kontrola SQLite handles a identity order. Není to skutečný
  uživatelský Windows end-to-end běh ani živá akceptace zdrojů.
- Rozšířený běh na `20cdbd7` spustil 524 testů a odhalil dvě další as-of
  regrese: shodný Windows wall-clock čas překročil hranici reportů, takže
  cutoff prvního reportu připustil i pozorování dalšího. Společný UTC clock
  teď pod zámkem posune shodný/zpětný vzorek o mikrosekundu; vynucený
  konstantní-clock test a 525/525 místních testů prošly. Navazující
  `windows-latest` běh na opravném commitu `9d83c221` dokončil také
  **525/525** testů za 758,382 sekundy bez selhání, chyb a přeskočení.
  Workflow `37012267755`, job `110854588580` uložil artifact `11228802805`
  a deterministic release gate prošel. Tím je tato konkrétní CI regrese
  uzavřená; stále nejde o skutečný uživatelský Windows end-to-end běh.
  Podrobnosti jsou v
  `evidence/windows_monotonic_clock_tests_20261002.json`.

## Ověření

Po doplnění deterministického identity pořadí celá místní sada skutečně
spustila **519 z 519 objevených testů**:
žádná chyba, selhání ani přeskočení. Důkaz s časy a otisky kódu je v
`evidence/specialist_tests_20261002.json` a nový krok v
`evidence/doj_sqlite_tests_20261002.json`; nejnovější Windows-order krok je
v `evidence/windows_identity_order_tests_20261002.json`. Kompilace a
`git diff --check`
prošly. Před během byly nainstalovány uložené requirements a constraints;
první diagnostika s chybějícími závislostmi se nepočítá jako průchod.
Hostovaný Windows průchod navíc dokončil stejných 519 testů; inventář se
tím nemění, protože chybí koncový živý běh a ostatní akceptační důkazy.
Po EPA inkrementu prošla rozšířená místní sada **524/524** bez chyb,
selhání a přeskočení; targeted EPA/acceptance sada má 20 PASS. Otisky a
časy jsou v `evidence/epa_echo_tests_20261002.json`. Nejde o nový Windows
průchod ani historickou evaluaci.
Po opravě nově pozorované Windows hranice času prošla sada **525/525**;
místně i na hostovaném GitHub Windows runneru. Hostovaný CI výsledek
nenahrazuje skutečný koncový běh na uživatelově Windows počítači.
Po rozšíření 13F identity registru a dvou fail-closed regresích prošla nová
úplná místní sada **527/527** bez selhání, chyb a přeskočení. Kompilace a
`git diff --check` prošly; strojový výsledek a otisky jsou v
`evidence/sec13f_identity_tests_20261002.json`. Navazující hostovaný
`windows-latest` běh na commitu `991b0cb` dokončil stejných **527/527**
za 684,297 sekundy (workflow `37021509189`, job `110885473281`, artifact
`11234027903`) a release gate prošel. Je to Windows kompatibilita kódu,
nikoli živý 13F ZIP nebo uživatelský end-to-end běh.
Po druhém identity rozšíření na deset instrumentů prošly cílené kontroly
**21/21** a úplná místní sada znovu **527/527**. Kompilace i
`git diff --check` prošly; otisky a časy jsou v
`evidence/sec13f_identity_expansion2_tests_20261002.json`. Navazující
hostovaný Windows job `110945954943` na commitu `b112003c` prošel stejných
**527/527**, workflow `37039539524` i release gate uspěly a stažený artifact
`11242338229` měl ověřený obsah a SHA-256. Jde o kompatibilitu na hostovaném
runneru; živý 13F ZIP, uživatelský Windows end-to-end a historická evaluace
zatím nejsou důkazem.

## Co zůstává otevřené

Inventář nadále poctivě uvádí **0/21 úplných provozních akceptací**.
22 bankovních mapování nejsou 22 specialisté ani úplnost bankovních skupin.
Bankovní financials nejsou automaticky konsolidované výkazy emitenta.

Další proveditelná práce: datované EPA facility-owner, FDA/CMS/ClinicalTrials
entity a produkty, další UEI/CUSIP a relevantní sektorové vazby. U 13F
zbývá živý oficiální ZIP s deklarovaným SEC kontaktem, rekonstrukce amendments,
měřené relevantní coverage a historická dostupnost.
Konkrétní zbývající úkol každého specialisty je v inventáři a předchozím
checkpointu. FINRA/FRED/EIA a SEC kontakt na tomto hostu chybějí;
licencované konsensus/options/Level 2 a alternativní zdroje nemají
poskytovatele ani doložený přístup. Přístup na GitHubu či Windows tím
není vyloučen. Skutečný koncový Windows běh, relevantní live coverage a
historická predikční výkonnost se nesmějí nahradit syntetickými testy.

Bez dalšího pokynu nemergovat PR, nekupovat data a nezřizovat účty.
Další pokračování načte aktuální GitHub head a tento checkpoint, ověří
konkrétní dostupný úkol a zachová otevřené přístupové/datové blokace.
Žádné změny skóre ani automatického zadávání obchodů.
