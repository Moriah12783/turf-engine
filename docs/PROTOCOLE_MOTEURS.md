# Cahier des charges commun : moteurs Elite Turf

**Pour :** les sessions NEW_VALUE_ENGINE (NVE) et RADAR_V4. **Arbitre :** Steph.
**Objectif :** battre le marché *en information*, mesuré de façon incontestable.
C'est la méthode de Bill Benter (*Computer Based Horse Race Handicapping and
Wagering Systems: A Report*, 1994), à lire en entier.

## 1. Une seule métrique pour piloter

**Gain de log-vraisemblance par course face au marché, hors échantillon :**

    Δ = moyenne sur les courses [ log p_combinaison(gagnant) − log p_marché(gagnant) ]

- `p_marché` = probabilités implicites dé-margées **à l'horizon considéré**
  (cotes du verrou, éditions `odds_real = 1`), jamais les cotes finales.
- Mesuré en walk-forward, sur les **courses communes**, par horizon (T_MATIN, T90, T30, T15).
- Intervalle de confiance à 95 % par bootstrap sur les courses.
- **Succès : Δ > 0 avec une borne basse de l'IC > 0 sur au moins 1 000 courses de test.**
- Indicateurs secondaires, jamais seuls : Brier, calibration par décile, ROI
  par tranche de value avec IC. Les taux « dans les 8 » sont du marketing, pas
  du pilotage.

Outil de référence : `python lab_combinaison_benter.py turf_bench.db T15`.

## 2. Point de départ mesuré (23/09/2026, T-15, 453 courses communes)

| Combinaison apprise (271 courses), jugée sur 182 courses jamais vues | Gain vs marché | Coefficients |
|---|---|---|
| Marché recalibré | −0,0006 | marché 1,06 |
| Marché + NVE pur (`model_probs`) | −0,0017 | NVE **−0,02** |
| Marché + Radar | **+0,0043** | Radar **+0,14** |
| Marché + NVE pur + Radar | +0,0029 (IC95 −0,010 ; +0,015) | NVE −0,03, Radar +0,15 |

**Lecture :**
- **Le modèle pur NVE n'apporte aucune information au-delà du marché** (poids
  nul). Sa couche value est même anti-prédictive : sur 9 073 partants, les
  chevaux à indice de value ≥ 1 rapportent −50 à −59 %, contre −13 à −21 % pour
  les autres.
- **Radar, seul, est moins bon que le marché** (log-loss 2,097 contre 2,019),
  **mais combiné, il reçoit un poids positif.** C'est la leçon de Benter : on
  ne juge pas un modèle seul, on juge ce qu'il ajoute au marché. Le gain n'est
  pas encore significatif (IC qui inclut 0, 16 jours de données).
- Le ROI de −2,3 % de Radar sur 453 paris est du bruit (±15 points). Ne jamais
  piloter sur un ROI de moins de 2 000 paris.

## 3. Protocole d'évaluation (obligatoire)

1. **Aucune fuite temporelle.** Toute variable (musique, stats humaines, biais
   de corde, historique) est calculée avec des données antérieures à
   `lock_time_utc`. Vérifier que les champs cumulés de l'historique (gains,
   nombre de courses) sont figés à la date de chaque course.
2. **Période de test gelée.** Les 20 % de dates les plus récentes ne servent
   jamais au réglage. On ne les consulte qu'une fois par version candidate.
3. **Mêmes courses, même horizon, mêmes cotes** (celles de `odds_snapshots` au verrou).
4. **Registre d'expériences** `docs/experiences.md`. Chaque essai y est consigné
   (date, hypothèse, paramètres, Δ, IC), **échecs compris**, pour éviter le
   surajustement par essais multiples.
5. **Tailles minimales :** 1 000 courses de test pour Δ, 2 000 paris pour un ROI.

## 4. Contrat de sortie de chaque moteur (étape 1 de Benter)

- **Probabilités fondamentales, sans marché,** pour chaque partant actif, somme égale à 1.
  - NVE : `metadata.model_probs` existe déjà.
  - Radar : **documenter si les probabilités scellées intègrent déjà les
    cotes**. Si oui, exposer aussi une version fondamentale.
- **Pur et déterministe :** aucune entrée-sortie dans `predict()` hors pont
  Radar existant (cf. `docs/NOTE_DEVS_MIGRATION.md`). Horodaté
  (`as_of` ≤ verrou), versionné (`engine_version`).
