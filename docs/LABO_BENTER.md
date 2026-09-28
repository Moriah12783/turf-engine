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
