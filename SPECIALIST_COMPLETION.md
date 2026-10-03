# Pátrací specialisté: jediný seznam stavu

Strojově čitelný seznam je v
`market_checker_app/data/specialist_status.json` a aplikace ho zobrazuje
v části **Co je hotové a co zbývá — specialisté**. Zahrnuje všech 687
vstupních tickerů; sektorový zdroj se vyhodnocuje jen pro relevantní firmy.
Mapování pokrývá výzkumné kroky 01–28 a sektorové profily.

Stejné fail-closed porovnání amendments následně prošlo kompletní
GitHub-hosted Windows sadou 543/543, úspěšným release gate a stažený artifact
11279019848 souhlasil s publikovaným SHA-256. Audit je v
`evidence/sec13dg_amendment_comparison_windows_20261003.json`. Hostovaný
runner není živý SEC ani koncový běh na uživatelově Windows; stav se nemění.

Source-verified primární Schedule 13D/G amendment se nyní porovná s jediným
bezprostředním předchůdcem, pouze pokud souhlasí ticker, issuer CIK, rodina
formuláře, jeden registry-matched CUSIP a přesná množina as-filed jmen a
všichni mají oba číselné cover údaje. Výsledkem je pouze
`AS_FILED_CANDIDATE`; jména zůstávají `NAME_ONLY`, změna vlastnictví se
neinterpretuje, je nutná lidská kontrola a skóre se nemění. Osiřelé,
nejednoznačné, index-only, budoucí nebo neúplné řetězce fail-closed.
Cíleně prošlo 21/21 a celá místní sada 543/543; viz
`evidence/sec13dg_amendment_comparison_tests_20261003.json`. Bez živých
případů, právních identit, Windows end-to-end, coverage a historie zůstává
insider PARTIAL/PENDING a celek 0/21 DONE.

Schedule 13D/G as-filed CUSIP se nyní přes reviewovaný SEC registr vyřeší
jen při jediné přesné shodě tickeru, issuer CIK, CUSIP, effective intervalu a
knowledge-time hranice. Aktuálně je takto dostupných jen 15 instrumentů pro
2026 Q2; více CUSIPů nebo jakýkoli nesoulad fail-closed zůstane bez shody.
Reporting-person jména jsou dál `NAME_ONLY`, změna vlastnictví se neodvozuje
a skóre se nemění. Cíleně prošlo 47/47 a celá místní sada 541/541; viz
`evidence/sec13dg_instrument_registry_match_tests_20261003.json`. Nejde o
plné instrumentové pokrytí ani živou akceptaci, stav zůstává 0/21 DONE.

Stejná instrumentová shoda následně prošla kompletní GitHub-hosted Windows
sadou 541/541, úspěšným release gate a stažený artifact 11277384887 souhlasil
s publikovaným SHA-256. Audit je v
`evidence/sec13dg_instrument_registry_match_windows_20261003.json`.
Hostovaný runner není živý SEC ani koncový běh na uživatelově Windows;
stav se proto nemění.

ScoutIndex už pro stejný issuer/accession nevydává index i primární dokument
jako dvě analytické reprezentace. Source-verified primární dokument má
přednost, zatímco indexový finding zůstává v analysis snapshotu a exportu.
Tím odpadá duplicitní 13D/G governance filing; cíleně prošlo 43/43 a celá
místní sada 540/540. Viz
`evidence/sec_filing_primary_dedup_tests_20261003.json`. Nejde o živou
akceptaci a stav zůstává 0/21 DONE.

Stejná deduplikační oprava následně prošla kompletní GitHub-hosted Windows
sadou 540/540, úspěšným release gate a stažený artifact 11275586495 souhlasil
s publikovaným SHA-256. Audit je v
`evidence/sec_filing_primary_dedup_windows_20261003.json`. Hostovaný runner
není živý SEC ani koncový běh na uživatelově Windows; stav se proto nemění.

