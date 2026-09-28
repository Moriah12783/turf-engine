# Labo Benter : étape 1 (fondamental) et étape 2 (combinaison)

**Question posée.** Un modèle qui ne regarde **jamais les cotes** apporte-t-il
une information que le marché PMU n'a pas déjà ? C'est le test fondateur de la
méthode Benter : un modèle fondamental seul est toujours moins bon que le
marché ; sa valeur se mesure une fois **combiné** au marché.

| | |
|---|---|
| Code | `turf_lab/benter_lab.py`, tests `tests/test_benter_lab.py` |
| Lancement | GitHub Actions → « Labo Benter » → *Run workflow* (et automatiquement sur une PR qui modifie le labo) |
| Données | miroir privé `history/turf_history.db` (voir `docs/HISTORIQUE_RADAR.md`), vue `courses_courues` |
| Résultat | rapport complet sur R2 privé : `lab/benter/dernier.json` (+ une copie datée par exécution) |
| Production | **aucun effet** : ni base de production, ni site, ni requête au Radar |

## Méthode

1. **Courses** : la vue `courses_courues` (règle officielle du Radar),
   partants seulement (`statut = 'PARTANT'`). Dead-heat ou gagnant introuvable :
   course écartée (comptée dans `BENTER_DONNEES`).
2. **30 variables d'avant-course**, sans aucune cote :
   - programme PMU : taux de victoires et de places lissés, nombre de
     courses, gains (carrière, année, par course), âge, sexe, œillères,
     changement de driver, corde relative, poids relatif (galop) ;
   - musique (6 dernières sorties) : place moyenne, victoires, podiums,
     fautes, dernière place ;
   - historique du miroir : jours de repos, première course vue, dernière
     place ; taux de victoires lissés du jockey/driver et de l'entraîneur.
   Les statistiques du miroir sont **figées à la veille au soir** : un
   pronostic du matin ne connaît pas les courses du jour.
3. **Audit de fuite** (`BENTER_AUDIT_FUITE`), vérifié le 28/09/2026 : le
   programme PMU est figé avant la course (un gagnant a +1,02 victoire à sa
   course suivante ; si le programme incluait la victoire du jour, ce serait
   0). Si une fuite est détectée un jour, les variables de programme sont
   retirées automatiquement.
4. **Logit conditionnel** (Newton, pénalité L2), un jeu de coefficients pour
   le **trot** et un pour le **galop**.
5. **Walk-forward mensuel** à partir de janvier 2026 : chaque mois est prédit
   par un modèle entraîné sur les mois précédents uniquement (apprentissage
   depuis le 15/08/2025).
6. **Combinaison Benter** : `p ∝ exp(α·log q_marché + β·log p_fondamental)`,
   α et β appris sur les mois de test **précédents**, jamais sur le mois jugé.

## Comment lire le résultat

- **Juge principal** : `delta_ll_par_course_vs_marche_recalibre`, le gain de
  log-vraisemblance par course de la combinaison face au marché **recalibré**
  (`q^γ`, qui corrige déjà le biais favori/outsider). Le gain mesuré est donc
  bien celui du fondamental, pas une simple correction de ce biais.
- **IC 95 %** par bootstrap sur les courses : l'avantage n'est **démontré**
  que si la borne basse est positive (`avantage_demontre`).
