"""Lecture SCELLÉE de la règle d'arrêt de la recette A en production.

Décision écrite de Steph du 13/11/2026 : recette A (0,90 marché + 0,10
fondamental) en production sur l'édition du matin à compter du 30/11/2026 ;
retour au moteur actuel si, sur 30 jours de production, TOUT l'intervalle de
confiance du gain passe sous zéro. Règle détaillée et gelée :
docs/REGLE_ARRET.md (constantes vérifiées par un test).

UNE SEULE lecture, à partir du LECTURE_LE (31e jour) : sur les éditions du
matin des 30 jours de course de la fenêtre (DEBUT_PRODUCTION à FIN_FENETRE
inclus). Avant : seul le COMPTEUR est rendu, sans aucun gain ni aucune
probabilité. La date de lecture n'est pas réglable en ligne de commande.

Gain d'une édition : log-vraisemblance du gagnant, recette A publiée moins le
témoin NVE archivé dans la même édition (``temoin_nve``), le critère de la
lecture de l'ombre du 12/11 avec les rôles échangés. Gain moyen et IC95 par
bootstrap de réunions entières (4 000 tirages, graine 20260928), comme le
12/11. Éligibilité identique à celle du 12/11 (édition du moteur NVE à marché
réel, poids 0,90) ; le gain ne porte que sur les éditions en recette A de la
clé gelée, avec un témoin complet. Une édition en repli publie NVE : son gain
est nul par construction, elle est comptée et exclue.

Décision : RETOUR_AU_MOTEUR_ACTUEL si la borne haute de l'IC95 est négative ;
sinon MAINTIEN_RECETTE_A. NON_MESURABLE (moins de deux réunions) : la règle
ne déclenche pas le retour, Steph décide par écrit.

Sortie : agrégats seulement (dépôt public) : aucune probabilité, aucun gain
course par course. Une empreinte des courses retenues et de leurs gains rend
la lecture vérifiable par une relance.

Usage : python -m turf_lab.regle_arret --banc copie_turf_bench.db
"""

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np

from turf_lab import ombre, ombre_lecture as lecture

# Règle d'arrêt gelée (docs/REGLE_ARRET.md). Contrat des marqueurs du moteur :
# docs/RECETTE_A_PRODUCTION.md (paquet moteur v2 du dev NVE).
DEBUT_PRODUCTION = "2026-11-30"              # premier jour de course en recette A (RECETTE_A_DEBUT)
DUREE_JOURS = 30                             # « sur 30 jours de production »
FIN_FENETRE = "2026-12-29"                   # 30e jour de course de la fenêtre (inclus)
LECTURE_LE = "2026-12-30"                    # 31e jour : la lecture unique, pas avant
HORIZON = "T_MATIN"                          # recette A sur l'édition du matin seulement
RECETTE_A = "A_lineaire_0.90_0.10"           # metadata.recette en recette A
RECETTE_REPLI = "REPLI_NVE"                  # metadata.recette en repli (NVE publié)
NVE_RECETTE_A = "nve-2026-11-30"             # metadata.nve_version en recette A
NVE_TEMOIN = "nve-2026-10-07"                # version NVE du témoin (le moteur actuel)
MODELE_ATTENDU = "fond-matin-e570d663793c"   # fondamental figé pendant la fenêtre
META_TEMOIN = "temoin_nve"                   # {nve_version, probabilities, selection}
NIVEAU = 0.95                                # IC95, comme la lecture du 12/11
REUNIONS_MIN = 2                             # en dessous : NON_MESURABLE

DECISION_RETOUR = "RETOUR_AU_MOTEUR_ACTUEL"
DECISION_MAINTIEN = "MAINTIEN_RECETTE_A"
DECISION_NON_MESURABLE = "NON_MESURABLE_DECISION_ECRITE_DE_STEPH"


