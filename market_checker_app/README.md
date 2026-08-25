# Market Checker (interní analytika)

Lokální Streamlit aplikace pro analýzu watchlistu z Excelu, ručního vstupu nebo MT5 a kombinaci zdrojů signálu:
- RSS/news scoring
- Yahoo/yfinance snapshot
- technické indikátory (modul připraven, aktuálně základní score fallback)

Aktuální stav celé Company Intelligence / Forensic vrstvy a navazující úkoly
jsou vedené v [`COMPANY_INTELLIGENCE_TASKS.md`](../COMPANY_INTELLIGENCE_TASKS.md).

## Spuštění

```bash
cd market_checker_app
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -c constraints.txt
streamlit run app.py
```

Ve Windows aktivuj prostředí příkazem `.venv\Scripts\activate` nebo použij
`Spustit_Market_Checker.bat` v kořeni repozitáře.

## Spuštění dvojklikem (Windows)

V kořeni repozitáře je připraven soubor:
- `Spustit_Market_Checker.bat`

Stačí na něj dvakrát kliknout. Skript:
- najde Python (`.venv\Scripts\python.exe`, nebo `py -3`, nebo `python`)
- nainstaluje závislosti a při chybě zobrazí skutečný důvod
- spustí Streamlit aplikaci

## Vstupy a zdroje

- Excel musí obsahovat sloupec `Yahoo ticker`, `yahoo_ticker` nebo `ticker`.
- Ruční watchlist přijímá jeden ticker na řádek.
- RSS a MT5 se zapínají samostatnými volbami v levém panelu; nahrání Excelu je automaticky nevypíná.
- Yahoo cenová historie se v rámci malého běhu sdílí mezi performance a technickou analýzou.
- Pro velký watchlist se Yahoo metadata ukládají trvale do stejné SQLite DB. Tlačítko
  **Doplnit Yahoo cache** jedním kliknutím automaticky zpracuje navazující dávky zvolené
  velikosti. Zastaví se až po dokončení nebo při ochranném Yahoo rate limitu.
- Pokud Yahoo omezí požadavky, aplikace zobrazí výraznou chybu a označí fallback výsledky jako nespolehlivé.
- Výchozí tickerové zprávy používají experimentální Google News RSS bez registrace.
  Nefunkční Yahoo Finance RSS URL není ve výchozím seznamu. Položky bez data publikace
  ani položky s budoucím datem se nezapočítají jako čerstvé zprávy.

### Oficiální tickerový universe NEW ANALYZER

- Kanonický seznam obsahuje přesně **687 unikátních tickerů** z exportu
  `market_checker_20260818_213623.xlsx`.
- Reprodukovatelná kopie je v
  `market_checker_app/data/market_checker_687_tickers.csv`; validuje se přes
  `market_checker_app/utils/ticker_universe.py`.
- Streamlit UI jej použije automaticky, pokud není nahrán vlastní Excel a není
  zadán ruční watchlist. Vlastní Excel nebo ruční watchlist má přednost.
- Weekly shadow runner používá tento seznam jako výchozí zdroj, pokud nebyly
  zadány explicitní `--tickers`. SQLite historie je pouze kompatibilní fallback
  pro starší checkout bez kanonického CSV.

### Velký universe (např. 687 tickerů z MT5)

- Analyzují se všechny tickery, výchozí limit jednoho běhu je 1000 symbolů.
- RSS zdroje se načítají paralelně s timeoutem a MT5 OHLCV v jednom připojení k terminálu.
- UI průběžně ukazuje fáze `RSS`, `MT5 OHLC` a následně pořadí zpracovávaného tickeru.
- Nad 100 tickerů se Yahoo metadata nenačítají jednotlivě uvnitř analýzy. Analýza použije
  trvalou Yahoo cache; čerstvá a zastaralá data jsou ve výsledku viditelně označena. Chybějící
  metadata mají neutrální skóre a skutečných 0 % důvěry. Aktuální cena a změny se v tomto
  režimu odvozují z MT5.
