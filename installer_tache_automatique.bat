@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

title Configuration Automatique du Banc de Mesure Turf
cls
echo ============================================================
echo   Installation de la Synchronisation Quotidienne Automatique
echo ============================================================
echo.

set "SCRIPT_PATH=%~dp0run_daily_sync.bat"

echo Creation des 2 taches planifiees quotidiennes :
echo   1. Le matin a 08h30 (recuperation des partants et verrouillage des pronostics)
echo   2. Le soir a 21h30 (recuperation des arrivees officielles et mise a jour du dashboard)
echo.

schtasks /create /tn "Turf_Sync_Matin" /tr "\"%SCRIPT_PATH%\"" /sc daily /st 08:30 /f >nul 2>&1
if errorlevel 1 (
    echo [INFO] Pour enregistrer la tache, veuillez faire un clic droit sur ce fichier
    echo et choisir "Executer en tant qu'administrateur".
    echo.
    pause
    exit /b 1
)

schtasks /create /tn "Turf_Sync_Soir" /tr "\"%SCRIPT_PATH%\"" /sc daily /st 21:30 /f >nul 2>&1

echo ============================================================
echo   [SUCCES] L'automatisation a bien ete configuree !
echo   - Tache Matin : 08:30 tous les jours
echo   - Tache Soir  : 21:30 tous les jours
echo ============================================================
echo.
echo Le banc de mesure tournera desormais tout seul en arriere-plan.
echo.
pause
