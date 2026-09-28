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
- Le marché de référence est la **cote de clôture** (`cote_reference`
  finale) : c'est le repère le plus exigeant. En production, on pariera à T-x
  avec des cotes moins informées, donc un gain mesuré ici est prudent.
- `ll_fondamental` < `ll_marche` est **normal** : Benter l'observait aussi.
  Ce qui compte, c'est `beta_fondamental` > 0 et le delta.

## Confidentialité (dépôt public)

- Le journal n'affiche que des **agrégats par mois** : nombres de courses,
  vraisemblances moyennes, taux de réussite, α, β, γ. Jamais un cheval, une
  course ni un coefficient ; un test le vérifie.
- **Aucun artefact**, aucun commit ; le rapport complet (coefficients compris)
  ne va que sur R2 privé.
- Lire le rapport : Cloudflare → R2 → `turf-engine-data` →
  `lab/benter/dernier.json` → *Download*.

## Suite du plan

1. Remplacer la cote de clôture par la **cote à T-x** reconstituée depuis
   `participants_cotes_hist` (depuis le 15/09/2026) : combinaison à l'horizon
   réel de pari.
2. Enrichir le fondamental (déferré, réduction kilométrique : phase 1c).
3. Étape 3 : mises au **Kelly fractionné** sur l'avantage estimé, en
   simulation d'abord (G2 : 1 000 courses en shadow).
