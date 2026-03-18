@echo off
setlocal
chcp 65001 >nul

cd /d "%~dp0"

set "APP_DIR=%CD%\market_checker_app"
if not exist "%APP_DIR%\app.py" (
  echo [CHYBA] Nenasel jsem market_checker_app\app.py
  pause
  exit /b 1
)

set "PYTHON_EXE="
if exist "%APP_DIR%\.venv\Scripts\python.exe" (
  set "PYTHON_EXE=%APP_DIR%\.venv\Scripts\python.exe"
) else (
  where py >nul 2>nul
  if %errorlevel%==0 (
    set "PYTHON_EXE=py -3"
  ) else (
    set "PYTHON_EXE=python"
  )
)

echo [INFO] Pouzivam Python: %PYTHON_EXE%

echo [INFO] Kontroluji zavislosti...
%PYTHON_EXE% -m pip install -r "%APP_DIR%\requirements.txt" >nul 2>nul
if errorlevel 1 (
  echo [INFO] Nepodarilo se tise doinstalovat zavislosti. Pokracuji dal...
)

echo [INFO] Spoustim Market Checker...
%PYTHON_EXE% -m streamlit run "%APP_DIR%\app.py"

if errorlevel 1 (
  echo.
  echo [CHYBA] Aplikace se nepodarila spustit. Zkontrolujte Python + zavislosti.
  pause
)

endlocal
