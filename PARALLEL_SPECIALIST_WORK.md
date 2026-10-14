# Souběžné dokončování specialistů

Platí od 11. 10. 2026. Vývoj běží v nejvýše šesti oddělených větvích;
koordinátor přebírá změny, řeší integraci a provádí společné kontroly.
Počet vývojových větví není počet dokončených analytických specialistů.

## 14. 10. 2026 — oprava životnosti testovacích SQLite spojení

Původní head páté dávky `e550b334` a run `38551067090` prošly Linux
1371/1371, UI a 687/60s kontrolou. Windows dokončily všech 1371 testů,
ale měly **12 errors / 0 failures/skips/expected/unexpected**; release gate
neprošel. Všech dvanáct zaznamenaných tracebacků je `WinError 32` při
úklidu dočasné DB: deset v IR fixture, dvě v price-provenance fixture.
Produkční stores již používají existující ClosingSQLiteConnection.

Původní ZIP artifact `11888031678` (43963 bytes) byl stažený a skutečně
obsahově přečtený; SHA256 `92e7ceb205ca6d98b4730ed5310b616ee85ef9001d82c2aaf32d9a6d470a281f`.
Původní CRLF JSON/log, decoded job log a Linux log jsou zachované v
`evidence/batch5_windows_failure_20261014/`; žádný neúspěch se nepřepisuje.

Agenti sec a evaluation opravili pět nativních spojení testovacích fixture:
commit/rollback proběhne před vždy provedeným close. Všechna assertions
původních 28 IR a 17 price testů jsou obsahově zachovaná; přibylo šest
regresí se silnými referencemi na spojení a kontrolou zavřeného native API.
Tyto regrese odhalí stejnou mezeru i na Linuxu. Cíleně prošlo 75 IR/budget
a 54 price/cache testů. Žádné potlačení cleanup errors, gc/retries,
změna dat/hashů/budgetu, source HTTP, kvóty, produkčního kódu nebo skóre.
Původní historické capture/reproduce helpery a všech 148 batch5 evidence
souborů z publikovaného e550 tree zůstávají byte-identical.

Nový místní výsledkový JSON zaznamenal 1377 discovered=executed,
successful=true a všechny failure/error/skip/expected/unexpected počty 0.
Oba původní lokální logy ale končí před terminal summary; chybějící
footer se nedopisuje a plain OK se z nich nevyvozuje. JSON a tyto původní
částečné captures jsou `evidence/batch5_linux_sqlite_fix*`. Úplný hostovaný
log a nový Windows ZIP/JSON jsou stále samostatnou podmínkou konkrétního
nového HEAD. Důkaz opravy: `evidence/batch5_windows_sqlite_fix_20261014.json`.

Žádný merge. Úplná akceptace zůstává 0/21; aktuální konkrétní source/identity
fronta po páté dávce níže pokračuje bez změny.

## Pátá navazující dávka — 14. 10. 2026

Základ PR #162 `5c5ea0ba`, main `a7aa1fec`; žádný merge nebo force push.
Šest skutečných workerů ve vlastních izolovaných větvích
`batch5/<lane>-20261014`; koordinátor znovu použil integrační worktree.
První výstupy i následné úkoly jsou převzaté jako lokální commity.
Limit používání byl přechodný; nedošlo k resetu práce nebo opakování LIVE.

| Vlastník | Konkrétní výstup | Lokální commit |
|---|---|---|
| identity_bulk | Nezávislý XOM corporate-action/class/CUSIP bridge, šest HTTP200 raw; žádná adopce. | `effead5` |
| healthcare | Prospektivní OBSERVED_SNAPSHOT sponsor contract, přesné capture/arm/issuer gates; skutečné dva případy withheld, 0 HTTP. | `93e6106` |
| regulatory | Osm skutečných model/year captures, striktní negative review a stejná kvóta pro běžný runtime NHTSA sběrač. | `366b4cd` |
| contracts | Read-only original-byte revision/absence audit stejného awardu/UEI, 14 unchanged, replay SAME_SOURCE_CAPTURE, 0 HTTP. | `4462bdd` |
| sec | LIVE primary IR s centrální rezervací každého hopu, whole-body withholding, CLI retry a 2 safe / 1 withheld body. | `a86336f` |
| evaluation | Skutečný provider/action-cache provenance adapter, 2 HTTP200 chart captures, null Oct13 Close a UNKNOWN revision zachované. | `ed3e56c` |
| identity_bulk — další úkol | Same-CIK PSTG→P symbol bridge; přesný day/class/CUSIP stále missing, 0 HTTP, žádný alias. | `a342492` |
| regulatory — další úkol | Read-only bounded scope-review API bez DDL/constructor/HTTP, přesných 8 uložených případů a kvóta beze změny. | `329ab50` |
| contracts — další úkol | Přísný candidate walk-forward label/price gate; fold knowledge před predikcí, legacy diagnostic oddělená. | viz integrační důkaz |
| regulatory — redirect guard | Skutečný urllib offline 302/307, žádná druhá neúčtovaná operace; 52 cílených testů, 0 HTTP. | `7732268` |
| sec — bezpečný historický replay | Celé starší IR authority body vyřazené; neměnná metadata/reporty, současný PARTIAL před parserem; 73 testů, 0 HTTP. | `ff42983` |
| koordinátor | Sdílené NHTSA/RSS limity, explicitní audit/IR/UI/source archivy, klinické clocks a strict shadow/layer běh. | viz integrační důkaz |

