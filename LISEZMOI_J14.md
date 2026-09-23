# Paquet J+14 (protocole commun) — 23/09/2026

6 fichiers, aucun fichier du développeur Radar ni de la migration R2
(daily_sync.yml, database.py, daily_sync.py, r2_store.py : intouchés).

1. `turf_lab/features.py` — `parse_music` : un « 0 » (non placé) reçoit la note la
   plus basse ; « Ret »/« R » (retiré) n'est plus lu comme un incident.
   Effet mesuré : nul sur le banc (1 086 courses). CHANGEMENT DE MOTEUR → ligne
   datée dans l'annexe du pont AVANT publication (fichier joint).
2. `turf_lab/combinaison.py` (nouveau) — Δ « Benter » par horizon : gain de
   log-vraisemblance face au marché, hors échantillon, IC95 bootstrap, contrôle des
   verrous non simultanés. Bibliothèque standard, < 2 s par passe.
3. `turf_lab/benchmark.py` — clé `benter_delta` dans `benchmark_report.json`.
4. `turf_lab/html_report.py` — section « Tableau de score — Gain d'information face
   au marché » sur le site (lignes non loyales grisées, ex. Matin).
5. `tests/test_combinaison.py` — 3 tests (suite : 79/79 verts).
6. `docs/experiences.md` — registre d'expériences ouvert (essais depuis le 02/09,
   échecs et résultats nuls compris).

Installation : `unzip -o paquet-j14-protocole.zip` à la racine, `python -m pytest tests -q`,
puis `publier_vers_github.bat` — hors gel de 24 h autour de la bascule R2, et après
enregistrement de la ligne d'annexe.
