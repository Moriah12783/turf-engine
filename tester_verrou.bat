@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  Dossier de travail : %CD%
echo.
echo  ===== Test 1/2 : porte de publication (tests\test_publication_gate.py) =====
python tests\test_publication_gate.py
echo.
echo  ===== Test 2/2 : verrouillage T-15 (test_verrouillage_t15.py, exige au moins 06h30 UTC) =====
python test_verrouillage_t15.py
echo.
echo  Termine. Les deux tests doivent se terminer par PASSENT.
echo.
pause
