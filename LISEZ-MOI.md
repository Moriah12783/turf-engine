# PAQUET CONSOLIDÉ — Éditions immuables + Réglage d'ensemble (R1-R5)

Ce paquet REMPLACE le zip « correctif-editions-immuables » précédent
(tout est inclus ici). Une seule manipulation :
1. Dézipper dans « TURF PROJET » (remplace 6 fichiers).
2. Lancer publier_vers_github.bat.
3. GitHub -> Actions -> Run workflow.

## Contenu
A. ÉDITIONS IMMUABLES : plus aucun pronostic verrouillé après le départ ni
   rétroactivement ; tableau « Éditions verrouillées » dans chaque fiche
   (Matin/T-90/T-30/T-15 avec heure de verrou, Moteur 8 + Marché 8) ; badge
   reflétant l'édition réellement en base ; édition Matin posée dès 06h30 GMT.
B. RÉGLAGE D'ENSEMBLE : disciplines normalisées (déferrage et pondérations
   trot réactivés sur la moitié des courses), vraie cote de référence PMU,
   vrai chrono trot, vraie valeur handicap, poids en kg, gains réels,
   suppression de la presse fictive (poids redistribué, colonne retirée).

## Effets visibles après le 1er run
- Les sélections du moteur VONT CHANGER (c'est le but : capteurs réels).
- La colonne « Synthèse Presse » disparaît du comparatif.
- Les statistiques repartent sur des bases saines ; le badge ROI reste
  honnête (dividendes officiels uniquement).

## Métronome n8n (à activer, ~5 min — règle les passes GitHub sautées)
1. GitHub -> Settings -> Developer settings -> Fine-grained tokens : jeton
   limité au repo turf-engine, permission « Actions : Read and write ».
2. n8n -> Credentials -> New -> Header Auth : Name = Authorization,
   Value = Bearer VOTRE_JETON. Nom : « GitHub PAT Turf Engine ».
3. n8n -> Import from file -> n8n-metronome-turf.json -> vérifier la
   credential sur le nœud HTTP -> ACTIVER.
