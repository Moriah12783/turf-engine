# Ombre du fondamental dans NVE : règle pré-enregistrée

**Statut : PROJET du 28/09/2026.** Il reprend les arbitrages du dev NVE sur
les sept points. La règle sera **gelée** au plus tard la veille du premier jour
d'ombre, dans un commit daté qui inscrira trois éléments : la date du gel, le
premier jour d'ombre (`DEBUT_OMBRE`) et la recette principale. Après le gel, la
règle ne change plus. Seule une coquille sans effet sur la règle peut encore
être corrigée, avec une justification dans le commit.

Rien ne tourne avant le 06/10. Le passage à l'ombre se fait sur la décision
écrite de Steph et avec le feu vert du dev NVE.

Deux verrous tiennent jusqu'au gel :

- **Aucun horaire.** Le workflow de nuit ne tourne pas automatiquement. Le
  commit de gel ajoute les deux horaires de nuit.
- **Aucune écriture.** Le calcul de nuit refuse d'écrire tant que `DEBUT_OMBRE`
  n'est pas inscrit (`OMBRE_PAS_OUVERTE`), même lancé à la main.

## Ce qui tourne

1. **Chaque nuit, calcul du fondamental** (`turf_lab/fondamental_nuit.py`,
   workflow `fondamental_nuit.yml` à 05h05 UTC, secours à 05h50).
   - Le calcul part du miroir historique Radar arrêté à la veille et du
     programme PMU du jour.
   - Il utilise le modèle « état du matin » : les 5 variables qui changent dans
     la journée sont retirées (changement de driver, taux et montes du jockey,
     œillères, œillères australiennes). Hyperparamètres et variables sont
     figés. Les coefficients sont réestimés chaque nuit sur l'historique arrêté
     à la veille.
   - Le résultat va dans la table `fundamental_probs(race_id, num, p,
     model_version, computed_at, train_until)` de `turf_bench.db`, entre le
     pull et le push R2. La clé est `(race_id, num, model_version)`, la somme
     des p vaut 1 par course, et le `race_id` est celui de `turf_bench.db`.
   - Aucune écriture après 06h20 UTC ni sur une course déjà verrouillée. Aucun
     envoi après 06h28 UTC.
   - Rien n'est écrit si l'audit de fuite est positif ou si l'historique a plus
     de 3 jours de retard. Ce jour-là n'a pas d'ombre et il est compté dans la
     couverture.
2. **daily_sync** (dev daily_sync) : une ligne porte ces probabilités sur les
   partants au verrou.
3. **NVE** (dev NVE) : l'ombre est calculée à chaque horizon et archivée dans
   les métadonnées de l'édition, sous la clé `ombre_fondamental` :
   `{recette, model_version, nve_version, train_until, probabilities}`. La
   sélection publiée ne change pas. Si la table est absente, rien ne change.
   - Un non-partant tardif entraîne une renormalisation sur les partants
     valides.
   - Il n'y a jamais d'ombre partielle : un partant sans p signifie pas d'ombre
     pour cette édition, et le cas est compté.

## Recettes

- **A, linéaire** : 0,90 × marché + 0,10 × fondamental. Le marché est la part
  de marché NVE (cotes du verrou, sans la marge). Le fondamental est renormalisé
  sur les partants valides.
- **B, log-linéaire** : p ∝ marché^a × fondamental^b. Les poids (a, b) sont
  réappris chaque lundi sur les seules éditions passées (logit conditionnel).
- **Recette principale.** Elle est choisie **une seule fois, avant le gel**,
  par le calcul de puissance du 28/09 (`BENTER_OMBRE_DECISION`) :
  - A si son effet est **détectable à 2 300 éditions** ;
  - sinon B.
  - « Détectable » veut dire une puissance d'au moins 80 % d'obtenir une borne
    basse de l'IC95 positive à 2 300 éditions. Le calcul part de l'effet estimé
    sur les éditions de production passées, avec l'erreur-type par réunion.
    Cette définition a été fixée avant le calcul.
  - L'autre recette peut être archivée à titre exploratoire. **Elle ne sert
    jamais à décider.**
  - Si B est retenue, son jeu d'apprentissage initial et ses horizons seront
    précisés ici avant le gel.

## Critère principal

- **Mesure** : écart de log-vraisemblance du gagnant (Δll), ombre moins édition
  NVE publiée, **au matin** (T_MATIN).
- **Éditions retenues** : celles à marché réel de la production gelée
  (`market_calibration.applied`, poids marché 0,90).