Pět původních production registrů v cílech uživatele zůstává beze změny:
canonical687; 150 historicalQ2/currentQ3=0; healthcare10/NHTSA3/legal5;
verified adoption0, full acceptance0/21. Source quota je prospektivní od
skutečného UTC14October; zmizelá starší temporary historie se nepředstírá.
NHTSA8requests/3makes a RSS3requests/1ticker zůstávají v původní společné DB.
Z 19 nových source HTTP odpovědí je 18 bezpečných original raw bodies;
další veřejný batch5 body je přesná kopie staršího transaction capture,
nikoli nový požadavek. Čtyři SEC discovery queries jsou samostatný research.
Celé nové IR authority body i dříve odmítnuté NHTSA dokumentační body jsou
withheld; žádný sanitizovaný či zakódovaný náhradní export.

Současný export navíc vyřazuje celé starší IR authority HTML po potvrzené detekci přístupových hodnot. Původní otisk, metadata a historické handoff/review záznamy zůstávají; jejich dvě source-only positives nejsou současná akceptace. Metadata-only unavailable capture vrací PARTIAL před čtením těla. Žádné náhradní HTML nebo přepsání původní historie; předchozí veřejná ancestry se neforce-přepisuje.

Běžný NHTSA klient nově odmítá redirect před druhou transportní operací;
každý pokus zůstává účtovaný, uložené řádky i vyčerpaná denní kvóta zůstávají.
52 cílených offline/contract testů ověřilo tento guard, 73 IR/budget testů
ověřilo metadata-only unavailable kontrakt. Tyto mechanické testy nejsou LIVE
negative, issuer ani Windows/OOS akceptace. Důkazy:
`evidence/batch5_nhtsa_redirect_guard_20261014/handoff.json` a
`evidence/batch5_ir_prior_withholding_20261014/handoff.json`.
Úplný výsledek 1358/1358 před těmito dvěma opravami patří pouze commitu
`4c26be8`; původní JSON/log jsou zachované v `batch5_pre_redirect_linux*`
a kvalifikace v `evidence/batch5_coordinator_20261014/pre_redirect_qualification.json`.
Finální společné kontroly mají vlastní JSON, celý console capture a otisky
v integračním důkazu; staré výsledky se nepřebírají jako akceptace nového kódu.

Finální společná sada 1371/1371 prošla bez failures/errors/skips/expected/unexpected
v 151,097 s. JSON a úplný console log jsou v `evidence/batch5_linux_20261014.json`
a `evidence/batch5_linux_console_20261014.log`; runner-side log je zachovaný
zvlášť. Přesný otestovaný code snapshot `baac707` a otisky 42 source/test
souborů jsou v integračním důkazu. Hostované kontroly nového HEAD zůstávají
samostatné; nevzniká žádná user Windows, source/history ani OOS akceptace.


Důkazy: `evidence/batch5_integration_20261014.json`, jednotlivé
`evidence/batch5_*_20261014/`, `evidence/batch5_coordinator_20261014/content_audit.json`.
CI tohoto nového HEAD a end-user Windows/OOS jsou samostatné podmínky;
zelený původní 1183-run není akceptace nového snapshotu.

### Aktuální konkrétní fronta po páté dávce

1. Datované nezávislé XOM new-CUSIP/class a konflikt July7; LEG corporate-action
   history; přesný PSTG→P action day a post-rename instrument proof. Neprodlužovat Q2.
2. Nový skutečný klinický capture s předchozí přesnou issuer vazbou ve stejné
   původní kohortě a doloženou trvalou kvótou; širší CMS/product/ownership scope.
   Staré '& Co' a pozdější SEC capture nejsou historický sponsor bridge.
