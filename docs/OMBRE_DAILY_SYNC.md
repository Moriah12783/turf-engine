# Ombre du fondamental : la part de daily_sync

**Livré le 28/09/2026 pour la répétition générale (01-06/10). Publication le
07/10, dans le même déploiement que la partie moteur du dev NVE.** Règle de
l'ombre : `docs/OMBRE_FONDAMENTAL.md`.

## 1. La ligne du verrou `T_MATIN`

`turf_lab/fondamental_verrou.py`, appelée depuis `DailySyncManager._lock_horizon`.

- **Quand** : au verrou `T_MATIN` seulement, après la porte de fraîcheur et
  l'enrichissement F2, juste avant les moteurs.
- **Quoi** : les partants **passés aux moteurs** portent leur probabilité
  fondamentale de la nuit, lue dans `fundamental_probs(race_id, num, p,
  model_version, computed_at, train_until)` pour le `race_id` de la course.
- **Contrat avec le moteur** : trois clés par partant.

| Clé | Contenu |
|---|---|
| `p_fondamental` (`CLE_P`) | `p` tel qu'écrit la nuit |
| `fondamental_model_version` (`CLE_MODEL_VERSION`) | `model_version` |
| `fondamental_train_until` (`CLE_TRAIN_UNTIL`) | `train_until` |

  Le moteur importe les constantes de `turf_lab.fondamental_verrou` : un
  changement de nom ne se fait qu'à un seul endroit.
- **Rien ne change** si la table est absente, s'il n'y a aucune ligne pour la
  course, ou si la course a plusieurs `model_version` ou plusieurs
  `train_until` : les moteurs reçoivent la même liste qu'avant.
- **Aucun filtre, aucun calcul** : un partant absent de la table ne porte
  rien. C'est le moteur qui décide (jamais d'ombre partielle, marché réel
  exigé, renormalisation sur les partants valides).
- **Copie, jamais les partants d'origine.** La même liste sert aux horizons
  suivants de la même passe (T90 peut être dû dans la passe qui pose
  T_MATIN) et au pont RADAR_V4. Le moteur n'ayant aucune logique d'horizon,
  une probabilité laissée sur eux ferait calculer une ombre hors du matin.
- **Jamais bloquant** : une erreur de lecture est journalisée et le verrou
  est posé normalement.
- **Sortie publiée inchangée** avec le moteur actuel (vérifié : les clés
  supplémentaires ne modifient pas la prédiction NVE).

### Journal

Une ligne par verrou `T_MATIN` :
`FONDAMENTAL_VERROU {"race_id", "horizon", "statut", ...}`, avec `statut` parmi
`PORTE`, `TABLE_ABSENTE`, `AUCUNE_LIGNE`, `VERSIONS_MULTIPLES`,
`TRAIN_UNTIL_MULTIPLES`, `ERREUR`. Pour `PORTE` : `model_version`,
`train_until`, `partants`, `portes`, `sans_p`, `lignes`.

**Aucune probabilité n'est jamais journalisée** : les journaux GitHub Actions
d'un dépôt public sont lisibles par tous, et rien de l'ombre ne doit être
visible avant la lecture.

## 2. Déferrage : les valeurs mixtes

`shoeing_code()` dans `turf_lab/daily_sync.py`, même règle que le labo
(`turf_lab/deferre_lab.code`, test d'égalité) : les pieds déferrés priment.

| Valeur du flux | Avant le 07/10 | À partir du 07/10 |
|---|---|---|
| `PROTEGE_ANTERIEURS_DEFERRRE_POSTERIEURS` (sic) | FERRE | **DP** |
| `DEFERRE_ANTERIEURS_PROTEGE_POSTERIEURS` | FERRE | **DA** |
| `DEFERRE_ANTERIEURS_POSTERIEURS` | D4 | D4 |
| `DEFERRE_ANTERIEURS`, `DEFERRE_POSTERIEURS` | DA, DP | DA, DP |
| protection seule (`PROTEGE_…`), absent, autre | FERRE | FERRE |

Seule différence avec le labo, voulue et confirmée par le dev NVE le 28/09 :
la protection seule reste `FERRE` en production (le labo la code `PROTEGE`).
Le correctif ne change ainsi que les valeurs mixtes.

**Effet sur NVE.** Le score de ferrure d'un partant concerné passe de 35
(ferré) à 75 (DP) ou 70 (DA) : les sorties NVE des courses de trot qui en
comptent changent. Échantillon du 27/09, 20 courses de trot : 29 partants
sur 257 (11 %), dont 28 en `PROTEGE_ANTERIEURS_DEFERRRE_POSTERIEURS`. Le
changement entre dans la `NVE_VERSION` publiée le 07/10.

**Journal** : `DEFERRE_MIXTE {"race_id", "partants"}` pour chaque course qui a
au moins une valeur mixte.

## Tests

`tests/test_ombre_daily_sync.py` : 15 tests (11 pour la ligne du verrou,
4 pour le déferrage), ajoutés en 10/10 à `tester_correctif_resultats.bat`.
