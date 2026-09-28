"""Lecture SCELLÉE de l'ombre du fondamental dans NVE.

Règle pré-enregistrée : docs/OMBRE_FONDAMENTAL.md (datée, committée avant
le premier jour d'ombre). Ce script en reprend les constantes (turf_lab/
ombre.py, vérifiées par un test) et refuse toute lecture intermédiaire.
UNE SEULE lecture, à l'édition du matin :
  - sur les 1 000 PREMIÈRES éditions éligibles portant l'ombre (dans l'ordre
    des verrous), au plus tôt le lendemain de la 1 000e ;
  - ou, si les 1 000 ne sont pas atteintes, sur toutes les éditions des
    35 premiers jours, à partir du 36e jour.
Avant : seul le COMPTEUR est rendu. Seules comptent les éditions portant les
mêmes ``model_version``, ``nve_version`` et recette. L'horloge part de
DEBUT_OMBRE (jour 1) ; un changement de code (bug bloquant seulement) remet
à zéro le compteur ET l'horloge, qui repart de la première édition de la
version corrigée.

Usage : python -m turf_lab.ombre_lecture --banc copie_turf_bench.db
"""

import argparse
import hashlib
import json
import math
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from turf_lab import ombre

META_OMBRE = "ombre_fondamental"        # {recette, model_version, nve_version, train_until, probabilities, selection, fondamental}
DEBUT_OMBRE: Optional[str] = None       # premier jour d'ombre (AAAA-MM-JJ), inscrit au gel de la règle
ENGINE = "NEW_VALUE_ENGINE"
_RACE_ID = re.compile(r"^R(\d+)C(\d+)_(\d{2})(\d{2})(\d{4})_(.*)$")


