# Table `verrous_tentatives` — tentatives de verrou sous le seuil de diffusion

**Statut : PROJET. Rien n'est codé.** La mise en service a lieu le lendemain de
la lecture unique de l'ombre : le **13/11/2026**, ou le **26/11/2026** si la
remise à zéro est utilisée. Pas avant : le code touche `turf_lab/daily_sync.py`
et `turf_lab/database.py`, qui font partie de la chaîne du matin gelée
(`docs/OMBRE_FONDAMENTAL.md`).

| Date | Étape |
|---|---|
| 14/10/2026 | Spécification transmise par Steph ; précisions du dev NVE ; convergence avec le labo (version 3, ci-dessous) |
| 14/10/2026 | Décision de Steph : le labo écrit le code, le dev NVE relit la PR et vérifie la concordance chaque mardi |
| Jour J (13/11 ou 26/11) | Annonce par ligne datée au dev Bases (changement de schéma) et au dev NVE, **avant** toute publication |
| Jour J et suivants | PR du labo : code, tests, cette documentation passée au statut « en service » ; relecture du dev NVE ; fusion sur l'accord de Steph |

## Pourquoi

Sous 50 % de partants cotés, un horizon n'est pas verrouillé : le refus
(`GATE_REFUSED`) ne laisse qu'une ligne dans les journaux GitHub, éphémères, et
rien dans la base. Entre 50 et 90 %, l'édition est posée sans marché
(`odds_real = 0`) ; on le lit dans `predictions` et `publication_decisions`,
mais dispersé. La table rassemble les deux cas, ligne par ligne, et rend
vérifiable que **chaque course et chaque horizon sans édition diffusable a une
explication**.

## Ce que contient la table

Une ligne par course et par horizon, moteur `NEW_VALUE_ENGINE` seulement.
Contrainte `UNIQUE(race_id, horizon)` : la première ligne est définitive.

| Colonne | Type | Sens |
|---|---|---|
| `id` | INTEGER | ordre d'écriture |
| `race_id` | TEXT | identifiant de course (`R1C1_13112026_VINCENNES`) |
| `horizon` | TEXT | `T_MATIN`, `T90`, `T30`, `T15` |
| `issue` | TEXT | `PARTIEL` ou `REFUSE` |
| `part_cotee` | REAL | part des partants actifs cotés, arrondie à 4 décimales comme `predictions.priced_ratio` |
| `n_partants` | INTEGER | partants actifs (non-partants exclus) |
| `n_cotes` | INTEGER | partants actifs dont au moins une cote est réelle |
| `decided_at_utc` | TEXT ISO | instant de la décision (voir plus bas) |
| `heure_max_utc` | TEXT ISO | instant où la part retenue a été observée |
| `min_lock_ratio` | REAL | seuil de verrou en vigueur (`odds_quality.MIN_LOCK_RATIO`, 0,50) |
| `min_priced_ratio` | REAL | seuil de diffusion en vigueur (`odds_quality.MIN_PRICED_RATIO`, 0,90) |
| `gate_version` | TEXT | version des règles de diffusion (`publication_gate.GATE_VERSION`, `PG1`) |

`part_cotee`, `n_partants` et `n_cotes` suivent la définition de
`odds_quality.priced_ratio` : une cote manquante sur 7 partants et quatre sur 28
donnent des parts voisines, les deux nombres les distinguent.

### `PARTIEL`

Verrou posé avec une part cotée d'au moins 50 % et de moins de 90 %. Ligne
écrite **au moment du verrou**, après lui, dans `_lock_horizon`.
`part_cotee` est la valeur enregistrée avec l'édition : la jointure avec
`predictions` donne deux chiffres identiques. `decided_at_utc` et
`heure_max_utc` sont égaux à `predictions.lock_time_utc`.

### `REFUSE`

Moins de 50 % de partants cotés à **chaque** observation de la fenêtre de
l'horizon. Ligne écrite **à la fermeture de la fenêtre**, avec la part la plus
haute observée, ses deux nombres et son heure (`heure_max_utc`).
`decided_at_utc` est l'instant de l'écriture.

