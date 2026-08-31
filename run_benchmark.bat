@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

title Turf Prediction Engine - Banc de Mesure (Simulation 300 courses)
cls
echo ============================================================
echo   Lancement du Banc de Mesure Turf (Simulation 300 courses)
echo ============================================================
echo Dossier : %CD%
echo.

set "PYTHON_CMD="
python --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=python"
) else (
    py --version >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_CMD=py"
    )
)

if "%PYTHON_CMD%"=="" (
    echo [ERREUR] Python n'est pas detecte sur votre machine.
    pause
    exit /b 1
)

echo Python detecte : %PYTHON_CMD%
echo.
echo Execution de la simulation et calcul des metriques sur 300 courses...
echo.

%PYTHON_CMD% main.py --action simulate --simulate 300

if errorlevel 1 (
    echo.
    echo [ERREUR] Une erreur s'est produite lors de l'execution du script.
) else (
    echo.
    echo ============================================================
    echo   Execution terminee !
    echo   Le rapport HTML "benchmark_dashboard.html" a ete genere.
    echo   Ouverture automatique dans votre navigateur...
    echo ============================================================
    start "" "benchmark_dashboard.html"
)

echo.
pause
