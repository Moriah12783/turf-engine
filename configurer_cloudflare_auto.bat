@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

title Configuration du Deploiement Automatique Cloudflare Pages
cls
echo ============================================================
echo   Configuration du Deploiement 100%% Automatique Cloudflare
echo ============================================================
echo.
echo Ce script vous permet d'activer la publication automatique
echo directe vers https://prono.elite-turf.fr sans manipulation manuelle.
echo.
echo Votre Account ID Cloudflare : bfb7a27738f18d7e642980d343a69ee8
echo Votre Projet Pages          : prono-elite-turf
echo.
echo Pour generer un Jeton API (API Token) gratuit sur Cloudflare :
echo   1. Rendez-vous sur : https://dash.cloudflare.com/profile/api-tokens
echo   2. Cliquez sur "Creer un jeton" > Utilisez le modele "Cloudflare Pages (Edit)"
echo   3. Copiez le jeton genere et collez-le ci-dessous.
echo.
set /p USER_TOKEN="Collez votre API Token Cloudflare (ou appuyez sur Entree pour ignorer) : "

if not "%USER_TOKEN%"=="" (
    python -c "import json; cfg={'account_id':'bfb7a27738f18d7e642980d343a69ee8','project_name':'prono-elite-turf','api_token':'%USER_TOKEN%'}; open('config.json','w',encoding='utf-8').write(json.dumps(cfg,indent=2))"
    echo.
    echo ============================================================
    echo   [SUCCES] Jeton API enregistre dans config.json !
    echo   A chaque synchronisation, le site sera desormais mis a jour
    echo   automatiquement sur https://prono.elite-turf.fr !
    echo ============================================================
) else (
    echo.
    echo [INFO] Aucun jeton saisi. Le fichier site/index.html sera synchronise localement.
)

echo.
pause
