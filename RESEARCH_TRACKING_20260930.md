# Hloubkový výzkum: vazba na skutečný provoz

Zdroj: `Market_Checker_hloubkovy_vyzkum_2026-09-22.md`, kapitoly 1–20 a
implementační pořadí 01–28. Stav níže je kontrola kódu k 30. 9. 2026,
nikoli potvrzení úspěšného živého běhu. `ZÁKLAD` označuje existující
analytický modul, který nemusí mít automatický sběr pro všech 687 vstupů.

| Výzkumné body | Stav v projektu | Chybějící provozní cesta |
|---|---|---|
| 01–04 identita, důkazy, SEC, Windows | ČÁSTEČNĚ | 651 identit mimo pilot, aliasy do cen/scoutu, živý 687 běh a Windows test |
| 05 insider | ZÁKLAD | Form 4 P/S/F/M, 13D/G a 13F interpretace a historická akceptace |
| 06 kontrakty | ZÁKLAD analytického agenta | USAspending podle doloženého UEI a datovaných dcer, modifikace/obligations |
| 07 vládní rizika | ZÁKLAD analytického agenta | OFAC/DOJ a sektorové EPA/NHTSA s identitou před přiřazením |
| 08 veřejná extrakce | ČÁSTEČNĚ | SEC scout nově ukládá omezené citované stopy dodavatelů a komodit z 10-K/Q/20-F/40-F; chybí přílohy, skutečná identita protistran a hedging |
| 09 IR a zprávy | ČÁSTEČNĚ | Ověřené IR feedy a propojení RSS stopy s primárním oznámením |
| 10 ceny | ČÁSTEČNĚ | Živá akceptace po opravě 28. 9., corporate actions a symboly BRKB/PSTG/LEG |
| 11 short interest | OTEVŘENO | FINRA consolidated SI, datum zveřejnění a settlement |
| 12 FDA | OTEVŘENO | Produktový/sponsor crosswalk, CRL/recall, sektorová fronta |
| 13 vstupní náklady | ČÁSTEČNĚ | FRED makro konektor čeká na klíč a live test; EIA/USDA a propojení firemních expozic, hedge, jednotek a vintage chybí |
| 14 entity enrichment | ČÁSTEČNĚ | GLEIF/OpenFIGI a dcery/produkty na doložených ID |
| 15 SAM | OTEVŘENO | Schválený klíč a reálná kvóta; doplnění USAspending |
| 16 instituce | OTEVŘENO | SEC 13F managers, security crosswalk a čas zveřejnění |
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
