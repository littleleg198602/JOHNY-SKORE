@echo off
setlocal
cd /d "%~dp0"

echo.
echo Vyhodnocuji zrale historicke predikce z ulozene SQLite historie.
echo Nova analyza trhu, SEC ani RSS se nespousti.
echo.

python -m market_checker_app.prediction_label_runner ^
  --db-path "outputs\market_checker_history.db" ^
  --output-path "outputs\prediction_label_resolution_latest.json" ^
  --limit 120

if errorlevel 1 (
  echo.
  echo [CHYBA] Vyhodnoceni predikci neproslo. Historie ani puvodni analyza nebyly zmeneny.
) else (
  echo.
  echo [HOTOVO] Vysledek je v outputs\prediction_label_resolution_latest.json
)

pause
endlocal