SC 13D/G primární dokument nyní fail-closed extrahuje as-filed datum události,
název třídy, checksum-valid CUSIP a nejvýše 32 reporting persons včetně počtu
akcií a procenta. Data přežijí celý scout/index/governance průchod, ale jméno
je stále `NAME_ONLY`, instrument není registry-matched a změna proti minulému
filingu se neinterpretuje. Cíleně prošlo 28/28 a celá místní sada 540/540;
viz `evidence/sec13dg_cover_extraction_tests_20261003.json`. Stav zůstává
PARTIAL/PENDING a 0/21 DONE.

Stejný cover parser následně prošel kompletní GitHub-hosted Windows sadou
540/540, úspěšným release gate a stažený artifact 11272746709 souhlasil s
publikovaným SHA-256. Audit je v
`evidence/sec13dg_cover_extraction_windows_20261003.json`. Hostovaný runner
není živý SEC ani koncový běh na uživatelově Windows; stav se proto nemění.

Primární SC 13D/G dokument ze skutečné SEC scout cesty nyní zachová form,
accession, issuer CIK, report date a hash až do analytického dokumentu a
governance normalizace. End-to-end captured test potvrzuje, že SC 13G skončí
jako UNVERIFIED filing bez změny skóre. Cíleně prošlo 46/46 a celá místní sada
537/537; viz `evidence/sec13dg_pipeline_identity_tests_20261003.json`.
Reporting person, instrument a vlastnická změna se ještě neparsují, proto se
stav insider ani celkových 0/21 DONE nemění.

Stejný pipeline kontrakt následně prošel kompletní GitHub-hosted Windows
sadou 537/537, úspěšným release gate a stažený artifact 11270157230
souhlasil s publikovaným SHA-256. Audit je v
`evidence/sec13dg_pipeline_identity_windows_20261003.json`. Hostovaný runner
není živý SEC ani koncový běh na uživatelově Windows, takže stav zůstává
PARTIAL/PENDING.

Nezpracovaný typ SC 13D/G už není vydáván za ověřenou změnu vlastnictví.
Agent jej ukládá jako `BENEFICIAL_OWNERSHIP_FILING` / `UNVERIFIED` a
explicitně značí chybějící reporting-person identitu, instrument i
interpretaci změny; skóre zůstává vypnuté. Cílená sada prošla 32/32 a celá
místní sada 536/536. Důkaz je v
`evidence/sec13dg_fail_closed_filing_tests_20261003.json`. Bez živého SEC
parsování, Windows end-to-end, coverage a historie zůstává insider
PARTIAL/PENDING a celek 0/21 DONE.

Stejná změna následně prošla kompletní GitHub-hosted Windows sadou 536/536,
úspěšným release gate a stažený artifact 11267017998 souhlasil s publikovaným
SHA-256. Audit je v
`evidence/sec13dg_fail_closed_filing_windows_20261003.json`. Hostovaný runner
nedokládá živé parsování ani koncový běh na uživatelově Windows, proto se
stav nemění.

SEC 13F sběrač nyní rekonstruuje `13F-HR/A` řetězce, pokud je původní filing
i všechny amendments v témže čtvrtletním SEC archivu. Restatement nahradí
dřívější efektivní filingy, new holdings se k nim přidají; osiřelé,
nejednoznačné nebo neznámé řetězce fail-closed vracejí PARTIAL. U nálezů se
ukládají efektivní accessions i celý lineage. Cílená sada prošla 37/37 a
celá místní sada po instalaci deklarovaných závislostí 536/536; důkaz je v
`evidence/sec13f_amendment_reconstruction_tests_20261003.json`. Živý SEC
ZIP, přes-čtvrtletní řetězce, širší identity, skutečný Windows běh, coverage
a historická evaluace chybí, takže institutions zůstává PILOT/PENDING.

