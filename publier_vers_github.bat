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

REM ------------------------------------------------------------
REM GARDE-FOU 1 : ce script doit etre lance depuis le dossier
REM COMPLET du projet (TURF PROJET), jamais depuis un dossier
REM contenant seulement quelques fichiers (ex: un zip de correctifs).
REM ------------------------------------------------------------
if not exist "turf_lab\engine.py" (
    echo [STOP] Le moteur turf_lab\engine.py est INTROUVABLE dans ce dossier.
    echo.
    echo Vous etes probablement dans un dossier incomplet. Publier depuis ici
    echo SUPPRIMERAIT les fichiers manquants sur GitHub.
    echo.
    echo Solution : copiez d'abord tous les fichiers dans votre dossier
    echo "TURF PROJET" complet, puis relancez ce script depuis ce dossier.
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

REM ------------------------------------------------------------
REM GARDE-FOU 2 : aucune SUPPRESSION de fichier n'est publiee
REM automatiquement. Si des fichiers manquent localement, on les
REM restaure depuis GitHub au lieu de les effacer du depot.
REM ------------------------------------------------------------
set "DELETIONS="
for /f "delims=" %%f in ('git diff --staged --diff-filter=D --name-only') do set "DELETIONS=1"
if defined DELETIONS (
    echo [!] Des fichiers presents sur GitHub manquent dans ce dossier :
    git diff --staged --diff-filter=D --name-only
    echo [*] Ils sont RESTAURES depuis GitHub au lieu d'etre supprimes.
    for /f "delims=" %%f in ('git diff --staged --diff-filter=D --name-only') do git checkout HEAD -- "%%f"
    git add -A
)

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
