@echo off
setlocal
cd /d "%~dp0.."
set PORT=%~1
if "%PORT%"=="" set PORT=8001
set RUN_EMBEDDED_SCHEDULER=1
set PYTHONUNBUFFERED=1
set "PY=%CD%\.venv\Scripts\python.exe"
set "SRVLOG=%CD%\artifacts\desktop_server.log"
title StockScope India - SERVER (keep open)
echo.
echo ============================================
echo   StockScope SERVER - keep this window open
echo ============================================
echo   URL:  http://127.0.0.1:%PORT%/
echo   Log also: %SRVLOG%
echo   Close this window to STOP the app.
echo ============================================
echo.
echo [%date% %time%] LOADING: manage.py run --port %PORT%
echo [%date% %time%] LOADING: manage.py run --port %PORT% > "%SRVLOG%"
"%PY%" -u manage.py run --port %PORT%
set EXITCODE=%ERRORLEVEL%
echo.
echo [%date% %time%] Server exited with code %EXITCODE%
echo If that was an error, scroll up for the traceback.
pause
endlocal