- Pro 687 tickerů spusťte **Doplnit Yahoo cache** jednou. Aplikace automaticky pokračuje
  po dávkách (výchozí velikost 100), dokud nezpracuje všechny aktuálně dostupné tickery.
  Při rate limitu jsou hotová data zachována a po ochranné pauze se pokračuje pouze
  zbývajícími tickery.
- Když pro některý symbol MT5 nevrátí svíčky, tento řádek zůstane ve výsledku, ale technická
  část bude neutrální a ve sloupci `warnings` bude uveden důvod.

## Co aplikace dělá

- načte watchlist z MetaTrader5 (nebo ručně z textového pole)
- stáhne RSS články a přiřadí je tickerům
- pokud ticker nemá zprávu za 48h, použijí se zprávy až 3 měsíce zpět s časovým útlumem (novější mají větší váhu)
- RSS URL může obsahovat placeholder `{ticker}` (např. Yahoo feed), který se při běhu rozbalí pro každý symbol z watchlistu
- získá Yahoo snapshoty a performance data
- spočítá `NewsScore`, `TechScore`, `YahooScore`, `TotalScore`, `Signal`
- zobrazí výsledky v tabech: `Signals`, `Dashboard`, `Articles`, `Sources`, `Delta`, `Trends`, `History`
- umí export do Excelu

## SQLite historie (100% lokálně)

- historie je ukládána do lokálního souboru SQLite, default:
  - `outputs/market_checker_history.db`
- tabulka `runs`: metadata každého běhu
- tabulka `signal_history`: jeden ticker v jednom běhu
- DB se vytvoří automaticky při prvním běhu

## Agentní architektura – etapa 1

Pipeline v2.1 po výpočtu predikcí automaticky spustí auditní agentní vrstvu:

- `OrchestratorAgent` hlídá pořadí závislostí, stav a dobu běhu každého agenta,
- `EntityRegistryAgent` sjednocuje tickery a aliasy (např. `BRK.B` → Yahoo
  `BRK-B`), odděluje právní entitu, emitenta a obchodovaný instrument a
  fail-closed validuje CIK/ISIN/LEI,
- `PredictionV21AdapterAgent` převádí existující výstup v2.1 na jednotný kontrakt
  `evidence` + `agent_signal`.

Etapa 1 běží výchozí v režimu `shadow_mode`: nic nemění na `forecast` ani na
`BUY` / `SELL` / `NO_TRADE`. Připravuje dohledatelný základ pro agenty finančních
výkazů, dodavatelských vazeb, energií, materiálů a short reportů. Chování lze řídit
v `AppConfig` přes `agent_stage1_enabled` a `agent_shadow_mode`.

Do stejné SQLite databáze se aditivně vytvářejí tabulky:

- `orchestration_runs` a `agent_runs` pro audit průběhu,
- `entities` + `entity_observations` pro aktuální registr společností,
- `entity_identity_versions` pro point-in-time historii názvu, tickeru, burzy,
  parent vazby a identifikátorů bez použití budoucích změn,
- `entity_identity_conflicts` + observations pro fail-closed karanténu
  neshodných CIK/ISIN/LEI,
- `documents` + `document_observations` pro původ a opakované použití dokumentů,
- `governance_events` + `governance_event_observations` pro časově dohledatelné
  změny auditorů, insider transakce, restatementy a další governance události,
- `research_claims` + `research_claim_observations` pro verzovaný stav jednotlivých tvrzení,
- `evidence` pro zjištění se směrem, rizikem, důvěrou, vetem a vazbou na dokument,
- `agent_signals` pro normalizovaný výstup každého analytického agenta.

Uložení jednoho agentního reportu je atomické. Selhání shadow vrstvy se zaznamená
jako varování a nesmí změnit ani zahodit původní predikci v2.1.

