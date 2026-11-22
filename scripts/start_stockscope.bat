@echo off
setlocal
cd /d "%~dp0.."
title StockScope India - starting
set "ROOT=%CD%"
set PORT=8001
set RUN_EMBEDDED_SCHEDULER=1
set "URL=http://127.0.0.1:%PORT%/"
set "LOGDIR=%ROOT%\artifacts"
set "BOOTLOG=%LOGDIR%\desktop_bootstrap.log"
set "SRVLOG=%LOGDIR%\desktop_server.log"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set "PY=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo ERROR: Missing .venv\Scripts\python.exe
  pause
  exit /b 1
)

echo.
echo ============================================
echo   StockScope India - offline desktop start
echo ============================================
echo   %ROOT%
echo   %URL%
echo ============================================
echo.

echo [1/4] ML check...
"%PY%" -c "import pandas, lightgbm, numpy; print('ok')" > "%BOOTLOG%" 2>&1
if errorlevel 1 (
  echo Repairing packages...
  "%PY%" -m pip install --force-reinstall --no-cache-dir "pandas==2.2.3" "pyarrow==17.0.0" "numpy==2.1.3" "lightgbm==4.5.0" "scikit-learn==1.5.2" >> "%BOOTLOG%" 2>&1
)

echo [2/4] Database + model...
"%PY%" manage.py bootstrap >> "%BOOTLOG%" 2>&1
if errorlevel 1 (
  echo Bootstrap warning - continuing. See artifacts\desktop_bootstrap.log
  "%PY%" manage.py migrate --noinput >> "%BOOTLOG%" 2>&1
)

echo [3/4] Free port %PORT% and start server...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$conns=Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue; foreach($c in @($conns)){ Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }"

echo [%date% %time%] starting server> "%SRVLOG%"
start "StockScope India" /D "%ROOT%" "%ROOT%\scripts\run_server.bat" %PORT%

echo [4/4] Waiting for server, then open browser...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\open_in_chrome.ps1" -Url "%URL%" -WaitSeconds 90
if errorlevel 1 (
  echo.
  echo FAILED to open - server not ready
  echo Bootstrap log:
  powershell -NoProfile -Command "Get-Content -LiteralPath '%BOOTLOG%' -Tail 25 -ErrorAction SilentlyContinue"
  echo.
  echo Server log:
  powershell -NoProfile -Command "Get-Content -LiteralPath '%SRVLOG%' -Tail 40 -ErrorAction SilentlyContinue"
  echo.
  pause
  exit /b 1
)

echo.
echo Opened %URL%
echo Keep the StockScope India window open while using the app.
timeout /t 5 >nul
endlocal
