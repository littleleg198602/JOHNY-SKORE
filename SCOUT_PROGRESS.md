# Průběh implementace pátracích agentů

## Živé veřejné zdroje a opravy jejich parserů (1. 10. 2026)

V `evidence/specialist_live_20261001.json` je 13 skutečných kontrol:
čtyři kladné FDIC banky (po dvou výkazech), záporný neexistující CERT,
FDA drug kandidát, tři záporné enforcement dotazy, CRL schéma a záporný
CRL dotaz, kladné modelové svolání NHTSA a odmítnutí neplatného modelu.
Opakované zpracování FDIC/NHTSA nevytvořilo duplicity. Jde o omezený
zdrojový test na aktuálním hostiteli, nikoli o Windows akceptaci nebo
pokrytí všech 687 firem. Inventář proto zůstává PILOT/PARTIAL.

Živý FDIC vrací finanční názvy jako `BANK OF AMERICA NA`, nikoli celý
právní název z SEC. Registr proto obsahuje přesný název doložený API
pro shodný CERT, citaci a čas prvního pozorování. Dřívější historický
běh tento alias nepoužije. Odmítnuté řádky jsou viditelné jako PARTIAL
a zvlášť od použitelné banky.

NHTSA živě vrátila `18/06/2025`: parser nyní důsledně používá
den/měsíc/rok, ponechává datum prvního pozorování oddělené a odmítnutý
záznam znovu nabídne po dni. Test fronty již nevolá živé API při
ověřování neměnnosti seznamu; unit suite nezávisí na těchto službách.

Připomínku k openFDA tempu jsme ověřili proti oficiálnímu
`https://open.fda.gov/apis/authentication/`: aktuálně uvádí 240/min
s klíčem i bez něj, bez klíče 1 000/den. Odstup 0,35 s proto zůstává.

## Denní kapacita FDA/FINRA pro 687 tickerů (1. 10. 2026)

Denní plánovač má nyní rozpočet 40 FDA a 75 FINRA emitentů. Při úplných
odpovědích a 687 doložených SEC identitách je první oběh nejvýše 18,
respektive 10 denních spuštění; tím se vejde do obnovovacích oken 30 a
15 dní. Pořadí ukládá SQLite a další den pokračuje u dalších firem.
FDA klient rozkládá požadavky nejméně o 0,35 s; nejvýše 40 × 4 endpointy ×
2 stránky znamená 320 požadavků za den, pod veřejným denním limitem 1 000.
Skutečné pokrytí může zdržet výpadek, 429, chybějící identita, neúplná
odpověď či nepřítomné FINRA přihlašovací údaje; zobrazuje ho UI.

## Stránkování openFDA (1. 10. 2026)

FDA pro drug/device/food recall a CRL nyní při překročení jedné stránky
načte druhou stránku pomocí oficiálního `skip`/`limit` (výchozí rozpočet
nejvýše 2 × 100 záznamů na endpoint a firmu). Úplnost je ověřena proti
`meta.results.total` a stránkovému offsetu. Přesah nebo nestabilní odpověď
zůstává `PARTIAL`; po dni lze s vyšším výslovným rozpočtem dohledat další
stránku. Kandidáti nadále nejsou důkazem vztahu produktu k emitentovi.

## Opakování částečných kontrol FDA/FINRA (1. 10. 2026)

Odpověď dosažená na limitu není úplná. Dávkový plánovač ji nabídne znovu
po dni, ale nejprve obslouží identity bez jakékoli kontroly a starší
kontroly. Opakování se stejným limitem stránek samo o sobě chybějící
stránky nedohledá; takový výsledek zůstává `PARTIAL`. Přehled pokrytí
ukazuje skutečné intervaly: FDA 30 dní a FINRA 15 dní.

## Citigroup / FDIC CERT 7213 (1. 10. 2026)

V datovaném registru bank přibyla vazba C na Citibank, National Association
(FDIC CERT 7213). Citigroup ji uvádí jako dceřinou společnost v SEC Exhibit
21.01 k 31. 12. 2025 a FDIC ji identifikuje pod tímto certifikátem. Scout
zprávu přiřadí až po ověření čísla certifikátu a přesného jména ve výsledku;
údaje jsou údaji dceřiné banky, nikoli konsolidovanou hodnotou akcie C.
Živé schéma BankFind a kladný/záporný běh stále čekají.

## Kumulativní pokrytí FDA a FINRA (1. 10. 2026)

UI vedle poslední dávky zobrazuje počet aktivních identit pozorovaných
v SEC a pro FDA/FINRA rozlišuje úplnou kontrolu v posledních 30 dnech,
neúplnou odpověď s limitem stránek, nikdy/stará data a historicky někdy
provedenou kontrolu. Kontrola pro starší nebo jinak pojmenovanou identitu
se k aktuální firmě nepřičítá. Jmenovatel není automaticky 687: dokud SEC
nepozoroval přesnou identitu všech tickerů, u zbývajících není z čeho
FDA/FINRA vyhledávání spouštět. Tato metrika nemění skóre ani nezastírá
neúplný výsledek jako negativní událost.


