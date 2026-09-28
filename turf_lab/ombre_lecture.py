"""Lecture SCELLÉE de l'ombre du fondamental dans NVE.

Règle pré-enregistrée : docs/OMBRE_FONDAMENTAL.md (datée, committée avant
le premier jour d'ombre). Ce script en reprend les constantes (turf_lab/
ombre.py, vérifiées par un test) et refuse toute lecture intermédiaire :
tant que 1 000 éditions éligibles ne portent pas d'ombre, seul le COMPTEUR
est rendu. Les lectures portent toujours sur les 1 000 puis 2 300 PREMIÈRES
éditions (dans l'ordre des verrous) : relancer le script plus tard ne
change rien à une lecture déjà rendue.

Usage : python -m turf_lab.ombre_lecture --banc copie_turf_bench.db
"""

import argparse
import json
import math
import re
import sqlite3
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from turf_lab import ombre

META_OMBRE = "ombre_fondamental"        # métadonnées NVE : {recette, model_version, nve_version, train_until, probabilities}
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
                                   "tierce": top3 if len(top3) >= 3 else None}
    conn.close()
    return editions, results


def is_eligible(ed: Dict[str, Any]) -> bool:
    """Édition à marché réel de la production gelée (poids marché 0,90)."""
    cal = ed["meta"].get("market_calibration") or {}
    return cal.get("applied") is True and abs(float(cal.get("market_weight") or 0.0) - ombre.POIDS_MARCHE) < 1e-9


