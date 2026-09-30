# Průběh implementace pátracích agentů

## Hlavní analýza po 686 tickerech (30. 9. 2026)

Snímek uživatele ukazuje 686/686 tickerů, 97 % a „Spouštím auditní agentní
pipeline“. Řádek RSS 1/691 je starý ukazatel z předchozího vykreslení stránky;
RSS běží dříve než tickery. Auditní agenti dosud neposílali dílčí průběh,
takže i dlouhé sekvenční načítání SEC výkazů pro celý watchlist vypadalo jako
zastavené. Nový průběh ukazuje jméno agenta a u SEC počet procházených tickerů,
odstraňuje starý ukazatel při novém spuštění a ruší popisek posledního tickeru
po vstupu do další fáze. SEC HTTP 403 a dlouhý `Retry-After` zastaví další
SEC požadavky v tomto běhu, zaznamenají nedostupná data a pustí navazující
agenty dál. Tato změna sama nezrychluje legitimní jednotlivá SEC volání ani
nedokládá dokončení konkrétního uživatelského běhu na Windows.

## Automatické pokračování SEC fronty z UI (30. 9. 2026)

Na uživatelském snímku bylo po ručním kliknutí zpracováno pouze 25 úloh,
zbylo 1 062 `READY` a 24 `DONE`. Jedna úloha znamená index firmy nebo
navazující primární dokument, nikoli vždy jednu firmu. Dosavadní tlačítko
spouštělo právě jednu synchronní dávku 25 úloh; denní plánovač samostatně
zpracovával 100 úloh na spuštění. Proto UI po 25 samo nepokračovalo.

Tlačítko nyní spouští nezávislý proces pro celý ověřený seznam 687 tickerů.
Ten přidá jen dosud neznámé SEC kontroly (již hotové indexy z ruční dávky
znovu nezařadí) a pak automaticky vybírá další
25úlohové dávky až do vyčerpání právě připravené fronty. Aktuální stav,
počet zpracovaných úloh a chyb ukládá do SQLite; druhé kliknutí aktivní běh
neduplikuje. UI ukazuje zvlášť dokončené indexové kontroly tickerů a
počty všech úloh. Při `Retry-After`, HTTP 403 nebo pouze odložených
chybách skončí stavem `PAUSED`; neobchází omezení zdroje. Odložené položky
zachovává databáze a navazující denní plánovač. Pokud se PC nebo proces
ukončí, nedokončené lease lze obnovit, po hodině lze spustit nový worker.
Lokálně ověřeno na 60 tickerech přes hranici dávky 25, izolované chybě a
zámku proti dvojímu spuštění; skutečný běh na uživatelově Windows čeká.

Aktualizováno: 29. 9. 2026. Rozsah požadavku: SC-00 až SC-35 v `AGENT_SCOUT_IMPLEMENTATION_TASKS.md` na dokumentační větvi. `IMPLEMENTED` znamená existující kód, `TESTED` místní nebo CI test, `MERGED` spojení do main, `LIVE_VERIFIED` běh proti skutečnému zdroji. Základní PR #129 a navazující PR #130–136 jsou sloučené do `main`; poslední CI run 36599400943 prošel všemi čtyřmi úlohami. Provozní akceptace zatím chybí.

## Provozní audit 29. 9. 2026