Navazující GitHub-hosted Windows job 111138490778 prošel 536/536, prošel i
release gate a stažený artifact 11265719882 byl obsahově i hashově ověřen v
`evidence/sec13f_amendment_reconstruction_windows_20261003.json`. Je to jen
ověření kompatibility; ne živý SEC ZIP ani skutečný uživatelský Windows
end-to-end běh.

Kanonický BRKB se nyní na všech Yahoo hranicích převádí na BRK-B, zatímco
kanonický seznam 687, cache a reporty dál používají BRKB. Dávkové OHLC,
individuální retry a corporate-action cesta zachovají kanonický výstupní
klíč. Cílená sada prošla 48/48 a celá místní sada 534/534; důkaz je v
evidence/price_brkb_yahoo_alias_tests_20261003.json. Živá odpověď, PSTG/LEG,
after-close Windows coverage a historie zůstávají otevřené, takže stav
prices ani celkových 0/21 DONE se nemění.

Navazující GitHub-hosted Windows job 111111841149 prošel 534/534, prošel i
release gate a stažený artifact 11263535162 byl obsahově i hashově ověřen v
evidence/price_brkb_yahoo_alias_windows_20261003.json. Je to jen ověření
kompatibility, nikoli živý uživatelský Windows end-to-end běh.

Akceptační report nyní skutečně načítá obsah všech odkazovaných JSON důkazů,
omezuje cesty na přímé soubory v evidence/, ověřuje schéma a zveřejňuje
SHA-256. Aktuálních 65 odkazů je obsahově čitelných, ale žádný název ani
neprázdný soubor sám nedokončuje specialistu. DONE/VERIFIED vyžaduje u
každého specialisty šest explicitních obsahových tvrzení: datovanou
issuer/instrument/product identitu, pozitivní a negativní živý případ,
skutečný Windows end-to-end, změřené relevantní pokrytí a historické
out-of-sample vyhodnocení. Teprve shoda všech 21 řádků může nastavit
completion_verified=true. Test auditní brány je v
evidence/specialist_evidence_content_audit_tests_20261003.json; stav
zůstává 0/21 DONE.

Tato brána následně prošla i celou GitHub-hosted Windows sadou 531/531;
workflow 37076717022, job 111068152597, release gate a stažený artifact
11256859768 byly ověřeny v
evidence/specialist_evidence_content_audit_windows_20261003.json.
Hostovaný CI není skutečný koncový běh na uživatelově Windows počítači.

Pro všech 16 provozních zdrojů report nově zveřejňuje jednotnou,
fail-closed klasifikaci attempted/result_usable/complete/partial/blocked/
failed. Čekání na přístup nebo identitu není pokus, PARTIAL bez pozitivního
uloženého či zpracovaného počtu není použitelný výsledek a neznámý status
není automaticky úspěch. Ani COMPLETE není akceptace specialisty. Kontrakt
a 58/58 testů jsou v
evidence/specialist_source_semantics_tests_20261003.json.

Tento kontrakt následně prošel i celou GitHub-hosted Windows sadou 532/532;
workflow 37083839180, job 111089918113, release gate a stažený artifact
11260106223 byly obsahově i hashově ověřeny v
evidence/specialist_source_semantics_windows_20261003.json. Ani tento
hostovaný CI není skutečný koncový běh na uživatelově Windows počítači.

SEC 13F registry byl dále rozšířen o V, MA, JNJ, XOM a WMT, tedy na patnáct
kanonických instrumentů pro 2026 Q2. Přesné issuer CIK/CUSIP/název/třída
z oficiálního listu jsou spojené s konkrétními Schedule 13G/13D; lokální
sada prošla 528/528. Důkazy jsou v
`evidence/sec13f_identity_expansion3_20261002.json` a
`evidence/sec13f_identity_expansion3_tests_20261002.json`. Navazující
GitHub-hosted Windows retry dokončil 528/528 a jeho stažený artifact byl
obsahově i hashově ověřen v
`evidence/sec13f_identity_expansion3_windows_tests_20261002.json`; první
zrušený pokus se nepočítá jako úspěch. Bez deklarovaného SEC User-Agent
nevznikl live ZIP důkaz. Plné pokrytí, amendments, uživatelský Windows
end-to-end a historie zůstávají otevřené; stav je stále PILOT/PENDING.

