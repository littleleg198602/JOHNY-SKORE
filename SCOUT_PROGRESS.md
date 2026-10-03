# Průběh implementace pátracích agentů

## SEC — primární dokument bez duplicitního index eventu (3. 10. 2026)

Po zapojení 13D/G cover parseru se ukázala navazující chyba: ScoutIndex pro
stejný issuer/accession předával index i source-verified primární dokument,
takže governance mohl vytvořit dvě filing události. Nyní se podle kanonického
subjectu a SEC source objectu předá jen primární dokument; indexový finding se
nezahodí a zůstává v neměnném analysis snapshotu a exportu. Žádné skóre se
nemění.

Cílená sada prošla 43/43 a po instalaci deklarovaných závislostí celá místní
deterministická sada 540/540. První clean-host pokus skončil po 434 testech s
22 importními chybami a nepočítá se jako úspěch. Regresní důkaz a otisky jsou
v `evidence/sec_filing_primary_dedup_tests_20261003.json`. Jde o opravu
reprezentace, ne živý SEC, uživatelský Windows end-to-end, coverage nebo
historickou evaluaci; stav specialistů se nemění.

## SEC 13D/G — as-filed cover fields bez domyšlené změny (3. 10. 2026)

Navazující GitHub-hosted Windows job 111190444784 na commitu 5ef9803f
dokončil 540/540 testů bez selhání, chyb a přeskočení. Workflow
37118714020 i deterministic release gate prošly. Stažený artifact
11272746709 obsahoval úplný log a strojový JSON; SHA-256 staženého ZIPu
souhlasil s metadaty workflow. Auditní záznam je v
`evidence/sec13dg_cover_extraction_windows_20261003.json`. Jde pouze o
hostovanou kompatibilitu, ne živý SEC ani skutečný uživatelský Windows
end-to-end běh.

Omezený parser primárního SC 13D/G dokumentu nyní ukládá datum události,
název třídy, pouze checksum-valid CUSIP a až 32 cover-page reporting persons
s as-filed počtem akcií a procentem třídy. Pole přežijí SEC scout → uložený
finding → ScoutIndex → governance event. Jméno reporting person zůstává
`NAME_ONLY`, CUSIP/třída `AS_FILED_NOT_REGISTRY_MATCHED`; jediný filing
neprokazuje změnu proti minulému stavu. Událost je proto dál UNVERIFIED,
vyžaduje kontrolu a nemá skóre.

Cílená sada prošla 28/28 a po instalaci deklarovaných závislostí celá místní
deterministická sada 540/540. První clean-host pokus skončil po 434 testech s
22 importními chybami a nepočítá se jako úspěch. Přesné limity a otisky jsou
v `evidence/sec13dg_cover_extraction_tests_20261003.json`. Chybí živé kladné
i záporné SEC případy, právní identita osob, datovaná instrumentová registry
shoda, porovnání amendments, skutečný Windows end-to-end, coverage a historie;
insider zůstává PARTIAL/PENDING.

## SEC 13D/G — identita podání přes celý pipeline (3. 10. 2026)

Navazující GitHub-hosted Windows job 111167791904 na commitu e7210757
dokončil 537/537 testů bez selhání, chyb a přeskočení. Workflow
37110670557 i deterministic release gate prošly. Stažený artifact
11270157230 obsahoval úplný log a strojový JSON; SHA-256 staženého ZIPu
souhlasil s metadaty workflow. Auditní záznam je v
`evidence/sec13dg_pipeline_identity_windows_20261003.json`. Jde pouze o
hostovanou kompatibilitu, ne živý SEC ani skutečný uživatelský Windows
end-to-end běh.

Source-verified primární dokument ze SEC fronty už při převodu do analytického
`DocumentRecord` neztrácí form, accession, issuer CIK, report date ani hash
dokumentu. Zachycený end-to-end test vede SC 13G od omezeného indexu a stažení
přes uložený finding až do `GovernanceEventAgent`; výsledek zůstává správně
`BENEFICIAL_OWNERSHIP_FILING` / `UNVERIFIED`, bez interpretace změny a bez
skóre. Tím se opravuje chybějící propojení, nikoli obsahový parser 13D/G.

Cílená sada prošla 46/46 a po instalaci deklarovaných závislostí celá místní
deterministická sada 537/537. První clean-host pokus skončil po 431 testech s
22 importními chybami a nepočítá se jako úspěch. Přesný kontrakt a otisky jsou
v `evidence/sec13dg_pipeline_identity_tests_20261003.json`. Stále chybí živý
SEC běh, parsování reporting person/CUSIP/třídy a změny vlastnictví, skutečný
Windows end-to-end, coverage a historie; insider zůstává PARTIAL/PENDING.

## SEC 13D/G — fail-closed význam nezpracovaného filingu (3. 10. 2026)

Navazující GitHub-hosted Windows job 111149500619 na commitu e1553b59
dokončil 536/536 testů bez selhání, chyb a přeskočení. Workflow
37104221313 i deterministic release gate prošly. Stažený artifact
11267017998 obsahoval úplný log a strojový JSON; SHA-256 staženého ZIPu
souhlasil s metadaty workflow. Auditní záznam je v
`evidence/sec13dg_fail_closed_filing_windows_20261003.json`. Jde pouze o
hostovanou kompatibilitu, ne živý SEC ani skutečný uživatelský Windows
end-to-end běh.

Samotný typ formuláře už nevytváří falešně ověřenou změnu významného
vlastnictví. Nezpracované SC 13D/G se ukládají jako samostatný
`BENEFICIAL_OWNERSHIP_FILING` ve stavu `UNVERIFIED`; metadata výslovně
uvádějí, že identita reporting person, instrument a interpretace změny
nejsou ověřené, je nutná kontrola a skóre se nepoužívá. Typ
`BENEFICIAL_OWNERSHIP_CHANGE` zůstává vyhrazený pro budoucí skutečně
interpretovanou změnu.

Cílená sada prošla 32/32 a po instalaci deklarovaných závislostí celá
místní deterministická sada 536/536. První clean-host pokus skončil po 430
testech s 22 importními chybami a nepočítá se jako úspěch. Přesné otisky a
omezení jsou v `evidence/sec13dg_fail_closed_filing_tests_20261003.json`.
Chybí živý SEC běh s deklarovaným User-Agentem, datované identity a
interpretace reporting person/instrument/change, pozitivní i negativní
živý případ, skutečný Windows end-to-end, coverage a historická evaluace.
Insider proto zůstává PARTIAL/PENDING a inventář 0/21 DONE.

## SEC 13F — rekonstrukce amendments v jednom čtvrtletním archivu (3. 10. 2026)

Navazující GitHub-hosted Windows job 111138490778 na commitu 106b1410
dokončil 536/536 testů bez selhání, chyb a přeskočení. Workflow 37100351318
i deterministic release gate prošly. Stažený artifact 11265719882 obsahoval
úplný log a strojový JSON; SHA-256 staženého ZIPu souhlasil s metadaty i
upload logem. Auditní záznam je v
`evidence/sec13f_amendment_reconstruction_windows_20261003.json`. Jde pouze
o hostovanou kompatibilitu, ne živý SEC ZIP ani uživatelský Windows
end-to-end běh.

13F sběrač už neignoruje `13F-HR/A`. Podle oficiálních polí SUBMISSION a
COVERPAGE seskupí filingy podle manager CIK a period of report. `RESTATEMENT`
nahradí dřívější efektivní filingy, zatímco `NEW HOLDINGS` přidá nový filing
k existující sadě. Uložený nález zachovává typ podání, celý filing chain a
konkrétní efektivní accessions. Skóre se nemění.

Rekonstrukce je záměrně omezená na řetězec, který je celý přítomen v jednom
oficiálním čtvrtletním ZIPu. Osiřelý amendment, více počátečních filingů,
neplatný `ISAMENDMENT` nebo neznámý typ vyřadí celou skupinu a vrátí
`PARTIAL`; nespojí se se zastaralými řádky. Nové testy pokrývají restatement,
new holdings i záporné případy. Cílená sada prošla 37/37 a po instalaci
připnutých závislostí celá místní deterministická sada 536/536. První
clean-host pokus s 22 chybějícími importy ani chybný seznam tří neexistujících
test modulů se nepočítají jako úspěch. Přesný rozsah a otisky jsou v
`evidence/sec13f_amendment_reconstruction_tests_20261003.json`.

Na hostu stále není deklarovaný SEC User-Agent, takže neproběhl živý ZIP.
Chybí řetězce přes hranici čtvrtletních archivů, širší datované instrumenty,
skutečný Windows end-to-end, změřené relevantní pokrytí a historická
out-of-sample evaluace. Institutions proto zůstává PILOT/PENDING a inventář
0/21 DONE.

## Ceny — BRKB na hranici Yahoo provideru (3. 10. 2026)

Kanonický identifikátor BRKB zůstává beze změny v seznamu 687, reportech a
cache. Pouze při volání Yahoo se nyní převádí na BRK-B. Převod platí pro
metadata, dávkové OHLC, individuální retry i přísnou corporate-action OHLC
cestu; dříve individuální retry používal chybné BRKB a mohl selhat i po
správném dávkovém požadavku. Loader kanonického vstupu vrací pro tento řádek
explicitně dvojici BRKB / BRK-B bez změny zdrojového CSV nebo jeho SHA-256.

Cílená sada prošla 48/48 a celá místní deterministická sada 534/534 bez
selhání, chyb a přeskočení. Přesné otisky a omezení jsou v
evidence/price_brkb_yahoo_alias_tests_20261003.json. Neproběhl živý Yahoo
důkaz, 687-ticker after-close Windows běh ani historická evaluace. PSTG a
LEG zůstávají otevřené; prices je proto dál PARTIAL/PENDING a inventář
0/21 DONE.

Navazující GitHub-hosted Windows job 111111841149 na commitu 47665023
dokončil stejných 534/534 testů bez selhání, chyb a přeskočení. Workflow
37091239382 i deterministic release gate prošly. Stažený artifact
11263535162 obsahoval úplný log a strojový JSON; SHA-256 staženého ZIPu
souhlasil s workflow metadaty i upload logem. Auditní záznam je v
evidence/price_brkb_yahoo_alias_windows_20261003.json. Jde pouze o
hostovanou kompatibilitu, ne živý 687-ticker běh na uživatelově Windows.

## Akceptace — audit obsahu důkazů (3. 10. 2026)

Provozní report už nepovažuje neprázdný název souboru za akceptační důkaz.
Načte každý odkazovaný JSON přímo z evidence/, odmítne chybějící,
neplatný, nepodporovaný nebo cestou unikající soubor a zveřejní jeho velikost
a SHA-256. Aktuálních 62 odkazovaných souborů je čitelných a schématicky
platných; to samo o sobě není důkaz dokončení.

Případný stav DONE/VERIFIED nyní fail-closed vyžaduje obsahové potvrzení
šesti oddělených tvrzení: datované identity, pozitivní a negativní živý
případ, skutečný Windows end-to-end, změřené relevantní pokrytí a historické
out-of-sample vyhodnocení. completion_verified může být true pouze pro
všech 21 řádků současně. Testy záměrně dokazují, že existující JSON s
nepravdivým Windows tvrzením ani únik cesty neprojde. Cílená sada prošla
20/20, compileall a diff kontrola prošly. Audit je v
evidence/specialist_evidence_content_audit_tests_20261003.json.

Navazující GitHub-hosted Windows job 111068152597 na commitu 6177d871
dokončil 531/531 testů bez selhání, chyb a přeskočení. Workflow
37076717022 i deterministic release gate prošly. Stažený artifact
11256859768 obsahoval úplný log a strojový JSON; SHA-256 staženého ZIPu
souhlasil s workflow metadaty i upload logem. Auditní záznam je v
evidence/specialist_evidence_content_audit_windows_20261003.json.
Jde pouze o hostovanou kompatibilitu, ne uživatelský Windows end-to-end.

Report navíc pro všech 16 provozních zdrojů odděluje nikdy neprovedený nebo
čekající stav, skutečný pokus, úplný výsledek, omezený/částečný výsledek,
blokaci a chybu. PARTIAL se považuje za použitelný jen při doloženém
kladném počtu uložených či zpracovaných výsledků; neznámý stav selže
zavřeně. Žádný provozní status sám není akceptace specialisty. Cílené a
dotčené testy prošly 58/58; přesný kontrakt je v
evidence/specialist_source_semantics_tests_20261003.json.

Navazující GitHub-hosted Windows job 111089918113 na commitu fedb0c8d
dokončil 532/532 testů bez selhání, chyb a přeskočení. Workflow
37083839180 i deterministic release gate prošly. Stažený artifact
11260106223 obsahoval úplný log a strojový JSON; SHA-256 staženého ZIPu
souhlasil s workflow metadaty i upload logem. Auditní záznam je v
evidence/specialist_source_semantics_windows_20261003.json. Jde jen o
hostovanou kompatibilitu, nikoli skutečný uživatelský Windows end-to-end.

