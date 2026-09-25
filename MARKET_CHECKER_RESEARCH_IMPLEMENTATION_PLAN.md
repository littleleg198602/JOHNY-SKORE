# Market Checker — implementační plán podle hloubkového výzkumu

Datum: 24. 9. 2026. Stav: PLÁN; tento commit nemění chování aplikace.

Podklad: `Market_Checker_hloubkovy_vyzkum_2026-09-22.md`, verze 2, 2 113 řádků, včetně přílohy U. Výzkum byl přečten a jeho návrhy porovnány s aktuálním vzdáleným `main` repozitáře `littleleg198602/JOHNY-SKORE`, commit `af92c51a16f8d299fc4a19098297b3c64a9b8b5f`. Jde o cílenou inspekci implementace, nikoli o nový kompletní test nebo živý coverage test.

## Cíl a pravidla

Automaticky získat, ověřit, uložit a předat agentům všechna dostupná relevantní data pro dodaný seznam. Uživatel nevyplňuje analytické údaje, dodavatele, události ani cenové body ručně. Klíče a nezbytná oprávnění se nastavují jednou; omezení přístupu se vykazují v reportu. Placené služby se bez rozhodnutí uživatele neobjednávají. Automatické obchodování se nikdy nezavádí.

Výstup zachová všech 687 původních vstupních řádků, jejich pořadí a hash. Výzkum uvádí 685 současných cenných papírů a 683 emitentů po aliasových a historických úpravách. Tato čísla jsou referenční výsledek výzkumu k 22. 9., nikoli trvalé konstanty ani dnešní počet úspěšných API odpovědí. Implementace je musí odvodit z doložených identit a dat platnosti. Nejednoznačný případ zůstane v karanténě, bez automatického vymazání vstupu.

Každý datový údaj má zdroj, datum dostupnosti a stav. Chybějící hodnota je NULL s důvodem, nikoli nula. Úspěšný download, správná extrakce a predikční přínos jsou samostatně ověřované výsledky.

## Co již existuje a na co navázat

| Oblast | Nález v main | Co chybí vůči výzkumu |
|---|---|---|
| Cenová vrstva | MT5, Yahoo klienti, cache, kalendář a kontrola OHLC | Další ověřený datový provider, přesná identita podkladu/CFD, úplnější corporate actions |
| SEC | Klient, fundamentální agent, datované facts/snapshoty a forenzní vrstva | Rozšíření a ověření příloh/kontextů, sdílení po emitentech, specializované sektorové metriky |
| Identity | Kanonický CSV seznam, registry, identity history/conflicts, GLEIF | Vstupní řádek versus security versus issuer, aliasová období, nástupnictví a sektorové routing profily |
| Zprávy | RSS a `SourceDiscoveryService` nad již načtenými položkami | Samostatné oficiální zdroje regulátorů, IR discovery, širší původní short-report discovery |
| Dodavatelé a vstupy | Automatická základní extrakce SEC textu, relationships/exposures | Přesnější extrakce tabulek a příloh, mapování protistran, skutečné automatické externí časové řady |
| Makro | Parser zadaných observations, datové tabulky a výpočet reportu | FRED/ALFRED/EIA a další sběrné konektory |
| Predikce | Verze snapshotů, candidate model, walk-forward a ablation služby | Připojení nových feature skupin s časově správnými daty a měření přínosu |
| Provoz | Týdenní runner, Windows spouštěče, fronta vyhodnocení labelů | Společná trvalá fronta sběru pro nové zdroje, denní aktualizace a jejich restart/checkpoint |
| Veřejné externí zdroje | V `collectors/` nejsou dedikované FINRA/FDA/USAspending/FRED/FDIC klienty | Nové konektory a jejich propojení až do agentů, SQLite a UI |

Předchozí výrok o dokončené opravě SQLite a QualityGate není důkaz nasazení: v kontrolovaném main stále existuje `row.rank_in_watchlist` bez normalizace a `_check_timestamp()` používá 15minutový limit i pro evidence. Začít bodem MC-00 a doručení ověřit v main.

## Etapa A — stabilita, identita a společná datová vrstva

### MC-00 — doručit provozní opravy