## FDA potravinové svolávací akce (1. 10. 2026)

Stejný omezený FDA scout nově dotazuje vedle léčiv a zdravotnických
prostředků také oficiální `food/enforcement.json`. I zde přijme pouze
shodu celého `recalling_firm` s pozorovaným SEC názvem; `recall_number`
je oddělené podle typu produktu a záznam zůstává `UNVERIFIED` bez datované
vazby konkrétního výrobku na emitenta. Potravinový profil tak dostává
samostatnou stopu, nikoli automatický závěr o dopadu svolání.


## FDA Complete Response Letters (1. 10. 2026)

FDA scout vedle drug/device svolávacích akcí vyhledává veřejné Complete
Response Letters v oficiálním openFDA `transparency/crl.json`. Každý dotaz
na název aktivně pozorovaného SEC emitenta znovu ověřuje celý `company_name`,
typ `COMPLETE RESPONSE`, číslo aplikace, soubor a platné datum dopisu. Dopis
je samostatný `UNVERIFIED` kandidát, bez automatického přiřazení produktu,
aplikace či ekonomického dopadu na akcii. FDA může historický dopis zveřejnit
později; `letter_date` se ukládá odděleně a dostupnost začíná nejdříve
skutečným prvním pozorováním. Pokud selže některý FDA endpoint, kontrola
emitenta se neoznačí za hotovou; limit odpovědi hlásí `PARTIAL`.
Lokální pozitivní a negativní testy prošly. Živý běh, datovaný
application/sponsor/product crosswalk a skutečné pokrytí zatím chybějí.

## Viditelný stav všech zdrojů (30. 9. 2026)

Denní runner nyní po dokončení zapisuje do SQLite poslední stav SEC, FRED,
EIA, USAspending, hledání příjemců, FDA, FINRA, FDIC, 13F a NHTSA včetně
počtu zkontrolovaných položek, nových nálezů a typu chyby. UI ukazuje i
zdroj bez nálezu nebo takový, který ještě neběžel. `WAIT_ACCESS` a
`WAIT_IDENTITY` se nepletou s nulovou událostí. Historie běhů zůstává pro
audit; při pádu před dokončením běhu zůstane vidět poslední dokončený záznam,
takže údaj není živý průběhový čítač. Denní Windows spouštěč při každém startu
načte také uživatelský FINRA Client ID a Secret bez jejich tisku do konzole.

## NHTSA modelové svolávací akce (30. 9. 2026)

Nový veřejný NHTSA API scout se po 30 dnech vrací k datovaně doloženému
modelu a ročníku. Pilot je TSLA → Tesla Model 3, modelový rok 2026, s citací
Tesla 2025 10-K a přesným porovnáním výrobce, značky, modelu a ročníku v
každém záznamu. Ukládá číslo kampaně, komponentu a textové shrnutí, odděluje
datum přijetí hlášení od prvního pozorování a nevyvozuje dopad na tržby ani
skóre. Měsíční dávka nejvýše deseti modelů zastaví další dotazy při 429/403.
Chybějící další produkty a ročníky znamenají neúplné pokrytí; skutečný živý
pozitivní/negativní běh API teprve musí projít.

## Citovaný seznam jmenovaných dodavatelů (30. 9. 2026)

SEC extraktor nově zachytí i výslovnou formulaci „we rely on suppliers such as
… for these cells“ a rozdělí uvedená jména bez domyšlené právní identity či
podílu nákupů. Pozitivní kontrola používá skutečnou větu z 2025 Tesla 10-K:
Panasonic a Contemporary Amperex Technology Co. Limited (CATL) jsou jmenováni
jako dodavatelé, viz
`https://www.sec.gov/Archives/edgar/data/1318605/000162828026003952/tsla-20251231.htm`.
Obě jména zůstávají `NAMED_ONLY`; „cells“ neoznačujeme bez kontextového
prokázání automaticky jako konkrétní komoditní nákupní koš. Navazující
identitní graf a zdravotní stav protistran stále vyžadují další důkazy.

## SEC 13F – čtvrtletní vzorek institucionálních držeb (30. 9. 2026)

