@echo off
REM ===========================================================================
REM  NANO DESKTOP - the desktop launcher.
REM
REM  To start Nano as a desktop application: double-click this file.
REM
REM  This launches the Electron shell, which then owns everything: it starts
REM  the Python backend itself, waits for it to be genuinely ready, and only
REM  then opens the Nano window. No browser tab is ever opened.
REM
REM  NANO.bat still exists and still works. It runs the same backend in browser
REM  mode, which is useful for development, but it has no tray, no global
REM  shortcut and no voice overlay.
REM
REM  ENCODING RULE (do not break this):
REM  This file must stay PURE ASCII with CRLF line endings. cmd.exe parses .bat
REM  files using the OEM codepage (850/437), not UTF-8, so accented text or box
REM  drawing saved as UTF-8 corrupts the parser and the window closes instantly.
REM  That was a real bug in NANO.bat. Keep it ASCII.
REM
REM  PARENTHESIS RULE (do not break this):
REM  Inside an IF block, an unescaped ( or ) in an echo argument ends the block
REM  early and everything after it runs unconditionally. Escape them: ^( ^)
REM ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title Nano Desktop

REM Editors that are themselves Electron applications (VS Code, for one)
REM export ELECTRON_RUN_AS_NODE=1 for their child processes. Inheriting it
REM makes the electron binary run as PLAIN NODE: require("electron") then
REM returns the npm shim instead of the real API and the app dies with an
REM unexplained "cannot read property of undefined". Clear it.
set "ELECTRON_RUN_AS_NODE="

echo.
echo  ==========================================
echo    NANO DESKTOP
echo  ==========================================
echo.

REM --- 1. Python runtime --------------------------------------------------
REM A local .venv wins so dependencies always come from one known interpreter.
REM Electron looks for the same interpreters in the same order.
set "NANO_PY=python"
if exist ".venv\Scripts\python.exe" set "NANO_PY=.venv\Scripts\python.exe"

"%NANO_PY%" --version >nul 2>&1
if errorlevel 1 (
    echo  [NANO] Python ........ FAILED
    echo.
    echo  Startup failed.
    echo  Reason:    Python was not found on this system.
    echo  Component: Python runtime
    echo  Fix:       Install Python 3.12 or newer and make sure "python"
    echo             works in a normal Command Prompt.
    echo.
    goto :fail
)

REM The `call` is load-bearing: for /f strips the outer quote pair of the whole
REM in-clause, which breaks an interpreter path containing spaces.
for /f "delims=" %%V in ('call "%NANO_PY%" -c "import sys;print(sys.version.split()[0])" 2^>nul') do set "PYVER=%%V"
echo  [NANO] Python ........ OK ^(%PYVER%^)

REM --- 2. Core dependencies -----------------------------------------------
"%NANO_PY%" -c "import eel, httpx, yaml, psutil" >nul 2>&1
if errorlevel 1 (
    echo  [NANO] Dependencies .. FAILED
    echo.
    echo  Startup failed.
    echo  Reason:    Required Python packages are missing in this interpreter.
    echo  Component: Dependencies
    echo  Fix:       "%NANO_PY%" -m pip install -r requirements.txt
    echo.
    goto :fail
)
echo  [NANO] Dependencies .. OK

REM --- 3. Electron ---------------------------------------------------------
if not exist "electron\node_modules\electron\package.json" (
    echo  [NANO] Electron ...... INSTALLING ^(first run only^)
    pushd electron
    call npm install
    popd
)
if not exist "electron\node_modules\electron\package.json" (
    echo  [NANO] Electron ...... FAILED
    echo.
    echo  Startup failed.
    echo  Reason:    The Electron shell is not installed.
    echo  Component: Electron
    echo  Fix:       cd electron  ^&^&  npm install
    echo.
    goto :fail
)
echo  [NANO] Electron ...... OK

REM --- 4. Application icon -------------------------------------------------
REM Generated from Nano's own mark by scripts\build_app_icon.ps1. Rebuilt only
REM when missing, so a normal launch costs nothing.
if not exist "electron\assets\icon.ico" (
    echo  [NANO] Icon .......... BUILDING
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\build_app_icon.ps1" >nul 2>&1
)
if exist "electron\assets\icon.ico" (
    echo  [NANO] Icon .......... OK
) else (
    echo  [NANO] Icon .......... MISSING ^(Nano will use the default window icon^)
)

REM --- 5. Frontend (build when missing OR when the sources changed) -------
REM core.frontend_build compares the newest frontend source against a stamp
REM written at build time and exits 1 only when a rebuild is really needed, so
REM the common case skips npm entirely.
set "NANO_BUILD=0"
if not exist "frontend\out\index.html" set "NANO_BUILD=1"
if "%NANO_BUILD%"=="0" (
    "%NANO_PY%" -m core.frontend_build check
    if errorlevel 1 set "NANO_BUILD=1"
)

if "%NANO_BUILD%"=="1" (
    echo  [NANO] Frontend ...... BUILDING ^(sources changed, this takes a minute^)
    pushd frontend
    call npm run build
    popd
    if not exist "frontend\out\index.html" (
        echo  [NANO] Frontend ...... FAILED
        echo.
        echo  Startup failed.
        echo  Reason:    The frontend build did not produce frontend\out\index.html
        echo  Component: Frontend
        echo  Fix:       cd frontend  ^&^&  npm install  ^&^&  npm run build
        echo.
        goto :fail
    )
    "%NANO_PY%" -m core.frontend_build stamp
    echo  [NANO] Frontend ...... OK ^(rebuilt^)
) else (
    echo  [NANO] Frontend ...... CURRENT
)

REM --- 6. Desktop shell ----------------------------------------------------
REM From here Electron is in charge: it spawns core\main.py itself with
REM --mode electron --desktop-control, waits for the control channel and the
REM HTTP server, and only then shows the window.
echo  [NANO] Desktop ....... STARTING
echo.
echo  ------------------------------------------
echo   Keep this window open while using Nano.
echo   Closing the Nano window hides it to the
echo   system tray so Ctrl+Shift+Space keeps
echo   working. Use "Sair do Nano" in the tray
echo   menu to quit completely.
echo  ------------------------------------------
echo.

pushd electron
call npx electron .
set "RC=%ERRORLEVEL%"
popd

echo.
if "%RC%"=="0" (
    echo  [NANO] Nano Desktop closed normally.
    endlocal
    exit /b 0
)

echo  [NANO] Desktop ....... EXITED WITH CODE %RC%
echo.
echo  Nano Desktop stopped unexpectedly.
echo  Component: Electron shell (electron\main.js)
echo  The error should be visible in the output above.
echo  A full backend log is also written to: logs\nano.log
echo.

:fail
echo  Press any key to close this window.
pause >nul
endlocal
exit /b 1