Aktuálně je stále 0/21 DONE; kontrola pouze brání budoucímu falešnému
dokončení a nemění skóre, živé coverage ani obchodování.

## SEC 13F — patnáct přesně citovaných instrumentů (2. 10. 2026)

Registr 2026 Q2 byl rozšířen o V, MA, JNJ, XOM a WMT. Všech pět je v
kanonickém vstupu 687; každý záznam má přesný ticker/issuer CIK, CUSIP,
název a třídu z oficiálního SEC 13F securities listu a konkrétní Schedule
13G/13D, které potvrzuje stejný instrument. Citace, locatory, event date,
čas znalosti a omezení jsou v
`evidence/sec13f_identity_expansion3_20261002.json`.

Produkční test nyní vyžaduje přesně všech patnáct schválených identit;
CUSIP check digit a fail-closed shoda názvu/třídy zůstávají zachované.
Cílená sada prošla 21/21 a po instalaci připnutých závislostí celá místní
deterministická sada **528/528** bez selhání, chyb a přeskočení. První
clean-host diagnostika s 22 importními chybami se nepočítá jako úspěch.
Časy a otisky jsou v
`evidence/sec13f_identity_expansion3_tests_20261002.json`.

Navazující GitHub-hosted Windows retry job `111042792217` na commitu
`01151de7` dokončil **528/528** testů bez selhání, chyb a přeskočení;
workflow `37062619610` i deterministic release gate prošly. První pokus byl
externě zrušen během sady a nepočítá se jako úspěch. Stažený artifact
`11253952762` obsahoval úplný log a strojový JSON a jeho SHA-256 souhlasil
s upload logem. Auditní záznam je v
`evidence/sec13f_identity_expansion3_windows_tests_20261002.json`.

Na hostu stále není deklarovaný SEC User-Agent, proto nebyl předstírán živý
ZIP běh. Patnáct instrumentů není plné relevantní pokrytí, rekonstrukce
amendments, uživatelský Windows end-to-end ani historická evaluace. Skóre
se nemění; hostovaný Windows běh je jen kontrola kompatibility, institutions
zůstává PILOT/PENDING a inventář 0/21 DONE.

## USAspending — NOC / Northrop Grumman Systems (2. 10. 2026)

Registr vládních kontraktů byl rozšířen z jediného LMT/Sikorsky pilotu o
NOC → Northrop Grumman Systems Corporation → UEI `LCV2N9FVV739`. Aktuální
SEC 10-K dokládá NOC, common stock a CIK 0001133421; Exhibit 21 dokládá
Northrop Grumman Systems jako 100% dceru k 31. 12. 2025. Oficiální
USAspending award dokládá přesný název příjemce, UEI a parent recipient.
Citace, filing publication time, čas znalosti a omezení jsou v
`evidence/usaspending_noc_identity_20261002.json`.

Omezený živý běh použil jednu stránku po 100 řádcích. Po odmítnutí awardů
před doloženou vztahovou hranicí uložil devět nálezů; protože API hlásilo
další stránku, stav zůstal poctivě PARTIAL. Síťový replay nepřidal duplicity
a syntetický absent-UEI dotaz vrátil nulu. Hash odpovědí, přesné award ID
a request budget jsou v `evidence/usaspending_noc_live_20261002.json`.
Nálezy nově zachovávají relationship effective-from/to vedle knowledge time.
Nový captured-payload test ověřuje přesné NOC scope, vztahovou hranici,
knowledge time a odmítnutí cizího tickeru. Cílená sada prošla 25/25 a po
doinstalování připnutých runtime závislostí celá místní deterministická sada
**528/528** bez selhání, chyb a přeskočení. První clean-host diagnostika s
22 chybějícími importy se nepočítá jako úspěch; časy, otisky a omezení jsou
v `evidence/usaspending_noc_tests_20261002.json`.

Navazující GitHub `windows-latest` job `110990378505` na commitu
`8df9d476` dokončil **528/528** testů bez selhání, chyb a přeskočení;
workflow `37052898902` i deterministic release gate prošly. Stažený artifact
`11248265909` obsahoval úplný log a strojový JSON a jeho SHA-256 souhlasil
s upload logem. Auditní záznam je v
`evidence/usaspending_noc_windows_tests_20261002.json`.

Jde o druhý pilot, ne kompletní recipient/subsidiary nebo modification
historii, relevantní coverage, uživatelský Windows end-to-end či historické
vyhodnocení. Contracts i aerospace zůstávají PARTIAL/PENDING a inventář
0/21 DONE. Hostovaný Windows běh je pouze kompatibilita: neprovedl živý
USAspending sběr na uživatelově počítači; skóre ani obchodování se nemění.

## SEC 13F — deset přesně citovaných instrumentů (2. 10. 2026)

Omezený registr byl rozšířen o GOOGL, TSLA, AVGO, AMD a JPM. Spolu s
předchozími AAPL, MSFT, NVDA, AMZN a META nyní obsahuje deset kanonických
tickerů ze vstupu 687. Každý nový záznam spojuje ticker a issuer CIK z
oficiální SEC publikace, přesný CUSIP/název/třídu z 2026 Q2 13F securities
listu a odpovídající issuer/instrument/CUSIP z konkrétního Schedule 13G.
Citace, pole a čas znalosti jsou v
`evidence/sec13f_identity_expansion2_20261002.json`.

Navazující GitHub `windows-latest` job `110945954943` na commitu
`b112003c` dokončil **527/527** testů bez selhání, chyb a přeskočení;
workflow `37039539524` i deterministic release gate prošly. Stažený artifact
`11242338229` obsahoval log a strojový JSON a jeho SHA-256 odpovídal
hodnotě z upload logu. Přesný výsledek je v
`evidence/sec13f_identity_expansion2_tests_20261002.json`.

Registr dál není plné pokrytí, živý ZIP běh ani rekonstrukce amendments a
nemění skóre. Na tomto hostu stále chybí deklarovaný SEC User-Agent; skutečný
uživatelský Windows end-to-end běh a historická evaluace také chybí. Tento
hostovaný CI běh je jen kontrola kompatibility, nikoli koncová akceptace.
Institucionální specialista proto zůstává PILOT/PENDING a inventář 0/21 DONE.

## SEC 13F — pět přesně citovaných instrumentů (2. 10. 2026)

Omezený 13F registr už není jen AAPL: pro čtvrtletí 2026 Q2 nyní obsahuje
také MSFT, NVDA, AMZN a META. Každý z pěti záznamů má kanonický ticker,
desetimístný issuer CIK, devítimístný CUSIP, třídu instrumentu, hranice
čtvrtletí, čas znalosti a odkazy na oficiální SEC 13F securities list,
Schedule 13G a ticker/CIK publikaci. Konkrétní pole a omezení jsou v
`evidence/sec13f_identity_expansion_20261002.json`.

Loader nově ověřuje CUSIP check digit a issuer CIK. Řádek z 13F datasetu se
uloží jen při shodě CUSIP, normalizovaného názvu emitenta i třídy cenného
papíru; issuer CIK se zachová v detailu nálezu. Místní i navazující
hostovaná GitHub Windows sada prošly **527/527** testů. Windows workflow
`37021509189`, job `110885473281` uložil artifact `11234027903` a prošel
i deterministic release gate. Na tomto hostu chybí deklarovaný SEC
User-Agent, proto nebyl předstírán živý ZIP běh. Pět identit není coverage 687 tickerů,
nezahrnuje 13F amendments ani historickou corporate-action kontinuitu a
nemění skóre. Institucionální specialista zůstává PILOT/PENDING a inventář
0/21 DONE.

## Windows čas znalosti — oprava prošla celou sadou (2. 10. 2026)

Commit `9d83c221` prošel na GitHub `windows-latest` celou deterministickou
sadu **525/525** testů za 758,382 sekundy bez selhání, chyb a přeskočení.
Workflow `37012267755`, job `110854588580` uložil log a JSON jako artifact
`11228802805`; všechny ostatní joby i společný deterministic release gate
také skončily úspěšně. Tím je uzavřená konkrétní CI regrese hranice času
znalosti popsaná níže.

Jde o hostovanou kontrolu Windows kompatibility, ne o skutečný koncový běh
na uživatelově Windows počítači. Běh nepřinesl relevantní živé coverage
687 tickerů ani historické out-of-sample vyhodnocení. Proto se stav žádného
specialisty nemění a inventář zůstává 0/21 DONE.

## Windows čas znalosti — monotónní hranice reportů (2. 10. 2026)

Rozšířený Windows běh na commitu `20cdbd7` dokončil všech 524 testů, ale
odhalil dvě selhání as-of pohledu identity. Windows vrátil shodný wall-clock
čas nejen dvěma verzím, ale i konci prvního reportu a pozorování následujícího
reportu. Dotaz s časem prvního reportu proto správně podle stejného timestampu
viděl i pozdější zápis, a historický test ztratil hranici znalosti.

Společný `utc_now` nyní pod procesním zámkem zachovává UTC wall clock, ale
shodný nebo zpětný vzorek posune o jednu mikrosekundu. Konec reportu je tak
striktně za jeho pozorováními a další report začíná až potom. Perzistentní
`agent_run_id` zůstává druhou ochranou pořadí databázových verzí. Nový test
vynutí konstantní Windows clock přes oba reporty; původní dvě regrese prošly
pětkrát opakovaně. Celá místní sada má 525/525 PASS. Důkaz je v
`evidence/windows_monotonic_clock_tests_20261002.json`. Navazující GitHub
Windows běh na `9d83c221` prošel 525/525; nejde však o uživatelský
end-to-end běh ani dokončení specialisty.

## EPA ECHO — omezené facility-name leads (2. 10. 2026)

Nový veřejný EPA ECHO sběr dotazuje jen 104 kanonických tickerů z profilů
CHEMICALS, METALS, INDUSTRIAL, HOME a PACKAGING, nejvýše deset emitentů
za běh. Každý požadavek má 20sekundový timeout a 2MB limit; celý běh má
60 sekund a končí po třech chybách. Volné výsledky oficiálního exact-name
filtru jsou ještě jednou lokálně filtrovány přes přesnou normalizovanou
shodu. Truncovaný či odmítnutý payload zůstává PARTIAL a opakuje se po dni.

FRS Registry ID dokládá jen identitu facility z publisher datasetu. Shoda
názvu není datovaný vztah zařízení k emitentovi, úplnost skupiny, ekologická
odpovědnost ani dopad do skóre. Sběr je v runneru, source policy, perzistenci,
coverage diagnostice a UI. Živý bounded smoke ověřil jeden přesný DOW INC
záznam a nulový výsledek umělého jména; obsah a hash odpovědí jsou v
`evidence/epa_echo_live_20261002.json`. Celá sada po instalaci deklarovaných
závislostí prošla 524/524; důkaz je v `evidence/epa_echo_tests_20261002.json`.
Regulační specialista zůstává PARTIAL/PENDING a inventář 0/21 DONE.

## Windows CI — úplná deterministická sada prošla (2. 10. 2026)

Na commitu `2945cc9` dokončil GitHub `windows-latest` job 519/519 testů
za 709,826 sekundy bez selhání a chyb; prošel i společný deterministic
release gate. Workflow run `36995388024`, job `110800809096` uložil log
a JSON jako artifact `11222061817`. Tím je doložená Windows kompatibilita
SQLite lifecycle i deterministického pořadí identity při shodném čase.

Tento CI runner není skutečný koncový běh na uživatelově Windows počítači,
neprovedl živé zdroje v plném relevantním scope a nedokládá historické
out-of-sample vyhodnocení. Proto nemění žádný specialista na DONE/VERIFIED;
inventář zůstává 0/21.

## Windows sada — zachovaný plný scope, opravený časový limit (2. 10. 2026)

Běh na commitu `58990b3` nehlásil testovací chybu, ale GitHub jej zrušil
po dosažení 15minutového limitu během pozdní části plné sady. Předchozí
Windows běh potřeboval samotných 747 sekund na 518 testů; instalace a setup
se počítají do stejného job limitu. Nejde tedy o doklad průchodu.

Windows job má nyní pevný limit 25 minut. Rozsah 519 testů, ukládání logu,
JSON výsledek i release gate zůstávají beze změny; timeout či zrušení stále
znamená failure. Navazující běh `36995388024` celou sadu dokončil. Toto je
CI kompatibilita, nikoli skutečný koncový běh na uživatelově Windows počítači.

## Deterministické pořadí identity na Windows (2. 10. 2026)

Opakovaný GitHub Windows běh na commitu `aaa5197` spustil všech 518 testů
a doběhl bez předchozích SQLite lock chyb. Odhalil jediný další rozdíl:
dva po sobě uložené identity reporty mohou mít na Windows stejný timestamp.
Čtecí dotaz pak používal obsahový hash `version_id` jako tie-breaker a
vrátil starou/novou identitu v náhodném chronologickém pořadí.

