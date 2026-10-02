@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ========================================================
echo       ManaVault - Compilar APK Android via GitHub
echo ========================================================
echo.

set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Packages\GitHub.cli_Microsoft.Winget.Source_8wekyb3d8bbwe\bin;%LOCALAPPDATA%\Microsoft\WinGet\Packages\Git.MinGit_Microsoft.Winget.Source_8wekyb3d8bbwe\cmd;%PATH%"

where gh >nul 2>&1
if errorlevel 1 (
    echo GitHub CLI nao foi encontrado no PATH.
    pause
    exit /b 1
)

where git >nul 2>&1
if errorlevel 1 (
    echo Git nao foi encontrado no PATH.
    pause
    exit /b 1
)

echo Verificando login no GitHub...
gh auth status >nul 2>&1
if errorlevel 1 (
    echo.
    echo Voce precisa fazer login na sua conta do GitHub uma unica vez.
    echo O navegador sera aberto para autorizar com um codigo.
    echo.
    gh auth login --web -p https
    if errorlevel 1 (
        echo Falha no login do GitHub.
        pause
        exit /b 1
    )
)

echo.
echo Login verificado!
echo Verificando repositorio remoto...

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    git init
    git config user.name "ManaVault Developer"
    git config user.email "dev@local"
    git branch -M main
)

git add .github/ android_app/ .gitignore
git commit -m "Update ManaVault Android app" >nul 2>&1

git remote get-url origin >nul 2>&1
if errorlevel 1 (
    echo Criando repositorio privado no seu GitHub...
    gh repo create manavault-apk --private --source=. --remote=origin --push
) else (
    echo Enviando atualizacoes para o GitHub...
    git push -u origin main
)

echo.
echo Disparando compilacao do APK no GitHub Actions...
gh workflow run "build-android.yml"

echo.
echo Aguardando compilacao no servidor Ubuntu (leva cerca de 8 a 10 minutos)...
gh run watch

echo.
echo Baixando o arquivo APK compilado...
if not exist "dist_apk" mkdir dist_apk
gh run download --name ManaVault-Android-debug --dir dist_apk

echo.
echo ========================================================
echo SUCESSO! Seu arquivo .apk foi gerado e salvo em:
echo %CD%\dist_apk\
echo ========================================================
explorer dist_apk
pause
