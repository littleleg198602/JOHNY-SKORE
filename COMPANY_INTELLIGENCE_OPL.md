# COMPANY INTELLIGENCE — audit a OPL

## Stav release candidate 2026-09-17

Implementační část auditu je dokončena v jednom společném release candidate. Níže ponechaný audit z 15. 9. je historický podklad: jeho tehdejší stavy `OPEN` a `PARTIAL` již nepopisují aktuální větev.

| Oblast | Aktuální stav | Ověření |
| --- | --- | --- |
| OPL-001 až OPL-013 | CODE_COMPLETE | jednotné verze, NYSE kalendář, cache/fronta, tržní a SEC faktory, target v4, kandidát a týdenní walk-forward |
| OPL-014 | CODE_COMPLETE / OFFLINE_VERIFIED | skutečný kanonický seznam 687 tickerů, prázdná databáze, restart, částečný výpadek, idempotentní persistence a UI cesta |
| OPL-015 až OPL-018 | CODE_COMPLETE | časově omezené evidence vztahů, protistran, zdrojových scénářů a makra; nedostatek dat zůstává `INSUFFICIENT_DATA` |
| OPL-019 | WAIT_DATA | software pro předem definované SEC/news/macro ablation je hotový; skutečný přínos vyžaduje kompatibilní uzavřené vzorky z nejméně 12 ISO týdnů |
| OPL-020 | CODE_COMPLETE | statický zákaz broker order volání, UI používá stav analytického ověření, žádná exekuční cesta |

Release candidate opravuje i integrační vady nalezené po původních PR: makro se znovu ukládá v týdenním běhu, běh z UI vytváří stejné snapshoty/reporty jako runner, celkový dluh se skládá pouze z úplných nepřekrývajících se komponent, label je dostupný až při oficiálním close a evaluace počítá skutečné ISO týdny. Přínos predikce se nevydává za prokázaný, dokud OPL-019 nemá reálnou historii.

