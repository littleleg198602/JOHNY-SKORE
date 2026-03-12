# Streamlit runtime checklist (manual)

## 1) Spuštění
1. Otevři terminál v rootu repo.
2. Spusť aplikaci:
   ```bash
   streamlit run market_checker_app/app.py
   ```
3. V sidebaru nastav:
   - **DB soubor** (např. `outputs/smoke_runtime/manual_history.db`)
   - **Ukládat historii do SQLite** = ON
   - **Export do Excelu** = ON

## 2) Co kliknout
1. Do Watchlist dej 3–5 tickerů (např. `AAPL`, `MSFT`, `TSLA`).
2. Klikni **Spustit analýzu**.
3. Po doběhu projdi taby:
   - **Signals**
   - **Ranking**
   - **History**
   - **Trends**

## 3) Jak poznám, že MT5 branch funguje
- V tabulce Signals je sloupec **tech_source_used**.
- Pokud je MT5 dostupné a data přišla, u tickeru je hodnota **`mt5`**.
- Confidence techniky bývá vyšší než fallback a warningy neobsahují fallback text.

## 4) Jak poznám, že fallback funguje
- V **tech_source_used** je **`yfinance_fallback`**.
- Ve **warnings** u tickeru je explicitní text `MT5 not used ... reason: ...`.
- Běh se dokončí i bez MT5.

## 5) Jak poznám, že SQLite ukládá
1. V tab History vyber ticker a ověř řádky z aktuálního běhu.
2. V řádcích zkontroluj sloupce:
   - `scoring_version`
   - `legacy_total_score`
   - `legacy_signal`
   - `tech_source_used`
3. Proveď druhý běh a ověř, že přibyly další záznamy (`run_id` roste).

## 6) Jak poznám, že export obsahuje nové sloupce
1. Po běhu se uloží `.xlsx`.
2. Otevři sheet **Signals**.
3. Ověř přítomnost sloupců:
   - `scoring_version`
   - `legacy_total_score`
   - `legacy_signal`
   - `tech_source_used`

## 7) Evaluation kontrola
- V tab Ranking / evaluation ověř tabulky pro:
  - score comparison (legacy vs new)
  - signal transition
  - hit-rate new vs legacy
  - side-by-side strategie
