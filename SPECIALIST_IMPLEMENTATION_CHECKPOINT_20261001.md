# NEW ANALYZER — checkpoint 1. 10. 2026

Pracovní větev: `fix/specialist-daily-capacity`, PR #160.
Autoritativní inventář všech 21 specialistů:
`market_checker_app/data/specialist_status.json`. Dosud **0/21 DONE**.
Tento dokument uchovává plán pokračování a akceptaci. Automatické
pokračování je popsané níže; úplná provozní akceptace dosud neproběhla.

## Co je doložené v tomto checkpointu

- FDIC skutečně používá v financials jiné názvy než právní názvy bank.
  JPM/BAC/WFC/C mají přesné CERT a datované citace příslušného API názvu;
  neznámý název, chybný CERT nebo datum vytvářejí částečný výsledek.
- NHTSA živý zdroj vrací `DD/MM/YYYY`. Opravený parser a kontrola
  odmítnutých řádků brání falešně úplnému nulovému výsledku.
- CMS: publikovaný dataset hospital-all-owners, přesné organizační
  názvy vlastníků, role, association date a procenta; osobní vlastníci
  nejsou ukládáni. Dotazy patří jen 19 profilům HEALTH_SERVICES.
- ClinicalTrials.gov: NCT, přesný lead sponsor/collaborator a jejich
  oddělené role, poslední zveřejněná aktualizace a fáze. Dotazy patří
  26 PHARMA a 23 MEDTECH profilům. Není tím doložen vztah studie k emitentovi.
- CMS/ClinicalTrials mají nejvýše 10 firem a dvě stránky po 100 na běh,
  časový rozpočet 60 sekund a zastavení po třech chybách. Již zahájený
  HTTP požadavek může doběhnout do svého 20sekundového timeoutu.
  Nedokončená firma zůstává splatná; dokončené dřívější kontroly se uchovají.
- OFAC: celý oficiální SDN CSV export jedním omezeným stažením; pouze
  primární názvy organizačních entit. Parser ověřuje strukturu a ID,
  přijímá pozorovaný koncový DOS EOF a ukládá otisk snapshotu. Oficiální
  export má ověřenou cestu přes konkrétní distribuční host; jiné redirecty
  jsou odmítnuté. Bez aliasů, Non-SDN a vlastnického look-through to není
  úplné sankční prověření firmy.
- Nové zdroje jsou zapojené do denního runneru, politik uložení,
  průběžných souhrnů a UI. Pokrytí CMS/ClinicalTrials má sektorový
  jmenovatel aktivních SEC identit; OFAC se obnovuje po dni.
- FDA/CMS/ClinicalTrials/OFAC jmenné výsledky zůstávají `UNVERIFIED`.
  Association date, historický dopis ani poslední aktualizace studie
  se nezaměňují za okamžik našeho prvního pozorování.
- Uložené živé případy: `evidence/specialist_live_20261001.json` (13),
  `evidence/healthcare_live_20261001.json` (4),
  `evidence/ofac_live_20261001.json` (2). Všech 19 případů PASS na zdejším
  Linux hostu; nejde o Windows nebo o všech 687 firem.

Navazující krok: `evidence/usaspending_live_20261001.json` dokládá další
čtyři PASS případy. Sikorsky UEI pilot uložil 100 zakázek z jedné stránky,
replay nevytvořil duplicity a další stránka správně zůstala PARTIAL.
Negativní UEI a recipient name jsou pouze absence v omezeném dotazu.
Součet je **23 živých případů u sedmi veřejných zdrojů**.

V UI je stažení provozního přehledu specialistů: 687tickerový archiv se
ověřuje podle skutečných řádků a pořadí, SEC počty se omezují na kanonický
universe, zdroje bez běhu mají NEVER_RUN, sektorové kontroly zachovávají
PARTIAL a přístupové údaje nejsou exportovány. Přehled nedokládá automaticky
Windows end-to-end nebo historickou evaluaci a nepřepisuje stav specialistů.

## Zbývající práce podle specialisty