- Normalizovat pandas/numpy chybějící skaláry pro SQLite; uchovat NULL rank, cenu i další chybějící údaje.
- Rozlišit čerstvost signálu, technický čas zpracování a platnost podkladového datasetu. U dlouhého běhu evidence ze začátku nesmí selhat pouze kvůli době zpracování.
- Budoucí pozorování vždy kontrolovat proti skutečnému času kontroly; změna reference pro stáří nesmí označit legitimní pozdější záznam ze stejného běhu jako budoucí.
- Akceptace: reálný zápis nullable Int64, NaT a numpy skalárů; 53minutový běh s evidencí na začátku, uprostřed a konci; budoucí/expirující/stará data mají správný výsledek; zachovat rollback neúspěšné transakce.
- Stav: TODO. Závislosti: žádné. Náklad na zdroj: žádný.

### MC-01 — registr původních řádků, instrumentů a emitentů

- Porovnat původní CSV se seznamem přílohy U, uložit rozdíly, původ a hash; nepřepsat potichu existující seznam.
- Rozšířit současný registry o stabilní security/listing klíče, historické aliasy a nástupnické vztahy. Znovu využít existující entity identity versions.
- Ověřit BRKB/třídu B, P/PSTG, LEG/SGI, XOM/BLK a přejmenování FISV/MRSH/XYZ/GAP/BRSL podle datovaných primárních dokladů a aktuálního katalogu.
- GOOG/GOOGL a FOX/FOXA sdílejí firemní dokumenty, ale mají samostatné cenové řady. Nepřipojovat historii znovupoužitého tickeru jiné firmy.
- Akceptace: 687 auditních řádků beze ztrát; alias jeden cenový job; dvě třídy dva cenové joby; explicitní historical/unresolved stav; žádná změna historie bez lineage.
- Závislosti: MC-00. Přístup: SEC/Nasdaq; broker inventář samostatně na Windows.

### MC-02 — sektorové a segmentové profily

- Převést 39 výzkumných profilů do verzovaných pravidel relevance zdrojů a metrik. Profil je vlastní výzkumná klasifikace, nikoli GICS licence.
- Podporovat více segmentů firmy a jejich časovou platnost. Banka, pojišťovna, zprostředkovatel, BDC, REIT a biotech potřebují různé účetní ukazatele.
- Příklady: MU zásoby/marže/capex; JPM vklady/kapitál/kredit; TTD reklama; MLM stavební poptávka; AVGO čipy i software.
- Akceptace: každý vstup má profil nebo explicitní nevyřešený stav; irelevantní metrika je NOT_APPLICABLE a nevytváří sankci ve skóre.
- Závislosti: MC-01.

### MC-03 — společný kontrakt zdroje a důkazů

- Rozšířit současné tabulky a služby pomocí verzovaných migrací; nevytvářet vedle nich druhý nespojený systém.
- Sjednotit source status, data status a applicability. Zachovat published_at, available_at, první observed_at, jednotlivé retrieved_at, přesnost času, verzi dokumentu/parseru a evidence locator.
- Zavést source policy pro povolené ukládání raw/derived dat, externí LLM a retenci; klíče nikdy v logu ani v commitu.
- Rozlišit historickou rekonstrukci veřejných informací a replay skutečných znalostí programu; nové stažení nesmí přepsat první pozorování.
- Kódy: AUTH_REQUIRED, API_KEY_REQUIRED, REQUIRES_LICENSE, RATE_LIMITED, SOURCE_BLOCKED, SOURCE_TIMEOUT, SCHEMA_CHANGED, INCOMPLETE_PAGINATION, IDENTITY_UNRESOLVED, IDENTITY_CONFLICT, STALE_DATA, DATA_NOT_DISCLOSED, NO_MATCH_IN_SCOPE, NOT_APPLICABLE a POINT_IN_TIME_UNSAFE.
- Akceptace: nová verze dokumentu nezmění starý snapshot; prázdné/vendor pole není automaticky nezveřejnění; každá vyplněná metrika má doložitelný původ.
- Závislosti: MC-01; využít existující degradation a snapshot služby.

### MC-04 — trvalá fronta automatického sběru

- Denní inkrementální aktualizace, týdenní catch-up, checkpoint, dedup úloh a zámek proti dvěma souběžným běhům.
- Centrální limity po providerovi, Retry-After, omezený backoff, pagination cursor, circuit breaker; neúspěšný zdroj neblokuje nezávislé zdroje.
- MT5 obsluhovat samostatně; Streamlit má zobrazovat dokončený stav a nespouštět sběr při každém překreslení.
- Jednorázové nastavení přes UI/spouštěč a Windows Credential Manager. Navázat na existující .bat/PowerShell spouštění bez potřeby příkazové řádky.
- Akceptace: restart pokračuje od checkpointu, nepřidá duplicity; 429/403/timeout mají odlišnou reakci; vypnutý počítač doplní dostupný backlog.
- Závislosti: MC-03. Živá Windows akceptace samostatně.

