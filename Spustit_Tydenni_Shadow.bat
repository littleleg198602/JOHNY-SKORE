@echo off
setlocal EnableExtensions EnableDelayedExpansion
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

call :ensure_sec_user_agent
if errorlevel 1 goto :error

echo [INFO] Spoustim tydenni analyticky shadow beh pro cely nakonfigurovany universe...
%PYTHON_EXE% -m market_checker_app.weekly_shadow_runner --no-mt5 --runtime-config "%APP_DIR%\autonomous_runtime.json" --ticker-file "%APP_DIR%\production_watchlist.txt"
if errorlevel 1 goto :error

echo [OK] Shadow beh prosel. Vysledek: outputs\weekly_shadow_latest.json
exit /b 0

:ensure_sec_user_agent
if defined JOHNY_SKORE_SEC_USER_AGENT (
  echo(!JOHNY_SKORE_SEC_USER_AGENT!|%SystemRoot%\System32\findstr.exe /b /c:"JohnySkore/2.1 " >nul
  if not errorlevel 1 exit /b 0
  rem Older manual setup may have saved only the e-mail address.
  set "SEC_EMAIL=!JOHNY_SKORE_SEC_USER_AGENT!"
) else (
  set "SEC_EMAIL="
)

if not defined SEC_EMAIL (
  echo [INFO] Zadej e-mail pouze pri prvnim spusteni.
  set /p "SEC_EMAIL=SEC e-mail: "
)
if not defined SEC_EMAIL (
  echo [CHYBA] SEC e-mail nebyl zadan.
  exit /b 1
)

set "JOHNY_SKORE_SEC_USER_AGENT=JohnySkore/2.1 !SEC_EMAIL!"
setx JOHNY_SKORE_SEC_USER_AGENT "!JOHNY_SKORE_SEC_USER_AGENT!" >nul 2>&1
if errorlevel 1 (
  echo [CHYBA] SEC User-Agent se nepodarilo ulozit do Windows.
  exit /b 1
)
echo [OK] SEC User-Agent ulozen pro dalsi behy.
exit /b 0

:error
echo.
echo [CHYBA] Tydenni analyticky shadow beh neprosel. Analyticke BUY/SELL nebylo zmeneno.
pause
exit /b 1
