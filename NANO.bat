@echo off
REM ===========================================================================
REM  NANO ASSISTANT - the one and only public launcher.
REM
REM  To start Nano: double-click this file.
REM
REM  ENCODING RULE (do not break this):
REM  This file must stay PURE ASCII. cmd.exe parses .bat files using the OEM
REM  codepage (850/437), not UTF-8. Box-drawing characters and accented text
REM  saved as UTF-8 get mis-decoded, which corrupts the parser and makes the
REM  window flash and close instantly. That was a real bug here. Keep it ASCII.
REM
REM  This launcher is self-contained on purpose: no chain of nested .bat files.
REM ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title Nano Assistant

echo.
echo  ==========================================
echo    NANO ASSISTANT
echo  ==========================================
echo.

REM --- 1. Python runtime --------------------------------------------------
REM A local .venv wins so dependencies always come from one known interpreter.
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

REM The `call` here is load-bearing. `for /f ('"%NANO_PY%" -c ...')` looks
REM right but returns nothing: cmd strips the outer quote pair of the whole
REM in-clause, which breaks the command. `call "..."` keeps the quotes, so an
REM interpreter path containing spaces still works.
for /f "delims=" %%V in ('call "%NANO_PY%" -c "import sys;print(sys.version.split()[0])" 2^>nul') do set "PYVER=%%V"
for /f "delims=" %%P in ('call "%NANO_PY%" -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%P"
echo  [NANO] Python ........ OK ^(%PYVER%^)
echo         %PYEXE%

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

REM --- 3. Frontend (build only when missing) ------------------------------
REM PARENTHESIS RULE (do not break this):
REM Inside an IF block, an unescaped ( or ) in an echo argument ends the block
REM early. That happened here: everything after the "BUILDING (...)" line fell
REM outside the IF and npm ran on EVERY launch, adding about a minute to each
REM startup even when frontend\out was already built. Escape them as ^( and ^).
if not exist "frontend\out\index.html" (
    echo  [NANO] Frontend ...... BUILDING ^(first run, this takes a minute^)
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
)
echo  [NANO] Frontend ...... OK

REM --- 4. Backend ----------------------------------------------------------
REM main.py starts Ollama if needed, starts the wake listener, and opens the
REM UI exactly once. This launcher never opens a browser itself.
echo  [NANO] Backend ....... STARTING
echo.
echo  ------------------------------------------
echo   Keep this window open while using Nano.
echo   Close it (or press Ctrl+C) to stop Nano.
echo  ------------------------------------------
echo.

"%NANO_PY%" core\main.py --mode default
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo  [NANO] Nano closed normally.
    echo.
    echo  Press any key to close this window.
    pause >nul
    endlocal
    exit /b 0
)

echo  [NANO] Backend ....... EXITED WITH CODE %RC%
echo.
echo  Nano stopped unexpectedly.
echo  Component: Backend (core\main.py)
echo  The error should be visible in the output above.
echo  A full log is also written to: nano.log
echo.

:fail
echo  Press any key to close this window.
pause >nul
endlocal
exit /b 1
