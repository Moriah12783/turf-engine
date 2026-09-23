# Note aux développeurs moteurs : migration de la persistance (étape 1/3)

**De :** Steph (Tsalach Ventures). **Pour :** les sessions en charge de
NEW_VALUE_ENGINE et de RADAR_V4.

## Pourquoi
`turf_bench.db` pèse 34 Mo et grossit d'environ 1,1 Mo par jour. Elle est
recommitée environ 115 fois par jour. GitHub bloquera tout push au-delà de
100 Mo, vers fin novembre, ce qui arrêterait la publication. Nous sortons donc
de GitHub en 3 étapes vers Cloudflare, notre compte payant.

## Étape 1 (maintenant) : la base passe sur Cloudflare R2
- Au début de chaque passe, la base est téléchargée depuis R2 (vérification
  SHA-256 et intégrité SQLite). À la fin, elle est renvoyée avec une sauvegarde
  quotidienne.
- **Aucun changement** sur les moteurs, `predict()`, le banc, le site ou le pont Radar.
- Service continu : prono elite ne s'interrompt pas pendant la bascule.

## Ce que vous devez adapter
1. **La copie Git de `turf_bench.db` sera figée** à la date de bascule, que
   Steph vous communiquera. Pour vos backtests, utilisez la base à jour :
   `python -m turf_lab.r2_store fetch --db turf_bench.db`
   Il faut un jeton R2 **lecture seule**, fourni par Steph, et `boto3>=1.36`.
2. **Ne committez plus jamais `turf_bench.db`.**
3. **Gardez l'historique en ajout seul** sur `races`, `predictions`,
   `race_results` et `odds_snapshots`. Toute passe qui fait perdre des lignes à
   ces tables est **refusée** (job rouge). Une purge volontaire se décide avec
   Steph et passe par `--allow-shrink`.
4. **Nouvelles tables et colonnes : pas de problème**, elles voyagent avec la base.
5. **`.github/workflows/daily_sync.yml` :** ne le modifiez pas sans
   coordination. Tout programme qui écrit dans la base doit tourner entre le
   `pull` et le `push` R2.
6. **Gel de 24 h autour de la bascule** sur `daily_sync.yml`,
   `turf_lab/database.py` et `turf_lab/daily_sync.py`.

## Préparez dès maintenant les étapes 2 et 3
L'exécution passera sur Cloudflare : un Worker planifié chaque minute avec
verrous exacts à T-90, T-30 et T-15, un Container Python, puis un dépôt privé.
- **Plus de dépendance à l'environnement GitHub.** Les champs de version (`GITHUB_SHA` et `GITHUB_RUN_ID` dans
  `results_export.version_code` et `radar_bridge.push_report_summary`) seront
  remplacés par un identifiant de build.
- **Des moteurs purs.** `predict()` ne doit faire ni entrée-sortie ni appel
  réseau en dehors du pont Radar existant, et doit être déterministe à entrée
  égale.
- **Radar :** les secrets `RADAR_*` et la fonction `fn_journal_du_jour` ne
  changent pas. Seule l'origine de `commit` et `run_id` dans le résumé poussé
  changera à l'étape 2.

Références : `docs/MIGRATION_R2.md` (procédure complète, retour arrière) et
`turf_lab/r2_store.py` (garde-fous).

Un cahier des charges commun aux deux moteurs suit : métrique unique,
protocole d'évaluation, combinaison finale.
