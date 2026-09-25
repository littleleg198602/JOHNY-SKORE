@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
set "APP_DIR=%CD%\market_checker_app"

rem Task Scheduler can retain an old environment after setx. Read the user
rem setting on every start, without printing the contact address.
for /f "usebackq delims=" %%A in (`powershell.exe -NoProfile -Command "[Environment]::GetEnvironmentVariable('JOHNY_SKORE_SEC_USER_AGENT','User')"`) do set "JOHNY_SKORE_SEC_USER_AGENT=%%A"

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
  echo [CHYBA] Sber SEC podani selhal nebo chybi jednorazovy SEC kontakt.
  echo [INFO] Nastaveni provede Nainstalovat_Patraci_Agenty.bat.
  exit /b 1
)
echo [OK] Vysledky jsou ulozene v databazi Market Checkeru.
exit /b 0