def _log(tag: str, payload: Dict[str, Any]) -> None:
    print(tag + " " + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def _race_day_meeting(race_id: str) -> Optional[Tuple[str, Tuple[str, int, str]]]:
    m = _RACE_ID.match(str(race_id or ""))
    if not m:
        return None
    day = f"{m.group(5)}-{m.group(4)}-{m.group(3)}"
    return day, (day, int(m.group(1)), m.group(6))


def load(bench_path: str, debut: str) -> Tuple[Dict[str, Dict[str, Dict[str, Any]]], Dict[str, Dict[str, Any]]]:
    """Éditions NVE (horizon -> race_id -> édition) depuis ``debut`` et
    arrivées DÉFINITIVES (race_id -> gagnant, trio de tête)."""
    conn = sqlite3.connect(f"file:{bench_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    editions: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    for row in conn.execute("SELECT race_id, horizon, lock_time, selection_json, probabilities_json, metadata_json "
                            "FROM predictions WHERE engine_name = ?", (ENGINE,)):
        parsed = _race_day_meeting(row["race_id"])
        if parsed is None or parsed[0] < debut:
            continue
        try:
            probs = {int(k): float(v) for k, v in json.loads(row["probabilities_json"] or "{}").items()}
            meta = json.loads(row["metadata_json"] or "{}")
            selection = [int(x) for x in json.loads(row["selection_json"] or "[]")]
        except (TypeError, ValueError):
            continue
        editions[row["horizon"]][row["race_id"]] = {
            "race_id": row["race_id"], "day": parsed[0], "reunion": parsed[1], "lock_time": row["lock_time"],
            "probs": probs, "meta": meta if isinstance(meta, dict) else {}, "selection": selection}
    results: Dict[str, Dict[str, Any]] = {}
    for row in conn.execute("SELECT race_id, statut, ranking_json, arrival_order_json FROM race_results"):
        if (row["statut"] or "DEFINITIVE") != "DEFINITIVE":
            continue
        try:
            ranking = json.loads(row["ranking_json"]) if row["ranking_json"] else [
                {"rang": i + 1, "num": n} for i, n in enumerate(json.loads(row["arrival_order_json"] or "[]"))]
        except (TypeError, ValueError):
            continue
        firsts = [int(r["num"]) for r in ranking if int(r.get("rang") or 0) == 1]
        top3 = [int(r["num"]) for r in ranking if 1 <= int(r.get("rang") or 0) <= 3]
        results[row["race_id"]] = {"gagnant": firsts[0] if len(firsts) == 1 else None,
                                   "dead_heat": len(firsts) > 1,
                                   "tierce": top3 if len(top3) >= 3 else None}
    conn.close()
    return editions, results


def is_eligible(ed: Dict[str, Any]) -> bool:
    """Édition à marché réel de la production gelée (poids marché 0,90)."""
    cal = ed["meta"].get("market_calibration") or {}
    return cal.get("applied") is True and abs(float(cal.get("market_weight") or 0.0) - ombre.POIDS_MARCHE) < 1e-9


def shadow_of(ed: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Ombre archivée et complète : une probabilité (arrondie à 4 décimales,
    mêmes clés que ``probabilities_json``) par partant publié, et la sélection
    de l'ombre (10 chevaux, tous les partants s'il y en a moins ; même code
    et même départage que la production). Sinon : pas d'ombre pour cette
    édition (jamais d'ombre partielle)."""
    sh = ed["meta"].get(META_OMBRE)
    if not isinstance(sh, dict):
        return None
    try:
        probs = {int(k): float(v) for k, v in (sh.get("probabilities") or {}).items()}
        selection = [int(x) for x in sh.get("selection") or []]
    except (TypeError, ValueError):
        return None
    if not probs or set(probs) != set(ed["probs"]) or any(v < 0 for v in probs.values()) or sum(probs.values()) <= 0:
        return None
    if len(selection) < min(8, len(probs)) or not set(selection) <= set(probs):
        return None
    return {"probs": probs, "selection": selection,
            "cle": (sh.get("model_version"), sh.get("nve_version"), sh.get("recette"))}


def _secondary(sample: List[Dict[str, Any]], results) -> Dict[str, Any]:
    """Les deux tests du matin : gagnant et tiercé dans les 8, ombre (sa
    sélection archivée) face à l'édition publiée, IC95 apparié par réunion."""
    rows = {"gagnant": [], "tierce": []}
    for r in sample:
        res = results[r["race_id"]]
        if res["gagnant"] is not None:
            rows["gagnant"].append((int(ombre.dans_les_8(r["selection_publiee"], [res["gagnant"]])),
                                    int(ombre.dans_les_8(r["selection_ombre"], [res["gagnant"]])), r["reunion"]))
        if res["tierce"]:
            rows["tierce"].append((int(ombre.dans_les_8(r["selection_publiee"], res["tierce"])),
                                   int(ombre.dans_les_8(r["selection_ombre"], res["tierce"])), r["reunion"]))
    tests: Dict[str, Any] = {}
    for crit, pairs in rows.items():
        name = f"{crit}_dans_8_T_MATIN"
        if len(pairs) < 2:
            tests[name] = {"editions": len(pairs), "verdict": "NON_MESURABLE"}        # compte comme un échec
            continue
        diff = [s_ - p_ for p_, s_, _ in pairs]
        lo, hi = ombre.intervalle(diff, [c for _, _, c in pairs], 0.95)
        tests[name] = {"editions": len(pairs), "publie": round(float(np.mean([p_ for p_, _, _ in pairs])), 4),
                       "ombre": round(float(np.mean([s_ for _, s_, _ in pairs])), 4),
                       "ecart": round(float(np.mean(diff)), 4), "ic95": [round(lo, 4), round(hi, 4)],
                       "verdict": "OK" if lo >= ombre.SEUIL_NON_DEGRADATION else "DEGRADATION"}
    return tests


def reading(sample: List[Dict[str, Any]], coverage: float, results, mode: str) -> Dict[str, Any]:
    """La lecture unique : critère principal, deux tests du matin, couverture, décision."""
    deltas = [r["delta"] for r in sample]
    clusters = [r["reunion"] for r in sample]
    lo, hi = ombre.intervalle(deltas, clusters, ombre.NIVEAU_LECTURE)
    lo_i, hi_i = ombre.intervalle(deltas, clusters, ombre.NIVEAU_INUTILITE)
    secondary = _secondary(sample, results)
    failures = [k for k, v in secondary.items() if v["verdict"] != "OK"]
    if coverage < ombre.COUVERTURE_MIN:
        decision = "DECISION_SUSPENDUE_COUVERTURE"
    elif hi_i < 0:
        decision = "ARRET_INUTILITE"
    elif lo > 0 and not failures:
        decision = "PASSAGE_EN_PRODUCTION_DU_MATIN_SUR_DECISION_ECRITE_DE_STEPH"
    elif lo > 0:
        decision = "PASSAGE_BLOQUE_CRITERE_SECONDAIRE"
    else:
        decision = "FIN_SANS_PREUVE_NVE_DEGELE"
    # Empreinte des courses retenues et de leurs écarts : la sortie archivée
    # de la lecture unique reste vérifiable par une relance.
    empreinte = hashlib.sha256(json.dumps([[r["race_id"], round(r["delta"], 6)] for r in sample]).encode()).hexdigest()
    return {"mode": mode, "editions": len(sample), "reunions": len(set(clusters)), "couverture": round(coverage, 4),
            "premier_jour": sample[0]["day"], "dernier_jour": sample[-1]["day"],
            "delta_ll": round(float(np.mean(deltas)), 5), "ic95": [round(lo, 5), round(hi, 5)],
            "ic95_inutilite": [round(lo_i, 5), round(hi_i, 5)], "criteres_secondaires": secondary,
            "echecs_secondaires": failures, "decision": decision, "empreinte": empreinte}


def read(bench_path: str, debut: Optional[str] = None, today: Optional[str] = None) -> Dict[str, Any]:
    debut = debut or DEBUT_OMBRE
    if not debut:
        out = {"refus": "REGLE_NON_DATEE : DEBUT_OMBRE est inscrit au gel de la règle"}
        _log("OMBRE_REFUS", out)
        return out
    today = today or datetime.now(timezone.utc).date().isoformat()
    editions, results = load(bench_path, debut)
    matin = sorted((ed for ed in (editions.get("T_MATIN") or {}).values() if is_eligible(ed)),
                   key=lambda ed: (ed["lock_time"] or "", ed["race_id"]))
    shadows = [(i, sh) for i, sh in ((i, shadow_of(ed)) for i, ed in enumerate(matin)) if sh]
    if not shadows:
        out = {"debut": debut, "editions_eligibles": len(matin), "editions_avec_ombre": 0}
        _log("OMBRE_COMPTEUR", out)
        return out
    # Clé courante (model_version, nve_version, recette). Première version :
    # compteur, couverture et horloge partent de DEBUT_OMBRE (les nuits
    # manquées comptent). Après un correctif (nouvelle clé) : de la première
    # édition de la version corrigée.
    key, start, correctif = shadows[-1][1]["cle"], shadows[-1][0], False
    for i, sh in reversed(shadows):
        if sh["cle"] != key:
            correctif = True
            break
        start = i
    if not correctif:
        start = 0
    clock = matin[start]["day"] if correctif else debut
    fin = (date.fromisoformat(clock) + timedelta(days=ombre.DUREE_MAX_JOURS - 1)).isoformat()      # 35e jour
    lecture_max = (date.fromisoformat(clock) + timedelta(days=ombre.DUREE_MAX_JOURS)).isoformat()  # 36e jour
    rows, excl, seen = [], defaultdict(int), 0
    for ed in matin[start:]:
        if ed["day"] > fin:
            break
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
        sh = shadow_of(ed)
        if sh is None or sh["cle"] != key:
            excl["sans_ombre_complete"] += 1
            continue
        if key[2] != ombre.RECETTE:
            excl["recette_differente"] += 1
            continue
        w = res["gagnant"]
        if w not in ed["probs"]:
            excl["gagnant_hors_partants_au_verrou"] += 1
            continue
        rows.append({"race_id": ed["race_id"], "day": ed["day"], "reunion": ed["reunion"], "rang_eligible": seen,
                     "selection_publiee": ed["selection"] or sorted(ed["probs"], key=lambda n: -ed["probs"][n]),
                     "selection_ombre": sh["selection"],
                     "delta": math.log(max(sh["probs"][w], ombre.PLANCHER))
                     - math.log(max(ed["probs"][w], ombre.PLANCHER))})
    out: Dict[str, Any] = {"debut": debut, "horloge_depart": clock, "fin_des_35_jours": fin,
                           "lecture_au_plus_tard": lecture_max,
                           "model_version": key[0], "nve_version": key[1], "recette": key[2],
                           "editions_eligibles": seen, "editions_avec_ombre": len(rows), "exclusions": dict(excl)}
    n = ombre.LECTURE
    if len(rows) >= n and today > rows[n - 1]["day"]:
        out["lecture"] = reading(rows[:n], n / rows[n - 1]["rang_eligible"], results, "1000_editions")
    elif len(rows) < n and today >= lecture_max and rows:
        out["lecture"] = reading(rows, len(rows) / max(seen, 1), results, "35_jours")
    else:
        out["prochaine_lecture"] = (f"le lendemain de la {n}e édition, ou le {lecture_max} au plus tard "
                                    f"({ombre.DUREE_MAX_JOURS + 1}e jour) ; aucune lecture intermédiaire")
    _log("OMBRE_LECTURE" if "lecture" in out else "OMBRE_COMPTEUR", out)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Lecture scellée de l'ombre du fondamental")
    parser.add_argument("--banc", default="turf_bench.db")
    args = parser.parse_args(argv)
    out = read(args.banc)
    return 2 if "refus" in out else 0


if __name__ == "__main__":
    sys.exit(main())
