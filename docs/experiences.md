# Registre d'expériences — moteur NEW_VALUE_ENGINE

Règle (docs/PROTOCOLE_MOTEURS.md, §3.4) : chaque essai est consigné ici,
**échecs compris**, avant toute mise en production. Un essai non consigné n'existe pas.

Format : date · hypothèse · données · méthode · résultat · décision.
Δ = gain de log-vraisemblance par course face au marché, hors échantillon (nats/course).
Les taux « dans les 8 » restent la métrique du produit livré aux abonnés ; Δ pilote la
combinaison et la mise.

---

## 2026-09-02 — F1 : calibration marché à poids fixe (0,70) + F2 stats humaines + F3 banc par horizon
- **Hypothèse :** mélanger 70 % de probabilité implicite du marché et 30 % de modèle pur bat le modèle pur seul.
- **Données :** 523 courses terminées à cotes réelles (25/08 → 01/09).
- **Méthode :** walk-forward (`backtest_reglages.py`), aucune fuite ; sélection production.
- **Résultat :** gagnant dans les 8 85 % → 92 % ; tiercé dans les 8 56 % → 69 % ; Brier 0,0773 → 0,0717.
- **Décision :** mis en production le 02/09 (poids 0,70).

## 2026-09-04 — Top 8 par probabilité calibrée (au lieu de « 5 par proba + 3 par value »)
- **Hypothèse :** les rangs 6-8 choisis par value sont moins souvent à l'arrivée que les rangs 6-8 par probabilité.
- **Données :** 523 courses, walk-forward.
- **Résultat :** tiercé / quarté / quinté dans les 8 : +14 / +13 / +15 points.
- **Décision :** mis en production le 04/09. Les rangs 9-10 deviennent les « regrets ».

## 2026-09-15 — Poids du marché 0,70 → 0,90
- **Hypothèse :** à 0,70 le moteur contredit le marché en tête trop souvent et perd ces duels (7 gagnés / 16 perdus sur 51 divergences).
- **Données :** 523 puis 1 054 courses (rejoué le 22/09), walk-forward.
- **Résultat (1 054 courses) :** 0,90 meilleur que 0,70 et que 1,00 (marché pur) sur tiercé/quarté/quinté dans les 8, tête et ROI gagnant ; Brier 0,0713 (égal à 1,00). Écarts de 1 point : petits, constants, non décisifs.
- **En production (16 → 21/09, 215 courses à marché réel) :** divergences 9 (6 gagnées / 1 perdue) ; couverture des 8 à parité avec le marché (4,15 vs 4,14 arrivants) ; tête 29 % vs 27 %.
- **Décision :** mis en production le 15/09 18:17 UTC. Déclaré dans l'annexe du pont Radar (deux versions NVE : avant/après).

## 2026-09-21 — Ligne de base marché « nominale » (bug de mesure)
- **Constat :** sans cotes réelles au verrou, `MarketOddsEngine` renvoyait l'ordre des dossards (1-2-…-8), affiché et compté comme pronostic du marché : 622 éditions dans l'archive.
- **Effet mesuré :** ligne Marché du banc sous-estimée (Matin 83,4 % → 87,2 % de gagnants dans les 8 après correction ; T-15 91,7 % → 93,4 %).
- **Décision :** correctif publié le 21/09 (sélection vide sans marché ; éditions nominales ignorées partout). Ligne datée transmise à l'annexe du pont.

## 2026-09-22 — Labo : calibration sur marché partiel (50-90 % de partants cotés)
- **Hypothèse :** sur les éditions où le marché est partiel, la formulation convenue avec le dev Radar (probabilités implicites des cotés corrigées de l'overround, masse résiduelle répartie sur les non-cotés selon le modèle pur, puis mélange 0,90/0,10) bat le modèle pur.
- **Données :** 189 éditions 50-90 % depuis le 16/09, cotes réelles archivées dans `odds_snapshots` (ratio moyen coté 68 %).
- **Résultat :** gagnant dans les 8 75 % → 85 % ; tiercé 47 % → 62 % ; quarté 34 % → 41 % ; tête 15 % → 24 %.
- **Décision :** proposition à déposer après le 06/10 (fenêtre de pré-enregistrement), à mesurer sur l'extrait MINCE du Radar. Dans l'architecture du protocole commun, ce calcul appartient au rôle « combinaison ».

## 2026-09-23 — Réplication du labo « combinaison Benter » (`lab_combinaison_benter.py`)
- **Données :** 453 courses communes T-15 (07 → 22/09), 271 apprentissage / 182 test.
- **Résultat T-15 :** marché recalibré −0,0005 ; + moteur pur −0,0017 (β = −0,02) ; + Radar +0,0043 ; les trois +0,0028, IC95 [−0,009 ; +0,014]. **Rien de significatif à cet échantillon.**
- **Résultat Matin :** + Radar +0,092, IC95 > 0 — **artefact** : dans 100 % des cas, l'édition Matin du Radar est verrouillée 75 min après la ligne de base marché (07h45 vs 06h30). Règle « mêmes cotes » non respectée à cet horizon ; le banc signale désormais ces lignes (« verrous non simultanés »).
- **Décision :** Δ calculé automatiquement par le banc à chaque passe (`turf_lab/combinaison.py`, méthode de Newton, < 2 s). Lecture honnête : l'information marginale du modèle pur n'est pas mesurable à 182 courses ; ni positive ni négative.

## 2026-09-23 — `parse_music` : « 0 » (non placé) noté comme une 6e-9e place
- **Constat :** un « 0 » recevait 15 points au lieu de la note la plus basse (5) ; « Ret »/« R » (retiré) pouvait être lu comme un incident.
- **Données :** 1 086 courses, walk-forward, avant/après.
- **Résultat :** effet nul sur le banc (modèle pur : tiercé dans les 8 56,7 % → 56,9 % ; à 0,90 : aucune différence à ±0,2 point). Correctif de propreté du modèle fondamental, pas de gain à attendre.
- **Décision :** livré le 23/09 ; changement de moteur → ligne datée dans l'annexe avant publication.

## 2026-09-23 — Mesure du « tocard » (outsider par value, cote ≥ 10)
- **Données :** 1 054 éditions T-15 avec tocard ; dividendes officiels.
- **Résultat :** ROI gagnant −27 % (depuis le 04/09) à −50 % (tout l'historique) ; ROI placé −34 % à −46 %. Référence tous partants à cote ≥ 10 : −35 % / −33 %. **Aucune valeur démontrée** ; pire que la référence en placé.
- **Décision :** en attente (choix produit de Steph : conserver comme élément éditorial ou retirer).
