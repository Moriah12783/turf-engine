# Recette A en production sur l'édition du matin

**Décision écrite de Steph du 13/11/2026**, après la lecture unique de l'ombre
(`docs/OMBRE_LECTURE.md`) : recette A en production sur l'édition du matin à
compter du 30/11/2026, calcul de nuit maintenu après le 24/11, règle d'arrêt :
retour au moteur actuel si, sur 30 jours de production, tout l'intervalle de
confiance du gain passe sous zéro.

Ce document est le contrat entre le moteur (dev NVE), la ligne du verrou (dev
Radar) et le lecteur de la règle d'arrêt (labo).

## Périmètre

- **Seule l'édition du matin (`T_MATIN`) change.** T-90, T-30 et T-15 gardent
  le calcul `nve-2026-10-07`, sorties identiques.
- **Recette A** : 0,90 × marché + 0,10 × fondamental de la nuit, à la place de
  0,90 × marché + 0,10 × modèle NVE. C'est le calcul mesuré du 08/10 au 11/11,
  par la même fonction (`NewValueEngine._recette_a`).
- **Code touché** : `turf_lab/engine.py`. La ligne du verrou
  (`turf_lab/fondamental_verrou.py`) porte une quatrième clé, le statut.
  `turf_lab/daily_sync.py` ne change pas.

## Deux constantes datées

Dans `turf_lab/engine.py`, comparées à la **date de la course**, jamais à
l'heure du calcul : aucune bascule en cours de journée, aucun déploiement de
nuit, fusion possible plusieurs jours avant la prise d'effet.

| Constante | Valeur | Sens |
|---|---|---|
| `RECETTE_A_DEBUT` | `2026-11-30` | premier jour de course en recette A |
| `RECETTE_A_FIN` | `None` | fin **exclue** : premier jour de course rendu au moteur actuel |

Date de course absente ou illisible : le moteur actuel s'applique.

## Ce que fait le moteur

Le moteur ne connaît pas l'horizon. Au verrou `T_MATIN`, et seulement là, la
ligne du verrou lui passe une copie des partants qui porte `fondamental_statut`
et, si le statut est `PORTE`, les trois clés du fondamental.

1. **Date hors service, ou aucune clé de la ligne** (T-90, T-30, T-15) :
   sortie `nve-2026-10-07`, champ pour champ. Avant le 30/11, l'archive
   `ombre_fondamental` continue d'être écrite comme pendant l'ombre.
2. **Date en service, édition du matin, recette calculable** : les
   probabilités de la recette A remplacent celles de NVE avant le classement.
   Sélection, bases, tocard, confiance et tickets en sortent par le code
   habituel. L'édition NVE est gardée en témoin.
3. **Date en service, édition du matin, recette non calculable** : l'édition
   est celle de NVE, marquée `REPLI_NVE` avec son motif. Jamais de recette
   partielle.

## Métadonnées d'une édition du matin, à partir du 30/11

| Champ de `metadata` | Recette A | Repli |
|---|---|---|
| `recette` | `A_lineaire_0.90_0.10` | `REPLI_NVE` |
| `motif_repli` | absent | voir ci-dessous |
| `nve_version` | `nve-2026-11-30` | `nve-2026-10-07` |
| `fondamental_model_version` | version portée | version portée si elle est unique, sinon `null` |
| `temoin_nve` | `{nve_version, probabilities, selection}` | absent |
| `market_calibration` | inchangé : `applied` vrai, `market_weight` 0,90 | inchangé |
| `ombre_fondamental` | absent | absent |

