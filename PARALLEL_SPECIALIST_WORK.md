# Souběžné dokončování specialistů

Platí od 11. 10. 2026. Vývoj běží v nejvýše šesti oddělených větvích;
koordinátor přebírá změny, řeší integraci a provádí společné kontroly.
Počet vývojových větví není počet dokončených analytických specialistů.

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
