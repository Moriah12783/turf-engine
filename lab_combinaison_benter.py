"""Labo « combinaison Benter » : ce que chaque moteur AJOUTE au marché.

Métrique unique (docs/PROTOCOLE_MOTEURS.md) : gain de log-vraisemblance par
course face au marché, hors échantillon.

    p(i) ∝ exp( α·log p_marché(i) + β_NVE·log p_NVE_pur(i) + β_Radar·log p_Radar(i) )

Coefficients appris par logit conditionnel sur les courses les plus anciennes
(60 %), jugés sur les plus récentes (40 %), jamais vues à l'apprentissage.
Sources au même horizon et au même instant de verrou (éditions odds_real).

Usage : python lab_combinaison_benter.py [/chemin/turf_bench.db] [HORIZON]
"""
import json
import math
import random
import sqlite3
import sys

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "turf_bench.db"
HORIZON = sys.argv[2] if len(sys.argv) > 2 else "T15"
TRAIN_SHARE = 0.60
FLOOR = 1e-4
SOURCES = ("MARCHE", "NVE_PUR", "RADAR")


def load(db_path, horizon):
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    arrivals = {r["race_id"]: json.loads(r["arrival_order_json"] or "[]")
                for r in c.execute("SELECT race_id, arrival_order_json FROM race_results "
                                   "WHERE COALESCE(statut, 'DEFINITIVE') = 'DEFINITIVE'")}
    dates = {r["race_id"]: r["date"] for r in c.execute("SELECT race_id, date FROM races")}
    preds = {}
    for r in c.execute("SELECT race_id, engine_name, probabilities_json, metadata_json FROM predictions "
                       "WHERE horizon = ? AND odds_real = 1", (horizon,)):
        d = preds.setdefault(r["race_id"], {})
        if r["engine_name"] == "MARKET_BASELINE":
            d["MARCHE"] = json.loads(r["probabilities_json"] or "{}")
        elif r["engine_name"] == "NEW_VALUE_ENGINE":
            d["NVE_PUR"] = json.loads(r["metadata_json"] or "{}").get("model_probs") or {}
        elif r["engine_name"] == "RADAR_V4":
            d["RADAR"] = json.loads(r["probabilities_json"] or "{}")

    races = []
    for rid, d in preds.items():
        arrival = arrivals.get(rid)
        if not arrival or any(s not in d for s in SOURCES):
            continue
        if len({round(float(v), 6) for v in d["MARCHE"].values()}) <= 1:
            continue  # édition marché nominale : aucune information de marché
        nums = [n for n in d["MARCHE"] if all(n in d[s] for s in SOURCES)]
        winner = str(arrival[0])
        if winner not in nums or len(nums) < 4:
            continue
        cols = []
        for s in SOURCES:
            v = [max(FLOOR, float(d[s][n])) for n in nums]
            tot = sum(v)
            cols.append([math.log(x / tot) for x in v])
        races.append({"date": dates.get(rid, ""), "x": list(zip(*cols)), "y": nums.index(winner)})
    races.sort(key=lambda r: r["date"])
    return races


def race_nll(beta, race, cols):
    scores = [sum(b * row[k] for b, k in zip(beta, cols)) for row in race["x"]]
    m = max(scores)
    return -(scores[race["y"]] - m - math.log(sum(math.exp(s - m) for s in scores)))


def mean_nll(beta, races, cols):
    return sum(race_nll(beta, r, cols) for r in races) / len(races)


def fit(races, cols, iters=400):
    """Logit conditionnel : log-vraisemblance concave, montée de gradient
    avec pas adaptatif (bibliothèque standard uniquement)."""
    beta = [1.0] + [0.0] * (len(cols) - 1)
    step, current = 0.5, mean_nll(beta, races, cols)
    for _ in range(iters):
        grad = [0.0] * len(cols)
        for r in races:
            scores = [sum(b * row[k] for b, k in zip(beta, cols)) for row in r["x"]]
            m = max(scores)
            w = [math.exp(s - m) for s in scores]
            z = sum(w)
            for j, k in enumerate(cols):
                expected = sum(wi * row[k] for wi, row in zip(w, r["x"])) / z
                grad[j] += (expected - r["x"][r["y"]][k]) / len(races)
        while step > 1e-6:
            cand = [b - step * g for b, g in zip(beta, grad)]
            value = mean_nll(cand, races, cols)
            if value < current:
                beta, current, step = cand, value, step * 1.2
                break
            step /= 2
        else:
            break
    return beta


def main():
    races = load(DB_PATH, HORIZON)
    if len(races) < 50:
        print(f"Échantillon trop faible ({len(races)} courses communes à {HORIZON}).")
        return
    cut = int(len(races) * TRAIN_SHARE)
    train, test = races[:cut], races[cut:]
    print(f"Horizon {HORIZON} : {len(races)} courses communes ({races[0]['date']} -> {races[-1]['date']})")
    print(f"Apprentissage {len(train)} courses, test {len(test)} courses (jamais vues)\n")
    base = mean_nll([1.0], test, [0])
    print(f"  {'Marché brut':34} LL test = {base:.4f}")
    variants = [("Marché recalibré", [0]), ("Marché + NVE pur", [0, 1]),
                ("Marché + Radar", [0, 2]), ("Marché + NVE pur + Radar", [0, 1, 2])]
    for label, cols in variants:
        beta = fit(train, cols)
        value = mean_nll(beta, test, cols)
        coefs = ", ".join(f"{SOURCES[k]}={b:+.3f}" for b, k in zip(beta, cols))
        print(f"  {label:34} LL test = {value:.4f}  gain = {base - value:+.4f} nat/course  [{coefs}]")

    cols = [0, 1, 2]
    beta = fit(train, cols)
    gains = [race_nll([1.0], r, [0]) - race_nll(beta, r, cols) for r in test]
    rng = random.Random(1)
    boots = sorted(sum(rng.choice(gains) for _ in gains) / len(gains) for _ in range(2000))
    print(f"\n  Gain combinaison 3 sources : {sum(gains) / len(gains):+.4f} nat/course, "
          f"IC95 [{boots[49]:+.4f} ; {boots[1949]:+.4f}]")
    print("  Succès = borne basse de l'IC > 0 sur au moins 1 000 courses de test.")


if __name__ == "__main__":
    main()
