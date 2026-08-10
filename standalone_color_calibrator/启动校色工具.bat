@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_GUI=..\.venv\Scripts\pythonw.exe"
set "PYTHON_CONSOLE=..\.venv\Scripts\python.exe"

if not exist "%PYTHON_GUI%" (
  where pythonw.exe >nul 2>nul
  if errorlevel 1 goto :missing_python
  set "PYTHON_GUI=pythonw.exe"
  set "PYTHON_CONSOLE=python.exe"
)

if /i "%~1"=="--check" (
  "%PYTHON_CONSOLE%" app.py --help >nul
  if errorlevel 1 goto :launch_failed
  echo Color calibrator launcher OK.
  exit /b 0
)

start "" "%PYTHON_GUI%" "%CD%\app.py"
if errorlevel 1 goto :launch_failed
exit /b 0

:missing_python
echo Python was not found.
echo Install Python 3.10 or newer, then run: pip install -r requirements.txt
pause
exit /b 1

:launch_failed
echo Failed to start the color calibrator.
echo Run this command to see details: python app.py
pause
exit /b 1
