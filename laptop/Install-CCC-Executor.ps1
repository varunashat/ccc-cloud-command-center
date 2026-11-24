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

Add-Type -AssemblyName System.Windows.Forms
$folders = New-Object System.Collections.Generic.List[string]
do {
  $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
  $dlg.Description = 'Choose a folder the CCC assistant may read and run git in (Cancel when finished)'
  if ($dlg.ShowDialog() -eq 'OK') { $folders.Add($dlg.SelectedPath); Write-Host "Added: $($dlg.SelectedPath)" } else { break }
} while ($true)
Set-Content (Join-Path $dest 'folders.txt') $folders
Write-Host "Selected folders saved in $dest\folders.txt (edit that file to change them)."

$lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Start CCC Executor.lnk'
$sh = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$sh.TargetPath = Join-Path $dest 'Start-CCC-Executor.cmd'; $sh.WorkingDirectory = $dest; $sh.Save()
Write-Host "`nDone. Double-click 'Start CCC Executor' on your desktop, note the pairing code, then open Environment & Deployment > Run on my laptop > Connect."
Write-Host "Sign in yourself with:  az login   and   gh auth login   (MFA included). The executor never sees your password."
