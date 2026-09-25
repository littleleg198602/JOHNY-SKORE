# Market Checker: přesný seznam úkolů pro pátrací agenty

Datum: 25. 9. 2026. Stav: implementační backlog; žádný níže uvedený bod není tímto dokumentem prohlášen za hotový. Navazuje na `MARKET_CHECKER_RESEARCH_IMPLEMENTATION_PLAN.md` ve stejné větvi. Aktuální `main` používá sekvenční `OrchestratorAgent` pro analytický běh a `SourceDiscoveryService` pro klasifikaci již nasbíraných RSS položek. Pátrací sběr proto vznikne jako samostatný trvalý proces před analytickým během; existující agenty a tabulky rozšíříme a využijeme.

## Jednotná definice dokončení

Každý úkol má testovanou cestu **skutečný zdroj → správná identita a datum → uložený důkaz → existující agent → běžný report**, pokud se týká sběru. Parser, HTTP 200 nebo naplněná cache samy o sobě nestačí. V provozu se zvlášť evidují `IMPLEMENTED`, `TESTED`, `MERGED`, `LIVE_VERIFIED` a důvody `WAIT_ACCESS`/`WAIT_DATA`. Žádné automatické obchodování. Analytické údaje o firmách se nevyplňují ručně; jednorázové nastavení oprávnění a klíčů je dovoleno.

## 0. Nutné opravy před nasazením sběru

- [ ] **SC-00 SQLite NULL:** opravit serializaci pandas/numpy chybějících skalárů, aby `pd.NA`, `NaT` a nullable rank nevyvolaly chybu zápisu. Ověřit skutečnou transakci a rollback v integračním testu. Vazba MC-00.
- [ ] **SC-01 Časové kontroly:** oddělit stáří signálu od stáří dlouhodobého důkazu a čerstvosti sběru; budoucí čas posuzovat proti skutečné době kontroly. Ověřit celý 53minutový běh, starý výkaz, později získaný důkaz a skutečně budoucí záznam. Vazba MC-00.
- [ ] **SC-02 Doručení:** před zaváděním dalších agentů ověřit, že SC-00/01 jsou součástí cílové větve a běžného Windows spouštění; samotný minulý lokální commit nestačí. Vazba MC-00.

## 1. Páteř pátrání

- [ ] **SC-03 Neměnný vstup:** uložit všech 687 vstupních řádků, původní pořadí a hash; při změně ukázat rozdíl, ne přepisovat výchozí seznam. Test: 687 auditních řádků i při nevyřešené identitě. MC-01.
- [ ] **SC-04 Identity:** rozšířit `EntityRegistryAgent` a registry o identifikátor cenného papíru, listing, emitenta, časově platné ticker aliasy, dcery, značky a produkty. Ověřit P/PSTG, LEG/SGI, BRKB a samostatné ceny GOOG/GOOGL. Nejasné mapování uložit do karantény. MC-01/11.
- [ ] **SC-05 Profily:** převést 39 profilů výzkumu na verzovaná pravidla zdrojů a metrik, včetně firem s více segmenty. Test: banka nedostává výrobní marži, irelevantní údaj `NOT_APPLICABLE`, žádný záporný bod za chybějící irelevantní zdroj. MC-02.
- [ ] **SC-06 Kontrakt nálezu:** sjednotit identitu objektu, typ nálezu, hodnotu/jednotku, zdroj, locator/citaci, zveřejněno, dostupné, poprvé pozorováno, staženo, stav ověření a parser/verzi. Novější oprava zdroje nevypíše starý snapshot. MC-03.
- [ ] **SC-07 Zdrojová pravidla:** pro každý konektor nastavit povolené domény/API, kvóty, rozsah ukládání, zveřejnění, retenci a případné použití externí AI. Tajné klíče neukládat do běžného runtime JSON, logu ani commitu. MC-03.
- [ ] **SC-08 Trvalá fronta:** nová verzovaná SQLite migrace pro `scout_jobs` (`source`, `subject_id`, `reason`, `priority`, `due_at`, `cursor`, `attempts`, `status`, `lease_until`, `last_error`, `dedupe_key`) a historii pokusů; unikátní klíč zabrání duplicitám. Test: crash/restart a dva souběžné runnery. MC-04.
- [ ] **SC-09 Plánovač:** denní inkrementální, týdenní catch-up, jednorázové zpracování události, dohledání mezer a spravedlivé rozdělení kapacity mezi 687 vstupů. Nastavit interval podle zdroje a relevance profilu; výpadek PC doplní backlog. MC-04.
- [ ] **SC-10 Síťová politika:** limity a retry po poskytovateli, `Retry-After`, stránkování a checkpoint, bounded backoff, timeout, circuit breaker. Rozlišit 403/429/timeout/změnu schématu; jeden zdroj nezastaví ostatní. MC-04.
- [ ] **SC-11 Identita běhu:** archivovat `as_of` analytického běhu a seznam použitých evidence IDs; analýza čte jen důkazy dostupné před cutoffem, sběr může mezitím pokračovat. Test: nově zveřejněný údaj se nedostane do historického replay. MC-03/22.