## Fundamentální ingest – etapa 2 (MVP)

Etapa 2 přidává opt-in agenta `SecFundamentalsAgent` (`f2_sec`). Používá pouze
oficiální veřejná rozhraní SEC EDGAR:

- mapu ticker → CIK z `company_tickers_exchange.json`,
- `data.sec.gov/submissions` pro formuláře `10-K`, `10-Q`, `8-K`, `20-F`,
  `6-K`, `40-F`, `S-1`, prospekty 424B, Form 4 a `SC 13D/G`, včetně omezeného
  načtení starších submission souborů, když `recent` nestačí,
- `data.sec.gov/api/xbrl/companyfacts` pro vybraná účetní fakta (výnosy, zisk,
  aktiva, závazky, cash flow, dluh a EPS).

Živý SEC ingest je výchozí vypnutý. Zapíná se v levém panelu volbou
**Načíst SEC výkazy (Etapa 2)**. SEC vyžaduje deklarovaný User-Agent obsahující
název aplikace a kontaktní e-mail; lze jej zadat v UI nebo proměnnou prostředí:

```bash
JOHNY_SKORE_SEC_USER_AGENT="JohnySkore/2.1 kontakt@example.com"
```

Klient vynucuje bezpečný odstup požadavků pod oficiálním limitem SEC 10 req/s.
Omezený počet filingů vybírá vyváženě mezi povolenými formuláři, aby série 8-K
nebo 6-K nevytlačila srovnatelná účetní období potřebná pro navazující kontroly.
Dokumenty mají stabilní ID podle CIK a accession number, účetní fakta obsahové ID
a opakovaný běh je v SQLite pouze znovu pozoruje — neduplikuje zdrojové záznamy.
Nové tabulky jsou `fundamental_facts` a `fundamental_fact_observations`.

Tato část etapy je pouze ingest a audit. Fundamentální fakta zatím nemění score,
`forecast` ani `BUY` / `SELL` / `NO_TRADE`; neobsahuje sentiment, backtesting,
portfolio logiku ani dodavatelský graf.

## Identity, evropské filingy a governance – etapa 5.1

`EntityRegistryAgent` umí ověřit přesný LEI/ISIN proti veřejnému GLEIF API.
Nikdy nevybírá firmu podle fuzzy podobnosti názvu. Jednoznačné mapování může
doplnit chybějící LEI nebo jediný ISIN; neshoda se uloží do karantény, nepřepíše
aktivní identitu a QualityGate daný ticker odmítne.

`EuropeanFilingsAgent` používá stejný `DocumentRecord` jako SEC a podporuje
Euronext, FCA NSM/RNS, AFM, BaFin, ČNB, konfigurovatelné lokální burzy a firemní
IR. Vstupem jsou přesné URL s LEI nebo ISIN; name-only scraping není povolen.
ESEF/XHTML se bezpečně načte a hashuje, surový obsah se nepersistuje. Autorita i
finální redirect musí zůstat na schválené doméně.

Každý dokument ukládá `source_priority` podle pevného pořadí:

1. regulatorní filing (600),
2. auditovaný výkaz (500),
3. burzovní oznámení (400),
4. investor relations (300),
5. prezentace managementu (200),
6. mediální článek (100).

Konfliktní dokumenty zůstávají oba v auditu, ale preferuje se vyšší úroveň.
Mediální článek se v `DecisionAgentu` nikdy nepoužije jako primární potvrzení a
QualityGate odmítne ručně podvrženou prioritu.

`GovernanceEventAgent` normalizuje Form 4, `SC 13D/G`, SEC items 3.02/4.01/4.02
a konzervativní textové nálezy pro qualified opinion, material weakness,
odchody CEO/CFO/directorů, related-party transakce, stock pledge, dilution a
stock compensation. Textový nález zůstává `UNVERIFIED` a vyžaduje lidské nebo
nezávislé potvrzení. Agent nevydává obchodní signal, směr, risk score ani veto;
budoucí dokumenty ignoruje.

