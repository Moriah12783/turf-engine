"""Ombre du fondamental : la ligne du verrou ``T_MATIN`` (dev daily_sync).

Règle : docs/OMBRE_FONDAMENTAL.md, « Ce qui tourne », point 2. Livrée le
29/09/2026 pour la répétition générale (01-06/10), publiée le 07/10 avec la
partie moteur du dev NVE.

Au verrou ``T_MATIN`` SEULEMENT, les partants passés aux moteurs portent leur
probabilité fondamentale de la nuit, lue dans la table
``fundamental_probs(race_id, num, p, model_version, computed_at, train_until)``.

Contrat avec le moteur (dev NVE) : trois clés par partant, posées sur une
COPIE des partants passés aux moteurs, jamais sur les partants d'origine :

  CLE_P              -> p (float), tel qu'écrit la nuit
  CLE_MODEL_VERSION  -> model_version
  CLE_TRAIN_UNTIL    -> train_until

Cette ligne ne filtre rien et ne calcule rien. C'est le moteur qui décide :
ombre complète ou rien, marché réel exigé, renormalisation sur les partants
valides, cas comptés.

Garanties :

- table absente, aucune ligne pour la course, plusieurs ``model_version`` ou
  plusieurs ``train_until`` pour la course : les partants passent tels quels
  (la même liste), le cas est journalisé ;
- les partants d'origine ne sont jamais modifiés. Ils servent aux horizons
  suivants de la même passe (T90 peut être dû dans la passe qui pose
  T_MATIN) et au pont RADAR_V4. Le moteur n'ayant aucune logique d'horizon,
  une probabilité laissée sur eux ferait calculer une ombre hors du matin ;
- aucune erreur ne remonte : le verrou est toujours posé ;
- le journal ``FONDAMENTAL_VERROU`` ne contient aucune probabilité : les
  journaux GitHub Actions d'un dépôt public sont lisibles par tous, et rien
  de l'ombre ne doit être visible avant la lecture.
"""

from typing import Any, Dict, List, Tuple

HORIZON = "T_MATIN"
TABLE = "fundamental_probs"
CLE_P = "p_fondamental"
CLE_MODEL_VERSION = "fondamental_model_version"
CLE_TRAIN_UNTIL = "fondamental_train_until"
CLES = (CLE_P, CLE_MODEL_VERSION, CLE_TRAIN_UNTIL)


def porter_fondamental(db, race_id: str,
                       runners: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Partants du verrou T_MATIN -> (partants à passer aux moteurs, info de
    journal). Sans probabilités exploitables, renvoie ``runners`` lui-même."""
    info: Dict[str, Any] = {"race_id": race_id, "horizon": HORIZON}
    try:
        conn = db.get_connection()
        try:
            present = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                                   (TABLE,)).fetchone()
            if not present:
                info["statut"] = "TABLE_ABSENTE"
                return runners, info
            rows = [tuple(r) for r in conn.execute(
                f"SELECT num, p, model_version, train_until FROM {TABLE} WHERE race_id = ?", (race_id,))]
        finally:
            conn.close()
    except Exception as exc:  # jamais d'erreur remontée au verrou
        info.update(statut="ERREUR", erreur=str(exc)[:200])
        return runners, info

    if not rows:
        info["statut"] = "AUCUNE_LIGNE"
        return runners, info
    versions = sorted({str(r[2]) for r in rows})
    if len(versions) > 1:
        info.update(statut="VERSIONS_MULTIPLES", versions=versions)
        return runners, info
    trains = sorted({str(r[3]) for r in rows})
    if len(trains) > 1:
        info.update(statut="TRAIN_UNTIL_MULTIPLES", model_version=versions[0], train_until=trains)
        return runners, info

    version, train_until = versions[0], trains[0]
    probs = {int(r[0]): float(r[1]) for r in rows}
    out: List[Dict[str, Any]] = []
    portes = 0
    for r in runners:
        c = dict(r)
        try:
            num = int(r.get("num"))
        except (TypeError, ValueError):
            num = None
        if num in probs:
            c[CLE_P] = probs[num]
            c[CLE_MODEL_VERSION] = version
            c[CLE_TRAIN_UNTIL] = train_until
            portes += 1
        out.append(c)
    info.update(statut="PORTE", model_version=version, train_until=train_until,
                partants=len(runners), portes=portes, sans_p=len(runners) - portes, lignes=len(rows))
    return out, info