USAspending má druhou nezávisle doloženou vazbu: NOC → 100% dcera Northrop
Grumman Systems Corporation → UEI `LCV2N9FVV739`, účinnou od 31. 12. 2025
a známou až od 2. 10. 2026. Jednostránkový živý běh uložil devět eligible
awardů a zůstal PARTIAL, protože API hlásilo další stránku; replay nepřidal
duplicity a absent-UEI kontrola vrátila nulu. Důkazy jsou v
`evidence/usaspending_noc_identity_20261002.json` a
`evidence/usaspending_noc_live_20261002.json`. Cílená sada prošla 25/25 a
celá místní deterministická sada 528/528; přesné časy, otisky a clean-host
diagnostika jsou v `evidence/usaspending_noc_tests_20261002.json`.
Navazující hostovaný Windows průchod 528/528 a ověřený artifact jsou v
`evidence/usaspending_noc_windows_tests_20261002.json`. Dva piloty nejsou úplná
coverage, modification historie, Windows end-to-end ani historická evaluace.

SEC 13F instrument registry nyní obsahuje deset kanonických tickerů pro
2026 Q2: AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA, AVGO, AMD a JPM.
Přesné CUSIP/issuer CIK/třída záznamy mají oficiální 13F list, Schedule 13G,
ticker/CIK zdroj, efektivní čtvrtletí a čas znalosti. CUSIP check digit,
issuer name a security class se validují fail-closed. Oba identity kroky
jsou v `evidence/sec13f_identity_expansion_20261002.json` a
`evidence/sec13f_identity_expansion2_20261002.json`. Úplná místní sada
527/527 i navazující hostovaný Windows průchod 527/527 pro deset instrumentů
jsou doložené v `evidence/sec13f_identity_expansion2_tests_20261002.json`.
Chybějící deklarovaný SEC User-Agent znamená, že nevznikl falešný live ZIP
důkaz; hostovaný CI není uživatelský Windows end-to-end běh.
Amendments, plné relevantní coverage, Windows end-to-end a historie
zůstávají otevřené; institutions je stále PILOT/PENDING.

GitHub Windows CI na opravném commitu `9d83c221` dokončilo všech **525**
testů bez selhání, chyb a přeskočení za 758,382 sekundy. Workflow
`37012267755`, job `110854588580` a artifact `11228802805` doplňují
konkrétní auditní stopu; všechny joby včetně deterministic release gate
prošly. Tím je ověřena tato konkrétní oprava monotónní hranice znalosti na
hostovaném Windows runneru. Nejde o uživatelský živý end-to-end běh,
relevantní coverage ani historické OOS vyhodnocení, takže stav zůstává
0/21 DONE.

Windows CI na `20cdbd7` nově odhalilo dvě as-of regrese při shodném
wall-clock čase přes hranici dvou reportů. Procesní UTC clock nyní pod
zámkem posune shodný/zpětný vzorek o mikrosekundu, takže report cutoff je
striktně po vlastních pozorováních a před dalším během. Vynucený konstantní
clock test i 525/525 místních testů prošly. Navazující Windows průchod na
`9d83c221` také prošel 525/525. Důkaz je v
`evidence/windows_monotonic_clock_tests_20261002.json`.

EPA ECHO je nově zapojené jako omezený exact-name facility lead pro 104
tickerů z pěti relevantních profilů. FRS ID je identita zařízení, ne důkaz
datovaného vlastnictví emitentem, úplné skupiny nebo ekologické odpovědnosti.
Živý publisher smoke má přesný DOW INC i absent-name případ v
`evidence/epa_echo_live_20261002.json`; 524/524 místních testů je v
`evidence/epa_echo_tests_20261002.json`. Regulační specialista tím není
DONE/VERIFIED a celkový stav zůstává 0/21.

