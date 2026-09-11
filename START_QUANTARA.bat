@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

title QUANTARA STARTER

where node >nul 2>&1
if errorlevel 1 (
  echo Node.js is required but was not found in PATH.
  echo Install Node.js LTS, then double-click START_QUANTARA again.
  echo.
  pause
  exit /b 1
)

where python >nul 2>&1
if errorlevel 1 (
  echo Python is required but was not found in PATH.
  echo Install Python 3, then double-click START_QUANTARA again.
  echo.
  pause
  exit /b 1
)

node scripts/start-quantara.mjs
echo.
pause
