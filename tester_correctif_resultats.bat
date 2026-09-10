@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  Dossier de travail : %CD%
echo.
echo  ===== Test 1/6 : validation HTTPS (tests\test_https_validation.py) =====
python tests\test_https_validation.py
echo.
echo  ===== Test 2/6 : lecture du classement PMU (tests\test_results_reader.py) =====
python tests\test_results_reader.py
echo.
echo  ===== Test 3/6 : arrivee definitive + suivi des corrections (tests\test_results_versioning.py) =====
python tests\test_results_versioning.py
echo.
echo  ===== Test 4/6 : export JSON des resultats (tests\test_results_export.py) =====
python tests\test_results_export.py
echo.
echo  ===== Test 5/6 : porte de publication (tests\test_publication_gate.py) =====
python tests\test_publication_gate.py
echo.
echo  ===== Test 6/6 : verrouillage T-15 (test_verrouillage_t15.py, exige au moins 06h30 UTC) =====
python test_verrouillage_t15.py
echo.
echo  Termine. Les six tests doivent se terminer par PASSENT.
echo.
pause