## 2. Co přesně dělá „čmuchání po stopě“

- [ ] **SC-12 Discovery:** ze zdrojového feedu/API/IR stránky vytvořit kandidátní dokument nebo událost. Název/ticker ve vyhledání smí vyvolat další kontrolu, ne potvrzení vazby. Pro kandidáta uložit dotaz, výsledek a důvod. MC-09/10.
- [ ] **SC-13 Sledovaný případ:** zavést `scout_leads` (otázka, subjekt, hypotéza, důkazy pro/proti, stav, priorita, due_at, nadřazený lead) a odkazy na úlohy. Stavový cyklus `OPEN → INVESTIGATING → VERIFIED / CONTRADICTED / INSUFFICIENT_DATA / EXPIRED`. Každý lead má dohledatelný původ. MC-03/22.
- [ ] **SC-14 Navazující otázky:** ověřený spouštěč založí nejvýše dvě úrovně podúloh podle verziovaných pravidel. Například růst zásob + komentář vedení → sektorová poptávka a ceny; před další úrovní musí existovat nový důkaz. Limit per lead, firma, den a provider. MC-04/22.
- [ ] **SC-15 Párování a deduplikace:** jedna událost sdílená v deseti článcích zůstává jedinou událostí, původní zdroje jsou zvlášť. Vazby dcer a vztahů musí být datované; protichůdná tvrzení uchovat, nepřepisovat silnějším. MC-01/03.
- [ ] **SC-16 Ověřovatel:** původní výkaz, oznámení regulátora nebo firmy versus sekundární zpráva; skutečné datum a relevance k firmě; případná odpověď emitenta. Jen ověřené a správně načasované údaje mohou měnit analytické metriky. Slabší stopa je vidět jako neověřená otázka. MC-03/22.
- [ ] **SC-17 Řízená AI extrakce:** nejprve deterministické API/parsery. Volitelný extraktor dlouhého textu vrací strukturovaný návrh a přesnou citaci; validátor ověří schéma, identitu a čas. Model nesmí svévolně přidávat domény, přeskočit kvóty ani spočítat číselné metriky bez vstupů. Pokud není povolený klíč/rozpočet, běží deterministická větev. MC-22.

## 3. Specialisté a konkrétní připojení

| ID | Úkol agenta | Vstupy, důkaz a akceptace | Navázání |
|---|---|---|---|
| SC-18 | Výkazy/výhled | SEC filings, přílohy, XBRL včetně amendments a období; najít novou informaci i mimo recent blok. Sdílet pod emitentem. | současný `SecFundamentalsAgent`, MC-05 |
| SC-19 | Forenzní stopa | Změny výkazů, auditora a neobvyklé účetní položky; původní věta/řádek a kontradikce, historický cutoff. | `FinancialForensicsAgent`, MC-05/17 |
| SC-20 | Firemní události | Ověřené IR domény, SEC a RSS, oznámení/dokončení/zrušení. Duplicitní článek nepřidá další hlas. | `GovernanceEventAgent`, MC-09 |
| SC-21 | Vlastníci/insider | Form 4, 13D/G a manager 13F, správná třída a okamžik zveřejnění. Odlišit nákup/prodej od vestingu a daní. | `GovernanceEventAgent`, MC-07 |
| SC-22 | Short | Původní short report, ověřovaná tvrzení a odpověď emitenta; FINRA periodický short interest odděleně od denního short volume. | `ShortReportAgent` + `ClaimVerificationAgent`, MC-08/10 |
| SC-23 | Dodavatelé/zákazníci | Texty a tabulky filingů: závislosti, koncentrace, výpadky a smlouvy. Anonymní zákazník bez dokladu zůstává anonymní. | `SupplyChainAgent`, MC-11/17 |
| SC-24 | Zakázky | USAspending a případně SAM, recipient/UEI → doložená dcera; `obligation` není hodnota celé smlouvy ani tržba. | `RegulatoryContractAgent`, MC-12 |
| SC-25 | Zdravotnictví | FDA, ClinicalTrials a CMS podle profilu; mapování NCT, produktu, žadatele, plánu a dcery. | `RegulatoryContractAgent`, MC-13 |
| SC-26 | Banky | FDIC/FFIEC/Fed podle profilu; CERT/RSSD a CIK rozlišit, správné období a revize. | `SecFundamentalsAgent` + profil, MC-14 |
| SC-27 | Náklady/makro | FRED/ALFRED, EIA a vybrané USDA/USGS/Census řady; jednotky, vintage a důkaz přenosu nákladů/hedge. | `CommodityEnergyAgent`, MC-15/17 |
| SC-28 | Regulace | OFAC/DOJ/FTC/BIS a sektorová EPA/NHTSA/FCC/FERC/FAA; doložená vazba na produkt/provoz/dceru a stav řízení. | `RegulatoryContractAgent`, MC-16 |
| SC-29 | Protistrany | Jen skutečně nalezené protistrany, veřejné výkazy a registry; soukromá firma může mít nedostatek údajů. | `SupplyChainAgent`, MC-18 |
| SC-30 | Tržní očekávání | Předvýsledkové vintage konsenzu, případně opce/borrow až při potvrzeném pokrytí a oprávnění. Chybějící placený zdroj má `WAIT_ACCESS`. | nové adaptéry, MC-19/20/21 |