- **G1** : au moins 3 000 courses jugées (`G1_courses_suffisantes`).
- Le marché de référence est la **cote de clôture** `cote_direct` finale :
  vérifié le 28/09/2026 sur 3 057 gagnants, elle vaut exactement le rapport
  simple gagnant payé (médiane rapport / cote = 1,000). **`cote_reference`
  n'est pas la clôture** (médiane 0,905, de 0,51 à 1,51) : c'est une cote de
  référence plus ancienne, gardée comme second repère
  (`combinaison_marche_reference`). Le premier passage du labo (PR #8)
  l'avait prise à tort pour la clôture ; corrigé dans la PR suivante.
- `ll_fondamental` < `ll_marche` est **normal** : Benter l'observait aussi.
  Ce qui compte, c'est `beta_fondamental` > 0 et le delta.

## Étape 3 simulée : Kelly fractionné (`BENTER_KELLY`)

- On mise sur chaque cheval dont l'espérance `p_combinée × cote − 1` dépasse
  **+5 %**, au **quart de Kelly**, sans dépasser **5 % de la bankroll par
  course**. Le gain est réglé au **rapport officiel** simple gagnant (à
  défaut, à la cote finale, qui lui est égale en médiane).
- Deux lectures : **ROI à mise fixe** (1 par pari, avec son IC 95 % par
  bootstrap sur les courses) et **bankroll Kelly** (bankroll finale,
  drawdown maximal), plus le ROI par mois et par tranche de cote.
- **Témoin** (`BENTER_KELLY_TEMOIN`) : la même stratégie sur le marché seul
  recalibré. Sur un marché calibré, elle ne trouve presque aucun pari ; si
  elle en trouvait beaucoup, la simulation serait suspecte.
- **Borne haute** : la décision utilise la cote finale, qu'on ne connaît
  qu'au départ en pari mutuel, et ignore l'effet de nos propres mises sur la
  cote. Le chiffre qui compte viendra de l'étape suivante : décider à T-30 ou
  T-15 avec les cotes de ce moment, régler au rapport final.

## Horizon réel de pari : T-30, T-15, T-5 (`BENTER_TX`)

En pari mutuel, on mise **avant** le départ mais on est payé à la cote
**finale**. Le test réaliste :

- cotes de la dernière photo `cotes_snapshots` prise **au moins H minutes**
  avant le départ, et au plus H+10, **cheval par cheval** (les partants
  d'une même capture ne tombent pas toujours sur la même minute) ; il faut
  une cote pour chaque partant ;
- combinaison `p ∝ exp(α·log q_T + β·log p_fondamental)` apprise **semaine
  par semaine** sur les semaines précédentes (au moins 400 courses) : les
  photos n'existent que depuis le 15/07/2026 ;
- juge : gain de log-vraisemblance face au marché de T-x recalibré, avec IC
  95 % ; `gain_ll_cloture_vs_T` mesure l'information qui arrive entre T-x et
  le départ ;
- Kelly **décidé aux cotes de T-x**, **réglé au rapport final** ; témoin sur
  le marché de T-x seul.

## Apport au produit : les moteurs publiés + le fondamental (`BENTER_BANC`)

Question : le fondamental améliore-t-il les probabilités que **nos moteurs
publient** (base du banc `turf_bench.db`, lue sur R2 en lecture seule) à
l'**édition du matin** (`T_MATIN`) et à **T-30** (l'horizon envoyé aux
abonnés) ?

- Pour chaque moteur (`MARKET_BASELINE`, `NEW_VALUE_ENGINE`,
  `ETPE_ENGINE`, `RADAR_V4`) et chaque horizon : combinaison
  `p ∝ exp(a·log p_moteur + b·log p_fondamental)` apprise semaine par
  semaine sur les semaines précédentes (au moins 300 courses).
- Juge : gain de log-vraisemblance face au moteur seul recalibré, avec IC
  95 % ; taux de gagnant en tête (moteur contre combinaison).
- Un gain démontré ici se traduit directement en pronostics plus justes
  pour les abonnés : c'est la voie « produit » décidée le 28/09. Intégration
  dans le moteur de production après le 06/10, avec le dev concerné.

## Résultats du 28/09/2026 (miroir au 27/09, 15 282 courses)

| Test | Courses | Gain ll/course vs marché recalibré (IC 95 %) | β fondamental | Mises (ROI mise fixe, IC 95 %) |
|---|---:|---|---:|---|
| Clôture (`cote_direct`) | 7 971 | **−0,00015** [−0,00036 ; +0,00005] | ≈ 0 | 65 paris, −4,8 % [−36 % ; +33 %] |
| Cote de référence (plus ancienne) | 6 940 | **+0,0045** [+0,0023 ; +0,0067] | > 0 | — |
| T-30 | 390 | +0,0067 [−0,012 ; +0,025] | 0,32 | 279 paris, −17 % [−41 % ; +12 %] |
| T-15 | 455 | −0,0006 [−0,012 ; +0,010] | 0,23 | 169 paris, −15 % [−63 % ; +54 %] |
| T-5 | 492 | +0,0053 [−0,008 ; +0,018] | 0,28 | 219 paris, **−33 %** [−59 % ; −1 %] |

Lecture :

- **Le marché de clôture du PMU est très efficace** (pseudo-R² ≈ 0,21, contre
  0,12 pour le fondamental). Nos 30 variables publiques n'y ajoutent rien.
- **Le marché apprend beaucoup dans les dernières minutes** : la clôture
  gagne +0,11 de log-vraisemblance par course sur T-30 et +0,08 sur T-5.
  Le fondamental apporte de l'information aux cotes anciennes (référence,
  T-30 : β ≈ 0,3), mais cette information arrive dans le marché avant le
  départ.
