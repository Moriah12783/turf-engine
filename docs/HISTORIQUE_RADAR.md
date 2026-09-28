# Historique Radar → R2 (phase 1 du plan Benter)

**Objectif.** Constituer, sans jamais toucher à la production, un miroir
fidèle d'un an d'historique PMU (courses, partants, arrivées, cotes,
rapports) pour entraîner et valider l'étape 1 du plan Benter : le modèle
fondamental (logit conditionnel **sans les cotes**), puis sa combinaison
avec le marché. Critère d'entrée G1 : au moins 3 000 courses de test hors
échantillon.

| | |
|---|---|
| Source | Supabase « Radar Elite Predictive », rôle `lecteur_benter` (lecture seule, 6 tables, 2 connexions max, 120 s par requête, transaction inactive fermée après 60 s) |
| Secret | `RADAR_HISTORY_DSN` (dépôt turf-engine, Session pooler, port 5432) + les 4 secrets R2 déjà en place |
| Cible | R2 `turf-engine-data` → `history/turf_history.db` (version précédente : `history/turf_history_prev.db`) |
| Code | `turf_lab/history_export.py`, tests `tests/test_history_export.py` |
| Planification | `.github/workflows/history_export.yml`, chaque nuit à **01h17 UTC**, filet à **03h47 UTC** (crons GitHub non garantis) |
| Production | **Aucun impact** : ne lit ni n'écrit `turf_bench.db`, ne committe rien, ne déploie rien, groupe de concurrence séparé |

## Règles convenues avec le dev Radar (27-28/09/2026)

1. **Pagination par date** : une requête par table et par date (au plus
   ~3 500 lignes), jamais de lecture d'une table entière. Le repérage des
   dates à lire se fait par un comptage par date (parcours d'index seul,
   quelques millisecondes, aucune ligne lue).
2. **Créneau 00h00–05h00 UTC, arrêt strict.** Aucune requête après
   04h55 (marge de 5 min) : hors créneau, l'export ne lit rien
   (`HISTORY_HORS_CRENEAU`), et le budget de temps (45 min) est rogné pour
   s'arrêter à 04h55, y compris avant les requêtes de comptage. Les
   captures du Radar démarrent à 05h20. Un lancement manuel hors créneau
   exige la case `force_hors_creneau` : à réserver aux urgences.
3. **Lecture seule, autocommit.** Aucune écriture côté Radar (le rôle est
   en `default_transaction_read_only`). Autocommit obligatoire : le rôle
   ferme toute transaction restée inactive plus de 60 s.

### Garanties d'écriture du Radar (message du 28/09/2026)

- **Reprise des rapports** : chaque date est écrite en une seule fois, donc
  visible complète ou pas du tout. La reprise descend d'environ 75 dates
  par nuit depuis le 21/07/2026 (le 20/07 n'est pas encore repris).
- **`rapports_definitifs` ne fait que s'enrichir** : le lendemain dans 74 %
  des cas, parfois le surlendemain, jusqu'à 17 jours après la course lors
  des reprises.
- **Les 5 autres tables** (`courses`, `participants`, `arrivees`,
  `cotes_snapshots`, `participants_cotes_hist`) ne sont écrites que le jour
  même de la course (mesuré depuis le 01/08, sans exception).
- Tout changement de ce mode d'écriture (par exemple une correction tardive
  des arrivées) sera annoncé par ligne datée, comme les changements de
  colonnes.

**Conséquence : plus de verrou ni de relance manuelle.** Une date non
encore reprise n'existe pas côté Radar, donc n'est pas lue. Dès qu'elle
est écrite, complète, le comptage par date la détecte et la nuit suivante
la lit. Un rapport tardif change le comptage de sa date et la fait relire.
Ce repère par comptage n'utilise aucun horodatage (`captured_at` n'est pas
fiable sur les tables réécrites) et couvre aussi le cas, improbable, d'une
date écrite en plusieurs fois : elle est relue la nuit suivante.

## Volumes (27/09/2026)

| Table | Lignes | Dates | Couverture |
|---|---:|---:|---|
| `participants` | 186 338 | 438 | 17/07/2025 → aujourd'hui |
| `cotes_snapshots` | 136 243 | 75 | 15/07/2026 → aujourd'hui |
| `rapports_definitifs` | 39 344 | 68 | 21/07/2026 → hier (reprise en cours, ~75 dates par nuit) |
| `courses` | 15 421 | 438 | 17/07/2025 → aujourd'hui |
| `arrivees` | 15 269 | 437 | 17/07/2025 → hier |
| `participants_cotes_hist` | 29 775 | 13 | 15/09/2026 → aujourd'hui |

