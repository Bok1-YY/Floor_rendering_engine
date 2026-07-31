@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Floor Engine API

if not exist ".venv\Scripts\python.exe" (
  echo The Python virtual environment was not found.
  echo Run the desktop dependency installer first.
  pause
  exit /b 1
)

if not exist "web\node_modules\next\package.json" (
  echo Frontend dependencies were not found.
  echo Run the desktop dependency installer first.
  pause
  exit /b 1
)

set "FLOOR_DATA_DIR=%CD%\data"
set "FLOOR_NO_BROWSER=1"
if not exist "%FLOOR_DATA_DIR%" mkdir "%FLOOR_DATA_DIR%"

start "Floor Engine Frontend" /D "%CD%\web" cmd.exe /k npm.cmd run dev
start "" powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 8; Start-Process 'http://localhost:3000'"

echo Backend:  http://127.0.0.1:7870
echo Frontend: http://localhost:3000
echo Close both command windows to stop development mode.
".venv\Scripts\python.exe" serve.py

if errorlevel 1 (
  echo.
  echo Floor Engine backend stopped with an error.
  pause
)
