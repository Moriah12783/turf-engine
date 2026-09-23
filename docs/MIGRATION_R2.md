# Migration de la persistance vers Cloudflare R2 — Étape 1/3

**But :** sortir `turf_bench.db` de Git avant le plafond GitHub de 100 Mo par
fichier (34 Mo au 23/09/2026, environ +1,1 Mo/jour, plafond vers fin novembre
2026). Cette étape ne change **ni les moteurs, ni le site, ni le déploiement
Cloudflare Pages**.

## Principe

| Situation | Comportement du workflow |
|---|---|
| Secrets R2 absents | **Mode legacy** : rien ne change, la base reste committée (`R2_DISABLED` dans les logs). |
| Secrets R2 posés, bucket vide | **Amorçage unique** depuis la copie Git, puis passe normale (`R2_SEED_OK`). |
| Secrets R2 posés, base présente | Téléchargement vérifié → passe → renvoi + sauvegarde du jour (`R2_PULL_OK` / `R2_PUSH_OK`). |
| Anomalie (réseau, empreinte, base qui rétrécit, écriture concurrente) | **Job rouge**, rien n'est committé ni déployé, la passe suivante réessaie. Jamais de repli silencieux sur une copie périmée. |

Contenu du bucket :
- `turf_bench.db` : la base de référence, avec métadonnées `sha256`, `counts`, `pushed-at`, `run-id`, `commit` ;
- `state/seed.json` : témoin d'amorçage (date, empreinte, comptes) ;
- `backups/turf_bench_AAAA-MM-JJ.db` : dernier état de chaque journée.

## Mise en place pas à pas (Steph, environ 15 minutes)

> Les intitulés du tableau de bord Cloudflare peuvent varier légèrement selon
> les versions de l'interface ; la logique reste la même.
> **Ne collez jamais une clé secrète dans une conversation, un commit ou un e-mail.**

### 0. Cloudflare : activer R2 (une seule fois, par Steph)
R2 n'est pas encore activé sur le compte : l'API répond `10042 Please enable
R2 through the Cloudflare Dashboard`. Cloudflare exige cette activation
**avant** toute création de bucket ou de jeton.
1. Se connecter sur https://dash.cloudflare.com, compte Elite Turf (celui qui
   porte les Workers `metronome-turf`, `elite-turf-predictive-engine`,
   `pmu-proxy`…).
2. Menu de gauche : **Storage & databases**, **R2 object storage** (ou
   directement **R2**).
3. Suivre l'activation proposée (*Purchase R2* / *Enable R2*) : validation
   du moyen de paiement déjà enregistré. La franchise gratuite est incluse
   (10 Go-mois de stockage, des millions d'opérations par mois, sortie de
   données gratuite). Rien n'est facturé à notre volume.

### A. Cloudflare : créer le bucket
*Claude peut le faire via le connecteur Cloudflare dès que R2 est activé.
Sinon, à la main :*
1. Page **R2 object storage**, bouton **Create bucket**.
2. Renseigner :
   - **Bucket name :** `turf-engine-data` (minuscules, chiffres et tirets uniquement) ;
   - **Location :** *Automatic* ;
   - **Default storage class :** *Standard*. Surtout **pas** *Infrequent
     Access* : la base est lue environ 100 fois par jour.
3. **Create bucket**.
4. Onglet **Settings** du bucket : vérifier que l'accès public (*Public
   Development URL* / *r2.dev*) est **désactivé** et qu'aucun domaine
   personnalisé n'est branché. Le bucket doit rester privé.
5. **Ne pas activer de *Bucket Lock*** pour l'instant : la sauvegarde du jour
   est réécrite à chaque passe, et un verrou la bloquerait (job rouge). Un
   verrou d'immuabilité sur des sauvegardes à écriture unique est prévu à
   l'étape 2.

### B. Cloudflare : jeton d'écriture pour GitHub
Selon la documentation officielle (août 2026) :
1. Page **R2 object storage**, section **Account Details** : cliquer sur
   **Manage** à côté de **API Tokens**.
