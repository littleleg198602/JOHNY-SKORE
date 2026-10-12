@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
rem Reuse the contact already configured by the existing scout installer.
for /f "usebackq delims=" %%A in (`powershell.exe -NoProfile -Command "[Environment]::GetEnvironmentVariable('JOHNY_SKORE_SEC_USER_AGENT','User')"`) do set "JOHNY_SKORE_SEC_USER_AGENT=%%A"
if exist "market_checker_app\.venv\Scripts\python.exe" (
  set "PYTHON_EXE=market_checker_app\.venv\Scripts\python.exe"
) else (
  where py >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_EXE=py -3"
  ) else (
    set "PYTHON_EXE=python"
  )
)
echo [INFO] Pripravuji kandidaty SEC pro vsech 687 tickeru. Kazdy krok se uklada.
%PYTHON_EXE% -m market_checker_app.sec13f_identity_bulk prepare --capture-global --batch-size 100
if errorlevel 1 (
  echo [CHYBA] Priprava se zastavila. Hotove kroky zustaly ulozene.
  echo [INFO] Po odstraneni vypsane priciny spust stejnou pripravu znovu.
  pause
  exit /b 1
)
echo [OK] Fronta kandidatu je v outputs\sec13f_identity_bulk\candidates.json.
echo [INFO] Jde o podklady k overeni, produkcni registry se nemenily.
pause
exit /b 0
