param(
  [Parameter(Mandatory = $true)][string]$Url,
  [int]$WaitSeconds = 90
)

$ErrorActionPreference = "Continue"

function Stamp { (Get-Date).ToString("HH:mm:ss") }
function Info([string]$msg) { Write-Host ("[{0}] {1}" -f (Stamp), $msg) -ForegroundColor Cyan }
function Ok([string]$msg)   { Write-Host ("[{0}] OK  {1}" -f (Stamp), $msg) -ForegroundColor Green }
function Warn([string]$msg) { Write-Host ("[{0}] !!  {1}" -f (Stamp), $msg) -ForegroundColor Yellow }
function Err([string]$msg)  { Write-Host ("[{0}] ERR {1}" -f (Stamp), $msg) -ForegroundColor Red }

Info "Waiting up to $WaitSeconds s for $Url ..."
Info "Status: LOADING (checking every second)"

$deadline = (Get-Date).AddSeconds($WaitSeconds)
$up = $false
$lastErr = ""
$n = 0
while ((Get-Date) -lt $deadline) {
  $n++
  try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
    Info ("Check #{0}: HTTP {1}" -f $n, $r.StatusCode)
    if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) {
      $up = $true
      break
    }
  } catch {
    $lastErr = $_.Exception.Message
    if (($n % 5) -eq 1) {
      Info ("Check #{0}: not ready yet - {1}" -f $n, $lastErr)
    } else {
      Write-Host ("[{0}]   ... still loading (try {1})" -f (Stamp), $n) -ForegroundColor DarkGray
    }
  }
  Start-Sleep -Seconds 1
}

if (-not $up) {
  Err "Server not ready after $WaitSeconds s."
  Err "Last error: $lastErr"
  exit 1
}

Ok "Server is UP"
Info "Opening browser..."

$chromeCandidates = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $chromeCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

$edgeCandidates = @(
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
)
$edge = $edgeCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

try {
  if ($chrome) {
    Start-Process -FilePath $chrome -ArgumentList @("--new-window", $Url) -ErrorAction Stop
    Ok "Opened in Chrome: $Url"
    exit 0
  }
  if ($edge) {
    Start-Process -FilePath $edge -ArgumentList @("--new-window", $Url) -ErrorAction Stop
    Ok "Opened in Edge: $Url"
    exit 0
  }
  Start-Process $Url -ErrorAction Stop
  Ok "Opened default browser: $Url"
  exit 0
} catch {
  Err ("Failed to open browser: {0}" -f $_.Exception.Message)
  Warn "Try opening manually: $Url"
  try { Start-Process $Url } catch {}
  exit 0
}
