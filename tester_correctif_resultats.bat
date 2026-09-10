@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  Dossier de travail : %CD%
echo.
echo  ===== Test 1/7 : validation HTTPS (tests\test_https_validation.py) =====
python tests\test_https_validation.py
echo.
echo  ===== Test 2/7 : lecture du classement PMU (tests\test_results_reader.py) =====
python tests\test_results_reader.py
echo.
echo  ===== Test 3/7 : arrivee definitive + suivi des corrections (tests\test_results_versioning.py) =====
python tests\test_results_versioning.py
echo.
echo  ===== Test 4/7 : export JSON des resultats (tests\test_results_export.py) =====
python tests\test_results_export.py
echo.
echo  ===== Test 5/7 : retour partenaire, disqualification finale et tracabilite (tests\test_partenaire_disqualification.py) =====
python tests\test_partenaire_disqualification.py
echo.
echo  ===== Test 6/7 : porte de publication (tests\test_publication_gate.py) =====
python tests\test_publication_gate.py
echo.
echo  ===== Test 7/7 : verrouillage T-15 (test_verrouillage_t15.py, exige au moins 06h30 UTC) =====
python test_verrouillage_t15.py
echo.
echo  Termine. Les sept tests doivent se terminer par PASSENT.
echo.
pause
