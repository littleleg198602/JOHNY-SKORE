# Pátrací specialisté: jediný seznam stavu

Strojově čitelný seznam je v
`market_checker_app/data/specialist_status.json` a aplikace ho zobrazuje
v části **Co je hotové a co zbývá — specialisté**. Zahrnuje všech 687
vstupních tickerů; sektorový zdroj se vyhodnocuje jen pro relevantní firmy.
Mapování pokrývá výzkumné kroky 01–28 a sektorové profily.

Od 1. 10. lze ve stejné části UI stáhnout **provozní přehled specialistů**
jako JSON: skutečné uložené běhy zdrojů, aktuální sektorové pokrytí,
integritu archivovaného 687tickerového vstupu a počet doložených SEC identit.
Nenalezený archiv ani nikdy neprovedený zdroj nevytváří falešnou nulovou
kontrolu. Výstup uvádí systém počítače; samotný export na Windows nepotvrzuje
koncový běh. Obsahuje pouze boolean přítomnosti přístupových nastavení,
nikoli jejich hodnoty, adresy vlastníků, pracovní cesty nebo přihlašovací
údaje. Přítomný klíč neprokazuje funkční oprávnění.

Tento aktuální diagnostický snapshot není historický replay ani certifikát
dokončení a nemění `DONE`, `VERIFIED` nebo predikční skóre. Pozitivní a
negativní případy, datované vztahy a out-of-sample vyhodnocení zůstávají
samostatnými akceptačními důkazy.

FDIC má od 1. 10. perzistentní obnovovací dávky: výchozí pět bank za běh,
deset požadavků za UTC den, 30 dní pro použitelnou odpověď a jeden den
pro částečnou/prázdnou/chybnou kontrolu. Diagnostika odděluje čtrnáct
mapovaných bank od 22 BANK profilů a skutečné pokusy od použitelných dat.
Prázdná či odmítnutá odpověď nezvyšuje použitelnou coverage. Staré nálezy
samotné nenahrazují refresh pokus; scope je nejvýše dva výkazy na CERT,
nikoli úplnost emitentovy skupiny. HTTP blokace mají uloženou pauzu.
Zachycené payloady a restart/kvóta replay jsou doložené v
`evidence/fdic_scheduler_replay_20261001.json`, deterministické kontroly
v `evidence/fdic_scheduler_tests_20261001.json`. Nejde o nový živý běh,
Windows akceptaci ani historickou evaluaci. Stav zůstává PILOT/PENDING.

V předchozím kroku bylo datované bankovní mapování rozšířené na osm z 22 BANK profilů:
TFC → Truist Bank / CERT 9846 a FITB → Fifth Third Bank, National
Association / CERT 6672. FITB Exhibit 21 platí k 15. 2. 2026, nikoli
k výročnímu období 31. 12. 2025. SEC vztahy a skutečné FDIC payloady
jsou v `evidence/fdic_tfc_fitb_identity_20261001.json`, tři omezené
kladné/záporné publisher kontroly v `evidence/fdic_tfc_fitb_live_20261001.json`.
Tento předchozí pilot nemění PILOT/PENDING nebo 0/21 DONE.

Po kroku CFG/HBAN bylo mapováno deset z 22 BANK profilů: CFG → Citizens Bank, National
Association / CERT 57957 a HBAN → The Huntington National Bank / CERT 6560.
CFG datum vztahového tvrzení je 22. 1. 2026 podle prospektového dodatku,
nikoli neověřené datum Exhibit 21; HBAN má výslovné datum 31. 12. 2025.
Důkazy a tři omezené živé případy jsou v
`evidence/fdic_cfg_hban_identity_20261001.json` a
`evidence/fdic_cfg_hban_live_20261001.json`.

Po kroku ALLY/CFR bylo mapováno dvanáct z 22 BANK profilů, nově ALLY → Ally Bank /
CERT 57803 a CFR → Frost Bank / CERT 5510. ALLY má výslovné as-of datum
31. 12. 2025; CFR as-of zůstává neověřené a přijímá pouze výkazy od
konzervativní hranice 5. 2. 2026 z ověřeného filing indexu. Není to
datum akvizice. Citace a skutečné publisher odpovědi jsou v
`evidence/fdic_ally_cfr_identity_20261001.json`, tři živé případy v
`evidence/fdic_ally_cfr_live_20261001.json`.

Aktuálně je mapováno čtrnáct z 22 BANK profilů, nově COF → Capital One,
National Association / CERT 4297 a EWBC → East West Bank / CERT 31628.
Obě SEC přílohy výslovně dokládají as-of 31. 12. 2025 a vynechávají
další dcery; nedokládají úplnost skupin ani kontinuální historii.
R1 / filing indexy rozlišují issuer/common-stock identitu, datum vztahu
a zveřejnění. Citace a skutečné FDIC odpovědi jsou v
`evidence/fdic_cof_ewbc_identity_20261001.json`, tři omezené živé případy
v `evidence/fdic_cof_ewbc_live_20261001.json`. Zbývá osm emitentů,
kompletní skupiny, Windows běh, relevantní provozní pokrytí a historická evaluace.

## Co znamenají stavy

- `NOT_STARTED`: potřebný sběr ještě neexistuje.
- `PILOT`: omezená prokázaná sada identit či instrumentů.
- `PARTIAL`: existuje sběr nebo analytika, ale chybí některá cesta či pokrytí.
- `DONE`: doložená identita v čase, živý pozitivní i negativní případ,
  koncový běh na Windows, coverage pro relevantní firmy a historie.
- `PENDING`: živý důkaz zatím chybí. `WAIT_ACCESS`: zdroj vyžaduje klíč,
  účet či licenci. `VERIFIED`: živý důkaz je uložený.

Žádný nový specialista zatím nesplňuje celé `DONE`. Kompilace, lokální
testy a zelené CI potvrzují vlastnosti kódu, nikoli úspěšné stažení dat
na uživatelově PC. Ani 687 řádků watchlistu nezaručuje, že je pro každý
relevantní ticker ověřená produktová, dceřiná a instrumentová identita.
Při změně stavu je nutné aktualizovat JSON ve stejném PR jako kód a připojit
důkaz živého běhu, pokud se nastavuje `VERIFIED`.

Denní přehled **Poslední běh pátracích zdrojů** vedle inventáře čte skutečné
záznamy z SQLite. Inventář je verzovaná mapa dokončení, nikoli náhrada
za aktuální běhovou telemetrii. Od 30. 9. 2026 platí pořadí: banky,
farmacie/medtech a zdravotní služby; energie a obrana; zbývající sektorové
profily; placené a alternativní zdroje až po ověřených pilotech.
