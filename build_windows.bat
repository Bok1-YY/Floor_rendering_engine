@echo off
REM ============================================================
REM  Floor engine - single-exe packaging script (Windows / Nuitka)
REM  Output: dist\FloorEngine.exe  (onefile, native code, bundled web UI)
REM
REM  Requirements:
REM    1) Python 3.10-3.12 installed and on PATH
REM    2) Floor_engine_server\web\out already exists (prebuilt static UI,
REM       platform-independent, copied over with the folder)
REM    3) Internet for the first build (Nuitka downloads MinGW64)
REM
REM  This file is pure ASCII on purpose: cmd parses .bat with the system
REM  codepage (GBK), so any non-ASCII text would be mangled. Do not add
REM  Chinese characters here.
REM
REM  Usage: double-click this file, or run it from a cmd in this folder.
REM ============================================================
setlocal

REM ---- editable metadata ----
set "APP_NAME=FloorEngine"
set "COMPANY=YourCompany"
set "PRODUCT=Floor Engine"
set "VERSION=7.0.0"

REM go to the PARENT of this script's folder, so that
REM "Floor_engine_server" is importable as a package
cd /d "%~dp0.."
set "PKG=Floor_engine_server"

echo.
echo ============================================================
echo  [1/4] Checking prebuilt web UI: %PKG%\web\out
echo ============================================================
if not exist "%PKG%\web\out\index.html" (
  echo   [ERROR] %PKG%\web\out\index.html not found.
  echo           Build the frontend first on a dev machine:
  echo               cd web  ^&^&  npx next build
  echo           then copy the whole folder here (with web\out).
  goto :fail
)
echo   OK

echo.
echo ============================================================
echo  [2/4] Preparing clean Python venv (.buildenv)
echo ============================================================
if not exist ".buildenv\Scripts\python.exe" (
  python -m venv .buildenv || goto :fail
)
call ".buildenv\Scripts\activate.bat" || goto :fail
python -m pip install --upgrade pip
python -m pip install nuitka zstandard ordered-set fastapi uvicorn h11 python-multipart Pillow==12.2.0 requests==2.34.2 numpy==2.4.6 deep-translator==1.11.4 urllib3==2.7.0 "python-pptx>=0.6.23"
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo  [3/4] Nuitka compile (first run 5-15 min, downloads compiler)
echo ============================================================
python -m nuitka ^
  --onefile ^
  --assume-yes-for-downloads ^
  --output-dir=dist ^
  --output-filename=%APP_NAME%.exe ^
  --company-name="%COMPANY%" ^
  --product-name="%PRODUCT%" ^
  --file-version=%VERSION% --product-version=%VERSION% ^
  --onefile-tempdir-spec="{CACHE_DIR}\%APP_NAME%\{VERSION}" ^
  --include-package=uvicorn ^
  --include-package=anyio ^
  --include-package=PIL ^
  --include-package=multipart ^
  --include-package-data=certifi ^
  --include-package-data=pptx ^
  --include-data-dir=%PKG%\web\out=%PKG%\web\out ^
  --include-data-dir=%PKG%\assets=%PKG%\assets ^
  %PKG%\serve.py
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo  [4/4] Done!  Output:  dist\%APP_NAME%.exe
echo ============================================================
echo   Double-click the exe to start the server and open the browser (port 7870).
echo   engine_config.json / output_files\ are created next to the exe.
echo.
pause
exit /b 0

:fail
echo.
echo   [FAILED] A step above errored. Send me the message above.
pause
exit /b 1
