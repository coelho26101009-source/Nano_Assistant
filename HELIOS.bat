@echo off
cd /d "%~dp0"
chcp 65001 >nul
title H.E.L.I.O.S. V7

REM Usa Python embutido se existir, senão usa o do sistema
if exist "python_embedded\python.exe" (
    set PYTHON=python_embedded\python.exe
) else if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
) else (
    set PYTHON=py
)

echo.
echo  ==========================================
echo   H.E.L.I.O.S. V7
echo  ==========================================

REM Build do frontend se necessario
if not exist "frontend\out" (
    echo  A construir frontend...
    cd frontend
    call npm install --silent 2>nul
    call npm run build
    cd ..
)

echo  A iniciar HELIOS...
echo.
%PYTHON% core\main.py
pause