3. Genuine kompletní HTTP200 NHTSA negative; po vyčerpání kvóty neopakovat
   dotazy téhož UTC dne. Další dated FRS/DOJ identity a relevantní jmenovatele.
4. Obnovit doložené prime-award targety z původních úplných archivů s dnešním
   ingestion clock, pokud obsah opravdu existuje; zachovat originální kvótu,
   širší UEI/awardy a další actual captures/revision timing. Nemyslet absenci jako deletion.
5. Bezpečný úplný primární IR authority doklad bez náhradního těla; samostatné
   person/instrument/legal a issuer identity. SOURCE_VERIFIED není issuer VERIFIED.
6. Prospektivní ceny/actions a source/label historie; skutečná dated publisher
   revision provenance a exaktní session/knowledge gates. UNKNOWN zůstává excluded,
   žádné zpětně vyrobené forecasty nebo posuny chybějících horizontů.
7. Další datované operational exposures a kvantifikace z nezávislých primárních
   zdrojů; ostatní specialisté, povolené přístupy a uživatelský Windows end-to-end.

Původní 60-second test budget i production score jsou zachované. Šest workerů
neznamená 21 hotových analytických specialistů; žádné oznámení dokončení.

## Čtvrtá navazující dávka — 13. 10. 2026

Základ je otevřený PR #162, GitHub HEAD `22bcb749`; main zůstává
`a7aa1fec`. Původních 996/996 Linux a hostovaných Windows testů patří
pouze tomuto základu. Šest skutečných vývojových agentů navázalo v původních
izolovaných worktrees; přerušená práce se po dočasném limitu používání
převzala bez resetu. Koordinátor převzal commity a zapojil výstupy do běhu/UI.

| Vlastník | Konkrétní odevzdaný výstup | Lokální commit |
|---|---|---|
| identity_bulk | Přechod stejného CUSIP mezi tickery vyžaduje primární review; přesný SEC SGML a aktuální Schedule13 XML; 11 raw captures, XOM/LEG/PSTG review. | `50346cf` |
| healthcare | Přesný pin sponsor/intervention snapshotu, publisher clocks a rozsah NCT; konkurenční vazby obnovují kontroly; 10 skutečných captures. | `edaba8c`, `d181185` |
| regulatory | Striktní NHTSA HTTP/JSON/success envelope a obnova po změně scope; 8 captures; 7 veřejných raw / dokumentační HTML withheld; 2 HTTP400 prázdné případy zůstávají PARTIAL. | `df2f9c6` |
| contracts | Neměnný audit každého skutečného zachycení transakce; clocks revize/capture/ingestion oddělené; 14-row upgrade/replay bez HTTP a změny starých nálezů. | `5e3c73a` |
| sec | Sibling Form4 identity review s přesnou osobou/třídou/derivative/footnotes; 5 skutečných captures, žádná adopce person/instrument. | `0eadd73` |
| evaluation | PIT label audit vyžaduje přesné cenové důkazy; label resolver je uchová před finalizací a používá skutečný čas získání. | `5382459`, `eb59a90` |
| regulatory — další úkol | Historické provozní output expozice XOM: 3 SEC captures, 2 explicitní výstupy, makrotext nepřidává firemní expozici. | `11797ed` |
| identity_bulk — jediný vlastník importů | Obsahové a časové review obou klinických návrhů: WITHHOLD 2 / adopt 0; produkčních 10 řádků beze změny. | `90f0d21` |
| sec — další úkol | Přesná IR RSS/page/portal provenance: 4 skutečné HTTP200 captures / 5 requestů; dva source-only pozitivní případy, homepage PARTIAL; skutečný čas importu, issuer atribuce vypnutá. | `7d1f990` |
| regulatory — bezpečný publikační scope | Dokumentační HTML s přístupovou hodnotou vyřazené; původní otisk/metadata, 7 původních API raw a striktní WITHHELD guard; bez bypassu. | `7845f57` |
| koordinátor | Throttle před snapshot deepcopy, bezpečné vlastní kopie scalar rows, monotónnost; Form4/transaction/label/IR audity v běhu, UI a CLI. | `c601705`, `78650f8`, `6aea39d`, `f16c5ec` |

Canonical 687 a produkční instrument/healthcare/NHTSA/legal registry jsou
zachované. Současné Q3 pokrytí historických 150 Q2 instrumentů stále 0;
žádná nová identita ani automatický PSTG→P alias není schválený. XOM má
konfliktní class/CIK/CUSIP přechod a neúplný HTML doklad, LEG deleted CUSIP.
NCT sponsor pins dokazují dnešní zobrazený objekt; samy nejsou neměnnou
historickou verzí ani sponsor-to-CIK vazbou k požadovaným event days.
Semaglutide/Placebo nepřebírají atribuci Lilly.