| Specialista | Další konkrétní krok |
|---|---|
| Identita | Datované aliasy, dcery, značky a produkty; zapojit do cen i sektorových zdrojů. |
| SEC | Živý běh celé fronty 687 firem, historie oprav a Windows akceptace. |
| Insider | Živé typy Form 4 a datovaní reporting persons/instrumenty 13D/G. |
| Kontrakty | Další ticker–dcera–UEI, modifikace zakázek a aktuální živé důkazy; SAM přístup. |
| Regulace | Další vozidla, OFAC weak aliasy/Non-SDN/vlastnictví, DOJ a EPA; ALT.CSV entity aliasy jsou zapojené a mají živý zdrojový důkaz. |
| Dodavatelé | Z citovaných právních názvů vytvořit datované entity a vztahy; koncentrace a živé dokumenty. |
| IR a zprávy | Ověřené firemní IR kanály a spojení RSS s primárním oznámením. |
| Ceny | Živá akceptace 687 po close opravě; BRKB/PSTG/LEG a corporate actions. |
| Short interest/borrow | FINRA účet a schema; datovaný instrument; licencovaný borrow pilot. |
| FDA/medicína | Produkt/application/sponsor–emitent a širší události; dnešní schema není úplný crosswalk. |
| Komodity | EIA živě s klíčem, USDA/USGS, firemní expozice a historická dostupnost hodnot. |
| Banky | Registry má šest emitentů z 22 BANK profilů, nově PNC/USB. Zbývá dalších 16 emitentů, další banky uvnitř skupin a omezené obnovovací dávky; jednotlivý CERT není skupina. |
| Zdravotní služby | CMS owner–dcera–emitent/provider, payer exposure a datované změny. |
| Energie/utilities | Vlastní aktiva a expozice propojit s primárními tržními/regulatorními zdroji. |
| Obrana/aerospace | Programy, dcery a recipient identity mimo jediný Sikorsky pilot. |
| Ostatní sektory | Připojit konkrétní poskytovatele k otázkám 39 profilů a změřit relevantní pokrytí. |
| Instituce | Více doložených CUSIP, 13F amendments a živý SEC ZIP. |
| Konsensus/options/Level 2 | Poskytovatel, entitlement a měřený licencovaný pilot. |
| Alternativní data | Vybraný licencovaný pilot a změřená přidaná hodnota. |
| Ověřování/missingness | Doložit hledání, usable/failed stavy a nezávislé primární potvrzení tvrzení. |
| Evaluace/audit | Skutečné coverage/outage/evidence a historické out-of-sample vyhodnocení před změnou skóre. |

Na zdejším hostu nejsou nastavené SEC kontaktní User-Agent, FINRA credential,
FRED ani EIA klíč. To nevypovídá o jejich přítomnosti na uživatelově Windows
nebo v GitHub secrets. Windows koncový běh, datové licence a skutečnou
historickou výkonnost nelze nahradit syntetickými testy.

Uživatel požádal o pokračování a oznámení až po dokončení všech specialistů.
Je nastavené automatické pokračování s hodinovým intervalem: načte tento
checkpoint a aktuální GitHub stav, vezme další dostupný konkrétní krok,
ověří změnu a uloží ji na pracovní větev/PR. Při chybějícím oprávnění či
Windows/historických důkazech zachová blokaci a pracuje na jiném dostupném
kroku; nevytváří prázdné commity. Bez dalšího pokynu nemerguje PR, nekupuje
data ani nezřizuje účty. Oznámení vyžaduje celý inventář DONE/VERIFIED a
skutečně existující akceptační důkazy, ne jen neprázdné cesty či zelené testy.

Žádná část tohoto úkolu nepovoluje automatické zadávání obchodů.

## Navazující checkpoint — OFAC ALT.CSV (1. 10. 2026)

Další dostupný krok regulace je implementovaný: oficiální ALT.CSV se
spojuje se SDN.CSV výhradně přes ENT_NUM. Parser kontroluje všech pět
polí, jedinečnost ALT_NUM a existenci rodiče; uchovává pouze organizační
entity. Shoda celého aktivně pozorovaného SEC názvu s primary/aka/fka/nka
vytváří UNVERIFIED kandidáta s uvedením shodného názvu a obou otisků,
nikoli potvrzenou sankci emitenta nebo změnu skóre. Stejné aliasy jednoho
SDN vytvoří jeden kandidát. Opakování bez změny nálezu nevytváří duplicity.

Denní sběr stáhne nejvýše dva exporty (SDN 8 MB, ALT 2 MB; 20sekundový
timeout každého požadavku). Neplatný/neúplný ALT včetně sirotčího ENT_NUM
nedokončí žádnou kontrolu emitenta. Starší primary-only kontrola zůstává
v historii, ale díky verzovanému scope klíči nepřispívá do aktuálního
pokrytí a firma je ihned splatná pro rozšířenou kontrolu. Primary-only
snapshot bez ALT je PARTIAL. Oddělené otisky nedokládají atomickou verzi
obou publikací. Weak aliasy v remarks, adresy/spillover, Non-SDN,
vlastnický look-through a primární ověření emitenta stále chybějí.

