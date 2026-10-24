@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

title AI TRADER — Stop

where node >nul 2>&1
if errorlevel 1 (
  echo Node.js is required but was not found in PATH.
  echo.
  pause
  exit /b 1
)

echo.
echo AI TRADER — Graceful Stop
echo ==========================
echo Sending stop signal to paper_runner.py...
echo The runner will finish its current loop tick (up to 30 s),
echo save the paper journal and state, then exit.
echo.

node scripts/ai-trader-manager.mjs stop

echo.
echo AI Trader stopped. Journal and state saved.
echo.
pause