Pořadí verzí a as-of čtení nyní při shodném čase používá perzistentní
monotónní `agent_run_id`; hash zůstává až posledním stabilním tie-breakerem.
Nový regresní test nastaví oběma reportům záměrně stejný čas a ověří AAPL →
APPL podle skutečného pořadí zápisu. Nejde o změnu identity, historie ani
skóre. Oprava prošla celou GitHub Windows sadou na `2945cc9`; uživatelský
koncový Windows běh a plné akceptace zůstávají otevřené.

## DOJ leads a Windows SQLite životní cyklus (2. 10. 2026)

Přibyl samostatný omezený sběrač oficiálních tiskových zpráv DOJ. Dotazuje
pouze title filtr, nejvýše 25 emitentů, dvě stránky po 25 položkách, tři
chyby a 60 sekund na běh. Odpověď má limit 2 MB a klient respektuje
publikovaný limit požadavků. Přijímá jen kanonické UUID, oficiální HTTPS
press-release URL, nebudoucí publisher datum a celé přesné slovní znění
dotazovaného názvu v titulku. Google proto nesmí automaticky představovat
Alphabet Inc. API shoda zůstává `UNVERIFIED`: nedokládá identitu emitenta,
odpovědnost ani úplné právní riziko a nemění skóre.

Skutečný omezený live smoke 2. 10. zachytil dva výsledky titulkového dotazu
Google a nulový výsledek uměle neexistujícího přesného názvu. Důkaz je v
`evidence/doj_live_20261002.json`; není přiřazen žádné akcii. Sběrač je
zapojený do runneru, perzistence, obnovovacího scope, UI, diagnostiky a
oddělené source policy. Chybná stránka, duplicitní UUID, změna total, limit
stránek nebo přístupová chyba nemohou vytvořit úplnou negativní kontrolu.

První skutečný GitHub Windows CI běh odkryl široké `ERROR` při mazání
dočasných SQLite souborů a doběhl do patnáctiminutového timeoutu. Příčinou
je standardní context manager `sqlite3.Connection`, který commitne či
rollbackne, ale spojení nezavře; Linux dovolí otevřený soubor smazat,
Windows ne. Všechny aplikační SQLite store nyní používají spojení, které
po transakci vždy uzavře native handle. Samostatné testy drží Python objekt
živý, ověřují commit, rollback, uzavření a přejmenování databáze.
Deterministický runner průběžně zapisuje traceback a strojový souhrn, aby
další Windows chyba nezmizela při timeoutu.

Místně prošlo 518/518 testů bez chyb a přeskočení. Windows oprava musí ještě
projít novým konkrétním GitHub CI během; ten stále není uživatelův skutečný
koncový Windows běh. Inventář proto zůstává 0/21 DONE. Další dostupné kroky
jsou EPA a datované zdravotnické/product/subsidiary identity; licence,
relevantní coverage a historické out-of-sample vyhodnocení zůstávají otevřené.

## OZK — oddělený přímý bankovní emitent a omezený živý pilot (1. 10. 2026)

Předchozí konkrétní modelová mezera OZK je vyřešena: registr přijímá
samostatný `issuer_identity_kind=direct_bank`, odlišný od SEC dceřiných
vztahů. Pro OZK/CERT 110 je schválen jen konkrétní ověřený FDIC 10-K
na oficiálním issuer IR, s přesným dokumentem, registrantem Bank OZK,
FDIC vydavatelem a common-stock symbolem OZK. Preferovaná třída OZKAP
není součástí této identity. Titulní strana datuje identitu k 31. 12. 2025;
IR index dokládá publikaci 25. 2. 2026. Čas znalosti je až
1. 10. 2026 20:43:33 UTC. Nevymýšlí se SEC CIK, holding ani rodič–dcera.
Další přímý emitent nebo nový dokument vyžaduje samostatnou zdrojovou
kontrolu a změnu schváleného seznamu; libovolný IR odkaz se nepřijímá.

Nálezy jsou `bank_issuer_financials` / `direct_bank_issuer`, s explicitní
neaplikovatelností dceřiného vztahu. Mají datovanou issuer/instrument
identitu, knowledge time a oddělenou report eligibility hranici; žádná
vlastnická ani vztahová historie se z nich neodvozuje. Výkazy CERT zůstávají
FDIC bankovními údaji, nikoli konsolidovanými údaji emitenta. Nová metadata
jsou součástí scheduler scope; změna znalosti neobchází uloženou denní kvótu.
Dosavadní SEC mapy, nejisté as-of CFR/MTB a kvalifikace vlastnictví FHN
zůstávají zachované.

Primární locatory, datum, krátké úryvky, manifest a skutečné odpovědi jsou
v `evidence/fdic_ozk_identity_20261001.json`. Původní modelová mezera v
`evidence/ozk_identity_model_gap_20261001.json` zůstává historickým snímkem,
nikoli tvrzením o nynějším registru. Originální PDF nebyl stažen; ověřena
byla oficiální textová extrakce. FDIC institutions uvádí Bank OZK a CERT
110; přesné finanční jméno je BANK OZK.

`evidence/fdic_ozk_live_20261001.json` má dva PASS případy: živý OZK
payload uložil dva výkazy k 31. 3. a 30. 6. 2026, replay zachycené odpovědi
nepřidal duplicity a skutečné volání pro neexistující CERT vrátilo prázdno.
Replay nepoužívá síť. Celkem proběhla čtyři nová FDIC API volání,
20 sekund / nejvýše 1 MB / jeden institutions a dva financials řádky.
Záporný případ neznamená úplnou absenci událostí u emitenta. Je to omezený
Linux pilot, nikoli Windows end-to-end nebo historická akceptace.

Registry nyní mapuje 18 z 22 BANK profilů kanonických 687 tickerů:
17 emitentů přes bankovní dcery a jeden přímý bankovní emitent. Prázdná
provozní databáze má 18 never-attempted a 0 usable, nikoli pokrytí 18 bank.
Chybí PNFP, RF, WAL a ZION, další relevantní dcery a změny vlastnictví.
Nové testy odmítají nesprávný issuer kind, CERT, dokument, publisher,
common/preferred instrument, datum i vymyšlený CIK či vztah; ověřují
persistenci/replay, knowledge time, scope a kvótu přes restart. FDIC má
24 PASS, diagnostika 13 PASS a celá místní sada 492/492 PASS. Časy,
otisky a kompilace/diff jsou v `evidence/fdic_ozk_tests_20261001.json`.

Bankovní specialista zůstává PILOT/PENDING a inventář 0/21 DONE.
Další dostupný krok: PNFP/RF datované bankovní identity; pak další banky,
FDA product/application/sponsor a CMS owner/provider. Skutečný Windows
koncový běh, relevantní provozní pokrytí a historická out-of-sample evaluace
zůstávají otevřené. Skóre ani obchodování se nemění.

## MTB — doložená dcera s neověřeným as-of; OZK vyžaduje jiný model (1. 10. 2026)

Nově je registrováno MTB (CIK 0000036270) → Manufacturers and Traders
Trust Company / CERT 588. SEC Exhibit 21.1 jmenuje banku (a/k/a M&T Bank),
ale vlastní řádek vztahu nemá výslovné datum ani vlastnické procento.
31. 12. 2025 v poznámce popisuje významnost vynechaných dalších dcer;
nepovažuje se za výslovný as-of uvedené vazby. Datum vlastnictví zůstává
neověřené. Přijímají se jen výkazy od konzervativní publication floor
18. 2. 2026 podle ověřeného SEC filing indexu, nikoli od výročního období.
Nálezy mají relationship_effective_from=null, relationship_as_of_verified=false
a samostatnou report eligibility hranici / filing_publication_floor / index
citaci. Nejde o datum akvizice, souvislou historii ani kompletní skupinu.

R1 stejného filing dokládá M&T BANK CORPORATION, CIK a Common Stock /
MTB, odděleně od prioritních tříd. FDIC institutions uvádí přesný právní
název a financials přesný MANUFACTURERS&TRADERS TR CO. Zdrojová jména,
SEC citace a krátké úryvky, data a skutečné publisher payloady jsou v
`evidence/fdic_mtb_identity_20261001.json`. Originální SEC HTML nebyl
stažen. RSSD není issuer CIK a výkazy CERT nejsou konsolidované hodnoty
emitenta. Čas znalosti nové vazby je až její pozorování 1. 10. 2026.

`evidence/fdic_mtb_live_20261001.json` má dva PASS případy: MTB uložilo
dva výkazy k 31. 3. a 30. 6. 2026, skutečné opakované zpracování zachyceného
payloadu nepřidalo duplicity a neexistující CERT vrátil prázdnou odpověď.
Replay je bez další sítě. V tomto kroku proběhla čtyři nová FDIC API
volání (jeden institutions, jeden první financials, dva smoke financials),
s 20sekundovým timeoutem, nejvýše 1 MB a jedním institutions / dvěma
financials řádky. Provozní kvóty a dávky zůstávají. Jde o omezený Linux
pilot, nikoli zápornou akceptaci emitenta, Windows běh nebo historii.

Současný oficiální Bank OZK 10-K na issuer IR výslovně uvádí FDIC,
CERT 110, registranta BANK OZK a common stock OZK; OZKAP je jiná
prioritní třída. IR seznam datuje filing 25. 2. 2026. Zdrojové odkazy
a locatory jsou v `evidence/ozk_identity_model_gap_20261001.json`.
Aktuální registr vyžaduje SEC vztahovou citaci a issuer CIK se SEC cestou;
neumí samostatnou identitu přímého bankovního emitenta s FDIC filingem.
Proto OZK zatím není přidané a neproběhl jeho FDIC API pilot. Nejde
o chybějící klíč: zbývá implementace odděleného validovaného identity kind,
zdrojového dokumentu a CERT, persistence/scheduler scope a živé kontroly.
Historický holding, současný SEC CIK ani parent–subsidiary se nevymýšlí.
Originální OZK PDF nebyl stažen; byla ověřena textová extrakce jeho titulní
strany přes webový výzkum. Tato mezera není akceptační důkaz OZK.

Mapa nyní má 17 z 22 BANK profilů kanonických 687 tickerů. Zbývá OZK,
PNFP, RF, WAL a ZION, další relevantní banky a změny vlastnictví skupin.
Captured-payload test ověřuje MTB zápis/replay, odmítnutí výkazu před
publication floor, čas znalosti, canonical scope a persistenci nejistoty.
Prázdná provozní databáze má 17 never-attempted a 0 usable, nikoli živé
pokrytí 17 bank; původní diagnostické clocky zůstávají. FDIC má 21 PASS,
diagnostika 12 PASS a celá místní sada 488/488 PASS. Kompilace/diff,
časy a otisky jsou v `evidence/fdic_mtb_tests_20261001.json`.

Bankovní specialista zůstává PILOT/PENDING, inventář 0/21 DONE.
Další dostupný krok: OZK direct-bank model nebo PNFP/RF bankovní dcery;
poté ostatní banky, FDA product/application/sponsor a CMS owner/provider.
Skutečný Windows koncový běh, změřené relevantní provozní pokrytí,
přístupy/licence a historická out-of-sample evaluace zůstávají otevřené.
Skóre ani obchodování se nemění.

## FHN a KEY — datované dcery a vlastnická výjimka (1. 10. 2026)

Registr doplněn o FHN (CIK 0000036966) → First Horizon Bank / CERT 4977
a KEY (CIK 0000091576) → KeyBank National Association / CERT 17534.
Obě SEC přílohy výslovně datují vztahové tvrzení k 31. 12. 2025;
filing indexy odděleně potvrzují zveřejnění 26. 2. 2026 (FHN) a
23. 2. 2026 (KEY). Datum akvizice ani souvislá historie se neodvozují.

FHN tabulka uvádí přímou konsolidovanou bankovní dceru, ale footnote (1)
výslovně zachovává 300 000 nehlasovacích prioritních akcií mimo vlastnictví
First Horizon, s celkovou likvidační preferencí 300 milionů USD a bez
účasti s common stock při likvidaci. Proto důkaz nepřijímá bezvýhradné
100% vlastnictví banky ani poměrné přepočítávání bankovních hodnot.
KEY footnote naproti tomu výslovně uvádí 100% vlastnictví KeyBank.
FHN vynechává neaktivní entity a bankovní dcery uvádí samostatně;
KEY vynechává dcery nevýznamné v souhrnu. Registry netvrdí celé skupiny.

