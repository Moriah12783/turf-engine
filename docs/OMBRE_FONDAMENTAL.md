# Ombre du fondamental dans NVE : règle pré-enregistrée

**Statut : PROJET du 28/09/2026.** Il reprend :

- les arbitrages du dev NVE sur les sept points ;
- les décisions de Steph du 28/09 : ombre limitée à l'édition du matin, gel de
  NVE limité à 5 semaines ;
- les précisions du dev NVE du 28/09 sur ces deux points et sur les clés de
  l'archive ;
- les cinq corrections demandées par le dev NVE du 28/09 après sa relecture
  du lecteur scellé : courses à moins de 8 partants, horloge ancrée sur
  `DEBUT_OMBRE`, lecture le 36e jour, étiquette des dead-heats, clause sur la
  chaîne de verrouillage.

La règle sera **gelée** au plus tard la veille du premier jour d'ombre, dans un
commit daté qui inscrira la date du gel et le premier jour d'ombre
(`DEBUT_OMBRE`), et qui ajoutera les horaires de nuit. Après le gel, la règle
ne change plus. Seule une coquille sans effet sur la règle peut encore être
corrigée, avec une justification dans le commit.

L'ombre ne tourne pas avant le 06/10. Seule la répétition générale, sans
effet sur la production, tourne du 01 au 06/10. Le passage à l'ombre se fait
sur la décision écrite de Steph et avec le feu vert du dev NVE.

Deux verrous tiennent jusqu'au gel :

- **Aucun horaire.** Le workflow de nuit ne tourne pas automatiquement. Le
  commit de gel ajoute les deux horaires de nuit.
- **Aucune écriture.** Le calcul de nuit refuse d'écrire tant que `DEBUT_OMBRE`
  n'est pas inscrit (`OMBRE_PAS_OUVERTE`), même lancé à la main.

## Calendrier (accepté par Steph le 28/09)

| Étape | Date |
|---|---|
| Répétition générale | du jeudi 01/10 au mardi 06/10 |
| Publication du gel : règle, horaires de nuit, partie moteur | mercredi 07/10 |
| Premier jour d'ombre (`DEBUT_OMBRE`) | jeudi 08/10 |
| 35e jour | mercredi 11/11 |
| Lecture unique, au plus tard | jeudi 12/11 |

Au rythme observé (environ 29 éditions éligibles par jour), les 1 000 éditions
arrivent vers le 35e jour : la lecture tombe vers le 12/11 quel que soit le
critère atteint en premier.

## Répétition générale (01-06/10)

- **But** : faire tourner toute la chaîne avant le premier jour d'ombre, sans
  toucher à la production. Un bug trouvé après `DEBUT_OMBRE` remettrait
  l'horloge à zéro.
- **Nuit** : chaque matin à 05h05 UTC (secours à 05h50), lancé par l'horloge
  Cloudflare avec les horaires GitHub en filet, le workflow
  `repetition_ombre.yml` fait une copie en lecture seule de la base, y calcule
  le fondamental du jour et l'envoie sur la clé privée
  `lab/repetition/turf_bench.db`, jamais sur la base de production. Un
  lancement en retard (calcul fini après 06h20) ou un contrôle de PR n'envoie
  rien : la copie du matin reste en place (constaté le 02/10, horaire GitHub
  parti à 10h56).
- **Matin** : les devs NVE et daily_sync récupèrent la copie
  (`python -m turf_lab.repetition fetch`) et y enchaînent le verrou du matin et
  le moteur avec l'ombre. Ils lancent ensuite le diagnostic
  (`python -m turf_lab.repetition diagnostic --jour AAAA-MM-JJ`).
- **Confidentialité** : le diagnostic ne compare jamais l'ombre à l'édition
  publiée. Après le 06/10, l'outil refuse tout.
- **Critères de réussite, chaque jour** :
  - calcul fini avant 06h20 UTC et copie envoyée avant 06h28 ;
  - probabilités fondamentales d'une seule version, de somme 1 par course ;
  - toutes les éditions du matin éligibles portent une ombre complète, avec
    une seule clé et la recette A ;
  - aucun écart sur ce qui est publié (contrôle du dev NVE, décidé le
    01/10). Chaque jour, le verrou du matin est rejoué avec et sans ombre
    sur les mêmes réponses PMU : 0 empreinte différente pour NVE, ETPE et
    MARCHÉ, sur toutes les éditions du matin. La comparaison avec la
    production reste un contrôle complémentaire : toute différence
    d'empreinte doit s'y expliquer par une différence d'entrées (cotes,
    non-partants, déferrage).
