@echo off
cd /d "%~dp0"
chcp 65001 >nul
title HELIOS V7  Setup Portatil

echo.
echo  ==========================================
echo   HELIOS V7  CRIAR VERSAO PORTATIL
echo  ==========================================
echo.
echo  Isto vai descarregar Python embutido (~30MB)
echo  e instalar todas as dependencias.
echo  Nao precisas de instalar nada no PC de destino!
echo.
echo  A iniciar em 3 segundos...
timeout /t 3 >nul

REM Cria pasta python_embedded
if exist "python_embedded" (
    echo  [OK] Python embutido ja existe. A saltar download.
    goto :install_pip
)

echo.
echo  [1/4] A descarregar Python 3.12 Embeddable...
mkdir python_embedded 2>nul

REM Descarrega Python embeddable (Windows 64-bit)
powershell -Command "& {
    $url = 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-embed-amd64.zip'
    $dest = 'python_embedded\python_embed.zip'
    Write-Host '  A descarregar...'
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
    Write-Host '  A extrair...'
    Expand-Archive -Path $dest -DestinationPath 'python_embedded' -Force
    Remove-Item $dest
    Write-Host '  Feito!'
}"

if not exist "python_embedded\python.exe" (
    echo  [ERRO] Falha ao descarregar Python!
    pause
    exit /b 1
)

echo  [OK] Python 3.12 extraido!

:install_pip
echo.
echo  [2/4] A configurar pip...

REM Habilita imports no Python embeddable
REM (por defeito o embedded nao tem pip nem site-packages habilitados)
set PYEMBED=python_embedded

REM Edita o python312._pth para habilitar site-packages
powershell -Command "
    \$pth = Get-ChildItem 'python_embedded\*.pth' | Select-Object -First 1
    if (\$pth) {
        \$content = Get-Content \$pth.FullName -Raw
        if (\$content -notmatch 'import site') {
            \$content = \$content -replace '#import site', 'import site'
            Set-Content \$pth.FullName \$content
            Write-Host '  site-packages habilitado!'
        } else {
            Write-Host '  site-packages ja habilitado.'
        }
    }
"

REM Descarrega get-pip.py
if not exist "python_embedded\get-pip.py" (
    powershell -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile 'python_embedded\get-pip.py' -UseBasicParsing"
)

REM Instala pip
python_embedded\python.exe python_embedded\get-pip.py --no-warn-script-location 2>nul
echo  [OK] Pip instalado!

echo.
echo  [3/4] A instalar dependencias do HELIOS...
echo  (Isto pode demorar 2-5 minutos na primeira vez)

python_embedded\python.exe -m pip install ^
    groq ^
    python-dotenv ^
    eel ^
    pyautogui ^
    pyperclip ^
    psutil ^
    duckduckgo-search ^
    requests ^
    SpeechRecognition ^
    gTTS ^
    pygame ^
    Pillow ^
    pyyaml ^
    httpx ^
    edge-tts ^
    chromadb ^
    pypdf ^
    playwright ^
    --no-warn-script-location ^
    --quiet

echo  [OK] Dependencias instaladas!

echo.
echo  Extras opcionais (nao instalados por defeito):
echo    Wake-word:  python_embedded\python.exe -m pip install openwakeword
echo                (ou pvporcupine + PICOVOICE_ACCESS_KEY no .env)
echo    STT local:  python_embedded\python.exe -m pip install faster-whisper

echo.
echo  [4/4] A criar launcher HELIOS.bat...

REM Cria o launcher portatil
(
echo @echo off
echo cd /d "%%~dp0"
echo chcp 65001 ^>nul
echo title H.E.L.I.O.S. V7
echo.
echo if not exist "frontend\out" ^(
echo     echo [1/2] A construir frontend...
echo     cd frontend
echo     call npm install --silent 2^>nul
echo     call npm run build
echo     cd ..
echo ^)
echo.
echo echo [HELIOS] A iniciar...
echo python_embedded\python.exe core\main.py
echo pause
) > HELIOS.bat

echo.
echo  ==========================================
echo   SETUP COMPLETO!
echo  ==========================================
echo.
echo  Para usar o HELIOS:
echo    Faz duplo clique em: HELIOS.bat
echo.
echo  Para levar numa pen:
echo    Copia a pasta HELIOS_V7 INTEIRA para a pen.
echo    No outro PC faz duplo clique em HELIOS.bat
echo    (Nao precisa de instalar Python nem nada!)
echo.
echo  Tamanho aproximado: ~500MB com todas as deps
echo.
pause