SEC R1 reporty dokládají issuer CIK a common-stock symbol, odděleně
od prioritních tříd. FHN R1 používá FIRST HORIZON CORP; plný název
First Horizon Corporation je doložen v úvodu Exhibit 21. KEY R1
uvádí KeyCorp a Common Shares / KEY. Citace, krátké úryvky, locatory,
vlastnická výjimka a skutečné FDIC institutions/financials payloady
s časy jsou v `evidence/fdic_fhn_key_identity_20261001.json`.
Originální SEC HTML nebyl stažen. Financial názvy jsou přesně
FIRST HORIZON BANK a KEYBANK NATIONAL ASSN. RSSD není SEC CIK;
výkazy jednotlivého CERT nejsou konsolidované hodnoty emitenta.
Nové vazby jsou známé až od pozorování 1. 10. 2026.

`evidence/fdic_fhn_key_live_20261001.json` má tři PASS případy:
každá nová banka uložila výkazy k 31. 3. a 30. 6. 2026, skutečné
znovuzpracování zachycené odpovědi nevytvořilo duplicity a neexistující
CERT vrátil prázdný publisher payload. Replay je bez další sítě.
V tomto kroku proběhlo sedm API volání (2 institutions, 2 první
financials, 3 smoke financials), s 20sekundovým timeoutem a limitem
1 MB / jeden institution / dva financials řádky. Provozní dávkové
a denní limity se nemění. Jde o omezený Linux pilot; neexistující
CERT nedokládá zápornou akceptaci emitenta nebo Windows běh.

Registry mapuje 16 z 22 BANK profilů kanonických 687 tickerů;
chybí MTB, OZK, PNFP, RF, WAL a ZION a další relevantní banky /
datované změny vlastnictví. Rozšířený captured-payload test ověřuje
zápis/replay a odmítnutí výkazu před vztahovým datem, časem znalosti
či mimo canonical scope. Diagnostika v prázdné provozní databázi
ukazuje 16 never-attempted a 0 usable, bez falešného živého pokrytí.
Předchozí testovací clocky zůstávají. FDIC má 21 PASS, diagnostika
11 PASS a celá místní sada 487/487 PASS; kompilace/diff a časy/otisky
jsou v `evidence/fdic_fhn_key_tests_20261001.json`.

První plný pokus měl 385 načtených testů a 21 importních chyb, protože
obnovené prostředí nemělo předepsané závislosti. Po instalaci requirements
s constraints repozitáře prošel opakovaný běh bez změny testovaných souborů;
první neúspěch a shodné otisky jsou zachovány v testovacím důkazu.

Banky zůstávají PILOT/PENDING a inventář 0/21 DONE. Další dostupný
krok: MTB/OZK a zbývající bankovní identity, další dcery / změny
vlastnictví, FDA product/application/sponsor a CMS owner/provider.
Skutečný Windows koncový běh, relevantní provozní pokrytí, přístupy/
licence a historická out-of-sample evaluace zůstávají otevřené.
Skóre a obchodování se nemění.

## COF a EWBC — další dvě datované bankovní dcery (1. 10. 2026)

Registr doplněn o COF (CIK 0000927628) → Capital One, National Association /
CERT 4297 a EWBC (CIK 0001069157) → East West Bank / CERT 31628.
Obě SEC přílohy výslovně uvádějí as-of 31. 12. 2025. Filing indexy
odděleně potvrzují zveřejnění 19. 2. 2026 (COF) a 27. 2. 2026 (EWBC).
Nejde o datum akvizice ani souvislou historickou vlastnickou vazbu.
Obě přílohy výslovně vynechávají ostatní dcery považované v souhrnu
za nevýznamné: ani tato bankovní mapa netvrdí úplnost skupin.

R1 reporty stejných filings nezávisle potvrzují issuer CIK a common-stock
symbol. COF R1 používá CAPITAL ONE FINANCIAL CORP; parent-company sloupec
Exhibit 21 uvádí plný Capital One Financial Corporation. Preferred-stock
symboly z téhož R1 nejsou přiřazené k COF common-stock scope. EWBC příloha
navíc výslovně uvádí 100% vlastnictví East West Bank. SEC úryvky, locatory,
indexy a skutečné FDIC institutions/financials odpovědi s časy jsou v
`evidence/fdic_cof_ewbc_identity_20261001.json`. Originální SEC HTML nebyl
stažen. FDIC RSSD není CIK emitenta a bankovní výkazy nejsou jeho
konsolidované hodnoty. Přesné financial názvy jsou CAPITAL ONE NATIONAL
ASSN a EAST WEST BANK; jiné jméno se fuzzy nedoplňuje. Čas znalosti
nových vazeb je až 1. 10. 2026, nikoli datum výročního období.

`evidence/fdic_cof_ewbc_live_20261001.json` obsahuje tři PASS případy:
každá nová banka uložila dva kvartální výkazy (31. 3. a 30. 6. 2026),
skutečné opakované zpracování zachycené odpovědi nevytvořilo duplicity
a neexistující CERT vrátil prázdný publisher payload. Replay se provádí
bez další sítě. V tomto kroku proběhlo sedm API volání: dvě institutions,
dvě první financials a tři smoke financials. Každý dotaz má 20sekundový
timeout, nejvýše 1 MB a jeden institution / dva financials řádky.
Dávkové/denní limity provozního sběrače se nemění. Jde o omezený Linux
pilot; absence neexistujícího CERT není negativní akceptace celé firmy.

Mapa nyní pokrývá 14 z 22 BANK profilů kanonických 687 tickerů;
chybí FHN, KEY, MTB, OZK, PNFP, RF, WAL a ZION a další relevantní banky
skupin / datované změny vlastnictví. Rozšířený test skutečných captured
payloadů ověřuje zápis, replay, odmítnutí výkazu před datem vztahu,
čas znalosti a canonical scope. Nová diagnostická kontrola měří
14 never-attempted a 0 usable v prázdné provozní databázi, nikoli živé
pokrytí 14 bank. Původní clocky předchozích onboarding kroků zůstávají.
FDIC má 21 PASS, diagnostika 10 PASS; celá místní sada 486/486 PASS,
kompilace a diff kontrola jsou uložené s otisky a časy v
`evidence/fdic_cof_ewbc_tests_20261001.json`.

Bankovní specialista zůstává PILOT/PENDING a inventář 0/21 DONE.
Další dostupná práce: FHN/KEY a zbývající bankovní identity, další dcery
a změny vlastnictví, FDA product/application/sponsor a CMS owner/provider
vazby. Skutečný Windows koncový běh, změřené relevantní provozní pokrytí,
přístupy/licence a historická out-of-sample evaluace zůstávají otevřené.
Skóre ani obchodování se nemění.

## ALLY a CFR — bankovní identity a konzervativní časová hranice (1. 10. 2026)

Přidané vazby ALLY (CIK 0000040729) → Ally Bank / CERT 57803 a
CFR (CIK 0000039263) → Frost Bank / CERT 5510. Ally Exhibit 21 výslovně
uvádí 31. 12. 2025. CFR Exhibit 21.1 potvrzuje Frost Bank a 100 % hlasovacích
práv Cullen/Frost, ale neuvádí vlastní as-of datum. Pro nový CFR scope je
proto hranice reportů konzervativně 5. 2. 2026 podle potvrzeného SEC filing
indexu, nikoli 31. 12. 2025. Tato hranice není datum nabytí vlastnictví;
skutečné datum účinnosti a souvislá historická vazba zůstávají neověřené.
Evidence má CFR as_of=null, samostatný publication date a popsané pravidlo.
Uložené nálezy mají relationship_effective_from=null a
relationship_as_of_verified=false; odděleně uchovávají report eligibility
hranici, basis filing_publication_floor a filing index citaci se stejným
CIK. Úplná dvojice provenance je validovaná a mění identitu scheduleru.

SEC R1 reporty ve stejných filings dokládají issuer CIK a common-stock
symbol; filing indexy odděleně potvrzují zveřejnění 25. 2. 2026 (ALLY)
a 5. 2. 2026 (CFR). Citace, krátké úryvky a skutečné FDIC institutions /
financials payloady s časy jsou v `evidence/fdic_ally_cfr_identity_20261001.json`.
Originální SEC HTML nebyl stažen. FDIC institutions uchovává původní
„Frost  Bank“ se dvěma mezerami; proti právnímu SEC jménu byl porovnán
pouze whitespace. Financials má samostatně doložený přesný FROST BANK
a ALLY BANK. Názvy zdrojů se nepřepisují ani fuzzy nedoplňují. Issuer CIK
není FDIC holdco RSSD a bankovní výkazy nejsou konsolidované hodnoty emitenta.

`evidence/fdic_ally_cfr_live_20261001.json` má tři PASS případy: obě banky
uložily po dvou výkazech, zachycený payload byl skutečně znovu zpracován
bez duplicit a neexistující CERT vrátil prázdnou odpověď. V tomto kroku
proběhlo sedm API volání (2 institutions, 2 první financials, 3 smoke
financials); replay je bez další sítě. Živé případy předcházejí doplnění
publication-floor persistence. Jejich původní identity a časy se zachovávají;
po této opravě byl skutečný zachycený payload zpracován offline a výsledné
nálezy jsou v identity důkazu, bez dalších API volání. Timeout 20 sekund, nejvýše dva
financials řádky a denní/dávkové limity sběrače se nemění. Jde o omezený
Linux publisher pilot, nikoli Windows, celý bankovní sektor nebo historie.

Registry mapuje 12 z 22 BANK profilů kanonických 687 tickerů; chybí COF,
EWBC, FHN, KEY, MTB, OZK, PNFP, RF, WAL a ZION, další relevantní banky
skupin a změny vlastnictví. Společný captured-payload test rozšířený o
ALLY/CFR ověřuje znalost/scope, hranici reportů a uchování CFR nejistoty.
Diagnostika před prvním provozním pokusem zůstává 12 never-attempted a
0 usable, bez falešné akceptace. FDIC má 21 PASS, diagnostika 9 PASS,
celá místní sada 485/485 PASS; kompilace/diff a časy/otisky jsou v
`evidence/fdic_ally_cfr_tests_20261001.json`. První lokální pokus měl
21 import chyb hostitele; po instalaci verzovaných requirements/constraints
prošel původní kód (484 PASS); po doplnění provenance proběhlo nové
plné ověření 485 PASS. Obě etapy jsou odlišené v test evidence.

Banky zůstávají PILOT/PENDING, celý inventář 0/21 DONE. Další dostupný
krok: COF/EWBC a zbývající datované dcery, FDA product/application/sponsor
a CMS owner/provider vazby. Windows koncový běh, relevantní živé pokrytí,
přístupy/licence a historická out-of-sample evaluace zůstávají otevřené.
Skóre a obchodování se nemění.

## CFG a HBAN — další datované bankovní identity (1. 10. 2026)

Přidané vazby CFG (CIK 0000759944) → Citizens Bank, National Association /
CERT 57957 a HBAN (CIK 0000049196) → The Huntington National Bank / CERT 6560.
CFG vztah je doložený v části Summary prospektového dodatku datovaného
22. 1. 2026. Doprovodný prospekt z 4. 10. 2024 ani výroční období 2025
nenahrazují toto datum. Exhibit 21.1 potvrzuje úplný právní název CFG banky,
ale nemá vlastní potvrzené as-of datum. HBAN Exhibit 21.1 výslovně uvádí
31. 12. 2025. Jde o doložená data vztahových tvrzení, nikoli datum akvizice
nebo potvrzení celé souvislé historie vlastnictví.

SEC 8-K dokládají issuer CIK/common-stock ticker; data události a podpisu
jsou oddělená, nepotvrzený filing date zůstává nepřijatý. Originální SEC
HTML nebyl stažen. Skutečné FDIC institutions a financials payloady jsou
uložené s časy v `evidence/fdic_cfg_hban_identity_20261001.json`. Přesné
financial názvy jsou CITIZENS BANK NATIONAL ASSN a HUNTINGTON NATIONAL BANK.
Identita se použije až od pozorování 1. 10. 2026; RSSD holdco není SEC CIK
a výkazy banky nejsou konsolidované hodnoty emitenta.

`evidence/fdic_cfg_hban_live_20261001.json` obsahuje tři PASS případy:
obě banky po dvou výkazech, skutečné opakované zpracování zachyceného
payloadu bez duplicit a živý prázdný neexistující CERT. Celkem sedm
síťových volání (2 institutions, 2 první financials, 3 smoke financials),
20sekundový timeout a nejvýše dva výkazy na financials dotaz. Replay
další síťové volání nedělá. Pilot na Linuxu není Windows ani úplná live
coverage. Perzistentní dávkové/denní limity sběrače se nemění.

Registry nyní mapuje 10 z 22 BANK profilů kanonických 687 tickerů.
Zbývá ALLY, CFR, COF, EWBC, FHN, KEY, MTB, OZK, PNFP, RF, WAL a ZION,
další banky skupin a změny vlastnictví. Nové testy odmítají výkazy před
datem vztahu, práci před časem znalosti a mimo scope; diagnostika odlišuje
mapování od nikdy neprovedeného provozního pokusu. FDIC má 20 PASS,
diagnostika 8 PASS a celá místní sada 483/483 PASS včetně UI a 687 vstupu.
Kompilace/diff kontroly a otisky jsou v `evidence/fdic_cfg_hban_tests_20261001.json`.
První lokální pokus měl 21 import chyb kvůli chybějícím závislostem;
po instalaci verzovaných requirements/constraints prošel nezměněný kód.

