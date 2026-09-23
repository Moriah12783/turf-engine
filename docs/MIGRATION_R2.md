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

## Mise en place (Steph, environ 10 minutes)

1. **Cloudflare, R2 :** créer le bucket `turf-engine-data`, privé (aucun accès public).
2. **R2, Manage API tokens :** créer un jeton **Object Read & Write** limité à
   ce bucket. Noter l'*Access Key ID* et le *Secret Access Key*.
3. **Pour les devs :** créer un second jeton **Object Read only** sur le même
   bucket (commande `fetch`).
4. **Fusionner la branche dans `main`.** Tant que les secrets ne sont pas posés, rien ne change.
5. **Le soir, après la passe de 21h30 GMT :** GitHub, Settings, Secrets and
   variables, Actions. Ajouter :
   - `R2_ACCESS_KEY_ID`
   - `R2_SECRET_ACCESS_KEY`
   - `R2_BUCKET` = `turf-engine-data`

   (`CLOUDFLARE_ACCOUNT_ID` existe déjà et sert à construire l'adresse R2.)
6. **Vérifier la passe suivante,** ou la lancer via *Run workflow* :
   - les logs affichent `R2_SEED_PENDING` puis `R2_SEED_OK` ;
   - le bucket contient les 3 objets ;
   - le site continue de se mettre à jour ;
   - les commits `Auto-sync` ne contiennent plus `turf_bench.db`.
7. **Optionnel :** règle de cycle de vie R2 sur le préfixe `backups/`, suppression après 90 jours.

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

1. Récupérer la base à jour :
   `python -m turf_lab.r2_store fetch --db turf_bench.db` (ou la télécharger depuis le tableau de bord R2).
2. La committer sur `main` à la place de la copie figée.
3. Seulement ensuite, supprimer les 3 secrets R2 : le mode legacy reprend.

> ⚠️ Supprimer les secrets **sans** les étapes 1 et 2 ferait repartir le
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
  - le métronome n8n est retiré.
- **3** : dépôt privé, et purge éventuelle de l'historique Git. Cette purge est
  une opération **manuelle** et concertée.
