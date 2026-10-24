@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

title AI TRADER — Paper Runner

where node >nul 2>&1
if errorlevel 1 (
  echo Node.js is required but was not found in PATH.
  echo Install Node.js LTS, then double-click START_AI_TRADER again.
  echo.
  pause
  exit /b 1
)

where python >nul 2>&1
if errorlevel 1 (
  echo Python is required but was not found in PATH.
  echo Install Python 3, then double-click START_AI_TRADER again.
  echo.
  pause
  exit /b 1
)

echo.
echo AI TRADER — Paper Runner
echo ========================
echo Strategy E  ^|  COP SLB IBM XOM LMT  ^|  $10,000 paper  ^|  No real money
echo.

node scripts/ai-trader-manager.mjs start

echo.
echo Output log: day-trader-skill-trial\trader\paper-runner.log
echo Status:     node scripts/ai-trader-manager.mjs status
echo Stop:       STOP_AI_TRADER.bat
echo.
pause