def _log(tag: str, payload: Dict[str, Any]) -> None:
    print(tag + " " + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def temoin_de(ed: Dict[str, Any]) -> Optional[Dict[int, float]]:
    """Probabilités du témoin NVE archivé dans l'édition, si elles sont
    complètes : une par partant publié (mêmes clés que ``probabilities_json``),
    positives, de somme non nulle, de la version du moteur actuel. Sinon None
    (jamais de témoin partiel)."""
    tm = ed["meta"].get(META_TEMOIN)
    if not isinstance(tm, dict) or tm.get("nve_version") != NVE_TEMOIN:
        return None
    try:
        probs = {int(k): float(v) for k, v in (tm.get("probabilities") or {}).items()}
    except (TypeError, ValueError):
        return None
    if not probs or set(probs) != set(ed["probs"]) or any(v < 0 for v in probs.values()) or sum(probs.values()) <= 0:
        return None
    return probs


def _cle_gelee(meta: Dict[str, Any]) -> bool:
    return (meta.get("nve_version") == NVE_RECETTE_A
            and meta.get("fondamental_model_version") == MODELE_ATTENDU)


def collecter(bench_path: str) -> Dict[str, Any]:
    """Éditions du matin éligibles de la fenêtre, exclusions et lignes du gain."""
    editions, results = lecture.load(bench_path, DEBUT_PRODUCTION)
    matin = sorted((ed for ed in (editions.get(HORIZON) or {}).values()
                    if ed["day"] <= FIN_FENETRE and lecture.is_eligible(ed)),
                   key=lambda ed: (ed["lock_time"] or "", ed["race_id"]))
    rows: List[Dict[str, Any]] = []
    excl: Dict[str, int] = defaultdict(int)
    jours, seen, repli = set(), 0, 0
    for ed in matin:
        jours.add(ed["day"])
        res = results.get(ed["race_id"])
        if res is None:
            excl["sans_arrivee_definitive"] += 1
            continue
        if res["dead_heat"]:
            excl["dead_heat_premiere_place"] += 1
            continue
        if res["gagnant"] is None:
            excl["arrivee_sans_gagnant"] += 1
            continue
        seen += 1
        recette = ed["meta"].get("recette")
        if recette == RECETTE_REPLI:
            repli += 1
            continue
        if recette != RECETTE_A:
            excl["marqueur_absent_ou_inconnu"] += 1
            continue
        if not _cle_gelee(ed["meta"]):
            excl["cle_differente"] += 1
            continue
        temoin = temoin_de(ed)
        if temoin is None:
            excl["temoin_incomplet"] += 1
            continue
        w = res["gagnant"]
        if w not in ed["probs"]:
            excl["gagnant_hors_partants_au_verrou"] += 1
            continue
        rows.append({"race_id": ed["race_id"], "day": ed["day"], "reunion": ed["reunion"],
                     "gain": math.log(max(ed["probs"][w], ombre.PLANCHER))
                     - math.log(max(temoin[w], ombre.PLANCHER))})
    return {"rows": rows, "exclusions": dict(excl), "eligibles": seen, "repli": repli,
            "jours_avec_edition": len(jours)}


def lecture_unique(rows: List[Dict[str, Any]], eligibles: int) -> Dict[str, Any]:
    """La lecture : gain moyen, IC95 par réunion, décision de la règle."""
    reunions = len({r["reunion"] for r in rows})
    out: Dict[str, Any] = {"editions": len(rows), "reunions": reunions,
                           "part_recette_a": round(len(rows) / eligibles, 4) if eligibles else 0.0}
    if reunions < REUNIONS_MIN:
        out["decision"] = DECISION_NON_MESURABLE
        return out
    gains = [r["gain"] for r in rows]
    lo, hi = ombre.intervalle(gains, [r["reunion"] for r in rows], NIVEAU)
    out.update({"premier_jour": rows[0]["day"], "dernier_jour": rows[-1]["day"],
                "gain_ll": round(float(np.mean(gains)), 5), "ic95": [round(lo, 5), round(hi, 5)],
                "decision": DECISION_RETOUR if hi < 0 else DECISION_MAINTIEN,
                "empreinte": hashlib.sha256(json.dumps([[r["race_id"], round(r["gain"], 6)]
                                                        for r in rows]).encode()).hexdigest()})
    return out


def read(bench_path: str, today: Optional[str] = None) -> Dict[str, Any]:
    today = today or datetime.now(timezone.utc).date().isoformat()
    data = collecter(bench_path)
    out: Dict[str, Any] = {"fenetre": [DEBUT_PRODUCTION, FIN_FENETRE], "lecture_le": LECTURE_LE,
                           "cle": [RECETTE_A, NVE_RECETTE_A, MODELE_ATTENDU], "temoin": NVE_TEMOIN,
                           "jours_avec_edition": data["jours_avec_edition"],
                           "editions_eligibles": data["eligibles"], "editions_en_repli": data["repli"],
                           "editions_recette_a_lisibles": len(data["rows"]), "exclusions": data["exclusions"]}
    if today < LECTURE_LE:
        out["prochaine_lecture"] = f"le {LECTURE_LE}, une seule fois ; aucune lecture intermédiaire"
        _log("REGLE_ARRET_COMPTEUR", out)
        return out
    out["lecture"] = lecture_unique(data["rows"], data["eligibles"])
    _log("REGLE_ARRET_LECTURE", out)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Lecture scellée de la règle d'arrêt de la recette A")
    parser.add_argument("--banc", default="turf_bench.db")
    args = parser.parse_args(argv)
    read(args.banc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
