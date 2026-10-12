@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "HEALTHCARE_PYTHON=python"
set "HEALTHCARE_PYTHON_ARGS="
if exist "market_checker_app\.venv\Scripts\python.exe" (
  set "HEALTHCARE_PYTHON=market_checker_app\.venv\Scripts\python.exe"
) else (
  where py >nul 2>nul
  if not errorlevel 1 (
    set "HEALTHCARE_PYTHON=py"
    set "HEALTHCARE_PYTHON_ARGS=-3"
  )
)

echo Vytvarim prehled zdravotnickych identit pro vsech 687 tickeru.
echo Ctu existujici data. Chybejici databaze zustane v reportu uvedena.
"%HEALTHCARE_PYTHON%" %HEALTHCARE_PYTHON_ARGS% -m market_checker_app.healthcare_coverage ^
  --db-path "outputs\market_checker_history.db" ^
  --output "outputs\healthcare_coverage_latest.json"
if errorlevel 1 (
  echo [CHYBA] Report se nepodarilo vytvorit.
  pause
  exit /b 1
)
echo [HOTOVO] Report je v outputs\healthcare_coverage_latest.json
echo Pokryti konkretnich objektu a dokoncene kontroly podle nazvu jsou uvedeny oddelene.
pause
exit /b 0
