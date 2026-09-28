"""Labo des paris combinés : le public du PMU sous-paie-t-il certaines
combinaisons ? Test historique sur les rapports DÉFINITIFS du miroir Radar,
sans aucune capture nouvelle (feu vert de Steph du 28/09/2026).

Critère central (ne demande que le rapport de la combinaison gagnante) :
miser sur TOUTES les combinaisons d'un pari, chacune au prorata de la
probabilité que lui donne le modèle, rapporte q(c*) × D(c*) par euro, où c*
est la combinaison gagnante et D son rapport pour 1 €. La croissance
logarithmique moyenne G = E[log(q × D)] est positive si et seulement si ce
portefeuille bat la masse malgré le prélèvement. Contrôle intégré : au simple
gagnant, avec les probabilités du marché de clôture, G vaut exactement
−log(surround) (le prélèvement).

Probabilités des combinaisons : formule de Harville à partir des
probabilités de victoire de trois modèles, calculées hors échantillon :
marché de clôture, fondamental seul, combinaison marché + fondamental. Le
marché de clôture est une borne haute : on ne parie pas aux cotes finales.

Aussi : rendement des K combinaisons les plus probables selon le modèle
(mise de 1 € chacune, réglée au rapport officiel).

Journal : agrégats seulement (dépôt public) ; rapport complet sur R2 privé.
Usage : python -m turf_lab.combines_lab --db turf_history.db [--sans-envoi]
"""

import argparse
import itertools
import json
import math
import os
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from turf_lab import benter_lab as lab
from turf_lab import ombre

REPORT_PREFIX = "lab/combines/"
MODELS = ("marche", "combine", "fondamental")
MAX_FIELD = 20                          # au-delà, tenseurs trop lourds : course écartée (comptée)
TOP_K = {"WIN": (1, 2, 3), "PLACE": (1, 2, 3), "PAIR_FIRST2": (1, 3, 6, 10), "SEQ_FIRST2": (1, 3, 6, 10),
         "PAIR_TOP3": (1, 3, 6, 10), "PAIR_TOP4": (1, 3, 6, 10), "SET_FIRST3": (1, 5, 10, 20)}
# Événement -> (nombre de places concernées, combinaisons gagnantes par arrivée)
EVENTS = {"WIN": 1, "PLACE": None, "PAIR_FIRST2": 2, "SEQ_FIRST2": 2, "PAIR_TOP3": 3, "PAIR_TOP4": 4,
          "SET_FIRST3": 3, "SEQ_FIRST3": 3, "SET_FIRST4": 4, "SEQ_FIRST4": 4, "SET_FIRST5": 5, "SEQ_FIRST5": 5}


