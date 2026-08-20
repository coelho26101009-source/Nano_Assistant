@echo off
REM ============================================================================
REM  NANO ASSISTANT - canonical launcher
REM
REM  What this does, in order:
REM    1. validate the Python runtime (and report which one it is)
REM    2. build the frontend only if it is missing
REM    3. start the backend ONCE, in this window
REM
REM  What this deliberately does NOT do:
REM    * it does not open a browser. core/main.py opens the UI exactly once.
REM      Both used to do it, which is why two Chrome tabs appeared.
REM    * it does not start Ollama. core/main.py starts the Ollama SERVER only
REM      if it is not already running, and never preloads a model.
REM    * it does not spawn a detached window, so closing this window stops Nano.
REM ============================================================================
setlocal
cd /d "%~dp0"
title Nano Assistant
chcp 65001 >nul

echo.
echo  ==========================================
echo    NANO ASSISTANT
echo  ==========================================
echo.

REM ── 1. Python runtime ──────────────────────────────────────────────────────
REM A local .venv wins, so dependencies always come from one known interpreter.
set "NANO_PY=python"
if exist ".venv\Scripts\python.exe" (
    set "NANO_PY=.venv\Scripts\python.exe"
    echo  [runtime] a usar o ambiente virtual .venv
) else (
    echo  [runtime] a usar o Python do sistema
)

"%NANO_PY%" --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [ERRO] Python nao encontrado. Instala o Python 3.12+ e tenta de novo.
    echo.
    pause
    exit /b 1
)
for /f "delims=" %%V in ('"%NANO_PY%" -c "import sys;print(sys.version.split()[0])"') do set "PYVER=%%V"
for /f "delims=" %%P in ('"%NANO_PY%" -c "import sys;print(sys.executable)"') do set "PYEXE=%%P"
echo  [runtime] Python %PYVER%
echo  [runtime] %PYEXE%

REM ── 2. Frontend (build only when missing) ──────────────────────────────────
if not exist "frontend\out\index.html" (
    echo  [frontend] a construir pela primeira vez...
    pushd frontend
    call npm run build
    popd
    if not exist "frontend\out\index.html" (
        echo.
        echo  [ERRO] O build do frontend falhou. Corre "npm run build" em frontend\.
        echo.
        pause
        exit /b 1
    )
)
echo  [frontend] pronto

REM ── 3. Backend (single instance; it opens the UI itself) ───────────────────
echo  [backend]  a arrancar o Nano...
echo.
echo  Deixa esta janela aberta. Fecha-a (ou Ctrl+C) para encerrar o Nano.
echo.

"%NANO_PY%" core\main.py --mode default
set "EXITCODE=%ERRORLEVEL%"

echo.
if "%EXITCODE%"=="0" (
    echo  Nano encerrado.
) else (
    echo  Nano terminou com o codigo %EXITCODE%.
)
echo.
pause
endlocal
