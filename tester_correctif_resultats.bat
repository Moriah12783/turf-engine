@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  Dossier de travail : %CD%
echo.
echo  ===== Test 1/8 : validation HTTPS (tests\test_https_validation.py) =====
python tests\test_https_validation.py
echo.
echo  ===== Test 2/8 : lecture du classement PMU (tests\test_results_reader.py) =====
python tests\test_results_reader.py
echo.
echo  ===== Test 3/8 : arrivee definitive + suivi des corrections (tests\test_results_versioning.py) =====
python tests\test_results_versioning.py
echo.
echo  ===== Test 4/8 : export JSON des resultats (tests\test_results_export.py) =====
python tests\test_results_export.py
echo.
echo  ===== Test 5/8 : retour partenaire, disqualification finale et tracabilite (tests\test_partenaire_disqualification.py) =====
python tests\test_partenaire_disqualification.py
echo.
echo  ===== Test 6/8 : Lot 1 audit - tickets, NO_BET, annulations, porte de diffusion (tests\test_lot1_sorties.py) =====
python tests\test_lot1_sorties.py
echo.
echo  ===== Test 7/8 : porte de publication (tests\test_publication_gate.py) =====
python tests\test_publication_gate.py
echo.
echo  ===== Test 8/8 : verrouillage T-15 (test_verrouillage_t15.py, exige au moins 06h30 UTC) =====
python test_verrouillage_t15.py
echo.
echo  Termine. Les huit tests doivent se terminer par PASSENT.
echo.
pause
