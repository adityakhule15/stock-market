# StockScope India - desktop launcher with live console status
$ErrorActionPreference = "Continue"
$Host.UI.RawUI.WindowTitle = "StockScope India - starting"

$Root = Split-Path -Parent $PSScriptRoot
if (-not $Root) { $Root = (Get-Location).Path }
Set-Location $Root

$Port = 8001
$Url = "http://127.0.0.1:$Port/"
$LogDir = Join-Path $Root "artifacts"
$BootLog = Join-Path $LogDir "desktop_bootstrap.log"
$SrvLog = Join-Path $LogDir "desktop_server.log"
$Py = Join-Path $Root ".venv\Scripts\python.exe"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$env:RUN_EMBEDDED_SCHEDULER = "1"
$env:PYTHONUNBUFFERED = "1"

function Stamp { (Get-Date).ToString("HH:mm:ss") }
function Info([string]$msg) { Write-Host ("[{0}] {1}" -f (Stamp), $msg) -ForegroundColor Cyan }
function Ok([string]$msg)   { Write-Host ("[{0}] OK  {1}" -f (Stamp), $msg) -ForegroundColor Green }
function Warn([string]$msg) { Write-Host ("[{0}] !!  {1}" -f (Stamp), $msg) -ForegroundColor Yellow }
function Err([string]$msg)  { Write-Host ("[{0}] ERR {1}" -f (Stamp), $msg) -ForegroundColor Red }
function Step([string]$msg) {
  Write-Host ""
  Write-Host ("========== {0} ==========" -f $msg) -ForegroundColor White
}

function Invoke-StockScopePy {
  param(
    [Parameter(Mandatory = $true)][string[]]$PyArgs,
    [Parameter(Mandatory = $true)][string]$Label
  )
  Info "RUNNING: $Label"
  Info ("Command: {0} {1}" -f $Py, ($PyArgs -join " "))
  Info "Status: LOADING (live output below)"
  Add-Content -LiteralPath $BootLog -Value ("`n---- {0} {1} ----" -f (Get-Date), $Label)

  # Call python directly - never use a param named $Args (PowerShell reserved)
  & $Py -u @PyArgs 2>&1 | ForEach-Object {
    $line = "$_"
    if ($line -match '(?i)error|traceback|exception|failed') {
      Write-Host ("  ! {0}" -f $line) -ForegroundColor DarkYellow
      Add-Content -LiteralPath $BootLog -Value ("ERR: " + $line)
    } else {
      Write-Host ("  | {0}" -f $line)
      Add-Content -LiteralPath $BootLog -Value $line
    }
  }
  $code = $LASTEXITCODE
  if ($null -eq $code) { $code = 0 }
  if ($code -eq 0) { Ok "$Label finished (exit 0)" }
  else {
    Warn "$Label finished with exit code $code"
    Warn "If you see a traceback above, that is the error."
  }
  return $code
}

Write-Host ""
Write-Host "============================================" -ForegroundColor White
Write-Host "  StockScope India - offline desktop start" -ForegroundColor White
Write-Host "============================================" -ForegroundColor White
Info "Project : $Root"
Info "Python  : $Py"
Info "URL     : $Url"
Info "Boot log: $BootLog"
Info "Srv log : $SrvLog"
"" | Set-Content -LiteralPath $BootLog

if (-not (Test-Path $Py)) {
  Err "Missing venv python: $Py"
  Read-Host "Press Enter to close"
  exit 1
}

# --- 1 ML check ---
Step "1/5  ML stack check (LOADING)"
$code = Invoke-StockScopePy -Label "import pandas/lightgbm/numpy" -PyArgs @(
  "-c", "import pandas, lightgbm, numpy; print('pandas', pandas.__version__); print('lightgbm', lightgbm.__version__); print('numpy', numpy.__version__)"
)
if ($code -ne 0) {
  Warn "ML import failed - repairing packages (this can take a few minutes)..."
  $null = Invoke-StockScopePy -Label "pip repair ML stack" -PyArgs @(
    "-m", "pip", "install", "--force-reinstall", "--no-cache-dir",
    "pandas==2.2.3", "pyarrow==17.0.0", "numpy==2.1.3", "lightgbm==4.5.0", "scikit-learn==1.5.2"
  )
  $code = Invoke-StockScopePy -Label "re-check ML imports" -PyArgs @(
    "-c", "import pandas, lightgbm, numpy; print('ok')"
  )
  if ($code -ne 0) {
    Err "ML stack still broken. Fix .venv then retry."
    Read-Host "Press Enter to close"
    exit 1
  }
}

# --- 2 bootstrap ---
Step "2/5  Database migrate + data + model (LOADING)"
Info "This may take seconds, or minutes if it needs to train."
$code = Invoke-StockScopePy -Label "manage.py bootstrap" -PyArgs @("manage.py", "bootstrap")
if ($code -ne 0) {
  Warn "Bootstrap reported an error - trying migrate only..."
  $null = Invoke-StockScopePy -Label "manage.py migrate" -PyArgs @("manage.py", "migrate", "--noinput")
}

# --- 3 free port ---
Step "3/5  Free port $Port (LOADING)"
try {
  $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  if ($conns) {
    foreach ($c in @($conns)) {
      Info ("Stopping old process PID {0} on port {1}" -f $c.OwningProcess, $Port)
      Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
    Ok "Port $Port freed"
  } else {
    Ok "Port $Port already free"
  }
} catch {
  Warn ("Port cleanup: {0}" -f $_.Exception.Message)
}

# --- 4 start server ---
Step "4/5  Start server window (LOADING)"
$serverBat = Join-Path $PSScriptRoot "run_server.bat"
Info "Opening server console: $serverBat"
Start-Process -FilePath "cmd.exe" -ArgumentList @("/k", "`"$serverBat`" $Port") -WorkingDirectory $Root
Ok "Server window launched"

# --- 5 wait + browser ---
Step "5/5  Wait until site is ready, then open browser (LOADING)"
$openPs1 = Join-Path $PSScriptRoot "open_in_chrome.ps1"
& $openPs1 -Url $Url -WaitSeconds 90
if ($LASTEXITCODE -ne 0) {
  Err "Server did not become ready in time."
  Write-Host ""
  Warn "Last bootstrap log lines:"
  if (Test-Path $BootLog) { Get-Content -LiteralPath $BootLog -Tail 30 | ForEach-Object { Write-Host ("  | " + $_) } }
  Warn "Last server log lines:"
  if (Test-Path $SrvLog) { Get-Content -LiteralPath $SrvLog -Tail 40 | ForEach-Object { Write-Host ("  | " + $_) } }
  Read-Host "Press Enter to close"
  exit 1
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Ok "App is open: $Url"
Info "Keep the SERVER window open while you use StockScope."
Info "Close the SERVER window to stop the app."
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Info "This starter window will close in 8 seconds..."
Start-Sleep -Seconds 8
exit 0
