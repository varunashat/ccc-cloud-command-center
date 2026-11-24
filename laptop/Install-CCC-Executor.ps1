# Installs the CCC laptop executor for the current user (no admin needed). Safe to re-run.
#   Right-click > Run with PowerShell, or:  powershell -ExecutionPolicy Bypass -File .\Install-CCC-Executor.ps1
$ErrorActionPreference = 'Stop'
$dest = Join-Path $env:LOCALAPPDATA 'CCC-Executor'
New-Item -ItemType Directory -Force $dest | Out-Null
foreach ($f in 'executor.py', 'policy.py', 'Start-CCC-Executor.cmd') { Copy-Item (Join-Path $PSScriptRoot $f) $dest -Force }
Set-Content (Join-Path $dest '__init__.py') ''

$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
if (-not $py) {
  Write-Host 'Python 3 was not found. Install it (user scope, no admin): winget install -e --id Python.Python.3.12 --scope user  then run this installer again.' -ForegroundColor Yellow
}
foreach ($t in 'az', 'git', 'gh') {
  if (Get-Command $t -ErrorAction SilentlyContinue) { Write-Host "$t : found" } else { Write-Host "$t : NOT found (optional; features that need it will report 'not installed')" -ForegroundColor Yellow }
}

# Folder choice is typed (works under Constrained Language Mode, which blocks pickers and COM objects).
$folders = New-Object System.Collections.Generic.List[string]
Write-Host "`nType a folder the CCC assistant may read and run git in (for example C:\Work\my-repo). Press Enter on an empty line when finished."
while ($true) {
  $p = (Read-Host 'Folder').Trim().Trim('"')
  if (-not $p) { break }
  if (Test-Path -LiteralPath $p -PathType Container) { $folders.Add((Resolve-Path -LiteralPath $p).Path); Write-Host "Added: $p" } else { Write-Host "Not a folder, skipped: $p" -ForegroundColor Yellow }
}
Set-Content (Join-Path $dest 'folders.txt') $folders
Write-Host "Folders saved in $dest\folders.txt (edit that file any time to change them)."

# Plain launcher on the desktop (no COM / shortcut object needed).
$desk = [Environment]::GetFolderPath('Desktop')
Set-Content (Join-Path $desk 'Start CCC Executor.cmd') ('@echo off' + "`r`n" + 'call "' + (Join-Path $dest 'Start-CCC-Executor.cmd') + '"')
Write-Host "`nDone. Double-click 'Start CCC Executor.cmd' on your desktop, note the pairing code, then open Environment & Deployment > Run on my laptop > Connect."
Write-Host "Sign in yourself with:  az login   and   gh auth login   (MFA included). The executor never sees your password."
