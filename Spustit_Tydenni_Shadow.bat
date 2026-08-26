@echo off
setlocal
chcp 65001 >nul

cd /d "%~dp0"
set "APP_DIR=%CD%\market_checker_app"

set "PYTHON_EXE="
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

echo [INFO] Instaluji overene verze zavislosti...
%PYTHON_EXE% -m pip install -r "%APP_DIR%\requirements.txt" -c "%APP_DIR%\constraints.txt"
if errorlevel 1 goto :error

echo [INFO] Spoustim tydenni auditni shadow beh pro 36 tickeru...
%PYTHON_EXE% -m market_checker_app.weekly_shadow_runner --no-mt5 --ticker-file "%APP_DIR%\production_watchlist.txt" --ticker-limit 36
if errorlevel 1 goto :error

echo [OK] Shadow beh prosel. Vysledek: outputs\weekly_shadow_latest.json
exit /b 0

:error
echo.
echo [CHYBA] Tydenni shadow beh neprosel. BUY/SELL nebylo agentni vrstvou zmeneno.
pause
exit /b 1
