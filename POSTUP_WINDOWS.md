# JOHNY-SKORE: presun na Windows

Tato prenosova vetev obsahuje aktualni zdrojovy stav vcetne rozpracovanych zmen.
Specialista 1 neni oznacen za dokonceny. Posledni uplne overeni pred novou
rozpracovanou davkou: 969 uspesnych Linux testu. Novejsi uplna sada byla
uzivatelem zastavena; nejedna se o potvrzeny uspesny beh.

1. Rozbal ZIP do C:\Projects\JOHNY-SKORE a tuto slozku otevri ve VS Code.
2. Nainstaluj Git for Windows, 64bitovy Python 3.12 s Python Launcherem
   a oficialni rozsireni Codex od OpenAI. Prihlas se do Codexu svym uctem.
3. V PowerShell terminalu v koreni projektu spust:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r market_checker_app/requirements.txt -c market_checker_app/constraints.txt
.\.venv\Scripts\python.exe -m streamlit run market_checker_app/app.py
```

Pro zachovani Git historie lze misto ZIP pouzit:

```powershell
git clone --branch codex/windows-handoff-20261016 https://github.com/littleleg198602/JOHNY-SKORE.git C:\Projects\JOHNY-SKORE
```

ZIP neobsahuje provozni cache a SQLite databaze z cloudoveho outputs/,
linuxove virtualni prostredi ani prihlasovaci udaje. Datovane primarni
dukazy v evidence/ a market_checker_app/data/ zustavaji soucasti projektu.

Lokalnimu Codexu predej:

> Precti docs/identity-specialist-runtime.md. Nejdrive zprovozni a over projekt
> primo na Windows. Pokracuj pouze na specialistovi 1. OZK a LEG jsou vynechane,
> 685 identit/trid je dolozenych. Novy doplnek obsahuje sedm dalsich dcer,
> celkem devet datovanych pozorovani. Posledni nova uplna sada testu byla
> zastavena a neni potvrzena jako uspesna. Neprechazej na specialistu 2.
