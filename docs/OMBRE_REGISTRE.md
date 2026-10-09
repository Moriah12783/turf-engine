# Registre de l'ombre du fondamental

Journal daté de tout ce qui entoure l'ombre sans toucher à la règle gelée
(`docs/OMBRE_FONDAMENTAL.md`). La règle, le code couvert par `model_version`,
`NVE_VERSION` et la chaîne du matin ne changent pas pendant l'ombre. Ce qui est
ajouté autour (contrôles, horloge) ou constaté en route est inscrit ici, avec
sa date, sa référence et son effet sur la mesure. Agrégats seulement : rien de
l'ombre n'y figure avant la lecture.

| Date (UTC) | Événement | Référence | Effet sur la mesure |
|---|---|---|---|
| 06/10, 22h39 | Déploiement de `nve-2026-10-07` : partie moteur et partie daily_sync, code identique à la répétition | PR #22, `bab71ce0e5d0` | — |
| 07/10 | Journée de rodage, sans ombre : 24 éditions du matin, `TABLE_ABSENTE` sur les 24 | journaux | — |
| 07/10, 07h49 | Gel de la règle, `DEBUT_OMBRE` = 2026-10-08, sur la décision écrite de Steph et le feu vert écrit du dev NVE | PR #23, `d8cc8ec4` | — |
| 07/10 | Engagement écrit du dev Bases : son code ne lit pas `metadata_json` | confirmation à Steph | — |
| 07/10, 08h14 | Garde de confidentialité (lecture seule, hors chaîne) | PR #24, `903f17e5` | Aucun |
| 08/10 | Jour 1 : 30 éditions éligibles sur 30 avec une ombre complète | compteur | — |
| 08/10 | Constat : un cheval déclaré non-partant entre les passages de 05h05 et 05h50 garde sa ligne de 05h05 dans `fundamental_probs` (chemin absent de la répétition, où chaque passage partait d'une copie vierge) | journaux de nuit | Aucun : le moteur n'utilise que les partants valides et renormalise. Correctif après la lecture : effacer les lignes du jour avant de les réécrire |
| 08/10, 09h31 | Compteur quotidien (lecture seule, aucune lecture de l'ombre possible) | PR #25, `20a04a96` | Aucun |
| 09/10 | Jour 2 : 31 éditions éligibles sur 31 avec une ombre complète ; couverture officielle de 47 sur 47 | compteur, lancé à la main à 15h15 (horaire GitHub en retard) | — |
| 09/10 | Horloge Cloudflare : la garde et le compteur sont lancés à 01h17 ; les créneaux de nuit de 05h05 et 05h50 ne changent pas | PR de l'horloge du 09/10 ; nouvelle version à inscrire après le redéploiement (précédente : `7853694e`) | Aucun |
