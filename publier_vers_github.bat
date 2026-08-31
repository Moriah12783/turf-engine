@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

title Publication Complete du Projet vers GitHub
cls
echo ============================================================
echo   Publication Automatique Complete vers GitHub
echo ============================================================
echo.
echo Depot cible : https://github.com/Moriah12783/turf-engine.git
echo.

where git >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERREUR] Git n'est pas encore installe dans le terminal.
    echo Rendez-vous sur https://git-scm.com/download/win pour l'installer rapidement.
    echo.
    pause
    exit /b
)

echo [*] Initialisation du depot Git local...
git init
git remote remove origin 2>nul
git remote add origin https://github.com/Moriah12783/turf-engine.git
git branch -M main

echo [*] Ajout de TOUS les dossiers (.github, turf_lab, site)...
git add .
git add -A

echo [*] Enregistrement (Commit)...
git commit -m "Turf Engine Full Cloud-Native 24/7 Deployment"

echo [*] Envoi vers GitHub (Push)...
git push -f -u origin main

echo.
echo ============================================================
echo   [SUCCES] Tous les dossiers (.github, turf_lab, site) 
echo   sont desormais publies sur votre depot GitHub !
echo ============================================================
echo.
pause