Původní performance diagnostika před scalar optimalizací zachovává 1 timing
failure (75,498s při neměnném 60s limitu); 5585 emit pokusů vytvořilo 393
snapshotů. Finální publikační sada ověřila 1181/1181 testů bez errors/failures/skips,
včetně celého 687 cached testu s původním 60s limitem. Tento lokální pre-CI
výsledek patří původnímu snapshotu čtvrté dávky. Profil starého
kódu ani běh se souběžnými agenty se nevydává za férové srovnání výkonu/OOS.
Původní publisher raw bytes a whitespace upozornění zůstávají zachované.
Z 41 zachycených odpovědí je 40 veřejných raw (38 HTTP200 / 2 HTTP400),
jedno dokumentační HTML s přístupovou hodnotou zůstává withheld s otiskem.
Obsahový audit ověřil 153 současných odkazů s otisky; staré diagnostiky jsou označené vlastní
verzí, nejsou akceptací finálního kódu. Důkaz:
`evidence/batch4_content_audit_20261013.json`.

### Následná oprava Windows repository reference

Čtvrtá dávka byla publikovaná jako HEAD `71d5ef65`, strom `8f52eeda`.
Hostovaný run `38454009731` ověřil Linux 1181/1181, UI a 687 scale/budget.
Windows vykonaly všech 1181 případů, ale jeden selhal; release gate neprošel.
Producent SEC review zapsal `raw_cover_revision.prior_path` pomocí
platformního `str(Path)` se zpětnými lomítky. Nová oprava používá kanonický
Git odkaz přes `relative_to(root).as_posix()` a odmítá cizí kořeny či traversal.
Původní přesné porovnání artefaktu, data a hash assertions zůstávají zachované.
Dvě regrese ověřují Windows/POSIX serializaci a odmítnutí cizích cest.
Nová místní úplná sada prošla 1183/1183 bez errors/failures/skips; 60s limit
se nemění. Ověření nového publikovaného HEAD na hostovaných Windows
je samostatná podmínka, původní neúspěch není akceptace opravy.

Původní Windows ZIP, JSON a log zůstávají v
`evidence/batch4_windows_failure_20261013/` včetně původních CRLF bajtů.
Přesné otisky, příčina, nový kód, testy a čekající hostované ověření jsou v
`evidence/batch4_windows_path_fix_20261013.json`. Původní SEC handoff a raw
archivy jsou neměnné; staré source hashes popisují jejich původní verzi.
Fronta níže ani úplná akceptace 0/21 se touto platformní opravou nemění.

### Aktuální konkrétní fronta po čtvrté dávce

1. identity_bulk: přesný datovaný XOM transition bridge a důkazy class/CUSIP;
   LEG merger/delisting instrument history; PSTG→P nezávislý symbol/class/CIK
   bridge. Další Q3 review nemá prodlužovat Q2 intervaly.
2. healthcare: oddělený OBSERVED_SNAPSHOT sponsor contract s prospektivní
   issuer evidencí; historický EVENT_DATE vyžaduje vlastní dated bridge.
   Širší CMS/product/ownership scope a relevantní jmenovatele.
3. regulatory: skutečný HTTP200 kompletní negativní NHTSA případ; další
   dated FRS/DOJ vazby. Prázdný HTTP400 se neopravuje na bezudálostní kontrolu.
4. contracts: širší doložené UEI/awardy a další skutečné captures s původní
   trvalou kvótou; source revision/deletion timing, nikoli jen traversal.
5. sec/IR: širší nezávislé person/instrument/legal/primary IR důkazy a
   explicitní issuer atribuce. Source authority a RSS lead jsou oddělené.
6. evaluation/prices: skutečný provider/action-cache provenance adapter pro
   ukládané cenové důkazy; UNKNOWN capture stále nemůže do PIT srovnání.
   Stejné provenance gates rozšířit do ostatních shadow/candidate evaluátorů;
   skutečná prospektivní historie a OOS zůstávají chybějící.
7. energy/ostatní: další datované provozní firemní expozice, kvantifikace
   se samostatným primárním zdrojem, širší relevantní pokrytí; přístupy/licence
   a skutečný uživatelský Windows koncový běh vést jako vlastní otevřené důkazy.

