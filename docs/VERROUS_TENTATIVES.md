# Table `verrous_tentatives` — tentatives de verrou sous le seuil de diffusion

**Statut : PROJET, version 3 confirmée. Rien n'est codé.** La mise en service
a lieu le lendemain de la lecture unique de l'ombre : le **13/11/2026**, ou le
**26/11/2026** si la remise à zéro est utilisée. Pas avant : le code touche
`turf_lab/daily_sync.py` et `turf_lab/database.py`, qui font partie de la
chaîne du matin gelée (`docs/OMBRE_FONDAMENTAL.md`).

| Date | Étape |
|---|---|
| 14/10/2026 | Spécification transmise par Steph ; précisions du dev NVE ; convergence avec le labo |
| 14/10/2026 | Décision de Steph : le labo écrit le code, le dev NVE relit la PR et vérifie la concordance chaque mardi |
| 14/10/2026 | Version 3 confirmée par le dev Radar (`daily_sync`, clé `spec_verrous_tentatives_v3`) : trois règles de reproductibilité, concordance C1 à C4, liste « non observée » produite par la requête. Le labo nomme le journal des refus (`verrous_refus`) |
| 20/10/2026 | Retouches du dev Radar, acceptées : `REFUSE` calculés à chaque passage (règle 3), ligne de journal `BALAYAGE` quotidienne relue chaque mardi, contrôle C5, cas test synthétique Moulins / Pontchâteau. Décision de Steph confirmée : le labo écrit le code, le dev Radar et le dev NVE relisent la PR |
| 20/10/2026 | Relecture du dev NVE, acceptée : marge avant l'écriture d'un `REFUSE` (arrivée enregistrée ou départ connu + 60 min), rapport quotidien committé dans le dépôt à la place de la ligne `BALAYAGE`. Calendrier fixé à partir du 22/10, sauf remise à zéro d'ici le 21/10 |
| 20/10/2026 | Règle de fermeture du dev Radar, retenue par le dev NVE à la place de la marge de 60 min : un `REFUSE` ne s'écrit que pour une course `FINISHED` ou qui n'est plus datée du jour UTC, jamais pour une course `ANNULEE`. C2 devient impossible par construction. Le premier passage du jour écrit les `REFUSE` de la veille avant son rapport |
| Jour J (13/11 ou 26/11) | Annonce par une seule ligne datée au dev Bases (changement de schéma : `verrous_tentatives` et `verrous_refus`), au dev NVE et au dev Radar, **avant** toute publication |
| Jour J et suivants | PR du labo : code, tests, cette documentation passée au statut « en service » ; relecture du dev Radar et du dev NVE ; fusion sur l'accord de Steph |

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

Moins de 50 % de partants cotés à **chaque** passage de la fenêtre de
l'horizon, et aucun verrou possible. Ligne écrite **au premier passage où la
course est `FINISHED` ou n'est plus datée du jour UTC** (règle 3), depuis le
journal `verrous_refus`. `decided_at_utc` est l'instant de ce
passage ; toutes les autres colonnes viennent du journal et ne
dépendent pas du moment du calcul (règles ci-dessous).

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

## Journal `verrous_refus` (source des `REFUSE`)

Aucune table ne garde aujourd'hui ce que la porte voit à un passage refusé :
la seule trace est la ligne de journal `GATE_REFUSED` (`race_id`, `horizon`,
part arrondie à 3 décimales, `now_utc`), éphémère et sans les deux nombres.
`odds_snapshots` n'est écrite qu'au verrou (clé `race_id + num + horizon`,
`INSERT OR IGNORE`) et **n'est ni lue ni écrite par ce chantier** : y écrire
les passages refusés remplacerait la photo du verrou.

Le journal est **créé par ce chantier**, dans `turf_bench.db` (donc sur R2,
lisible par les passages dans GitHub Actions comme par le dev Bases). Une ligne
par passage refusé, écrite dans `_lock_horizon`, dans la branche
`GATE_REFUSED`, à côté de la ligne de journal (qui gagne `n_partants` et
`n_cotes`) :

