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
  echo Building the Floor Engine frontend...
  pushd "web"
  if not exist "node_modules\next\package.json" (
    call npm.cmd ci --replace-registry-host=always
    if errorlevel 1 (
      popd
      echo Frontend dependency installation failed.
      pause
      exit /b 1
    )
  )
  call npm.cmd run build
  if errorlevel 1 (
    popd
    echo Frontend build failed.
    pause
    exit /b 1
  )
  popd
)

set "FLOOR_DATA_DIR=%CD%\data"
if not exist "%FLOOR_DATA_DIR%" mkdir "%FLOOR_DATA_DIR%"

echo Floor Engine is starting at http://127.0.0.1:7870
echo Keep this window open. Press Ctrl+C to stop the service.
".venv\Scripts\python.exe" serve.py

if errorlevel 1 (
  echo.
  echo Floor Engine stopped with an error.
  pause
)
