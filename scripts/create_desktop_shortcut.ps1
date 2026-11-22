# Creates a Desktop shortcut that launches StockScope in Chrome on port 8001
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $root "scripts\start_stockscope.bat"
$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "StockScope India.lnk"

$w = New-Object -ComObject WScript.Shell
$sc = $w.CreateShortcut($lnkPath)
$sc.TargetPath = $bat
$sc.WorkingDirectory = $root
$sc.WindowStyle = 7  # minimized
$sc.Description = "Start StockScope India (port 8001) and open in Chrome"
# Prefer Chrome icon if available
$chromeIcon = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($chromeIcon) {
  $sc.IconLocation = "$chromeIcon,0"
}
$sc.Save()
Write-Host "Desktop shortcut created: $lnkPath"
Write-Host "Double-click 'StockScope India' to start on http://127.0.0.1:8001"
