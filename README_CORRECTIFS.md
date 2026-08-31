# Correctifs Turf Engine — T-15 réel, courses en cours, historique permanent

Branche : `feat/t15-historique-persistant` (1 commit au-dessus de `main`, commit `a05db4b`).
Le moteur de prédiction (`engine.py`, `features.py`, `baselines.py`) n'est **pas modifié** — seuls le pipeline de données, la planification et l'affichage le sont.

## Ce qui était cassé (diagnostic)

**Le T-15 était fictif.** À chaque exécution, `daily_sync.py` verrouillait les 4 horizons (T_MATIN, T90, T30, T15) en même temps, avec les mêmes cotes du moment, et `INSERT OR REPLACE` écrasait les verrouillages précédents. Preuve en base : pour une même course, les 4 pronostics avaient le même `lock_time` à la milliseconde près et des `value_indices` identiques. Il n'existait d'ailleurs aucun cron proche du départ des courses : rien ne tournait entre 12h45 et 21h30 GMT, donc ni T-15 ni suivi des courses en cours l'après-midi.

**L'historique n'était pas garanti.** Trois fuites : (1) `publier_vers_github.bat` faisait `git push -f`, écrasant à chaque publication PC toute la base accumulée par le cloud ; (2) le workflow poussait avec `git push || true` — un push refusé perdait silencieusement les données du run ; (3) chaque re-synchronisation réécrivait partants, cotes et pronostics des courses déjà terminées, et le site était plafonné à 1000 courses.

**Les cotes du matin étaient presque toujours le défaut 15.0** (le `rapportReference` PMU est souvent absent du flux participants), donc le signal « Smart Money » (matin vs direct) était faussé.

## Ce qui a été corrigé (10 fichiers)

`turf_lab/daily_sync.py` — verrouillage par fenêtres temporelles réelles : T_MATIN à la première vue de la course, T90 dès que départ ≤ 100 min, T30 ≤ 35 min, T15 ≤ 18 min (tolérance incluse pour la latence des crons GitHub). Un horizon verrouillé n'est **plus jamais** recalculé (`has_prediction`). Les courses `FINISHED` sont gelées définitivement. La cote du matin est figée à la première capture ; `final_odds` suit le direct jusqu'à l'arrivée. Pour les dates passées (rattrapage `--days 7`), tous les horizons manquants sont posés d'un coup, comme avant.

`turf_lab/database.py` — nouvelle table `odds_snapshots` (photo des cotes de chaque partant à chaque horizon, `INSERT OR IGNORE` donc immuable), et les gardes `has_prediction` / `get_locked_horizons`. Migration automatique au premier lancement, aucune intervention nécessaire.

`.github/workflows/daily_sync.yml` — nouvelles passes live `6,21,36,51 9-20 * * *` (toutes les ~15 min, 09h–20h59 GMT) en `--days 1` (rapides), qui verrouillent T-90/T-30/T-15 course par course, rafraîchissent les cotes en direct et rapatrient les arrivées au fil de l'après-midi. Les passes 08h30 et 21h30 restent en `--days 7`. Ajout d'un `concurrency group` (un seul sync à la fois) et d'un push **sans force** avec 3 tentatives rebase — en cas d'échec le job échoue visiblement au lieu de perdre les données.

`publier_vers_github.bat` — réécrit : récupère d'abord l'état GitHub, reprend systématiquement la version cloud de `turf_bench.db`, `site/` et des rapports, publie uniquement vos changements de code, et pousse **sans -f**. L'historique cloud ne peut plus être écrasé depuis le PC.

`turf_lab/benchmark.py` — historique sans limite (fin du plafond 1000) ; le cockpit choisit désormais le pronostic de l'horizon le plus proche du départ réellement verrouillé (T15 > T30 > T90 > T_MATIN) ; les cotes par horizon sont jointes à chaque partant.

`turf_lab/html_report.py` — persistance « pour toujours » côté site : les ~3 dernières semaines restent embarquées dans `index.html`, le reste est archivé en fichiers mensuels statiques `site/archive/AAAA-MM.json` chargés à la demande par le navigateur. Nouveaux onglets « 📚 AAAA-MM » et « 📚 Tout l'historique », recherche inchangée mais couvrant tout ce qui est chargé. La fiche course affiche maintenant les colonnes Matin / T-90 / T-30 / T-15 / Cote finale (« – » tant qu'un horizon n'est pas atteint).

`turf_lab/cloudflare_deploy.py` — déploiement multi-fichiers (index + archives mensuelles) au lieu du seul `index.html`.

`main.py` — orchestration des archives mensuelles avant génération du HTML. `test_verrouillage_t15.py` — test autonome (`python test_verrouillage_t15.py`) qui vérifie fenêtres de verrouillage, immuabilité des cotes/pronostics, gel des courses terminées et rattrapage. `.gitignore` — exclut `__pycache__`.

## Comment appliquer

Option A (recommandée, historique git propre) — depuis le dossier du projet à jour de `origin/main` :

    git checkout main
    git pull origin main
    git am correctif-t15-historique.patch
    git push origin main

Option B (simple) — dézipper `correctifs-t15-historique.zip` à la racine du projet en remplaçant les fichiers, puis publier avec le **nouveau** `publier_vers_github.bat`.

Vérification après mise en ligne : lancer le workflow à la main (GitHub → Actions → Run workflow), puis contrôler qu'un pronostic déjà verrouillé ne change plus entre deux runs, et que les fiches courses affichent des cotes différentes entre Matin et T-15 en fin d'après-midi.

## Points d'attention (décisions à prendre, rien n'a été changé sans vous)

1. **Taille de la base dans git.** `turf_bench.db` (~5 Mo/semaine) est committée à chaque passe. GitHub refuse les fichiers > 100 Mo : à ce rythme, le plafond arrive dans environ 4 à 5 mois. Prévoir d'ici là une bascule du stockage vers Cloudflare D1 (ou des bases mensuelles). Les archives du site, elles, sont des JSON légers et ne posent pas de problème.
2. **Repo public vs privé.** Les passes toutes les 15 min consomment ~4 500 min/mois de runners. C'est illimité et gratuit tant que le repo est **public** ; en privé, le quota gratuit (2 000 min/mois) serait dépassé. Si vous repassez en privé, réduisez la cadence (ex. toutes les 30 min) ou activez la facturation.
3. **Données de référence injectées.** `historical_archive.py` injecte 33 courses des 28–29/08 avec chevaux, cotes et rapports **fictifs** (noms `HIPP_n`, dividendes forfaitaires), comptés dans les statistiques publiées. De même, `daily_sync.py` **estime** des dividendes quand le flux PMU ne les fournit pas (lignes « rapports estimation »). Au regard du positionnement « aucun chiffre retouché » d'Elite Turf, nous recommandons de purger ces courses de la base et de marquer « N/A » les dividendes manquants — non appliqué ici car cela modifie les statistiques affichées : à valider par Steph.
4. **Heure d'hiver.** La conversion Paris = GMT+2 est codée en dur (héritée de l'existant). À partir du 25 octobre 2026 (passage à GMT+1), les heures affichées et les fenêtres T-90/T-30/T-15 des courses sans timestamp PMU seront décalées d'une heure. Le correctif est simple (utiliser le fuseau `Europe/Paris`) — à planifier avant fin octobre.
