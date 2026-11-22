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

function Stamp { (Get-Date).ToString("HH:mm:ss") }
function Info([string]$msg) { Write-Host ("[{0}] {1}" -f (Stamp), $msg) -ForegroundColor Cyan }
function Ok([string]$msg)   { Write-Host ("[{0}] OK  {1}" -f (Stamp), $msg) -ForegroundColor Green }
function Warn([string]$msg) { Write-Host ("[{0}] !!  {1}" -f (Stamp), $msg) -ForegroundColor Yellow }
function Err([string]$msg)  { Write-Host ("[{0}] ERR {1}" -f (Stamp), $msg) -ForegroundColor Red }
function Step([string]$msg) {
  Write-Host ""
  Write-Host ("========== {0} ==========" -f $msg) -ForegroundColor White
}

function Run-Py {
  param([string[]]$Args, [string]$Label)
  Info "RUNNING: $Label"
  Info ("Command: {0} {1}" -f $Py, ($Args -join " "))
  Info "Status: LOADING (live output below)"
  Add-Content -LiteralPath $BootLog -Value ("`n---- {0} {1} ----" -f (Get-Date), $Label)

  $argLine = ($Args | ForEach-Object {
    if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
  }) -join " "

  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $Py
  $psi.Arguments = $argLine
  $psi.WorkingDirectory = $Root
  $psi.UseShellExecute = $false
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.CreateNoWindow = $true
  $psi.EnvironmentVariables["RUN_EMBEDDED_SCHEDULER"] = "1"
  $psi.EnvironmentVariables["PYTHONUNBUFFERED"] = "1"

  $p = New-Object System.Diagnostics.Process
  $p.StartInfo = $psi
  $p.EnableRaisingEvents = $true

  $handlerOut = {
    if ($EventArgs.Data) {
      $line = $EventArgs.Data
      Write-Host ("  | {0}" -f $line)
      Add-Content -LiteralPath $Event.MessageData -Value $line
    }
  }
  $handlerErr = {
    if ($EventArgs.Data) {
      $line = $EventArgs.Data
      Write-Host ("  ! {0}" -f $line) -ForegroundColor DarkYellow
      Add-Content -LiteralPath $Event.MessageData -Value ("ERR: " + $line)
    }
  }

  $outSub = Register-ObjectEvent -InputObject $p -EventName OutputDataReceived -Action $handlerOut -MessageData $BootLog
  $errSub = Register-ObjectEvent -InputObject $p -EventName ErrorDataReceived -Action $handlerErr -MessageData $BootLog

  $null = $p.Start()
  $p.BeginOutputReadLine()
  $p.BeginErrorReadLine()
  while (-not $p.HasExited) {
    Start-Sleep -Milliseconds 200
  }
  Start-Sleep -Milliseconds 300
  Unregister-Event -SourceIdentifier $outSub.Name -ErrorAction SilentlyContinue
  Unregister-Event -SourceIdentifier $errSub.Name -ErrorAction SilentlyContinue
  Remove-Job $outSub -Force -ErrorAction SilentlyContinue
  Remove-Job $errSub -Force -ErrorAction SilentlyContinue

  $code = $p.ExitCode
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
$code = Run-Py @("-c", "import pandas, lightgbm, numpy; print('pandas', pandas.__version__); print('lightgbm', lightgbm.__version__); print('numpy', numpy.__version__)") "import pandas/lightgbm/numpy"
if ($code -ne 0) {
  Warn "ML import failed - repairing packages (this can take a few minutes)..."
  $null = Run-Py @(
    "-m", "pip", "install", "--force-reinstall", "--no-cache-dir",
    "pandas==2.2.3", "pyarrow==17.0.0", "numpy==2.1.3", "lightgbm==4.5.0", "scikit-learn==1.5.2"
  ) "pip repair ML stack"
  $code = Run-Py @("-c", "import pandas, lightgbm, numpy; print('ok')") "re-check ML imports"
  if ($code -ne 0) {
    Err "ML stack still broken. Fix .venv then retry."
    Read-Host "Press Enter to close"
    exit 1
  }
}

# --- 2 bootstrap ---
Step "2/5  Database migrate + data + model (LOADING)"
Info "This may take seconds, or minutes if it needs to train."
$code = Run-Py @("manage.py", "bootstrap") "manage.py bootstrap"
if ($code -ne 0) {
  Warn "Bootstrap reported an error - trying migrate only..."
  $null = Run-Py @("manage.py", "migrate", "--noinput") "manage.py migrate"
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