- **Suites** : un écart est corrigé avant le gel, sans effet sur la règle. Si
  le retard de déclenchement des horaires GitHub menace la limite de 06h20, le
  commit de gel avance les horaires de nuit.

## Ce qui tourne

1. **Chaque nuit, calcul du fondamental** (`turf_lab/fondamental_nuit.py`,
   workflow `fondamental_nuit.yml` à 05h05 UTC, secours à 05h50).
   - Le lancement est fait par l'horloge Cloudflare (Worker `turf-horloge`,
     `docs/HORLOGE_CLOUDFLARE.md`), tenue à la minute. Les horaires GitHub,
     ajoutés au gel, servent de filet : ils ne sont pas garantis la nuit.
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
2. **daily_sync** (dev daily_sync) : la ligne ajoutée au verrouillage ne porte
   les probabilités fondamentales sur les partants **que lors du verrou
   `T_MATIN`**.
3. **NVE** (dev NVE) :
   - `engine.py` calcule l'ombre **seulement quand les partants portent ces
     probabilités**, donc sans aucune logique d'horizon dans le moteur ;
   - et **seulement si la calibration marché est appliquée** (marché réel). En
     marché partiel ou sans marché, il n'y a pas d'ombre, et l'édition n'est
     pas éligible ;
   - la sélection publiée ne change pas, et si la table est absente, rien ne
     change ;
   - un non-partant tardif entraîne une renormalisation sur les partants
     valides ;
   - il n'y a jamais d'ombre partielle : un partant sans p signifie pas d'ombre
     pour cette édition, et le cas est compté.

### Archive de l'ombre

Elle se trouve dans les métadonnées de l'édition du matin, sous la clé
`ombre_fondamental`, avec sept champs :

| Clé | Contenu |
|---|---|
| `recette` | la recette appliquée, `A_lineaire_0.90_0.10` |
| `model_version` | la version du modèle fondamental (empreinte de son code) |
| `nve_version` | la constante `NVE_VERSION` de `engine.py`, figée pendant l'ombre |
| `train_until` | la dernière journée d'historique du modèle |
| `probabilities` | les probabilités de l'ombre, arrondies à 4 décimales, avec les mêmes clés que `probabilities_json` |
| `selection` | les 10 chevaux de l'ombre (tous les partants s'il y en a moins), calculés par le même code et avec le même départage que la production |
| `fondamental` | les probabilités fondamentales renormalisées sur les partants valides (pour concevoir une recette B sans rien recalculer) |

**Confidentialité.** L'archive reste dans la base privée sur R2. Ni le banc, ni
le site, ni `benchmark_report.json` ne lisent `ombre_fondamental` : le rapport
public ne lit que deux champs des métadonnées (`value_indices` et
`smart_signals`), ce qui a été vérifié dans le code le 28/09. Rien de l'ombre
n'est donc visible publiquement avant la lecture.

**Lecteurs de la base sur R2 (lecture seule)**, recensés le 28/09 :

- **le labo** : seul le lecteur scellé lit l'archive, et il ne rend que le
  compteur avant la lecture ;
- **Bases** (`bases-engine`, jeton `bases-engine-ro`) : il voit l'archive, mais
  s'engage à ne jamais reproduire `ombre_fondamental` jusqu'à la lecture, ni
  dans ses exports, ni dans ses pages, ni dans ses journaux, ni dans ses
  fixtures. Toute fixture tirée d'une base postérieure au 08/10 est expurgée
  de cette clé ;
- **la répétition** (PC de Steph, jeton en lecture seule qui expire le
  08/10) : elle ne lit qu'une copie antérieure à l'ombre.

Tout nouveau lecteur est ajouté à cette liste, avec le même engagement, avant
d'accéder à la base. **Garde-fou automatique** : un test du dépôt
(`tests/test_ombre_confidentialite.py`) échoue si un autre code que le lecteur
scellé, l'outil de répétition et le moteur (qui écrit l'archive) cite la clé,
ou si le site public la contient.

