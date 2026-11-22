@echo off
REM Thin wrapper - all live status is in PowerShell
cd /d "%~dp0.."
title StockScope India - starting
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_stockscope.ps1"
if errorlevel 1 (
  echo.
  echo Start failed. Press any key to close.
  pause >nul
  exit /b 1
)
