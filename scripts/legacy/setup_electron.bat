@echo off
cd /d "%~dp0\..\.."
chcp 65001 >nul
title NANO  Instalar Electron

echo.
echo  ==========================================
echo   NANO  CONFIGURAR APP ELECTRON
echo  ==========================================
echo.

REM Verifica Node.js
node --version >nul 2>&1
if errorlevel 1 (
    echo  [ERRO] Node.js nao encontrado!
    echo  Instala em: https://nodejs.org
    pause
    exit /b 1
)
echo  [OK] Node.js encontrado: 
node --version

REM Vai para a pasta electron
cd electron

echo.
echo  [1/2] A instalar dependencias Electron...
call npm install
if errorlevel 1 (
    echo  [ERRO] Falha ao instalar dependencias!
    pause
    exit /b 1
)

echo.
echo  [2/2] A gerar icones NANO...
cd ..

REM Um so master aprovado gera os icones de janela, tray e overlay.
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\build_app_icon.ps1"
if errorlevel 1 exit /b 1

echo.
echo  ==========================================
echo   INSTALACAO COMPLETA!
echo  ==========================================
echo.
echo  Para TESTAR o Electron:
echo    cd electron
echo    npm start
echo.
echo  Para COMPILAR o .exe instalador:
echo    cd electron
echo    npm run build
echo.
pause
