@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title QUANTARA TAILSCALE SETUP

where node >nul 2>&1
if errorlevel 1 (
  echo Node.js is required but was not found in PATH.
  pause
  exit /b 1
)

node scripts/setup-tailscale-funnel.mjs
echo.
pause