| Colonne | Type | Sens |
|---|---|---|
| `id` | INTEGER | ordre d'écriture |
| `race_id`, `horizon` | TEXT | course et horizon refusés |
| `observed_at_utc` | TEXT ISO | instant du passage (`now_utc` de `GATE_REFUSED`) |
| `part_cotee` | REAL | `priced_ratio`, arrondie à 4 décimales |
| `n_partants`, `n_cotes` | INTEGER | les deux nombres de `priced_ratio` |
| `min_lock_ratio`, `min_priced_ratio` | REAL | seuils en vigueur à ce passage |
| `gate_version` | TEXT | `publication_gate.GATE_VERSION` à ce passage |

Contrainte `UNIQUE(race_id, horizon, observed_at_utc)`. **Ajout seul** : deux
déclencheurs refusent `UPDATE` et `DELETE`. Une erreur d'écriture est
journalisée (`VERROU_REFUS_ERROR {...}`) et ne change rien au passage. Volume :
une ligne par course, par horizon dû et non verrouillé, et par passage refusé ;
mesuré la première semaine.

## Règles de reproductibilité et de contrôle

Aucune ligne ne dépend du moment où elle est calculée.

1. **Passage retenu.** Pour un `REFUSE`, `part_cotee`, `n_partants`, `n_cotes`
   et `heure_max_utc` viennent du passage du journal où la part cotée est la
   plus haute dans la fenêtre ; en cas d'égalité, le plus ancien. Seuls
   comptent les passages antérieurs au départ, selon l'heure de départ de
   `races` au moment du calcul.
2. **Seuils.** `min_lock_ratio`, `min_priced_ratio` et `gate_version` sont
   ceux du passage retenu, lus dans le journal. Comme le journal les
   enregistre à chaque passage, un changement de la porte déployé dans la
   journée (y compris le soir, entre 21h30 et 23h00) ne fausse aucune ligne.