Miroir attendu : ~60 Mo. Premier export : environ 1 550 requêtes (une par
table et par date, plus 6 comptages et le test d'identité), 5 à 15 minutes
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

### Angle mort du comptage et ses deux filets (message Radar du 28/09/2026)

Un comptage ne voit pas une ligne **modifiée sur place** : même nombre de
lignes, valeur différente (`cote_reference`, statut d'un partant,
`ordre_arrivee`, `annulee`…). Aujourd'hui toutes ces modifications ont lieu
le jour même, donc la fenêtre de 3 jours les relit. Deux filets en plus :

- **Relecture liée.** Toute date relue dans `participants_cotes_hist` (une
  cote a changé) est aussi relue dans `participants`. Coût : nul ou
  presque (quelques dates).
- **Empreinte hebdomadaire.** Chaque dimanche à 01h17 UTC, **à partir du
  11/10/2026** (après l'échéance Radar du 06/10), une empreinte MD5 par
  date et par table est calculée côté Radar sur les colonnes exportées :
  6 requêtes groupées de quelques secondes. Toute date dont l'empreinte a
  changé, ou n'a jamais été calculée, est relue.
  - Le **premier** contrôle relit donc une fois toutes les dates (~1 500
    lectures, comme la première nuit). Ensuite : 6 empreintes et une
    quarantaine de relectures par semaine (les dates de la semaine, plus
    celles réellement modifiées).
  - L'empreinte est calculée **avant** la lecture et enregistrée **avec**
    les lignes : une modification survenue entre les deux est vue au
    contrôle suivant. Un contrôle interrompu par le budget reprend la
    semaine suivante sans rien perdre.
  - Lancement manuel : *Run workflow*, entrée `empreintes` = `oui` (ou `non`
    pour sauter le contrôle un dimanche).

### Contrôle qualité automatique (table `anomalies` du miroir)

Après chaque export, un contrôle **local** (aucune requête Radar) remplit la
table `anomalies`, refaite à chaque passage (une anomalie réparée côté
Radar disparaît d'elle-même) :

| `anomalie` | Définition | Colonne utile |
|---|---|---|
| `arrivee_absente` | Course dont le `statut` n'est pas `COURSE_ANNULEE` mais sans ligne dans `arrivees` | `partants_avec_ordre` : partants dont `participants.ordre_arrivee` est rempli (repli possible) |
| `annulee_incoherente` | `statut = 'COURSE_ANNULEE'` mais `annulee` faux | — |

Le journal du workflow en donne le résumé (`HISTORY_QUALITE`).

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
   tout seul, date par date, au rythme de la reprise Radar (~75 dates par
   nuit, fin prévue ~02/10).
5. **`participants_cotes_hist` est la vraie cote à un instant donné** :
   chaque changement de `cote_reference` et `cote_direct`, horodaté par
   `changed_at` (29 775 changements du 15/09 au 27/09). C'est la matière
   première de l'étape 2 (combinaison avec le marché à T-90, T-30, T-15),
   plus fine que les photographies de `cotes_snapshots`. Seulement depuis
   le 15/09/2026 : chaque jour de journal est irremplaçable.
6. **Champs absents** chez le Radar : déferré, réduction kilométrique,
   valeur de handicap. Candidats à une phase 1c (récupération via l'API
   PMU depuis GitHub Actions), à décider au vu des premiers résultats.
7. **Courses sans résultat : réponses du Radar du 28/09/2026.** Sur les
   15 421 courses (au 27/09), 122 n'ont pas de résultat exploitable :
   - **108 annulées** (`statut = 'COURSE_ANNULEE'`) ;
   - **25/06/2026, La Teste, R1C1 à R1C12** : **non courue**. Le PMU
     affiche encore toute la réunion en `PROGRAMMEE`, sans arrivée, et la
     reprise n'a trouvé aucun rapport pour R1. À exclure ;
   - **30/11/2025 R15C7** (Mauquenchy) et **07/05/2026 R1C7**
     (ParisLongchamp) : courues (arrivée officielle au PMU) mais **trous de
     capture du Radar** : ni partants, ni arrivée, ni rapports. À exclure ;
     toute réparation sera annoncée par ligne datée.

   Cas à part, **28/07/2026** : `arrivees` vide pour les 31 courses courues,
   mais `participants.ordre_arrivee` est rempli (313 partants sur 458) et
   les 534 rapports sont présents. Ces courses restent exploitables par
   l'ordre d'arrivée. Pas de réparation Radar avant le 06/10
   (pré-enregistrement).
8. **Le champ `courses.annulee` n'est pas fiable, sur toute la période :
   `statut` fait foi** (confirmé par le Radar). Seules 16 courses (28/07 et
   12/08/2026) ont `annulee = true` ; 92 courses annulées d'après leur
   statut ont `annulee = false` (par exemple le 04/09/2026 R5C2). Filtrer
   sur `annulee` garderait des courses sans gagnant, et chacune fausserait
   la vraisemblance du logit conditionnel.

   **Règle officielle « course courue »** (Radar, 28/09/2026) : `statut`
   commence par `ARRIVEE` ou vaut `FIN_COURSE`, **et** la course a une ligne
   dans `arrivees` ou au moins un `participants.ordre_arrivee`. Elle est
   codée une fois pour toutes dans la **vue `courses_courues`** du miroir
   (colonnes de `courses`, plus `a_arrivee` et `partants_avec_ordre`) :
   **15 299 courses courues** au 27/09. Tout entraînement ou test part de
   cette vue, jamais de `courses` directement. Le journal du workflow en
   donne le compte (`HISTORY_QUALITE`, clé `courses_courues`).
9. **Données internes.** Usage exclusif du labo ; jamais redistribuées ni
   publiées.

## Opérations

**Après fusion de la PR (une fois).** Actions → « Historique Radar -> R2 »
→ *Run workflow* → action `probe`. Attendu dans le journal :
`HISTORY_PROBE_OK {"courses": …, "current_user": "lecteur_benter"}`. Une
seule requête, possible à toute heure. Le premier export complet se fait
seul la nuit suivante à 01h17 UTC.

**Vérifier l'état du miroir.** Même bouton, action `status` : taille,
empreinte et nombre de lignes par table.

**Reprise des rapports.** Rien à faire : les dates reprises arrivent
seules chaque nuit. À la ligne de fin de reprise du Radar (~02/10),
vérifier avec `status` que `rapports_definitifs` couvre bien le
17/07/2025 → hier.

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
