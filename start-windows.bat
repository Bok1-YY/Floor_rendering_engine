@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Floor Engine

if not exist ".venv\Scripts\python.exe" (
  echo The Python virtual environment was not found.
  echo Run the desktop dependency installer first.
  pause
  exit /b 1
)

set "FRONTEND_BUILD_REQUIRED=1"
if exist "web\out\index.html" (
  for /f %%I in ('powershell.exe -NoProfile -Command "$out=(Get-Item -LiteralPath 'web\out\index.html').LastWriteTimeUtc; $stale=$false; foreach($file in Get-ChildItem -LiteralPath 'web\src' -Recurse -File){if($file.LastWriteTimeUtc -gt $out){$stale=$true;break}}; if(-not $stale){foreach($path in @('web\package.json','web\package-lock.json','web\next.config.ts','web\tsconfig.json')){if((Test-Path -LiteralPath $path) -and (Get-Item -LiteralPath $path).LastWriteTimeUtc -gt $out){$stale=$true;break}}}; if($stale){'1'}else{'0'}"') do set "FRONTEND_BUILD_REQUIRED=%%I"
)

if "%FRONTEND_BUILD_REQUIRED%"=="1" (
  if not exist "web\node_modules\next\package.json" (
    echo Frontend dependencies are missing.
  ) else (
    echo Frontend static build is missing or stale.
  )
  echo Install dependencies explicitly if needed, then run npm run build in web.
  pause
  exit /b 1
)

echo Floor Engine is starting at http://127.0.0.1:7870
echo Manual-safe mode is ON. Paid commit is OFF.
echo Keep this window open. Press Ctrl+C to stop the service.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%CD%\tools\start_whole_home_manual.ps1"

if errorlevel 1 (
  echo.
  echo Floor Engine stopped with an error.
  pause
)