- **Parier à T-x sur nos « value » perd** (−15 % à −33 %) : le prélèvement
  (≈ 16 %) et les mouvements tardifs, qui vont contre nos paris, l'emportent.
- Le premier passage (PR #8) affichait +0,020 : c'était un artefact
  (`cote_reference` prise à tort pour la clôture).
- Photos de cotes : environ 900 courses par horizon seulement (captures
  Radar espacées de 5 à 15 min) ; échantillons T-x encore petits.

### Apport au produit (moteurs du banc, 28/09/2026)

| Moteur × horizon | Courses jugées | Gain ll/course (IC 95 %) | Gagnant en tête : moteur → combiné |
|---|---:|---|---|
| NEW_VALUE_ENGINE, matin | 718 | **+0,070** [+0,036 ; +0,104] | **23,8 % → 27,0 %** |
| MARKET_BASELINE, matin | 704 | **+0,086** [+0,058 ; +0,115] | 21,7 % → 27,3 % |
| MARKET_BASELINE, T-30 | 715 | +0,014 [+0,0004 ; +0,027] | 26,3 % → 28,3 % |
| NEW_VALUE_ENGINE, T-30 | 718 | +0,008 [−0,015 ; +0,032] | 26,6 % → 28,6 % |
| RADAR_V4 (matin = T-30) | 197 | −0,011 [−0,034 ; +0,012] | 27,9 % → 27,9 % |

ETPE_ENGINE ne publie que des sélections (pas de probabilités) : non mesuré.

- **L'édition du matin est la vraie cible produit** : le fondamental y ajoute
  une information que le marché du matin n'a pas encore (gain démontré sur
  NVE et sur la base marché ; +3,2 points de gagnants en tête pour NVE).
- À T-30, le marché a déjà rattrapé l'essentiel : gain faible (démontré de
  justesse pour la base marché, pas pour NVE).
- RADAR_V4 : aucun apport mesurable (échantillon plus court).

### Découpages demandés par le dev NVE (28/09/2026)

