# Export JSON des résultats — contrat de données (schéma 1.1)

Flux de résultats publié par **Elite Turf** (prono.elite-turf.fr) à partir du flux public PMU, pour un raccordement partenaire. Ce document est le contrat : tout ce qui n'y figure pas n'est pas garanti.

## Où lire

| Ressource | URL | Rafraîchissement |
|---|---|---|
| Manifeste des journées | `https://prono.elite-turf.fr/resultats/index.json` | à chaque passe (≈ toutes les 15 min de 09h00 à 21h00 UTC, plus 08h30 et 21h30) |
| Journée | `https://prono.elite-turf.fr/resultats/AAAA-MM-JJ.json` | idem, pour **toutes** les journées présentes en base (depuis le 25/08/2026) |
| Inventaire des corrections | `https://prono.elite-turf.fr/resultats/corrections.json` | idem ; chaque événement `CORRECTION_CLASSEMENT` / `ANNULATION` avec versions, classements et incidents avant/après |

Tout est servi en **HTTPS** (Cloudflare Pages). Aucune authentification ; usage raisonnable attendu (une lecture du manifeste puis de la journée par cycle de sondage, pas plus d'une fois par minute).

## Garanties

1. **Origine vérifiée.** Chaque lecture du flux PMU est faite en HTTPS avec certificat et nom d'hôte vérifiés. Une lecture dont le certificat ne peut être validé n'est **jamais** ingérée (journal `TLS_ERROR`), le flux préfère le silence à une donnée d'origine non prouvée.
2. **Arrivée définitive contrôlée.** `statut.code = "DEFINITIVE"` uniquement lorsque le flux PMU le déclare (`arriveeDefinitive` / `ARRIVEE_DEFINITIVE*`). Un classement visible avant cette déclaration est exposé comme `"PROVISOIRE"`. Une arrivée définitive ne redevient jamais provisoire.
3. **Finalité distinguée.** `statut.finalite` sépare `VERIFIEE_PMU` (finalité vue par le lecteur actuel, avec le drapeau PMU ; `horodatages.verifiee_le_utc` renseigné) de `LEGACY_NON_VERIFIEE` (arrivée tenue pour définitive par l'ancien système, jamais re-vérifiée ; `definitive_depuis_utc` **null**, horodatage ancien conservé dans `enregistree_ancien_systeme_utc`). Aucune date historique n'est présentée comme une validation nouvelle. La première relecture vérifiée d'une ligne héritée, même sans changement de classement, crée une version `CONFIRMATION_DEFINITIVE` qui enregistre la source, l'URL et le statut PMU : c'est la preuve de revalidation.
4. **Corrections suivies.** Toute modification après première publication crée une **version** ; l'historique complet est conservé (`correction.historique`) et l'inventaire global est publié (`corrections.json`). `nb_corrections` ne compte que les changements d'un rang déjà publié (réclamation, déclassement, disqualification finale) — un simple complément de classement (rangs supplémentaires) incrémente `version` mais pas `nb_corrections`. Un cheval retiré du classement **et** expliqué par un incident ou une non-partance est une correction (le cheval sort, l'incident reste) ; un classement plus court **sans** explication est une source partielle ou périmée, ignorée. **Information monotone** : un incident ou une non-partance déjà publiés ne disparaissent que si le cheval réapparaît au classement (disqualification annulée) — une lecture du flux sans le bloc `incidents` (variante de cache PMU) ne crée ni version ni correction. Le 10/09/2026, avant cette règle, sept courses de Mauquenchy ont reçu des versions techniques alternées (perte / retour des mêmes incidents) ; elles ont été retirées et la réparation est tracée par une version `REPARATION_OSCILLATION_INCIDENTS`, aucune correction réelle n'a été touchée.
5. **Classement PMU lu correctement.** Rangs de compétition avec ex æquo préservés (`dead_heat`), chevaux non classés (disqualifiés, arrêtés, tombés…) dans `non_classes` avec le motif PMU, non-partants dans `non_partants`.
6. **Intégrité et version déployée.** `empreinte_sha256` = SHA-256 du tableau `courses` (ou `corrections`) sérialisé en JSON compact, clés triées, UTF-8 (`json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`). `version_code.commit` = SHA du commit GitHub qui a produit le fichier (null en exécution locale).

## Fichier journée

```json
{
  "schema_version": "1.1",
  "fournisseur": "Elite Turf — prono.elite-turf.fr",
  "origine": "PMU (flux public turfinfo, lecture HTTPS vérifiée)",
  "version_code": {"commit": "<sha du commit GitHub>", "run_id": "<id du run Actions>", "depot": "Moriah12783/turf-engine"},
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
| `identite.heure_depart_utc` | ISO-8601 `Z` **ou null** | Heure de départ programmée (UTC), renseignée **uniquement** depuis `heureDepart` du flux PMU (`heure_depart_source = "PMU_HEUREDEPART"`). Null si le flux ne l'a pas fournie ; jamais dérivée de l'affichage ni d'un horaire standard. Les courses relues par la re-vérification reçoivent leur heure depuis la source |
| `identite.heure_depart_source` | `"PMU_HEUREDEPART"` ou null | Provenance de l'heure UTC |
| `identite.partants_declares`, `identite.partants_actifs` | int | Déclarés au programme / hors non-partants |
| `statut.code` | enum | `EN_ATTENTE` · `PROVISOIRE` · `DEFINITIVE` · `ANNULEE` |
| `statut.definitive`, `statut.annulee` | bool | Raccourcis |
| `statut.finalite` | `VERIFIEE_PMU` · `LEGACY_NON_VERIFIEE` · null | Voir garantie 3. Null hors `DEFINITIVE` |
| `statut.pmu_statut` | string | Statut PMU brut (`ARRIVEE_DEFINITIVE_COMPLETE`, `ARRIVEE_PROVISOIRE`, `FIN_COURSE`, `COURSE_ANNULEE`…) |
| `classement[]` | `{rang, num, nom, dead_heat}` | Rangs de compétition (après un ex æquo au 2ᵉ rang, le suivant est 4ᵉ) |
| `arrivee[]` | int[] | Numéros classés, dans l'ordre (ex æquo voisins, numéro croissant) — pratique pour un affichage « 14-3-5-6-12 » |
| `non_classes[]` | `{num, nom, incident}` | Motif PMU brut, ex. `DISQUALIFIE_POUR_ALLURE_IRREGULIERE`, `ARRETE`, `TOMBE` |
| `non_partants[]` | `{num, nom}` | |
| `rapports` | objet | Dividendes **officiels** PMU pour 1 € (`simple_gagnant`, `simple_place`, `autres[]`) ; `disponibles=false` tant que non publiés. Jamais estimés. |
| `source.canal` | enum | `PMU_PROGRAMME` (objet course du programme) · `PMU_COURSE` (endpoint course) · `PMU_PARTICIPANTS` (repli, provisoire au mieux) · `PMU_LEGACY` (ligne antérieure au versionnage, re-vérifiée à la première relecture) |
| `source.url_origine` | URL | Ressource PMU lue |
| `horodatages.premiere_lecture_utc` | ISO `Z` | Première publication par Elite Turf |
| `horodatages.definitive_depuis_utc` | ISO `Z` / null | Première lecture **vérifiée** de la finalité (null pour `LEGACY_NON_VERIFIEE`) |
| `horodatages.verifiee_le_utc` | ISO `Z` / null | Identique à `definitive_depuis_utc` (explicite) |
| `horodatages.enregistree_ancien_systeme_utc` | ISO `Z` / null | Horodatage de l'ancien système pour une ligne héritée |
| `horodatages.derniere_modification_utc` | ISO `Z` | Dernière nouvelle version |
| `horodatages.derniere_verification_utc` | ISO `Z` | Dernière relecture du flux (même sans changement) |
| `correction.version` | int | Version courante (0 = aucune arrivée) |
| `correction.nb_corrections` | int | Corrections de rang déjà publié |
| `correction.historique[]` | `{version, statut, raison, horodatage_utc, source, classement}` | Raisons : `INITIAL`, `PROVISOIRE_VERS_DEFINITIVE`, `COMPLETION`, `CONFIRMATION_DEFINITIVE`, `CORRECTION_CLASSEMENT`, `ANNULATION`, `MIGRATION_LEGACY`, `REPARATION_OSCILLATION_INCIDENTS` (réparation technique tracée du 10/09/2026, voir garantie 4) |

## Inventaire des corrections (`corrections.json`)

`{schema_version, version_code, genere_le_utc, nb_corrections, compte_par_raison, corrections[]}` où chaque événement porte `course_id`, `date`, `raison`, `version_avant`/`version_apres`, `statut_avant`/`statut_apres`, `classement_avant`/`classement_apres`, `incidents_avant`/`incidents_apres`, `source_avant`/`source_apres`, `horodatage_utc`. Le manifeste (`index.json → inventaire_corrections`) en donne le nombre et l'empreinte.

## Sondage recommandé côté partenaire

1. Lire `index.json` ; pour la journée visée, comparer `empreinte_sha256` à la dernière valeur connue. Inchangée → rien à faire.
2. Sinon lire `AAAA-MM-JJ.json` ; pour chaque course, comparer `correction.version` à la version stockée. Supérieure → appliquer la nouvelle version (le `classement` complet remplace l'ancien), consigner `raison`.
3. Ne considérer un résultat comme exploitable que si `statut.definitive = true` **et** `statut.finalite = "VERIFIEE_PMU"`. Un `PROVISOIRE` peut être affiché comme tel, jamais comme définitif ; un `LEGACY_NON_VERIFIEE` est un historique, pas une preuve prospective.
4. Une course `ANNULEE` doit être retirée de tout calcul ; son éventuel classement antérieur reste dans `historique`.

## Périmètre et limites

- Réunions **françaises** du flux PMU (parité LONACI). Les réunions étrangères ne sont pas ingérées.
- Les horodatages sont ceux d'Elite Turf (le flux PMU ne publie pas d'heure de modification) ; la latence habituelle entre la déclaration PMU et la publication est celle du cycle de passes (≤ 15 min en journée).
- Le schéma 1.1 (10/09/2026) ajoute, sans rupture, `version_code`, `statut.finalite`, `identite.heure_depart_source`, `horodatages.verifiee_le_utc` / `enregistree_ancien_systeme_utc`, la raison `CONFIRMATION_DEFINITIVE` et `corrections.json` ; il rend explicite la nullabilité de `identite.heure_depart_utc` et redéfinit `definitive_depuis_utc` comme la date de première vérification (null pour l'héritage). Toute suppression ou changement de sens ultérieur incrémentera `schema_version`.
- Ce flux est une **restitution** d'informations publiques PMU à des fins d'information ; il ne constitue ni une source officielle de paiement ni un conseil de jeu.
