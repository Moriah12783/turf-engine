# Favicon prono.elite-turf.fr — 22/09/2026

3 fichiers, aucun fichier du développeur Radar, aucun effet sur le moteur ni sur le banc.

- `site/favicon.svg` — fer à cheval or sur fond nuit (vectoriel, 326 octets).
- `turf_lab/html_report.py` — en-tête de page : icône PNG 32 px embarquée (base64)
  pour tous les navigateurs, lien vers le SVG, `theme-color` pour la barre d'adresse mobile.
- `turf_lab/cloudflare_deploy.py` — `.svg` accepté par le déploiement direct (fichier texte).

Installation : `unzip -o correctif-favicon.zip` à la racine du projet, puis `publier_vers_github.bat`.
L'icône apparaît à la première passe qui régénère le site (quelques minutes). Si l'onglet
garde l'ancienne icône vide, c'est le cache du navigateur : Ctrl+F5 ou ouvrir la page en
navigation privée.