- **Probabilités** : celles archivées au verrou, sans renormalisation.
- **Gagnant** : arrivée définitive. En cas de dead-heat pour la première place,
  la course est exclue et comptée.
- **Intervalle** : apparié, par bootstrap de **réunions** entières (4 000
  tirages, graine fixe). Grouper par jour ne donnerait qu'environ 25 groupes à
  1 000 éditions, trop peu pour un intervalle stable.

## Lectures et décisions

- **Deux lectures seulement** : sur les **1 000** premières éditions, puis sur
  les **2 300** premières, dans l'ordre des verrous. **Aucune lecture
  intermédiaire.** Le script de lecture ne rend que le compteur avant 1 000.
  Une lecture rendue ne change plus.
- **Passage en production** (sur décision écrite de Steph) : si la borne basse
  est positive, avec l'**IC99 à 1 000 éditions** et l'**IC95 à 2 300**, et
  seulement si les huit critères secondaires sont remplis.
- **Arrêt pour inutilité** : si la borne haute de l'**IC95** est négative, à
  l'une ou l'autre lecture. Une règle d'inutilité ne crée pas de faux positif.
- **Sinon, à 1 000 éditions** : l'ombre continue jusqu'à 2 300 éditions.
- **Sinon, à 2 300 éditions** : c'est la fin sans preuve, et Steph décide par
  écrit.

## Critères secondaires

Il y a huit tests : **gagnant dans les 8** et **tiercé dans les 8**, à chacun
des quatre horizons (matin, T-90, T-30, T-15).

- Chaque test compare les 8 de l'ombre aux 8 de la sélection publiée, sur les
  courses de la lecture. Les 8 de l'ombre sont ses 8 plus fortes probabilités ;
  à égalité, on suit l'ordre de la sélection publiée.
- « Aucune dégradation » : la borne basse de l'IC95 apparié (bootstrap par
  réunion) de l'écart ombre − publié doit rester **≥ −2 points**.
- Les tests sont mesurés aux deux lectures, jamais ailleurs.
- **Un seul échec bloque le passage en production**, même si le critère
  principal est rempli. Un test non mesurable compte comme un échec.

## Couverture

La couverture est la part des éditions éligibles, avec arrivée définitive, qui
portent une ombre complète. Elle est journalisée. Si elle passe **sous 90 %**
au moment d'une lecture, la lecture est rendue mais **la décision est
suspendue** jusqu'à explication. Un trou non aléatoire, par exemple une réunion
entière manquante, biaiserait l'échantillon.

## Gel symétrique et compteur

Pendant l'ombre, **la production NVE est gelée elle aussi** : poids marché 0,90,
capteurs et sélection. La calibration du marché partiel attendra la lecture
finale. Elle ne concerne que des éditions non diffusées, donc aucun abonné n'y
perd.

Tout changement de code **remet le compteur à zéro**, que ce soit :

- côté fondamental, avec un nouveau `model_version`, empreinte du seul code du
  modèle ;
- côté NVE, avec un nouveau `nve_version`.

Le compteur repart de la première édition qui porte le couple courant. Chaque
ligne porte `train_until`, pour que tout calcul puisse être rejoué.

## Scellage

- Ce document est daté et committé avant le premier jour d'ombre.
- `turf_lab/ombre.py` et `turf_lab/ombre_lecture.py` reprennent les constantes
  ci-dessous. `tests/test_ombre_lecture.py` vérifie qu'elles sont identiques à
  ce tableau.
- Le dev NVE s'engage de même : ses inspections du mardi ne calculent aucune
  comparaison entre l'ombre et l'édition publiée avant les lectures prévues.
  La règle vaut aussi pour le labo.

## Constantes

| Constante | Valeur |
|---|---|
| POIDS_MARCHE | 0.90 |
| LECTURE_1 | 1000 |
| LECTURE_2 | 2300 |
| NIVEAU_LECTURE_1 | 0.99 |
| NIVEAU_LECTURE_2 | 0.95 |
| NIVEAU_INUTILITE | 0.95 |
| COUVERTURE_MIN | 0.90 |
| SEUIL_NON_DEGRADATION | -0.02 |
| HORIZONS_SECONDAIRES | T_MATIN, T90, T30, T15 |
| PUISSANCE_MIN | 0.80 |
| BOOTSTRAP_TIRAGES | 4000 |
| GRAINE | 20260928 |
| HEURE_LIMITE_UTC | 06:20 |
| RETARD_MAX_JOURS | 3 |
| META_OMBRE | ombre_fondamental |
| DEBUT_OMBRE | à inscrire au gel |
