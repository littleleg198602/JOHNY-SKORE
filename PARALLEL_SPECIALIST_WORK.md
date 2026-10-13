# Souběžné dokončování specialistů

Platí od 11. 10. 2026. Vývoj běží v nejvýše šesti oddělených větvích;
koordinátor přebírá změny, řeší integraci a provádí společné kontroly.
Počet vývojových větví není počet dokončených analytických specialistů.

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
včetně celého 687 cached testu s původním 60s limitem. Nový hostovaný
Windows/required CI výsledek je po publikaci stále samostatné ověření. Profil starého
kódu ani běh se souběžnými agenty se nevydává za férové srovnání výkonu/OOS.
Původní publisher raw bytes a whitespace upozornění zůstávají zachované.
Z 41 zachycených odpovědí je 40 veřejných raw (38 HTTP200 / 2 HTTP400),
jedno dokumentační HTML s přístupovou hodnotou zůstává withheld s otiskem.
Obsahový audit ověřil 153 současných odkazů s otisky; staré diagnostiky jsou označené vlastní
verzí, nejsou akceptací finálního kódu. Důkaz:
`evidence/batch4_content_audit_20261013.json`.

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