Týdenní runner s platným SEC User-Agent zjišťuje nejnovější oficiální ZIP
dataset, stáhne ho nejvýše jednou pro stejnou verzi doloženého registru
instrumentů a z `SUBMISSION`/`INFOTABLE` vezme pouze přímé 13F-HR řádky
běžných akcií se shodným CUSIP a názvem. První datovaná vazba je AAPL →
`037833100` pro 2. kvartál 2026 (SEC 13(f) seznam, Schedule 13G a SEC
katalog tickerů). Uloží nejvýše 25 největších řádků na ticker, s identitou
manažera, podáním, reportovaným obdobím, počtem akcií a as-filed USD hodnotou.
Pokud je řádků více, stav je `SAMPLED`; absence ve vzorku není důkazem absence
institucionálních držitelů. Amendmenty, 13F-NT, opce a PRN se neinterpretují
jako přímá akciová držba. Historická dostupnost začíná skutečným pozorováním
datasetu, nikoli reportovaným kvartálem či pouhým filing date. Nálezy nemění
skóre ani neimplikují nákup/prodej během kvartálu. Zbývají ověřené instrumenty
dalších tickerů, rekonstrukce amendmentů a živé ověření staženého ZIP.

## FDIC bankovní specialista (30. 9. 2026)

K 1. 10. 2026 přibyla druhá přesná vazba: BAC → Bank of America,
National Association → FDIC CERT 3510. FDIC detail certifikátu a SEC
Exhibit 21 Bank of America Corporation k 31. 12. 2025 jsou uloženy v
`verified_fdic_banks.json`; `known_at` je až čas našeho zjištění.
Třetí je WFC → Wells Fargo Bank, National Association → CERT 3511,
doložený FDIC a SEC Exhibit 21 Wells Fargo & Company ke stejnému datu.
Nepřiřazují se další banky podle podobného názvu. Živé API a skutečné
schéma jeho finančních polí stále čekají na ověření.

Veřejný BankFind API konektor sleduje poslední dva čtvrtletní výkazy jen pro
ručně doložený vztah ticker → bankovní dcera → FDIC CERT. První záznam je
JPM → JPMorgan Chase Bank, National Association → CERT 628; registr cituje
FDIC detail instituce a SEC Exhibit 21 s dceřinou společností. Každý výsledek
musí mít shodný CERT i celý právní název, datum v době doloženého vztahu a
vlastní čas prvního pozorování. Aktiva, vklady, vlastní kapitál a čistý zisk
se ukládají jako tisíce USD banky, nikoli konsolidovaná data JPM; skóre se
nemění. Další banky potřebují samostatné datované vztahy. Živé API a skutečné
schéma polí ještě vyžadují ověření v provozu.

## Form 4 klasifikace (30. 9. 2026)

Governance agent rozlišuje přímý nederyvátový P/A či S/D obchod od
kompenzačních A/M/F a ostatních Form 4 transakcí. U neobchodních položek
nevyvozuje finanční hodnotu nákupu/prodeje z počtu a vykázané ceny.
Ověření právní identity a časová historická akceptace dalších insider
typů zůstávají otevřené.

## FINRA short interest – přístupový konektor (30. 9. 2026)

FINRA Consolidated Short Interest má samostatnou omezenou dávku do 25
aktivních SEC tickerů. Po zadání veřejného FINRA API Client ID a Secret
v prostředí systému (`JOHNY_SKORE_FINRA_CLIENT_ID`,
`JOHNY_SKORE_FINRA_CLIENT_SECRET`) používá oficiální OAuth a filtrovaný
dotaz podle přesného `symbolCode`. Datum vypořádání ukládá odděleně od
prvního skutečného pozorování; shodu samotného symbolu označí `UNVERIFIED`,
bez doloženého historického instrumentu nemění skóre. Bez přístupu je
`WAIT_ACCESS`; 401/403 a 429 zastaví další dotazy. Je nutné ověřit živé
schéma API, vazbu symbolu na konkrétní třídu akcie a čas publikace.

## FDA recall specialista – první veřejná cesta (30. 9. 2026)

Přidán omezený pravidelný dotaz na openFDA drug/device enforcement pro aktivní
SEC názvy emitentů. Najde jen celé shodné `recalling_firm`, uloží citaci
`recall_number` a produktový popis jako `UNVERIFIED` kandidáta, nikoli
prokázanou vazbu výrobku k akcii. Neznámý čas skutečného zveřejnění se
nepřepisuje datem reportu; pro historické použití je nejdříve okamžik našeho
pozorování. Zpracuje nejvýše 25 emitentů v běhu, po 30 dnech je znovu zařadí;
chyba zdroje neoznačí kontrolu za hotovou. Dalším krokem je doložený
produktový/sponsor crosswalk, další FDA události a živý pozitivní i negativní
test. Tato změna nemění predikční skóre.

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

## 1. 10. 2026 — CMS, ClinicalTrials a OFAC veřejný sběr