Bankovní stav zůstává PILOT/PENDING, inventář 0/21 DONE. Další dostupná
práce: zbývajících 12 bankovních emitentů (začít ALLY/CFR), další datované
dcery a FDA product/application/sponsor nebo CMS owner/provider vazby.
Skutečný Windows běh, kompletní relevantní živé pokrytí, přístupy/licence
a historické out-of-sample důkazy zůstávají otevřené. Skóre ani obchodování
se nemění.

## TFC a FITB — dvě další doložené bankovní dcery (1. 10. 2026)

Registr rozšířen o TFC (CIK 0000092230) → Truist Bank → CERT 9846 a
FITB (CIK 0000035527) → Fifth Third Bank, National Association → CERT 6672.
Oficiální SEC Exhibit 21 a 8-K cover doložily vztah a issuer/common-stock
symbol; přímo zachycené FDIC institutions odpovědi potvrdily přesný CERT,
právní název a aktivní banku. Financials vrací TRUIST BANK a FIFTH THIRD
BANK NA. Citace se vážou na správný CIK a identita se použije až od času
zjištění v tomto běhu. Datum vztahu TFC je 31. 12. 2025, ale FITB Exhibit
21 výslovně uvádí 15. 2. 2026: datum výročního období proto není zaměněné
za datum vztahu. Starší FITB výkazy pod tímto novým vztahem odmítáme.

`evidence/fdic_tfc_fitb_identity_20261001.json` uchovává SEC pozorované
úryvky s citacemi/locatory, datované identity a skutečné FDIC institutions
/financials payloady. Original SEC HTML nebyl stažen; údaj o filing date
TFC 8-K není potvrzený a důkaz ho nenahrazuje datem podpisu. TFC 8-K je
datovaný událostí 5. 6. a podpisem 8. 6. 2026. Instrument scope je pouze
doložený CIK a ticker, nikoli CUSIP/ISIN nebo aktuální exchange/corporate-
action historie. Bankovní data nejsou konsolidované údaje emitenta;
RSSD holdco čísla se neinterpretují jako SEC CIK.

Smoke `--sources fdic --fdic-tickers TFC FITB` prošel třemi skutečnými
případy v `evidence/fdic_tfc_fitb_live_20261001.json`: obě nové banky
uložily po dvou kvartálních výkazech, zachycený payload byl skutečně znovu
zpracován bez duplicit a záporný neexistující CERT vrátil prázdný výsledek.
Dotazy mají nejvýše dva výkazy; sedm živých API volání v tomto kroku
zahrnuje dvě institution, dvě první financial a tři smoke financial
kontroly. Replay neprovádí další síťové dotazy. Jde o publisher pilot
na Linuxu, nikoli Windows koncový běh, relevantní live coverage nebo
historickou akceptaci.

Změřený rozsah registry je osm tickerů z 22 BANK profilů. Chybí ALLY,
CFG, CFR, COF, EWBC, FHN, HBAN, KEY, MTB, OZK, PNFP, RF, WAL a ZION;
další dcery již mapovaných emitentů a změny vlastnictví jsou samostatná
otevřená práce. Registry není úplnost skupin ani runtime coverage.
FDIC testy mají 19 PASS a diagnostika sedm PASS; celá místní sada
481/481 PASS včetně UI a kanonických 687 tickerů. Otisky a časy jsou v
`evidence/fdic_tfc_fitb_tests_20261001.json`. Nové testy používají skutečně
zachycené payloady, ověřují datované FITB hranice, znalost/scope a rozdíl
mezi novým mapováním a nikdy neprovedeným provozním sběrem.

Stav bank zůstává PILOT/PENDING a celý inventář 0/21 DONE. Další kroky:
zbývajících 14 emitentů a relevantní dcery/změny vlastnictví, datované
FDA product/application/sponsor a CMS owner/provider vazby. SEC kontakt,
FINRA, FRED a EIA přístup stále nejsou nastavené na tomto hostu; skutečné
Windows a historické out-of-sample důkazy chybějí. Skóre a obchodování
se nemění.

## Omezené FDIC obnovování a skutečné běhové pokrytí (1. 10. 2026)

FDIC sběr nově ukládá pokus do SQLite před požadavkem: výchozí limit je
pět bank v dávce a deset požadavků za UTC den napříč restartovanými běhy.
Selhání i přerušený pokus spotřebují kapacitu. Provider lease brání
souběžnému sběru; rezervace ověřuje jeho token. Nikdy nezkoušené banky
mají přednost, potom nejstarší splatné pokusy. Použitelný výsledek se
obnoví za 30 dní, neúplný/prázdný/chybný/přerušený za jeden den.
Identita zahrnuje ticker, CERT, přesná jména, datované vztahy a citace;
nově známý financial alias či změna identity otevře nový scope bez
přepisování starých pokusů. Sběr omezuje scope na kanonický vstup.

Po 60 sekundách se nezahajuje další požadavek; právě běžící požadavek
má 20sekundový síťový timeout. Tři síťová/strukturální selhání ukončí
dávku, HTTP 401/403 okamžitě uloží 24hodinovou pauzu a 429 hodinovou.
Odpověď má nejvýše 1 MB a dva výkazy; duplicitní report date, odmítnutý
CERT/jméno/datum či chybějící hodnoty nedokládají použitelnou kontrolu.
Prázdná odpověď je samostatný stav EMPTY, nikoli úplná záporná akceptace
emitenta. NO_DUE_WORK znamená pouze žádnou splatnou práci. Omezený
recheck v smoke skutečně znovu zpracuje zachycený payload pro deduplikaci;
nezaměňuje přeskočený požadavek s replay testem.

Provozní diagnostika FDIC odděluje 22 relevantních BANK profilů, šest
mapovaných tickerů/bank, 16 nemapovaných, nikdy nezkoušené, zastaralé,
použitelné, částečné, prázdné, chybné a přerušené pokusy. Scope je výslovně
latest-two-reports-per-CERT; nedokládá úplnost dceřiných skupin. Staré
nálezy bez nové evidence pokusu nevytvářejí falešné běhové pokrytí.
Neplatný uživatelský identity manifest je samostatná chyba diagnostiky.

`evidence/fdic_scheduler_replay_20261001.json` ověřuje uložené skutečné
publisher payloady všech šesti mapovaných bank. Při simulovaném limitu
2/dávku a 3/den proběhlo 2/1/0 pokusů první den, 2/1/0 další den a
obnova dvou bank po 30 dnech bez duplicit. SQLite se před každou dávkou
znovu otevřela. Vzniklo 12 verzí výkazů; šest použitelných bank v tomto
replay není nové živé pokrytí. Důkaz obsahuje původní pozorované časy,
zdrojové cesty a otisky payloadů i kódu. V tomto kroku neproběhl žádný
nový síťový sběr ani Windows běh nebo historická evaluace.

Cílené FDIC kontroly mají 18 PASS, diagnostika šest PASS. Celá místní
sada má 479/479 PASS včetně UI a přesného 687tickerového vstupu;
časy a otisky jsou v `evidence/fdic_scheduler_tests_20261001.json`.
Bankovní specialista zůstává PILOT/PENDING a inventář 0/21 DONE.
Další práce: doložit zbývajících 16 bankovních emitentů a další relevantní
dcery, rozšířit datované FDA produkt/application/sponsor a CMS owner/provider
vztahy. Chybějící SEC/FINRA/FRED/EIA přístup na tomto hostu, skutečný
Windows běh a historické vyhodnocení zůstávají otevřené. Skóre ani
obchodování se nemění.

## Další dvě bankovní dcery — PNC a USB (1. 10. 2026)

SEC 2025 10-K/Exhibit 21 a skutečné FDIC institutions odpovědi doložily
PNC → PNC Bank, National Association → CERT 6384 a USB → U.S. Bank
National Association → CERT 6548. Registr obsahuje samostatné issuer
CIK, citaci emitenta, datum vztahu 31. 12. 2025 a čas skutečné znalosti
v tomto běhu. Obě SEC citace nového issuer CIK se musí shodovat v cestě;
CIK a citace se ukládají do nálezu spolu s bankovním CERT, nikoli místo něj.
Přesné FDIC financial names byly získány přímo z API. Dřívější časy
tyto nové identity nepoužijí. Bankovní údaje nejsou konsolidovanými
hodnotami akcie a nepřidávají se do skóre.

`evidence/fdic_expansion_identity_20261001.json` ukládá podklady vztahu,
SEC pozorované úryvky a skutečné FDIC payloady. Přepínač smoke
`--fdic-tickers PNC USB` ověřil jen nové banky: dvě kladné kontroly
s dvěma výkazy na banku a replay bez duplicit, plus záporný neexistující
CERT. Všechny tři případy jsou PASS v
`evidence/fdic_expansion_live_20261001.json`. Jde o pilot na Linuxu,
nikoli Windows end-to-end nebo historickou akceptaci.

Změřená úplnost mapování je šest tickerů z 22 BANK profilů; nezaručuje
všechny banky uvnitř těchto šesti skupin nebo skutečné provozní pokrytí.
Další dostupné kroky jsou 16 zbývajících emitentů, další relevantní dcery
a omezené obnovovací dávky FDIC. Zdravotnické produktové/owner identity,
další specialisté a Windows/historické důkazy zůstávají v checkpointu.
Inventář zůstává **0/21 DONE**, bankovní specialista **PILOT/PENDING**.

Celá místní sada prošla **468/468 testy**, včetně 687tickerových a UI
kontrol. Devět cílených FDIC testů ověřuje nové issuer citace, odmítnutí
jiného CIK, čas znalosti, scope, deduplikaci a neobchodní povahu dat.
Otisky kódu i testovaného registru jsou v
`evidence/fdic_expansion_tests_20261001.json`. První pokus měl chyby
importů chybějících závislostí hostitele; po instalaci projektových
requirements podle uložených constraints se kód testů nezměnil a
celá sada prošla. Tento bootstrap je zaznamenaný v testovém důkazu.

## OFAC alternate entity names (1. 10. 2026, navazující checkpoint)

Denní OFAC collector nově připojuje oficiální ALT.CSV přes ENT_NUM;
ověřuje ALT_NUM, typ aka/fka/nka a všechny rodiče před zahájením kontrol.
Jména fyzických osob/plavidel/letadel se neuchovávají jako emitenti.
Přesný SEC název hledá mezi primary i alternate entity names; více
aliasů téhož SDN vytvoří jeden UNVERIFIED kandidát s oběma otisky a
konkrétními shodnými jmény. Bez ALT nemůže výsledek být úplnou kontrolou
tohoto scope. Chyba druhého exportu nezapisuje falešnou kontrolu bez
nálezu. Starší primary-only historie nezvyšuje rozšířené pokrytí:
verzovaný scope klíč zařadí takovou identitu ihned znovu do fronty.

Rozpočet je SDN do 8 MB a ALT do 2 MB, každý stažený nejvýše jednou
za běh se stejnými povolenými redirect hosty a timeoutem 20 sekund.
Oddělené otisky nejsou důkazem atomické publikace. Weak aliases v
remarks, adresy/spillover, Non-SDN a vlastnické look-through zůstávají
otevřené; jmenná shoda nedokládá vztah sankcionovaného subjektu k akcii.

Oficiální DAT_SPEC/Tutorial byly znovu ověřeny. Živý parser prošel třemi
případy v `evidence/ofac_alias_live_20261001.json`: primary entity,
AERO-CARIBBEAN (ENT_NUM 36, ALT_NUM 12) a absence konkrétního jména v
obou exportech. 19 452 primary řádků / 10 006 entit / 20 220 ALT řádků
jsou rozsah publikace, nikoli akceptované pokrytí watchlistu.

Celá deterministická sada má **465/465 PASS**, včetně 687tickerových
a UI kontrol; 9 cílených OFAC testů zahrnuje chybu ALT, sirotčí/duplicitní
ID, vynechání osobních aliasů, scope migraci a deduplikaci. Časy a otisky
testovaného kódu jsou v `evidence/ofac_alias_tests_20261001.json`.
Inventář zůstává **0/21 DONE**. Windows, datované identity a historická
evaluace nejsou tímto testem doložené. Další konkrétní kroky a přístupové
blokace zůstávají v aktuálním implementačním checkpointu.

## Živé veřejné zdroje a opravy jejich parserů (1. 10. 2026)