2. Choisir **Create Account API token** (recommandé : lié au compte, pas à une
   personne, valable jusqu'à révocation). Cela exige le rôle **Super
   Administrator**. Sinon, *Create User API token* fonctionne aussi.
3. Renseigner :
   - **Nom :** `turf-engine-github-rw` ;
   - **Permissions :** **Object Read & Write** (*Object Read and Write*), pas *Admin* ;
   - **Buckets :** restreindre à `turf-engine-data` uniquement ;
   - **TTL :** *Forever* ;
   - **Filtrage par adresse IP :** laisser vide (les machines GitHub changent d'adresse).
4. Valider (**Create Account API token**). L'écran suivant n'apparaît
   **qu'une seule fois**. Copier dans un gestionnaire de mots de passe :
   - **Access Key ID** ;
   - **Secret Access Key** ;
   - l'adresse S3 `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`. Son
     `<ACCOUNT_ID>` doit être identique au secret GitHub
     `CLOUDFLARE_ACCOUNT_ID` déjà en place.

### C. Cloudflare : jeton lecture seule pour les devs
Même procédure, avec :
- **Token name :** `turf-engine-devs-ro` ;
- **Permissions :** **Object Read only** (*Object Read*) ;
- **Bucket :** `turf-engine-data` uniquement.

Le transmettre aux devs par un canal sûr. Dans les sessions Claude des devs,
poser les clés comme **variables d'environnement** de l'environnement cloud
(`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_ACCOUNT_ID`) et
autoriser le domaine `<ACCOUNT_ID>.r2.cloudflarestorage.com` dans l'accès
réseau de cet environnement. Sinon `fetch` sera bloqué.

### D. Cloudflare (optionnel) : purge automatique des sauvegardes
Bucket, onglet **Settings**, **Object lifecycle rules**, **Add rule** :
- nom `purge-backups-90j` ;
- préfixe `backups/` ;
- action : suppression des objets après **90 jours**.

Sans cette règle, les sauvegardes s'accumulent (environ 1 Go par mois), ce qui reste dans la franchise gratuite pendant des mois.

### E. GitHub : fusionner la branche (n'importe quand)
Fusionner la PR dans `main`. **Sans secrets R2, rien ne change** : les logs
affichent `R2_DISABLED` et la base continue d'être committée.

### F. GitHub : poser les secrets (le soir, après la passe de 21h30 GMT)
1. Dépôt `turf-engine`, **Settings**, **Secrets and variables**, **Actions**,
   onglet **Secrets**.
2. Vérifier que `CLOUDFLARE_ACCOUNT_ID` est présent. Il l'est déjà, car il
   sert au déploiement.
3. **New repository secret**, trois fois :
   - `R2_ACCESS_KEY_ID` : l'Access Key ID du jeton `turf-engine-github-rw` ;
   - `R2_SECRET_ACCESS_KEY` : son Secret Access Key ;
   - `R2_BUCKET` : `turf-engine-data`.

   Poser les **trois** : une configuration partielle fait échouer la passe
   (`R2_CONFIG_ERROR`), volontairement.
4. Pour ne pas attendre le métronome : **Actions**, *Turf Engine 24/7 Cloud
   Sync & Deploy*, **Run workflow**, branche `main`, `verify_days` vide, **Run workflow**.

### G. Vérifier (5 minutes)
1. Dans la passe GitHub :
   - l'étape *Restore Database from Cloudflare R2* affiche `R2_SEED_PENDING` ;
   - l'étape *Persist Database to Cloudflare R2* affiche `R2_SEED_OK` puis `R2_PUSH_OK`.
2. Dans le bucket R2, onglet **Objects** :
   - `turf_bench.db` (environ 34 Mo) ;
   - `state/seed.json` ;
   - `backups/turf_bench_AAAA-MM-JJ.db`.
3. À la passe suivante : `R2_PULL_OK` dans les logs.
4. Le dernier commit *Auto-sync PMU multi-horizon* ne contient plus `turf_bench.db`.
5. prono.elite-turf.fr continue de se mettre à jour.

### Dépannage rapide
| Message dans les logs | Cause probable | Action |
|---|---|---|
| `R2_CONFIG_ERROR … manquant : X` | un secret absent ou mal nommé | corriger le nom du secret X |
| `AccessDenied` / `403` | jeton sans *Object Read & Write* ou limité à un autre bucket | recréer le jeton (étape B) |
| `InvalidAccessKeyId` / `SignatureDoesNotMatch` | clé copiée incomplète (espace, retour à la ligne) | recoller les deux clés |
| `NoSuchBucket` | `R2_BUCKET` mal orthographié | corriger le secret |
| `R2_GUARD_REFUSED …` | garde-fou déclenché (voir le motif) | ne rien forcer, me transmettre le log |

> **Tant qu'aucun `R2_SEED_OK` n'est apparu**, supprimer les trois secrets
> ramène sans risque au mode legacy. **Après**, suivre la procédure de retour
> arrière ci-dessous.

Coût attendu : environ 0 €. Tout reste dans la franchise R2 (10 Go de stockage
et plusieurs millions d'opérations par mois, sortie de données gratuite).

## Continuité de service

- **Avant la pose des secrets :** comportement identique à aujourd'hui.
- **À la bascule :** la première passe amorce R2 avec la base la plus récente
  de `main`. Aucune passe n'est sautée et aucune édition n'est perdue.
- **Une panne R2 isolée** fait échouer une passe (job rouge, site inchangé) ;
  la suivante réessaie 15 minutes plus tard. Un verrou n'est manqué que si la
  panne couvre toute la fenêtre d'un horizon.

## Retour arrière (dans cet ordre, sinon perte de données)

De préférence la nuit, hors fenêtres de verrouillage.

1. **Geler les écritures.** GitHub, **Actions**, *Turf Engine 24/7 Cloud Sync
   & Deploy*, menu **⋯**, **Disable workflow**. Mettre aussi en pause les
   métronomes : n8n et le Worker Cloudflare `metronome-turf` (déclencheur Cron).
2. **Récupérer la base à jour :**
   `python -m turf_lab.r2_store fetch --db turf_bench.db` (ou la télécharger
   depuis le bucket, onglet **Objects**).
3. **La committer sur `main`** à la place de la copie figée.
4. **Supprimer les 3 secrets R2** : le mode legacy reprend.
5. **Réactiver** le workflow (**Enable workflow**) et les métronomes.

> ⚠️ Supprimer les secrets **sans** les étapes 1 à 3 ferait repartir le
> pipeline de la copie Git figée à la date de bascule. Tout ce qui a été
> verrouillé depuis serait perdu.

## Commandes

```bash
python -m turf_lab.r2_store pull   --db turf_bench.db   # début de passe (workflow)
python -m turf_lab.r2_store push   --db turf_bench.db   # fin de passe (workflow)
python -m turf_lab.r2_store fetch  --db turf_bench.db   # copie locale à jour, lecture seule
python -m turf_lab.r2_store status                      # état du bucket
```

Pour `fetch` et `status`, définir les variables `R2_ACCESS_KEY_ID`,
`R2_SECRET_ACCESS_KEY`, `R2_BUCKET` et `CLOUDFLARE_ACCOUNT_ID` (ou
`R2_ACCOUNT_ID`), et installer `boto3>=1.36`.

## Suite du plan

- **1bis** (après environ 7 jours stables) : retirer `turf_bench.db` de l'index
  Git (`git rm --cached` et `.gitignore`).
- **2** : exécution sur Cloudflare :
  - un Worker planifié chaque minute sert d'ordonnanceur par course (verrous
    exacts à T-90, T-30 et T-15) ;
  - un Container exécute le Python ;
  - `site/` est persisté sur R2 ;
  - un identifiant de build remplace `GITHUB_SHA` et `GITHUB_RUN_ID` ;
  - la re-vérification `verify_days` passe par un point d'appel authentifié ;
  - les métronomes (n8n et Worker `metronome-turf`) sont remplacés par l'ordonnanceur.
- **3** : dépôt privé, et purge éventuelle de l'historique Git. Cette purge est
  une opération **manuelle** et concertée.
