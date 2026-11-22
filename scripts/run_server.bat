@echo off
setlocal
cd /d "%~dp0.."
set PORT=%~1
if "%PORT%"=="" set PORT=8001
set RUN_EMBEDDED_SCHEDULER=1
set "PY=%CD%\.venv\Scripts\python.exe"
set "SRVLOG=%CD%\artifacts\desktop_server.log"
title StockScope India
echo StockScope server starting on http://127.0.0.1:%PORT%/
echo Log: %SRVLOG%
echo Close this window to stop the app.
echo.
"%PY%" manage.py run --port %PORT% 1>>"%SRVLOG%" 2>>&1
echo.
echo Server exited. See log: %SRVLOG%
pause
endlocal