def _log(tag: str, payload: Dict[str, Any]) -> None:
    print(tag + " " + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def _norm(text: Any) -> str:
    s = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", s.upper()).strip()


def classify(type_pari: Any, libelle: Any) -> Optional[str]:
    """(type de pari, libellé) -> événement analysé, ou None (bonus, rapports
    spéciaux, paris non reconnus : inventoriés, jamais analysés)."""
    t = _norm(type_pari)
    t = t[2:] if t.startswith("E_") else t
    lib = _norm(libelle)
    # Rapports particuliers (non-partant, dégradé, tirelire, bonus, remboursement) :
    # d'autres masses ou d'autres règles de gain, inventoriés seulement.
    if any(w in lib for w in ("BONUS", "SPECIAL", "NON PARTANT", "REMBOURS", "DEGRADE", "TIRELIRE")) \
            or re.search(r"\bNP\b", lib):
        return None
    ordre = bool(re.search(r"(^|[^S])ORDRE", lib)) and "DESORDRE" not in lib
    desordre = "DESORDRE" in lib
    if t == "SIMPLE_GAGNANT":
        return "WIN"
    if t == "SIMPLE_PLACE":
        return "PLACE"
    if t == "COUPLE_GAGNANT":
        return "PAIR_FIRST2"
    if t == "COUPLE_ORDRE":
        return "SEQ_FIRST2"
    if t == "COUPLE_PLACE":
        return "PAIR_TOP3"
    if t == "DEUX_SUR_QUATRE":
        return "PAIR_TOP4"
    if t == "TRIO":
        return "SET_FIRST3"
    if t == "TRIO_ORDRE":
        return "SEQ_FIRST3"
    if t == "TIERCE":
        return "SEQ_FIRST3" if ordre else ("SET_FIRST3" if desordre else None)
    if t in ("QUARTE_PLUS", "QUARTE"):
        return "SEQ_FIRST4" if ordre else ("SET_FIRST4" if desordre else None)
    if t in ("QUINTE_PLUS", "QUINTE"):
        return "SEQ_FIRST5" if ordre else ("SET_FIRST5" if desordre else None)
    if t == "MULTI" and re.search(r"\bEN 4\b", lib):
        return "SET_FIRST4"
    return None


def places_paid(n_runners: int) -> int:
    """Règle PMU du simple placé : 3 places à partir de 8 partants, 2 de 4 à 7."""
    return 3 if n_runners >= 8 else (2 if n_runners >= 4 else 0)


# ── Harville ─────────────────────────────────────────────────────────────
def seq_prob(p: np.ndarray, seq: Sequence[int]) -> float:
    """Probabilité que ``seq`` occupe les premières places, dans cet ordre."""
    out, used = 1.0, 0.0
    for i in seq:
        rest = 1.0 - used
        if rest <= 0:
            return 0.0
        out *= p[i] / rest
        used += p[i]
    return out


def set_prob(p: np.ndarray, members: Sequence[int]) -> float:
    """Probabilité que ``members`` occupent les premières places, dans un ordre quelconque."""
    return sum(seq_prob(p, perm) for perm in itertools.permutations(members))


def ordered_tensor(p: np.ndarray, k: int) -> np.ndarray:
    """T[i1..ik] = probabilité de l'arrivée ordonnée i1..ik en tête (Harville)."""
    n = len(p)
    t = np.ones([n] * k)
    used = np.zeros([n] * k)
    for pos in range(k):
        shape = [1] * k
        shape[pos] = n
        pk = p.reshape(shape)
        rest = 1.0 - used
        with np.errstate(divide="ignore", invalid="ignore"):
            t = t * np.where(rest > 1e-12, pk / np.maximum(rest, 1e-12), 0.0)
        used = used + pk
    idx = np.indices([n] * k)
    for a, b in itertools.combinations(range(k), 2):
        t = np.where(idx[a] == idx[b], 0.0, t)
    return t


def in_top_k(p: np.ndarray, k: int) -> Tuple[np.ndarray, np.ndarray]:
    """P(partant dans les k premiers) et P(paire dans les k premiers)."""
    t = ordered_tensor(p, k)
    axes = tuple(range(k))
    single = sum(t.sum(axis=tuple(a for a in axes if a != pos)) for pos in axes)
    pair = np.zeros((len(p), len(p)))
    for a, b in itertools.combinations(axes, 2):
        m = t.sum(axis=tuple(x for x in axes if x not in (a, b)))
        pair += m + m.T
    return single, pair


def event_matrix(kind: str, p: np.ndarray, k_place: int) -> Dict[Tuple[int, ...], float]:
    """Toutes les combinaisons d'un événement et leur probabilité (pour le
    rendement des K meilleures) ; seulement pour les événements énumérables."""
    n = len(p)
    if kind == "WIN":
        return {(i,): float(p[i]) for i in range(n)}
    if kind == "PLACE":
        single, _ = in_top_k(p, k_place)
        return {(i,): float(single[i]) for i in range(n)}
    if kind in ("PAIR_FIRST2", "SEQ_FIRST2"):
        t = ordered_tensor(p, 2)
        if kind == "SEQ_FIRST2":
            return {(i, j): float(t[i, j]) for i in range(n) for j in range(n) if i != j}
        return {(i, j): float(t[i, j] + t[j, i]) for i in range(n) for j in range(i + 1, n)}
    if kind in ("PAIR_TOP3", "PAIR_TOP4"):
        _, pair = in_top_k(p, 3 if kind == "PAIR_TOP3" else 4)
        return {(i, j): float(pair[i, j]) for i in range(n) for j in range(i + 1, n)}
    if kind == "SET_FIRST3":
        t = ordered_tensor(p, 3)
        sym = sum(np.transpose(t, perm) for perm in itertools.permutations(range(3)))
        return {c: float(sym[c]) for c in itertools.combinations(range(n), 3)}
    return {}


# ── Données ──────────────────────────────────────────────────────────────
def load_rapports(db_path: str) -> Tuple[Dict[Tuple[str, int, int], List[Dict[str, Any]]], List[Dict[str, Any]]]:
    """Rapports définitifs par course, et inventaire par (pari, libellé)."""
    conn = sqlite3.connect(db_path)
    by_race: Dict[Tuple[str, int, int], List[Dict[str, Any]]] = defaultdict(list)
    inv: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for date_c, r, c, type_pari, libelle, comb, div, nb_g, remb in conn.execute(
            "SELECT date_course, num_reunion, num_course, type_pari, libelle, combinaison, dividende_pour_1e, "
            "nombre_gagnants, rembourse FROM rapports_definitifs"):
        key = (str(type_pari), str(libelle))
        e = inv.setdefault(key, {"pari": key[0], "libelle": key[1], "lignes": 0, "dates": set(), "courses": set(),
                                 "rembourse": 0, "formats": defaultdict(int), "evenement": classify(*key)})
        e["lignes"] += 1
        e["dates"].add(date_c)
        e["courses"].add((date_c, r, c))
        e["rembourse"] += int(bool(remb))
        e["formats"][re.sub(r"\d+", "N", str(comb or ""))] += 1
        by_race[(date_c, int(r), int(c))].append({"pari": key[0], "libelle": key[1], "nums": [
            int(x) for x in re.findall(r"\d+", str(comb or ""))], "dividende": lab._f(div), "rembourse": bool(remb)})
    conn.close()
    inventory = []
    for e in inv.values():
        formats = sorted(e["formats"].items(), key=lambda kv: -kv[1])[:3]
        inventory.append({"pari": e["pari"], "libelle": e["libelle"], "evenement": e["evenement"], "lignes": e["lignes"],
                          "dates": len(e["dates"]), "courses": len(e["courses"]), "rembourse": e["rembourse"],
                          "formats": dict(formats), "premiere_date": min(e["dates"]), "derniere_date": max(e["dates"])})
    inventory.sort(key=lambda x: -x["lignes"])
    return by_race, inventory


def arrival(race: lab.Race) -> Optional[List[int]]:
    """Indices des partants par place (1er, 2e, ...) ; None si ex aequo ou trou
    dans les 5 premières places."""
    ranked = sorted((int(r["ordre_arrivee"]), i) for i, r in enumerate(race.runners)
                    if isinstance(r.get("ordre_arrivee"), (int, float)) and int(r["ordre_arrivee"]) >= 1)
    order = [i for _, i in ranked]
    ranks = [rk for rk, _ in ranked]
    top = ranks[:5]
    if len(set(top)) != len(top) or top != list(range(1, len(top) + 1)):
        return None
    return order


def winning_combos(kind: str, order: List[int], n: int) -> Optional[List[Tuple[int, ...]]]:
    """Combinaisons gagnantes selon l'arrivée (clés comparables aux lignes de rapport)."""
    need = EVENTS[kind] or places_paid(n)
    if need == 0 or len(order) < need:
        return None
    head = order[:need]
    if kind in ("WIN",):
        return [(head[0],)]
    if kind == "PLACE":
        return [(i,) for i in sorted(head)]
    if kind in ("PAIR_TOP3", "PAIR_TOP4"):
        return [tuple(sorted(c)) for c in itertools.combinations(head, 2)]
    if kind.startswith("SEQ"):
        return [tuple(head)]
    return [tuple(sorted(head))]                                  # ensembles (désordre)


def _combo_key(kind: str, idx: List[int]) -> Tuple[int, ...]:
    return tuple(idx) if kind.startswith("SEQ") else tuple(sorted(idx))


def combo_prob(kind: str, p: np.ndarray, combo: Tuple[int, ...], k_place: int, cache: Dict) -> float:
    if kind == "WIN":
        return float(p[combo[0]])
    if kind == "PLACE":
        if "place" not in cache:
            cache["place"] = in_top_k(p, k_place)[0]
        return float(cache["place"][combo[0]])
    if kind in ("PAIR_TOP3", "PAIR_TOP4"):
        k = 3 if kind == "PAIR_TOP3" else 4
        if k not in cache:
            cache[k] = in_top_k(p, k)[1]
        return float(cache[k][combo[0], combo[1]])
    if kind.startswith("SEQ"):
        return seq_prob(p, combo)
    return set_prob(p, combo)


# ── Évaluation ───────────────────────────────────────────────────────────
def evaluate_combines(races: Sequence[lab.Race], probs: Dict[str, Dict[Tuple[str, int, int], np.ndarray]],
                      by_race: Dict[Tuple[str, int, int], List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Par (pari, libellé) et par modèle : croissance G = E[log(q × D)] du
    portefeuille « au prorata du modèle », écart apparié au marché, et
    rendement des K combinaisons les plus probables."""
    acc: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for race in races:
        rows = by_race.get(race.key)
        if not rows:
            continue
        n = len(race.runners)
        num_to_idx = {int(r["num_pmu"]): i for i, r in enumerate(race.runners)}
        order = arrival(race)
        model_p = {m: probs[m].get(race.key) for m in MODELS}
        if order is None or any(v is None for v in model_p.values()):
            continue
        groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[(row["pari"], row["libelle"])].append(row)
        for gkey, grows in groups.items():
            kind = classify(*gkey)
            if kind is None:
                continue
            a = acc.setdefault(gkey, {"evenement": kind, "courses": 0, "non_concordant": 0, "rembourse": 0,
                                      "champ_trop_grand": 0, "G": {m: [] for m in MODELS}, "reunion": [],
                                      "topk": {m: defaultdict(list) for m in MODELS}})
            if any(r["rembourse"] for r in grows):
                a["rembourse"] += 1
                continue
            if n > MAX_FIELD:
                a["champ_trop_grand"] += 1
                continue
            expected = winning_combos(kind, order, n)
            try:
                paid = {_combo_key(kind, [num_to_idx[x] for x in r["nums"]]): r["dividende"] for r in grows}
            except KeyError:
                paid = {}
            if (not expected or len(paid) != len(grows) or set(paid) != set(expected)
                    or any(d is None or d <= 0 for d in paid.values())):
                a["non_concordant"] += 1
                continue
            k_place = places_paid(n)
            m_count = len(expected)                         # combinaisons gagnantes par arrivée
            a["courses"] += 1
            a["reunion"].append((race.day, race.key[1]))
            for model in MODELS:
                p = model_p[model]
                cache: Dict = {}
                ret = sum(combo_prob(kind, p, c, k_place, cache) / m_count * paid[c] for c in expected)
                a["G"][model].append(math.log(max(ret, 1e-12)))
                if kind in TOP_K:
                    ranked = sorted(event_matrix(kind, p, k_place).items(), key=lambda kv: -kv[1])
                    for k in TOP_K[kind]:
                        chosen = [c for c, _ in ranked[:k]]
                        a["topk"][model][k].append(sum(paid.get(c, 0.0) for c in chosen) / k - 1.0)
    out = {}
    for (pari, libelle), a in acc.items():
        entry: Dict[str, Any] = {"pari": pari, "libelle": libelle, "evenement": a["evenement"],
                                 "courses": a["courses"], "non_concordant": a["non_concordant"],
                                 "rembourse": a["rembourse"], "champ_trop_grand": a["champ_trop_grand"]}
        if a["courses"] >= 30:
            clusters = a["reunion"]
            for model in MODELS:
                g = a["G"][model]
                entry[f"G_{model}"] = round(float(np.mean(g)), 4)
                entry[f"ic95_G_{model}"] = [round(v, 4) for v in ombre.intervalle(g, clusters, 0.95)]
                if a["topk"][model]:
                    entry[f"roi_top_{model}"] = {
                        str(k): {"roi": round(float(np.mean(v)), 4),
                                 "ic95": [round(x, 4) for x in ombre.intervalle(v, clusters, 0.95)]}
                        for k, v in a["topk"][model].items()}
            for model in ("combine", "fondamental"):
                diff = [x - y for x, y in zip(a["G"][model], a["G"]["marche"])]
                entry[f"delta_G_{model}_vs_marche"] = round(float(np.mean(diff)), 4)
                entry[f"ic95_delta_G_{model}_vs_marche"] = [round(v, 4) for v in ombre.intervalle(diff, clusters, 0.95)]
            entry["bat_la_masse_combine"] = entry["ic95_G_combine"][0] > 0
        out[f"{pari}|{libelle}"] = entry
    return out


def model_probs(races: Sequence[lab.Race]) -> Tuple[Dict[str, Dict], Dict[str, Any]]:
    """Probabilités de victoire hors échantillon des trois modèles."""
    audit = lab.build_features(races)
    names = [n for n in lab.FEATURES if not (audit["fuite_suspectee"] and n in lab.CAREER_FEATURES)]
    wf = lab.fundamental_walk_forward(races, names)
    ev = lab.evaluate(races, wf, "market")
    comb = ev.pop("_comb")
    probs = {"marche": {r.key: r.market for r in races if r.market is not None and r.key in comb},
             "combine": comb,
             "fondamental": {k: v for k, v in wf["oos"].items() if k in comb}}
    return probs, audit


def run(db_path: str) -> Dict[str, Any]:
    races, data_stats = lab.load_races(db_path)
    by_race, inventory = load_rapports(db_path)
    for item in inventory:
        _log("COMBINES_INVENTAIRE", item)
    probs, audit = model_probs(races)
    _log("COMBINES_DONNEES", {"courses": len(races), "courses_avec_rapports": len(by_race),
                              "courses_avec_trois_modeles": len(probs["combine"]),
                              "fuite_suspectee": audit["fuite_suspectee"]})
    results = evaluate_combines(races, probs, by_race)
    for entry in sorted(results.values(), key=lambda e: (-e["courses"], e["pari"])):
        _log("COMBINES_RESULTAT", entry)
    return {"genere_le_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "methode": {"critere": "G = E[log(q x D)] : portefeuille au prorata du modele, regle au rapport officiel",
                        "harville": True, "modeles": list(MODELS),
                        "avertissement": "marche de cloture = borne haute (cotes finales)"},
            "donnees": data_stats, "inventaire": inventory, "resultats": results}


def upload(report: Dict[str, Any], client_factory=None) -> Optional[str]:
    from turf_lab.r2_store import make_client, r2_config
    cfg = r2_config()
    if cfg is None:
        _log("COMBINES_ENVOI_IGNORE", {"raison": "secrets R2 absents"})
        return None
    client = (client_factory or make_client)(cfg)
    body = json.dumps(report, ensure_ascii=False, indent=1, default=str).encode("utf-8")
    key = f"{REPORT_PREFIX}combines_{report['genere_le_utc'][:10]}_{os.environ.get('GITHUB_RUN_ID', 'local')}.json"
    for k in (key, REPORT_PREFIX + "dernier.json"):
        client.put_object(Bucket=cfg["bucket"], Key=k, Body=body, ContentType="application/json")
    _log("COMBINES_RAPPORT_ENVOYE", {"cle": key, "octets": len(body)})
    return key


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Labo des paris combinés (test historique)")
    parser.add_argument("--db", default="turf_history.db")
    parser.add_argument("--sans-envoi", action="store_true")
    args = parser.parse_args(argv)
    if not os.path.exists(args.db):
        _log("COMBINES_ERREUR", {"erreur": "miroir absent"})
        return 2
    report = run(args.db)
    if not args.sans_envoi:
        upload(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
