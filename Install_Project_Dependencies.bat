@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Install dependencies for both projects

set "EQUIPMENT=%USERPROFILE%\production-equipment-system\equipment-system"
set "EQUIPMENT_MOBILE=%EQUIPMENT%\mobile"
set "FLOOR=%USERPROFILE%\Floor_engine_Linux"
set "FLOOR_WEB=%FLOOR%\web"
set "PATH=%PATH%;%ProgramFiles%\Git\cmd;%ProgramFiles%\nodejs;%LocalAppData%\Programs\Python\Python312;%LocalAppData%\Programs\Python\Python312\Scripts"

echo ============================================================
echo Installing development dependencies for both projects
echo ============================================================
echo.

if not exist "%EQUIPMENT%\package-lock.json" (
  echo ERROR: Equipment System was not found at:
  echo %EQUIPMENT%
  goto :failed
)
if not exist "%FLOOR%\requirements-dev.txt" (
  echo ERROR: Floor Engine was not found at:
  echo %FLOOR%
  goto :failed
)

where winget.exe >nul 2>nul
if errorlevel 1 (
  echo ERROR: winget is required. Install or update Microsoft App Installer.
  goto :failed
)

where git.exe >nul 2>nul
if errorlevel 1 (
  echo [1/8] Installing Git...
  winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
  if errorlevel 1 goto :failed
) else (
  echo [1/8] Git is already installed.
)

set "NODE_OK=0"
where node.exe >nul 2>nul
if not errorlevel 1 (
  for /f %%V in ('node.exe -p "Number(process.versions.node.split('.')[0])"') do set "NODE_MAJOR=%%V"
  if !NODE_MAJOR! GEQ 22 set "NODE_OK=1"
)
if "!NODE_OK!"=="0" (
  echo [2/8] Installing Node.js LTS...
  winget install --id OpenJS.NodeJS.LTS -e --source winget --accept-package-agreements --accept-source-agreements
  if errorlevel 1 goto :failed
  set "PATH=%PATH%;%ProgramFiles%\nodejs"
) else (
  echo [2/8] Node.js !NODE_MAJOR! is already installed.
)

where npm.cmd >nul 2>nul
if errorlevel 1 (
  echo ERROR: npm.cmd was not found after installing Node.js.
  goto :failed
)

set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python312\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%ProgramFiles%\Python312\python.exe"
if not exist "%PYTHON_EXE%" (
  echo [3/8] Installing Python 3.12...
  winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
  if errorlevel 1 goto :failed
  set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python312\python.exe"
) else (
  echo [3/8] Python 3.12 is already installed.
)
if not exist "%PYTHON_EXE%" (
  echo ERROR: Python 3.12 was not found after installation.
  goto :failed
)

echo [4/8] Installing Equipment System Node.js dependencies...
pushd "%EQUIPMENT%"
call npm.cmd ci --replace-registry-host=always
if errorlevel 1 (
  popd
  goto :failed
)
popd

echo [5/8] Installing Equipment mobile JavaScript dependencies...
pushd "%EQUIPMENT_MOBILE%"
call npm.cmd ci --replace-registry-host=always
if errorlevel 1 (
  popd
  goto :failed
)
popd

echo [6/8] Creating Floor Engine Python environment...
if not exist "%FLOOR%\.venv\Scripts\python.exe" (
  "%PYTHON_EXE%" -m venv "%FLOOR%\.venv"
  if errorlevel 1 goto :failed
)
"%FLOOR%\.venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed
"%FLOOR%\.venv\Scripts\python.exe" -m pip install -r "%FLOOR%\requirements-dev.txt"
if errorlevel 1 goto :failed

echo [7/8] Installing Floor Engine frontend dependencies...
pushd "%FLOOR_WEB%"
call npm.cmd ci --replace-registry-host=always
if errorlevel 1 (
  popd
  goto :failed
)

echo [8/8] Building Floor Engine frontend...
call npm.cmd run build
if errorlevel 1 (
  popd
  goto :failed
)
popd

echo.
echo ============================================================
echo SUCCESS: All Windows runtime and development dependencies
echo were installed for both projects.
echo ============================================================
echo.
echo Equipment System:
echo   %EQUIPMENT%\start-windows.bat
echo Floor Engine:
echo   %FLOOR%\start-windows.bat
echo Development mode:
echo   %FLOOR%\dev-windows.bat
echo.
if not defined PROJECT_INSTALLER_NO_PAUSE pause
exit /b 0

:failed
echo.
echo ============================================================
echo INSTALLATION FAILED. Review the error shown above.
echo Check your network connection, then run this file again.
echo ============================================================
echo.
if not defined PROJECT_INSTALLER_NO_PAUSE pause
exit /b 1