## Fenêtres

Chaque fenêtre s'ouvre à l'heure où l'horizon devient dû dans
`due_horizons` et **se ferme au départ de la course**, pour les quatre
horizons :

| Horizon | Ouverture |
|---|---|
| `T_MATIN` | 06h30 UTC le jour de la course |
| `T90` | 100 min avant le départ |
| `T30` | 35 min avant le départ |
| `T15` | 18 min avant le départ |

Un horizon ouvert reste dû jusqu'au départ, sans borne haute. Dans l'archive
publique, du 01/10 au 14/10/2026 : 60 éditions `T90` sur 491 ont été posées
après l'ouverture de `T30`, et 40 `T30` sur 486 après l'ouverture de `T15`,
toutes dans la même passe que l'horizon suivant (exemple : Graignes R6C1 le
02/10, les quatre horizons à 15h16 pour un départ à 15h30). Fermer une fenêtre
à l'ouverture de l'horizon suivant écrirait un `REFUSE` suivi d'un verrou ;
la règle est donc la même pour les quatre. Pour ces éditions, `decided_at_utc`
montre qu'une édition `T90` a été prise à une trentaine de minutes du départ.

## Table de suivi (interne au labo, hors garantie)

`odds_snapshots` n'est écrite qu'au verrou (clé `race_id + num + horizon`,
`INSERT OR IGNORE`) : rien n'y existe pour un horizon refusé, et y écrire les
passes refusées remplacerait la photo du verrou par la première observation
refusée. Le `REFUSE` est donc calculé depuis une table de suivi propre au labo,
qui enregistre ce que la porte a vu à chaque passe pour un horizon dû et pas
encore verrouillé :

| Colonne | Sens |
|---|---|
| `race_id`, `horizon` | clé |
| `part_max`, `n_partants_max`, `n_cotes_max`, `heure_max_utc` | meilleure observation de la fenêtre |
| `nb_observations` | nombre de passes où la porte a évalué l'horizon |
| `premiere_observation_utc`, `derniere_observation_utc` | bornes des observations |

Elle est modifiable (une ligne par course et par horizon, mise à jour à chaque
passe). Ce n'est pas une table de référence : seules `verrous_tentatives` et
`verrous_non_observes` portent la garantie d'ajout seul.

## Écriture du `REFUSE` (proposé par le labo le 14/10, à confirmer à la relecture du dev NVE)

1. Au **premier passage après le départ** : pour chaque horizon dû, non
   verrouillé, observé au moins une fois, une ligne `REFUSE`.
2. **Balayage de fin de journée**, en filet, au premier passage après 23h00
   UTC, pour toutes les courses du jour.

La clé unique rend la double écriture sans effet : la seconde est ignorée.

## Table `verrous_non_observes` (proposée par le labo le 14/10, à confirmer)

Un horizon sans édition et sans observation n'est pas un refus de marché. Il
est inscrit, au balayage de fin de journée, dans une table séparée, ajout seul
avec les mêmes deux déclencheurs : `race_id`, `horizon`, `motif`,
`constate_utc`. `verrous_tentatives` garde ainsi uniquement les décisions de
la porte.

| Motif | Cas |
|---|---|
| `ANNULEE` | course annulée avant le départ |
| `HEURE_INCONNUE` | heure de départ inconnue : la fenêtre ne peut pas se fermer |
| `AUCUN_PASSAGE` | course connue de la chaîne, mais aucune observation dans la fenêtre (passe manquée, partants absents) |
| `HORS_PROGRAMME` | course du programme PMU du jour, relu au balayage, jamais reçue par les passes |

Cas test : Moulins R11C1 et Pontchâteau R12C1 du 10/10/2026, dans la base mais
sans aucune édition. La table de suivi dira si les passes les ont vues.

## Concordance

Chaque course et chaque horizon sans édition `NEW_VALUE_ENGINE` figure soit
dans `verrous_tentatives` (`REFUSE`), soit dans `verrous_non_observes`. Jamais
silencieusement absent des deux. Le dev NVE joue la requête chaque mardi et
vérifie que les lignes `PARTIEL` concordent avec les éditions publiées.