GitHub Windows CI na commitu `2945cc9` nyní dokončilo všech 519 testů
bez selhání nebo chyb (workflow `36995388024`, job `110800809096`) a prošel
i společný release gate. Log a JSON jsou uložené v artifactu `11222061817`;
strojový souhrn je také v
`evidence/windows_identity_order_tests_20261002.json`. Jde o ověření
kompatibility na hostovaném runneru, nikoli koncový živý běh na uživatelově
Windows počítači, coverage 687 tickerů nebo historickou evaluaci. Stav
0/21 DONE se proto nemění.

Windows běh na `58990b3` byl bez zaznamenané testovací chyby zrušen pevně
po 15 minutách ještě před dokončením sady. Samotných 518 testů v předchozím
běhu trvalo 747 sekund a setup/instalace se počítají do limitu. Job proto
dostal stále omezených 25 minut; plný test scope a release gate se
nezmenšují a timeout nadále selže. Navazující úplný průchod na `2945cc9`
prošel.

Windows CI na commitu `aaa5197` dokončilo 518 testů bez předchozích SQLite
lock chyb, ale našlo jediný deterministický problém: dvě identity verze se
shodným timestampem byly sekundárně řazené podle obsahového hashe. Čtení
nyní používá monotónní `agent_run_id`, tedy skutečné pořadí persistence;
regresní test vynutí shodný čas. Opravená sada prošla na `2945cc9`.

Od 2. 10. je zapojen i omezený DOJ press-release title collector. Přesná
slova názvu v titulku jsou pouze neověřený lead; publisher datum se nebere
za datum prvního pozorování a shoda nedokládá issuer identity ani právní
odpovědnost. Pozitivní a absent-name publisher smoke je v
`evidence/doj_live_20261002.json`. DOJ scope je součástí coverage a identity
změny znovu otevírají kontrolu. Tím není regulační specialista DONE.

První vyžádaná Windows CI sada na commitu `714bb97` odhalila nezavřené
SQLite file handles: Python context manager provedl transakci, ale spojení
nezavřel, takže Windows nedokázal mazat dočasné databáze. Oprava zavírá
spojení po commit/rollback ve všech pěti store a přidává přímé lifecycle
testy. Nový deterministický runner průběžně uchovává traceback i JSON
souhrn. Tehdejší lokální sada měla 518/518 PASS; navazující opravy a nový
regresní test mají úplný Windows výsledek 519/519 na `2945cc9`.

Od 1. 10. lze ve stejné části UI stáhnout **provozní přehled specialistů**
jako JSON: skutečné uložené běhy zdrojů, aktuální sektorové pokrytí,
integritu archivovaného 687tickerového vstupu a počet doložených SEC identit.
Nenalezený archiv ani nikdy neprovedený zdroj nevytváří falešnou nulovou
kontrolu. Výstup uvádí systém počítače; samotný export na Windows nepotvrzuje
koncový běh. Obsahuje pouze boolean přítomnosti přístupových nastavení,
nikoli jejich hodnoty, adresy vlastníků, pracovní cesty nebo přihlašovací
údaje. Přítomný klíč neprokazuje funkční oprávnění.

Tento aktuální diagnostický snapshot není historický replay ani certifikát
dokončení a nemění `DONE`, `VERIFIED` nebo predikční skóre. Pozitivní a
negativní případy, datované vztahy a out-of-sample vyhodnocení zůstávají
samostatnými akceptačními důkazy.