Gain apparié de gagnants en tête (même course, combiné − moteur) et IC 95 %.
Éditions NVE de production (`market_calibration.applied` et poids 0,9) :
combinaison apprise sur toutes les éditions NVE passées, jugée sur les seules
éditions de production (345 au matin : trop récentes pour 300 courses
d'apprentissage).

| Ligne | Jugées | Gain ll (IC 95 %) | Gain gagnant en tête (IC 95 %) |
|---|---:|---|---|
| NVE matin, toutes (37 % sans marché, 33 % poids 0,70, 30 % poids 0,90) | 718 | **+0,070** [+0,036 ; +0,104] | +3,2 pts [+0,1 ; +6,4] |
| **NVE matin, production poids 0,9** | 345 | +0,036 [−0,010 ; +0,082] | +2,9 pts [−1,4 ; +7,0] |
| NVE T90, production poids 0,9 | 385 | −0,011 [−0,040 ; +0,019] | +3,1 pts [−0,3 ; +6,5] |
| NVE T30, production poids 0,9 | 388 | −0,009 [−0,038 ; +0,018] | +2,1 pts [−1,3 ; +5,4] |
| Marché matin, informatives (172 nominales exclues) | 597 | **+0,051** [+0,029 ; +0,074] | **+3,2 pts** [+0,5 ; +6,0] |
| Marché T90, informatives | 648 | +0,003 [−0,011 ; +0,017] | +0,9 pt [−1,1 ; +2,9] |
| Marché T30, informatives | 651 | +0,002 [−0,011 ; +0,015] | +1,1 pt [−0,9 ; +3,1] |

**Fondamental seul face au NVE pur** (`model_probs`, matin, mêmes courses) :

| Découpage | Courses | Écart ll (IC 95 %) | Gagnant en tête : NVE pur → fondamental |
|---|---:|---|---|
| Toutes | 886 | **+0,259** [+0,203 ; +0,316] | 15,9 % → **23,9 %** (+8,0 pts [+4,9 ; +11,3]) |
| Production poids 0,9 | 345 | **+0,193** [+0,103 ; +0,280] | 15,1 % → **24,1 %** (+9,0 pts [+3,8 ; +14,2]) |

Le NVE pur a une log-vraisemblance (−2,40 à −2,43) de l'ordre du tirage au
sort (−2,41) : la valeur du NVE vient presque entièrement de son mélange avec
le marché.

**Robustesse « état connu au matin »** : fondamental réentraîné sans les
variables qui peuvent changer dans la journée (driver, changement de driver,
œillères). Gains réduits d'environ un tiers : NVE matin toutes +0,046
[+0,020 ; +0,073] (démontré), production +0,025 [−0,010 ; +0,060] (non
démontré), marché informatives +0,033 [+0,016 ; +0,050] (démontré), fondamental
seul face au NVE pur +0,209 [+0,156 ; +0,263].

Variables et heure de publication : les champs de carrière du programme
(courses, victoires, places, gains, musique) sont figés avant la course
(vérifié : +1,016 victoire à la course suivante d'un gagnant) ; corde et
poids sont connus à la déclaration ; historique cheval, jockey et entraîneur
arrêtés à la veille au soir. Le miroir Radar ne garde que l'état final des
partants (lignes réécrites dans la journée) : l'état exact de 06h30 n'est pas
rejouable depuis lui, d'où le test de robustesse ci-dessus. Les partants non
partants tardifs sont exclus des deux côtés (moteur et fondamental
renormalisés sur les partants au départ).

## Confidentialité (dépôt public)

- Le journal n'affiche que des **agrégats par mois** : nombres de courses,
  vraisemblances moyennes, taux de réussite, α, β, γ. Jamais un cheval, une
  course ni un coefficient ; un test le vérifie.
- **Aucun artefact**, aucun commit ; le rapport complet (coefficients compris)
  ne va que sur R2 privé.
- Lire le rapport : Cloudflare → R2 → `turf-engine-data` →
  `lab/benter/dernier.json` → *Download*.
- **Décision du 28/09/2026 (Steph, avec le dev Radar)** : le dépôt reste
  **public** tant que rien n'est vendu (pas de frais GitHub). Au passage à la
  vente : dépôt **privé**, ou `site/` retiré de Git et publié directement sur
  Cloudflare depuis le workflow, avec un petit commit régulier pour que
  GitHub ne coupe pas les tâches planifiées après 60 jours sans activité.
  À décider aussi : le sort de l'ancienne `turf_bench.db` (pronostics
  RADAR_V4 scellés du 07/09 au 24/09, présents dans le dépôt et son
  historique).

## Suite du plan

1. Remplacer la cote de clôture par la **cote à T-x** reconstituée depuis
   `participants_cotes_hist` (depuis le 15/09/2026) : combinaison à l'horizon
   réel de pari.
2. Enrichir le fondamental (déferré, réduction kilométrique : phase 1c).
3. Étape 3 : mises au **Kelly fractionné** sur l'avantage estimé, en
   simulation d'abord (G2 : 1 000 courses en shadow).
