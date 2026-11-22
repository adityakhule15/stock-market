param(
  [Parameter(Mandatory = $true)][string]$Url,
  [int]$WaitSeconds = 90
)

$ErrorActionPreference = "Continue"
Write-Host "Waiting up to $WaitSeconds s for $Url ..."

$deadline = (Get-Date).AddSeconds($WaitSeconds)
$up = $false
$lastErr = ""
while ((Get-Date) -lt $deadline) {
  try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
    if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) {
      $up = $true
      break
    }
  } catch {
    $lastErr = $_.Exception.Message
  }
  Start-Sleep -Seconds 1
}

if (-not $up) {
  Write-Host "Server not ready after $WaitSeconds s. Last error: $lastErr"
  exit 1
}

Write-Host "Server is up. Opening browser..."

$chromeCandidates = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $chromeCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

$edgeCandidates = @(
  "$env:ProgramFiles (x86)\Microsoft\Edge\Application\msedge.exe",
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
)
$edge = $edgeCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

try {
  if ($chrome) {
    Start-Process -FilePath $chrome -ArgumentList @("--new-window", $Url) -ErrorAction Stop
    Write-Host "Opened in Chrome: $Url"
    exit 0
  }
  if ($edge) {
    Start-Process -FilePath $edge -ArgumentList @("--new-window", $Url) -ErrorAction Stop
    Write-Host "Opened in Edge: $Url"
    exit 0
  }
  Start-Process $Url -ErrorAction Stop
  Write-Host "Opened default browser: $Url"
  exit 0
} catch {
  Write-Host "Failed to open browser: $($_.Exception.Message)"
  # Still success for server — user can paste URL
  Start-Process $Url
  exit 0
}
