# Market Checker (interní analytika)

Lokální Streamlit aplikace pro analýzu watchlistu z Excelu, ručního vstupu nebo MT5 a kombinaci zdrojů signálu:
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

Ve Windows aktivuj prostředí příkazem `.venv\Scripts\activate` nebo použij
`Spustit_Market_Checker.bat` v kořeni repozitáře.

## Spuštění dvojklikem (Windows)

V kořeni repozitáře je připraven soubor:
- `Spustit_Market_Checker.bat`

Stačí na něj dvakrát kliknout. Skript:
- najde Python (`.venv\Scripts\python.exe`, nebo `py -3`, nebo `python`)
- nainstaluje závislosti a při chybě zobrazí skutečný důvod
- spustí Streamlit aplikaci

## Vstupy a zdroje

- Excel musí obsahovat sloupec `Yahoo ticker`, `yahoo_ticker` nebo `ticker`.
- Ruční watchlist přijímá jeden ticker na řádek.
- RSS a MT5 se zapínají samostatnými volbami v levém panelu; nahrání Excelu je automaticky nevypíná.
- Yahoo cenová historie se v rámci malého běhu sdílí mezi performance a technickou analýzou.
- Pro velký watchlist se Yahoo metadata ukládají trvale do stejné SQLite DB. Tlačítko
  **Doplnit Yahoo cache** zpracuje nastavenou dávku a další kliknutí automaticky pokračuje.
- Pokud Yahoo omezí požadavky, aplikace zobrazí výraznou chybu a označí fallback výsledky jako nespolehlivé.
- Výchozí tickerové zprávy používají experimentální Google News RSS bez registrace.
  Nefunkční Yahoo Finance RSS URL není ve výchozím seznamu. Položky bez data publikace
  ani položky s budoucím datem se nezapočítají jako čerstvé zprávy.

### Velký universe (např. 687 tickerů z MT5)

- Analyzují se všechny tickery, výchozí limit jednoho běhu je 1000 symbolů.
- RSS zdroje se načítají paralelně s timeoutem a MT5 OHLCV v jednom připojení k terminálu.
- UI průběžně ukazuje fáze `RSS`, `MT5 OHLC` a následně pořadí zpracovávaného tickeru.
- Nad 100 tickerů se Yahoo metadata nenačítají jednotlivě uvnitř analýzy. Analýza použije
  trvalou Yahoo cache; čerstvá a zastaralá data jsou ve výsledku viditelně označena. Chybějící
  metadata mají neutrální skóre a skutečných 0 % důvěry. Aktuální cena a změny se v tomto
  režimu odvozují z MT5.
- Pro 687 tickerů nejdřív opakovaně spusťte **Doplnit Yahoo cache**, dokud ukazatel pokrytí
  nedosáhne požadovaného počtu, a potom spusťte analýzu. Při rate limitu jsou hotová data
  zachována a po ochranné pauze se pokračuje pouze zbývajícími tickery.
- Když pro některý symbol MT5 nevrátí svíčky, tento řádek zůstane ve výsledku, ale technická
  část bude neutrální a ve sloupci `warnings` bude uveden důvod.

## Co aplikace dělá

- načte watchlist z MetaTrader5 (nebo ručně z textového pole)
- stáhne RSS články a přiřadí je tickerům
- pokud ticker nemá zprávu za 48h, použijí se zprávy až 3 měsíce zpět s časovým útlumem (novější mají větší váhu)
- RSS URL může obsahovat placeholder `{ticker}` (např. Yahoo feed), který se při běhu rozbalí pro každý symbol z watchlistu
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

## Rychlá validace scoring pipeline

Po refaktoru scoringu doporučujeme po změnách vždy ověřit minimálně:

```bash
python -m compileall market_checker_app
python -m unittest discover -s tests -v
```

Volitelně (pokud je nainstalovaný Streamlit):

```bash
streamlit run market_checker_app/app.py
```

Pull requesty navíc kontrolují tři deterministické GitHub testovací agenty:

- **Contract, source and persistence agent** spustí kompilaci a celý testovací balík,
- **687 ticker scale and progress agent** vyžaduje přesně 687 unikátních výsledků,
  Yahoo cache, jeden MT5 batch a dokončení progressu na 100 %,
- **Streamlit UI agent** ověří start aplikace a tlačítka celého Yahoo workflow.

Závěrečný release gate projde pouze tehdy, když projdou všichni tři. Testy nepoužívají
živou síť, takže Yahoo/RSS timeout ani cizí rate limit nemohou náhodně rozhodnout o PR.

Zkontroluj v UI, že tab **Signals** obsahuje sloupce:
- `raw_total_score`, `final_total_score`
- `final_confidence`, `data_quality_score`
- `news_confidence`, `tech_confidence`, `yahoo_confidence`
- `signal_strength`, `reasons`, `warnings`

## Poznámky k odolnosti

- při nedostupném MT5 aplikace zobrazí chybu a umožní ruční watchlist
- při chybě Yahoo fallbacku přidá warning a pokračuje
- při timeout/chybě RSS zdroje pokračuje s ostatními zdroji
- při chybě SQLite pokračuje bez historie (warning)
- při chybějícím marketcap souboru pokračuje bez market cap ranking dat

## Lokální tajné hodnoty

Soubor `code.env` se nesmí commitovat. Pokud ho potřebuje starší skript
`refresh_news.py`, zkopíruj `code.env.example` na `code.env` a vlož nový klíč
pouze do lokální kopie.

## Poznámka k PR workflow

- pokud v GitHub UI vidíš tlačítko **Zobrazit PR**, znamená to, že pro tuto branch už PR existuje