## Recette

- **Recette principale : A, linéaire.** 0,90 × marché + 0,10 × fondamental. Le
  marché est la part de marché NVE (cotes du verrou, sans la marge).
- **Pourquoi A.** Elle a été choisie une seule fois, avant le gel, par le calcul
  de puissance du 28/09 (`BENTER_OMBRE_DECISION`). La règle, fixée avant le
  calcul, disait : A si son effet est détectable à 2 300 éditions (puissance
  d'au moins 80 % d'une borne basse IC95 positive), sinon B. Résultat :
  puissance de 99,7 %, donc A.
- **Recette B (log-linéaire).** Elle n'est jamais jugée par cette ombre. Une
  reprise avec B est une **nouvelle expérience pré-enregistrée**, jugée sur des
  éditions nouvelles. Les données de cette ombre peuvent servir à concevoir B,
  jamais à la juger.

## Périmètre : l'édition du matin

- **Seul horizon testé : `T_MATIN`.**
- Une éventuelle mise en production ne portera **que sur l'édition du matin**.
- T-90, T-30 et T-15 gardent NVE tel quel, jusqu'à un test dédié.

## Critère principal

- **Mesure** : écart de log-vraisemblance du gagnant (Δll), ombre moins édition
  NVE publiée, **au matin**.
- **Éditions retenues** : celles à marché réel de la production gelée
  (`market_calibration.applied`, poids marché 0,90).
- **Probabilités** : celles archivées au verrou, sans renormalisation.
- **Gagnant** : arrivée définitive. En cas de dead-heat pour la première place,
  la course est exclue, hors couverture, et comptée sous sa propre étiquette
  (`dead_heat_premiere_place`).
- **Petits champs** : une course à moins de 8 partants compte comme les
  autres. Sa sélection d'ombre contient tous ses partants.
- **Intervalle** : apparié, par bootstrap de **réunions** entières (4 000
  tirages, graine fixe).

## Lecture unique et décision

- **Une seule lecture**, sur :
  - les **1 000 premières** éditions éligibles portant l'ombre, dans l'ordre des
    verrous, au plus tôt le lendemain de la 1 000e ;
  - ou, si les 1 000 ne sont pas atteintes au bout de **35 jours**, toutes les
    éditions des 35 premiers jours, à partir du 36e jour.
- **Horloge.** Le jour 1 est `DEBUT_OMBRE`. Les nuits manquées des premiers
  jours comptent donc dans la couverture et n'allongent pas le gel. Après un
  correctif de bug bloquant seulement, l'horloge repart de la première
  édition de la version corrigée.
- **Aucune lecture intermédiaire.** Avant, le script de lecture ne rend que le
  compteur. Une lecture rendue ne change plus.
- **Archivage.** La lecture est lancée une seule fois, le jour prévu. Sa
  sortie complète est archivée dans la base privée sur R2, avec son empreinte
  (SHA-256 des courses retenues et de leurs écarts). Une relance ne sert qu'à
  vérifier l'empreinte.
- **Éditions retenues** : seulement celles qui portent les mêmes
  `model_version`, `nve_version` et recette.
- **Passage en production**, pour l'édition du matin seulement et sur décision
  écrite de Steph. Il faut trois conditions :
  - borne basse de l'**IC95** positive ;
  - les deux tests du matin passés ;
  - couverture d'au moins 90 %.
- **Arrêt pour inutilité** : borne haute de l'IC95 négative.
- **Sinon** : l'ombre se termine sans passage en production, et NVE est dégelé.
  Toute reprise est une nouvelle expérience.
- **Puissance attendue** (effet estimé le 28/09 sur les éditions passées :
  +0,0095 par course) : environ 87 % à 1 000 éditions, et 82 % à 875.

## Critères secondaires : les deux tests du matin

- **Gagnant dans les 8** et **tiercé dans les 8**, à `T_MATIN`.
- On compare les 8 premiers chevaux de la `selection` archivée de l'ombre aux 8
  premiers de la sélection publiée, sur les courses de la lecture.
- **Aucune dégradation** : la borne basse de l'IC95 apparié (bootstrap par
  réunion) de l'écart ombre − publié doit rester **≥ −2 points**.
- **Un seul échec bloque le passage en production.** Un test non mesurable
  compte comme un échec.

