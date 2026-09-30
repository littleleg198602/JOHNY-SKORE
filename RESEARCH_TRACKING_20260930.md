# Hloubkový výzkum: vazba na skutečný provoz

Zdroj: `Market_Checker_hloubkovy_vyzkum_2026-09-22.md`, kapitoly 1–20 a
implementační pořadí 01–28. Stav níže je kontrola kódu k 30. 9. 2026,
nikoli potvrzení úspěšného živého běhu. `ZÁKLAD` označuje existující
analytický modul, který nemusí mít automatický sběr pro všech 687 vstupů.

| Výzkumné body | Stav v projektu | Chybějící provozní cesta |
|---|---|---|
| 01–04 identita, důkazy, SEC, Windows | ČÁSTEČNĚ | 651 identit mimo pilot, aliasy do cen/scoutu, živý 687 běh a Windows test |
| 05 insider | ČÁSTEČNĚ: typy Form 4 | P/S non-derivative s odpovídajícím A/D jsou oddělené od A/M/F kompenzací a ostatních transakcí; 13D/G, 13F a historická akceptace zbývají. |
| 06 kontrakty | ČÁSTEČNĚ: USAspending scout | Doložená jedna vazba LMT → Sikorsky (`UTJWTSLMFNG4`); automatické dohledávání dalších UEI ze SEC názvu ukládá pouze kandidáty. Živý API běh a historie modifikací/obligations zbývají. |
| 07 vládní rizika | ČÁSTEČNĚ: NHTSA modelový scout | Doložený TSLA Model 3 2026 má oddělené kampaně NHTSA, bez automatického finančního závěru. OFAC/DOJ/EPA a další produkty vyžadují vlastní identitní vazby a provozní test. |
| 08 veřejná extrakce | ČÁSTEČNĚ | SEC scout nově ukládá omezené citované stopy dodavatelů a komodit z 10-K/Q/20-F/40-F; chybí přílohy, skutečná identita protistran a hedging |
| 09 IR a zprávy | ČÁSTEČNĚ | Ověřené IR feedy a propojení RSS stopy s primárním oznámením |
| 10 ceny | ČÁSTEČNĚ | Živá akceptace po opravě 28. 9., corporate actions a symboly BRKB/PSTG/LEG |
| 11 short interest | ČÁSTEČNĚ: FINRA kandidáti | Přesný symbol, settlement a revize s `UNVERIFIED` identitou; veřejné API vyžaduje FINRA účet/credential, live test, datovaný instrument crosswalk a skutečný čas zveřejnění zbývají. |
| 12 FDA | ČÁSTEČNĚ: recall kandidáti | Denní dávka do 25 aktivních SEC emitentů hledá drug/device enforcement záznamy a ukládá jen přesnou shodu celého názvu jako `UNVERIFIED`; produktový/sponsor crosswalk, CRL, živý test a plná sektorová fronta zbývají. |
| 13 vstupní náklady | ČÁSTEČNĚ | FRED makro a EIA WTI/jet-fuel spot mají oddělené volitelné konektory; čekají na klíče a live test. USDA, propojení firemních expozic s cenami a historické vintage chybí. |
| 14 entity enrichment | ČÁSTEČNĚ | GLEIF/OpenFIGI a dcery/produkty na doložených ID |
| 15 SAM | OTEVŘENO | Schválený klíč a reálná kvóta; doplnění USAspending |
| 16 instituce | ČÁSTEČNĚ: SEC 13F vzorek | Čtvrtletní ZIP parser bere jen přesně doložené CUSIP, prozatím AAPL `037833100`, a ukládá nejvýše 25 velkých původních 13F-HR řádků na ticker; nejsou to všechny instituce. Amendmenty, další instrumenty a živý test zbývají. |
| 17–21 placené piloty | NEZAPOJENO | Účet, datové oprávnění, coverage a měření přínosu před nákupem |
| 22–26 drahé/alternativní | NEZAPOJENO | Jen cílený pilot s oprávněním a měřením přínosu |
| 27 missingness | ČÁSTEČNĚ | Důvod mezery po prokazatelném hledání každého relevantního zdroje |
| 28 audit report | ČÁSTEČNĚ | Jednotný přehled coverage, výpadků a důkazů po živém běhu |

### Další posun veřejné extrakce

Extraktor nově umí omezený přesný vzor pro větu typu „We purchase [produkt]
from [právní název s Inc./Corp./LLC/Ltd.]“. Výsledek je `NAMED_ONLY`:
název a role jsou citovány, avšak právní identita/CIK/LEI ani dnešní vlastník
zatím nejsou prokázáni. Pojmenované věty dostávají přednost v omezené dávce
před opakovanými anonymními formulacemi. Výslovně uvedený podíl zajištění
jediné komodity a období se ukládají k důkazové stopě. U dvou komodit ve stejné
větě zůstává podíl nevyplněn. Obojí zůstává mimo predikční skóre. Přijetí
vyžaduje živé SEC dokumenty a širší evaluaci na různých firmách; ručně
zkonstruované testovací věty nejsou důkazem plošného pokrytí.

U pojmenovaného dodavatele SEC scout nově porovnává úplný právní název
s oficiálním aktuálním SEC katalogem tickerů. Povolí jediný CIK (více tříd
téže firmy je přípustných), zapíše citaci katalogu a čas pozorování. Podobný
název, dvě různé CIK či nedostupný katalog nedávají automatické propojení.
Tento aktuální katalog sám o sobě nedokazuje, že protistrana měla stejný CIK
nebo vlastnickou strukturu po celou historickou dobu vztahu. Další úkol je
datovaný identitní graf pro dcery a změny vlastníka, včetně doloženého vztahu
ke zdrojovému filingovému období.

