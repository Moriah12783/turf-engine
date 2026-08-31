@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

title Turf Engine - Synchronisation Quotidienne PMU & Banc de Mesure
cls
echo ============================================================
echo   Synchronisation Quotidienne des Courses Reelles PMU
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
echo Telechargement du programme PMU, calcul des pronostics et mise a jour des resultats...
echo.

%PYTHON_CMD% main.py --action sync

if errorlevel 1 (
    echo.
    echo [ERREUR] Une erreur s'est produite lors de la synchronisation.
) else (
    echo.
    echo ============================================================
    echo   Mise a jour terminee avec succes !
    echo   Ouverture du tableau de bord actualise...
    echo ============================================================
    start "" "benchmark_dashboard.html"
)

echo.
pause
