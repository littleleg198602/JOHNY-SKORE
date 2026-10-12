@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "SPECIALIST_PYTHON=python"
if exist "market_checker_app\.venv\Scripts\python.exe" (
  set "SPECIALIST_PYTHON=market_checker_app\.venv\Scripts\python.exe"
) else (
  where py >nul 2>nul
  if not errorlevel 1 set "SPECIALIST_PYTHON=py -3"
)

echo Vyhodnocuji ulozene snapshoty specialistu. Nespoustim nove sbery.
%SPECIALIST_PYTHON% -m market_checker_app.specialist_evaluation ^
  --db-path "outputs\market_checker_history.db" ^
  --output-path "outputs\specialist_evaluation_latest.json"
if errorlevel 1 (
  echo [CHYBA] Report se nepodarilo vytvorit.
) else (
  echo [HOTOVO] Report je v outputs\specialist_evaluation_latest.json
  echo Nedostatek historie a chybejici archivovane vstupy jsou uvedeny v reportu.
)
pause
endlocal