## Finanční forenzní screening – etapa 2

Na normalizovaná SEC fakta navazuje volitelný `FinancialForensicsAgent`. Pro
každý ticker vytváří auditní evidence a kontroluje zejména:

- převod účetního zisku do provozního a volného cash flow,
- závazky a úročený dluh vůči aktivům a krátkodobou likviditu,
- akruály vůči aktivům,
- růst pohledávek nebo zásob vůči růstu tržeb,
- materiálně rozdílné hodnoty stejného období mezi filingy a zpoždění podání.

Výstup obsahuje použité metriky, hranice, kódy nálezů, důvěru podle datového
pokrytí a odkazy na konkrétní SEC dokumenty. Nálezy jsou konzervativní indikátory
pro další ověření: nejsou závěrem o podvodu, nemají sektorovou kalibraci a v této
etapě nevytvářejí signal, hard veto ani změnu score, `forecast` či obchodní akce.
Screening lze v UI vypnout nezávisle na SEC ingestu.

## Short reporty a ověření tvrzení – etapa 2

`ShortReportAgent` načítá reporty, které uživatel výslovně zadá v levém panelu,
a umí konzervativně rozpoznat přímý odkaz na report známé short analytické firmy
v již načteném RSS. Pouhá mediální zmínka ani odkaz na cizí doménu se za report
nepovažuje. Samotná existence reportu není důkazem, obchodním signálem, vetem ani
důvodem pro `SELL`. Jeden ruční řádek vstupu má formát:

```text
TICKER | vydavatel | YYYY-MM-DD | https://verejna-domena.example/report.pdf
```

Klient přijímá strojově čitelné HTML, PDF a prostý text. Vynucuje veřejné HTTPS,
kontroluje i cíle přesměrování a DNS adresy, odmítá lokální a privátní sítě,
omezuje velikost stažení a neukládá surový obsah reportu. Do SQLite se ukládá
metadata dokumentu, hash a auditní tvrzení, nikoli stažené tělo reportu.

Deterministická extrakce označí věty pouze jako `UNVERIFIED`. Navazující
`ClaimVerificationAgent` lze spustit jen s aktivním SEC ingestem a finančním
forenzním screeningem. Každé úzké tvrzení porovná s daty dostupnými k okamžiku
běhu a přiřadí jeden ze stavů:

- `CORROBORATED` — dostupná SEC diagnostika je s úzkým tvrzením konzistentní,
- `CONTRADICTED` — konzervativní zdravé metriky úzké tvrzení nepodporují,
- `INSUFFICIENT_DATA` — primární data nestačí k rozhodnutí,
- `UNVERIFIED` — tvrzení zatím nebylo ověřeno.

Stav `CORROBORATED` ani `CONTRADICTED` není hodnocením celého reportu a není
závěrem o podvodu. Kontrolní agent vyžaduje u obou stavů dohledatelný report,
nezávislý primární SEC dokument, identitu ověřovacího agenta a časové údaje.
Chybějící vazba tvrzení zamítne v auditu. Vše zůstává v `shadow_mode` a nemění
score, `forecast`, `BUY`, `SELL`, `NO_TRADE` ani hard veto.

## Síť firem, materiály, energie, regulace a kontrakty – etapa 3

Etapa 3 přidává tři nezávislé opt-in agenty:

- `SupplyChainAgent` ukládá vztahy `SUPPLIER`, `CUSTOMER`,
  `CONTRACT_MANUFACTURER`, `LOGISTICS` a `PARTNER`,
- `CommodityEnergyAgent` ukládá expozice `MATERIAL_INPUT`,
  `COMMODITY_OUTPUT`, `ELECTRICITY` a `FUEL`,
- `RegulatoryContractAgent` ukládá kontrakty, schválení, vyšetřování, sankce,
  změny licencí a granty včetně veřejně oznámené hodnoty a stavu.

