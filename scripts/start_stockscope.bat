@echo off
setlocal
cd /d "%~dp0.."
set PORT=8001
set RUN_EMBEDDED_SCHEDULER=1
set URL=http://127.0.0.1:8001/

set PY=python
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe

echo [StockScope] Checking pandas/pyarrow/lightgbm...
"%PY%" -c "import pandas, pyarrow, lightgbm, numpy; assert numpy.__version__.startswith('2.1'); print('ok', pandas.__version__, lightgbm.__version__, numpy.__version__)" 2>nul
if errorlevel 1 (
  echo [StockScope] Repairing ML stack for Windows DLL policy...
  "%PY%" -m pip install --force-reinstall --no-cache-dir "pandas==2.2.3" "pyarrow==17.0.0" "numpy==2.1.3" "lightgbm==4.5.0" "scikit-learn==1.5.2"
)

echo [StockScope] Preparing model/data if needed...
"%PY%" manage.py bootstrap
if errorlevel 1 (
  echo [StockScope] bootstrap warning — continuing with start
)

echo [StockScope] Starting server on %URL% ...
start "StockScope India" /MIN "%PY%" manage.py run --port %PORT%

echo [StockScope] Opening Chrome...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0open_in_chrome.ps1" -Url "%URL%" -WaitSeconds 60

endlocal
