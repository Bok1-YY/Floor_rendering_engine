@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" tools\build_windows.py %*
) else (
  py -3.12 tools\build_windows.py %*
)
set "BUILD_RESULT=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %BUILD_RESULT%
