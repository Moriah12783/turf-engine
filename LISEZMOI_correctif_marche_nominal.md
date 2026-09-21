# Correctif « sélection marché par défaut » — 21/09/2026

## Ce que ça corrige

Quand une course est verrouillée sans cotes PMU réelles (cotes non ouvertes
au verrou, ou entre 50 et 90 % de partants cotés → cotes neutralisées par la
porte de verrouillage), la ligne de base MARCHÉ triait des cotes toutes
égales à 15,0 et sortait l'ordre des dossards : **1-2-3-4-5-6-7-8**. Cette
« sélection » était affichée sur le site et comptée dans tous les bancs comme
un pronostic du marché.

Mesure dans l'archive : **622 éditions marché nominales** (451 antérieures à
la porte de fraîcheur, 171 depuis la porte à 50 % du 15/09, dont 170 sur le
seul week-end 19-20/09).

Effet sur le banc F3 (ligne Marché) : Matin 83,4 % → **87,2 %** de gagnants
dans les 8, T-15 91,7 % → **93,4 %** ; tête gagnante Matin 22,1 % → 24,9 %.
Le marché était sous-estimé, donc l'écart moteur/marché surestimé.

## Ce que fait le correctif (4 fichiers, aucun fichier du développeur Radar)

1. `turf_lab/baselines.py` — `MarketOddsEngine` : sans marché réel (moins de
   50 % de partants réellement cotés, ou cotes neutralisées), la sélection
   est **vide** (édition nominale, probabilités uniformes = « le marché ne
   sait rien »). Sur marché partiel ≥ 50 %, les favoris cotés passent d'abord.
   Nouvelle fonction `market_edition_informative()` : reconnaît une édition
   marché archivée sans cotes réelles (`odds_real` faux, ou probabilités
   toutes égales).
2. `turf_lab/benchmark.py` — ces éditions sont traitées comme **absentes**
   dans le banc, le banc par horizon, les courses communes du pont Radar et
   les fiches de courses (colonne Marché « indisponible », couverture
   « Marché — »). Compteur `market_nominal_editions` dans le rapport JSON.
3. `turf_lab/html_report.py` — affichage « indisponible » / « — sans cotes
   réelles » et note de méthode sous le banc F3 avec le compteur.
4. `tests/test_marche_nominal.py` — 5 tests de non-régression.

Les éditions déjà archivées ne sont **pas réécrites** (immuabilité) : elles
restent en base, simplement reconnues comme nominales et ignorées.

## Installation

    unzip -o correctif-marche-nominal.zip      (à la racine du projet)
    python -m pytest tests -q                  → 76 passed
    publier_vers_github.bat

(`test_verrouillage_t15.py` échoue déjà sur `main` sans ce correctif — test
dépendant de l'heure du jour, sans rapport.)

## Gouvernance (fenêtre de pré-enregistrement jusqu'au 06/10)

Ce correctif touche la ligne de base MARCHÉ et la lecture du banc, donc la
comparaison RADAR_V4 / marché : la ligne datée ci-dessous doit être
enregistrée dans l'annexe du pont **avant** la publication.
