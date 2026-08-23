@echo off
REM ===========================================================================
REM  NANO - BENCHMARK DE PRECISAO DE FALA (ferramenta de desenvolvimento)
REM
REM  Grava-te a ler ~30 frases em portugues UMA VEZ e compara os modelos
REM  faster-whisper tiny / base / small sobre exactamente as mesmas gravacoes.
REM
REM  As gravacoes ficam SO nesta maquina, em runtime\speech_benchmark\,
REM  que esta ignorada pelo git. Nada vai para o Groq nem para a nuvem.
REM
REM  ENCODING RULE (do not break this):
REM  This file must stay PURE ASCII. cmd.exe parses .bat files using the OEM
REM  codepage (850/437), not UTF-8. Accented text saved as UTF-8 gets
REM  mis-decoded, which corrupts the parser and makes the window close
REM  instantly. That was a real bug in NANO.bat. Keep it ASCII.
REM
REM  LOCATION RULE (do not move this to the project root):
REM  the root holds exactly two public launchers, NANO.bat and
REM  NANO_DESKTOP.bat, and tests/test_launcher.py enforces that so a user can
REM  never double-click the wrong one. This is a developer tool and lives here.
REM ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0.."
title Nano - Benchmark de voz

REM The corpus is Portuguese and the user has to READ IT ALOUD, so the console
REM must be able to render UTF-8 before a single phrase is printed.
chcp 65001 >nul 2>&1

set "NANO_PY=python"
if exist ".venv\Scripts\python.exe" set "NANO_PY=.venv\Scripts\python.exe"

"%NANO_PY%" --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  Python nao foi encontrado. Instala o Python 3.12 ou mais recente.
    echo.
    pause
    exit /b 1
)

"%NANO_PY%" -c "import faster_whisper, pyaudio, psutil" >nul 2>&1
if errorlevel 1 (
    echo.
    echo  Faltam dependencias do benchmark neste interpretador.
    echo  Instala com:
    echo      "%NANO_PY%" -m pip install faster-whisper PyAudio psutil
    echo.
    pause
    exit /b 1
)

"%NANO_PY%" scripts\speech_accuracy_benchmark.py %*
set "RC=%ERRORLEVEL%"

echo.
pause
exit /b %RC%