`evidence/ofac_alias_live_20261001.json` obsahuje tři skutečné PASS
kontroly publikace: primary entity, alias AERO-CARIBBEAN přes ENT_NUM 36
a ALT_NUM 12 a nepřítomné celé jméno v obou tabulkách. Ověřeno 19 452
primárních řádků (10 006 entit) a 20 220 ALT řádků. Nebyl přiřazen žádný
watchlistový emitent; nejde o Windows akceptaci nebo relevantní pokrytí
687 tickerů. Regulační specialista proto zůstává PARTIAL/PENDING.

Oficiální kontrakt byl ověřen 1. 10. 2026:
https://ofac.treasury.gov/media/29976/download?inline=
a https://ofac.treasury.gov/sdn-list-data-formats-data-schemas/tutorial-on-the-use-of-list-related-legacy-flat-files.
Celá deterministická sada prošla 465 testy včetně UI a 687tickerových
kontraktů. Otisky kódu a časy jsou v `evidence/ofac_alias_tests_20261001.json`;
9 cílených OFAC kontrol ověřuje mimo jiné migraci scope bez přepsání
historie, sirotčí/duplicitní ID, chybu druhého exportu a deduplikaci.
Další regulační kroky: Non-SDN/weak aliasy/vlastnictví, DOJ/EPA a další
datované produkty; ostatní specialisté a přístupové/Windows/historické
blokace uvedené výše zůstávají otevřené.

## Navazující checkpoint — PNC a USB bankovní identity (1. 10. 2026)

Přidány dvě doložené vazby: PNC (CIK 0000713676) → PNC Bank,
National Association → CERT 6384 a USB (CIK 0000036104) → U.S. Bank
National Association → CERT 6548. Oficiální SEC 2025 10-K/Exhibit 21
a FDIC institutions odpovědi potvrzují právní jméno a vazbu na emitenta.
FDIC financials dokládají přesné zkrácené názvy PNC BANK NATIONAL ASSN
a U S BANK NATIONAL ASSN. Datum vztahu je 31. 12. 2025, čas znalosti
je až tento běh; dřívější replay tyto identity nepoužije. Nové issuer
CIK citace se při načtení musí shodovat s oběma SEC cestami a zapisují
se do výsledků odděleně od banky. Starší čtyři mapování zůstávají zachovaná.

Podklady a skutečné FDIC institution/financial payloady jsou v
`evidence/fdic_expansion_identity_20261001.json`. Dvě kladné živé
kontroly a záporný neexistující CERT jsou v
`evidence/fdic_expansion_live_20261001.json`: každá nová banka uložila
dva kvartální výkazy a replay nevytvořil duplicity. Nový smoke přepínač
`--fdic-tickers PNC USB` omezuje opakování na zvolená mapování.
Živé API bylo omezené na dva řádky na banku a kontrolu neexistujícího
CERT, nikoli celé dějiny či všechny banky.

Registry pokrývá šest různých tickerů z 22 BANK profilů; zbývá ALLY,
CFG, CFR, COF, EWBC, FHN, FITB, HBAN, KEY, MTB, OZK, PNFP, RF, TFC,
WAL a ZION. To je změřená úplnost mapování v repozitáři, nikoli runtime
coverage na Windows, úplnost všech dcer skupiny či CUSIP/corporate-action
historie. Bankovní hodnoty nejsou konsolidované hodnoty emitenta a skóre
se nemění. Bankovní specialista zůstává PILOT/PENDING; všech 21 specialistů
má stále otevřenou úplnou akceptaci.

Další dostupná práce: ověřit zbývající bankovní dcery a přidat omezené
obnovovací plánování FDIC; poté pokračovat produktovými/sponsor vztahy
FDA a owner/provider vztahy CMS. SEC, FINRA, FRED a EIA nastavení na
tomto hostu stále chybějí; přítomnost na Windows/GitHub není tímto testem
ověřená. Windows a historická evaluace zůstávají otevřené.

Celá místní sada má 468/468 PASS; čas, otisky kódu a registru i první
neúspěšný pokus kvůli chybějícím importům hostitele jsou v
`evidence/fdic_expansion_tests_20261001.json`. Po instalaci uložených
projektových závislostí se testy neměnily. Kompilace a kontrola diffu
prošly. Zelené testy nepřepisují stav PILOT/PENDING ani nedokládají
historickou výkonnost nebo běh uživatelova Windows.

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