FDIC má od 1. 10. perzistentní obnovovací dávky: výchozí pět bank za běh,
deset požadavků za UTC den, 30 dní pro použitelnou odpověď a jeden den
pro částečnou/prázdnou/chybnou kontrolu. Diagnostika odděluje osmnáct
mapovaných bank od 22 BANK profilů a skutečné pokusy od použitelných dat.
Prázdná či odmítnutá odpověď nezvyšuje použitelnou coverage. Staré nálezy
samotné nenahrazují refresh pokus; scope je nejvýše dva výkazy na CERT,
nikoli úplnost emitentovy skupiny. HTTP blokace mají uloženou pauzu.
Zachycené payloady a restart/kvóta replay jsou doložené v
`evidence/fdic_scheduler_replay_20261001.json`, deterministické kontroly
v `evidence/fdic_scheduler_tests_20261001.json`. Nejde o nový živý běh,
Windows akceptaci ani historickou evaluaci. Stav zůstává PILOT/PENDING.

V předchozím kroku bylo datované bankovní mapování rozšířené na osm z 22 BANK profilů:
TFC → Truist Bank / CERT 9846 a FITB → Fifth Third Bank, National
Association / CERT 6672. FITB Exhibit 21 platí k 15. 2. 2026, nikoli
k výročnímu období 31. 12. 2025. SEC vztahy a skutečné FDIC payloady
jsou v `evidence/fdic_tfc_fitb_identity_20261001.json`, tři omezené
kladné/záporné publisher kontroly v `evidence/fdic_tfc_fitb_live_20261001.json`.
Tento předchozí pilot nemění PILOT/PENDING nebo 0/21 DONE.

Po kroku CFG/HBAN bylo mapováno deset z 22 BANK profilů: CFG → Citizens Bank, National
Association / CERT 57957 a HBAN → The Huntington National Bank / CERT 6560.
CFG datum vztahového tvrzení je 22. 1. 2026 podle prospektového dodatku,
nikoli neověřené datum Exhibit 21; HBAN má výslovné datum 31. 12. 2025.
Důkazy a tři omezené živé případy jsou v
`evidence/fdic_cfg_hban_identity_20261001.json` a
`evidence/fdic_cfg_hban_live_20261001.json`.

Po kroku ALLY/CFR bylo mapováno dvanáct z 22 BANK profilů, nově ALLY → Ally Bank /
CERT 57803 a CFR → Frost Bank / CERT 5510. ALLY má výslovné as-of datum
31. 12. 2025; CFR as-of zůstává neověřené a přijímá pouze výkazy od
konzervativní hranice 5. 2. 2026 z ověřeného filing indexu. Není to
datum akvizice. Citace a skutečné publisher odpovědi jsou v
`evidence/fdic_ally_cfr_identity_20261001.json`, tři živé případy v
`evidence/fdic_ally_cfr_live_20261001.json`.

Po kroku COF/EWBC bylo mapováno čtrnáct z 22 BANK profilů, nově COF → Capital One,
National Association / CERT 4297 a EWBC → East West Bank / CERT 31628.
Obě SEC přílohy výslovně dokládají as-of 31. 12. 2025 a vynechávají
další dcery; nedokládají úplnost skupin ani kontinuální historii.
R1 / filing indexy rozlišují issuer/common-stock identitu, datum vztahu
a zveřejnění. Citace a skutečné FDIC odpovědi jsou v
`evidence/fdic_cof_ewbc_identity_20261001.json`, tři omezené živé případy
v `evidence/fdic_cof_ewbc_live_20261001.json`.

Po kroku FHN/KEY bylo mapováno šestnáct z 22 BANK profilů, nově FHN → First Horizon
Bank / CERT 4977 a KEY → KeyBank National Association / CERT 17534,
obě vztahová tvrzení výslovně k 31. 12. 2025. FHN vlastnická výjimka
pro cizí nehlasovací prioritní akcie zůstává v důkazu, bez tvrzení
bezvýhradného 100% vlastnictví nebo přepočítávání bankovních hodnot.
SEC citace a skutečné FDIC odpovědi jsou v
`evidence/fdic_fhn_key_identity_20261001.json`, tři omezené živé případy
v `evidence/fdic_fhn_key_live_20261001.json`. Zbývá šest emitentů,
úplné skupiny, Windows běh, relevantní provozní pokrytí a historická evaluace.

