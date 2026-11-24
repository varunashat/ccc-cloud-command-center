@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
set ARGS=
if exist folders.txt for /f "usebackq delims=" %%f in ("folders.txt") do set ARGS=!ARGS! --folder "%%f"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
echo Starting CCC laptop executor. Close this window or press Ctrl+C to disconnect.
%PY% executor.py %ARGS%
pause