Úplná akceptace zůstává **0/21**. Nové testy a zachycené piloty nejsou DONE.
Společné ověření/přenos je v `evidence/batch4_integration_20261013.json`;
průběžná pre-CI diagnostika nenahrazuje ověřený artefakt konkrétního HEAD.

## Opravy review a Windows checkoutu — 13. 10. 2026

První PR #162 HEAD `290e093` ověřil 990 Linux testů, UI i 687 kontrakty.
Skutečný Windows běh 38272126978 však selhal: 990 discovered, 981 executed,
4 failures a 1 error. Checkout změnil LF na CRLF v byte-hash důkazech;
čtyři původní rozdílné otisky jsou přesně reprodukované touto konverzí.
Následná oprava zachovává původní bajty archivů, pinned dat a fixtures
při checkoutu na obou platformách. Test provádí skutečný Git clone
s core.autocrlf=true a kontroluje původní i smíšené/binary source bytes.
Přísné hash assertions ani data se neuvolňují či nepřepisují.

Review transakčního sběrače se současně řeší: veřejný status vyčerpané
sdílené kvóty je WAIT_QUOTA, ztráty lease LEASE_LOST; již uložené stránky
zůstávají zachované a traversal není kompletní. CLI vrací retry exit 2.
Čtyři testy skutečných SQLite reservation/lease přechodů a CLI případ
se obejdou bez HTTP. Cílená integrační kontrola 62 testů prošla.
První neúspěšný Windows běh se nevydává za akceptaci nové opravy.
Důkaz: `evidence/windows_checkout_fix_20261013.json`.

## Třetí navazující dávka — 12. 10. 2026

Základem je skutečně sloučený PR #161, main
`a7aa1fec2d1241ca5a8c2c87ae62e42b9dd510e5`. Šest izolovaných vývojových
větví odevzdalo konkrétní zdrojové captures, kód, replay a testy. Koordinátor
zapojil archivaci každého dokončeného zdroje a explicitní načtení transakcí
vybrané doložené zakázky do UI/CLI; současně opravil obě potvrzené P2 review.

| Vlastník | Odevzdaný commit | Konkrétní výstup |
|---|---|---|
| identity_bulk | `9cbc75c` | Úplný raw SEC katalog (10 445 položek) a Q3 TXT (25 789 řádků); skutečná příprava 687/687. |
| healthcare | `c4e74f1` | 18 HTTP captures a 5 CRL event-only vazeb PFE/MRK/ABBV, čtyři aplikace; skutečné scope/partial/NOT_FOUND replay. |
| regulatory | `ffc976a` | 24 NHTSA/SEC captures, přesné modely F/GM 2026, 5+3 campaigns; HTTP400 prázdné odpovědi zůstávají PARTIAL. |
| contracts | `55fc322` | Bounded LIVE sběrač a neměnný raw archiv 14 USAspending transakcí jednoho doloženého Sikorsky/LMT awardu. |
| sec | `2da667f` | 15 primárních SEC/GLEIF captures; čtyři filing-day DIRECT a jedna snapshot-day PRIVATE identita, oprava Trading Symbol(s). |
| evaluation | `0aadf86` | Neměnné archivy jednotlivých zdrojových běhů, bounded replay a pravdivý dnešní replay starších FDIC payloadů. |
| identity_bulk — import | `d50e18a` | Jediný vlastník posoudil 35 raw referencí a přijal 5 healthcare +2 modelové +5 právních vazeb. |

Následný fix `e57766a` oddělil pevný původní pětizáznamový testovací
snapshot od rostoucího produkčního registru. Původní boundary assertions
zůstávají zachované a nový samostatný test validuje všechny skutečné
produkční řádky. První rozšířená kombinovaná kontrola měla 5 selhání a 1
chybu kvůli tomuto coupling; úplný původní log zůstává v import evidence.

Produkční registry nyní obsahují 10 healthcare event/object záznamů,
3 přesné NHTSA modely a 5 časově úzkých právních identit. Dceřiná vazba
Sikorsky→LMT v nové SEC identity větvi zůstává withheld: důkazy nemají
společný doložený den. Samostatná již existující USAspending UEI kontinuita
zůstává zachována. Čtyři právní DIRECT záznamy nejsou dnešním průběžným
pokrytím emitentů. Jednodenní gate je scope důkazu, nikoli domyšlené
historické právní datum.

