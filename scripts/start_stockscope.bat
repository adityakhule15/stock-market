@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
title StockScope India — starting
set PORT=8001
set RUN_EMBEDDED_SCHEDULER=1
set URL=http://127.0.0.1:%PORT%/

set PY=python
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe

echo.
echo ============================================
echo   StockScope India — offline desktop start
echo ============================================
echo   Project: %CD%
echo   Python:  %PY%
echo   URL:     %URL%
echo ============================================
echo.

echo [1/5] Checking ML stack (pandas / lightgbm)...
"%PY%" -c "import pandas, pyarrow, lightgbm, numpy; print('ok', pandas.__version__, lightgbm.__version__, numpy.__version__)" 2>nul
if errorlevel 1 (
  echo [StockScope] Repairing ML stack for Windows...
  "%PY%" -m pip install --force-reinstall --no-cache-dir "pandas==2.2.3" "pyarrow==17.0.0" "numpy==2.1.3" "lightgbm==4.5.0" "scikit-learn==1.5.2"
  if errorlevel 1 (
    echo [StockScope] ERROR: could not install ML packages. Fix .venv then retry.
    pause
    exit /b 1
  )
)

echo.
echo [2/5] Database migrate + data + model train/update...
echo        (first run or after an overhaul can take several minutes)
echo.
"%PY%" manage.py bootstrap
if errorlevel 1 (
  echo [StockScope] WARNING: bootstrap reported an error — trying train once more...
  "%PY%" manage.py migrate --noinput
  "%PY%" manage.py train --mode swing --retire-legacy
)

echo.
echo [3/5] Making sure a trained model exists...
"%PY%" -c "import django; import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup(); from ml import registry; c=registry.champion('swing_lgbm'); print('champion', c.name if c else None, 'v'+str(c.version) if c else '', 'honest='+str((c.metrics or {}).get('honest')) if c else ''); raise SystemExit(0 if c else 2)"
if errorlevel 1 (
  echo [StockScope] No champion — forcing full train (this may take a while)...
  "%PY%" manage.py train --mode swing --full --retire-legacy
)

echo.
echo [4/5] Starting server on %URL% ...
start "StockScope India" /MIN "%PY%" manage.py run --port %PORT%

echo [5/5] Opening Chrome...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0open_in_chrome.ps1" -Url "%URL%" -WaitSeconds 90

echo.
echo Done. Leave the minimized "StockScope India" window running while you use the app.
echo Close that window to stop the server.
echo.
endlocal
