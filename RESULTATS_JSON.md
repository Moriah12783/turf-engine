# Export JSON des résultats — contrat de données (schéma 1.0)

Flux de résultats publié par **Elite Turf** (prono.elite-turf.fr) à partir du flux public PMU, pour un raccordement partenaire. Ce document est le contrat : tout ce qui n'y figure pas n'est pas garanti.

## Où lire

| Ressource | URL | Rafraîchissement |
|---|---|---|
| Manifeste des journées | `https://prono.elite-turf.fr/resultats/index.json` | à chaque passe (≈ toutes les 15 min de 09h00 à 21h00 UTC, plus 08h30 et 21h30) |
| Journée | `https://prono.elite-turf.fr/resultats/AAAA-MM-JJ.json` | idem, pour les 8 derniers jours ; les journées plus anciennes restent servies telles quelles |

Tout est servi en **HTTPS** (Cloudflare Pages). Aucune authentification ; usage raisonnable attendu (une lecture du manifeste puis de la journée par cycle de sondage, pas plus d'une fois par minute).

## Garanties

1. **Origine vérifiée.** Chaque lecture du flux PMU est faite en HTTPS avec certificat et nom d'hôte vérifiés. Une lecture dont le certificat ne peut être validé n'est **jamais** ingérée (journal `TLS_ERROR`), le flux préfère le silence à une donnée d'origine non prouvée.
2. **Arrivée définitive contrôlée.** `statut.code = "DEFINITIVE"` uniquement lorsque le flux PMU le déclare (`arriveeDefinitive` / `ARRIVEE_DEFINITIVE*`). Un classement visible avant cette déclaration est exposé comme `"PROVISOIRE"`. Une arrivée définitive ne redevient jamais provisoire.
3. **Corrections suivies.** Toute modification après première publication crée une **version** ; l'historique complet est conservé (`correction.historique`). `nb_corrections` ne compte que les changements d'un rang déjà publié (réclamation, déclassement) — un simple complément de classement (rangs supplémentaires) incrémente `version` mais pas `nb_corrections`.
4. **Classement PMU lu correctement.** Rangs de compétition avec ex æquo préservés (`dead_heat`), chevaux non classés (disqualifiés, arrêtés, tombés…) dans `non_classes` avec le motif PMU, non-partants dans `non_partants`.
5. **Intégrité.** `empreinte_sha256` = SHA-256 du tableau `courses` sérialisé en JSON compact, clés triées, UTF-8 (`json.dumps(courses, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`). Recalculable côté consommateur.

## Fichier journée

```json
{
  "schema_version": "1.0",
  "fournisseur": "Elite Turf — prono.elite-turf.fr",
  "origine": "PMU (flux public turfinfo, lecture HTTPS vérifiée)",
  "date_course": "2026-09-09",
  "genere_le_utc": "2026-09-10T08:32:20Z",
  "nb_courses": 31,
  "compte_par_statut": {"EN_ATTENTE": 0, "PROVISOIRE": 0, "DEFINITIVE": 31, "ANNULEE": 0},
  "empreinte_sha256": "5d89a647…",
  "courses": [ { …voir ci-dessous… } ]
}
```

## Objet course

| Champ | Type | Sens |
|---|---|---|
| `course_id` | string | Identifiant stable Elite Turf : `R{réunion}C{course}_{JJMMAAAA}_{HIPPODROME}` |
| `identite.date` | `AAAA-MM-JJ` | Date de la course |
| `identite.reunion`, `identite.course` | int | Numéros PMU |
| `identite.code` | string | `R1C1` |
| `identite.hippodrome`, `identite.libelle`, `identite.discipline`, `identite.distance_m` | string / int | Identité PMU (discipline normalisée : `TROT_ATTELE`, `TROT_MONTE`, `PLAT`, `OBSTACLE_HAIE`, `OBSTACLE_STEEPLE`, `OBSTACLE_CROSS`) |
| `identite.heure_depart_utc` | ISO-8601 `Z` | Heure de départ programmée (UTC) |
| `identite.partants_declares`, `identite.partants_actifs` | int | Déclarés au programme / hors non-partants |
| `statut.code` | enum | `EN_ATTENTE` · `PROVISOIRE` · `DEFINITIVE` · `ANNULEE` |
| `statut.definitive`, `statut.annulee` | bool | Raccourcis |
| `statut.pmu_statut` | string | Statut PMU brut (`ARRIVEE_DEFINITIVE_COMPLETE`, `ARRIVEE_PROVISOIRE`, `FIN_COURSE`, `COURSE_ANNULEE`…) |
| `classement[]` | `{rang, num, nom, dead_heat}` | Rangs de compétition (après un ex æquo au 2ᵉ rang, le suivant est 4ᵉ) |
| `arrivee[]` | int[] | Numéros classés, dans l'ordre (ex æquo voisins, numéro croissant) — pratique pour un affichage « 14-3-5-6-12 » |
| `non_classes[]` | `{num, nom, incident}` | Motif PMU brut, ex. `DISQUALIFIE_POUR_ALLURE_IRREGULIERE`, `ARRETE`, `TOMBE` |
| `non_partants[]` | `{num, nom}` | |
| `rapports` | objet | Dividendes **officiels** PMU pour 1 € (`simple_gagnant`, `simple_place`, `autres[]`) ; `disponibles=false` tant que non publiés. Jamais estimés. |
| `source.canal` | enum | `PMU_PROGRAMME` (objet course du programme) · `PMU_COURSE` (endpoint course) · `PMU_PARTICIPANTS` (repli, provisoire au mieux) · `PMU_LEGACY` (ligne antérieure au versionnage, re-vérifiée à la première relecture) |
| `source.url_origine` | URL | Ressource PMU lue |
| `horodatages.premiere_lecture_utc` | ISO `Z` | Première publication par Elite Turf |
| `horodatages.definitive_depuis_utc` | ISO `Z` / null | Passage en définitive |
| `horodatages.derniere_modification_utc` | ISO `Z` | Dernière nouvelle version |
| `horodatages.derniere_verification_utc` | ISO `Z` | Dernière relecture du flux (même sans changement) |
| `correction.version` | int | Version courante (0 = aucune arrivée) |
| `correction.nb_corrections` | int | Corrections de rang déjà publié |
| `correction.historique[]` | `{version, statut, raison, horodatage_utc, source, classement}` | Raisons : `INITIAL`, `PROVISOIRE_VERS_DEFINITIVE`, `COMPLETION`, `CORRECTION_CLASSEMENT`, `ANNULATION`, `MIGRATION_LEGACY` |

## Sondage recommandé côté partenaire

1. Lire `index.json` ; pour la journée visée, comparer `empreinte_sha256` à la dernière valeur connue. Inchangée → rien à faire.
2. Sinon lire `AAAA-MM-JJ.json` ; pour chaque course, comparer `correction.version` à la version stockée. Supérieure → appliquer la nouvelle version (le `classement` complet remplace l'ancien), consigner `raison`.
3. Ne considérer un résultat comme exploitable que si `statut.definitive = true`. Un `PROVISOIRE` peut être affiché comme tel, jamais comme définitif.
4. Une course `ANNULEE` doit être retirée de tout calcul ; son éventuel classement antérieur reste dans `historique`.

## Périmètre et limites

- Réunions **françaises** du flux PMU (parité LONACI). Les réunions étrangères ne sont pas ingérées.
- Les horodatages sont ceux d'Elite Turf (le flux PMU ne publie pas d'heure de modification) ; la latence habituelle entre la déclaration PMU et la publication est celle du cycle de passes (≤ 15 min en journée).
- Le schéma 1.0 peut recevoir des champs supplémentaires sans changement de version ; toute suppression ou changement de sens incrémentera `schema_version`.
- Ce flux est une **restitution** d'informations publiques PMU à des fins d'information ; il ne constitue ni une source officielle de paiement ni un conseil de jeu.
