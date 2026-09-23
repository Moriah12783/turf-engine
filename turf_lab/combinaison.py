"""Tableau de score « Benter » : ce que chaque moteur AJOUTE au marché.

Métrique unique du cahier des charges commun (docs/PROTOCOLE_MOTEURS.md) :
gain de log-vraisemblance par course face au marché, hors échantillon.

    p(i) ∝ exp( α·log p_marché(i) + β_NVE·log p_NVE_pur(i) + β_Radar·log p_Radar(i) )

- Coefficients appris par logit conditionnel (Newton, log-vraisemblance
  concave, bibliothèque standard uniquement) sur les 60 % de courses les plus
  anciennes, jugés sur les 40 % les plus récentes, jamais vues.
- Mêmes courses, même horizon, éditions à cotes réelles ; les éditions marché
  nominales (sans information de marché) sont exclues.
- Contrôle de loyauté : l'écart de verrouillage entre le Radar et la ligne de
  base marché est mesuré ; si le Radar est verrouillé nettement après le
  marché (cas de l'horizon Matin), la comparaison est signalée NON LOYALE, car
  un moteur verrouillé plus tard peut simplement porter des cotes plus fraîches.
- Intervalle de confiance à 95 % par bootstrap sur les courses de test.

Ce module ne modifie rien : il lit la base et rend des chiffres.
"""
import json
import math
import random
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

TRAIN_SHARE = 0.60
FLOOR = 1e-4
SOURCES = ("MARCHE", "NVE_PUR", "RADAR")
ENGINE_OF = {"MARKET_BASELINE": "MARCHE", "NEW_VALUE_ENGINE": "NVE_PUR", "RADAR_V4": "RADAR"}
MAX_LOCK_GAP_MINUTES = 5.0     # au-delà : verrous non simultanés, comparaison non loyale
MIN_RACES = 50
BOOTSTRAP = 2000


