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

echo [*] Ajout de TOUS les dossiers (.github, turf_lab, site) et de la base de donnees...
git add .
git add -f turf_bench.db
git add -A

echo [*] Enregistrement (Commit)...
git commit -m "Turf Engine Full Cloud-Native with Complete History DB"

echo [*] Envoi vers GitHub (Push)...
git push -f -u origin main

echo.
echo ============================================================
echo   [SUCCES] Tous les dossiers et la base de donnees
echo   sont desormais publies et persistes sur GitHub !
echo ============================================================
echo.
pause