## Etapa B — společný datový základ

| ID | Implementace a předání agentům | Akceptační podmínka | Závislosti / přístup |
|---|---|---|---|
| MC-05 SEC | Doplnit současný klient o potřebný rozsah filings/exhibits, dedup po CIK, amendments, jednotky/období a vlastní XBRL/segmentové kontexty; agenti čtou společné důkazy | Starší filing mimo recent blok, 20-F/6-K, opravný výkaz a změna CIK; bez novější revize před cutoffem | MC-01/03/04; platný SEC User-Agent nastavený jednou |
| MC-06 PRICES | Doplnit cenový provider po ověření datového oprávnění; kandidáti Alpaca a Massive, případně Tiingo dle pokrytí. Evidovat raw/adjusted ceny, splity/dividendy, delisting a spin-off | Pokrytí každého instrumentu nebo přesný reason; validní svíčky/seance/objem; MT5 CFD se nesmíchá se skutečnou akcií; stávající split-price target zůstane verzovaný, případný total-return target dostane novou verzi | MC-01/03/04; bezplatný klíč nebo později schválená licence |
| MC-07 OWNERSHIP | Rozšířit governance Form 4/13D/G; doplnit 13F manager discovery a držby, nikoli pouze issuer submissions | P/S versus vesting/daň F/deriváty; amendment bez dvojího započtení; 13F až po publikaci a s přesnou třídou | MC-05; stejné SEC připojení |
| MC-08 SHORT | FINRA periodický short interest a zvlášť daily short volume; vlastní borrow data jen s oprávněným broker přístupem | Settlement a publikace zvlášť; žádná náhrada short interest součtem daily volume; NULL při chybě | MC-01/03/04/06; ověřit skutečný produkční dataset a případné veřejné credentials |
| MC-09 NEWS | Automatické IR discovery z doložených firemních domén; SEC události a stávající RSS do společných eventů; oddělit oznámení/dokončení/zrušení | Stejná událost z více médií nezvyšuje score opakovaně; ticker A/P/ON a BYD/FOXF bez falešného spojení; aktualizace guidance není stará zpráva | MC-01/03/04/05; full text pouze v povoleném rozsahu |
| MC-10 SHORT-REPORTS | Centrální discovery původních vydavatelů, povolené feedy/archivy, odpovědi emitenta a následné ověřování tvrzení | Mediální zmínka ani long teze není původní short report; neaktivní archiv nemá falešný alarm; původ a datum jsou doloženy | MC-09 a stávající ShortReport/ClaimVerification; zvlášť ověřit podmínky každé domény |

## Etapa C — konkrétní firmy a specializované zdroje

| ID | Implementace | Prioritní použití | Akceptace a přístup |
|---|---|---|---|
| MC-11 RELATIONSHIPS | Automaticky odvozovat doložené dcery, značky, produkty a registry IDs; vlastnická platnost v čase | Základ pro zakázky, regulátory, bankovní dcery a CMS | Name search smí vytvořit kandidáta, ne potvrzenou vazbu; nejednoznačné ID zůstává unresolved. SEC/registry/GLEIF/OpenFIGI dle oprávnění |
| MC-12 CONTRACTS | USAspending awards a transakce; SAM jako rozšíření; sdílet s Contracts/SupplyChain | LMT, RTX, NOC, GD, HII, LDOS, BAH, CACI, SAIC a další podle relevance | Recipient/UEI → doložená dcera; ceiling, obligation, modification a tržby se nesčítají. USAspending veřejná větev, SAM klíč dle skutečné kvóty |
| MC-13 HEALTH | FDA drugs/devices/recalls/CRL, ClinicalTrials.gov; CMS pro plány a poskytovatele | LLY/SRPT/ISRG/MDT/UNH a příslušné profily | NCT/product/applicant/plan/facility ID → správný issuer; public date versus datum dopisu; zdravý endpoint bez nálezu není jistota nulového rizika |
| MC-14 BANKS | FDIC/FFIEC, podle potřeby Fed; kapitál, vklady, kvalita úvěrů | JPM/BAC/C a ostatní banky, doložené bankovní dcery | CERT/RSSD a holding CIK se nerozpustí v jednom součtu; kvartál a revize časově správně; veřejné/bulk cesty, registrace dle vybraného rozhraní |
| MC-15 MACRO-COSTS | FRED/ALFRED, EIA, vybrané USDA/USGS/Census řady; připojit ke stávajícím makro a scenario službám | Energie, materiály, sazby, inflace, bydlení a sektorová poptávka | Společná řada se stáhne jednou; units/vintage/availability; fyzická cena/proxy/hedge se odlišují. Jednorázové klíče dle zdroje |
| MC-16 REGULATORY | OFAC/DOJ/FTC/BIS/Federal Register a profilově EPA/NHTSA/FCC/FERC/FAA; BTS provozní data | Polovodiče, utility, auta, telekomunikace, průmysl a aerolinky | ID/provozovna/produkt/dcera s platností; probíhající řízení není rozsudek; obecná regulace bez doložené expozice není automatická ztráta konkrétní firmy |
| MC-17 EXTRACTION | Rozšířit existující filing extrakci na koncentrace, dodavatele/zákazníky, kontrakty, materiály, hedge a přenos nákladů; propojit s cenami vstupů | Podle profilů, společný základ všech relevantních emitentů | Citace/odstavec/tabulka pro každý fakt; anonymous zůstává anonymous; chybějící hedge není 0; žádná sensitivity bez vstupů nebo označených scénářových předpokladů |
| MC-18 COUNTERPARTIES | Stávající report zdraví protistran doplnit automatickým získáním dostupných výkazů, registrů, sankčních a soudních událostí | Pouze skutečně nalezené protistrany | Private/unresolved má omezený datový stav; žádný fiktivní rating; protistrana se automaticky nepřidá do investičního univerza |

