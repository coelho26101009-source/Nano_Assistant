@echo off
setlocal EnableExtensions EnableDelayedExpansion
REM The & is escaped: cmd.exe treats a bare & as a command separator, so the
REM unescaped version ran `title Nano - GitHub Commit` and then tried to run
REM `Push` as a program, printing a spurious error on every launch.
title Nano - GitHub Commit ^& Push
REM This helper lives in scripts\ so it cannot be mistaken for one of the two
REM sanctioned launchers in the project root. It still has to operate on the
REM repository, so it walks one level up before touching git.
cd /d "%~dp0.."

echo.
echo ============================================================
echo              NANO - GITHUB COMMIT ^& PUSH
echo ============================================================
echo.

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Este ficheiro tem de estar dentro da pasta do repositorio Nano.
    echo.
    pause
    exit /b 1
)

echo Mensagem sugerida:
echo.
echo   Add secure PC control and robust local fallback
echo.
set /p "COMMIT_MSG=Escreve/cola a mensagem do commit: "

if not defined COMMIT_MSG (
    echo.
    echo [CANCELADO] Nao foi indicada nenhuma mensagem.
    pause
    exit /b 1
)

echo.
echo [1/5] Estado atual...
git status --short
echo.

echo [2/5] A adicionar alteracoes...
git add -A
if errorlevel 1 goto :error

set "SENSITIVE_FOUND="
for /f "delims=" %%F in ('git diff --cached --name-only') do (
    set "FILE=%%F"
    echo !FILE! | findstr /I /R /C:"^\.env$" /C:"^\.env\." /C:"^logs/" /C:"^runtime/" /C:"\.wav$" /C:"\.mp3$" /C:"\.key$" /C:"\.pem$" >nul
    if not errorlevel 1 (
        set "SENSITIVE_FOUND=1"
        echo [ATENCAO] Ficheiro potencialmente privado/temporario: !FILE!
    )
)

if defined SENSITIVE_FOUND (
    echo.
    echo [BLOQUEADO] Foram encontrados ficheiros potencialmente privados ou temporarios.
    echo Nada sera enviado para o GitHub.
    echo A retirar os ficheiros do staging...
    git reset
    echo.
    pause
    exit /b 1
)

echo.
echo Ficheiros preparados para o commit:
git diff --cached --name-status
echo.

git diff --cached --quiet
if not errorlevel 1 (
    echo [INFO] Nao existem alteracoes para fazer commit.
    echo.
    git status
    pause
    exit /b 0
)

echo [3/5] A criar commit...
git commit -m "%COMMIT_MSG%"
if errorlevel 1 goto :error

echo.
echo [4/5] A enviar para o GitHub...
git push
if errorlevel 1 goto :error

echo.
echo [5/5] Estado final...
git status

echo.
echo ============================================================
echo   CONCLUIDO - commit criado e enviado para o GitHub.
echo ============================================================
echo.
pause
exit /b 0

:error
echo.
echo ============================================================
echo [ERRO] O processo parou. Ve a mensagem acima para perceber o motivo.
echo ============================================================
echo.
pause
exit /b 1
