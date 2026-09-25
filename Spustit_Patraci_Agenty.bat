@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
set "APP_DIR=%CD%\market_checker_app"

if exist "%APP_DIR%\.venv\Scripts\python.exe" (
  set "PYTHON_EXE=%APP_DIR%\.venv\Scripts\python.exe"
) else (
  where py >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_EXE=py -3"
  ) else (
    set "PYTHON_EXE=python"
  )
)

echo [INFO] Kontroluji dalsi SEC podani pro produkcni seznam...
%PYTHON_EXE% -m market_checker_app.scout_runner --db-path "outputs\market_checker_history.db" --limit 100
if errorlevel 1 (
  echo [CHYBA] Sber SEC podani selhal nebo chybi JOHNY_SKORE_SEC_USER_AGENT.
  echo [INFO] Kontakt SEC lze jednorazove nastavit pri spusteni tydenniho shadow runneru.
  pause
  exit /b 1
)
echo [OK] Vysledky jsou ulozene v databazi Market Checkeru.
exit /b 0