- `temoin_nve.probabilities` a les mêmes clés que `probabilities` ;
  `temoin_nve.selection` compte 10 chevaux (tout le champ s'il y en a moins),
  avec le départage de la production ; `temoin_nve.nve_version` vaut
  `nve-2026-10-07`. C'est exactement ce que NVE aurait publié.
- `model_probs` reste le modèle NVE pur : l'ingrédient du témoin.
- Aucun de ces champs n'apparaît hors du matin ni avant le 30/11.
- **Rapport par version** : lire `metadata.nve_version` de l'édition, pas la
  date. Une édition du matin en repli est une édition `nve-2026-10-07`.

### Motifs de repli

| `motif_repli` | Décidé par | Cas |
|---|---|---|
| `TABLE_ABSENTE`, `AUCUNE_LIGNE`, `ERREUR` | ligne du verrou | rien à porter pour la course |
| `VERSIONS_MULTIPLES`, `TRAIN_UNTIL_MULTIPLES` | ligne du verrou, ou moteur | plusieurs versions du modèle ou plusieurs dates d'apprentissage |
| `PARTANT_SANS_FONDAMENTAL` | moteur | un partant valide sans probabilité, ou probabilité nulle ou invalide |
| `MARCHE_ABSENT` | moteur | marché partiel neutralisé au verrou (`LOCK_PARTIAL_ODDS`), ou moins de 50 % de partants cotés |
| `STATUT_INCOHERENT` | moteur | statuts différents entre partants (défense, jamais observé) |
| `ERREUR_MOTEUR` | moteur | erreur inattendue dans le calcul de la recette : jamais remontée au verrou, `fondamental_model_version` vaut `null` |

## Une convention héritée de l'ombre

L'indice de value d'une édition en recette A est la probabilité publiée (4
décimales) × la cote, comme dans l'ombre lue le 12/11. Le calcul NVE multiplie
avant d'arrondir. L'écart est d'un centième au plus ; il garantit que la
sélection publiée est celle de l'ombre mesurée, départage compris.

## Témoin et confidentialité

Jusqu'à la lecture de la règle d'arrêt, le témoin n'est lu par aucun code et
n'atteint jamais le site : les exports publics ne reprennent de `metadata`
que `value_indices` et `smart_signals`. Deux tests le gardent
(`test_seul_le_moteur_cite_le_temoin`,
`test_le_site_public_ne_contient_jamais_le_temoin`). Le lecteur du labo
s'ajoute à la liste autorisée dans la PR qui le gèle. Le moteur n'écrit aucune
ligne de journal.

## Retour arrière (règle d'arrêt)

Une ligne, relue d'avance : `RECETTE_A_FIN = "AAAA-MM-JJ"`, le premier jour de
course rendu au moteur actuel (le lendemain de la lecture). À partir de ce
jour, les sorties sont celles de `nve-2026-10-07`, archive de l'ombre comprise.
Les éditions déjà verrouillées ne sont jamais recalculées.

## Tests

- `tests/fixtures/recette_a_reference.json` : 16 cas, figés avec le moteur du
  gel (sha256 `bf7193459ab3c82d…`) par
  `tests/fixtures/generer_reference_recette_a.py`, qui refuse tout autre moteur.
- **La référence ne dépend pas de la plateforme.** La somme des flottants a
  changé en Python 3.12, et la production tourne en Python 3.11 : un arrondi à
  4 décimales qui tombe sur la limite peut basculer d'une version à l'autre
  (vu à la relecture du labo du 16/11). Un cas n'est retenu que si le moteur
  du gel rend exactement la même sortie sous 15 variantes : trois sommes
  (Python courant, Python 3.11, exacte) et cinq exponentielles (décalées d'une
  unité du dernier chiffre). Le fichier est le même, octet pour octet, en
  Python 3.11, 3.12 et 3.13. `tests/fixtures/recette_a_cas_fragile.json` garde
  les entrées du cas écarté, pour vérifier que la garde le détecte.
- `tests/test_recette_a_production.py` : avant le 30/11, sorties identiques au
  gel ; à partir du 30/11, publié = ombre du gel et témoin = publié du gel ;
  les mêmes égalités, exactes, sous les 15 variantes de plateforme ; champs
  dérivés par le code habituel ; T-90 inchangé dans la passe qui pose
  `T_MATIN` ; un test par cas de repli, au moteur et sur la chaîne complète
  (table, ligne du verrou, moteur, base) ; marqueurs toujours cohérents ;
  retour arrière ; aucun journal.
- `tests/test_ombre_nve.py` (gel) : inchangé, au vert.
- À lancer avec le Python de la production : `python3.11 -m pytest -q`.