- **Ne plus tirer vers le marché à l'intérieur du moteur.** Le
  `MARKET_WEIGHT = 0.90` fixe disparaît : c'est la combinaison qui décide
  du poids du marché.

## 5. Combinaison (étape 2 de Benter) : un rôle dédié

    p_final(i) ∝ exp( α·log p_marché(i) + β_NVE·log p_NVE(i) + β_Radar·log p_Radar(i) )

- **Apprentissage :** coefficients appris par logit conditionnel, **par
  horizon** (puis par discipline quand l'échantillon le permet), réappris
  chaque semaine en walk-forward, avec régularisation L2.
- **Propriétaire :** un rôle distinct des deux moteurs. Les moteurs optimisent
  leur **contribution marginale** : un moteur « moins bon » mais différent du
  marché vaut plus qu'un clone du favori.
- **Mise en service :** d'abord en *shadow*, c'est-à-dire calculée et archivée
  mais non publiée, jusqu'au critère de succès.

## 6. Mise (étape 3), seulement après un Δ démontré

- **Kelly fractionné (¼)** appliqué à `p_final`.
- **Seuil de value** après marge de sécurité (indice ≥ 1,10 à 1,15).
- **Plafonds d'exposition** par course et par jour.
- **Ordre :** simples d'abord, exotiques ensuite (combinaisons de type
  Harville/Stern ajustées).

## 7. Chantiers par moteur

**NVE (priorité) :**
1. Désactiver la couche value et le choix du tocard jusqu'à recalibration.
   Supprimer la température fixe de 12 : elle sera apprise.
2. Remplacer les poids fixés à la main par des coefficients appris (logit
   conditionnel sur les capteurs).
3. `parse_music` : un « 0 » (non placé) est noté comme une 6e à 9e place
   (15 points). Il doit être noté le plus bas. Gérer aussi `R`/`Ret`.
4. Chrono : le record à vie n'est pas normalisé. Normaliser par distance, type
   de départ (autostart ou volté) et hippodrome, et pondérer la récence.
5. Biais de corde codé en dur : l'apprendre par hippodrome × discipline ×
   distance × départ, avec lissage bayésien.
6. Capteur humain appris sur 30 jours seulement : brancher l'année
   d'historique (projet Apps Script), avec coupure au jour de la course.
7. Smart Money : aujourd'hui +100 points sur forte baisse de cote, sans avantage
   démontré (ROI −31,7 %). En faire une variable apprise :
   `log(cote matin / cote verrou)`.
8. Étoiles et NO_BET : remplacer les seuils fixes par l'entropie ou l'espérance de gain estimée.

**Radar :**
1. Documenter la présence du marché dans les probabilités scellées. Fournir la version fondamentale.
2. Recalibrer (température apprise), sans tirer soi-même vers le marché.
3. Profiter de la diversité. Le poids positif vient de ce que Radar voit autrement que le marché : c'est l'actif à cultiver.

**Communs :**
- Historique : brancher l'année disponible, puis une année de plus pour
  valider d'une saison à l'autre (apprendre sur N−1, tester sur N).
- Les cotes intrajournalières ne se récupèrent pas après coup. Les relevés
  multi-horizons collectés depuis le 25/08 sont irremplaçables : la
  combinaison (5 paramètres) s'apprend dessus ; les moteurs fondamentaux
  s'apprennent sur l'historique, sans cotes.

## 8. Rituel d'équipe

- **Chaque lundi :** tableau de score unique (banc des courses communes, avec Δ par horizon).
- **Revue hebdomadaire de 30 minutes :** registre, Δ, décisions.
- **Règle d'entrée en production :** une modification de moteur n'entre en
  production que si le Δ **de la combinaison** ne baisse pas sur la période gelée.

## 9. Jalons à 90 jours

| Échéance | Livrable |
|---|---|
| J+14 | Δ calculé automatiquement par le banc, registre ouvert, value NVE désactivée ou recalibrée, point 3 de `parse_music` corrigé |
| J+45 | NVE en logit conditionnel appris sur 1 an, Radar en version fondamentale documentée, combinaison v1 en shadow |
| J+90 | Combinaison en production si Δ > 0 avec IC > 0 sur au moins 1 000 courses de test |
