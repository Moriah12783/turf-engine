@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

title Publication du Projet vers GitHub (sans ecraser l'historique cloud)
cls
echo ============================================================
echo   Publication vers GitHub - VERSION SECURISEE
echo ------------------------------------------------------------
echo   Cette version ne fait JAMAIS de push force :
echo   - votre code local est publie
echo   - la base de donnees et l'historique accumules dans le
echo     cloud (turf_bench.db, site/, rapports) sont PRESERVES
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

echo [*] Recuperation de l'etat actuel du cloud (GitHub)...
git fetch origin main
if %errorlevel% neq 0 (
    echo [ERREUR] Impossible de recuperer le depot GitHub.
    echo Verifiez votre connexion internet et vos droits d'acces.
    pause
    exit /b
)

echo [*] Alignement sur l'historique du cloud (aucune perte)...
git branch -f main FETCH_HEAD
git symbolic-ref HEAD refs/heads/main
git reset FETCH_HEAD >nul

echo [*] Restauration des fichiers geres par le cloud...
REM La base de donnees et les fichiers generes appartiennent au moteur cloud :
REM on reprend TOUJOURS la version GitHub pour ne jamais perdre l'historique.
git checkout -- turf_bench.db 2>nul
git checkout -- benchmark_report.json 2>nul
git checkout -- benchmark_dashboard.html 2>nul
git checkout -- site 2>nul

echo [*] Ajout de vos modifications de code locales...
git add -A

git diff --staged --quiet
if %errorlevel% equ 0 (
    echo.
    echo [OK] Aucun changement de code a publier : GitHub est deja a jour.
    echo.
    pause
    exit /b
)

echo [*] Enregistrement (Commit)...
git commit -m "Mise a jour du code depuis le PC"

echo [*] Envoi vers GitHub (Push securise, sans force)...
git push origin main
if %errorlevel% neq 0 (
    echo.
    echo [!] Le push a ete refuse (le cloud a publie entre-temps).
    echo [*] Nouvelle tentative avec rebase...
    git pull --rebase origin main
    git push origin main
)

echo.
echo ============================================================
echo   [SUCCES] Votre code est publie et l'historique cloud
echo   (base de donnees + archives) est intact !
echo ============================================================
echo.
pause
