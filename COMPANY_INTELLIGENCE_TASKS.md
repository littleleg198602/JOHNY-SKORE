
# JOHNY-SKORE – predikční a datová roadmapa

Tento soubor je kanonický backlog pro analytický program JOHNY-SKORE. Určuje, co má systém skutečně dělat, jaká data smí použít a podle čeho poznáme, že nová vrstva pomohla.

> **Aktualizace 2026-09-11:** aktuální konkrétní pořadí oprav je v [§11 – auditní úkoly AUD-001 až AUD-015](#11-implementační-úkoly-z-auditu-2026-09-11). Historické implementační stavy níže nejsou potvrzením nasazení ani splnění všech akceptací.

## 1. Rozsah projektu

### Produkční cíl

- **687 tickerů** je skutečný produkční cíl.
- Ticker universe tvoří americké akcie vybrané z Nasdaq, S&P 500 a WS30.
- **36 tickerů není konečný rozsah.** Je to pouze technický pilot a smoke-test.
- Pilot slouží k ověření kódu, zdrojů a persistence. Nesmí se zaměnit za analytický výsledek celého projektu.
- Každý produkční běh musí umět zpracovat všech 687 tickerů v dávkách, s cache a přesným reportem chybějících tickerů.

### Co má být výsledkem

Systém nemá být věštec ceny. Má každý týden seřadit 687 akcií podle pravděpodobnosti, že během následujících pěti obchodních dnů překonají zvolený benchmark nebo svůj sektor.

Primární výstup:

- pořadí tickerů 1–687,
- percentil a skóre relativní síly,
- pravděpodobnost nadvýkonnosti,
- očekávaný nadvýnos pouze jako pomocný údaj,
- kvalita a úplnost dat,
- hlavní důvody,
- hlavní rizika,
- stav `BUY_CANDIDATE`, `HOLD`, `AVOID` nebo `INSUFFICIENT_DATA`.

Sekundární horizonty 20 a 60 obchodních dnů se přidají až po ověření pětidenního modelu. Nesmí vzniknout tři neověřené modely současně.

## 2. Co program skutečně dělá dnes

Současná verze je bezpečný heuristický agregátor, nikoli prokázaný predikční model.

Pro ticker typicky:

1. načte cenu, historii a část fundamentů z Yahoo,
2. spočítá technické ukazatele a volatilitu,
3. načte přibližně několik desítek článků,
4. vypočítá sentiment a důvěru ve zdroje,
5. přidá analytický konsensus a valuaci dostupnou přes Yahoo,
6. přidá behaviorální a riskové ukazatele,
7. použije SEC/forenzní vrstvy hlavně jako bezpečnostní veto,
8. vytvoří `BUY/HOLD/SELL`, `UP/FLAT/DOWN` a případně `NO_TRADE`.

Historický 36tickerový pilot (nikoli aktuální produkční akceptace) vykázal:

- 36/36 zpracovaných tickerů,
- 633 SEC dokumentů,
- 2 214 SEC finančních faktů,
- 35 textových filingů,
- přibližně 30 článků na ticker,
- opakované `low source diversity`,
- 0/200 OOS vzorků,
- 0/12 uzavřených týdnů,
- produkt je trvale pouze analytický; automatické obchodování bylo odstraněno.

`Pipeline: SUCCESS` a `QualityGate: PASS` znamenají technicky dokončené zpracování a bezpečné uložení. Neznamenají prokázanou predikční přesnost.

Současný problém není jen malý počet zdrojů. Specifikace targetu a snapshot legacy baseline již existují ve foundation vrstvě, ale zbývá:

- úplná point-in-time feature historie,
- korektní implementace společného kalendáře predikčního cíle (AUD-006),
- výsledkově ověřený zmrazený baseline (AUD-012),
- walk-forward backtest,
- ablation test každé nové vrstvy,
- kalibrace pravděpodobností,
- historická evidence toho, co bylo známo v okamžiku predikce.

## 3. Co je reálně možné

| Komponenta | Stav proveditelnosti | Reálné použití |
| --- | --- | --- |
| 687 tickerů | ANO | Dávky, cache, noční/týdenní běh |
| Denní ceny a objemy | ANO S OMEZENÍM | Yahoo jako praktický zdroj/fallback; druhý zdroj pro kontrolu |
| SEC filingy a XBRL | ANO | Primární fundamentální a událostní zdroj |
| Momentum a relativní síla | ANO | Samostatně měřitelný faktor |
| Fundamentální faktory | ANO | SEC historická data, point-in-time |
| Makro a sektor | ANO | FRED/ALFRED a tržní benchmarky |
| Aktuální internetové zprávy | ANO S OMEZENÍM | RSS, GDELT, IR a primární zdroje |
| Google jako runtime vyhledávač | NE | API má kvóty, vyžaduje klíč a končí 1. 1. 2027; není základem pipeline |
| Historie běžných článků zdarma | OMEZENĚ | Od nynějška archivovat; starší kompletní historii nelze garantovat |
| Analyst revisions | OMEZENĚ | Použít pouze s datem zveřejnění a historickou dostupností |
| Supply-chain signály z filingů | ANO OMEZENĚ | Pouze ověřené události a evidence |
| Kompletní mapa soukromých dodavatelů | NE GARANTOVANĚ | Výsledek musí umět `UNKNOWN` nebo `DATA_UNAVAILABLE` |
| Přesný dopad materiálů na marži | OMEZENĚ | Pouze při dostupném podílu nákladů, ceně a pass-through |
| Automatické obchodování | NIKDY | Exekuční cesta není součástí produktu; výstupy jsou pouze analytické. |

### Zdroje a jejich omezení

SEC je pro tento projekt nejdůležitější veřejný zdroj. Poskytuje EDGAR API, XBRL data i bulk datové sady; automatizovaný přístup musí respektovat fair-use limit a stahovat pouze potřebná data.

- [SEC EDGAR API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- [SEC Financial Statement Data Sets](https://www.sec.gov/data-research/sec-markets-data/financial-statement-data-sets)
- [SEC accessing EDGAR data](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)

Yahoo může zůstat praktickým zdrojem cen a rychlého přehledu, ale jeho open-source klient není oficiální Yahoo API a nemá být jediným zdrojem kritické hodnoty.

- [yfinance dokumentace a omezení](https://ranaroussi.github.io/yfinance/)

Google použijeme pro výzkum metod a ruční dohledání zdrojů. Nebude hlavním automatickým ingestem. Oficiální dokumentace Google uvádí kvóty, nutnost API klíče a ukončení Custom Search JSON API 1. 1. 2027.

- [Google Custom Search JSON API](https://developers.google.com/custom-search/v1/overview)

GDELT je vhodný pro průběžné sledování zpráv, nikoli jako garantovaný archiv kompletní historické news vrstvy.

- [GDELT DOC 2.0 API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/)

Makrodata budeme získávat přes FRED a pro historické testy používat vintage hodnoty z ALFRED, aby backtest nepoužíval pozdější revize.

- [FRED API](https://fred.stlouisfed.org/docs/api/fred/)
- [FRED vintage data](https://fred.stlouisfed.org/docs/api/fred/series_vintagedates.html)

## 4. Nová architektura

```text
687 tickerů
    ↓
point-in-time ceny, SEC, události, makro a sektor
    ↓
feature store se snapshotem ke dni predikce
    ↓
baseline + ověřené modely
    ↓
walk-forward predikce a ranking
    ↓
risk overlay / NO_TRADE
    ↓
dashboard, historie a OOS vyhodnocení
```

Každá datová vrstva musí mít:

- `published_at`,
- `observed_at`,
- zdroj a URL,
- stabilní ID,
- hash dokumentu nebo odpovědi,
- stav dostupnosti,
- confidence,
- informaci o stáří,
- informaci, zda byla data použitelná v okamžiku predikce.

Chybějící údaj nesmí být nahrazen nulou, průměrem nebo domyšlenou hodnotou.

## 5. Kanonické úkoly

### PRED-001 – Definice predikčního cíle — PARTIAL

Audit 2026-09-11: specifikace je implementovaná, ale akceptaci výpočtu stejného targetu blokuje AUD-006. Původní DONE neznamenalo správnost společného kalendáře.

Definitivně stanovit, co se predikuje.

První verze:

- horizont: 5 obchodních dnů,
- cíl: nadvýnos akcie proti sektoru nebo benchmarku,
- neutrální pásmo zohlední poplatky a běžný šum,
- výstupem bude pravděpodobnost a pořadí, ne pouze UP/DOWN.

Akceptace:

- stejný target lze zpětně spočítat pro každý ticker,
- target nepoužívá informace z budoucnosti,
- pravidlo je verzované,
- existuje negativní test proti look-ahead.

### DATA-001 – Point-in-time feature store — PARTIAL

Implementováno v foundation vrstvě:

- každý běh vytváří neměnný snapshot pro každý zpracovaný ticker,
- snapshot obsahuje feature payload, přesný baseline output, benchmark, čas a provenance,
- label je při vytvoření vždy `PENDING` a budoucí ceny se do něj nezapisují,
- SQLite má idempotentní write-once úložiště `prediction_snapshots`.

Implementován je samostatný `PredictionLabelService`: zralé snapshoty uzavírá pouze z časově seřazených budoucích close cen, neúplný horizont ponechá jako `PENDING` a výpadek zdroje nepřevádí na nulový výnos. Týdenní runner jej nyní podporuje explicitním přepínačem `--resolve-labels`; běžný běh tím není zatížen dalšími Yahoo požadavky. Zbývá udělat bezpečný backfill, dávkování pro celý 687tickerový universe a ověřit reprodukci nad delší historií.

Vytvořit historické tabulky, ve kterých bude uloženo:

- ticker,
- čas snapshotu,
- dostupné ceny a objemy,
- SEC fakta,
- filing date a period end,
- makrodata v tehdy známé podobě,
- eventy,
- feature values,
- source provenance,
- missingness.

Akceptace:

- snapshot lze znovu reprodukovat,
- později opravený údaj nezmění starou predikci,
- každý feature má čas a zdroj,
- běh je idempotentní.

### BASE-001 – Zmrazení současného modelu — PARTIAL

Audit 2026-09-11: ukládání baseline existuje; výsledky po pěti dnech a plná provozní akceptace zatím nejsou doložené. Uzavření navazuje na AUD-007, AUD-012 a AUD-014.

Foundation ukládá současný `v2.1_guarded_consensus` jako `legacy_v2.1_heuristic` baseline v každém snapshotu. V této etapě se baseline nepřepisuje novou vrstvou ani se z něj neprovádí žádná exekuce.

Současný Yahoo/technický/news/risk výpočet se uloží jako baseline.

Akceptace:

- baseline se nemění při přidání nových vrstev,
- pro každý ticker je uložen vstup, výstup a datum,
- baseline lze spustit nad celým 687tickerovým universe,
- známe výsledky baseline po 5 dnech.

### MKT-001 – Tržní a technická vrstva — PARTIAL

Zachovat současný základ, ale doplnit:

- výnos 1/5/20/60/120/252 dnů,
- relativní sílu proti SPY/QQQ,
- relativní sílu proti sektoru,
- volume confirmation,
- drawdown,
- ATR a realizovanou volatilitu,
- trendový a mean-reversion režim,
- benchmarkové a sektorové zpoždění.

Akceptace:

- každý feature je point-in-time,
- Yahoo chyba neshodí celý běh,
- je vidět stáří a zdroj ceny,
- proběhne samostatný ablation test.

### FUND-001 – SEC fundamentální faktory — PARTIAL

Přestat používat Yahoo jako jediný fundamentální zdroj.

Doplnit:

- růst tržeb,
- růst EPS,
- vývoj marží,
- free cash flow,
- ROA/ROIC,
- zadlužení,
- úrokové krytí,
- změnu počtu akcií,
- working capital,
- cash-flow kvalitu,
- profitabilitu a investiční intenzitu,
- sektorové srovnání.

Akceptace:

- primárním zdrojem je SEC,
- každá hodnota má filed date,
- jsou oddělené annual a quarterly hodnoty,
- custom XBRL tagy se nezahodí bez evidence,
- žádný budoucí filing se nepoužije ve starém snapshotu.

Výzkumný základ: [Fama–French five-factor model](https://www.sciencedirect.com/science/article/pii/S0304405X14002323) a [Piotroskiho historická finanční analýza](https://www.jstor.org/stable/2672906). Tyto modely jsou zdrojem kandidátních faktorů, nikoli automatickým obchodním pravidlem.

### EVENT-001 – SEC a firemní události — PARTIAL

Z filingů a investor-relations zdrojů vytvořit strukturované eventy:

- earnings surprise,
- guidance,
- nový nebo ztracený kontrakt,
- emise akcií,
- buyback,
- insider transakce,
- akvizice,
- žaloba,
- regulace,
- odstávka,
- výrobní problém,
- potvrzený supply-chain problém.

Každý event má typ, závažnost, první zveřejnění, zdroj, časový horizont a odhadovaný směr dopadu.

### NEWS-001 – News ingest, deduplikace a klasifikace — PARTIAL

Současný počet článků není cílový ukazatel.

Nový postup:

1. načíst primární zdroje, IR, SEC a RSS,
2. GDELT použít jako doplněk,
3. spojit duplicity jedné události,
4. určit původní čas zveřejnění,
5. oddělit skutečnou událost od komentáře,
6. klasifikovat událost a závažnost,
7. aplikovat časový útlum,
8. uložit důkaz a source tier.

Obecný sentiment nesmí být jedinou metodou. Finanční text vyžaduje oborový slovník a kontext, jak ukazuje [Loughran–McDonald](https://sraf.nd.edu/loughranmcdonald-master-dictionary/). Výzkum [Tetlock](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.2007.01232.x) podporuje testování informační hodnoty tónu zpráv, nikoli bezpodmínečné používání sentimentu jako signálu.

Akceptace:

- žádná duplicita významně nezvýší důvěru,
- článek po rozhodném čase nemění starou predikci,
- každá událost má zdroj a čas,
- historická běžná news data jsou označena podle dostupnosti archivu,
- odteď se vytváří vlastní forward archive.

### MACRO-001 – Makro a sektorový režim — TODO

Doplnit pouze měřitelné proměnné:

- trend hlavních indexů,
- VIX,
- sazby,
- výnosová křivka,
- dolar,
- ropa,
- inflace,
- průmyslová produkce,
- sektorová relativní síla.

Akceptace:

- makrohodnota je dostupná v okamžiku predikce,
- historický test používá vintage data,
- každý faktor má samostatný test přínosu,
- makro nepřepíše firemní signál bez evidence.

### MODEL-001 – Model a ranking — TODO

Začít jednoduše:

1. současné ruční skóre jako baseline,
2. logistická regrese jako kontrolní model,
3. gradient boosting pro nelineární kombinace.

Nezačínat hlubokou neuronovou sítí. Nejdříve musí být prokázáno, že kvalitní feature set funguje mimo trénovací období.

Výzkumný základ: [Gu, Kelly a Xiu – Empirical Asset Pricing via Machine Learning](https://www.nber.org/papers/w25398).

Výstup:

- pravděpodobnost,
- ranking,
- kalibrace,
- uncertainty,
- vysvětlení hlavních faktorů,
- datová kvalita.

### EVAL-001 – Walk-forward, ablation a OOS — BLOCKED

Nahradí současné úzké pojetí `EVAL-703`.

Musí porovnat:

```text
baseline
baseline + technika
baseline + SEC fundamenty
baseline + eventy
baseline + news
baseline + makro/sektor
kombinovaný model
```

Požadované metriky:

- precision BUY candidate,
- hit rate proti jednoduchému benchmarku,
- ranking IC,
- excess return horního decilu,
- Brier score,
- kalibrace,
- false-positive rate,
- drawdown,
- turnover,
- výsledek po nákladech,
- výsledky podle sektoru a tržního režimu.

Minimální technická brána zůstává:

- 200 uzavřených vzorků,
- 12 různých týdnů,
- 3 průchody,
- lift alespoň 2 procentní body,
- dolní 95% mez liftu nad nulou,
- kladný přínos nejméně v 60 % týdnů.

To však není konečný důkaz. 200 akcií ve stejném týdnu není 200 nezávislých pozorování. Pro hodnocení dlouhodobé analytické spolehlivosti se požaduje alespoň 26 týdnů shadow provozu, ideálně 52 týdnů, a několik různých tržních režimů.

Backtest nesmí vybírat pouze dnešní vítězné akcie bez označení survivorship bias. Výběr historického universe se musí verzovat.

Riziko backtest overfittingu je popsáno v práci [Bailey a kol. – Deflated Sharpe Ratio](https://papers.ssrn.com/).

### SCALE-001 – Skutečný běh všech 687 tickerů — PARTIAL

Audit 2026-09-11: produkční watchlist má 687 tickerů. Odstranění limitu ve Windows launcheru je pouze v nesloučeném PR #87, zatímco main stále obsahuje limit 36. Týdenní analytický krok GitHub workflow má limit 36 i v PR větvi. Sjednocení řeší AUD-002; skutečnou odolnost a úplnost reportu ověří AUD-014.

Implementované části na pracovní větvi / historická pozorování (neznamenají nasazení do main ani dokončení SCALE-001):

- launcher bez `--ticker-limit`,
- regresní test hlídá, že se pilotní limit nevrátí,
- dokumentace rozlišuje vědomý pilot od výchozího produkčního běhu.
- lokální audit potvrdil skutečné dokončení `687/687`, ale ne analytickou připravenost: stará větev bez MT5 neposkytovala OHLC technické vrstvě a výpadek textu jednoho filingu chybně shodil celý běh,
- v aktuální opravě je dávkový Yahoo OHLC fallback po 50 symbolech; bezúspěšné dávky se evidují jako chybějící a nikdy se nenahrazují nulou ani vymyšlenou cenou,
- volitelné textové filingy se nově vykazují jako degradace `PARTIAL` s detailní diagnostikou; QualityGate REJECT a chyby integrity zůstávají blokující.

Zbývající požadavky:

- provozní ověření, že Yahoo bulk skutečně načte dostatečné OHLC pokrytí pro všech 687,
- persistentní cache mezi běhy,
- retry/backoff a circuit breaker při rate limitu,
- žádné opakované stahování stejného dokumentu,
- možnost pokračovat po výpadku,
- přesný seznam SUCCESS/PARTIAL/FAILED,
- žádný tichý fallback na 36 tickerů,
- agregovaný výstup všech 687.

Úspěch znamená `687 attempted`, nikoli pouze `36 processed`.

### OPS-805 – Source health a provozní audit — PARTIAL

Pro každý požadavek uložit:

- zdroj,
- URL,
- ticker,
- čas,
- počet pokusů,
- HTTP status,
- timeout,
- typ chyby,
- velikost odpovědi,
- hash,
- stáří dat,
- parser status,
- důvod vynechání.

Rozlišovat:

- server nedostupný,
- rate limit,
- timeout,
- prázdná odpověď,
- parser selhal,
- dokument neexistuje,
- firma údaj nezveřejňuje,
- zdroj není nakonfigurován.

QualityGate nesmí označit běh jako použitelný, pokud chybějící zdroj ovlivnil výsledek a není to ve výstupu vidět.


Doplněno v runneru pracovní větve PR #87 (úplnost pravidel a integraci ještě ověří AUD-005):

- pro SEC textové filingy se ukládá ticker, form, accession, URL, čas, typ chyby a zpráva,
- pro dávkové Yahoo OHLC se ukládá počet pokusů, načtených a neúspěšných symbolů i detail ticker/error pro každý neúspěch; pokud se bulk vůbec nepoužil, stav je `NOT_USED`,
- QualityGate exportuje konkrétní ticker, gate, rozhodnutí, kódy rejectů a warnings,
- globální stav rozlišuje `SUCCESS`, `PARTIAL` a `FAILED`; `PARTIAL` nesmí skrýt, že je výsledek pro některé vrstvy omezený.


### UI-806 – Detailní výsledek pro všech 687 — PARTIAL

`OPS-804` zůstává dokončený jako načtení posledního shadow JSONu. Nový úkol doplní:

- počet analyzovaných tickerů z 687,
- datové pokrytí každého tickeru,
- zdroje a jejich stav,
- feature snapshot,
- hlavní eventy,
- důkazy,
- důvod `NO_TRADE`,
- porovnání baseline versus nový model,
- export do Excelu/JSON.

### SUPPLY-401 až SUPPLY-403 – Dodavatelské a zákaznické vztahy — DEFERRED / SECONDARY

Tyto úkoly nejsou zrušené, ale nejsou první cestou ke zlepšení pětidenní predikce.

Povoleno:

- zachytit potvrzenou významnou událost,
- uložit přesnou větu a dokument,
- rozlišit známého a anonymního partnera,
- přidat riziko pouze při dostatečném důkazu.

Zakázáno:

- vydávat `Unnamed supplier` za identifikovanou firmu,
- tvrdit kompletní graf řetězce,
- zvyšovat skóre pouze kvůli nalezenému klíčovému slovu,
- používat nízkodůvěryhodnou expozici jako hlavní predikční faktor.

### RESOURCE-501 až RESOURCE-503 – Materiály a energie — DEFERRED / SECONDARY

Pozdější vrstva může použít:

- energie a komoditní ceny,
- sektorové nákladové koše,
- zveřejněný podíl nákladů,
- hedging,
- pass-through,
- citlivost marže.

Bez podílu nákladů, časové řady a vazby na firmu musí být výstup pouze `PARTIAL` nebo `INSUFFICIENT_DATA`.

### PRIVATE-001 – Finanční zdraví soukromých protistran — DEFERRED

Pouze pokud je partner přesně identifikován, hledají se:

- veřejné výkazy,
- bankrot,
- zástavy,
- soudy,
- sankce,
- mateřská společnost,
- veřejné zakázky.

Není dovoleno předstírat, že každý americký soukromý dodavatel má veřejně dostupnou kompletní účetní závěrku.

## 6. Co je hotové a co se mění

### Zachovat jako hotové

- `CI-001` až `CI-010` – bezpečný orchestration, persistence, provenance, shadow-only a základní QualityGate.
- `ENTITY-101` – identity kontrakt a fail-closed základ.
- `FILING-101`, `FILING-103` – základní filing a source-resolution vrstva.
- `GOV-101` – governance observations.
- `OPS-801` – týdenní shadow runner a obnova historie.
- `OPS-804` – načtení posledního weekly shadow výstupu ve Streamlitu.

### Přeznačit jako částečné

- `FILING-102` – evropská větev není prioritou pro současný americký universe.
- `FORENSIC-201/202` – základ funguje, ale chybí plné delty a kalibrace.
- `SHORT-301` až `SHORT-305` – doplňková vrstva, ne hlavní predikce.
- `DECISION-701/702` – bezpečnostní overlay je použitelný, predikční přínos není prokázaný.
- `SUPPLY-401` – evidence existuje, ale není to kompletní graph.

### Odložit mimo kritickou cestu

- `SUPPLY-402`, `SUPPLY-403`,
- `RESOURCE-501`, `RESOURCE-502`, `RESOURCE-503`,
- kompletní private-company enrichment,
- evropské registry, pokud se později nerozšíří universe mimo USA.

### Zachovat jako blokující

- `EVAL-703` / nový `EVAL-001`,
- ochrana `main`,
- skutečný 687tickerový běh,
- source-health logování.

## 7. Správné pořadí realizace

### Fáze A – specifikace a baseline

1. `PRED-001` – definovat pětidenní nadvýnos.
2. `DATA-001` – vytvořit point-in-time snapshot.
3. `BASE-001` – zmrazit současný model.
4. `OPS-805` – doplnit diagnostiku zdrojů.

### Fáze B – měřitelná predikce

5. `MKT-001` – dokončit tržní a relativní features.
6. `FUND-001` – přidat SEC fundamentální delty.
7. `MODEL-001` – vytvořit jednoduché kontrolní modely.
8. `EVAL-001` – spustit walk-forward a ablation.

### Fáze C – události a zprávy

9. `EVENT-001` – vytvořit strukturované SEC/IR eventy.
10. `NEWS-001` – deduplikovat a klasifikovat zprávy.
11. `MACRO-001` – přidat makro a sektor.

### Fáze D – škála

12. `SCALE-001` – odblokovat skutečný běh všech 687.
13. `UI-806` – zobrazit pokrytí, důkazy a ranking všech tickerů.
14. Udržovat týdenní shadow historii.

### Fáze E – sekundární rizika

15. Supply-chain události.
16. Materiály a energie.
17. Private-company evidence.
18. Regulace a další externí zdroje.

Tyto vrstvy se nesmějí zapojit do rozhodovacího skóre jen proto, že existuje jejich třída nebo databázová tabulka. Musí projít vlastním ablation testem.

## 8. Pravidla proti falešnému zlepšování

- Více článků neznamená lepší model.
- Více indikátorů neznamená více informací.
- Jeden event kopírovaný v deseti médiích je stále jeden event.
- `PASS` technického běhu není `PASS` predikční přesnosti.
- Confidence musí být kalibrovaná na skutečné výsledky.
- Každá nová datová vrstva musí být porovnána proti baseline.
- Nesmí se používat data publikovaná až po okamžiku rozhodnutí.
- Běžný Google search nesmí být základem historického backtestu.
- Short report, anonymní dodavatel ani obecná zmínka o materiálu nesmí sama vytvořit BUY nebo SELL.
- automatická exekuce neexistuje; OOS validace slouží pouze k měření analytické kvality.

## 9. Definition of Done

Nová vrstva je dokončená pouze tehdy, když:

- zpracuje celý zamýšlený rozsah,
- má stabilní identitu a verzi,
- ukládá source, čas a důkaz,
- umí přiznat chybějící data,
- prochází point-in-time testem,
- prochází negativním look-ahead testem,
- má deterministický unit test,
- má persistence test,
- má QualityGate negativní test,
- má samostatný ablation výsledek,
- nezmění produkční analytickou predikci bez explicitního shadow přepínače,
- je vidět ve výstupu i v logu.

## 10. Konečný verdikt

Projekt je reálný, pokud bude jeho hlavním produktem:

> týdenní ranking 687 amerických akcií založený na point-in-time cenách, SEC fundamentálních datech, strukturovaných událostech, makru a sektoru, s měřitelnou OOS validací.

Projekt není reálný v této podobě:

> automaticky prohledat Google, získat kompletní historii všech článků, přesně znát každý soukromý dodavatelský řetězec a z toho bez dlouhého testu generovat spolehlivé BUY/SELL.

36tickerový pilot zůstává technický nástroj pro rychlé ověření. Produkční cíl je a zůstává 687 tickerů.


## 11. Implementační úkoly z auditu 2026-09-11

Tato sekce je aktuální realizační fronta a do jejího uzavření má přednost před obecným pořadím v §7. Původní ID zůstávají nadřazenými oblastmi; AUD úkoly jsou konkrétní opravy jejich nedostatků, nikoli druhá nezávislá roadmapa.

### Rozsah a ověřený stav

- Trvale pouze analytický produkt pro **687 amerických tickerů**. Automatické obchodování ani budoucí „aktivace“ exekuce se neplánují.
- Audit porovnal `main@8adc899744c97a7b4f9eb06de0f7876bc5421ea0` a pracovní větev `codex/rework-prediction-roadmap-20260901@c55060348ad6608f0b9abba8bcb3058d3cbe1f58`. [PR #87](https://github.com/littleleg198602/JOHNY-SKORE/pull/87) byl při zápisu otevřený a nesloučený.
- [Živý běh 7. 9. 2026](https://github.com/littleleg198602/JOHNY-SKORE/actions/runs/34121882908) skončil na identity kontrole AMAT; týdenní analýza byla přeskočena. Dílčí Yahoo, RSS a SEC smoke kontroly prošly. Není to důkaz dostupnosti všech dat pro 687 firem ani důkaz současného výpadku SEC serveru.
- [Zelené CI](https://github.com/littleleg198602/JOHNY-SKORE/actions/runs/33639298724) dokládá testovanou sadu, ne skutečnou dostupnost zdrojů a přesnost modelu.
- Audit zahrnoval čtení kódu/logu a dva izolované diagnostické testy (news skóre a kalendář labelů). Nebyl to nový úplný test suite ani nový živý 687tickerový běh.
- **Zápis úkolu ≠ implementace ≠ sloučení do main ≠ provozní ověření ≠ prokázaný predikční přínos.** Tento commit mění pouze dokumentaci.

### Priority a pravidla uzavírání

- **P0:** odstranit provozní blokátory a chyby vstupů/targetu, než se bude hodnotit přesnost.
- **P1:** dokončit informační kvalitu, pravidelnou evaluaci, report a provozní důkazy.
- **P2:** sekundární řetězce a materiály; nejsou zrušené, ale nesmějí zdržet základní měřitelný produkt.
- Každý úkol je při zápisu otevřený. Závislosti omezují zahájení příslušné části nebo její akceptaci.
- Při uzavření připojit implementační commit/PR, názvy a výsledky testů a případný live artefakt. Pouhé založení třídy, tabulky nebo zelené CI nestačí.
- Realizace: nejprve AUD-001/002 a souběžně AUD-003 až AUD-006; potom AUD-007 až AUD-013 podle závislostí. AUD-014 připravovat průběžně, finální ověření až po opravách. AUD-015 je sekundární.

### AUD-001 — Opravit kosmetický konflikt identity AMAT

- **Priorita / stav:** P0 / TODO
- **Nadřazené úkoly:** ENTITY-101, OPS-805
- **Závislosti:** Bez závislosti
- **Zjištění:** Live kontrola 7. 9. zastavila běh na rozdílu APPLIED MATERIALS INC /DE/ versus APPLIED MATERIALS INC /DE, přestože předchozí kontrola CIK prošla.
- **Řešení:** Upřednostnit ověřený identifikátor; pouze při jeho shodě připustit úzce definovanou normalizaci kosmetických znaků nebo auditovaný alias. Nepoužívat fuzzy přiřazování podle názvu.
- **Akceptace:** Regresní případ AMAT projde; odlišný CIK zůstane blokovaný; skutečná změna názvu vyžaduje dohledatelnou evidenci. Opakovaný identity smoke uloží konkrétní výsledek.

### AUD-002 — Sjednotit produkční universe a verzi spouštěčů

- **Priorita / stav:** P0 / PARTIAL
- **Implementace 2026-09-14:** PR #87 odstraňuje limit z Windows launcheru i z týdenního GitHub analytického kroku. Malý identity/source smoke nadále vědomě používá limit 3; není to produkční analýza. Zbývá uložit hash seznamu a počty requested/attempted/usable/partial/failed do reportu a provést provozní 687tickerové ověření (AUD-014).
- **Nadřazené úkoly:** SCALE-001
- **Závislosti:** Bez závislosti
- **Zjištění:** Windows launcher v main má limit 36. V PR #87 je odstraněný, ale týdenní krok GitHub workflow má stále --ticker-limit 36.
- **Řešení:** Windows i týdenní GitHub analýza musí číst stejný production_watchlist.txt bez skrytého limitu. Pilot pouze explicitní volba; malý source smoke je samostatná kontrola. Zapisovat commit, verzi konfigurace a hash seznamu.
- **Akceptace:** Test obou produkčních vstupů ověří přesnou množinu 687 tickerů, nikoli jen počet. Report rozliší requested, attempted, usable, partial a failed. Nasazení do main bude samostatně schválené, nikoli předpokládané.

### AUD-003 — Zavést persistentní OHLC cache a obnovu sběru

- **Priorita / stav:** P0 / TODO
- **Nadřazené úkoly:** SCALE-001, MKT-001
- **Závislosti:** Bez závislosti
- **Zjištění:** Dávka 50 symbolů v yfinance není jeden serverový požadavek; klient interně zpracovává symboly. Nová bulk cesta nemá persistentní OHLC cache a interně zachycená chyba může vrátit prázdný rámec bez vnějšího retry.
- **Řešení:** Ukládat OHLC podle symbolu, poskytovatele, seance a verze; aktualizovat inkrementálně, opakovat jen chybějící symboly. Doplnit omezené retry/backoff, respektování Retry-After, circuit breaker a checkpoint/resume. Existující metadata cache zachovat.
- **Akceptace:** Testy 429, timeout, prázdný rámec, částečný výpadek a restart uprostřed dávky. Druhý běh použije cache a nestahuje kompletní historii znovu; počty pokusů a chyb jsou doloženy.

### AUD-004 — Opravit výběr ceny a kontrolu použitelnosti OHLC

- **Priorita / stav:** P0 / TODO
- **Nadřazené úkoly:** MKT-001, DATA-001
- **Závislosti:** Bez závislosti; cache využije AUD-003
- **Zjištění:** Pipeline může dát přednost starému Yahoo currentPrice z metadata cache před čerstvým Yahoo OHLC. Samotná přítomnost sloupce Close nestačí jako validace.
- **Řešení:** Volit cenu podle účelu a časového razítka, odlišit kotaci od uzavírací ceny. Ověřit numerickou kladnou cenu, délku historie a poslední očekávanou uzavřenou seanci. Chybějící ceny nezaměnit za neutrální připravený signál.
- **Akceptace:** Čerstvé OHLC není přepsáno starými metadaty. Test all-NaN, nečíselných cen, víkendu, svátku a krátké historie. UI, indikátory a vyhodnocení používají doložené konzistentní zdroje a časy.

### AUD-005 — Dokončit diagnostiku zdrojů a pravidla degradace

- **Priorita / stav:** P0 / PARTIAL
- **Implementováno 2026-09-14:** retry pro dočasné chyby při bezpečném stahování textu filingů a strukturovaný audit důvodu pro SEC bundle i text filingů (`source`, ticker, URL, čas, HTTP stav, pokusy, parser, náprava). Úplně sdílený transport/circuit breaker mezi všemi zdroji zůstává otevřený.
- **Nadřazené úkoly:** OPS-805, FILING-101, SCALE-001
- **Závislosti:** Návaznost AUD-003 a AUD-004
- **Zjištění:** SEC JSON má retry a limiter, textová cesta přes ShortReportClient.fetch nemá vlastní opakování se sdíleným limitem. Souhrnné počty Yahoo chyb samy nezaručují správný globální stav.
- **Řešení:** Sjednotit bezpečný HTTP transport pro SEC texty a JSON, cache a omezené opakování. Ukládat ticker, URL, čas, HTTP/transport chybu, pokusy, parser a stáří. Odlišit 403, 429, timeout, nepublikováno, nenakonfigurováno a neověřeno. Integrita blokuje, volitelný enrichment degraduje konkrétní vrstvu.
- **Akceptace:** Simulovaný výpadek všech cen nesmí dát použitelný ranking; jeden volitelný textový filing neshodí nezávislé platné výsledky. Každý REJECT/PARTIAL má konkrétní důvod a cestu k nápravě. Ze screenshotu samotného se neurčuje neznámá příčina SEC chyby.

### AUD-006 — Opravit společný pětidenní kalendář labelů

- **Priorita / stav:** P0 / IMPLEMENTED_PENDING_MERGE
- **Nadřazené úkoly:** PRED-001, DATA-001, EVAL-001
- **Závislosti:** Bez závislosti
- **Zjištění:** Izolovaný test PredictionLabelService s chybějící seancí skončil pro akcii 9. 9. 2026 a benchmark 8. 9. 2026: pět řádků v každé řadě není vždy stejných pět seancí.
- **Řešení:** Stanovit společné t0 a t+5 dle burzovního kalendáře, jednotnou metodiku splitů/dividend a dostupnost uzavřených cen k evaluačnímu času. Chybějící seanci nepřeskakovat. Verzovat target i metodiku labelu; staré predikce neměnit.
- **Akceptace:** Akcie a benchmark mají vždy totožná endpoint data. Test chybějící seance, svátku, splitu, nezralého horizontu a budoucích dat. Neúplný label zůstává PENDING s důvodem; opravy jsou verzované a auditovatelné.

### AUD-007 — Zapojit pravidelné uzavírání a vyhodnocování snapshotů

- **Priorita / stav:** P1 / TODO
- **Nadřazené úkoly:** DATA-001, EVAL-001
- **Závislosti:** Blokováno AUD-006; pro škálu AUD-003
- **Zjištění:** --resolve-labels je opt-in a standardní launcher/workflow ho nepoužívá. Starší Stage4 vyhodnocení historie není totožné s novým pětidenním benchmark-relative targetem.
- **Řešení:** Přidat samostatný plánovaný či ručně spustitelný analytický krok pro zralé snapshoty, s dávkováním, cache, checkpointem a idempotencí. Jasně oddělit legacy evaluaci od nové target verze.
- **Akceptace:** Dva opakované běhy nevytvoří duplicitní labely; nezralé zůstanou PENDING, zralé se vyhodnotí pro celý dostupný universe. Report ukáže resolved/pending/failed a metriky jen kompatibilní target verze.

### AUD-008 — Oddělit směr zprávy od množství a důvěry

- **Priorita / stav:** P1 / IMPLEMENTED_PENDING_MERGE
- **Nadřazené úkoly:** NEWS-001, MODEL-001
- **Závislosti:** Před změnou zmrazit a verzovat dosavadní baseline
- **Zjištění:** Izolovaný test neutrálních čerstvých titulků: sentiment 0 v obou případech, ale news score vzrostlo z 43,25 u jednoho na 61,50 u třiceti. Objem a čerstvost tak ovlivňují směrové skóre.
- **Řešení:** Samostatně ukládat směr, závažnost události, pokrytí a interní důvěru. Neutrální články nesmějí mechanicky vytvářet býčí signál; žádné zprávy znamenají missingness, ne negativní sentiment.
- **Akceptace:** Test 1 versus 30 neutrálních zpráv nemění směrový signál. Pokrýt pozitivní/negativní událost, negaci a chybějící news. Přínos nové verze hodnotit až ablation testem, nikoli počtem článků.

### AUD-009 — Evidovat původního vydavatele a deduplikovat události

- **Priorita / stav:** P1 / IMPLEMENTED_PENDING_MERGE
- **Nadřazené úkoly:** NEWS-001, EVENT-001
- **Závislosti:** Bez závislosti; návaznost AUD-008
- **Zjištění:** RSS source je URL feedu, nikoli nutně původní vydavatel. Současná deduplikace shodného normalizovaného titulku neodstraní přepsané zprávy o stejné události.
- **Řešení:** Oddělit feed_url, publisher, publisher_domain, original_url, event_id, published_at a observed_at. Archivovat point-in-time, ověřovat relevanci k firmě a seskupovat kopie; nezávislé potvrzení odlišit od syndikace.
- **Akceptace:** Deset přepisů jedné zprávy nezvýší směrový signál ani důvěru jako deset nezávislých událostí. Test neznámého vydavatele, krátkého tickeru, nezávislého potvrzení a článku po cutoffu; provenance zůstane dohledatelná.

### AUD-010 — Neprezentovat heuristickou důvěru jako kalibrovanou pravděpodobnost

- **Priorita / stav:** P1 / IMPLEMENTED_PENDING_MERGE
- **Nadřazené úkoly:** MODEL-001, UI-806, EVAL-001
- **Závislosti:** Označení bez závislosti; kalibrace po AUD-007 a AUD-012
- **Zjištění:** DecisionAgent._probabilities převádí confidence vzorcem; například 0,6 na dominantní hodnotu přibližně 0,7333. To není změřená četnost úspěchu.
- **Řešení:** Označit současný údaj jako interní heuristickou důvěru a calibrated=false. Rozlišit absolutní UP od nadvýkonnosti benchmarku. Pravděpodobnost zveřejnit až po časově korektní kalibraci a validaci; zachovat kompatibilitu historických záznamů.
- **Akceptace:** UI/export nepíše 73 % šance na úspěch bez kalibrační evidence. Kalibrace se učí jen na minulých oknech; uložit Brier score, reliability bins, target, období a verzi.

### AUD-011 — Dokončit tržní a SEC point-in-time faktory

- **Priorita / stav:** P1 / TODO
- **Nadřazené úkoly:** MKT-001, FUND-001, DATA-001
- **Závislosti:** AUD-004; následné ověření AUD-012
- **Zjištění:** Foundation snapshoty a SEC fakta existují, ale nejde ještě o úplný historický feature store se srovnatelnými fundamentálními deltami.
- **Řešení:** Doplnit relativní sílu, výnosy, volatilitu a sektorové benchmarky; SEC růst tržeb, marží, FCF a zadlužení se správnými obdobími a filing/availability daty. Každý feature musí mít zdroj, verzi a missingness.
- **Akceptace:** Reprodukce snapshotu používá jen tehdy dostupná data; oddělí annual/quarterly, revize, jednotky a odlišné účetní kalendáře. Chybějící fakt se nevydává za nulu. Každá skupina faktorů projde samostatným ablation.

### AUD-012 — Prokázat přínos modelu proti zmrazenému baseline

- **Priorita / stav:** P1 / TODO
- **Nadřazené úkoly:** BASE-001, MODEL-001, EVAL-001
- **Závislosti:** Blokováno AUD-006 a AUD-007; nové faktory postupně z AUD-008/009/011
- **Zjištění:** Současný systém je heuristický agregátor, ne prokázaný naučený predikční model. Existující snapshot baseline nedokládá splnění výsledkové akceptace po pěti dnech.
- **Řešení:** Uchovat verzi legacy baseline a přidat jednoduchý momentum baseline, následně logistický model. Použít walk-forward s nedotčeným testovacím obdobím, bez úniku překrývajících se labelů; boosting až po kontrole jednoduchých variant.
- **Akceptace:** Porovnat ranking IC, top-decile excess return, kalibraci a výsledky podle týdnů/sektorů. Uvést nejistotu, survivorship bias, korelaci vzorků a ablation; 687 akcií z jednoho týdne nejsou 687 nezávislých důkazů. Negativní výsledek se nezamlčí. Žádná automatická exekuce.

### AUD-013 — Zobrazit použitelný analytický report celého universe

- **Priorita / stav:** P1 / IMPLEMENTED_PENDING_MERGE
- **Nadřazené úkoly:** UI-806, OPS-805
- **Závislosti:** AUD-002, AUD-004, AUD-005; metriky postupně AUD-007/010/012
- **Zjištění:** Zobrazení posledního JSONu existuje, ale kompletní auditovatelnost 687 výsledků a oddělení signálu, interní důvěry a prokázaného výkonu nejsou dokončené.
- **Řešení:** Dashboard a export zobrazí všech 687 požadovaných tickerů, i neúspěšné s důvodem. Uvést verzi běhu, coverage, stáří cen, důkazy a odkazy, missingness, stav jednotlivých vrstev a poslední validní snapshot.
- **Akceptace:** Bez spuštění nové analýzy lze vysvětlit každý výsledek a každý vynechaný ticker. Neúplná data nejsou skryta jako neutrální zdravá firma; UI nikde nenabízí automatické obchodování. Modelová validace není nazývána aktivací obchodování.

### AUD-014 — Doložit provozní akceptaci reálné 687tickerové cesty

- **Priorita / stav:** P1 / TODO
- **Nadřazené úkoly:** SCALE-001, OPS-805
- **Závislosti:** Finální live ověření po AUD-001 až AUD-006 a AUD-013; testy připravovat souběžně
- **Zjištění:** Dosavadní scale test používá syntetické T0000… tickery, předplněnou metadata cache a fake MT5. Zelené CI není důkaz funkčního --no-mt5 sběru z prázdné cache ani kvality predikcí.
- **Řešení:** Přidat deterministickou integrační sadu pro skutečnou no-MT5 cestu, prázdnou cache a částečné výpadky. Po opravách provést zvlášť schválený živý full-universe běh a opakování s cache; uložit artefakty a časovou/nákladovou bilanci.
- **Akceptace:** Souhrn i per-ticker report doloží přesný universe, čas sběru, usable ceny/features, selhání a obnovu; nepožaduje vymyšlené 100% pokrytí. Unit, integrační, live a predikční validace mají oddělené výsledky. Samotné 687/687 ani CI PASS nestačí.

### AUD-015 — Vymezit důkazní úroveň řetězců, materiálů a protistran

- **Priorita / stav:** P2 / TODO
- **Nadřazené úkoly:** SUPPLY-401..403, RESOURCE-501..503, PRIVATE-001
- **Závislosti:** Sekundární; mimo kritickou cestu cen a evaluace
- **Zjištění:** Regex evidence typu Unnamed major customer / Unnamed critical supplier a zmínky o materiálech nejsou kompletní graf protistran ani jejich finanční zdraví.
- **Řešení:** Ukládat přesnou citaci, dokument, období, identifikovaného/anonymního partnera a zveřejněný podíl. Zdraví protistrany hodnotit jen při ověřené identitě a dostupných datovaných výkazech. Dopad materiálů kvantifikovat pouze se zveřejněnými nákladovými podíly, smluvní cenou, hedgingem a pass-through.
- **Akceptace:** Rozlišit NOT_DISCLOSED, ACCESS_BLOCKED, NOT_CONFIGURED a UNKNOWN; bez evidence nevytvářet zdraví firmy ani dopad do marže. Uvést konkrétní zdroj a důvod nedostupnosti, nikoli obecné 'nejde'. Do predikce až po samostatném testu přínosu.