Společné závislosti MC-11 až MC-18: MC-01 až MC-05, MC-02 pro relevance routing a MC-03/04 pro data/provoz. MC-12/13/14/16 vyžadují příslušné vazby z MC-11. MC-17 sdílí SEC evidence; číselné scénáře dále vyžadují MC-15. MC-18 navazuje na nalezené vztahy.

## Etapa D — podmíněné zdroje, predikce a dokončení

| ID | Úkol | Kdy jej nasadit a jak ověřit |
|---|---|---|
| MC-19 CONSENSUS | Denní vlastní snapshoty EPS/revenue/target estimates a revizí; earnings surprise proti předvýsledkovému odhadu | Provider a skutečný endpoint podle pokrytí a licence. FMP/EODHD či jiný poskytovatel jsou kandidáti, nikoli zakoupené služby. Historie období není historie vintage; současný odhad nepoužít do minulosti |
| MC-20 OPTIONS-BORROW | Opční chain snapshots, IV/skew/term structure/OI/volume PCR; broker borrow samostatně | Oprávněný účet/licence, liquid/standard versus adjusted contracts. NO_OPTIONS, STALE_QUOTE a API failure odděleně. Bez licence povolený modul vypnutý s důvodem |
| MC-21 EXPERIMENTS | Povolené ATS jobs, Wikimedia attention, Google Trends po přidělení přístupu; social/placená alternativa a research pouze selektivně; L2 jako samostatný podmíněný experiment | Metadata-only nebo NOT_ENABLED pokud přístup nevyhoví. Levné OHLCV proxy se neoznačují jako skutečné order flow. Každý experiment má předem stanovenou metriku, náklad a podmínku ukončení |
| MC-22 MODEL-INPUTS | Nové informace převést na verzované features a report agentů; pravidla účetní použitelnosti podle profilů; evidence proti/pro scénář | Agenti skutečně přečtou uložený údaj a uvedou zdroj v závěru. Extrakce přes LLM jen s doloženou citací a povoleným poskytovatelem/rozpočtem. Čistě výpočtové poměry počítá deterministický kód |
| MC-23 EVALUATION | Rozšířit současné walk-forward/ablation reporty o nové skupiny, profil a režim; benchmark, kalibrace, ranking a výnosové metriky | Stejný target/cutoff, oddělené train/test, časově správné identity, případné náklady; nový zdroj ovlivní produkční score až po doloženém přínosu. Nedostatek uzavřených výsledků je WAIT_DATA |
| MC-24 UI-ACCEPTANCE | Společný report dostupnosti, historie a předání do běžného tlačítka analýzy i týdenního runneru | Pilot 48 vstupů z výzkumu, poté všech 687 auditních řádků. Known-positive živé vzorky, restart, výpadek, bez duplicit a bez order API. Uživatel vidí datum posledního úspěchu, přesné chybějící pole, důvod a dopad |