Dodavatelské vztahy a materiálové/energetické expozice lze dodat ručně nebo je
konzervativně objevit přímo v bezpečně načteném textu posledního SEC 10-K. Parser
bere pouze explicitní věty o koncentraci zákazníků/dodavatelů, smluvní výrobě a
vstupních materiálech či energiích. Neznámou protistranu si nevymýšlí, neodhaduje
směr ceny a automatickému nálezu dává důvěru nejvýše 0,45. Text 10-K se používá
jen v paměti; do SQLite se ukládá URL, hash, MIME a strukturovaný audit, nikoli
surové tělo filingu. Regulační a kontraktní události lze navíc konzervativně
objevit v již načteném RSS; takový nález je vždy označen jako neověřený s
důvěrou 0,45, a proto nesplní práh DecisionAgentu 0,70. Ruční záznam například
na firemní výkaz, regulatorní oznámení nebo registr kontraktů má tvar:

```text
TICKER | protistrana | typ vztahu | podíl %/- | vydavatel | YYYY-MM-DD | HTTPS URL
TICKER | materiál/energie | typ expozice | podíl %/- | vydavatel | YYYY-MM-DD | HTTPS URL
TICKER | typ události | stav | název | protistrana/úřad | hodnota/- | měna/- | vydavatel | YYYY-MM-DD | HTTPS URL
```

Každý záznam má stabilní ID, datum zveřejnění, zdrojový dokument a samostatnou
observaci běhu. Lokální, privátní a URL s přihlašovacími údaji se odmítají.
UI a týdenní runner navíc bezpečně stáhnou obsah, zkontrolují každý redirect a
DNS cíl, uloží pouze hash, MIME a auditní metadata a hledají klíčový pojem
z manifestu. Surové tělo dokumentu se neukládá. Když se klíčový pojem v obsahu
nenajde, záznam zůstane pouze auditní a jeho důvěra je omezena na 0,45.
Budoucí publikace nesmí projít point-in-time kontrolou. QualityGate současně
zamítne jakýkoli signal, nenulový směr, risk score nebo hard veto vydané těmito
třemi agenty. Vše proto zůstává auditní a nemění predikci v2.1.

SQLite tabulky etapy 3 jsou:

- `company_relationships` + `company_relationship_observations`,
- `resource_exposures` + `resource_exposure_observations`,
- `regulatory_contract_events` + `regulatory_contract_event_observations`.

## DecisionAgent, OOS evaluace a aktivace – etapa 4

Etapa 4 se v UI spouští ve výchozím stavu a vždy jako `shadow`. `DecisionAgent`
navazuje na původní výstup v2.1 a používá konzervativní risk-overlay:

- vrací auditní `P(UP)`, `P(FLAT)` a `P(DOWN)`, důvody a konflikty,
- smí původní `BUY`/`SELL` pouze ponechat nebo navrhnout `NO_TRADE`,
- nesmí obrátit směr ani vytvořit obchod z původního `NO_TRADE`,
- neověřené tvrzení short reportu se nikdy nepovažuje za fakt ani samostatný
  důvod k `SELL`,
- v shadow režimu nevydává obchodní signál a nemění tabulku `Signals`.

`EvaluationAgent` porovnává shadow návrh a stejnou původní predikci v2.1 na
společných out-of-sample výsledcích. Použije pouze poslední běh v týdnu a cenu z
následujícího týdne; výsledek s časem po začátku aktuální orchestrace odmítne.
Jednotkou statistického vyhodnocení je nezávislý týden, nikoli ticker. Tím stovky
korelovaných tickerů z jednoho týdne nemohou vytvořit falešně úzký interval
spolehlivosti. Sleduje týdenně clusterovaný paired lift, konzervativní dolní 95%
Studentovu mez, podíl týdnů s kladným přínosem, coverage, false-positive rate,
Brier score a kalibrační chybu. Výchozí aktivační brána vyžaduje nejméně:

