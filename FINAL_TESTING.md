# Finální uživatelský test

## Spuštění

1. Na Windows rozbalte nebo aktualizujte celý projekt tak, aby zůstala zachována složka `outputs`.
2. Dvakrát klikněte na `Spustit_Market_Checker.bat`. Spouštěč sám zkontroluje a doinstaluje uzamčené závislosti a otevře Streamlit aplikaci.
3. Nechte zapnuté **Ukládat historii do SQLite**. Výchozí databáze je `outputs/market_checker_history.db`.
4. Pro rychlý první test zadejte do ručního watchlistu například `AAPL`, `MSFT`, `NVDA`, vypněte nepotřebné živé vrstvy a klikněte na **Spustit analýzu**.
5. Pro celý produkční test odstraňte ruční watchlist. Aplikace použije kanonický seznam 687 tickerů. Doba a počet upozornění závisí na dostupnosti Yahoo, RSS, SEC a nastavení MT5.

SEC vrstva se bez kontaktní identity v prostředí sama vypne; aplikace po vás ve formuláři nechce e-mail a ostatní analýza normálně funguje.

## Co zkontrolovat

- Výsledná tabulka obsahuje jen řádky s použitelnou cenou v rankingu. Neúplný ticker musí mít viditelný důvod a nesmí dostat falešné pořadí.
- Sekce **Analytické reporty a stav ověření** ukazuje makro, protistrany, kandidátní model a historické ověření. `INSUFFICIENT_DATA` je očekávaný stav, dokud nevznikne kompatibilní historie targetu v4.
- Soubor `outputs/weekly_shadow_latest.json` obsahuje `pipeline_status`, warnings, failures, přesný počet snapshotů a všechny stejné navazující reporty.
- Druhý běh používá stejnou SQLite historii. Staré výsledky se nepřepisují a snapshot stejného běhu se nevytvoří podruhé.

## Jak číst výsledek

`SUCCESS` potvrzuje dokončené zpracování a uložení. `PARTIAL` znamená, že některý zdroj nebo analytický report nebyl kompletní. `FAILED` označuje porušený povinný kontrakt. Žádný z těchto stavů sám o sobě neprokazuje zvýšení predikční přesnosti. Kandidát, makro, SEC, zprávy a protistrany jsou shadow analýzy a nemění hlavní ranking.

Program neodesílá obchodní příkazy. Výsledné rozhodnutí dělá uživatel ručně.