V `evidence/specialist_live_20261001.json` je 13 skutečných kontrol:
čtyři kladné FDIC banky (po dvou výkazech), záporný neexistující CERT,
FDA drug kandidát, tři záporné enforcement dotazy, CRL schéma a záporný
CRL dotaz, kladné modelové svolání NHTSA a odmítnutí neplatného modelu.
Opakované zpracování FDIC/NHTSA nevytvořilo duplicity. Jde o omezený
zdrojový test na aktuálním hostiteli, nikoli o Windows akceptaci nebo
pokrytí všech 687 firem. Inventář proto zůstává PILOT/PARTIAL.

Živý FDIC vrací finanční názvy jako `BANK OF AMERICA NA`, nikoli celý
právní název z SEC. Registr proto obsahuje přesný název doložený API
pro shodný CERT, citaci a čas prvního pozorování. Dřívější historický
běh tento alias nepoužije. Odmítnuté řádky jsou viditelné jako PARTIAL
a zvlášť od použitelné banky.

NHTSA živě vrátila `18/06/2025`: parser nyní důsledně používá
den/měsíc/rok, ponechává datum prvního pozorování oddělené a odmítnutý
záznam znovu nabídne po dni. Test fronty již nevolá živé API při
ověřování neměnnosti seznamu; unit suite nezávisí na těchto službách.

Připomínku k openFDA tempu jsme ověřili proti oficiálnímu
`https://open.fda.gov/apis/authentication/`: aktuálně uvádí 240/min
s klíčem i bez něj, bez klíče 1 000/den. Odstup 0,35 s proto zůstává.

## Denní kapacita FDA/FINRA pro 687 tickerů (1. 10. 2026)

Denní plánovač má nyní rozpočet 40 FDA a 75 FINRA emitentů. Při úplných
odpovědích a 687 doložených SEC identitách je první oběh nejvýše 18,
respektive 10 denních spuštění; tím se vejde do obnovovacích oken 30 a
15 dní. Pořadí ukládá SQLite a další den pokračuje u dalších firem.
FDA klient rozkládá požadavky nejméně o 0,35 s; nejvýše 40 × 4 endpointy ×
2 stránky znamená 320 požadavků za den, pod veřejným denním limitem 1 000.
Skutečné pokrytí může zdržet výpadek, 429, chybějící identita, neúplná
odpověď či nepřítomné FINRA přihlašovací údaje; zobrazuje ho UI.

## Stránkování openFDA (1. 10. 2026)

FDA pro drug/device/food recall a CRL nyní při překročení jedné stránky
načte druhou stránku pomocí oficiálního `skip`/`limit` (výchozí rozpočet
nejvýše 2 × 100 záznamů na endpoint a firmu). Úplnost je ověřena proti
`meta.results.total` a stránkovému offsetu. Přesah nebo nestabilní odpověď
zůstává `PARTIAL`; po dni lze s vyšším výslovným rozpočtem dohledat další
stránku. Kandidáti nadále nejsou důkazem vztahu produktu k emitentovi.

## Opakování částečných kontrol FDA/FINRA (1. 10. 2026)

Odpověď dosažená na limitu není úplná. Dávkový plánovač ji nabídne znovu
po dni, ale nejprve obslouží identity bez jakékoli kontroly a starší
kontroly. Opakování se stejným limitem stránek samo o sobě chybějící
stránky nedohledá; takový výsledek zůstává `PARTIAL`. Přehled pokrytí
ukazuje skutečné intervaly: FDA 30 dní a FINRA 15 dní.

## Citigroup / FDIC CERT 7213 (1. 10. 2026)

V datovaném registru bank přibyla vazba C na Citibank, National Association
(FDIC CERT 7213). Citigroup ji uvádí jako dceřinou společnost v SEC Exhibit
21.01 k 31. 12. 2025 a FDIC ji identifikuje pod tímto certifikátem. Scout
zprávu přiřadí až po ověření čísla certifikátu a přesného jména ve výsledku;
údaje jsou údaji dceřiné banky, nikoli konsolidovanou hodnotou akcie C.
Živé schéma BankFind a kladný/záporný běh stále čekají.

## Kumulativní pokrytí FDA a FINRA (1. 10. 2026)

UI vedle poslední dávky zobrazuje počet aktivních identit pozorovaných
v SEC a pro FDA/FINRA rozlišuje úplnou kontrolu v posledních 30 dnech,
neúplnou odpověď s limitem stránek, nikdy/stará data a historicky někdy
provedenou kontrolu. Kontrola pro starší nebo jinak pojmenovanou identitu
se k aktuální firmě nepřičítá. Jmenovatel není automaticky 687: dokud SEC
nepozoroval přesnou identitu všech tickerů, u zbývajících není z čeho
FDA/FINRA vyhledávání spouštět. Tato metrika nemění skóre ani nezastírá
neúplný výsledek jako negativní událost.


## FDA potravinové svolávací akce (1. 10. 2026)

Stejný omezený FDA scout nově dotazuje vedle léčiv a zdravotnických
prostředků také oficiální `food/enforcement.json`. I zde přijme pouze
shodu celého `recalling_firm` s pozorovaným SEC názvem; `recall_number`
je oddělené podle typu produktu a záznam zůstává `UNVERIFIED` bez datované
vazby konkrétního výrobku na emitenta. Potravinový profil tak dostává
samostatnou stopu, nikoli automatický závěr o dopadu svolání.


## FDA Complete Response Letters (1. 10. 2026)

FDA scout vedle drug/device svolávacích akcí vyhledává veřejné Complete
Response Letters v oficiálním openFDA `transparency/crl.json`. Každý dotaz
na název aktivně pozorovaného SEC emitenta znovu ověřuje celý `company_name`,
typ `COMPLETE RESPONSE`, číslo aplikace, soubor a platné datum dopisu. Dopis
je samostatný `UNVERIFIED` kandidát, bez automatického přiřazení produktu,
aplikace či ekonomického dopadu na akcii. FDA může historický dopis zveřejnit
později; `letter_date` se ukládá odděleně a dostupnost začíná nejdříve
skutečným prvním pozorováním. Pokud selže některý FDA endpoint, kontrola
emitenta se neoznačí za hotovou; limit odpovědi hlásí `PARTIAL`.
Lokální pozitivní a negativní testy prošly. Živý běh, datovaný
application/sponsor/product crosswalk a skutečné pokrytí zatím chybějí.

## Viditelný stav všech zdrojů (30. 9. 2026)

Denní runner nyní po dokončení zapisuje do SQLite poslední stav SEC, FRED,
EIA, USAspending, hledání příjemců, FDA, FINRA, FDIC, 13F a NHTSA včetně
počtu zkontrolovaných položek, nových nálezů a typu chyby. UI ukazuje i
zdroj bez nálezu nebo takový, který ještě neběžel. `WAIT_ACCESS` a
`WAIT_IDENTITY` se nepletou s nulovou událostí. Historie běhů zůstává pro
audit; při pádu před dokončením běhu zůstane vidět poslední dokončený záznam,
takže údaj není živý průběhový čítač. Denní Windows spouštěč při každém startu
načte také uživatelský FINRA Client ID a Secret bez jejich tisku do konzole.

## NHTSA modelové svolávací akce (30. 9. 2026)

Nový veřejný NHTSA API scout se po 30 dnech vrací k datovaně doloženému
modelu a ročníku. Pilot je TSLA → Tesla Model 3, modelový rok 2026, s citací
Tesla 2025 10-K a přesným porovnáním výrobce, značky, modelu a ročníku v
každém záznamu. Ukládá číslo kampaně, komponentu a textové shrnutí, odděluje
datum přijetí hlášení od prvního pozorování a nevyvozuje dopad na tržby ani
skóre. Měsíční dávka nejvýše deseti modelů zastaví další dotazy při 429/403.
Chybějící další produkty a ročníky znamenají neúplné pokrytí; skutečný živý
pozitivní/negativní běh API teprve musí projít.

## Citovaný seznam jmenovaných dodavatelů (30. 9. 2026)

SEC extraktor nově zachytí i výslovnou formulaci „we rely on suppliers such as
… for these cells“ a rozdělí uvedená jména bez domyšlené právní identity či
podílu nákupů. Pozitivní kontrola používá skutečnou větu z 2025 Tesla 10-K:
Panasonic a Contemporary Amperex Technology Co. Limited (CATL) jsou jmenováni
jako dodavatelé, viz
`https://www.sec.gov/Archives/edgar/data/1318605/000162828026003952/tsla-20251231.htm`.
Obě jména zůstávají `NAMED_ONLY`; „cells“ neoznačujeme bez kontextového
prokázání automaticky jako konkrétní komoditní nákupní koš. Navazující
identitní graf a zdravotní stav protistran stále vyžadují další důkazy.

## SEC 13F – čtvrtletní vzorek institucionálních držeb (30. 9. 2026)

Týdenní runner s platným SEC User-Agent zjišťuje nejnovější oficiální ZIP
dataset, stáhne ho nejvýše jednou pro stejnou verzi doloženého registru
instrumentů a z `SUBMISSION`/`INFOTABLE` vezme pouze přímé 13F-HR řádky
běžných akcií se shodným CUSIP a názvem. První datovaná vazba je AAPL →
`037833100` pro 2. kvartál 2026 (SEC 13(f) seznam, Schedule 13G a SEC
katalog tickerů). Uloží nejvýše 25 největších řádků na ticker, s identitou
manažera, podáním, reportovaným obdobím, počtem akcií a as-filed USD hodnotou.
Pokud je řádků více, stav je `SAMPLED`; absence ve vzorku není důkazem absence
institucionálních držitelů. Amendmenty, 13F-NT, opce a PRN se neinterpretují
jako přímá akciová držba. Historická dostupnost začíná skutečným pozorováním
datasetu, nikoli reportovaným kvartálem či pouhým filing date. Nálezy nemění
skóre ani neimplikují nákup/prodej během kvartálu. Zbývají ověřené instrumenty
dalších tickerů, rekonstrukce amendmentů a živé ověření staženého ZIP.

## FDIC bankovní specialista (30. 9. 2026)

K 1. 10. 2026 přibyla druhá přesná vazba: BAC → Bank of America,
National Association → FDIC CERT 3510. FDIC detail certifikátu a SEC
Exhibit 21 Bank of America Corporation k 31. 12. 2025 jsou uloženy v
`verified_fdic_banks.json`; `known_at` je až čas našeho zjištění.
Třetí je WFC → Wells Fargo Bank, National Association → CERT 3511,
doložený FDIC a SEC Exhibit 21 Wells Fargo & Company ke stejnému datu.
Nepřiřazují se další banky podle podobného názvu. Živé API a skutečné
schéma jeho finančních polí stále čekají na ověření.

Veřejný BankFind API konektor sleduje poslední dva čtvrtletní výkazy jen pro
ručně doložený vztah ticker → bankovní dcera → FDIC CERT. První záznam je
JPM → JPMorgan Chase Bank, National Association → CERT 628; registr cituje
FDIC detail instituce a SEC Exhibit 21 s dceřinou společností. Každý výsledek
musí mít shodný CERT i celý právní název, datum v době doloženého vztahu a
vlastní čas prvního pozorování. Aktiva, vklady, vlastní kapitál a čistý zisk
se ukládají jako tisíce USD banky, nikoli konsolidovaná data JPM; skóre se
nemění. Další banky potřebují samostatné datované vztahy. Živé API a skutečné
schéma polí ještě vyžadují ověření v provozu.

## Form 4 klasifikace (30. 9. 2026)

Governance agent rozlišuje přímý nederyvátový P/A či S/D obchod od
kompenzačních A/M/F a ostatních Form 4 transakcí. U neobchodních položek
nevyvozuje finanční hodnotu nákupu/prodeje z počtu a vykázané ceny.
Ověření právní identity a časová historická akceptace dalších insider
typů zůstávají otevřené.

## FINRA short interest – přístupový konektor (30. 9. 2026)

FINRA Consolidated Short Interest má samostatnou omezenou dávku do 25
aktivních SEC tickerů. Po zadání veřejného FINRA API Client ID a Secret
v prostředí systému (`JOHNY_SKORE_FINRA_CLIENT_ID`,
`JOHNY_SKORE_FINRA_CLIENT_SECRET`) používá oficiální OAuth a filtrovaný
dotaz podle přesného `symbolCode`. Datum vypořádání ukládá odděleně od
prvního skutečného pozorování; shodu samotného symbolu označí `UNVERIFIED`,
bez doloženého historického instrumentu nemění skóre. Bez přístupu je
`WAIT_ACCESS`; 401/403 a 429 zastaví další dotazy. Je nutné ověřit živé
schéma API, vazbu symbolu na konkrétní třídu akcie a čas publikace.

## FDA recall specialista – první veřejná cesta (30. 9. 2026)

