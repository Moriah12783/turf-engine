# Table `publication_decisions` — décisions de la porte de diffusion

**Annoncée le 27/09/2026** (ligne datée dans l'annexe du pont RADAR_V4), à la demande de Bases, consommateur en lecture seule de `turf_bench.db` sur R2. Ajout pur : aucun moteur, aucune règle de verrouillage, aucune règle de la porte n'est modifiée.

## Ce que contient la table

Une ligne par course et par horizon, écrite **à l'instant où l'horizon est verrouillé** (moteur `NEW_VALUE_ENGINE`, celui que la porte juge), avec la réponse que `turf_lab.publication_gate.can_publish` donne à cet instant précis.

| Colonne | Type | Sens |
|---|---|---|
| `id` | INTEGER | ordre d'écriture |
| `race_id` | TEXT | identifiant de course (`R1C1_27092026_VINCENNES`) |
| `horizon` | TEXT | `T_MATIN`, `T90`, `T30`, `T15` |
| `publishable` | INTEGER 0/1 | 1 si la porte autorisait la diffusion |
| `reason` | TEXT | vocabulaire existant de `can_publish` (voir plus bas) |
| `decided_at_utc` | TEXT ISO | instant du verrou, **égal** à `predictions.lock_time_utc` de l'édition NVE |
| `gate_version` | TEXT | version des règles de la porte (`PG1` au 27/09/2026) |

Contrainte `UNIQUE(race_id, horizon)` : la première décision est définitive. **Ajout seul garanti par la base** : deux déclencheurs SQLite refusent tout `UPDATE` et tout `DELETE`.

## Vocabulaire de `reason`

`OK`, `PRICED_RATIO_LOW`, `ODDS_DEFAULT`, `BEFORE_0630`, `STALE_ODDS`, `NO_LOCK_PROOF`, `NOT_LOCKED`, `START_UNKNOWN`, `RACE_STARTED`, `RACE_CANCELLED`, `RACE_UNKNOWN`. Libellés dans `REASON_LABELS`. Au verrou, les cas attendus en pratique sont `OK` (marché coté à ≥ 90 %) et `PRICED_RATIO_LOW` (édition posée entre 50 et 90 %, `odds_real = 0`).

## Ce que la table ne contient pas

- **Aucune ligne sans verrou.** Sous 50 % de partants cotés, l'horizon n'est pas verrouillé (`GATE_REFUSED` dans les logs) : pas de ligne. L'absence de ligne pour un horizon signifie « pas de verrou NVE », à lire dans `predictions`.
- **Aucune ligne pour RADAR_V4**, qui a sa propre porte (`_lock_radar`) et n'est jamais diffusé.
- **Aucune reprise du passé.** La table commence à la première passe qui suit la publication de ce code ; les verrous antérieurs n'ont pas de décision reconstituée après coup.
- **Pas l'état d'affichage ultérieur.** Plus tard dans la journée, `can_publish` peut répondre autrement pour la même édition (`STALE_ODDS` si les cotes vieillissent, `RACE_STARTED` après le départ). La table fige la décision **au verrou**.

## `gate_version` et l'empreinte des règles

`publication_gate.GATE_VERSION` est écrite avec chaque ligne. `GATE_FINGERPRINT` est l'empreinte des règles : constantes (`MIN_PRICED_RATIO`, `MAX_ODDS_AGE_MINUTES`, plancher 06h30, bornes de cotes, liste des motifs) et code des fonctions qui décident (`can_publish` et ses auxiliaires, `priced_ratio`, `all_default_odds`, `odds_age_minutes`, repli `minutes_to_start`), commentaires et docstrings exclus. Le test `test_empreinte_de_la_porte` (dans `tests/test_publication_gate.py`, joué par `tester_correctif_resultats.bat`) **échoue dès qu'une règle change sans nouvelle version**. Procédure : nouvelle `GATE_VERSION`, nouvelle `GATE_FINGERPRINT`, ligne datée annoncée avant publication, ligne ajoutée à l'historique dans `publication_gate.py`.

Engagement : tout changement de la porte est annoncé par ligne datée au moins jusqu'au **20/10/2026**, date du verdict de Bases, y compris après la lecture du pont du 06/10.

## Requêtes utiles

```sql
-- Part diffusable par jour et par horizon
SELECT substr(decided_at_utc, 1, 10) AS jour, horizon,
       COUNT(*) AS verrous, SUM(publishable) AS diffusables
FROM publication_decisions GROUP BY 1, 2 ORDER BY 1, 2;

-- Décision jointe à l'édition NVE qu'elle juge
SELECT d.*, p.odds_real, p.priced_ratio
FROM publication_decisions d
JOIN predictions p ON p.race_id = d.race_id AND p.horizon = d.horizon
                  AND p.engine_name = 'NEW_VALUE_ENGINE'
WHERE p.lock_time_utc = d.decided_at_utc;
```

Journalisation dans les passes : `PUBLICATION_DECISION {...}` à chaque ligne écrite ; `PUBLICATION_DECISION_ERROR {...}` si l'écriture échoue, sans jamais annuler le verrou.