```sql
-- Horizons sans édition NVE et sans explication : doit renvoyer zéro ligne.
WITH horizons(h) AS (VALUES ('T_MATIN'), ('T90'), ('T30'), ('T15'))
SELECT r.race_id, h.h AS horizon
FROM races r CROSS JOIN horizons h
WHERE r.date >= :mise_en_service          -- AAAA-MM-JJ
  AND NOT EXISTS (SELECT 1 FROM predictions p WHERE p.race_id = r.race_id
                  AND p.horizon = h.h AND p.engine_name = 'NEW_VALUE_ENGINE')
  AND NOT EXISTS (SELECT 1 FROM verrous_tentatives v WHERE v.race_id = r.race_id
                  AND v.horizon = h.h AND v.issue = 'REFUSE')
  AND NOT EXISTS (SELECT 1 FROM verrous_non_observes n WHERE n.race_id = r.race_id
                  AND n.horizon = h.h);

-- PARTIEL : même part que l'édition, même instant.
SELECT v.race_id, v.horizon
FROM verrous_tentatives v
JOIN predictions p ON p.race_id = v.race_id AND p.horizon = v.horizon
                  AND p.engine_name = 'NEW_VALUE_ENGINE'
WHERE v.issue = 'PARTIEL'
  AND (p.priced_ratio <> v.part_cotee OR p.lock_time_utc <> v.decided_at_utc);   -- zéro ligne
```

## Ce que la table ne contient pas

- **Les verrous à 90 % ou plus** : aucune ligne. Leur décision est dans
  `publication_decisions` (motif `OK`). L'absence de ligne ne suffit donc pas
  à conclure : il faut lire `predictions`, `publication_decisions` et
  `verrous_non_observes`.
- **RADAR_V4**, qui a sa propre porte (`_lock_radar`, 90 %) et n'est jamais
  diffusé.
- **Le passé** : aucune ligne reconstituée. Seules les fenêtres ouvertes après
  la mise en service sont suivies ; une fenêtre ouverte avant n'a ni `REFUSE`
  ni ligne « non observée ».
- **Rien de l'ombre du fondamental.**

## Garanties (les mêmes que `publication_decisions`)

- Deux déclencheurs SQLite refusent tout `UPDATE` et tout `DELETE` sur
  `verrous_tentatives` et sur `verrous_non_observes`.
- Enregistrement pur : une erreur d'écriture est journalisée
  (`VERROU_TENTATIVE_ERROR {...}`) et n'annule jamais un verrou. Chaque ligne
  écrite est journalisée (`VERROU_TENTATIVE {...}`), comptes et parts
  seulement.
- Aucune règle de verrou, de diffusion ni de moteur ne change.
- Un test échoue si `MIN_LOCK_RATIO` ou `MIN_PRICED_RATIO` change sans
  nouvelle annonce, comme `test_empreinte_de_la_porte` pour la porte de
  diffusion.

## Tests prévus

1. `PARTIEL` : `part_cotee` et `decided_at_utc` identiques à l'édition.
2. Les déclencheurs refusent modification et suppression, sur les deux tables.
3. Une erreur d'écriture n'annule pas le verrou.
4. `REFUSE` au premier passage après le départ, avec la part maximale, ses
   deux nombres et son heure.
5. Un horizon refusé le matin puis verrouillé plus tard (même dans la passe de
   l'horizon suivant) n'a jamais de `REFUSE`.
6. Aucun `REFUSE` sans observation : course annulée, heure inconnue, aucune
   passe → ligne dans `verrous_non_observes` avec le bon motif.
7. Aucune reprise du passé : fenêtre ouverte avant la mise en service ignorée.
8. Verrou à 90 % ou plus : aucune ligne.
9. Double écriture (passage après le départ puis balayage) : une seule ligne.
10. Empreinte des deux seuils.
11. La requête de concordance renvoie zéro ligne sur un banc synthétique
    complet.
