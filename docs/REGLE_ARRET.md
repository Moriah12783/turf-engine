# Règle d'arrêt de la recette A en production

**Statut : PROJET, en relecture (PR du labo du 25/11/2026).** La règle est gelée
à la fusion, avant le premier jour de production (30/11). Après la fusion,
aucune de ses constantes ne change : le workflow de lecture vérifie les
empreintes du lecteur et de ses modules.

## Décision de départ

Décision écrite de Steph du 13/11/2026 (registre `docs/OMBRE_REGISTRE.md`) :

1. recette A (0,90 marché + 0,10 fondamental) en production sur l'édition du
   matin à compter du 30/11/2026 ;
2. calcul de nuit maintenu après le 24/11 ;
3. **règle d'arrêt : retour au moteur actuel si, sur 30 jours de production,
   tout l'intervalle de confiance du gain passe sous zéro.**

Ce document fixe d'avance comment le point 3 est lu, avant la première
édition de production.

## Ce qui est mesuré

- **Le gain d'une édition** est l'écart de log-vraisemblance du gagnant :
  probabilité du gagnant dans l'édition publiée en recette A, moins sa
  probabilité dans le témoin NVE archivé dans la même édition
  (`metadata.temoin_nve`, calculé par le moteur actuel au même verrou).
  C'est le critère de la lecture de l'ombre du 12/11, rôles échangés : un
  gain positif veut dire que la recette A donne plus de probabilité au
  gagnant que le moteur actuel.
- **Le gain de la règle** est la moyenne des gains des éditions lues.
  Son intervalle de confiance à 95 % est obtenu par bootstrap de réunions
  entières, avec 4 000 tirages et la graine 20260928, comme le 12/11
  (`turf_lab/ombre.py`).

## Éditions lues

Une édition entre dans la lecture si **toutes** les conditions suivantes sont
réunies.

1. Elle est du moteur `NEW_VALUE_ENGINE`, à l'horizon `T_MATIN`, d'un jour de
   course compris entre le 30/11 et le 29/12/2026 inclus (30 jours).
2. Elle est à marché réel, avec un poids marché de 0,90 : c'est l'éligibilité
   du lecteur du 12/11 (`ombre_lecture.is_eligible`).
3. Sa course a une arrivée définitive, avec un seul gagnant (dead-heat à la
   première place exclu).
4. Elle est en recette A de la clé gelée : `recette` vaut
   `A_lineaire_0.90_0.10`, `nve_version` vaut `nve-2026-11-30` et
   `fondamental_model_version` vaut `fond-matin-e570d663793c`.
5. Son témoin est complet : version `nve-2026-10-07`, une probabilité par
   partant publié, aucune négative, de somme non nulle.
6. Le gagnant figure parmi les partants de l'édition.

**Éditions en repli.** Une édition en repli (`recette` = `REPLI_NVE`) publie
le moteur actuel : son gain est nul par construction. Elle est comptée et
n'entre pas dans le gain. La part des éditions éligibles lues en recette A
est rendue à titre d'information. Elle ne suspend pas la décision : la règle
de Steph n'en prévoit pas.

**Aucune remise à zéro.** Une édition d'une autre clé (correctif du moteur ou
du fondamental pendant la fenêtre) est exclue, au motif `cle_differente`. Les
jours manqués (calcul de nuit absent) comptent dans les 30 jours.

## Lecture unique et décision

- **Une seule lecture, le 30/12/2026 (31e jour) ou après.** Avant cette date,
  le lecteur ne rend que le compteur, sans aucun gain. La date de lecture
  n'est pas réglable en ligne de commande.
- **Décision :**

| Résultat | Décision |
|---|---|
| Borne haute de l'IC95 < 0 | `RETOUR_AU_MOTEUR_ACTUEL` : PR de retour (`RECETTE_A_FIN` = lendemain de la lecture), fusion sur GO écrit de Steph |
| Sinon | `MAINTIEN_RECETTE_A` |
| Moins de deux réunions lues | `NON_MESURABLE_DECISION_ECRITE_DE_STEPH` : la règle ne déclenche pas le retour |

- **Ce qui est publié :** des agrégats seulement (dépôt public). On publie les
  comptes, le gain moyen, l'IC95, la décision et une empreinte des courses
  retenues et de leurs gains. Aucune probabilité, ni aucun gain course par
  course.
- **Exécution :** workflow « Lecture unique de la règle d'arrêt »
  (`.github/workflows/regle_arret_lecture.yml`). Il se lance à la main depuis
  `main`, une seule fois, à partir du 30/12. Il refuse de tourner si une
  exécution a déjà réussi ou si `docs/REGLE_ARRET_LECTURE.md` existe. Il
  vérifie les empreintes du lecteur et de ses modules, et lit une copie de la
  base qui n'est jamais renvoyée sur R2.

## Constantes gelées

| Constante | Valeur |
|---|---|
| DEBUT_PRODUCTION | 2026-11-30 |
| DUREE_JOURS | 30 |
| FIN_FENETRE | 2026-12-29 |
| LECTURE_LE | 2026-12-30 |
| HORIZON | T_MATIN |
| RECETTE_A | A_lineaire_0.90_0.10 |
| RECETTE_REPLI | REPLI_NVE |
| NVE_RECETTE_A | nve-2026-11-30 |
| NVE_TEMOIN | nve-2026-10-07 |
| MODELE_ATTENDU | fond-matin-e570d663793c |
| META_TEMOIN | temoin_nve |
| NIVEAU | 0.95 |
| REUNIONS_MIN | 2 |
| BOOTSTRAP_TIRAGES | 4000 |
| GRAINE | 20260928 |

## Confidentialité jusqu'à la lecture

Une lecture intermédiaire est impossible par construction :

- le compteur quotidien (`turf_lab/recette_compteur.py`) ne lit jamais le
  témoin ;
- ce lecteur ne rend aucun gain avant le 30/12 ;
- le témoin ne vit que dans la base privée sur R2 ;
- la garde de confidentialité (`turf_lab/garde_ombre.py`) cherche chaque nuit
  la clé `temoin_nve` dans tout ce qui est public, en plus de la clé de
  l'ancienne archive d'ombre.

## Points soumis au dev NVE et au dev Radar

1. Gain sur les seules éditions en recette A ; éditions en repli comptées et
   exclues ; part en recette A rendue à titre d'information, sans suspendre la
   décision.
2. Aucune remise à zéro : toute autre clé est exclue (`cle_differente`).
3. `NON_MESURABLE` sous deux réunions, sans retour automatique.
4. Le lecteur cite le témoin : il rejoint le moteur dans la liste de
   `test_seul_le_moteur_cite_le_temoin`, une fois la PR du moteur fusionnée.
5. Garde de confidentialité : marqueurs `ombre_fondamental` et `temoin_nve`
   à partir de la fusion. `A_lineaire_0.90_0.10` et `fond-matin-` sont
   retirés, car ce sont des identifiants de production à partir du 30/11.
   Fin des horaires du compteur de l'ombre, qui se seraient relancés chaque
   automne.
