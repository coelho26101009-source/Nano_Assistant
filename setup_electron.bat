@echo off
cd /d "%~dp0"
chcp 65001 >nul
title HELIOS — Instalar Electron

echo.
echo  ==========================================
echo   HELIOS V7 — CONFIGURAR APP ELECTRON
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
echo  [2/2] A gerar icone HELIOS...
cd ..

REM Gera icone simples se nao existir
if not exist "electron\assets\icon.ico" (
    py -c "
from PIL import Image, ImageDraw
import os

os.makedirs('electron/assets', exist_ok=True)

# Cria icone 256x256 com o sol HELIOS
img = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# Background circular escuro
draw.ellipse([8, 8, 248, 248], fill=(3, 5, 8, 255))

# Glow externo
for r in range(30, 0, -5):
    alpha = int(40 * (1 - r/30))
    draw.ellipse([128-80-r, 128-80-r, 128+80+r, 128+80+r],
                 fill=(255, 184, 0, alpha))

# Sol principal
draw.ellipse([48, 48, 208, 208], fill=(255, 140, 0, 255))
draw.ellipse([60, 60, 196, 196], fill=(255, 184, 0, 255))
draw.ellipse([80, 80, 176, 176], fill=(255, 210, 80, 255))
draw.ellipse([100, 100, 156, 156], fill=(255, 240, 180, 255))

# Salva como ICO
img.save('electron/assets/icon.ico', format='ICO', sizes=[(256,256),(128,128),(64,64),(32,32),(16,16)])
print('Icone criado!')
" 2>nul || echo  [AVISO] PIL nao instalado, icone padrao sera usado
)

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
