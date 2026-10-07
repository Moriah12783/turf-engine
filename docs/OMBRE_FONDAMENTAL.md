# Ombre du fondamental dans NVE : règle pré-enregistrée

**Statut : GELÉE le 07/10/2026.** Premier jour d'ombre : jeudi 08/10/2026.

| Élément gelé | Valeur |
|---|---|
| Premier jour d'ombre (`DEBUT_OMBRE`) | 2026-10-08 |
| Modèle fondamental (`model_version`) | `fond-matin-e570d663793c` |
| Moteur (`NVE_VERSION`) | `nve-2026-10-07` |
| Recette | `A_lineaire_0.90_0.10` |
| Déploiement de la partie moteur et de la partie daily_sync | commit `bab71ce0e5d0` (PR #22), mardi 06/10/2026 à 22h39 UTC |
| Horloge Cloudflare (`turf-horloge`) | version `7853694e` |

La règle reprend le projet du 28/09/2026, c'est-à-dire :

- les arbitrages du dev NVE sur les sept points ;
- les décisions de Steph du 28/09 : ombre limitée à l'édition du matin, gel de
  NVE limité à 5 semaines ;
- les précisions du dev NVE du 28/09 sur ces deux points et sur les clés de
  l'archive ;
- les cinq corrections demandées par le dev NVE du 28/09 après sa relecture
  du lecteur scellé : courses à moins de 8 partants, horloge ancrée sur
  `DEBUT_OMBRE`, lecture le 36e jour, étiquette des dead-heats, clause sur la
  chaîne de verrouillage ;
- le critère « avec et sans ombre » de la répétition (PR #20, 01/10).

Le commit de gel inscrit la date du gel et le premier jour d'ombre
(`DEBUT_OMBRE`), et ajoute les horaires de nuit. Après le gel, la règle ne
change plus. Seule une coquille sans effet sur la règle peut encore être
corrigée, avec une justification dans le commit.

Le gel est publié sur la décision écrite de Steph, avec le feu vert du dev
NVE, après la répétition générale du 01 au 06/10 (6 jours sur 6 validés, voir
l'annexe).

Les deux verrous d'avant le gel sont levés par ce commit :

- **Horaires de nuit.** L'horloge Cloudflare lance le calcul de nuit à 05h05
  et 05h50 UTC, du 08/10 au 24/11. Les horaires GitHub, sur les mêmes jours,
  servent de filet.
- **Écriture.** Le calcul de nuit écrit à partir de `DEBUT_OMBRE`. Avant
  cette date, il refuse toujours (`OMBRE_PAS_OUVERTE`).

## Calendrier (accepté par Steph le 28/09)

| Étape | Date |
|---|---|
| Répétition générale | du jeudi 01/10 au mardi 06/10 (6 jours sur 6 validés) |
| Déploiement de `nve-2026-10-07` : partie moteur et partie daily_sync | mardi 06/10, 22h39 UTC |
| Journée de rodage, sans ombre | mercredi 07/10 |
| Gel : règle et horaires de nuit | mercredi 07/10 |
| Premier jour d'ombre (`DEBUT_OMBRE`) | jeudi 08/10 |
| 35e jour | mercredi 11/11 |
| Lecture unique, au plus tard | jeudi 12/11 |

Au rythme observé (environ 29 éditions éligibles par jour), les 1 000 éditions
arrivent vers le 35e jour : la lecture tombe vers le 12/11 quel que soit le
critère atteint en premier.

## Répétition générale (01-06/10, terminée : voir l'annexe)

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
- **Matin** : les devs NVE et Radar récupèrent la copie
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
     ajoutés au gel sur les mêmes jours (du 08/10 au 24/11), servent de
     filet : ils ne sont pas garantis la nuit. Un horaire GitHub parti après
     06h20 UTC ne calcule rien et n'envoie rien (`FILET_HORS_DELAI`).
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
2. **daily_sync** (dev Radar) : la ligne ajoutée au verrouillage ne porte
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
  Radar. Le compteur et l'horloge repartent alors à zéro (dans la limite
  de la règle de remise à zéro), et on ne mesure jamais deux régimes
  mélangés.
- **Traçabilité.** Chaque ligne porte `train_until`, pour que tout calcul
  puisse être rejoué.

## Scellage

- Ce document est daté et committé avant le premier jour d'ombre : gel du
  07/10/2026.
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
| DEBUT_OMBRE | 2026-10-08 |

## Code déployé

Le code publié le 06/10 (commit `bab71ce0e5d0`, PR #22) est identique, octet
pour octet, à celui de la répétition générale. Le labo l'a vérifié par
empreinte SHA-256, sur la branche de la PR puis sur `main` :

| Fichier | SHA-256 |
|---|---|
| `turf_lab/engine.py` | `bf7193459ab3c82d0ef9ee6b8082e881f5ec423065bfcbdcc388d266c5c9b8ba` |
| `turf_lab/daily_sync.py` | `f7e3fc3b170f0209a3aa28a6b2de61653f8ec54ed9c35e3504cf59f4002e5395` |
| `turf_lab/fondamental_verrou.py` | `e5e0f68f7deaee61f343ba7c12c42e5c20635f4bbd962a7f06cb76dd9b2ccfd7` |

Pendant l'ombre, toute empreinte différente sur `main` est un changement de
code, soumis à la règle des changements de code ci-dessus.

## Annexe : répétition générale du 01 au 06/10

Synthèse du tableau de suivi du labo, en agrégats seulement. Chaque jour est
compté à son lancement de référence sur le PC de Steph : celui du matin, et à
partir du 02/10 la tâche automatique de 09h00 UTC.

| Jour | Calcul fini / copie envoyée (UTC) | Courses calculées la nuit | Éditions du matin éligibles avec ombre complète | Différences avec / sans ombre (NVE, ETPE, MARCHÉ) |
|---|---|---|---|---|
| jeu. 01/10 | 05h06 / 05h06 | 23 | 23 sur 23 | 0 sur 23 éditions |
| ven. 02/10 | 05h07 / 05h07 | 48 | 38 sur 38 | 0 sur 38 éditions |
| sam. 03/10 | 05h06 / 05h07 | 49 | 31 sur 31 | 0 sur 33 éditions |
| dim. 04/10 | 05h07 / 05h07 | 51 | 19 sur 19 | 0 sur 20 éditions |
| lun. 05/10 | 05h07 / 05h07 | 33 | 25 sur 25 | 0 sur 25 éditions |
| mar. 06/10 | 05h06 / 05h06 | 24 | 24 sur 24 | 0 sur 24 éditions |
| **Total** | **toujours avant 06h20 / 06h28** | **228** | **160 sur 160** | **0 sur 163 éditions** |

- **Fondamental.** Chaque jour, une seule version
  (`fond-matin-e570d663793c`), avec un historique à jour et une somme de 1
  par course (écart maximal 0).
- **Archive.** Une seule clé, la recette A. L'écart à la recette est resté
  sous 1e-4, pour une tolérance du contrôle de 3e-4.
- **Production.** Toutes les différences d'empreinte avec la production
  s'expliquent par les cotes : 0 édition absente, 0 anomalie.
- **Édition du matin posée tard.** Ce cas se produit quand l'édition du matin
  part avec le premier horizon de la journée. Il a été rejoué le 05/10 à
  18h32 UTC : 2 éditions sur 2 avec une ombre complète, 0 différence avec et
  sans ombre.
- **02/10.** Le lancement de 06h32 donnait aussi 39 éditions sur 39 et
  0 différence. Le total retient celui de 09h01.

Défauts trouvés et corrigés avant le gel, sans effet sur la règle :

1. **Horaire GitHub en retard.** Le 02/10, un horaire GitHub parti avec plus
   de 6 h de retard a écrasé la copie du matin. Correctif : PR #21, qui
   n'envoie rien hors délai ni depuis un contrôle de PR (`SANS_ENVOI_HORS_DELAI`,
   `SANS_ENVOI_CONTROLE`). Le même principe s'applique au filet GitHub du calcul
   de nuit (`FILET_HORS_DELAI`).
2. **En-tête de `turf_lab/fondamental_verrou.py`.** Le paquet du dev Radar ne
   portait pas la date de la version de la répétition (« 29/09 »). Le paquet
   a été réaligné sur cette version.
3. **Patch du dev Radar.** Il n'était pas aligné sur cette même version. Il a
   été régénéré, puis vérifié par empreinte.

## Journée de rodage du 07/10

Premier matin produit par `nve-2026-10-07`, sans ombre :

- 24 courses, avec 24 éditions du matin posées à 06h31 UTC ;
- `FONDAMENTAL_VERROU` au statut `TABLE_ABSENTE` sur les 24, comme prévu ;
- le correctif du déferrage est actif : `DEFERRE_MIXTE` sur 6 courses de trot ;
- rien de l'ombre n'apparaît dans l'archive publique du site.
