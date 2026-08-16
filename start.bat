@echo off
cd /d "%~dp0"
title Nano Assistant
color 0b
chcp 65001 >nul

echo.
echo  ==========================================
echo   NANO ASSISTANT - A INICIAR
echo  ==========================================
echo.

REM Verifica .env
if not exist ".env" (
    echo  [AVISO] Ficheiro .env nao encontrado! A criar default...
    if exist ".env.example" copy .env.example .env >nul
)

REM Carrega variaveis do .env
if exist ".env" (
    for /f "eol=# tokens=1,2 delims==" %%A in (.env) do (
        set "%%A=%%B"
    )
)

REM Define modo de abertura do navegador
set NANO_MODE=default

REM Build do frontend se necessario
if not exist "frontend\out\index.html" (
    echo  [1/2] A construir o frontend...
    cd frontend
    call npm run build
    cd ..
    echo  [1/2] Frontend pronto!
) else (
    echo  [1/2] Frontend pronto!
)

REM Lanca o Nano Assistant e abre automaticamente a aba no Google / Navegador Padrao
echo  [2/2] A lancar o Nano Assistant e a abrir o navegador...
echo.

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
    start "" cmd /c python core\main.py --mode default
) else (
    start "" cmd /c python core\main.py --mode default
)

pause
