
# JOHNY-SKORE – predikční a datová roadmapa

Tento soubor je kanonický backlog pro analytický program JOHNY-SKORE. Určuje, co má systém skutečně dělat, jaká data smí použít a podle čeho poznáme, že nová vrstva pomohla.

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

Poslední pilotní běh prokázal:

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

Současný problém není jen malý počet zdrojů. Chybí:

- point-in-time feature historie,
- pevně definovaný predikční cíl,
- skutečný baseline,
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

### PRED-001 – Definice predikčního cíle — DONE

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

### BASE-001 – Zmrazení současného modelu — DONE

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

To však není konečný důkaz. 200 akcií ve stejném týdnu není 200 nezávislých pozorování. Pro návrh live aktivace se požaduje alespoň 26 týdnů shadow provozu, ideálně 52 týdnů, a několik různých tržních režimů.

Backtest nesmí vybírat pouze dnešní vítězné akcie bez označení survivorship bias. Výběr historického universe se musí verzovat.

Riziko backtest overfittingu je popsáno v práci [Bailey a kol. – Deflated Sharpe Ratio](https://papers.ssrn.com/).

### SCALE-001 – Skutečný běh všech 687 tickerů — PARTIAL

Workflow už zná produkční universe 687 tickerů. Původní Windows launcher měl skrytý limit 36 tickerů; ten je nyní odstraněn a standardní `Spustit_Tydenni_Shadow.bat` používá celý `production_watchlist.txt`. Samotný skutečný 687tickerový běh, jeho odolnost vůči výpadkům a úplnost výsledného reportu ale ještě musí být provozně ověřeny.

Hotovo v této části:

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


Aktuálně doplněno v runneru:

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