Živý `main` run [36431270885](https://github.com/littleleg198602/JOHNY-SKORE/actions/runs/36431270885) z 28. 9. zpracoval 687 vstupů, ale skončil `FAILED`: 0 použitelných cen, 0 způsobilých pořadí a 0 nových snapshotů. Uložení signálů selhalo na SQLite `NAType`, QualityGate odmítl starší průběžnou evidenci. SC-00/01 jsou nyní v `main`, ale ještě nejsou živě ověřené po sloučení.

Další cenová závada je doložena stejným artefaktem: Yahoo stáhlo 685 řad; denní řady běžně obsahovaly rozpracovanou svíčku 28. 9. před uzavřením NYSE. Validátor kvůli tomu odmítl i předchozí platnou páteční cenu. Oprava v `main` odfiltruje nedokončené seance a stále vyžaduje poslední skutečně uzavřený close. Read-only replay cache pro kanonických 687 tickerů vrátil **684 použitelných cen**; LEG má zastaralou řadu, BRKB/PSTG zůstávají bez ceny. To je kontrola cenového validátoru, nikoli nový úspěšný běh pipeline. Regresní testy pokrývají rozpracovanou, pouze budoucí a zastaralou řadu. Nový živý 687tickerový běh je stále nutný pro provozní akceptaci.

SC-03 nyní ukládá každý ověřený CSV vstup jako neměnný snapshot se SHA-256, původním pořadím a všemi řádky; neschválenou změnu CSV ukáže po pozicích a sběr nespustí. Lokální první běh a restart vytvořily právě jeden snapshot a 687 řádků. Přímé porovnání s původním `market_checker_20260818_213623.xlsx` (list `Signals`, sloupce `ticker`/`yahoo_ticker`) potvrdilo **687/687 shod ve stejném pořadí, 0 rozdílů**. SHA-256 tohoto XLS je `065444437863dbb65e65f5c97b0611545b0ceaa8c4ed778f5f9a68c874480f8b`. OKE je již ve zdrojovém XLS na pozici 177. Datovaná identita instrumentů a aliasy patří do SC-04 a zůstávají otevřené.

První CI po opravě cen odhalilo dvě časově křehké RSS testovací fixture: článek s pevným datem 1. 7. 2026 po uplynutí 90denního okna správně vypadl ze sběru. Fixture nyní používají včerejší datum; kód RSS ani 90denní pravidlo se nemění. Poslední CI PR #129 prošlo.

Navazující SC-04 změna v PR #130, sloučeném jako `b02f80b`, ukládá pozorovaný přesný SEC ticker/CIK do samostatné historie. Při změně CIK pro stejný ticker uloží kandidáta do karantény a odmítne nové nálezy; při migraci čte i starší SEC nálezy bez této tabulky. GOOG a GOOGL mohou sdílet CIK, ale mají oddělené tickerové řádky. CI run 36564820357 prošel. Úplné časově platné aliasy, dcery, značky a produkty zůstávají otevřené.

SC-05 v PR #131, sloučeném jako `50574d5`, převádí všech 39 textových profilů na stabilně pojmenované kandidátní otázky metrik a zdrojů (`research-v1:profil:druh:pozice:otisk_popisu`). Pravidlo jiného profilu vrací `NOT_APPLICABLE`; podmíněný zdroj u vlastního profilu je pouze `CANDIDATE`, dokud není doložena konkrétní dcera, produkt nebo segment. Pravidla dosud nejsou napojena na skóre ani plánovač providerů; úplné ověření více segmentů zbývá.

SC-06 v PR #132, sloučeném jako `ecb724f`, doplňuje časovou invariantu ukládaného nálezu: zveřejnění nesmí nastat po deklarované dostupnosti, dostupnost nesmí nastat po prvním pozorování. Nález s budoucím datem publikace se odmítne před zápisem a nevstoupí do historického replay. CI run 36566202962 prošel.

SC-07 v PR #133, sloučeném jako `2266772`, přidává na společné hranici uložení politiky dosud zapojených zdrojů: SEC citace musí být na přesném oficiálním HTTPS hostu a RSS zůstává `UNVERIFIED`; soukromá IP, podvržená SEC subdoména a neznámý provider jsou odmítnuté. CI run 36596878077 prošel. Oprávnění a kvóty budoucích konektorů zbývají.

SC-09 v PR #134, sloučeném jako `a0dd967`, byl ověřen průchodem skutečné SQLite fronty všech 687 tickerů bez síťových volání: 100/den, restart databáze mezi dny a jeden den výpadku PC. Po šesti provedených dávkách je zpracováno 600 různých firem, po sedmé všech 687. Již existující pořadí dle `due_at` zachovává backlog; CI run 36597493754 prošel. Profilové intervaly a fairness mezi budoucími providery zbývají.

SC-04 v PR #135, sloučeném jako `acccb63`, přijímá jen explicitní ticker aliasy s datem počátku, případným koncem a veřejnou citací. `resolve_ticker_identity_as_of` zohledňuje zvlášť čas znalosti a skutečnou platnost aliasu; překryv dvou instrumentů odmítá místo libovolného přiřazení. Metadata aliasu se účastní otisku verzované identity, takže pozdější oprava nezmění starý známý snapshot. Tento obecný mechanismus ještě není napojen do cenové a SEC scout cesty a neověřuje konkrétní P/PSTG či LEG/SGI bez primárního zdroje.

SC-14 v PR #136, sloučeném jako `5ab138f`, omezuje následné otázky: nová úroveň musí citovat jiný, nově pozorovaný `SOURCE_VERIFIED` nebo `CLAIM_VERIFIED` nález než rodič a jeden rodič má nejvýše tři přímé podotázky. Transakční zámek chrání limit proti souběžným běhům; již existující limit hloubky zůstává dvě úrovně. CI run 36599400943 prošel. Celkový rozpočet per firma/den/provider a verziované spouštěče ještě chybí.

Navazující pátrání z hloubkového výzkumu: existující `FilingExposureDiscoveryService` již v omezeném týdenním analytickém běhu extrahoval anonymní koncentraci dodavatelů/zákazníků a zmínky o materiálech. Nový SEC scout jej nyní volá také po stažení primárního 10-K/10-Q/20-F/40-F. Ukládá citovaný úsek, otisk dokumentu a otevřenou otázku; pro firmu nevyvozuje identitu anonymního dodavatele, skutečný nákupní koš, nezveřejněný hedge ani dopad na marži. Průchod je omezen šesti supply a dvanácti commodity kandidáty na podání a 500 000 znaky textu. Další kroky výzkumu: úplnější extrakce 10-K příloh, identitní graf protistran, datované vztahy, EIA/USDA/USGS řady a prokázané napojení expozic na ceny. Živé ověření stále chybí.

Další inkrement veřejné extrakce rozlišuje citovaný právní název dodavatele od identifikované entity: `NAMED_ONLY` nezakládá ověřené CIK/LEI ani health rating. U jediné komodity ukládá výslovně uvedené procento hedge a období; u více komodit v jedné větě procento nepřiřadí. Tyto kandidáty stále nepromítá do skóre a před produkčním použitím vyžadují test na skutečných SEC podáních.

Pojmenovaný dodavatel může získat doplňkový `sec_catalog_match` jen při jediné přesné shodě celého právního názvu v aktuálním SEC katalogu (share classes se stejným CIK lze sloučit). Zaznamenává se CIK, tickery, odkaz na katalog a skutečný čas pozorování. Jméno zůstává `NAMED_ONLY` pro historické tvrzení, dokud není datovaně doložena platnost a vztah případných dcer; podobné názvy se nespojují.

EIA spot ceny WTI a Gulf Coast jet fuel mají volitelný společný sběr se dvěma různými jednotkami a vlastní historickou verzí prvního pozorování. Bez `JOHNY_SKORE_EIA_API_KEY` hlásí `WAIT_ACCESS`; do skóre ani firemní ceny se nepřevádějí. Streamlit je ukazuje odděleně od SEC nálezů. Živý test zdroje čeká.

USAspending má nový konektor na prime zakázky A–D: za posledních 730 dnů hledá pouze pro datovaně doložené ticker–UEI vazby v `market_checker_app/data/verified_usaspending_uei.json`, ověřuje přesnou shodu UEI v každém výsledku a ukládá částku zakázky a výdaje odděleně s verzí pozorování. Pro firmy bez doloženého UEI nepředstírá nulové zakázky. Maximum tří stránek na UEI signalizuje `PARTIAL`; období před oknem a úplné modifikace nejsou pokryté. Další úkol je doložit vztahy dalších příjemců a dcer primárními citacemi, doplnit registr a ověřit živý pozitivní i negativní případ. Název firmy nebo ticker nejsou důkazem vazby.

První doložený záznam registru: LMT → Sikorsky Aircraft Corporation, UEI `UTJWTSLMFNG4`; citace konkrétní zakázky USAspending, SEC 2015 10-K s datem akvizice a SEC 2025 Exhibit 21 s pokračujícím zařazením dcery. `known_at` odpovídá 30. 9. 2026 12:15 UTC. Nyní je možné hledání pro toto jediné UEI bez klíče; živé ověření API stále chybí. Ostatní příjemci ani dcery nejsou automaticky potvrzeni.

PR #141 prošlo čtyřmi CI úlohami a bylo sloučeno. Navazující dohledávání příjemců automaticky zkouší přesný název aktivně pozorovaného SEC emitenta v oficiálním USAspending recipient endpointu. Ukládá až 100 jmen na běh s 30denním opakováním a max. dvě stránky; nalezená UEI jsou `UNVERIFIED` kandidáti `NAME_ONLY`, viditelní v samostatné sekci. Chybějící datovaný právní vztah je explicitní a kandidáti se automaticky nepřesunují do potvrzeného registru ani do skóre. Živé API ověření čeká.

| Body | Aktuální stav | Důkaz a zbývající práce |
|---|---|---|
| SC-00 | MERGED, TESTED | Nullable `pd.NA` rank ukládá do SQLite NULL, skutečná transakce v testu. Čeká živý běh. |
| SC-01 | MERGED, TESTED | 53minutový běh: důkaz z počátku i konce je platný; opravdu budoucí údaj je odmítnut. Ještě živý Windows běh. |
| SC-02 | PARTIAL | Opravy jsou v main, ale Windows spouštění po sloučení nebylo ověřeno. |
| SC-03 | MERGED / OFFLINE_VERIFIED | CSV drží 687 řádků, pořadí a SHA-256; ScoutStore archivuje neměnné verze a ukazuje změnový diff. Zdrojové XLS se shoduje 687/687. Provozní ověření na Windows ještě neproběhlo. |
| SC-04 | MERGED / PARTIAL | Oddělené identity emitenta/instrumentu, SEC změna CIK v karanténě a datovaný alias s fail-closed as-of lookupem. PR #135 / CI 36598477993. Napojení aliasu do sběru, doložené mapování sporných tickerů, dcery, značky a produkty zbývají. |
| SC-14 | PARTIAL | Dvě úrovně, jiný nově pozorovaný ověřený důkaz před další úrovní a max. tři děti na případ. Čeká širší denní/provider rozpočet a ověřené spouštěče. |
| SC-05 | MERGED / PARTIAL | 39 profilů má verzované metrické a zdrojové otázky; cizí profil je `NOT_APPLICABLE`, vlastní je pouze `CANDIDATE`. Doložení segmentů a zapojení do plánování čeká. PR #131 / CI 36565597107. |
| SC-06 | PARTIAL | Nález ukládá původ, locator, časy a stav ověření; navazující ochrana odmítá publikaci po dostupnosti. Chybí sjednocení kontraktu dalších specialistů. |
| SC-07 | PARTIAL | Úložiště povoluje pouze SEC a RSS nálezy podle zdrojové politiky; SEC host je přesný, RSS kandidát nesmí získat stav ověření pouhým ingestem. Kvóty, retence a licence dalších zdrojů zbývají. |
| SC-04–07 | PARTIAL | 39 profilů výzkumu je strojově čitelných, verzovaných a ověřených proti produkčnímu seznamu; `UNKNOWN` není chyba ani záporný bod. Výzkum uvádí `P`, ale skutečné CSV místo něj obsahuje `OKE`; P zůstává pouze výzkumný neprodukční řádek. `OKE` má oddělenou doloženou profilovou opravu `OIL_GAS` z oficiálního ONEOK a SEC 10-K, bez přepsání CSV. Profilové metriky zatím neřídí skóre/konektory. SEC konektor odmítá cizí hosty/cesty i přesměrování, RSS ukládá jen kandidátní veřejné HTTPS odkazy. Úplné identity a pravidla budoucích poskytovatelů čekají. |
| SC-08 | MERGED, TESTED | SQLite fronta, dedupe, lease token, zámek transakce, historie pokusů, restart; test končícího lease. Schéma nyní eviduje verze 1–5. |
| SC-09 | PARTIAL / OFFLINE_VERIFIED | Denní Windows plánovač a týdenní dávka, max. 100 firem/den. Sedm restartovaných dávek s výpadkem dne obsáhne 687/687 bez vyhladovění. Chybí profilové intervaly a fairness pro další providery; Windows neověřen. |
| SC-10 | PARTIAL | SEC client používá limit a retry včetně `Retry-After`; dlouhý požadavek i HTTP 403 zastaví dávku a uloží cooldown poskytovatele do SQLite. Fronta odděluje zdroje a obnovuje vlastnictví SEC během dávky. Chybí obecný circuit breaker pro ostatní zdroje. |
| SC-11 | IMPLEMENTED, TESTED lokálně pro scout | `findings_as_of` a `ScoutIndexAgent` odmítají data nedostupná před cutoffem; pro každou orchestraci s nálezem ukládá neměnný cutoff a přesný seznam použitých finding IDs. Ostatní vrstvy analýzy mají vlastní existující snapshoty. |
| SC-12–16 | PARTIAL | SEC index i omezený primární dokument mají samostatný otisk, URL/CIK/accession kontrolu, skutečnou dobu pozorování a otevřenou otázku; 8-K parser vytváří citované sekce Item do hloubky 1. RSS zprávy už vstupují jako časově omezené `UNVERIFIED` stopy s pouhým ticker hintem a otázkou na primární zdroj, nikdy do skóre; rozpočet jsou dva bezpečné unikátní odkazy na ticker a běh. Stavový cyklus leadů má časovou historii; závěr `VERIFIED`/`CONTRADICTED` vyžaduje zvláštní claim-level ověřený důkaz. Další primární zdroje a automatické ověření tvrzení chybí. |
| SC-17 | TODO | Bez schváleného poskytovatele a rozpočtu; deterministická cesta běží bez AI. |
| SC-18 | PARTIAL | Nová lehká cesta na SEC index bez XBRL downloadu a omezený primární dokument nejnovějšího podání z každé rodiny formulářů; existující fundamentální agent čte filings/XBRL. Potřebuje úplné historické pokrytí a skutečné živé ověření. |
| SC-19–30 | PARTIAL | Existují analytické agentní moduly a SEC/RSS vstupy. FRED makro a EIA spot ceny mají volitelné sdílené konektory s evidencí revizí a konzervativním časem prvního pozorování; potřebují klíče a živé ověření. Nejde o cenu konkrétní firmy a nemění skóre. FINRA, USAspending, FDA a FDIC čekají na bezpečnou vazbu primárního identifikátoru na emitenta/instrument. Form 4 zatím nerozlišuje obchod od vestingu. |
| SC-31 | PARTIAL, TESTED lokálně | Index i primární dokument předá `ScoutIndexAgent` do `SourceResolutionAgent` a `QualityGateAgent` bez změny skóre; test celé orchestrace. Neměnný scout as-of bundle se seznamem použitých ID je uložen a lze jej znovu načíst; další specialisté čekají. |
| SC-32–33 | PARTIAL | Streamlit ukazuje poslední SEC index/dokument, oddělené neověřené RSS stopy, otevřené i uzavřené otázky s citovanými finding IDs, chyby zdroje a další pokus, stav fronty i tlačítko dávky; Excel má při použitých nálezech list `ScoutEvidence` s as-of cutoffem a citacemi. Windows .bat a volitelná denní úloha; instalátor jednou vyžádá kontaktní e-mail SEC. Chybí skutečné ověřené odpovědi a provozní Windows zkouška. |
| SC-34 | TODO | Žádné nové feature zatím nevstupují do predikčního skóre; vyhodnocení přínosu začne až s podklady a dokončenými výsledky. |
| SC-35 | PARTIAL | Místní testy, kompilace a zkouška plánování všech 687 bez přístupu k SEC. Chybí live known-positive SEC, Windows a end-to-end kontrola 48/687 tickerů. |

Nepoužívat sloučení základního PR jako potvrzení dokončení všech 36 úkolů. Bez reálného SEC User-Agent se automatický sběr vrátí `WAIT_ACCESS`, nepředstírá nalezené podání. Zdroje s klíčem/licencí se nezapojují bez odpovídajícího přístupu. Žádné order API není přidáno.

GitHub CI základní verze a navazujících PR #130–136 prošlo. Živá akceptace Windows/SEC/cen po sloučení stále chybí. Výzkumné `P` nesmí automaticky nahradit produkční `OKE` ani se bez ověřeného časového aliasu sloučit s `PSTG`.