Přidán omezený pravidelný dotaz na openFDA drug/device enforcement pro aktivní
SEC názvy emitentů. Najde jen celé shodné `recalling_firm`, uloží citaci
`recall_number` a produktový popis jako `UNVERIFIED` kandidáta, nikoli
prokázanou vazbu výrobku k akcii. Neznámý čas skutečného zveřejnění se
nepřepisuje datem reportu; pro historické použití je nejdříve okamžik našeho
pozorování. Zpracuje nejvýše 25 emitentů v běhu, po 30 dnech je znovu zařadí;
chyba zdroje neoznačí kontrolu za hotovou. Dalším krokem je doložený
produktový/sponsor crosswalk, další FDA události a živý pozitivní i negativní
test. Tato změna nemění predikční skóre.

## Hlavní analýza po 686 tickerech (30. 9. 2026)

Snímek uživatele ukazuje 686/686 tickerů, 97 % a „Spouštím auditní agentní
pipeline“. Řádek RSS 1/691 je starý ukazatel z předchozího vykreslení stránky;
RSS běží dříve než tickery. Auditní agenti dosud neposílali dílčí průběh,
takže i dlouhé sekvenční načítání SEC výkazů pro celý watchlist vypadalo jako
zastavené. Nový průběh ukazuje jméno agenta a u SEC počet procházených tickerů,
odstraňuje starý ukazatel při novém spuštění a ruší popisek posledního tickeru
po vstupu do další fáze. SEC HTTP 403 a dlouhý `Retry-After` zastaví další
SEC požadavky v tomto běhu, zaznamenají nedostupná data a pustí navazující
agenty dál. Tato změna sama nezrychluje legitimní jednotlivá SEC volání ani
nedokládá dokončení konkrétního uživatelského běhu na Windows.

## Automatické pokračování SEC fronty z UI (30. 9. 2026)

Na uživatelském snímku bylo po ručním kliknutí zpracováno pouze 25 úloh,
zbylo 1 062 `READY` a 24 `DONE`. Jedna úloha znamená index firmy nebo
navazující primární dokument, nikoli vždy jednu firmu. Dosavadní tlačítko
spouštělo právě jednu synchronní dávku 25 úloh; denní plánovač samostatně
zpracovával 100 úloh na spuštění. Proto UI po 25 samo nepokračovalo.

Tlačítko nyní spouští nezávislý proces pro celý ověřený seznam 687 tickerů.
Ten přidá jen dosud neznámé SEC kontroly (již hotové indexy z ruční dávky
znovu nezařadí) a pak automaticky vybírá další
25úlohové dávky až do vyčerpání právě připravené fronty. Aktuální stav,
počet zpracovaných úloh a chyb ukládá do SQLite; druhé kliknutí aktivní běh
neduplikuje. UI ukazuje zvlášť dokončené indexové kontroly tickerů a
počty všech úloh. Při `Retry-After`, HTTP 403 nebo pouze odložených
chybách skončí stavem `PAUSED`; neobchází omezení zdroje. Odložené položky
zachovává databáze a navazující denní plánovač. Pokud se PC nebo proces
ukončí, nedokončené lease lze obnovit, po hodině lze spustit nový worker.
Lokálně ověřeno na 60 tickerech přes hranici dávky 25, izolované chybě a
zámku proti dvojímu spuštění; skutečný běh na uživatelově Windows čeká.

Aktualizováno: 29. 9. 2026. Rozsah požadavku: SC-00 až SC-35 v `AGENT_SCOUT_IMPLEMENTATION_TASKS.md` na dokumentační větvi. `IMPLEMENTED` znamená existující kód, `TESTED` místní nebo CI test, `MERGED` spojení do main, `LIVE_VERIFIED` běh proti skutečnému zdroji. Základní PR #129 a navazující PR #130–136 jsou sloučené do `main`; poslední CI run 36599400943 prošel všemi čtyřmi úlohami. Provozní akceptace zatím chybí.

## Provozní audit 29. 9. 2026