3. **Calcul à chaque passage, sur les seules courses fermées.** Chaque
   passage calcule les `REFUSE` des courses du jour ou de la veille qui ne
   peuvent plus être verrouillées : au moins un passage au journal, aucune
   édition NVE, pas encore de ligne. Une course est fermée dans deux cas
   seulement, pris dans le code (numéros de ligne au 20/10/2026) :
   - elle est `FINISHED` (arrivée définitive) : la course est gelée et plus
     aucun pronostic n'est touché (`turf_lab/daily_sync.py`, ligne 659) ;
   - elle n'est plus datée du jour UTC : `due_horizons` ne rend alors plus
     aucun horizon (ligne 258).

   Jamais pour une course `ANNULEE`, qui relève de la liste « non observée ».
   Chaque passage réécrit l'heure de départ dans `races` (`INSERT OR
   REPLACE`) : une règle fondée sur l'heure de départ laisserait un départ
   repoussé rendre l'horizon dû à nouveau. Fondée sur ces deux états, elle rend
   C2 impossible par construction. Aucun passage n'est journalisé après la
   fermeture, donc la ligne est la même quel que soit le moment du calcul ; la
   clé unique ignore les répétitions. La règle ne dépend d'aucune heure de
   passage : le métronome qui lance les passes vit hors de ce dépôt (constat du
   20/10 dans les journaux Actions : 4 passages par heure, 24 h sur 24).
4. **Rapport quotidien committé.** La tâche qui a accès à R2 (le passage de
   `daily_sync`) écrit chaque jour, pour la veille, un rapport dans le dépôt :
   `rapports/verrous/AAAA-MM-JJ.md`, committé avec les exports du site. Il
   donne le nombre de `PARTIEL` et de `REFUSE` par horizon, les horizons « non
   observée » par motif, la liste `HORS_PROGRAMME`, et le résultat de C1 à C5
   avec les couples course et horizon en défaut. Comptes et identifiants
   seulement : aucune probabilité, rien de l'ombre. Chaque passage régénère le
   rapport de la veille depuis les tables en ajout seul ; Git ne committe que
   s'il a changé, et un envoi refusé (conflit connu) est rattrapé au passage
   suivant. Le premier passage du jour UTC écrit les `REFUSE` de la veille
   **avant** de calculer le rapport de la veille. Le dev NVE relit les sept
   rapports chaque mardi, sans clé R2 ni
   journaux Actions. Ce rapport remplace la ligne de journal `BALAYAGE` ;
   aucune table ni aucun changement de schéma de plus.

## Liste « non observée »

Produite par la requête, sans table : un horizon sans édition NVE et sans
passage dans le journal. Motifs :

| Motif | Cas |
|---|---|
| `ANNULEE` | course annulée (`races.status`) |
| `HEURE_INCONNUE` | heure de départ inconnue : la fenêtre ne peut pas se fermer |
| `AUCUN_PASSAGE` | course connue de la chaîne, aucun passage dans la fenêtre (passe manquée, partants absents) |

Une course du programme PMU jamais reçue par les passes n'est pas dans
`races` : le rapport quotidien compare le programme de la veille à `races` et
l'écrit (`HORS_PROGRAMME`, comptes et `race_id`). Cas connu : Moulins R11C1 et
Pontchâteau R12C1 du 10/10/2026, antérieurs à la mise en service, donc absents
du journal. La vérification du dev Radar du 14/10 a établi que la porte les a
refusés jusqu'au départ ; ils deviennent un test synthétique à quatre `REFUSE`
attendus.

## Concordance, dans les deux sens, depuis la mise en service

Chaque requête doit renvoyer zéro ligne. Le rapport quotidien en donne le résultat, couples en défaut compris ; le dev NVE le relit chaque mardi.

| Contrôle | Règle |
|---|---|
| C1 | Tout horizon sans édition NVE a une ligne `REFUSE` ou figure dans la liste « non observée » |
| C2 | Aucune ligne `REFUSE` là où une édition NVE existe |
| C3 | Toute édition NVE à `odds_real = 0` a sa ligne `PARTIEL` avec la même part et le même instant, et aucune ligne `PARTIEL` n'existe sans elle |
| C4 | Toute ligne `REFUSE` a une part inférieure à son `min_lock_ratio` ; sinon, c'est la porte qu'il faut examiner |
| C5 | Toute ligne `PARTIEL` vérifie `min_lock_ratio` ≤ `part_cotee` < `min_priced_ratio` (demandé par le dev NVE) |

```sql
-- C1 : horizons sans édition NVE, sans REFUSE et sans motif « non observée ».
WITH horizons(h) AS (VALUES ('T_MATIN'), ('T90'), ('T30'), ('T15'))
SELECT r.race_id, h.h AS horizon
FROM races r CROSS JOIN horizons h
WHERE r.date >= :mise_en_service          -- AAAA-MM-JJ
  AND NOT EXISTS (SELECT 1 FROM predictions p WHERE p.race_id = r.race_id
                  AND p.horizon = h.h AND p.engine_name = 'NEW_VALUE_ENGINE')
  AND NOT EXISTS (SELECT 1 FROM verrous_tentatives v WHERE v.race_id = r.race_id
                  AND v.horizon = h.h AND v.issue = 'REFUSE')
  AND NOT (r.status = 'ANNULEE' OR r.scheduled_start_time IS NULL
           OR NOT EXISTS (SELECT 1 FROM verrous_refus j WHERE j.race_id = r.race_id
                          AND j.horizon = h.h));

-- C2 : REFUSE là où une édition NVE existe.
SELECT v.race_id, v.horizon FROM verrous_tentatives v
JOIN predictions p ON p.race_id = v.race_id AND p.horizon = v.horizon
                  AND p.engine_name = 'NEW_VALUE_ENGINE'
WHERE v.issue = 'REFUSE';

-- C3 : éditions sans marché sans PARTIEL identique, puis PARTIEL sans édition.
SELECT p.race_id, p.horizon FROM predictions p
LEFT JOIN verrous_tentatives v ON v.race_id = p.race_id AND v.horizon = p.horizon
                              AND v.issue = 'PARTIEL'
WHERE p.engine_name = 'NEW_VALUE_ENGINE' AND p.odds_real = 0
  AND p.lock_time_utc >= :mise_en_service
  AND (v.race_id IS NULL OR v.part_cotee <> p.priced_ratio
       OR v.decided_at_utc <> p.lock_time_utc);
SELECT v.race_id, v.horizon FROM verrous_tentatives v
LEFT JOIN predictions p ON p.race_id = v.race_id AND p.horizon = v.horizon
                       AND p.engine_name = 'NEW_VALUE_ENGINE' AND p.odds_real = 0
WHERE v.issue = 'PARTIEL' AND p.race_id IS NULL;