## Co přesně dnes dělá extrakce dodavatelů a komodit

Starší `SupplyChainAgent` a `CommodityEnergyAgent` mají pravidla pro text
staženého SEC filingu, ale automatický týdenní běh má jen malý pilotní registr
identit. Nový `SecScoutService` po stažení primárního dokumentu používá stejný
konzervativní extraktor. Záznam obsahuje accession, citovaný úsek, otisk
dokumentu, skutečný čas pozorování a otázku k ověření. Anonymní dodavatel
zůstává anonymní; zmínka o mědi není změřená cenová citlivost firmy.

Další implementační pořadí: rozšířit ověřené identity a SEC dokumenty,
vyhodnocovat protistrany a produkt/segment po datovaných důkazech, přidat
EIA/USDA a USAspending, pak sektorové regulátory a FINRA. Každý zdroj musí
mít živý pozitivní i negativní test a vlastní coverage. Predikční skóre se
mění teprve po historickém vyhodnocení přínosu.

EIA konektor sleduje jednou pro celý seznam dvě konkrétní denní spot řady:
WTI Cushing (`RWTC`, USD/barel) a US Gulf Coast jet fuel
(`EER_EPJK_PF4_RGC_DPG`, USD/galon). Vyžaduje bezplatný osobní EIA API klíč
v uživatelské proměnné Windows `JOHNY_SKORE_EIA_API_KEY`; pokud chybí, výstup
hlásí `WAIT_ACCESS`. Období řady není čas vydání hodnoty, proto historická
dostupnost začíná až naším prvním pozorováním. Revize hodnot se verzují. UI
ukazuje tyto ceny jako sdílené tržní proxy, ne firemní nákupní ceny. Živá
kontrola EIA zatím neproběhla.

## USAspending: přesný identifikátor před přiřazením firmě

`market_checker_app/data/verified_usaspending_uei.json` je verzovaný registr
doložených ticker–UEI vztahů. Konkrétní záznam musí
mít `ticker`, `uei`, `recipient_name`, `uei_evidence_url`,
`relationship_evidence_url`, `effective_from`, případně `effective_to`, a
`known_at` s časovou zónou. Lze použít vlastní cestu přes
`JOHNY_SKORE_USASPENDING_UEI_FILE`. První citace dokládá UEI příjemce,
druhá datovaný vztah právnické osoby k emitentovi. Shoda názvu či tickeru
sama nestačí; různé pobočky Lockheed Martin mají různá UEI. Pokud registr
neobsahuje způsobilý vztah, runner vrací `WAIT_IDENTITY`, ne nulové zakázky.

První doložený záznam: `LMT` → **Sikorsky Aircraft Corporation**,
UEI `UTJWTSLMFNG4`. USAspending jej uvádí u zakázky
`CONT_AWD_SPE4A125F1406_9700_SPE4A122G0005_9700` s adresou ve Stratfordu
a jako parent recipient uvádí Lockheed Martin Corp. SEC 2015 10-K uvádí
dokončení akvizice 6. 11. 2015 a 2025 Exhibit 21 stále uvádí Sikorsky jako
dceru. V registru jsou přímé odkazy na oba SEC dokumenty a USAspending
zakázku; `known_at` je až okamžik našeho zjištění 30. 9. 2026 v 12:15 UTC.
Starší historický backtest tento dnešní objev nesmí zpětně vidět. Při živém
API běhu se i u tohoto UEI znovu ověří přesná shoda v každém řádku.
Tento jeden vztah neprokazuje UEI dalších závodů či dcer Lockheed Martin.

Konektor hledá pouze prime contracts A–D za posledních 730 dnů, nejvýše tři
stránky na UEI a běh. Každý výsledek z textového vyhledávání přijme až po
přesné shodě `Recipient UEI`; začátek zakázky musí ležet v doložené době
vztahu. Ukládá nahlášenou `Award Amount` a `Total Outlays` odděleně,
nikoli tržby firmy ani vyvozený strop kontraktu. Revize řádku dostanou nový
otisk a čas prvního pozorování, úplná historie modifikací však zatím není
k dispozici. Při limitu stránek hlásí `PARTIAL`. Chybějící zakázky mimo
časové okno a limit nejsou negativním důkazem. Před produkčním přiřazením
je potřeba naplnit registr ověřenými dokumenty a provést živý pozitivní
i negativní test.

Automatické dohledání příjemců zkouší oficiální USAspending `/api/v2/recipient/`
pro aktivní názvy emitentů, které SEC scout už pozoroval. Nejvýše 100 názvů
za běh, dvě stránky po 50 výsledcích na název, kontrola znovu po 30 dnech;
pořadí z databáze dává prostor i dalším firmám. Pouze celý shodný název
v seznamu příjemců vytvoří stopu s UEI a `NAME_ONLY`, `UNVERIFIED`.
Vazba k emitentovi a datum případného vlastnictví stále vyžadují samostatný
primární doklad. Kandidát nevstupuje do registru potvrzených UEI, zakázek
ani skóre. UI jej ukazuje odděleně a popisuje chybějící důkaz. Stránkovací
limit vrací `PARTIAL`; nulový počet kandidátů neříká, že firma nemá kontrakty.
Živý test oficiálního API zatím neproběhl.