Po kroku MTB bylo mapováno sedmnáct z 22 BANK profilů. Nové MTB → Manufacturers
and Traders Trust Company / CERT 588 má vztahové as-of neověřené;
samostatná publication floor 18. 2. 2026 vychází z potvrzeného SEC indexu,
nikoli z výročního období v poznámce o významnosti jiných dcer.
Citace a skutečné FDIC payloady jsou v `evidence/fdic_mtb_identity_20261001.json`,
dva omezené živé případy v `evidence/fdic_mtb_live_20261001.json`.
V tomto předchozím kroku byl OZK přímý bankovní emitent podle vlastního FDIC 10-K, zatím bez
odpovídajícího identity modelu v SEC-only registru; ověřené primární
locatory a konkrétní mezera jsou v `evidence/ozk_identity_model_gap_20261001.json`.
Zbývá pět emitentů (OZK, PNFP, RF, WAL, ZION), úplné skupiny,
Windows běh, relevantní provozní pokrytí a historická evaluace.

Aktuálně registry mapuje 18 z 22 BANK profilů: 17 dceřiných vazeb a nový
přímý bankovní emitent OZK / CERT 110. Oddělený direct-bank model vyžaduje
konkrétní schválený FDIC filing, common OZK instrument (nikoli OZKAP),
as-of 31. 12. 2025, publikaci 25. 2. 2026 a znalost až 1. 10. 2026.
Neobsahuje vymyšlený CIK nebo parent–subsidiary vztah. FDIC reporty nejsou
issuer konsolidované hodnoty; scope hash, kvóty a restart byly ověřeny.
Zdrojový důkaz je v `evidence/fdic_ozk_identity_20261001.json`, dva omezené
živé případy v `evidence/fdic_ozk_live_20261001.json`, 492 místních testů
v `evidence/fdic_ozk_tests_20261001.json`. Předchozí OZK gap je historický
snímek. Zbývá PNFP, RF, WAL a ZION, úplné skupiny, relevantní provozní
pokrytí, Windows běh a historická evaluace. Počet DONE zůstává 0/21.

## Co znamenají stavy

- `NOT_STARTED`: potřebný sběr ještě neexistuje.
- `PILOT`: omezená prokázaná sada identit či instrumentů.
- `PARTIAL`: existuje sběr nebo analytika, ale chybí některá cesta či pokrytí.
- `DONE`: doložená identita v čase, živý pozitivní i negativní případ,
  koncový běh na Windows, coverage pro relevantní firmy a historie.
- `PENDING`: živý důkaz zatím chybí. `WAIT_ACCESS`: zdroj vyžaduje klíč,
  účet či licenci. `VERIFIED`: živý důkaz je uložený.

Žádný nový specialista zatím nesplňuje celé `DONE`. Kompilace, lokální
testy a zelené CI potvrzují vlastnosti kódu, nikoli úspěšné stažení dat
na uživatelově PC. Ani 687 řádků watchlistu nezaručuje, že je pro každý
relevantní ticker ověřená produktová, dceřiná a instrumentová identita.
Při změně stavu je nutné aktualizovat JSON ve stejném PR jako kód a připojit
důkaz živého běhu, pokud se nastavuje `VERIFIED`.

Denní přehled **Poslední běh pátracích zdrojů** vedle inventáře čte skutečné
záznamy z SQLite. Inventář je verzovaná mapa dokončení, nikoli náhrada
za aktuální běhovou telemetrii. Od 30. 9. 2026 platí pořadí: banky,
farmacie/medtech a zdravotní služby; energie a obrana; zbývající sektorové
profily; placené a alternativní zdroje až po ověřených pilotech.