-- C4 : REFUSE au-dessus du seuil de verrou.
SELECT race_id, horizon FROM verrous_tentatives
WHERE issue = 'REFUSE' AND part_cotee >= min_lock_ratio;

-- C5 : PARTIEL hors de la bande [seuil de verrou, seuil de diffusion).
SELECT race_id, horizon FROM verrous_tentatives
WHERE issue = 'PARTIEL'
  AND (part_cotee < min_lock_ratio OR part_cotee >= min_priced_ratio);
```

Le délai avant le départ se calcule par jointure sur `races`
(`scheduled_start_time`).

## Ce que la table ne contient pas

- **Les verrous à 90 % ou plus** : aucune ligne. Leur décision est dans
  `publication_decisions` (motif `OK`). L'absence de ligne ne suffit donc pas
  à conclure : il faut lire `predictions`, `publication_decisions` et la liste
  « non observée ».
- **RADAR_V4**, qui a sa propre porte (`_lock_radar`, 90 %) et n'est jamais
  diffusé.
- **Le passé** : aucune ligne reconstituée. Seules les fenêtres ouvertes après
  la mise en service sont suivies ; une fenêtre ouverte avant n'a ni `REFUSE`
  ni motif « non observée ».
- **Rien de l'ombre du fondamental.**

## Garanties (les mêmes que `publication_decisions`)

- Deux déclencheurs SQLite refusent tout `UPDATE` et tout `DELETE` sur
  `verrous_tentatives` et sur `verrous_refus`.
- Enregistrement pur : une erreur d'écriture est journalisée
  (`VERROU_TENTATIVE_ERROR {...}`) et n'annule jamais un verrou. Chaque ligne
  écrite est journalisée (`VERROU_TENTATIVE {...}`), comptes et parts
  seulement.
- Aucune règle de verrou, de diffusion ni de moteur ne change.
- Un test échoue si `MIN_LOCK_RATIO` ou `MIN_PRICED_RATIO` change sans
  nouvelle annonce, comme `test_empreinte_de_la_porte` pour la porte de
  diffusion.

## Tests prévus

1. `PARTIEL` : `part_cotee` et `decided_at_utc` identiques à l'édition (C3).
2. Les déclencheurs refusent modification et suppression, sur les deux tables.
3. Une erreur d'écriture, dans l'une ou l'autre table, n'annule pas le verrou
   et ne change rien au passage.
4. `REFUSE` : valeurs du passage à la part la plus haute, le plus ancien en cas
   d'égalité, passages après le départ ignorés.
5. Seuils et `gate_version` du passage retenu, même si la porte change dans la
   journée.
6. Un horizon refusé le matin puis verrouillé plus tard (même dans la passe de
   l'horizon suivant) n'a jamais de `REFUSE` (C2).
7. Liste « non observée » : course annulée, heure inconnue, aucun passage,
   chacune avec son motif ; aucune `REFUSE` sans passage au journal (C1).
8. Aucune reprise du passé : fenêtre ouverte avant la mise en service ignorée.
9. Verrou à 90 % ou plus : aucune ligne.
10. Calcul répété à chaque passage : une seule ligne, identique ; une course
    de la veille partie après minuit UTC est couverte ; aucun `REFUSE` tant que
    la course n'est ni `FINISHED` ni d'un jour UTC passé ; aucun `REFUSE` pour
    une course `ANNULEE`.
11. Empreinte des deux seuils.
12. Les cinq requêtes C1 à C5 renvoient zéro ligne sur un banc synthétique
    complet.
13. Cas synthétique Moulins R11C1 et Pontchâteau R12C1 : chacune des deux
    courses, refusée à chaque passage jusqu'au départ, donne quatre `REFUSE`
    (`T_MATIN`, `T90`, `T30`, `T15`), huit en tout.
14. Rapport quotidien : comptes exacts pour la veille, C1 à C5 avec les couples
    en défaut, aucune probabilité ni marqueur de l'ombre ; régénéré à
    l'identique, rattrapé après un envoi refusé.
15. Départ repoussé de 2 h après des passages refusés, puis verrou : aucun
    `REFUSE` (C2).
16. Arrivée définitive jamais reçue : `REFUSE` écrit au premier passage du jour
    UTC suivant, avant le rapport de la veille, et compté dans ce rapport.