def _parse_ts(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", ""))
    except Exception:
        return None


def load_common_races(db, horizon: str) -> Dict[str, Any]:
    """Courses communes aux trois sources à cet horizon, triées par date.

    Retourne {"races": [...], "lock_gap_median_min": float|None} où chaque
    course porte les log-probabilités normalisées de chaque source et l'indice
    du gagnant."""
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute("SELECT race_id, arrival_order_json FROM race_results WHERE COALESCE(statut, 'DEFINITIVE') = 'DEFINITIVE'")
        arrivals = {r["race_id"]: json.loads(r["arrival_order_json"] or "[]") for r in cur.fetchall()}
        cur.execute("SELECT race_id, date FROM races")
        dates = {r["race_id"]: r["date"] for r in cur.fetchall()}
        cur.execute("SELECT race_id, engine_name, probabilities_json, metadata_json, lock_time_utc FROM predictions "
                    "WHERE horizon = ? AND odds_real = 1", (horizon,))
        rows = cur.fetchall()

    preds: Dict[str, Dict[str, Any]] = {}
    locks: Dict[str, Dict[str, Optional[datetime]]] = {}
    for r in rows:
        src = ENGINE_OF.get(r["engine_name"])
        if not src:
            continue
        d = preds.setdefault(r["race_id"], {})
        if src == "NVE_PUR":
            d[src] = (json.loads(r["metadata_json"] or "{}") or {}).get("model_probs") or {}
        else:
            d[src] = json.loads(r["probabilities_json"] or "{}") or {}
        locks.setdefault(r["race_id"], {})[src] = _parse_ts(r["lock_time_utc"])

    races: List[Dict[str, Any]] = []
    gaps: List[float] = []
    for rid, d in preds.items():
        arrival = arrivals.get(rid)
        if not arrival or any(s not in d or not d[s] for s in SOURCES):
            continue
        if len({round(float(v), 6) for v in d["MARCHE"].values()}) <= 1:
            continue  # édition marché nominale
        nums = [n for n in d["MARCHE"] if all(n in d[s] for s in SOURCES)]
        winner = str(arrival[0])
        if winner not in nums or len(nums) < 4:
            continue
        cols = []
        for s in SOURCES:
            v = [max(FLOOR, float(d[s][n])) for n in nums]
            tot = sum(v) or 1.0
            cols.append([math.log(x / tot) for x in v])
        races.append({"race_id": rid, "date": dates.get(rid, ""), "x": [list(t) for t in zip(*cols)], "y": nums.index(winner)})
        lk = locks.get(rid, {})
        if lk.get("RADAR") and lk.get("MARCHE"):
            gaps.append((lk["RADAR"] - lk["MARCHE"]).total_seconds() / 60.0)
    races.sort(key=lambda r: (r["date"], r["race_id"]))
    gaps.sort()
    median_gap = gaps[len(gaps) // 2] if gaps else None
    return {"races": races, "lock_gap_median_min": median_gap}


def race_nll(beta: Sequence[float], race: Dict[str, Any], cols: Sequence[int]) -> float:
    scores = [sum(b * row[k] for b, k in zip(beta, cols)) for row in race["x"]]
    m = max(scores)
    return -(scores[race["y"]] - m - math.log(sum(math.exp(s - m) for s in scores)))


def mean_nll(beta: Sequence[float], races: Sequence[Dict[str, Any]], cols: Sequence[int]) -> float:
    return sum(race_nll(beta, r, cols) for r in races) / len(races)


def _solve(mat: List[List[float]], vec: List[float]) -> Optional[List[float]]:
    """Résolution d'un petit système linéaire (pivot de Gauss)."""
    n = len(vec)
    a = [row[:] + [vec[i]] for i, row in enumerate(mat)]
    for i in range(n):
        piv = max(range(i, n), key=lambda r: abs(a[r][i]))
        if abs(a[piv][i]) < 1e-12:
            return None
        a[i], a[piv] = a[piv], a[i]
        for r in range(n):
            if r != i:
                f = a[r][i] / a[i][i]
                for c in range(i, n + 1):
                    a[r][c] -= f * a[i][c]
    return [a[i][n] / a[i][i] for i in range(n)]


def fit(races: Sequence[Dict[str, Any]], cols: Sequence[int], iters: int = 30, ridge: float = 1e-3) -> List[float]:
    """Logit conditionnel par méthode de Newton (gradient et hessienne
    analytiques : espérance et covariance des variables sous le modèle),
    avec une légère régularisation L2 pour la stabilité."""
    k = len(cols)
    beta = [1.0] + [0.0] * (k - 1)
    current = mean_nll(beta, races, cols)
    for _ in range(iters):
        grad = [0.0] * k
        hess = [[0.0] * k for _ in range(k)]
        for r in races:
            scores = [sum(b * row[c] for b, c in zip(beta, cols)) for row in r["x"]]
            m = max(scores)
            w = [math.exp(s - m) for s in scores]
            z = sum(w) or 1.0
            p = [wi / z for wi in w]
            ex = [sum(pi * row[c] for pi, row in zip(p, r["x"])) for c in cols]
            xw = r["x"][r["y"]]
            for i, c in enumerate(cols):
                grad[i] += (ex[i] - xw[c]) / len(races)
                for j, c2 in enumerate(cols):
                    cov = sum(pi * (row[c] - ex[i]) * (row[c2] - ex[j]) for pi, row in zip(p, r["x"]))
                    hess[i][j] += cov / len(races)
        for i in range(k):
            grad[i] += ridge * (beta[i] - (1.0 if i == 0 else 0.0))
            hess[i][i] += ridge
        step = _solve(hess, grad)
        if step is None:
            break
        # Recherche linéaire (garantit la décroissance)
        t = 1.0
        while t > 1e-4:
            cand = [b - t * s for b, s in zip(beta, step)]
            value = mean_nll(cand, races, cols)
            if value <= current:
                improved = current - value
                beta, current = cand, value
                break
            t /= 2
        else:
            break
        if improved < 1e-9:
            break
    return beta


def evaluate_delta(db, horizon: str) -> Dict[str, Any]:
    """Δ hors échantillon par variante + IC95 bootstrap, pour un horizon."""
    data = load_common_races(db, horizon)
    races = data["races"]
    gap = data["lock_gap_median_min"]
    loyal = gap is None or abs(gap) <= MAX_LOCK_GAP_MINUTES
    out: Dict[str, Any] = {
        "horizon": horizon, "courses": len(races), "lock_gap_median_min": (round(gap, 1) if gap is not None else None),
        "comparaison_loyale": loyal, "status": "OK",
    }
    if len(races) < MIN_RACES:
        out.update({"status": "NO_DATA", "train": 0, "test": 0, "variantes": {}})
        return out
    cut = int(len(races) * TRAIN_SHARE)
    train, test = races[:cut], races[cut:]
    out.update({"train": len(train), "test": len(test), "periode_test": [test[0]["date"], test[-1]["date"]]})
    base = mean_nll([1.0], test, [0])
    out["ll_marche_brut"] = round(base, 4)
    variants = {"marche_recalibre": [0], "marche_nve": [0, 1], "marche_radar": [0, 2], "marche_nve_radar": [0, 1, 2]}
    res: Dict[str, Any] = {}
    for name, cols in variants.items():
        beta = fit(train, cols)
        value = mean_nll(beta, test, cols)
        gains = [race_nll([1.0], r, [0]) - race_nll(beta, r, cols) for r in test]
        rng = random.Random(1)
        boots = sorted(sum(rng.choice(gains) for _ in gains) / len(gains) for _ in range(BOOTSTRAP))
        res[name] = {
            "gain": round(base - value, 4),
            "ic95": [round(boots[int(0.025 * BOOTSTRAP)], 4), round(boots[int(0.975 * BOOTSTRAP) - 1], 4)],
            "coefs": {SOURCES[c]: round(b, 3) for b, c in zip(beta, cols)},
        }
    out["variantes"] = res
    g = res["marche_nve_radar"]
    out["succes"] = bool(g["gain"] > 0 and g["ic95"][0] > 0 and len(test) >= 1000)
    return out


def evaluate_all(db, horizons: Sequence[str] = ("T_MATIN", "T90", "T30", "T15")) -> Dict[str, Any]:
    return {h: evaluate_delta(db, h) for h in horizons}