Každý specialista otevírá lead pouze pro relevantní firmu/profil. Konektory a analytičtí agenti mají odlišnou úlohu: provider dodává primární fakta, agent z nich vytváří otázky a report. Společné makro, SEC dokument či emitent se stahuje jednou; samostatné cenové řady akciových tříd zůstávají oddělené. Cenová vrstva a corporate actions z MC-06 jsou nutný podklad pro následné vyhodnocení.

## 4. Integrace do analýzy, obrazovky a kontroly přínosu

- [ ] **SC-31 Evidence adapter:** sestavit verzovaný as-of bundle z uložených nálezů a leads; připojit do `PipelineService` před analytickým `OrchestratorAgent`. `SourceResolutionAgent` spojí výsledky, `QualityGateAgent` ověří stáří a konflikty. Výpadek volitelného konektoru nezablokuje celé skóre. MC-22.
- [ ] **SC-32 Report:** pro firmu „co je nového“, nejvýznamnější změny, otázky v pátrání, důkazy pro/proti, zdroj, datum, chybějící data a důvod nedostupnosti. Rozlišit vyhledanou stopu a potvrzený závěr. MC-24.
- [ ] **SC-33 Ovládání bez CLI:** tlačítko „Aktualizovat podklady“, status fronty, datum posledního úspěchu, zapnutí specialistů, jednorázová konfigurace a stávající Windows spouštěče/týdenní runner. Streamlit rerender nesmí spouštět nový sběr. MC-04/24.
- [ ] **SC-34 Shadow a vyhodnocení:** všechny nové feature skupiny ukládat jako verzované a zpočátku jen do reportu; walk-forward a ablation se stejným targetem/cutoffem, oddělenými vzorky a baseline. Do skóre až po doloženém přínosu. MC-22/23.
- [ ] **SC-35 Akceptace:** pilotních 48 vstupů z výzkumu, pak audit všech 687; skutečné known-positive nálezy u každého podstatného typu zdroje, falešná identita, duplicita, schema change, 429, dlouhý běh, crash/restart, historický replay, výpadek PC. Živá Windows zkouška odděleně od místních testů; nulové volání order API. MC-24.

## Implementační balíky

1. **Zprovoznit základ:** SC-00 až SC-07. Výstup: opravená analýza, identity a společná definice ověřeného nálezu.
2. **Spustit skutečné pátrání:** SC-08 až SC-16 plus SC-18 a SC-20. Výstup: obnovitelná fronta, SEC/IR stopy, navazující otázka a dohledatelná odpověď.
3. **Dodat to do aplikace:** SC-31 až SC-33 a pilotní část SC-35. Výstup: uživatel vidí nález v běžné analýze bez CLI.
4. **Rozšiřovat specialisty:** SC-19, SC-21 až SC-30 podle dostupnosti primárních dat a relevance profilů; společné testy a viditelné `WAIT_ACCESS`.
5. **Ověřit účinnost:** SC-34 a úplné SC-35. Každá nová skupina dat má samostatnou kontrolu pokrytí, časové správnosti a přínosu pro predikci.

První demonstrace končí skutečným dokumentem SEC nebo IR: automaticky nalezeným, správně přiřazeným firmě, uloženým s datem a citací, prověřeným na navazující otázku a zobrazeným v běžném reportu. Samotná simulace úspěchu úkol neuzavírá.
