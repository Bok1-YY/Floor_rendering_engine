@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
for %%I in ("%~dp0.") do set "FLOOR_STOP_ROOT=%%~fI"
title Floor Engine - 关闭所有后台端口

rem Double-click mode elevates once so a hidden process started by another
rem administrator session can still be stopped.  --no-pause is reserved for
rem automated verification and intentionally skips elevation.
if /I "%~1"=="--elevated" goto run
if /I "%~1"=="--no-pause" goto run

fltmc >nul 2>&1
if not errorlevel 1 goto run

set "FLOOR_STOP_BAT=%~f0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { Start-Process -FilePath $env:FLOOR_STOP_BAT -ArgumentList '--elevated' -WorkingDirectory $env:FLOOR_STOP_ROOT -Verb RunAs -ErrorAction Stop } catch { exit 1 }"
if errorlevel 1 (
  echo.
  echo [失败] 没有取得管理员权限，未执行关停。
  pause
  exit /b 1
)
exit /b 0

:run
echo.
echo ============================================================
echo   Floor Engine 一键关闭后台进程和占用端口
echo ============================================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\stop_floor_engine_ports.ps1" -ProjectRoot "%FLOOR_STOP_ROOT%"
set "STOP_EXIT=%ERRORLEVEL%"

echo.
if "%STOP_EXIT%"=="0" (
  echo [完成] Floor Engine 后台进程已经关闭，项目端口已经复核。
) else (
  echo [注意] 有进程未能关闭，请查看上面的占用信息。
)

if /I not "%~1"=="--no-pause" pause
exit /b %STOP_EXIT%