Živý `main` run [36431270885](https://github.com/littleleg198602/JOHNY-SKORE/actions/runs/36431270885) z 28. 9. zpracoval 687 vstupů, ale skončil `FAILED`: 0 použitelných cen, 0 způsobilých pořadí a 0 nových snapshotů. Uložení signálů selhalo na SQLite `NAType`, QualityGate odmítl starší průběžnou evidenci. SC-00/01 jsou nyní v `main`, ale ještě nejsou živě ověřené po sloučení.

Další cenová závada je doložena stejným artefaktem: Yahoo stáhlo 685 řad; denní řady běžně obsahovaly rozpracovanou svíčku 28. 9. před uzavřením NYSE. Validátor kvůli tomu odmítl i předchozí platnou páteční cenu. Oprava v `main` odfiltruje nedokončené seance a stále vyžaduje poslední skutečně uzavřený close. Read-only replay cache pro kanonických 687 tickerů vrátil **684 použitelných cen**; LEG má zastaralou řadu, BRKB/PSTG zůstávají bez ceny. To je kontrola cenového validátoru, nikoli nový úspěšný běh pipeline. Regresní testy pokrývají rozpracovanou, pouze budoucí a zastaralou řadu. Nový živý 687tickerový běh je stále nutný pro provozní akceptaci.

SC-03 nyní ukládá každý ověřený CSV vstup jako neměnný snapshot se SHA-256, původním pořadím a všemi řádky; neschválenou změnu CSV ukáže po pozicích a sběr nespustí. Lokální první běh a restart vytvořily právě jeden snapshot a 687 řádků. Přímé porovnání s původním `market_checker_20260818_213623.xlsx` (list `Signals`, sloupce `ticker`/`yahoo_ticker`) potvrdilo **687/687 shod ve stejném pořadí, 0 rozdílů**. SHA-256 tohoto XLS je `065444437863dbb65e65f5c97b0611545b0ceaa8c4ed778f5f9a68c874480f8b`. OKE je již ve zdrojovém XLS na pozici 177. Datovaná identita instrumentů a aliasy patří do SC-04 a zůstávají otevřené.

První CI po opravě cen odhalilo dvě časově křehké RSS testovací fixture: článek s pevným datem 1. 7. 2026 po uplynutí 90denního okna správně vypadl ze sběru. Fixture nyní používají včerejší datum; kód RSS ani 90denní pravidlo se nemění. Poslední CI PR #129 prošlo.

Navazující SC-04 změna v PR #130, sloučeném jako `b02f80b`, ukládá pozorovaný přesný SEC ticker/CIK do samostatné historie. Při změně CIK pro stejný ticker uloží kandidáta do karantény a odmítne nové nálezy; při migraci čte i starší SEC nálezy bez této tabulky. GOOG a GOOGL mohou sdílet CIK, ale mají oddělené tickerové řádky. CI run 36564820357 prošel. Úplné časově platné aliasy, dcery, značky a produkty zůstávají otevřené.

SC-05 v PR #131, sloučeném jako `50574d5`, převádí všech 39 textových profilů na stabilně pojmenované kandidátní otázky metrik a zdrojů (`research-v1:profil:druh:pozice:otisk_popisu`). Pravidlo jiného profilu vrací `NOT_APPLICABLE`; podmíněný zdroj u vlastního profilu je pouze `CANDIDATE`, dokud není doložena konkrétní dcera, produkt nebo segment. Pravidla dosud nejsou napojena na skóre ani plánovač providerů; úplné ověření více segmentů zbývá.

SC-06 v PR #132, sloučeném jako `ecb724f`, doplňuje časovou invariantu ukládaného nálezu: zveřejnění nesmí nastat po deklarované dostupnosti, dostupnost nesmí nastat po prvním pozorování. Nález s budoucím datem publikace se odmítne před zápisem a nevstoupí do historického replay. CI run 36566202962 prošel.

SC-07 v PR #133, sloučeném jako `2266772`, přidává na společné hranici uložení politiky dosud zapojených zdrojů: SEC citace musí být na přesném oficiálním HTTPS hostu a RSS zůstává `UNVERIFIED`; soukromá IP, podvržená SEC subdoména a neznámý provider jsou odmítnuté. CI run 36596878077 prošel. Oprávnění a kvóty budoucích konektorů zbývají.

SC-09 v PR #134, sloučeném jako `a0dd967`, byl ověřen průchodem skutečné SQLite fronty všech 687 tickerů bez síťových volání: 100/den, restart databáze mezi dny a jeden den výpadku PC. Po šesti provedených dávkách je zpracováno 600 různých firem, po sedmé všech 687. Již existující pořadí dle `due_at` zachovává backlog; CI run 36597493754 prošel. Profilové intervaly a fairness mezi budoucími providery zbývají.

SC-04 v PR #135, sloučeném jako `acccb63`, přijímá jen explicitní ticker aliasy s datem počátku, případným koncem a veřejnou citací. `resolve_ticker_identity_as_of` zohledňuje zvlášť čas znalosti a skutečnou platnost aliasu; překryv dvou instrumentů odmítá místo libovolného přiřazení. Metadata aliasu se účastní otisku verzované identity, takže pozdější oprava nezmění starý známý snapshot. Tento obecný mechanismus ještě není napojen do cenové a SEC scout cesty a neověřuje konkrétní P/PSTG či LEG/SGI bez primárního zdroje.

SC-14 v PR #136, sloučeném jako `5ab138f`, omezuje následné otázky: nová úroveň musí citovat jiný, nově pozorovaný `SOURCE_VERIFIED` nebo `CLAIM_VERIFIED` nález než rodič a jeden rodič má nejvýše tři přímé podotázky. Transakční zámek chrání limit proti souběžným běhům; již existující limit hloubky zůstává dvě úrovně. CI run 36599400943 prošel. Celkový rozpočet per firma/den/provider a verziované spouštěče ještě chybí.

Navazující pátrání z hloubkového výzkumu: existující `FilingExposureDiscoveryService` již v omezeném týdenním analytickém běhu extrahoval anonymní koncentraci dodavatelů/zákazníků a zmínky o materiálech. Nový SEC scout jej nyní volá také po stažení primárního 10-K/10-Q/20-F/40-F. Ukládá citovaný úsek, otisk dokumentu a otevřenou otázku; pro firmu nevyvozuje identitu anonymního dodavatele, skutečný nákupní koš, nezveřejněný hedge ani dopad na marži. Průchod je omezen šesti supply a dvanácti commodity kandidáty na podání a 500 000 znaky textu. Další kroky výzkumu: úplnější extrakce 10-K příloh, identitní graf protistran, datované vztahy, EIA/USDA/USGS řady a prokázané napojení expozic na ceny. Živé ověření stále chybí.

Další inkrement veřejné extrakce rozlišuje citovaný právní název dodavatele od identifikované entity: `NAMED_ONLY` nezakládá ověřené CIK/LEI ani health rating. U jediné komodity ukládá výslovně uvedené procento hedge a období; u více komodit v jedné větě procento nepřiřadí. Tyto kandidáty stále nepromítá do skóre a před produkčním použitím vyžadují test na skutečných SEC podáních.

Pojmenovaný dodavatel může získat doplňkový `sec_catalog_match` jen při jediné přesné shodě celého právního názvu v aktuálním SEC katalogu (share classes se stejným CIK lze sloučit). Zaznamenává se CIK, tickery, odkaz na katalog a skutečný čas pozorování. Jméno zůstává `NAMED_ONLY` pro historické tvrzení, dokud není datovaně doložena platnost a vztah případných dcer; podobné názvy se nespojují.

EIA spot ceny WTI a Gulf Coast jet fuel mají volitelný společný sběr se dvěma různými jednotkami a vlastní historickou verzí prvního pozorování. Bez `JOHNY_SKORE_EIA_API_KEY` hlásí `WAIT_ACCESS`; do skóre ani firemní ceny se nepřevádějí. Streamlit je ukazuje odděleně od SEC nálezů. Živý test zdroje čeká.

USAspending má nový konektor na prime zakázky A–D: za posledních 730 dnů hledá pouze pro datovaně doložené ticker–UEI vazby v `market_checker_app/data/verified_usaspending_uei.json`, ověřuje přesnou shodu UEI v každém výsledku a ukládá částku zakázky a výdaje odděleně s verzí pozorování. Pro firmy bez doloženého UEI nepředstírá nulové zakázky. Maximum tří stránek na UEI signalizuje `PARTIAL`; období před oknem a úplné modifikace nejsou pokryté. Další úkol je doložit vztahy dalších příjemců a dcer primárními citacemi, doplnit registr a ověřit živý pozitivní i negativní případ. Název firmy nebo ticker nejsou důkazem vazby.

První doložený záznam registru: LMT → Sikorsky Aircraft Corporation, UEI `UTJWTSLMFNG4`; citace konkrétní zakázky USAspending, SEC 2015 10-K s datem akvizice a SEC 2025 Exhibit 21 s pokračujícím zařazením dcery. `known_at` odpovídá 30. 9. 2026 12:15 UTC. Nyní je možné hledání pro toto jediné UEI bez klíče; živé ověření API stále chybí. Ostatní příjemci ani dcery nejsou automaticky potvrzeni.

PR #141 prošlo čtyřmi CI úlohami a bylo sloučeno. Navazující dohledávání příjemců automaticky zkouší přesný název aktivně pozorovaného SEC emitenta v oficiálním USAspending recipient endpointu. Ukládá až 100 jmen na běh s 30denním opakováním a max. dvě stránky; nalezená UEI jsou `UNVERIFIED` kandidáti `NAME_ONLY`, viditelní v samostatné sekci. Chybějící datovaný právní vztah je explicitní a kandidáti se automaticky nepřesunují do potvrzeného registru ani do skóre. Živé API ověření čeká.

| Body | Aktuální stav | Důkaz a zbývající práce |
|---|---|---|
| SC-00 | MERGED, TESTED | Nullable `pd.NA` rank ukládá do SQLite NULL, skutečná transakce v testu. Čeká živý běh. |
| SC-01 | MERGED, TESTED | 53minutový běh: důkaz z počátku i konce je platný; opravdu budoucí údaj je odmítnut. Ještě živý Windows běh. |
| SC-02 | PARTIAL | Opravy jsou v main, ale Windows spouštění po sloučení nebylo ověřeno. |
| SC-03 | MERGED / OFFLINE_VERIFIED | CSV drží 687 řádků, pořadí a SHA-256; ScoutStore archivuje neměnné verze a ukazuje změnový diff. Zdrojové XLS se shoduje 687/687. Provozní ověření na Windows ještě neproběhlo. |
| SC-04 | MERGED / PARTIAL | Oddělené identity emitenta/instrumentu, SEC změna CIK v karanténě a datovaný alias s fail-closed as-of lookupem. PR #135 / CI 36598477993. Napojení aliasu do sběru, doložené mapování sporných tickerů, dcery, značky a produkty zbývají. |
| SC-14 | PARTIAL | Dvě úrovně, jiný nově pozorovaný ověřený důkaz před další úrovní a max. tři děti na případ. Čeká širší denní/provider rozpočet a ověřené spouštěče. |
| SC-05 | MERGED / PARTIAL | 39 profilů má verzované metrické a zdrojové otázky; cizí profil je `NOT_APPLICABLE`, vlastní je pouze `CANDIDATE`. Doložení segmentů a zapojení do plánování čeká. PR #131 / CI 36565597107. |
| SC-06 | PARTIAL | Nález ukládá původ, locator, časy a stav ověření; navazující ochrana odmítá publikaci po dostupnosti. Chybí sjednocení kontraktu dalších specialistů. |
| SC-07 | PARTIAL | Úložiště povoluje pouze SEC a RSS nálezy podle zdrojové politiky; SEC host je přesný, RSS kandidát nesmí získat stav ověření pouhým ingestem. Kvóty, retence a licence dalších zdrojů zbývají. |
| SC-04–07 | PARTIAL | 39 profilů výzkumu je strojově čitelných, verzovaných a ověřených proti produkčnímu seznamu; `UNKNOWN` není chyba ani záporný bod. Výzkum uvádí `P`, ale skutečné CSV místo něj obsahuje `OKE`; P zůstává pouze výzkumný neprodukční řádek. `OKE` má oddělenou doloženou profilovou opravu `OIL_GAS` z oficiálního ONEOK a SEC 10-K, bez přepsání CSV. Profilové metriky zatím neřídí skóre/konektory. SEC konektor odmítá cizí hosty/cesty i přesměrování, RSS ukládá jen kandidátní veřejné HTTPS odkazy. Úplné identity a pravidla budoucích poskytovatelů čekají. |
| SC-08 | MERGED, TESTED | SQLite fronta, dedupe, lease token, zámek transakce, historie pokusů, restart; test končícího lease. Schéma nyní eviduje verze 1–5. |
| SC-09 | PARTIAL / OFFLINE_VERIFIED | Denní Windows plánovač a týdenní dávka, max. 100 firem/den. Sedm restartovaných dávek s výpadkem dne obsáhne 687/687 bez vyhladovění. Chybí profilové intervaly a fairness pro další providery; Windows neověřen. |
| SC-10 | PARTIAL | SEC client používá limit a retry včetně `Retry-After`; dlouhý požadavek i HTTP 403 zastaví dávku a uloží cooldown poskytovatele do SQLite. Fronta odděluje zdroje a obnovuje vlastnictví SEC během dávky. Chybí obecný circuit breaker pro ostatní zdroje. |
| SC-11 | IMPLEMENTED, TESTED lokálně pro scout | `findings_as_of` a `ScoutIndexAgent` odmítají data nedostupná před cutoffem; pro každou orchestraci s nálezem ukládá neměnný cutoff a přesný seznam použitých finding IDs. Ostatní vrstvy analýzy mají vlastní existující snapshoty. |
| SC-12–16 | PARTIAL | SEC index i omezený primární dokument mají samostatný otisk, URL/CIK/accession kontrolu, skutečnou dobu pozorování a otevřenou otázku; 8-K parser vytváří citované sekce Item do hloubky 1. RSS zprávy už vstupují jako časově omezené `UNVERIFIED` stopy s pouhým ticker hintem a otázkou na primární zdroj, nikdy do skóre; rozpočet jsou dva bezpečné unikátní odkazy na ticker a běh. Stavový cyklus leadů má časovou historii; závěr `VERIFIED`/`CONTRADICTED` vyžaduje zvláštní claim-level ověřený důkaz. Další primární zdroje a automatické ověření tvrzení chybí. |
| SC-17 | TODO | Bez schváleného poskytovatele a rozpočtu; deterministická cesta běží bez AI. |
| SC-18 | PARTIAL | Nová lehká cesta na SEC index bez XBRL downloadu a omezený primární dokument nejnovějšího podání z každé rodiny formulářů; existující fundamentální agent čte filings/XBRL. Potřebuje úplné historické pokrytí a skutečné živé ověření. |
| SC-19–30 | PARTIAL | Existují analytické agentní moduly a SEC/RSS vstupy. FRED makro a EIA spot ceny mají volitelné sdílené konektory s evidencí revizí a konzervativním časem prvního pozorování; potřebují klíče a živé ověření. Nejde o cenu konkrétní firmy a nemění skóre. FINRA, USAspending, FDA a FDIC čekají na bezpečnou vazbu primárního identifikátoru na emitenta/instrument. Form 4 zatím nerozlišuje obchod od vestingu. |
| SC-31 | PARTIAL, TESTED lokálně | Index i primární dokument předá `ScoutIndexAgent` do `SourceResolutionAgent` a `QualityGateAgent` bez změny skóre; test celé orchestrace. Neměnný scout as-of bundle se seznamem použitých ID je uložen a lze jej znovu načíst; další specialisté čekají. |
| SC-32–33 | PARTIAL | Streamlit ukazuje poslední SEC index/dokument, oddělené neověřené RSS stopy, otevřené i uzavřené otázky s citovanými finding IDs, chyby zdroje a další pokus, stav fronty i tlačítko dávky; Excel má při použitých nálezech list `ScoutEvidence` s as-of cutoffem a citacemi. Windows .bat a volitelná denní úloha; instalátor jednou vyžádá kontaktní e-mail SEC. Chybí skutečné ověřené odpovědi a provozní Windows zkouška. |
| SC-34 | TODO | Žádné nové feature zatím nevstupují do predikčního skóre; vyhodnocení přínosu začne až s podklady a dokončenými výsledky. |
| SC-35 | PARTIAL | Místní testy, kompilace a zkouška plánování všech 687 bez přístupu k SEC. Chybí live known-positive SEC, Windows a end-to-end kontrola 48/687 tickerů. |

Nepoužívat sloučení základního PR jako potvrzení dokončení všech 36 úkolů. Bez reálného SEC User-Agent se automatický sběr vrátí `WAIT_ACCESS`, nepředstírá nalezené podání. Zdroje s klíčem/licencí se nezapojují bez odpovídajícího přístupu. Žádné order API není přidáno.

GitHub CI základní verze a navazujících PR #130–136 prošlo. Živá akceptace Windows/SEC/cen po sloučení stále chybí. Výzkumné `P` nesmí automaticky nahradit produkční `OKE` ani se bez ověřeného časového aliasu sloučit s `PSTG`.

## 1. 10. 2026 — CMS, ClinicalTrials a OFAC veřejný sběr

CMS hospital-all-owners a ClinicalTrials.gov nyní mají samostatné omezené
sběrače přesných organizačních/sponsor názvů. CMS vybírá jen 19 relevantních
zdravotních profilů, ClinicalTrials 49 PHARMA/MEDTECH; dvě stránky po 100
a nejvýše deset emitentů na běh. Časový rozpočet 60 sekund a tři chyby
zastaví další dotazy; nedokončené identity zůstávají splatné. Kandidáti
nedokládají vztah produktu, studie nebo nemocnice k emitentovi a zůstávají
UNVERIFIED. CMS neukládá osobní vlastníky ani jejich adresy.

OFAC stahuje oficiální SDN export jednou, ověřuje jeho strukturu a
unikátní ID a porovnává jen přesné primární organizační názvy. Živý CSV
obsahuje koncový DOS EOF, který parser nyní korektně přijímá pouze na
konci. Otisk snapshotu je uložený; cizí redirect není povolen. Absence
názvu neznamená sankční clearance: aliasy, Non-SDN a 50% vlastnická
analýza dosud nejsou zapojené. Nové zdroje jsou v denním runneru, UI,
politikách uložení i odděleném přehledu aktuálního pokrytí.

Uložené živé důkazy mají 19 PASS případů celkem: 13 FDA/FDIC/NHTSA,
čtyři CMS/ClinicalTrials a dva OFAC. Celá sada prošla: **456 testů**,
Linux/Python 3.12.14; příkaz, čas a otisky testovaného Python kódu jsou
v evidence/specialist_tests_20261001.json. Nejde o koncový Windows běh
ani prokázané identity a pokrytí všech 687 firem. Inventář zůstává
pravdivě PARTIAL/PILOT/NOT_STARTED; 0/21 DONE.

SPECIALIST_IMPLEMENTATION_CHECKPOINT_20261001.md uchovává další práci
pro všech 21 specialistů a konkrétní nevyřešené přístupy, identity a
akceptaci. Je nastavená kontrola budoucího úplného dokončení s oznámením
až po skutečně existujících důkazech; kontrola sama nepíše další kód.

## 1. 10. 2026 — USAspending živě a přenosná provozní diagnostika

Oficiální USAspending API prošlo čtyřmi novými případy. Dokumentovaný
Sikorsky UEI UTJWTSLMFNG4 uložil přes stávající collector 100 aktuálních
zakázek pro LMT; opakování stejného zachyceného payloadu nepřidalo duplicity.
První stránka měla hasNext, takže výsledek zůstává PARTIAL. Negativní
UEI, pozitivní recipient schema a negativní název prošly také. Úplný
payload, výsledek a scope jsou v evidence/usaspending_live_20261001.json.
Celkem je uložených 23 PASS případů sedmi veřejných zdrojů, nikoli úplná
akceptace 21 specialistů.

UI má tlačítko Stáhnout provozní přehled specialistů. Přehled skutečně
čte archiv vstupu, pořadí 687 řádků, SEC identity a dokončené indexy jen
v kanonickém scope, poslední běhy zdrojů a aktuální sektorové pokrytí.
NEVER_RUN neznamená nulové nálezy; částečná stránka zůstává částečná.
Exportuje systém/Python a jen boolean přítomnosti přístupových nastavení,
nikoli hodnoty klíčů, cesty nebo adresy vlastníků. Přehled neprovádí
externí volání, nevytváří záznam běhu a nepřepisuje DONE/VERIFIED.
I na Windows je to diagnostika, nikoli automatický důkaz koncového běhu.
Přítomný klíč není úspěšná autentizace. Uložení souhrnu příjemců už zachovává
checked/new/truncated/failed počty, které předtím propadávaly allowlistem.

Navazující testy ověřují prázdnou databázi na Windows bez falešného
potvrzení, poškozený archiv, cizí ticker, sektorový jmenovatel, zachování
PARTIAL/WAIT_ACCESS a neexportování přístupových hodnot. Předchozí celá
sada 456 testů a GitHub CI prošly; navazující kontrola má samostatný
čas a otisky kódu v evidence/specialist_acceptance_tests_20261001.json.
Inventář zůstává 0/21 DONE. Další dostupný vývoj i závislosti jsou otevřeně
uvedené v checkpointu; kontrola budoucího dokončení nenahrazuje vývoj.

Po uložení tohoto kroku byla původní kontrola dokončení rozšířena podle
uživatelského požadavku na automatické pokračování implementace. Hodinový
běh obnoví stav z GitHubu, provede další dostupný konkrétní krok, ověří ho
a uloží na pracovní větev/PR. Nepřepisuje souběžnou práci a nevymýšlí chybějící
důkazy; při skutečné blokaci pracuje na jiném dostupném úkolu a bez práce
nevytváří prázdný commit. Oznámení pošle až po skutečné akceptaci všech 21.
Nepovoluje automatický merge, nákup dat, zakládání účtů nebo obchody.