- 200 OOS predikcí a 12 různých týdnů,
- lift alespoň 2 procentní body a jeho dolní 95% mez nad nulou,
- kladný přínos alespoň v 60 % vyhodnocených týdnů,
- coverage alespoň 35 %,
- žádné zhoršení false-positive rate, Brier score ani kalibrační chyby,
- tři úspěšná vyhodnocení s nově přibylým týdenním výsledkem; opakovaný běh nad
  stejným oknem se znovu nezapočítá.

Stavy jsou `INSUFFICIENT_DATA`, `REJECTED`, `SHADOW`, `ELIGIBLE` a `ENABLED`.
UI může dojít nejvýše do `ELIGIBLE`; live aplikace vyžaduje současně vypnutý
globální shadow režim, explicitní allowlist politiky a povolení v konfiguraci
obou agentů. QualityGate odmítne podvržený `ENABLED`, budoucí OOS výsledek,
nesprávné pravděpodobnosti, změnu směru i aplikaci bez navázané evidence.

SQLite tabulky etapy 4 jsou:

- `decision_records`,
- `policy_evaluations`,
- `signal_activation_decisions`.

Přepínače agentů a ruční zdrojové manifesty lze uložit tlačítkem **Uložit
nastavení agentů** do `outputs/agent_runtime.json`. SEC kontaktní User-Agent se
z bezpečnostních důvodů do souboru neukládá a dál se bere z proměnné prostředí.
Pro pravidelný sběr nezávislých OOS týdnů slouží:

```bash
python -m market_checker_app.weekly_shadow_runner --mt5
python -m market_checker_app.weekly_shadow_runner \
  --ticker-file market_checker_app/production_watchlist.txt \
  --ticker-limit 36 --no-mt5
```

Ve Windows lze stejný kontrolovaný běh spustit souborem
`Spustit_Tydenni_Shadow.bat`. Soubor `Nainstalovat_Tydenni_Shadow.bat` vytvoří
úlohu Plánovače úloh každé pondělí v 06:30, se zapnutým doběhnutím zmeškaného
startu. Stav nebo odstranění úlohy lze provést v PowerShellu:

```powershell
market_checker_app\install_weekly_shadow_task.ps1 -Mode Status
market_checker_app\install_weekly_shadow_task.ps1 -Mode Remove
```

Runner vybírá watchlist v pořadí: explicitní tickery z příkazové řádky,
`--ticker-file`, a teprve potom existující SQLite historie. Produkční soubor
`production_watchlist.txt` obsahuje všech 687 unikátních tickerů z exportu
Market Checker; workflow z něj zachová pořadí a vybere 36tickerový pilot včetně
tickerů vyžadovaných nakonfigurovanými zdroji. Runner ukládá audit do stejné DB,
odmítne chybný manifest a skončí chybou, pokud QualityGate neprojde nebo by se
agentní návrh pokusil změnit predikci. Poslední provozní souhrn je v
`outputs/weekly_shadow_latest.json`; schema 2 obsahuje stav jednotlivých zdrojů,
výsledky statistických bran, explicitní blokátory, `accuracy_improvement_proven`,
`live_buy_sell_ready` a `live_buy_sell_enabled`.

GitHub workflow `Market Checker weekly production shadow` navíc každé pondělí:

- ověří deset přesných SEC identit bez fuzzy shody, skutečné Yahoo
  OHLC/metadata, Google News RSS, SEC EDGAR a jeden přímý short report načtený
  ze stejného runtime manifestu jako agentní pipeline,
- spustí 36 tickerů z produkčního 687tickerového souboru v trvale bezpečném
  shadow režimu,
- stáhne SQLite artefakt minulého týdne a po běhu jej znovu uloží na 90 dní,
- odmítne tichý reset historie a zachová audit i při selhání živého zdroje.