687 instrumentových návrhů není 687 ověřených identit. Stav fronty je
663 NEEDS_REVIEW, 22 WAIT_INSTRUMENT_EVIDENCE, 2 WAIT_TICKER_CIK.
Původních 150 instrumentových záznamů se vztahuje na Q2 a pokrývá **0
pozic Q3**. XOM má aktuální odlišný CIK a starý CUSIP je v Q3 označen jako
deleted; přechod není schválen. Výstup a audit jsou
`evidence/batch3_identity_20261012.json` a
`evidence/batch3_identity_bulk_20261012/registry_review.json`.

### Aktuální fronta po třetí dávce

1. identity_bulk: nezávislé primární instrumentové a corporate-action
   důkazy pro current Q3 návrhy; XOM/LEG/PSTG nejdříve prověřit, ne přepsat
   staré intervaly. Samotný přesný název a současný katalog nestačí.
2. healthcare: širší produktové/aplikační/sponzorské/CMS vazby v čase a
   relevantní jmenovatele; publisher NOT_FOUND nezaměňovat s celou skupinou.
3. regulatory: úplný negativní NHTSA případ s úspěšnou odpovědí, další
   dated model/FRS/DOJ vazby; HTTP400 není kontrola bez událostí.
4. contracts: další doložené UEI/awardy a source revision timing; 14 řádků
   jednoho awardu není kompletní historie revizí ani výnosy firmy.
5. sec: aktuální dated právní/subsidiary/brand identity a primární IR,
   instrumentové/osobní vazby Form4; konfliktní publication clocks zachovat.
6. evaluation: průběžně shromažďovat skutečně dostupné source-run archivy
   a point-in-time label data. Dnešní replay nelze backdatovat na 1. říjen;
   chybějící dříve přepsané kontroly nebyly získány zpět.
7. koordinátor: integrace dalších konkrétních specialistů a datovaných
   firemních expozic, relevantní pokrytí a skutečný uživatelský Windows běh.

Code/live stavy všech 21 specialistů zůstávají beze změny: **0/21 plně
akceptováno**. Hostovaná deterministická Windows sada se kontroluje pro
každý nový HEAD zvlášť; předchozí 902/902 na PR #161 je důkaz předchozího
kódu. Aktuální výsledek a přesný hash budou doložené CI nového PR.
Integrační důkaz této dávky: `evidence/batch3_integration_20261012.json`.

## Druhá převzatá dávka — 11. 10. 2026

Aktuální GitHub HEAD PR #160 byl znovu načten: `e0ce88621ddfe79cc32b90fb488c052bfb20c7ae`.
Předchozí lokálně otestovaná dávka `209eeed` byla zachována; nebyla znovu
implementována. Šest skutečných vývojových agentů dostalo šest izolovaných
worktrees a nekolidující soubory. Do integrační větve zapisuje koordinátor.
Žádný agent této dávky neměnil produkční registry. Datové registry mají
jediného vlastníka `identity_bulk`; konfiguraci a společné vstupy vlastní
koordinátor.

| Vývojový agent | Jeden konkrétní výstup | Lokální commit agenta | Převzatý commit |
|---|---|---|---|
| `identity_bulk` | Zdrojově doložené datované přechody CIK/CUSIP/ticker/třídy a ochrana sousedních verzí identit při reviewovaném exportu. | `5eda707` | `2cc8f7e` |
| `healthcare` | Přehled konkrétních objektových identit a skutečných dotazů FDA/CMS/ClinicalTrials pro kanonických 687 tickerů. | `2afe381` | `130b936` |
| `regulatory` | Přesné datované vlastnictví EPA FRS zařízení; změny znalosti a scope obnovují kontrolu bez přenosu právní odpovědnosti. | `2b0eef9` | `1600c44` |
| `contracts` | Audit změn kumulativních závazků a výdajů stejného awardu a UEI bez odvození tržeb či jednotlivé transakce. | `424b70f` | `cc9ba6d` |
| `sec` | Doložené datované právní osoby a dceřiné společnosti u pojmenovaných protistran v SEC; nezávislý důkaz mimo expoziční dokument. | `7a80d8d` | `7daa42a` |
| `evaluation` | Read-only neměnný archiv pozorování jednotlivých zdrojů se zvlášť vedeným časem události, dostupnosti a pozorování. | `603c117` | `53dd85e` |

Uvolněný agent `healthcare` dostal další konkrétní úkol: samostatné read-only
spuštění diagnostiky přes CLI a `Zkontrolovat_Zdravotnicke_Identity.bat`.
Výstup `42210c1` byl převzat jako `1d6ba71`. Agent `identity_bulk` doplnil
úspěšný test všech 687 pozic v připraveném prostředí, `977b9bc` → `b70b88f`.
Koordinátor navíc opravil trvalé EIA kvóty pro dvě sdílené surovinové série
(`aeb83a4`), zapojil nové identity do SEC, SupplyChain, ScoutIndex a EPA,
zobrazil diagnostiku a změny závazků v aplikaci a přidal explicitní archivní
export. Důkazy jednotlivých výstupů jsou v `evidence/parallel2_*_20261011.json`.

