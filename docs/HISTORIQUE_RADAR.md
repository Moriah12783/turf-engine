# Historique Radar → R2 (phase 1 du plan Benter)

**Objectif.** Constituer, sans jamais toucher à la production, un miroir
fidèle d'un an d'historique PMU (courses, partants, arrivées, cotes,
rapports) pour entraîner et valider l'étape 1 du plan Benter : le modèle
fondamental (logit conditionnel **sans les cotes**), puis sa combinaison
avec le marché. Critère d'entrée G1 : au moins 3 000 courses de test hors
échantillon.

| | |
|---|---|
| Source | Supabase « Radar Elite Predictive », rôle `lecteur_benter` (lecture seule, 5 tables, 2 connexions max, 120 s par requête) |
| Secret | `RADAR_HISTORY_DSN` (dépôt turf-engine, Session pooler, port 5432) + les 4 secrets R2 déjà en place |
| Cible | R2 `turf-engine-data` → `history/turf_history.db` (version précédente : `history/turf_history_prev.db`) |
| Code | `turf_lab/history_export.py`, tests `tests/test_history_export.py` |
| Planification | `.github/workflows/history_export.yml`, chaque nuit à **01h17 UTC** |
| Production | **Aucun impact** : ne lit ni n'écrit `turf_bench.db`, ne committe rien, ne déploie rien, groupe de concurrence séparé |

## Règles convenues avec le dev Radar (27/09/2026)

1. **Pagination par date** : une requête par table et par date (au plus
   ~3 500 lignes), jamais de lecture d'une table entière. Le repérage des
   dates à lire se fait par un comptage par date (parcours d'index seul,
   quelques millisecondes, aucune ligne lue).
2. **Créneau 00h00–05h00 UTC.** Hors créneau, l'export ne lit rien
   (`HISTORY_HORS_CRENEAU`). Le budget de temps (45 min) est en plus rogné
   pour ne jamais déborder 05h00. Un lancement manuel hors créneau exige
   la case `force_hors_creneau` : à réserver aux urgences.
3. **Lecture seule.** Aucune écriture côté Radar ; le rôle n'en a de toute
   façon pas le droit.
4. **Rapports antérieurs au 20/07/2026 : on attend la ligne de fin de
   reprise du Radar (~02/10).** Verrou dans le code : sans `--start`
   explicite, aucun rapport antérieur au 20/07/2026 n'est lu.

## Volumes (27/09/2026)

| Table | Lignes | Dates | Couverture |
|---|---:|---:|---|
| `participants` | 186 338 | 438 | 17/07/2025 → aujourd'hui |
| `cotes_snapshots` | 136 243 | 75 | 15/07/2026 → aujourd'hui |
| `rapports_definitifs` | 39 344 | 68 | 21/07/2026 → hier (reprise en cours pour l'antérieur) |
| `courses` | 15 421 | 438 | 17/07/2025 → aujourd'hui |
| `arrivees` | 15 269 | 437 | 17/07/2025 → hier |

Miroir attendu : ~60 Mo. Premier export : ~2 000 requêtes, 5 à 15 minutes
depuis GitHub Actions. Nuits suivantes : les 3 derniers jours, soit une
quinzaine de requêtes et moins d'une minute.

## Fonctionnement

- **Miroir par date, idempotent.** Chaque date exportée est remplacée en
  entier et consignée dans la table `export_log` (table, date, nombre de
  lignes, horodatage).
- **Une date est (ré)exportée** si elle est nouvelle, si son nombre de
  lignes a changé côté Radar (reprise, correction : détecté
  automatiquement) ou si elle fait partie des 3 derniers jours (données
  encore mouvantes). La date du jour n'est jamais exportée.
- **Reprise automatique.** Si le budget de temps est épuisé, l'export
  envoie ce qui est fait ; la nuit suivante reprend là où il s'est arrêté.
- **Colonnes explicites.** Une colonne disparue côté Radar fait échouer
  l'export (job rouge) au lieu de produire un miroir amputé en silence.

## Garde-fous

| Situation | Comportement |
|---|---|
| Miroir R2 corrompu (empreinte SHA-256 fausse) | Refus, code 3, rien n'est envoyé |
| Le miroir perdrait plus de 1 % de ses lignes (droit révoqué, table vidée, bug) | Refus `CHUTE_REFUSEE`, code 3, R2 intact |
| Droit retiré ou requête en erreur côté Radar | Job rouge (code 1), R2 intact, aucun secret affiché |
| Secret manquant ou table inconnue | Code 2, aucune lecture |
| Chaque envoi | L'ancienne version est conservée en `history/turf_history_prev.db` |
| Poste d'un dev | `turf_history*.db` est ignoré par Git : `publier_vers_github.bat` ne peut pas publier ces données internes |

## Mises en garde sur les données (à lire avant toute modélisation)

1. **`participants.cote_reference` est réécrite au fil de la journée**
   (constaté : 74 → 16 le même jour). Ce n'est **jamais** une cote à un
   instant donné. Pour le marché à T-x, utiliser `cotes_snapshots`
   (`minutes_avant_depart`) — disponible seulement depuis le 15/07/2026.
2. **Statistiques humaines (jockey, entraîneur) incomplètes avant
   mi-octobre 2025** : période de chauffe, à exclure de l'entraînement des
   variables humaines.
3. **Le Radar comme source de combinaison** : utilisable seulement sur les
   courses postérieures au 30/04/2026 (ses poids ont été entraînés
   jusqu'à cette date ; avant, il se serait « vu » lui-même).
4. **Rapports** : complets à partir du 21/07/2026 ; l'antérieur arrive
   avec la reprise Radar (~02/10).
5. **Champs absents** chez le Radar : déferré, réduction kilométrique,
   valeur de handicap. Candidats à une phase 1c (récupération via l'API
   PMU depuis GitHub Actions), à décider au vu des premiers résultats.
6. **Données internes.** Usage exclusif du labo ; jamais redistribuées ni
   publiées.

## Opérations

**Après fusion de la PR (une fois).** Actions → « Historique Radar -> R2 »
→ *Run workflow* → action `probe`. Attendu dans le journal :
`HISTORY_PROBE_OK {"courses": …, "current_user": "lecteur_benter"}`. Une
seule requête, possible à toute heure. Le premier export complet se fait
seul la nuit suivante à 01h17 UTC.

**Vérifier l'état du miroir.** Même bouton, action `status` : taille,
empreinte et nombre de lignes par table.

**Après la ligne de fin de reprise du Radar (~02/10).** *Run workflow*,
action `run`, `start_date` = `2025-07-17`, `end_date` = `2026-07-19`,
`tables` = `rapports_definitifs`, sans cocher le hors-créneau. Le bouton
part immédiatement : le déclencher entre 00h et 05h UTC (même heure à
Abidjan). Hors créneau, l'export s'arrête sans rien lire.

**Récupérer le miroir pour le labo (devs, analyses).** Avec les secrets R2
en variables d'environnement, sans aucun accès au Radar :

```
python -m turf_lab.history_export fetch --db turf_history.db
```

Téléchargement vérifié par empreinte ; `HISTORY_ABSENT` (code 1) tant que
le premier export n'a pas eu lieu. `fetch` n'écrit jamais sur R2 et ne
supprime jamais une copie locale.

**Codes de sortie.** 0 succès (ou hors créneau) · 1 erreur source ou miroir
absent (`fetch`) · 2 configuration · 3 garde-fou.