Provozní piloty
[32490389851](https://github.com/littleleg198602/JOHNY-SKORE/actions/runs/32490389851)
a
[32491052650](https://github.com/littleleg198602/JOHNY-SKORE/actions/runs/32491052650)
prošly za sebou. Druhý běh obnovil předchozí SQLite artefakt, zvýšil počet
úspěšných orchestračních běhů z jednoho na dva a zachoval nulový zásah do
BUY/SELL. Aktuální short-report canary je zdrojovaná položka Spruce Point pro
MSCI z `autonomous_runtime.json`; není hardcoded ve smoke testu.

V GitHubu je nutné vytvořit Actions secret `JOHNY_SKORE_SEC_USER_AGENT` ve tvaru
`JohnySkore/2.1 kontakt@example.com`. Bez deklarovaného kontaktu produkční smoke
správně selže, protože SEC fair-access identitu nelze bezpečně doplnit za
uživatele. Zdroj short-report canary se mění pouze v auditovaném runtime
manifestu. Žádný secret ani surový obsah zdroje se do artefaktu neukládá.

## Stav původní implementační roadmapy

- **Etapa 1 — hotovo:** orchestrace, registr entit, společné dokumenty/evidence/běhy,
  adaptér v2.1 a kontrolní mechanismus.
- **Etapa 2 — hotovo v shadow režimu:** SEC výkazy, finanční forenzní screening,
  short reporty a ověřování jednotlivých tvrzení.
- **Etapa 3 — hotovo v shadow režimu:** `SupplyChainAgent`,
  `CommodityEnergyAgent` a `RegulatoryContractAgent` pro auditní síť firem,
  materiály, energie, regulaci a kontrakty.
- **Etapa 4 — hotovo v bezpečném shadow režimu:** `DecisionAgent`, paired OOS
  `EvaluationAgent`, point-in-time kontrola a víceprůchodová aktivační brána.
  UI live aplikaci nepovoluje; politika se může stát nejvýše `ELIGIBLE`.

## Kde se ukládá výstup

- Excel: do vybrané složky `Output directory` (default `outputs/`)
- SQLite DB: dle pole `DB soubor` (default `outputs/market_checker_history.db`)

## Delta a trendy

- Delta je primárně počítána z SQLite mezi posledním během a předchozím během
- pokud v SQLite není porovnatelný předchozí běh, použije se fallback na poslední dostupný Excel `Signals` sheet
- počítá se `DeltaTotal`, `DeltaNews`, `DeltaTech`, `DeltaYahoo`, `SignalChange`
- tab `Trends` ukazuje:
  - průměrný TotalScore v čase
  - počty signalů podle běhů
  - top změny proti předchozímu běhu
  - distribuci TotalScore posledního běhu
- tab `History` ukazuje detail vybraného tickeru v čase

## Oveření pondělních predikcí

Pokud je zapnuté **Ukládat historii do SQLite**, tab **Predikce** automaticky
porovná poslední uložený běh jednoho týdne s následujícím týdenním během:

- v2.1 odděluje `forecast` (`UP` / `DOWN` / `FLAT`) od obchodní `action`
  (`BUY` / `SELL` / `NO_TRADE`),
- `BUY` vyjde, pokud cena vzroste, a `SELL`, pokud klesne,
- `NO_TRADE` je vědomá abstence a nepočítá se jako `HIT` ani `MISS`,
- `FLAT` forecast vyjde, pokud absolutní pohyb zůstane ve zvolené toleranci
  (výchozí ±2 %),
- nejnovější signály jsou `PENDING`, dokud není uložen další týdenní běh.

Obchodní akce projde jen při shodě nové a konzervativní legacy vrstvy, nebo při
silném signálu bez ATR/module-conflict veta. Samotná silná technika už nesmí
překlopit HOLD do obchodu. Opakované spuštění ve stejném kalendářním týdnu se
nepočítá jako budoucí výsledek;
použije se poslední uložený běh daného týdne. Nepravidelné mezery delší než
deset dní jsou viditelné jako `IRREGULAR_GAP`, ale nezkreslují týdenní úspěšnost.
Je-li pro ticker dostupné MT5 OHLC, uložená vyhodnocovací cena použije přednostně
`mt5_close`; tím starší Yahoo metadata cache nemůže vytvořit falešný týdenní výsledek.
Poměry velmi blízké běžným stock splitům se před výpočtem výnosu upraví a v detailu
zůstane auditní poznámka `corporate_action_note`.

SQLite historii aplikace automaticky nemaže. Tab **Predikce** proto vyhodnocuje všechny
uzavřené týdny v aktivním DB souboru a zobrazuje:

- directional hit rate pouze pro BUY/SELL,
- trade coverage a průměrný/mediánový signed return,
- samostatnou přesnost forecastu UP/DOWN/FLAT,
- kumulativní úspěšnost váženou počtem skutečných obchodních akcí,
- týdenní počty `HIT` a `MISS`,
- dlouhodobou úspěšnost jednotlivých tickerů a kompletní detail.

Kumulativní výsledek není prostý průměr týdnů: 100 úspěšných a 1 neúspěšná predikce
znamená úspěšnost 99,01 % bez ohledu na to, ve kterých týdnech vznikly.

## Rychlá validace scoring pipeline

Po refaktoru scoringu doporučujeme po změnách vždy ověřit minimálně:

```bash
python -m compileall market_checker_app
python -m unittest discover -s tests -v
```

Volitelně (pokud je nainstalovaný Streamlit):

```bash
streamlit run market_checker_app/app.py
```

Pull requesty navíc kontrolují tři deterministické GitHub testovací agenty:

- **Contract, source and persistence agent** spustí kompilaci a celý testovací balík,
- **687 ticker scale and progress agent** vyžaduje přesně 687 unikátních výsledků,
  Yahoo cache, jeden MT5 batch a dokončení progressu na 100 %,
- **Streamlit UI agent** ověří start aplikace a tlačítka celého Yahoo workflow.

Závěrečný release gate projde pouze tehdy, když projdou všichni tři. Testy nepoužívají
živou síť, takže Yahoo/RSS timeout ani cizí rate limit nemohou náhodně rozhodnout o PR.
Závislosti se instalují přes verzovaný `constraints.txt`, takže CI a Windows
launcher používají stejnou ověřenou kombinaci balíčků. Samostatný plánovaný
workflow **Market Checker live source smoke** jednou týdně ověřuje skutečné Yahoo
a RSS zdroje na třech tickerech; je oddělený od deterministického release gate a
ukládá auditní JSON a SQLite jako GitHub artifact.

Zkontroluj v UI, že tab **Signals** obsahuje sloupce:
- `raw_total_score`, `final_total_score`
- `final_confidence`, `data_quality_score`
- `news_confidence`, `tech_confidence`, `yahoo_confidence`
- `decision_signal`, `forecast`, `action`, `action_reasons`
- `signal_strength`, `blocked_reasons`, `reasons`, `warnings`

## Poznámky k odolnosti

- při nedostupném MT5 aplikace zobrazí chybu a umožní ruční watchlist
- při chybě Yahoo fallbacku přidá warning a pokračuje
- při timeout/chybě RSS zdroje pokračuje s ostatními zdroji
- při chybě SQLite pokračuje bez historie (warning)
- při chybějícím marketcap souboru pokračuje bez market cap ranking dat

## Lokální tajné hodnoty

Soubor `code.env` se nesmí commitovat. Pokud ho potřebuje starší skript
`refresh_news.py`, zkopíruj `code.env.example` na `code.env` a vlož nový klíč
pouze do lokální kopie.

## Poznámka k PR workflow

- pokud v GitHub UI vidíš tlačítko **Zobrazit PR**, znamená to, že pro tuto branch už PR existuje