Konfigurace nezávislých protistran je
`JOHNY_SKORE_COUNTERPARTY_IDENTITIES_FILE`; konfigurace vlastnictví zařízení
je `JOHNY_SKORE_REGULATORY_IDENTITIES_FILE`. Bez doloženého manifestu zůstává
nález jmenným kandidátem. Neplatný manifest vrací explicitní chybový stav
bez zveřejnění jeho hodnot či cesty. Produkční soubor vztahů protistran ani
EPA nebyl vytvořen z fixture záznamů.

Archiv je omezený na 687 × 16 zdrojových buněk a limity pozorování/kontrol;
makrosérie a souhrnné 13F kontroly nedokládají kontrolu jednotlivého emitenta.
FDA oborový výběr 111, CMS 19 a ClinicalTrials 49 jsou výzkumní kandidáti,
nikoli konečné relevantní jmenovatele. Doklad dvou aplikací, studie nebo
nemocničních řádků neznamená úplnou skupinu firmy. Původní registry a skóre
zůstávají zachovány, 13F stále 150/687 a 537 nezmapovaných.

### Konkrétní otevřená implementační fronta

1. `identity_bulk`: skutečný omezený archiv oficiálního SEC seznamu a ticker
   katalogu; poté zdrojově doložené přechody BLK/SO a další instrumenty.
   Nepoužívat syntetické přechodové odstavce jako produkční identity.
2. `healthcare`: doplnit primární, datované vlastnické a produktové vazby;
   samostatně zachytit pozitivní i úplné negativní dotazy s publisher scope.
   CLI a diagnostiku již neopakovat bez nové chyby.
3. `regulatory`: připravit posouzené vlastnictví EPA FRS zařízení a NHTSA
   produktových řad; důkaz vlastnictví nesmí přenášet právní účinek na skupinu.
4. `contracts`: získat omezenou skutečnou historii verzí či transakcí awardu
   se zdrojovým pořadím; samotný rozdíl dvou kumulativních snímků není historie.
5. `sec`: naplnit nezávislými primárními důkazy datovaný registr protistran;
   rozšířit přesné instrumentové/osobní identity Form 4 a skutečné IR zdroje.
6. `evaluation`: navázat skutečné přírůstkové archivy jednotlivých běhů,
   obsahově ověřit dostatečnou historii a dostupnost štítků; až poté OOS
   srovnání. Dnešní archiv nenahrazuje přepsané starší source-check řádky.
7. Koordinátor: omezené veřejné energetické/surovinové zdroje a datované
   firemní expozice; potřebné přístupy, Windows a akceptace vést odděleně.

Úplná akceptace nadále **0/21**. Nové testy jsou převážně syntetické a
lokální Linux není skutečný koncový Windows běh. Nové živé pokrytí a OOS
důkazy tato dávka netvrdí. Společná sada ověřila commit `2c8c4b6` na Linuxu:
899 testů, 0 chyb, selhání a přeskočení. Kompilace a diff kontrola prošly.
Obsahově načteno všech osm předávacích JSON důkazů a 167 existujících
referencí inventáře; to neuděluje chybějící akceptační tvrzení.
Přesné výsledky, úplný testový log, otisky a limity jsou v
`evidence/parallel2_integration_20261011.json`.

| Větev / vlastník | Specialisté | Konkrétní výstup první dávky |
|---|---|---|
| `identity_bulk` | identity, institutions | Hromadná příprava a kontrola SEC 13F identit, archivované vstupy, pokračování po přerušení, oddělené kandidáty a doložené vazby. |
| `healthcare` | fda, health_services | Datované produktové, aplikační, sponzorské a poskytovatelské vazby pro FDA, ClinicalTrials a CMS. |
| `regulatory` | regulatory | OFAC hlavní záznamy, aliasy a navazující poznámky; slabý alias sám nesmí potvrdit identitu. |
| `contracts` | contracts, aerospace | USAspending: uložené pokračování stránek, omezené dávky, izolace výpadků, skutečné počty úplných a částečných kontrol. |
| `sec` | sec, insider, suppliers | Zpracování Form 4 v trvalé SEC frontě; přesná identita emitenta a osoby, audit transakcí, opakování bez duplicit. |
| `evaluation` | verification, evaluation | Spustitelné historické vyhodnocení z neměnných snímků, kontrola času dostupnosti a přínosu jednotlivých specialistů. |

