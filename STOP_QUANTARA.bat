@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

title QUANTARA STOP

where node >nul 2>&1
if errorlevel 1 (
  echo Node.js is required but was not found in PATH.
  echo.
  pause
  exit /b 1
)

node scripts/stop-quantara.mjs
echo.
pause
