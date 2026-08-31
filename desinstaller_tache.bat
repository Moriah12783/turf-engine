@echo off
title Suppression des taches planifiees Turf
cls
echo Suppression des taches planifiees Turf...
schtasks /delete /tn "Turf_Sync_Matin" /f >nul 2>&1
schtasks /delete /tn "Turf_Sync_Soir" /f >nul 2>&1
echo Taches supprimees avec succes.
echo.
pause