## Způsob práce

1. Obnovit aktuální HEAD otevřeného PR a přečíst inventář a tuto frontu.
2. Každému agentovi přidělit jeden konkrétní výstup a samostatný worktree.
   Šest agentů nesmí měnit stejný soubor ani současně zapisovat do větve PR.
3. Agent dokončí implementaci, relevantní testy a stručný vlastní JSON důkaz.
   Odevzdá lokální commit, seznam změn a zbývající překážky.
4. Koordinátor převezme commity, zapojí konfiguraci, běh a přehled aplikace,
   spustí společné kontroly a uloží výsledek na GitHub bez force push.
5. Uvolněnému agentovi přidělit další dostupný úkol podle fronty níže.
   Registrům ponechat vlastní kapacitu; jejich rozšiřování nesmí blokovat
   implementaci ostatních specialistů.

## Následující fronta

První společná dávka přidala hromadnou přípravu SEC identit, datované vztahy
zdravotnických objektů, zachování OFAC doplňků, navazování zakázek, trvalé
pozorování Form 4 a samostatné historické vyhodnocení v aplikaci. Jednotlivé
větve mají vlastní důkazy `evidence/parallel_*_20261011.json`. Všechny kódové
změny zůstávají odlišené od úplné akceptace specialisty.

FDA, CMS a ClinicalTrials používají jeden soubor vztahů:
`JOHNY_SKORE_HEALTHCARE_IDENTITIES_FILE`, standardně
`market_checker_app/data/verified_healthcare_identities.json`. Stejná datovaná
verze se používá při sběru, zjišťování čekající práce i vykazování pokrytí.
Neplatný soubor se vykáže jako chyba; nepřebírá se z něj úplné pokrytí.

Hromadný SEC postup připravuje kandidáty pro všech 687 tickerů. Výstup
posouzených instrumentů je samostatný návrh a zachovává produkční registry.
Samotný fixture průchod není živé ověření 687 identit. Historické vyhodnocení
se spouští explicitně tlačítkem nebo `Vyhodnotit_Specialisty.bat`; jeho report
nemění produkční skóre.

- **Identity / instituce:** hromadná zdrojová kontrola, širší instrumenty,
  změny emitentů a tříd akcií; živý omezený běh 13F.
- **Zdravotnictví / banky:** doložené vazby vlastnictví v čase, měření
  relevantního pokrytí FDA/CMS/ClinicalTrials a skupin FDIC.
- **Regulace:** produktové a vlastnické vazby NHTSA/EPA/DOJ; oddělené
  ověření tvrzení, identity a právního účinku.
- **Zakázky / obrana:** doložené příjemce rozšířit pomocí připraveného
  hromadného postupu; historii změn zakázek vést odděleně od výnosů firmy.
- **SEC / dodavatelé / IR:** datované právní osoby a protistrany,
  primární firemní oznámení a skutečné oborové zdroje.
- **Ceny / energie / obory:** ceny a corporate actions, vazby na zdroje
  surovin a energetická aktiva, vyhodnocení dostupných veřejných dat.
- **Ověření / historie:** archiv jednotlivých zdrojových příznaků,
  nezávislé koncové kontroly a historické vyhodnocení.
- **Přístupy:** FINRA, placený konsensus/opce/Level 2 a alternativní data
  zůstávají otevřené bez doloženého oprávnění. Dostupný veřejný vývoj tím
  nesmí čekat. Bez pokynu nekupovat data a nezakládat účty.

## Akceptace a pokračování

`market_checker_app/data/specialist_status.json` zůstává autoritou pro 21
specialistů. Kód, test, živá odpověď, identita, relevantní pokrytí,
uživatelský Windows a historické vyhodnocení jsou samostatné důkazy.
Nález podle názvu není automaticky vztah k emitentovi. Hostované Windows
nejsou koncovým během na uživatelově PC. Chybějící data se nevymýšlejí a
změny skóre vyžadují dřívější out-of-sample vyhodnocení.

Pokračování musí přebírat skutečné nehotové implementační úkoly z různých
oblastí. Nepoužívat opakované rozšíření registru o pět položek jako jedinou
práci celé dávky. Každé dokončené rozšíření workflow ověřit jednou
přiměřenými kontrolami; znovu pouze při změně či zjištěné chybě.

Žádné automatické obchodování. Oznámení úplného dokončení až po doložené
akceptaci všech specialistů; bez prázdných commitů a bez vymyšlených běhů.