MC-19/20/21 jsou podmíněné přístupem, licencí a rozpočtem. Jejich blokace nezastaví MC-00 až MC-18 ani report. MC-22/23 navazují na každou dokončenou datovou skupinu průběžně; není nutné čekat na všechny volitelné zdroje. MC-24 se rozšiřuje s každou etapou, závěrem má celý povolený rozsah.

## Úplnost vůči 20 okruhům výzkumu

| Okruh výzkumu | Implementační body |
|---|---|
| 1 SEC | MC-05, MC-07, MC-17 |
| 2 Ceny a corporate actions | MC-01, MC-06 |
| 3 Zprávy | MC-09 |
| 4 Placený research | MC-21, podmíněné API/licence |
| 5 Short reporty | MC-10 |
| 6 Konsenzus | MC-19 |
| 7 Short interest a borrow | MC-08, MC-20 |
| 8 Opce | MC-20 |
| 9 Level 2/order flow | MC-06 proxy, MC-21 skutečný feed podmíněně |
| 10 Dodavatelé a zákazníci | MC-11, MC-17 |
| 11 Soukromé protistrany | MC-18 |
| 12 Kontrakty/granty | MC-12 |
| 13 Materiály/energie/hedging | MC-15, MC-17 |
| 14 Regulátoři | MC-13, MC-14, MC-16 |
| 15 Insider/instituce | MC-07 |
| 16 Sociální sentiment | MC-21, podmíněně |
| 17 Search intensity | MC-21, podmíněně |
| 18 Alternativní data | MC-13/14/15/16 veřejné sektorové; MC-21 experimenty |
| 19 MT5 | MC-01, MC-04, MC-06, MC-24 |
| 20 Neveřejné informace | MC-03, MC-18, MC-24; pouze vykázání mezery |

## První implementační balík a způsob předávání

První balík: MC-00 → MC-01 → MC-02/03 → MC-04. Výsledkem má být stabilní běh se správnou identitou, přehledem skutečného pokrytí a obnovitelným sběrem. Poté MC-05/06/07/08/09; sektorové zdroje následují po potřebných mapováních. Pilot lze provádět průběžně, ale není náhradou plného auditu 687 řádků.

U každého bodu průběžně zapisovat implementační commit, dotčené soubory, test a živý důkaz nebo konkrétní blocker. Stavy: TODO → IN_PROGRESS → IMPLEMENTED → TESTED → MERGED; zvlášť LIVE_VERIFIED a WAIT_DATA/WAIT_ACCESS. DONE nepoužívat pro pouhý parser nebo prázdnou datovou strukturu.

Konektor je implementačně dokončen až po cestě zdroj → validace → SQLite → agent → běžný report, včetně chyb a restartu. Živý úspěch vyžaduje ověřený konkrétní objekt/obsah, nikoli pouze HTTP 200. Rozsah pokrytí se měří po jednotlivých polích. Predikční validace zůstává oddělenou branou.

Pro první bezplatné kroky není třeba ručně vyplňovat údaje o firmách. Chybějící skutečný SEC kontakt, API klíč, broker session nebo licence bude uveden jako konkrétní jednorázový blocker. Bez souhlasu se nekupují předplatná, nevytvářejí placené cloudové/LLM závislosti a neposílají žádosti třetím stranám.

## Podklady a omezení ověření

- Úplný výzkum verze 2 včetně přílohy U; ceny a plošná oprávnění nejsou tímto plánem znovu potvrzeny.
- Aktuální `main` byl ověřen pomocí vzdáleného git ref a stažen k read-only porovnání. Zdrojová větev nebyla změněna.
- SEC veřejné datové zdroje: https://www.sec.gov/about/developer-resources a https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data
- FINRA specifikace: https://developer.finra.org/docs — nepoužívat mock dataset jako produkční důkaz.
- USAspending specifikace: https://api.usaspending.gov/docs/endpoints
- Znovu načtené primární podklady identit: https://www.everpuredata.com/uk/company/newsroom/press-releases/everpure-to-change-ticker-symbol.html a https://www.sec.gov/Archives/edgar/data/58492/000119312526366694/d152590d8k.htm
- Před potvrzením konkrétního konektoru doplnit aktuální schema, licenci, kvótu, coverage a praktický malý živý test. Výzkumné čtení webu není test produkčního API.

V této etapě vznikl implementační plán; nové konektory ani opravy aplikace tímto dokumentem ještě nejsou implementovány ani nasazeny.