## Couverture

La couverture est la part des éditions éligibles, avec arrivée définitive, qui
portent une ombre complète, depuis le jour 1 de l'horloge. Elle est
journalisée. Si elle passe **sous 90 %**
au moment de la lecture, la lecture est rendue mais **la décision est
suspendue** jusqu'à explication. Un trou non aléatoire, par exemple une réunion
entière manquante, biaiserait l'échantillon.

## Gel symétrique, changements de code et durée

- **Gel symétrique.** Pendant l'ombre, **la production NVE est gelée elle
  aussi** : poids marché 0,90, capteurs, sélection.
- **Durée.** Le gel dure **5 semaines au plus** (décision de Steph du 28/09).
  Le prolonger demande l'accord écrit de Steph.
- **Remise à zéro : une seule, fixée d'avance** (décision de Steph). Un
  correctif de bug bloquant ne remet le compteur et l'horloge à zéro que
  s'il est le premier, et si la version corrigée démarre dans les
  **14 premiers jours** de l'ombre. Sinon, l'ombre se termine sans passage
  en production et NVE est dégelé ; toute reprise est une nouvelle
  expérience. Le gel de NVE dure donc au plus 14 + 35 jours, sans qu'aucune
  prolongation reste à décider pendant l'ombre.
- **Changements de code.** Pendant l'ombre, un changement de code n'est permis
  **que pour un bug bloquant**. Il remet à zéro **le compteur et l'horloge des
  35 jours**, que le changement vienne :
  - du fondamental, avec un nouveau `model_version` (empreinte du seul code du
    modèle) ;
  - ou de NVE, avec une nouvelle `NVE_VERSION`.
- **Code du modèle.** Le code couvert par `model_version` (chargement,
  variables, apprentissage, conversion du programme) est gelé comme NVE. Un
  ajout au fondamental, par exemple le déferrage, se fait avant le gel ou
  après la lecture.
- **Chaîne de verrouillage.** Pendant l'ombre, aucun changement de la chaîne
  qui produit l'édition du matin : daily_sync, verrou, filtres,
  enrichissement, stockage. Sont visés en particulier l'étape 2 de la
  migration R2, le filtre de fraîcheur et la ligne d'enrichissement. Ces
  changements sont faits avant le gel ou reportés après la lecture. Si l'un
  d'eux devient inévitable (bug bloquant), il est annoncé et daté dans cette
  règle, et `NVE_VERSION` change dans le même déploiement : c'est le dev
  NVE qui publie ce changement de version, avec le correctif du dev
  daily_sync. Le compteur et l'horloge repartent alors à zéro (dans la limite
  de la règle de remise à zéro), et on ne mesure jamais deux régimes
  mélangés.
- **Traçabilité.** Chaque ligne porte `train_until`, pour que tout calcul
  puisse être rejoué.

## Scellage

- Ce document est daté et committé avant le premier jour d'ombre.
- `turf_lab/ombre.py` et `turf_lab/ombre_lecture.py` reprennent les constantes
  ci-dessous. `tests/test_ombre_lecture.py` vérifie qu'elles sont identiques à
  ce tableau.
- Le dev NVE s'engage de même : ses inspections du mardi ne calculent aucune
  comparaison entre l'ombre et l'édition publiée avant la lecture. La règle
  vaut aussi pour le labo.

## Constantes

| Constante | Valeur |
|---|---|
| POIDS_MARCHE | 0.90 |
| RECETTE | A_lineaire_0.90_0.10 |
| LECTURE | 1000 |
| DUREE_MAX_JOURS | 35 |
| NIVEAU_LECTURE | 0.95 |
| NIVEAU_INUTILITE | 0.95 |
| COUVERTURE_MIN | 0.90 |
| SEUIL_NON_DEGRADATION | -0.02 |
| HORIZONS_SECONDAIRES | T_MATIN |
| REMISES_MAX | 1 |
| DELAI_REMISE_JOURS | 14 |
| BOOTSTRAP_TIRAGES | 4000 |
| GRAINE | 20260928 |
| HEURE_LIMITE_UTC | 06:20 |
| RETARD_MAX_JOURS | 3 |
| META_OMBRE | ombre_fondamental |
| DEBUT_OMBRE | à inscrire au gel |