Datum auditu: **2026-09-15**. OPL = Open Point List, seznam otevřených bodů.
Auditovaný repozitář: **littleleg198602/JOHNY-SKORE**.
Zmrazený podklad: [main@a64d4de5677f04b4eae140994bc303356ca338f8](https://github.com/littleleg198602/JOHNY-SKORE/commit/a64d4de5677f04b4eae140994bc303356ca338f8).

Tento soubor je aktuální realizační seznam navazující na COMPANY_INTELLIGENCE_TASKS.md. Původní AUD a oblastní ID zachovává jako odkazy, nevytváří paralelní nesouvisející roadmapu. Při rozporu má pro stav akceptace přednost tento audit. Starý zápis DONE není důkazem splnění nově reprodukovaných hraničních případů.

## 1. Verdikt

**Ne, všechny úkoly nejsou hotové. Včerejší implementace nejsou ztracené, ale některé akceptace byly uzavřeny předčasně.**

- Produkční cíl je 687 amerických tickerů, nikoli 36. Windows i GitHub týdenní krok již používají celý production_watchlist.txt bez limitu. Seznam v auditovaném commitu obsahuje 687 neprázdných nekomentářových řádků; kanonický kontrakt zároveň prochází v CI.
- Program je aktuálně heuristický analytický agregátor s ukládáním historie a části point-in-time faktorů. Není doložený naučený ani kalibrovaný model, který prokazatelně zlepšuje predikce.
- Automatické obchodování se nikdy nezavádí. Uživatel pouze dostává podklady k vlastnímu rozhodnutí; žádná OOS hranice neslouží k budoucímu zapnutí exekuce.
- Zelené CI současného main potvrzuje existující testy, ale audit našel další konkrétní vady ve validaci cen, targetu, vyhodnocovací frontě, relativních faktorech a reportování.
- Uživatel nyní nemusí nic spouštět. Implementační a integrační ověření má provést vývoj. Živý plný běh je samostatná řízená akceptace, ne přesunutí ladění na uživatele.
- Tento audit mění dokumentaci, ne produkční kód, workflow ani oprávnění. PR #104 se tímto neobnovuje ani neslučuje.

## 2. Co bylo skutečně ověřeno

### Stav GitHubu

| Položka | Doložený stav |
| --- | --- |
| PR #102 | MERGED, opravy dokumentace/artefaktu a duplicity benchmarku jsou v main |
| PR #103 | MERGED, OHLC cache/retry/validace jsou v main |
| PR #104 | CLOSED, NOT MERGED; nepočítat jako dodanou opravu |
| CI aktuálního main | [run 181 / 34939930892](https://github.com/littleleg198602/JOHNY-SKORE/actions/runs/34939930892): všechny čtyři joby SUCCESS |
| CI PR #104 | [run 179 / 34939808788](https://github.com/littleleg198602/JOHNY-SKORE/actions/runs/34939808788): chyba testu — ticker_statuses je list, test jej indexuje jako dict |
| Otevřené PR při kontrole | 0, před založením tohoto dokumentačního PR |
| Ochrana main | GitHub branch metadata: protected=false; required status checks prázdné |
| Poslední nalezený týdenní live běh | [14. 9., run 34844755845](https://github.com/littleleg198602/JOHNY-SKORE/actions/runs/34844755845), starý commit 8adc899, FAILURE před hlavní analýzou |
| Nový live běh aktuálního main | V načtené historii nedoložen |

Poslední live log obsahuje přesnou příčinu: SEC legal-name konflikt AMAT mezi koncovými zápisy /DE/ a /DE. Yahoo, RSS, SEC a short-report kontroly v tomto běhu prošly. Hlavní analýza byla přeskočena. Existující uložený artefakt proto není nový výsledek všech 687 tickerů.

Oprava AMAT již existuje v produkční funkci verify_company_identity_pilot v live_source_smoke.py: nejprve přesný CIK/LEI/ISIN, poté pouze úzká normalizace jurisdikčního zakončení. Testy pokrývají AMAT i odmítnutí věcné změny názvu. PR #104 místo toho upravoval jiný GLEIF pomocný skript a normalizoval i právní přípony. Není důvod přebírat tuto duplicitní změnu.

### Metoda a hranice auditu

- Čtení kódu na přesném commitu, workflow, konfigurace, testů, metadat PR a skutečných CI/live logů.
- Deset izolovaných diagnostických případů nad pracovními kopiemi zdrojů tohoto commitu; bez změny jejich funkcí. U propojených modulů byly vyjmuty příslušné definice přes AST a použity datové fixture / fake store, nikoli reálná SQLite uživatele.
- Nešlo o nový celý lokální test suite, nový síťový canary ani živou 687tickerovou analýzu. Výsledek plného CI je převzat z konkrétního GitHub běhu.
- Nebyly provedeny nové dotazy na Yahoo/SEC/Google ani čteny secrets. Dnešní dostupnost každého endpointu tedy není potvrzena. Nedostupnost endpointu se nesmí dovozovat z nepřítomnosti dat.
- Bez čtení všech historických artefaktů nelze prohlásit kompletní OOS historii za nulovou ani kompletní. Její množství a kompatibilita jsou otevřený akceptační bod.

### Reprodukované případy

| Důkaz | Vstup / očekávaná ochrana | Skutečný výsledek main | OPL |
| --- | --- | --- | --- |
| R01 | OHLC Close = +inf | price_usable=true, close=inf | 002 |
| R02 | 60 kopií stejné seance | observation_count=60, history_usable=true | 002 |
| R03 | Denní bar datovaný 15. 9. 00:00 UTC, analýza 15. 9. 12:00 UTC před otevřením US trhu | price_usable=true; chybí ochrana uzavření seance | 002 |
| R04 | Poslední bar 11. 9., analýza 15. 9. po chybějící pondělní seanci | price_usable=true, protože pevná hranice je 7 dní | 002 |
| R05 | Výnos akcie končí 14. 9., benchmarku 11. 9. | relative_returns.1d = 0.09, i když období nejsou shodná | 006 |
| R06 | Akcii i benchmarku chybí 3. 9.; data 1., 2., 4., 8., 9., 10. 9. | target akceptuje endpoint 10. 9.; společná absence dne není odhalena | 003 |
| R07 | Evaluace as_of=2. 9., loader vrátí i data do 10. 9. | resolved=1, target_observed_at=10. 9. — budoucnost vůči evaluaci | 003 |
| R08 | Jeden požadovaný ticker a řádek bez ceny | coverage_pct=100; je to pokrytí řádků, ne použitelných dat | 007 |
| R09 | Stejný titulek jedné zprávy u 10 vydavatelů, event ID tvořeno doména + titulek jako v RSS | duplicate_ratio=0; confidence roste 51.43 → 86.10 | 010 |
| R10 | Řádek bez ceny se skóre 99 versus platný řádek se skóre 60 | RankingService dá neplatný řádek na první místo | 007 |

R10 dokládá absenci ochrany přímo v ranking službě, nikoli tvrzení, že každý skutečný běh takový výsledek vytvoří. R01/R02 dokládají vady validátoru i kdyby je některý jiný vstupní filtr zachytil. R09 ukazuje nesplněnou akceptaci syndikace napříč vydavateli, nikoli to, že je vždy každá kopie fakticky nezávislý event.

## 3. Co už je v kódu a nemá se dělat znovu

| Oblast | Hotová část | Co tím ještě není prokázáno |
| --- | --- | --- |
| Universe / spouštěče | Týdenní GitHub a Windows cesta bez produkčního limitu 36; 687tickerový kontrakt | Čerstvá a použitelná data pro všech 687 |
| Identity AMAT | Úzká oprava v live_source_smoke.py + negativní testy | Nový live artefakt a úplné identity celého běhu |
| Yahoo OHLC | Persistentní SQLite cache, failure checkpoint, stale fallback, omezené retry chybějících symbolů | Inkrementální update, validní seance, dlouhodobá dostupnost |
| Ceny | Přednost ověřované OHLC před nečasovanou metadata quote; označení krátké historie | Všechny hraniční případy R01–R04 |
| Snapshoty / labely | Ukládání snapshotů, target 5d, samostatný dávkový resolver, atomický JSON | Kalendářní a časová správnost, obsloužení celé fronty |
| News | Neutrální titulky již nemění směrové skóre počtem; provenance pole existují | Skutečné event-clustery, nezávislost zdrojů, kalibrace |
| Tržní faktory | Výnosy, relativní výnos, volatilita, drawdown se ukládají | Shodná období, kompletní SEC faktory nebo jejich predikční přínos |
| UI / artefakty | Načítání posledního JSON, řádky, missing seznam, rolling SQLite a acceptance artefakt | Kompletní použitelnost, stáří cen, nezavádějící názvy a evidence |
| Řetězce / materiály | Evidence zveřejněné koncentrace a zmínek v SEC textu | Úplný graf dodavatelů/odběratelů, zdraví protistran a dopad do marží |

## 4. Dostupnost dat a realistický rozsah

| Zdroj / oblast | Co máme doložené | Co chybí / skutečná překážka | Realistická cesta |
| --- | --- | --- | --- |
| Yahoo ceny | Malý live smoke prošel 14. 9.; wrapper a cache existují | Neověřená celá dávka, stáří, throttle a obnova | Dávky, session-aware cache, přesný transportní audit, měření dvou běhů |
| SEC JSON | Malý canary 14. 9. prošel | Ověření celého universe, periodizace a point-in-time faktory | CIK resolver, bezpečný sdílený transport, filing/availability data |
| SEC texty | Kód stahování a evidence chyb existuje | Každý chybějící text vyžaduje URL, čas, HTTP/parser důvod | Oddělit timeout/403/429/404/parsing; volitelnou vrstvu degradovat |
| Google News / RSS | Malý canary prošel; provenance pole existují | Titulky nejsou záruka přístupu k plnému článku ani kompletní historie | Archivovat od nynějška, extrahovat datované události, deduplikovat a uvádět evidence level |
| Short reporty | Dva zdroje v posledním canary prošly | Omezené pokrytí a tvrzení nejsou automaticky fakta | Citace, datum, lifecycle, potvrzení primárními dokumenty |
| Evropské endpointy | Canary ukázal HTTP odpovědi včetně 202; produkční evropské filingy jsou vypnuté | HTTP odpověď není ověřený firemní filing | Není blokátor US produktu; zapínat až podle konkrétního použití a obsahu |
| Dodavatelé / odběratelé | Parser umí i anonymní koncentraci typu Unnamed major customer | Často není zveřejněna identita, podíl, období nebo zdraví partnera | Přesná citace a stav neúplnosti; známé protistrany párovat silnou identitou |
| Soukromé protistrany | V auditu není doložen funkční systematický sběr jejich výkazů | Pro konkrétní firmu není ověřeno, zda výkaz existuje a je přístupný; placená DB není automatická odpověď | Nejdřív zjistit identitu, jurisdikci a veřejný primární dokument; teprve pak posoudit přístup a případnou licenci |
| Materiály / energie | Expozice lze evidovat, agent má price_series_attached=false | Cena komodity sama nestačí bez nákladového podílu, hedgingu a přenosu cen | Oddělit zveřejněný fakt od scénáře a neznámé hodnoty |

Povinné rozlišení: NOT_CONFIGURED, NOT_DISCLOSED, ACCESS_BLOCKED, RATE_LIMITED, TIMEOUT, PARSE_FAILED, STALE, UNKNOWN. NOT_DISCLOSED se použije až po kontrole relevantního dokumentu, ACCESS_BLOCKED jen s konkrétním důkazem odmítnutí. Chybějící data nejsou zdravá firma ani nulové riziko. Paywall/403 se neobchází.

Projekt je reálný jako transparentní a postupně ověřovaný analytický nástroj. Nelze poctivě garantovat lepší přesnost přidáním článků ani úplný graf soukromých protistran. Případný placený zdroj či služba vyžaduje samostatné rozhodnutí uživatele; není předem podmínkou.

## Vývojová aktualizace — 2026-09-15

První společná P0 dávka je implementována v otevřeném draftu [PR #106](https://github.com/littleleg198602/JOHNY-SKORE/pull/106), head **579a104951c2221c8e38528826d3ba9f7a0cf319**.

| OPL | Milník | Důkaz | Co ještě nelze tvrdit |
| --- | --- | --- | --- |
| OPL-002 | CODE_COMPLETE | Validace konečných kladných cen, unikátních dokončených NYSE seancí a historie; regresní testy R01–R04. | MERGED ani nový live 687 běh. |
| OPL-003 | CODE_COMPLETE | Společný NYSE kalendář, cutoff evaluace a úplné t0–t+5 sessiony; regresní testy R06–R07. | MERGED ani nový live 687 běh. |
| OPL-006 | CODE_COMPLETE | Relativní faktory vyžadují shodné endpointy/sessiony; krátká historie nevydává 252d drawdown; regresní test R05. | MERGED ani nový live 687 běh. |
| CI | PASS | [run 188](https://github.com/littleleg198602/JOHNY-SKORE/actions/runs/34968959343): UI, 687 kontrakt, 243 deterministických testů a release gate. | CI není živá akceptace ani důkaz predikčního přínosu. |

Žádná z těchto změn nepřidává obchodní exekuci ani nemění plánovaný live workflow. Další vývojová dávka je OPL-004 + OPL-005 + OPL-007: kapacita labelů, rozsah cache a pravdivý ranking/report.

## 5. Nový OPL — souhrn otevřených bodů

Stavy: OPEN = konkrétní práce zbývá; PARTIAL = část v main existuje; VERIFY = kód existuje, chybí důkaz; WAIT_DATA = vyžaduje historii po opravě metodiky.
Priority: P0 = správnost vstupů/targetu/reportu; P1 = úplnost předávané základní analytiky; P2 = další specializovaná vrstva, nikoli skrytě hotový bod.
Velikost S/M/L je relativní rozsah implementace, ne slíbený termín.
Vlastník DEV/DATA/QA označuje navrženou roli, nikoli již objednaného externího člověka. REPOMAIN = správce s oprávněním k nastavení GitHubu.

| ID | Priorita | Stav | Bod | Vazba | Vlastník / velikost |
| --- | --- | --- | --- | --- | --- |
| OPL-001 | P1 | OPEN | Sjednotit pravdivé stavy, verze a předání | AUD-001/002, BASE-001 | DEV / S |
| OPL-002 | P0 | PARTIAL / CODE_COMPLETE | Validní konečné ceny a uzavřené seance | AUD-004 | DEV / M |
| OPL-003 | P0 | PARTIAL / CODE_COMPLETE | Kalendář a časová hranice targetu | AUD-006 | DEV / M |
| OPL-004 | P0 | PARTIAL | Fronta labelů pro celý universe bez hladovění | AUD-007 | DEV / M |
| OPL-005 | P0 | PARTIAL | Cache se správným rozsahem a cenovou metodikou | AUD-003/004/006 | DEV / L |
| OPL-006 | P0 | PARTIAL / CODE_COMPLETE | Srovnatelná období tržních faktorů | AUD-011, MKT-001 | DEV / M |
| OPL-007 | P0 | PARTIAL | Poctivé pokrytí, ranking a UI pro 687 | AUD-002/005/013/014 | DEV / L |
| OPL-008 | P1 | PARTIAL | Jednotná diagnostika zdrojů a degradace | AUD-005, OPS-805 | DEV / M |
| OPL-009 | P1 | VERIFY | Identity a source smoke skutečné verze | AUD-001, ENTITY-101 | QA / M |
| OPL-010 | P1 | PARTIAL | Události, syndikace a relevance zpráv | AUD-008/009, NEWS-001 | DEV+DATA / L |
| OPL-011 | P1 | PARTIAL | SEC fundamentální faktory dostupné k času rozhodnutí | AUD-011, FUND-001 | DEV+DATA / L |
| OPL-012 | P1 | CODE_COMPLETE / OOS evidence pending | Zmrazené baseline a jednoduchý kandidátní model | AUD-012, BASE-001, MODEL-001 | DEV+DATA / L |
| OPL-013 | P1 | CODE_COMPLETE / OOS evidence pending | Vyhodnocovací software a report nové target verze | AUD-007/010/012, EVAL-001 | DEV+DATA / L |
| OPL-014 | P1 | PARTIAL | Integrační a živá akceptace 687 bez MT5 | AUD-014, SCALE-001 | QA / L |
| OPL-015 | P1 | CODE_COMPLETE / live evidence pending | Důkazní vrstva dodavatelů i zákazníků | AUD-015, SUPPLY-401..403 | DEV+DATA / L |
| OPL-016 | P2 | CODE_COMPLETE / live evidence pending | Zdraví identifikovaných protistran včetně soukromých | PRIVATE-001, AUD-015 | DATA+DEV / L |
| OPL-017 | P2 | CODE_COMPLETE / live evidence pending | Materiály, energie a scénáře marží | RESOURCE-501..503, AUD-015 | DATA+DEV / L |
| OPL-018 | P2 | CODE_COMPLETE / live evidence pending | Makro/sektorový režim bez look-ahead | MACRO-001 | DATA+DEV / M |
| OPL-019 | P1 | WAIT_DATA | Skutečný OOS přínos, kalibrace a ablation | AUD-012, EVAL-001 | DATA / průběžně |
| OPL-020 | P1 | PARTIAL | Výhradně analytický produkt a ochrana vydání | UI-806, OPS-805 | DEV+REPOMAIN / M |

Celkem **20 otevřených bodů: 6 P0, 11 P1 a 3 P2**. Z toho 14 PARTIAL, 4 OPEN, 1 VERIFY a 1 WAIT_DATA. Počet není procento dokončení projektu; velikosti a závislosti jsou různé.

## 6. Přesné zadání a podmínky uzavření

### OPL-001 — pravdivé stavy a verze

Důkaz: AUD-001 je stále TODO přestože oprava je v produkčním smoke; AUD-006/009/013 jsou DONE přes nálezy R06/R07/R09/R08/R10. PR #104 není sloučený. Baseline model version a SCORING_VERSION používají legacy označení i po změnách news logiky.
Provést: sladit implementace versus akceptace, odkazovat na tento OPL; oddělit původní zmrazené skóre od pozdějších změn. Uložit code SHA, konfiguraci/hash, model/target/feature verzi a datum sestavení.
Hotovo: každý výsledek má dohledatelnou verzi; změna scoringu má novou verzi; staré snapshoty se nepřepisují. Jeden přehled předání říká, co je v main, co prošlo testy a co čeká na data. Žádná duplicitní AMAT oprava.
Závislost: bez závislosti. Důkaz uzavření: dokumentace + test verzování + SHA vydání.

### OPL-002 — OHLC validace

Důkaz: R01–R04, services/ohlc_quality.py; 7denní tolerance a počet řádků místo unikátních uzavřených seancí.
Provést: odmítnout ne-konečné hodnoty, validovat OHLC/objem dle použitého indikátoru, deduplikovat data s auditovaným pravidlem. Určit poslední očekávanou uzavřenou US seanci včetně DST, svátků a zkrácených dnů. Oddělit cenu použitelnou pro informaci od kompletní historie pro výpočet.
Hotovo: +inf/NaN, duplicitní seance, otevřený denní bar a chybějící poslední seance nemohou vytvořit připravený signál. Testy pro jednotlivé indikátorové lookbacky, ne jen univerzálních 60 řádků.
Závislost: společný kalendář s OPL-003. Důkaz: negativní unit testy + pipeline test.

### OPL-003 — target a evaluační čas

Důkaz: R06/R07; _common_price_windows srovnává dostupné řádky, ne nezávislý kalendář. resolve_pending_snapshots neomezuje target endpoint evaluačním clock.
Provést: společné t0 a t+5 odvodit z kalendáře, vyžadovat všechny očekávané seance, price timestamps mapovat na session close/availability. Odmítnout target_observed_at po evaluation_as_of. Změnu targetu/metodiky verzovat.
Hotovo: stejné chybějící datum v obou řadách nesmí prodloužit horizont; předčasný loader nesmí uzavřít label; víkend/svátek/zkrácený den má přesný očekávaný výsledek. Neúplné dočasné zdroje mají PENDING s důvodem.
Závislost: společný kalendář OPL-002, cenová metodika OPL-005.

### OPL-004 — kapacita a spravedlnost label fronty

Důkaz: workflow jednou týdně spustí --limit 120; runner i resolver berou head(limit). SQLite řadí as_of ASC, ticker ASC. Při jedné predikci na 687 tickerů přibývá až 687 položek, ale tato cesta odebere maximálně 120 — rozdíl až 567 za týden. Stále nedostupné první záznamy mohou blokovat další.
Provést: vybírat zralé/due snapshoty, stránkovat přes celou frontu s časovým budgetem; uložit cursor, retry_after a důvod odkladu. Oddělit kandidátní frontu pro výběr symbolů od nezralých a odložených položek. Windows i plánovaný orchestrátor musí obsloužit evaluaci bez další ruční CLI povinnosti.
Hotovo: test s více než 687 položkami, z toho první 120 opakovaně nedostupných, přesto zpracuje pozdější zralé položky; restart nevytváří duplicity; report uvádí backlog, nejstarší čekání a pending_after. Kapacita odpovídá přítoku.
Závislost: OPL-003/005.

### OPL-005 — rozsah cache a cenová metodika

Důkaz: cache se klíčuje tickerem a expirací, při úspěchu nahrazuje rámec. Resolver i benchmark loader přijímají lookup.usable včetně stale a nedoplňují chybějící konec; resolver běží před novou analýzou. Collector stahuje period=1y, auto_adjust=False a výpočty čtou Close.
Provést: cache klíč doplnit o poskytovatele, interval, úpravy cen a verzi; ověřovat potřebný rozsah seancí, aktualizovat chybějící dny, respektovat backoff i v resolveru/benchmarku. Výslovně zvolit price-return versus total-return target; zdokumentovat split/dividend metodiku a kompatibilitu historických záznamů.
Hotovo: týden starý rámec se doplní pro splatný label, nevyvolá předčasně trvalé UNAVAILABLE; druhý běh znovu nestahuje kompletní dostupnou historii; test dividendy/splitu/nově kotované firmy/revize provideru; chybějící starší období je explicitní.
Závislost: OPL-002/003; souběžně s OPL-004.

### OPL-006 — srovnatelné faktory

Důkaz: R05; build_market_factor_snapshot odečítá výnosy spočtené nezávisle podle počtu řádků. drawdown_252d vrací hodnotu i z krátkého okna a jeho missingness není úplnost 252 dní.
Provést: vyžadovat společný kalendář, endpoint i počátek jednotlivých horizontů; u každého faktoru uložit observation/availability čas, skutečné období a partial/missing důvod. Rozlišit current drawdown od maxima drawdownu.
Hotovo: nesourodá období vrací MISSING, nikoli 9% relativní výnos; krátká historie nesmí předstírat plný roční faktor. Test stejných i chybějících seancí a cutoffu.
Závislost: OPL-002/003/005.

### OPL-007 — použitelný ranking a report

Důkaz: R08/R10. _SIGNAL_DETAIL_COLUMNS neexportuje cenu, zdroj, ohlc_close_at ani history_usable. _source_health_summary nepřebírá nové result.source_health.current_prices; ranking_usable se do summary nepřenáší. RankingService řadí bez eligibility masky. UI zobrazuje pokrytí řádků a chybějící tickery jen bokem.
Provést: společný per-ticker kontrakt pro REQUESTED/ATTEMPTED/USABLE/PARTIAL/FAILED/NOT_ATTEMPTED s reason codes. Doplnit cenu, session close, fetched_at, provenance, stáří, stav kritických vrstev, hash přesného uspořádaného universe. Počítat coverage řádků a usable coverage odděleně. Ranking pouze nad způsobilými položkami; ostatní zobrazit bez zavádějícího pořadí.
Hotovo: všech 687 je dohledatelných v UI/JSON/exportu i při selhání; 1 platná cena neznamená platný celý ranking. Souhrnné počty sedí na detail, unexpected/duplicate ticker je odmítnut, attempt není odhad z počtu řádků. Test celé cesty pipeline → SQLite/JSON → UI.
Závislost: OPL-002/006/008. PR #104 nepřebírat naslepo; opravit kontrakt na aktuálním main.

### OPL-008 — dostupnost a degradace

Důkaz: retry části existují; summary redukuje Yahoo stav na počty downloadů, což neodlišuje fresh cache, stale cache a kvalitní data. Live RSS/short canary jsou blokující, přestože jejich selhání nemusí zničit platné ceny.
Provést: společný transportní audit a provider-level limiter/circuit breaker pro relevantní cesty; respektovat Retry-After tam, kde je hlavička dostupná. Rozlišit transport, parsing, identity, missing disclosure a konfiguraci. Které zdroje blokují kterou vrstvu definovat explicitně; integritní selhání se nikdy nezmění na úspěch.
Hotovo: 403 se neřeší nekonečným retry; 429/timeout mají omezený postup; každý PARTIAL/REJECT má ticker, URL, čas, kategorii, pokusy a konkrétní nápravu. Žádná analýza zdraví z absence dat. Test all-prices-down i výpadku jednoho enrichmentu.
Závislost: OPL-005/007.

### OPL-009 — identity a canary

Důkaz: oprava AMAT i negativní testy jsou v main, ale poslední live běh používal starou verzi. Konfigurace smoke má minimum 36 identit, nikoli důkaz všech 687.
Provést: použít existující úzkou opravu, spustit kontrolu aktuálního commitu v nakonfigurovaném prostředí; uložit výsledek a audit identity všech požadovaných tickerů v plné analýze. Zvlášť instrument versus issuer, GOOG/GOOGL, přejmenování a delisting.
Hotovo: AMAT projde kosmetickou kontrolou; jiný identifikátor nebo věcná změna je konflikt; všech 687 má resolved/quarantined/unresolved s důvodem. Malý canary se neprezentuje jako plné pokrytí.
Závislost: OPL-008; code-ready AMAT se znovu nepřepisuje.

### OPL-010 — kvalita zpráv

Důkaz: R09. RSS event_id = publisher_domain + normalizovaný titulek, což neclustruje stejnou událost napříč vydavateli. Trust čte article.source, i když publisher má vlastní pole; relevance používá substring tickeru. No-news větev vrací score 42, nikoli samostatné missingness.
Provést: oddělit transport od původního vydavatele; kanonické event ID napříč zdroji a přepisy, ticker/entity relevance, odlišení syndikace od nezávislého potvrzení. Explicitní as_of/observed_at pro replay, evidence-level title/summary/full-text, případné licence. Neutrální/no-news nezaměňovat se směrovým negativním signálem.
Hotovo: deset kopií nezvýší důvěru jako deset potvrzení; test krátkého tickeru, negace, neznámého vydavatele, no-news, cutoffu a přepsaných titulků. Změna logiky má novou model/feature verzi a nemění zpětně baseline.
Závislost: OPL-001; měření přínosu v OPL-019.

### OPL-011 — SEC point-in-time faktory

Důkaz: ingest a forenzní vrstva existují, ale AUD-011 sám přiznává chybějící srovnatelné SEC delty; přidané market factors nejsou SEC feature store.
Provést: tržby, marže, cash flow, capex/FCF, dluh a hotovost podle správného účetního období a jednotek; oddělit quarter/annual/YTD, původní filing a revizi. Ukládat availability/accepted-at a reference na původní fakta, nikdy chybějící údaj jako nulu.
Hotovo: známé fixture dávají správné meziroční/kvartální srovnání i u jiného fiskálního roku; pozdější oprava výkazu nesmí změnit historicky dostupný faktor. Jsou doložené missingness, provenance a export; oddělit forenzní red flag od dokázaného problému.
Závislost: OPL-008/009; vstup pro OPL-012/019.

### OPL-012 — skutečný model, ne pouze další skóre

Důkaz: aktuální ranking je řazení final_total_score; přínos nové naučené varianty není doložen.
Provést: zachovat původní legacy baseline se skutečnou verzí; přidat jednoduchý momentum/relativní baseline a následně jednoduchý pravidelně trénovatelný kandidát. Trénovací okna a imputace/scaling/kalibrace pouze z minulosti; pipeline a model version musí být reprodukovatelné.
Hotovo kódově: deterministický trénink/predikce, artefakt s verzí a datovým intervalem, ochrana proti leakage, fallback k transparentnímu baseline při nedostatku dat. Nasazení kandidáta do produkčního pořadí není automatické.
Závislost: OPL-001/003/006/010/011. Lepší přesnost není součástí slibu implementace.

### OPL-013 — vyhodnocovací software

Důkaz: vedle nových 5denních snapshotů existují legacy Stage4 statistiky a pole activation_state; to není automaticky evaluace nového targetu.
Provést: samostatný report kompatibilních model/target verzí; walk-forward, ochrana překryvu labelů, kalibrace, ranking IC a top-decile excess return, výsledky podle týdne/sektoru. Otestovat oddělení legacy metrik a nových labelů; intervaly nejistoty podle týdnů, ne předstírané nezávislosti všech tickerů.
Hotovo kódově: syntetický známý výsledek má správné metriky, negativní leakage test selže, nedostatek dat dá jasné INSUFFICIENT_DATA. Program umí vyhodnotit i horší kandidát bez jeho zatajení; report není aktivace obchodování.
Závislost: OPL-003/004/012. Skutečné hodnoty až OPL-019.

### OPL-014 — provozní akceptace

Důkaz: CI #181 prošlo; poslední live #34844755845 přeskočil hlavní analýzu. Full-universe acceptance dnes kontroluje účetnictví řádků, ne čerstvost nebo přesnost.
Provést: nejdřív deterministický integrační běh skutečné no-MT5 cesty s kanonickými 687 a prázdnou cache, pak restart s částečným selháním. Následně zvlášť schválený živý běh aktuální verze a opakování s cache, v časovém rozpočtu workflow.
Hotovo: JSON, SQLite a UI se shodují; SHA, universe hash, cache hity/missy, retry, runtime a každý neúspěch jsou doložené. 100 % úspěšných providerů není vymyšlená podmínka — omezení jsou transparentní a neplatné řádky nejsou rankovány. Obnova nesmí vydat starý report za právě dokončený běh.
Závislost: P0 opravy, OPL-008/009; finální předání po zvoleném rozsahu P1.

### OPL-015 — dodavatelský i zákaznický řetězec

Důkaz: filing_exposure_discovery_service zapisuje Unnamed major customer / Unnamed critical supplier / Unnamed contract manufacturer; to není plný graf partnerů.
Provést: evidovat orientovanou vazbu dodavatel → firma → odběratel; identita partnera nebo výslovné anonymity, produkt/vstup, země, období, zveřejněný podíl, citace a zdroj. Evidovat koncentraci, single-source a konkrétní události narušení. Kontrolovat čerstvost a důkazní úroveň každé hrany.
Hotovo: na ručně ověřené sadě firem lze vysvětlit každou vazbu a chybějící hodnotu; anonymní odběratel se nepřiřadí náhodné firmě. Supply i customer mají samostatné zobrazení. Bez podkladu výstup UNKNOWN, ne „řetězec v pořádku“. Základní evidence se dodává před použitím, predikční dopad až po ablation.
Závislost: OPL-008/009/011. Tento bod není zrušen ani nahrazen počítáním regex vztahů.

### OPL-016 — zdraví protistran

Důkaz: automaticky nalezená koncentrace neobsahuje datované finanční zdraví partnera.
Provést: u identifikované veřejné firmy navázat její výkazy. U soukromé nejdřív ověřit, které veřejné dokumenty skutečně existují a zda jsou dostupné; zaznamenat datum/zdroj a rozsah zveřejnění. Placenou databázi řešit až po zmapování konkrétní mezery.
Hotovo: likvidita, zadlužení a cash flow mají doložený výkaz a období; neúplný veřejný dokument vyústí v omezené posouzení, nikoli kompletní skóre zdraví. Partner bez identity/výkazu nemá vymyšlený rating.
Závislost: OPL-011/015. Dostupnost dat může část bodu trvale omezit; to se vykáže jednotlivě.

### OPL-017 — materiály a energie

Důkaz: resource agent eviduje expozice, price_series_attached=false; samotná zmínka o ropě/mědi/energii nevyčísluje dopad.
Provést: navázat relevantní cenovou řadu a jednotku/měnu, zveřejněný nákladový podíl, fixaci cen, hedging a přenos zdražení na zákazníka. Oddělit datovaný fakt od scénáře/citlivosti a jeho předpokladů.
Hotovo: bez podílu či smluv se nevydává přesný dopad do marže; příklad s doloženými parametry má ověřitelný výpočet, neznámé vstupy jsou vidět. Historická cena ani budoucí zveřejnění nesmí proniknout před cutoff.
Závislost: OPL-011/015; případné licencování ceny vyžaduje souhlas.

### OPL-018 — makro a sektor

Důkaz: sektorový benchmark existuje, MACRO-001 zůstává TODO; není to kompletní režimová vrstva.
Provést: malý předem definovaný soubor ukazatelů, publikační čas a případné vintage revize; žádné široké hledání článků bez kontrolovatelného významu.
Hotovo: historický replay nepoužije revidovanou budoucí hodnotu; missingness, verze a samostatná ablation jsou doložené.
Závislost: OPL-006/013; mimo opravy správnosti základní verze.

### OPL-019 — reálný přínos

Důkaz: zelené CI ani počet článků nedokazuje zlepšení; aktuální kompatibilní OOS dataset nebyl tímto auditem kompletně načten.
Provést: po opravách pravidelně sbírat snapshoty a zralé labely; inventura starých dat podle verzí, žádné umělé přeznačení neslučitelných záznamů. Vyhodnotit baseline/kandidáta a přínos jednotlivých vrstev; uvést nejistotu i negativní výsledek.
Hotovo: dohledatelný reprodukovatelný report s intervaly a obdobími. Původní minimum 200 vzorků / 12 týdnů je kontrola evidence, ne záruka úspěchu ani zákaz zobrazení analýzy. Nelze „doprogramovat“ skutečně uplynulé týdny.
Závislost: OPL-003/004/012/013/014. WAIT_DATA nezastírá zbývající práci na softwaru.

### OPL-020 — analýza bez exekuce a bezpečné vydání

Důkaz: JSON deklaruje analysis_only a permanently_disabled; UI posledního reportu stále používá „Aktivace“/„aktivace“. main nemá ochranu větve.
Provést: odstranit uživatelskou terminologii aktivace obchodování, důsledně oddělit analytický návrh od ověření modelu. Negativním testem/scannem ověřit nepřítomnost odesílání obchodních příkazů (čtení MT5 dat není exekuce). Zachovat existující data během update; vydat jednoznačně označenou verzi.
Hotovo: žádný broker order endpoint, žádná cesta budoucího auto-trading switch; v UI pouze analytický stav a uživatelova ruční volba. Správce samostatně nastaví ochranu main, povinný release check a zákaz force-push/delete, pokud má potřebné oprávnění. Změna pravidel se neprovádí tímto auditem.
Závislost: OPL-001/007; ochrana vyžaduje správce, nesmí se obcházet.

## 7. Doporučený postup — co lze dělat spolu

1. **Dávka A: správnost a pravdivý výstup.** OPL-002 + 003 sdílejí kalendář; paralelně OPL-004 + 005 frontu/cache; OPL-006 naváže na jejich kontrakt. Současně připravit OPL-001, 007 a 020. Uzavřít všechny P0 negativními testy. Žádné další míchání skóre před touto dávkou.
2. **Dávka B: zdroje a analytické podklady.** OPL-008 + 009; samostatně OPL-010 news; samostatně OPL-011 SEC a OPL-015 evidence řetězců. Každá větev má stejnou verzi datového kontraktu, bez konfliktních duplicit.
3. **Dávka C: model a měření.** OPL-012 + 013; odlišit zmrazený baseline, kandidáta a skutečnou pravděpodobnost. Nečekat pasivně na 12 týdnů, když ještě chybí evaluační software.
4. **Dávka D: vývojová akceptace a předání.** OPL-014: deterministická integrace, ověření aktuálních identit, schválené live běhy a ověřený report/UI. Teprve poté nabídnout uživateli hotovou základní verzi, ne další ladicí pokus.
5. **Dávka E: specializované rozšíření.** OPL-016/017/018 vyžadují konkrétní dostupná data; OPL-019 běží průběžně po opravě metodiky.

Předání základní verze vyžaduje uzavření implementačních a provozních bodů P0/P1 kromě samostatně přiznaného OPL-019. P2 zůstávají viditelně otevřené. **Pokud uživatel trvá na dokončení úplně všech bodů včetně P2 a OOS, nelze základní verzi vydávat za dokončený celý projekt.** Tento dokument žádné odložení P2 za uživatele neschvaluje.

## 8. Pravidla evidence a uzavírání

Každá aktualizace OPL musí uvést: vlastník, stav, implementační SHA/PR, test přímo dané akceptace, stav CI finálního commitu, případný live run + artefakt, známé omezení a datum revize. OPL se uzavírá po důkazu, ne po textu „implementováno“.

Oddělené milníky:
- CODE_COMPLETE — kód a regresní testy hotové.
- MERGED — změna skutečně ve vydávané větvi.
- VERIFIED — integrační/provozní akceptace hotová.
- BENEFIT_MEASURED — přínos změřen; výsledek může být i negativní.

DONE úkolu znamená splnění jeho výslovné akceptace, nikoli pouze jednoho milníku. U časově/datově omezeného bodu je přípustné přiznat WAIT_DATA nebo ověřený limit zdroje, nikoli předstírat hotovo.

## 9. Rejstřík důkazů

Všechny níže uvedené cesty byly čteny na auditovaném SHA, není-li řečeno jinak.

- [Produkční identity a canary](https://github.com/littleleg198602/JOHNY-SKORE/blob/a64d4de5677f04b4eae140994bc303356ca338f8/market_checker_app/live_source_smoke.py): verify_company_identity_pilot, _legal_name_match_mode.
- [Identity regresní testy](https://github.com/littleleg198602/JOHNY-SKORE/blob/a64d4de5677f04b4eae140994bc303356ca338f8/tests/test_live_source_smoke.py).
- [OHLC validace](https://github.com/littleleg198602/JOHNY-SKORE/blob/a64d4de5677f04b4eae140994bc303356ca338f8/market_checker_app/services/ohlc_quality.py): assess_daily_ohlc.
- [Label resolver](https://github.com/littleleg198602/JOHNY-SKORE/blob/a64d4de5677f04b4eae140994bc303356ca338f8/market_checker_app/services/prediction_label_service.py): _common_price_windows, resolve_pending_snapshots.
- [Label runner](https://github.com/littleleg198602/JOHNY-SKORE/blob/a64d4de5677f04b4eae140994bc303356ca338f8/market_checker_app/prediction_label_runner.py): _pending_symbols, resolve_prediction_labels.
- [Cache](https://github.com/littleleg198602/JOHNY-SKORE/blob/a64d4de5677f04b4eae140994bc303356ca338f8/market_checker_app/storage/yahoo_ohlc_cache_store.py): get, usable, upsert_success.
- [Tržní faktory](https://github.com/littleleg198602/JOHNY-SKORE/blob/a64d4de5677f04b4eae140994bc303356ca338f8/market_checker_app/services/market_factor_service.py).
- [Pipeline](https://github.com/littleleg198602/JOHNY-SKORE/blob/a64d4de5677f04b4eae140994bc303356ca338f8/market_checker_app/services/pipeline_service.py): benchmark cache, OHLC quality, source_health, ranking_usable.
- [Weekly JSON](https://github.com/littleleg198602/JOHNY-SKORE/blob/a64d4de5677f04b4eae140994bc303356ca338f8/market_checker_app/weekly_shadow_runner.py): _SIGNAL_DETAIL_COLUMNS, _universe_coverage, _source_health_summary.
- [Ranking](https://github.com/littleleg198602/JOHNY-SKORE/blob/a64d4de5677f04b4eae140994bc303356ca338f8/market_checker_app/services/ranking_service.py).
- [UI](https://github.com/littleleg198602/JOHNY-SKORE/blob/a64d4de5677f04b4eae140994bc303356ca338f8/market_checker_app/app.py): poslední týdenní report.
- [RSS provenance](https://github.com/littleleg198602/JOHNY-SKORE/blob/a64d4de5677f04b4eae140994bc303356ca338f8/market_checker_app/collectors/rss_client.py), [news scoring](https://github.com/littleleg198602/JOHNY-SKORE/blob/a64d4de5677f04b4eae140994bc303356ca338f8/market_checker_app/analysis/news_analysis.py).
- [Řetězce a materiály — discovery](https://github.com/littleleg198602/JOHNY-SKORE/blob/a64d4de5677f04b4eae140994bc303356ca338f8/market_checker_app/services/filing_exposure_discovery_service.py), agents/supply_chain_agent.py, agents/commodity_energy_agent.py.
- [Týdenní workflow](https://github.com/littleleg198602/JOHNY-SKORE/blob/a64d4de5677f04b4eae140994bc303356ca338f8/.github/workflows/market-checker-live-smoke.yml), Spustit_Tydenni_Shadow.bat.
- [PR #102](https://github.com/littleleg198602/JOHNY-SKORE/pull/102), [PR #103](https://github.com/littleleg198602/JOHNY-SKORE/pull/103), [uzavřený PR #104](https://github.com/littleleg198602/JOHNY-SKORE/pull/104).
