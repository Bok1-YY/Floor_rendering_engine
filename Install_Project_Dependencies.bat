@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools\setup_windows.ps1 %*
set "SETUP_RESULT=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %SETUP_RESULT%
