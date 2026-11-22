param(
  [Parameter(Mandatory = $true)][string]$Url,
  [int]$WaitSeconds = 45
)

$ErrorActionPreference = "SilentlyContinue"

# Wait until URL responds (optional; skip wait if already up)
$deadline = (Get-Date).AddSeconds($WaitSeconds)
$up = $false
while ((Get-Date) -lt $deadline) {
  try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
    if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { $up = $true; break }
  } catch {}
  Start-Sleep -Seconds 1
}

$chromeCandidates = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $chromeCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if ($chrome) {
  # Force a new Chrome window/tab with this URL
  Start-Process -FilePath $chrome -ArgumentList @("--new-window", $Url)
  Write-Host "Opened in Chrome: $Url"
  exit 0
}

# Fallback: default browser
Start-Process $Url
Write-Host "Chrome not found — opened default browser: $Url"
exit 0
