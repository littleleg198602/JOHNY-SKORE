@echo off
setlocal
chcp 65001 >nul

cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0market_checker_app\install_weekly_shadow_task.ps1" -Mode Install
if errorlevel 1 (
  echo.
  echo [CHYBA] Tydenni ulohu se nepodarilo nainstalovat.
  pause
  exit /b 1
)

echo.
echo [OK] Tydenni shadow uloha je nainstalovana.
pause
exit /b 0
