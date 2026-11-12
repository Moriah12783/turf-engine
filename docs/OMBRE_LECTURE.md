# Lecture unique de l'ombre du fondamental

Lecture scellée du 12/11/2026 à 13h08 UTC. C'est le 36e jour, date limite de la
règle gelée le 07/10 (`docs/OMBRE_FONDAMENTAL.md`, commit `d8cc8ec4`). Le dépôt
est public : ce document ne donne que des agrégats et le verdict, sans aucune
probabilité ni aucun écart course par course.

## Verdict

**Décision de la règle gelée : `PASSAGE_EN_PRODUCTION_DU_MATIN_SUR_DECISION_ECRITE_DE_STEPH`.**

Les trois conditions de la règle sont réunies :

| Condition | Seuil | Résultat |
|---|---|---|
| Borne basse de l'IC95 du critère principal | > 0 | **+0,00126** : oui |
| Tests du matin (gagnant et tiercé dans les 8) | borne basse ≥ −2 points | **les deux passés** |
| Couverture | ≥ 90 % | **100 %** |

Le passage en production ne porte que sur l'édition du matin, et seulement sur
décision écrite de Steph. **Rien ne change dans la production avant cette
décision.** T-90, T-30 et T-15 gardent NVE tel quel.

## 1. Critère principal

Écart de log-vraisemblance du gagnant (Δll), ombre (recette A : 0,90 marché +
0,10 fondamental) moins édition NVE publiée, au matin (`T_MATIN`).

| Mesure | Valeur |
|---|---|
| Éditions | 800, réparties sur 149 réunions, du 08/10 au 11/11 |
| Mode de lecture | `35_jours` (le seuil de 1 000 éditions n'est pas atteint) |
| Δll moyen par édition | **+0,00706** |
| IC95, bootstrap par réunion (4 000 tirages, graine 20260928) | **[+0,00126 ; +0,01326]** |
| Borne basse > 0 ? | **Oui** |
| Arrêt pour inutilité (borne haute de l'IC95 < 0) ? | Non |
| Effet estimé avant le gel (28/09) | +0,0095 par course, compris dans l'IC95 |

En moyenne géométrique, l'ombre donne au gagnant une probabilité environ 0,7 %
plus élevée que l'édition publiée (e^0,00706 ≈ 1,0071).

## 2. Les deux tests du matin

Les 8 premiers chevaux de la sélection archivée de l'ombre face aux 8 premiers
de la sélection publiée. IC95 apparié, bootstrap par réunion.

| Test | Éditions | Publié | Ombre | Écart | IC95 de l'écart | Seuil (borne basse) | Verdict |
|---|---|---|---|---|---|---|---|
| Gagnant dans les 8 | 800 | 86,75 % | 87,75 % | **+1,00 point** | [+0,11 ; +1,97] points | ≥ −2 points | **OK** |
| Tiercé dans les 8 | 799 | 59,32 % | 59,32 % | **0,00 point** | [−1,48 ; +1,46] points | ≥ −2 points | **OK** |

Le test du tiercé porte sur 799 éditions : une course de la lecture n'a pas de
tiercé officiel à l'arrivée.

## 3. Couverture

| Étape | Éditions |
|---|---|
| Éditions du matin éligibles au compteur, du 08/10 au 11/11 | 811 |
| Exclues : sans arrivée définitive (9 courses de la réunion de Clairefontaine annulée le 23/10, Nancy R3C8 du 11/10) | −10 |
| Exclue : dead-heat à la première place | −1 |
| **Éditions éligibles avec arrivée définitive** | **800** |
| Exclues pour ombre incomplète, clé ou recette différente, gagnant hors des partants au verrou | 0 |
| **Éditions avec une ombre complète de la clé gelée** | **800** |
| **Couverture** (seuil : 90 %) | **100 %** |

Clé gelée, identique sur toute la période : `fond-matin-e570d663793c`,
`nve-2026-10-07`, `A_lineaire_0.90_0.10`. Aucune remise à zéro.

## 4. Effet du passage à l'heure d'hiver (25/10)

Le plancher du verrou du matin est resté à 06h30 UTC pendant toute l'ombre, soit 08h30 à
Paris jusqu'au 24/10 et 07h30 à partir du 25/10, avec moins de cotes ouvertes.

| Période | Heure du verrou à Paris | Jours | Éditions retenues | Par jour |
|---|---|---|---|---|
| 08/10 au 24/10 | 08h30 | 17 | 479 | 28,2 |
| 25/10 au 11/11 | 07h30 | 18 | 321 | 17,8 |
| **Total** | | **35** | **800** | **22,9** |

Les 10 courses sans arrivée définitive sont antérieures au 25/10. Le dead-heat
est postérieur au 27/10 (il n'apparaît pas au compteur du 28/10).

**Largeur de l'intervalle.**
- Observée : 0,01200.
- Pour 1 000 éditions, à variance par édition égale, on aurait eu environ
  0,01073 (0,01200 × √(800/1000)).
- L'intervalle observé est donc environ 12 % plus large que prévu. Ce chiffre
  est une estimation : le bootstrap par réunion ne suit pas exactement la
  racine du nombre d'éditions.

La puissance enregistrée avant le gel était de 82 % à 875 éditions ; elle est
un peu plus faible à 800, et l'effet a été détecté. Le lecteur gelé ne découpe
pas le critère par période : aucun résultat par période n'est publié, car ce
serait une analyse hors de la règle.

## 5. Traçabilité

- **Exécution.** Workflow « Lecture unique de l'ombre », run `43224310651`,
  lancé à la main (`workflow_dispatch`) sur `main`, le 12/11/2026 à 13h07 UTC.
  La ligne `OMBRE_LECTURE` est produite à 13h08m06s UTC.
- **Commit lu.** `6c8bcbc2d7d3ee466f3bf784133836ab013beeb8`, la fusion de la
  PR #37.
- **Lecteur inchangé depuis le gel.** Les sha256 ont été vérifiés dans le run
  (`sha256sum -c` : OK) et sont identiques au gel `d8cc8ec4` du 07/10 :
  - `turf_lab/ombre_lecture.py` : `5c3b379b903378ac471c5e66562a51eeac23697d64b8ac94fe9f49dfee58fa61`
  - `turf_lab/ombre.py` : `79fee2b28ba07e1577a705086ae3866b3158cee0bf5813b3ad06dc74785fb5b7`
  - `turf_lab/r2_store.py` : `4f3d5c4471dca1a15119bd15e65479d8cacb64b9eda272206d59c5edeaeb9a80`
- **Exécution unique.** Avant la lecture, le run a vérifié qu'aucune
  exécution réussie de ce workflow n'existait et que ce document n'existait
  pas. C'est la seule exécution réussie. Ce document étant désormais publié,
  tout nouveau lancement est refusé.
- **Base lue.** Une copie en lecture seule du banc, jamais renvoyée sur R2,
  de sha256 `77164a01338d29bec16e561fc25d5a58fb2df1a7cd255f4395c7a5c44e75d0bf`.
- **Empreinte des courses retenues et de leurs écarts.**
  `d01e75187fc7367c293312a0d80c03c5d733f185b0c09ff56fb74d4b927304b1`. Selon
  la règle, une relance ne sert qu'à vérifier cette empreinte.
