# Réparation express (5 minutes)

Le run Actions #7 a échoué car la publication du 31/08 a supprimé 18 fichiers
du projet sur GitHub (moteur inclus). Ce paquet restaure tout et ajoute des
garde-fous pour que cela ne puisse plus se reproduire.

## Étapes

1. Dézippez TOUT ce zip dans votre vrai dossier « TURF PROJET » (celui qui
   contient déjà le projet complet), en remplaçant les fichiers existants.
2. Double-cliquez sur `publier_vers_github.bat` (la nouvelle version).
   Il récupère l'état GitHub, restaure la base cloud, publie le code complet
   — et refuse désormais de publier depuis un dossier incomplet ou de
   supprimer des fichiers du dépôt.
3. Sur GitHub → Actions → « Turf Engine 24/7 Cloud Sync & Deploy » →
   Run workflow. Le run doit passer au vert.

Alternative pour utilisateur git : `git am correctif-restauration.patch`
depuis un clone à jour de main, puis `git push origin main`.
