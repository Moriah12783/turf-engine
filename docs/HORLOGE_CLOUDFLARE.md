# Horloge Cloudflare : le Worker `turf-horloge`

## Pourquoi

Les horaires de GitHub ne sont pas garantis la nuit :

- les 28 et 29/09, l'export de l'historique Radar prévu à 01h17 UTC n'a
  **jamais démarré** ;
- le filet de 03h47 est parti avec **plus de 3 heures de retard** (06h55),
  hors de la fenêtre du Radar. Le script a refusé, comme prévu.

Pour l'ombre, c'est un risque direct. Le calcul de nuit refuse d'écrire après
06h20 UTC. Une nuit ratée, c'est une journée sans ombre, soit environ 3 points
de couverture, alors que la décision est suspendue sous 90 %.

Les horaires de Cloudflare (*Cron Triggers*, en UTC) sont tenus à la minute. Le
Worker lance donc les workflows GitHub à heure fixe par `workflow_dispatch`,
qui démarre en quelques secondes. **Les horaires GitHub restent en place comme
filet.** Deux lancements le même matin ne posent aucun problème : les workflows
passent l'un après l'autre, et le second remplace le premier.

## Ce qu'il lance (UTC)

| Horaire | Workflow | Période |
|---|---|---|
| 01h17 | `history_export.yml` (export de l'historique Radar) | tous les jours |
| 05h05 et 05h50 | `repetition_ombre.yml` (répétition générale) | du 01/10 au 06/10 |
| 05h05 et 05h50 | `fondamental_nuit.yml`, action `nuit` (ombre) | du 08/10 au 24/11 |
| 01h17 | `garde_ombre.yml` (garde de confidentialité de l'ombre) | du 08/10 au 25/11 |
| 01h17 | `ombre_compteur.yml` (compteur quotidien de l'ombre) | du 08/10 au 25/11 |

Le 24/11 est le dernier jour d'ombre possible avec une remise à zéro (version
corrigée au 14e jour, puis 35 jours). Avant le gel, le calcul de nuit refuse
d'écrire (`OMBRE_PAS_OUVERTE`).

La garde et le compteur s'ajoutent le 09/10 (décision de Steph) : leurs
horaires GitHub partaient avec 4 à 7 heures de retard, et le premier compteur
programmé n'était toujours pas parti à 15h. Ils prennent le créneau de 01h17
pour ne pas ajouter de *Cron Trigger* : à cette heure, la journée de la veille
est complète (éditions du matin, horizons de la journée, arrivées). Les deux
workflows ne font que lire. Leurs horaires GitHub restent en filet.

## Ce qu'il ne fait pas

- Il ne lit ni n'écrit **aucune donnée**.
- Il n'a **aucune page publique** : son adresse répond 404, et l'adresse
  `workers.dev` est désactivée.
- Les **garde-fous restent dans les workflows** : la fenêtre 00h-05h du Radar,
  les limites de 06h20 et 06h28, et `OMBRE_PAS_OUVERTE`.
- Il **ne journalise jamais son jeton**.

## Déploiement (environ 15 minutes, à faire avant le 01/10 à 05h05 UTC)

### A. GitHub : jeton limité au lancement des workflows

1. github.com : ta photo de profil, puis **Settings**, **Developer settings**,
   **Personal access tokens** et **Fine-grained tokens**. Clique sur
   **Generate new token**.
2. Remplis :
   - **Token name :** `turf-horloge-lancement-expire-2026-12-31` ;
   - **Expiration :** *Custom*, 31/12/2026 ;
   - **Resource owner :** `Moriah12783` ;
   - **Repository access :** *Only select repositories*, puis `turf-engine`
     seulement ;
   - **Permissions**, *Repository permissions* : **Actions : Read and write**.
     *Metadata : Read-only* s'ajoute tout seul. **Rien d'autre.**
3. **Generate token.** Le jeton ne s'affiche qu'une fois : range-le tout de
   suite dans ton gestionnaire de mots de passe. **Ne le colle nulle part
   ailleurs** (chat, dépôt, capture d'écran).

### B. Cloudflare : le Worker

1. dash.cloudflare.com : **Workers & Pages**, **Create**, puis **Create
   Worker** (modèle *Hello World*). Nom : `turf-horloge`. Clique sur **Deploy**.
2. **Edit code** : remplace tout le code par le contenu de
   `infra/cloudflare/horloge/worker.js`, puis clique sur **Deploy**.
3. **Settings**, **Variables and Secrets**, **Add** :
   - **Type :** *Secret* ;
   - **Variable name :** `GITHUB_TOKEN` ;
   - **Value :** le jeton de l'étape A.

   Enregistre (**Deploy**).
4. **Settings**, **Trigger events** (*Cron Triggers*), **Add**, trois fois :
   `17 1 * * *`, `5 5 * * *` et `50 5 * * *`.
5. **Settings**, **Domains & Routes** : désactive **workers.dev**.
6. **Observability** : active les journaux (*Workers Logs*), pour voir les
   lignes `HORLOGE`.

Variante en ligne de commande, facultative : dans `infra/cloudflare/horloge/`,
lance `npx wrangler deploy`, puis `npx wrangler secret put GITHUB_TOKEN`.

### C. Test immédiat, sans risque

Dans l'éditeur du Worker, onglet **Schedule** (ou *Trigger scheduled event*),
choisis `17 1 * * *` et lance l'événement.

- **Attendu dans GitHub**, onglet *Actions* : une exécution
  « Historique Radar -> R2 » apparaît en quelques secondes, avec le déclencheur
  *workflow_dispatch*. Hors de la fenêtre 00h-05h UTC, elle s'arrête proprement
  (`HISTORY_HORS_CRENEAU`) sans rien lire du Radar.
- **Attendu dans les journaux du Worker** :
  `HORLOGE {"etat":"LANCE","workflow":"history_export.yml",…}`.

Si l'aperçu de l'éditeur n'a pas accès au secret (`SANS_JETON`), le vrai test a
lieu à 01h17 UTC. Le compte rendu du matin le vérifie.

## Mise à jour du 09/10 : garde et compteur de l'ombre (environ 5 minutes)

Aucun nouveau *Cron Trigger*, aucun nouveau secret : seul le code change.

1. Ouvre `infra/cloudflare/horloge/worker.js` sur GitHub (branche `main`),
   bouton **Raw**, puis copie tout le texte.
2. dash.cloudflare.com : **Workers & Pages**, `turf-horloge`, **Edit code**.
   Remplace tout le code par le texte copié, puis clique sur **Deploy**.
3. Onglet **Deployments** : note l'identifiant de la nouvelle version (les 8
   premiers caractères) et transmets-le au labo, qui l'inscrit au registre de
   l'ombre (`docs/OMBRE_REGISTRE.md`).
4. Vérifie dans **Settings**, **Trigger events** que les trois horaires sont
   toujours là : `17 1 * * *`, `5 5 * * *` et `50 5 * * *`.

Le lendemain à 01h17 UTC, l'onglet *Actions* de GitHub doit montrer trois
lancements *workflow_dispatch* : l'export de l'historique, la garde et le
compteur. Le calcul de nuit de 05h05 et 05h50 ne change pas.

## Lire les journaux

| Ligne | Sens |
|---|---|
| `LANCE` | GitHub a accepté le lancement |
| `ECHEC` statut 401 ou 403 | jeton invalide, expiré ou sans la permission *Actions* : refaire l'étape A |
| `ECHEC` statut 404 | nom de dépôt ou de workflow faux |
| `ECHEC` statut 422 | entrée inconnue du workflow : le Worker relance aussitôt sans entrées |
| `ECHEC` statut 5xx ou 429 | incident passager chez GitHub : le Worker réessaie (4 essais) |
| `RIEN_A_LANCER` | normal hors des périodes du plan |
| `SANS_JETON` | le secret `GITHUB_TOKEN` n'est pas posé |

Côté répétition, la ligne `REPETITION_NUIT` indique le déclencheur :
`workflow_dispatch` pour l'horloge Cloudflare, `schedule` pour l'horaire
GitHub.

## Sécurité et arrêt

- **Si le jeton fuitait**, il ne permettrait que de lancer les workflows de ce
  dépôt : ni lire les secrets, ni modifier le code. Le révoquer suffit
  (GitHub, *Fine-grained tokens*, **Revoke**).
- **Arrêt :** le plan de l'ombre s'arrête seul après le 25/11. Pour arrêter plus tôt,
  supprime les *Cron Triggers* ou le Worker, puis révoque le jeton.
- **Tests :** `node --test infra/cloudflare/horloge/worker.test.mjs` teste la
  logique du Worker. `tests/test_horloge_cloudflare.py` vérifie que chaque
  workflow lancé déclare les entrées envoyées, et que les horaires du plan sont
  ceux de `wrangler.toml`.
