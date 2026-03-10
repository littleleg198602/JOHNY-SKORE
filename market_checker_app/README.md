# Market Checker (interní analytika)

Lokální Streamlit aplikace pro analýzu watchlistu z MT5 a kombinaci tří zdrojů signálu:
- RSS/news scoring
- Yahoo/yfinance snapshot
- technické indikátory (modul připraven, aktuálně základní score fallback)

## Spuštění

```bash
cd market_checker_app
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Co aplikace dělá

- načte watchlist z MetaTrader5 (nebo ručně z textového pole)
- stáhne RSS články a přiřadí je tickerům
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

## Poznámky k odolnosti

- při nedostupném MT5 aplikace zobrazí chybu a umožní ruční watchlist
- při chybě Yahoo fallbacku přidá warning a pokračuje
- při timeout/chybě RSS zdroje pokračuje s ostatními zdroji
- při chybě SQLite pokračuje bez historie (warning)
- při chybějícím marketcap souboru pokračuje bez market cap ranking dat
