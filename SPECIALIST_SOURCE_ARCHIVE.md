# Archiv jednotlivých zdrojů NEW ANALYZER

`specialist_source_archive_service.py` připraví samostatný archiv skutečných
pozorování ve ScoutStore. Používá přesně původních 687 tickerů včetně BRKB;
provider alias BRK-B ani obecný makroekonomický kontext nepřisuzuje firmám.
Zdrojové směrování pouze uvádí oblasti použití, nezvyšuje jejich akceptaci.

```python
from pathlib import Path
from datetime import datetime, timezone
from market_checker_app.services.specialist_source_archive_service import (
    build_specialist_source_archive, export_specialist_source_archive,
)

# existing_store je již otevřený ScoutStore aplikace; tento export jej nemění.
archive = build_specialist_source_archive(
    existing_store, as_of=datetime.now(timezone.utc),
    maximum_observations=10_000, observations_per_source_ticker=20,
    maximum_checks=10_000,
)
path = export_specialist_source_archive(archive, Path("outputs/source_archives"))
```

Koordinátor může výsledek zapojit jako samostatný explicitní export v UI.
Žádné síťové sběrače, nové databázové tabulky, predikční snímky, skóre ani
akceptační příznaky tento servis nezapisuje. Čtení má jeden konzistentní
SQLite snapshot a `query_only`. Soubor se publikuje atomicky pod SHA-256;
existující odlišný obsah odmítne přepsat. Předchozí archivy zůstávají samostatné.

Schéma `specialist_source_archive_v1` obsahuje:

| Část | Význam a omezení |
|---|---|
| `universe` | Původní seznam, pořadí, source Excel a ověřený CSV hash. |
| `observations` | Jednotlivé verze nálezů podle zdroje a tickeru; stabilní hash, původní finding ID, hash objektu/obsahu a veřejná URL bez query/fragmentu. |
| Časy pozorování | `stored_publication_at`, `available_at`, `first_observed_at`, identity knowledge times a konzervativní `knowledge_at` jsou oddělené. |
| `historical_event_dates` | Staré období 13F, report FDIC, podpis FDA dopisu, CMS association date nebo začátek kontraktu nikdy nejsou náhradou času znalosti. |
| `source_fields` | Omezená projekce známých hodnot a vztahů; volný text, chybové zprávy, request hlavičky, klíče a cursory se neexportují. Původní JSON zůstává ve zdrojové DB, export nese jeho hash. |
| `checks` | Poslední uložená kontrola konkrétního identity scope, FDIC pokus nebo USAspending awards checkpoint. Scope hash nezaměňovat za doloženou aktuální identitu. |
| `source_ticker_missingness` | Pro všech 16 zdrojů × 687 tickerů odděluje chybějící pozorování, nulovou/částečnou kontrolu, skutečné počty verzí a omezení exportu. Relevantnost se neodhaduje. |
| `source_runs` | Poslední uložený stav zdroje dostupný k cutoff; bez odhadování per-ticker coverage z agregovaných čítačů. |
| `bounds` / `input` | Počty uložených a exportovaných verzí, odmítnuté záznamy, omezení výstupu a skutečně dostupné tabulky. |

Výchozí export ponechá nejvýše 20 posledních verzí zdroje/tickeru, celkem
10 000 pozorování a 10 000 kontrol. Horní mez je 100 verzí v buňce a 20 000
záznamů každého druhu. JSON detail přes 128 KiB se odmítne. Chybějící
exportovaná verze se vykáže, nevytvoří se úplnost. Neplatné nebo budoucí
identity knowledge časy se odmítnou; časy mají timezone a subsecond cutoff.

ScoutStore uchovává nezměněné verze nálezů, ale mění poslední retrieval
heartbeat. Hash pozorování proto heartbeat nezahrnuje. Poslední retrieval
po cutoff se nevydává za dřívější znalost; starší heartbeat už nejde obnovit.
Kontroly a checkpointy přepisují poslední stav. Historickou nulovou kontrolu
po pozdějším přepsání dnešní DB nedoloží, což archiv výslovně uvádí.
Samostatné průběžně ukládané archivy jsou podklad pro budoucí časovou historii.

Ani úplná nulová kontrola konkrétního dotazu nedokazuje absenci všech událostí
firmy. Datasetová kontrola 13F nemění úplnost jednotlivých tickerů a ukončené
USAspending stránkování nedokazuje neměnný zdrojový snímek. Agregované
regulatory/agent features se těmto sběračům nepřisuzují.

Tato změna nedoložila živé relevantní coverage, publisher release timestamps,
uživatelský Windows ani OOS výsledky. Pro úplné historické vyhodnocení jsou
nadále potřebné průběžné zdrojové archivy, datované identity, predikční
snapshoty a skutečné výsledky trhu. Přínos nebo nasazení skóre se z tohoto
exportu nevyvozuje.