CMS hospital-all-owners a ClinicalTrials.gov nyní mají samostatné omezené
sběrače přesných organizačních/sponsor názvů. CMS vybírá jen 19 relevantních
zdravotních profilů, ClinicalTrials 49 PHARMA/MEDTECH; dvě stránky po 100
a nejvýše deset emitentů na běh. Časový rozpočet 60 sekund a tři chyby
zastaví další dotazy; nedokončené identity zůstávají splatné. Kandidáti
nedokládají vztah produktu, studie nebo nemocnice k emitentovi a zůstávají
UNVERIFIED. CMS neukládá osobní vlastníky ani jejich adresy.

OFAC stahuje oficiální SDN export jednou, ověřuje jeho strukturu a
unikátní ID a porovnává jen přesné primární organizační názvy. Živý CSV
obsahuje koncový DOS EOF, který parser nyní korektně přijímá pouze na
konci. Otisk snapshotu je uložený; cizí redirect není povolen. Absence
názvu neznamená sankční clearance: aliasy, Non-SDN a 50% vlastnická
analýza dosud nejsou zapojené. Nové zdroje jsou v denním runneru, UI,
politikách uložení i odděleném přehledu aktuálního pokrytí.

Uložené živé důkazy mají 19 PASS případů celkem: 13 FDA/FDIC/NHTSA,
čtyři CMS/ClinicalTrials a dva OFAC. Celá sada prošla: **456 testů**,
Linux/Python 3.12.14; příkaz, čas a otisky testovaného Python kódu jsou
v evidence/specialist_tests_20261001.json. Nejde o koncový Windows běh
ani prokázané identity a pokrytí všech 687 firem. Inventář zůstává
pravdivě PARTIAL/PILOT/NOT_STARTED; 0/21 DONE.

SPECIALIST_IMPLEMENTATION_CHECKPOINT_20261001.md uchovává další práci
pro všech 21 specialistů a konkrétní nevyřešené přístupy, identity a
akceptaci. Je nastavená kontrola budoucího úplného dokončení s oznámením
až po skutečně existujících důkazech; kontrola sama nepíše další kód.

## 1. 10. 2026 — USAspending živě a přenosná provozní diagnostika

Oficiální USAspending API prošlo čtyřmi novými případy. Dokumentovaný
Sikorsky UEI UTJWTSLMFNG4 uložil přes stávající collector 100 aktuálních
zakázek pro LMT; opakování stejného zachyceného payloadu nepřidalo duplicity.
První stránka měla hasNext, takže výsledek zůstává PARTIAL. Negativní
UEI, pozitivní recipient schema a negativní název prošly také. Úplný
payload, výsledek a scope jsou v evidence/usaspending_live_20261001.json.
Celkem je uložených 23 PASS případů sedmi veřejných zdrojů, nikoli úplná
akceptace 21 specialistů.

UI má tlačítko Stáhnout provozní přehled specialistů. Přehled skutečně
čte archiv vstupu, pořadí 687 řádků, SEC identity a dokončené indexy jen
v kanonickém scope, poslední běhy zdrojů a aktuální sektorové pokrytí.
NEVER_RUN neznamená nulové nálezy; částečná stránka zůstává částečná.
Exportuje systém/Python a jen boolean přítomnosti přístupových nastavení,
nikoli hodnoty klíčů, cesty nebo adresy vlastníků. Přehled neprovádí
externí volání, nevytváří záznam běhu a nepřepisuje DONE/VERIFIED.
I na Windows je to diagnostika, nikoli automatický důkaz koncového běhu.
Přítomný klíč není úspěšná autentizace. Uložení souhrnu příjemců už zachovává
checked/new/truncated/failed počty, které předtím propadávaly allowlistem.

Navazující testy ověřují prázdnou databázi na Windows bez falešného
potvrzení, poškozený archiv, cizí ticker, sektorový jmenovatel, zachování
PARTIAL/WAIT_ACCESS a neexportování přístupových hodnot. Předchozí celá
sada 456 testů a GitHub CI prošly; navazující kontrola má samostatný
čas a otisky kódu v evidence/specialist_acceptance_tests_20261001.json.
Inventář zůstává 0/21 DONE. Další dostupný vývoj i závislosti jsou otevřeně
uvedené v checkpointu; kontrola budoucího dokončení nenahrazuje vývoj.

Po uložení tohoto kroku byla původní kontrola dokončení rozšířena podle
uživatelského požadavku na automatické pokračování implementace. Hodinový
běh obnoví stav z GitHubu, provede další dostupný konkrétní krok, ověří ho
a uloží na pracovní větev/PR. Nepřepisuje souběžnou práci a nevymýšlí chybějící
důkazy; při skutečné blokaci pracuje na jiném dostupném úkolu a bez práce
nevytváří prázdný commit. Oznámení pošle až po skutečné akceptaci všech 21.
Nepovoluje automatický merge, nákup dat, zakládání účtů nebo obchody.