def shadow_of(ed: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Ombre archivée, complète (une probabilité par partant publié)."""
    sh = ed["meta"].get(META_OMBRE)
    if not isinstance(sh, dict):
        return None
    try:
        probs = {int(k): float(v) for k, v in (sh.get("probabilities") or {}).items()}
    except (TypeError, ValueError):
        return None
    if not probs or set(probs) != set(ed["probs"]) or any(v <= 0 for v in probs.values()):
        return None                                            # jamais d'ombre partielle
    return {"probs": probs, "pair": (sh.get("model_version"), sh.get("nve_version")), "recette": sh.get("recette")}


def shadow_top8(ed: Dict[str, Any], sh: Dict[str, Any]) -> List[int]:
    """Les 8 plus fortes probabilités de l'ombre ; à égalité, l'ordre de la
    sélection publiée, puis le numéro."""
    rank = {n: i for i, n in enumerate(ed["selection"])}
    return sorted(sh["probs"], key=lambda n: (-sh["probs"][n], rank.get(n, 99), n))[:8]


def _secondary(sample_ids: List[str], editions: Dict[str, Dict[str, Dict[str, Any]]], results, pair) -> Dict[str, Any]:
    tests: Dict[str, Any] = {}
    for horizon in ombre.HORIZONS_SECONDAIRES:
        eds = editions.get(horizon) or {}
        rows = {"gagnant": [], "tierce": []}
        for race_id in sample_ids:
            ed = eds.get(race_id)
            res = results.get(race_id)
            sh = shadow_of(ed) if ed else None
            if sh is None or sh["pair"] != pair or not ed["selection"]:
                continue
            mine = shadow_top8(ed, sh)
            if res["gagnant"] is not None:
                rows["gagnant"].append((int(ombre.dans_les_8(ed["selection"], [res["gagnant"]])),
                                        int(ombre.dans_les_8(mine, [res["gagnant"]])), ed["reunion"]))
            if res["tierce"]:
                rows["tierce"].append((int(ombre.dans_les_8(ed["selection"], res["tierce"])),
                                       int(ombre.dans_les_8(mine, res["tierce"])), ed["reunion"]))
        for crit, pairs in rows.items():
            name = f"{crit}_dans_8_{horizon}"
            if len(pairs) < 2:
                tests[name] = {"editions": len(pairs), "verdict": "NON_MESURABLE"}    # compte comme un échec
                continue
            diff = [s - p for p, s, _ in pairs]
            clusters = [c for _, _, c in pairs]
            lo, hi = ombre.intervalle(diff, clusters, 0.95)
            tests[name] = {"editions": len(pairs), "publie": round(float(np.mean([p for p, _, _ in pairs])), 4),
                           "ombre": round(float(np.mean([s for _, s, _ in pairs])), 4),
                           "ecart": round(float(np.mean(diff)), 4), "ic95": [round(lo, 4), round(hi, 4)],
                           "verdict": "OK" if lo >= ombre.SEUIL_NON_DEGRADATION else "DEGRADATION"}
    return tests


def reading(rows: List[Dict[str, Any]], n: int, level: float, coverage: float,
            editions, results, pair) -> Dict[str, Any]:
    """Une lecture sur les ``n`` premières éditions : critère principal,
    critères secondaires, couverture, décision."""
    sample = rows[:n]
    deltas = [r["delta"] for r in sample]
    clusters = [r["reunion"] for r in sample]
    mean = float(np.mean(deltas))
    lo, hi = ombre.intervalle(deltas, clusters, level)
    lo95, hi95 = ombre.intervalle(deltas, clusters, ombre.NIVEAU_INUTILITE)
    secondary = _secondary([r["race_id"] for r in sample], editions, results, pair)
    failures = [k for k, v in secondary.items() if v["verdict"] != "OK"]
    if coverage < ombre.COUVERTURE_MIN:
        decision = "DECISION_SUSPENDUE_COUVERTURE"
    elif hi95 < 0:
        decision = "ARRET_INUTILITE"
    elif lo > 0 and not failures:
        decision = "PASSAGE_EN_PRODUCTION_SUR_DECISION_ECRITE_DE_STEPH"
    elif lo > 0:
        decision = "PASSAGE_BLOQUE_CRITERE_SECONDAIRE"
    elif n < ombre.LECTURE_2:
        decision = "PROLONGATION_JUSQU_A_2300"
    else:
        decision = "FIN_SANS_PREUVE_DECISION_ECRITE_DE_STEPH"
    return {"editions": n, "reunions": len(set(clusters)), "couverture": round(coverage, 4),
            "delta_ll": round(mean, 5), f"ic{round(level * 100)}": [round(lo, 5), round(hi, 5)],
            "ic95_inutilite": [round(lo95, 5), round(hi95, 5)], "criteres_secondaires": secondary,
            "echecs_secondaires": failures, "decision": decision}


def read(bench_path: str, debut: Optional[str] = None) -> Dict[str, Any]:
    debut = debut or DEBUT_OMBRE
    if not debut:
        out = {"refus": "REGLE_NON_DATEE : DEBUT_OMBRE est inscrit au gel de la règle"}
        _log("OMBRE_REFUS", out)
        return out
    editions, results = load(bench_path, debut)
    matin = sorted((ed for ed in (editions.get("T_MATIN") or {}).values() if is_eligible(ed)),
                   key=lambda ed: (ed["lock_time"] or "", ed["race_id"]))
    # Compteur : il repart de la première édition qui porte le couple
    # (model_version, nve_version) courant — tout changement de code, d'un
    # côté ou de l'autre, remet le compteur à zéro.
    pairs = [(i, sh["pair"]) for i, sh in ((i, shadow_of(ed)) for i, ed in enumerate(matin)) if sh]
    if not pairs:
        out = {"debut": debut, "editions_eligibles": len(matin), "editions_avec_ombre": 0}
        _log("OMBRE_COMPTEUR", out)
        return out
    pair, start = pairs[-1][1], pairs[-1][0]
    for i, p in reversed(pairs):
        if p != pair:
            break
        start = i
    window = matin[start:]
    rows, eligible_seen, excl = [], [], defaultdict(int)
    for ed in window:
        res = results.get(ed["race_id"])
        if res is None or res["gagnant"] is None:
            excl["sans_arrivee_definitive"] += 1
            continue
        eligible_seen.append(ed["race_id"])
        sh = shadow_of(ed)
        if sh is None or sh["pair"] != pair:
            excl["sans_ombre"] += 1
            continue
        w = res["gagnant"]
        if w not in ed["probs"]:
            excl["gagnant_hors_partants_au_verrou"] += 1
            continue
        rows.append({"race_id": ed["race_id"], "reunion": ed["reunion"], "rang_eligible": len(eligible_seen),
                     "delta": math.log(max(sh["probs"][w], ombre.PLANCHER))
                     - math.log(max(ed["probs"][w], ombre.PLANCHER))})
    out: Dict[str, Any] = {"debut": debut, "model_version": pair[0], "nve_version": pair[1],
                           "editions_eligibles": len(eligible_seen), "editions_avec_ombre": len(rows),
                           "exclusions": dict(excl)}
    for n, level, name in ((ombre.LECTURE_1, ombre.NIVEAU_LECTURE_1, "lecture_1"),
                           (ombre.LECTURE_2, ombre.NIVEAU_LECTURE_2, "lecture_2")):
        if len(rows) < n:
            out["prochaine_lecture"] = f"{name} à {n} éditions ; aucune lecture intermédiaire"
            break
        coverage = n / rows[n - 1]["rang_eligible"]
        out[name] = reading(rows, n, level, coverage, editions, results, pair)
        if out[name]["decision"] in ("ARRET_INUTILITE", "PASSAGE_EN_PRODUCTION_SUR_DECISION_ECRITE_DE_STEPH"):
            break
    _log("OMBRE_LECTURE" if "lecture_1" in out else "OMBRE_COMPTEUR", out)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Lecture scellée de l'ombre du fondamental")
    parser.add_argument("--banc", default="turf_bench.db")
    args = parser.parse_args(argv)
    out = read(args.banc)
    return 2 if "refus" in out else 0


if __name__ == "__main__":
    sys.exit(main())
