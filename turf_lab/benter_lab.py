"""Labo Benter — étape 1 (modèle fondamental, SANS les cotes) et étape 2
(combinaison avec le marché), jugées en walk-forward sur le miroir de
l'historique Radar (``history/turf_history.db``, voir docs/HISTORIQUE_RADAR.md).

Méthode (docs/LABO_BENTER.md, métrique unique de docs/PROTOCOLE_MOTEURS.md) :
  1. Modèle fondamental : logit conditionnel sur ~30 variables d'avant-course
     (programme PMU vérifié figé avant la course, forme, historique cheval,
     jockey/driver et entraîneur à la veille au soir). Un jeu de coefficients
     pour le TROT, un pour le GALOP. Aucune cote n'entre dans le modèle.
  2. Walk-forward mensuel : chaque mois de test est prédit par un modèle
     entraîné sur les seuls mois précédents.
  3. Combinaison Benter : p ∝ exp(α·log p_fond + β·log q_marché), α et β
     appris sur les mois de test PRÉCÉDENTS (jamais sur le mois jugé).
  4. Juge : gain de log-vraisemblance par course face au marché RECALIBRÉ
     (q^γ, qui corrige déjà le biais favori/outsider), IC 95 % par bootstrap.
     Marché = cotes de clôture (cote_reference finale) : repère exigeant ; en
     production les cotes à T-x seront moins informées.

Confidentialité (dépôt public) : le journal n'affiche que des agrégats par
mois ; aucune donnée par course ou par partant, aucun coefficient. Le rapport
complet va sur R2 privé (``lab/benter/``). Aucun artefact.

Usage :
    python -m turf_lab.benter_lab --db turf_history.db [--sortie rapport.json] [--sans-envoi]
"""

import argparse
import json
import math
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

TRAIN_START = "2025-08-15"      # deux semaines d'historique cheval avant le 1er apprentissage
FIRST_TEST_MONTH = "2026-01"
RIDGE = 1.0                     # pénalité L2 (variables standardisées)
BOOTSTRAP = 2000
G1_MIN_TEST_RACES = 3000
PRIOR_K = 30.0                  # lissage des taux jockey / entraîneur
PRIOR_WIN = 0.08                # ~1 / taille moyenne des pelotons
FLOOR = 1e-6
GROUPS = {"TROT_ATTELE": "TROT", "TROT_MONTE": "TROT", "PLAT": "GALOP", "OBSTACLE": "GALOP"}
DISCIPLINE_GROUP = {"ATTELE": "TROT", "MONTE": "TROT", "PLAT": "GALOP", "HAIE": "GALOP",
                    "STEEPLECHASE": "GALOP", "CROSS": "GALOP"}
REPORT_PREFIX = "lab/benter/"

FEATURES = (
    "taux_victoires", "taux_places", "log_courses", "log_gains", "log_gains_annee",
    "log_gain_par_course", "age", "age2", "femelle", "male", "oeilleres", "oeilleres_austr",
    "driver_change", "corde_rel", "corde_absente", "poids_rel", "poids_absent",
    "mus_moyenne", "mus_victoires", "mus_top3", "mus_fautes", "mus_derniere", "mus_absente",
    "log_jours_repos", "premiere_vue", "derniere_place",
    "jockey_taux", "jockey_log_montes", "entraineur_taux", "entraineur_log_courses",
)
CAREER_FEATURES = ("taux_victoires", "taux_places", "log_courses", "log_gains", "log_gains_annee",
                   "log_gain_par_course", "mus_moyenne", "mus_victoires", "mus_top3", "mus_fautes",
                   "mus_derniere", "mus_absente")

_LOAD_SQL = """
SELECT c.date_course, c.num_reunion, c.num_course, c.specialite, c.discipline, c.heure_depart,
       p.num_pmu, p.nom, p.nom_pere, p.nom_mere, p.age, p.sexe, p.place_corde, p.oeilleres,
       p.entraineur, p.driver, p.driver_change, p.musique, p.nombre_courses, p.nombre_victoires,
       p.nombre_places, p.gains_carriere, p.gains_annee_en_cours, p.handicap_poids,
       p.cote_reference, p.cote_direct, p.ordre_arrivee
  FROM courses_courues c
  JOIN participants p ON p.date_course = c.date_course AND p.num_reunion = c.num_reunion
                     AND p.num_course = c.num_course
 WHERE p.statut = 'PARTANT' AND (p.incident IS NULL OR p.incident <> 'NON_PARTANT')
 ORDER BY c.date_course, c.heure_depart, c.num_reunion, c.num_course, p.num_pmu"""


def _log(tag: str, payload: Dict[str, Any]) -> None:
    print(tag + " " + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def _f(value: Any) -> Optional[float]:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


def _name(value: Any) -> str:
    return " ".join(str(value or "").upper().split())


# ── Musique PMU (vérifiée figée avant la course) ─────────────────────────
_MUSIQUE_TOKEN = re.compile(r"([0-9A-Z])([a-z])")


def parse_musique(musique: Optional[str], last: int = 6) -> Dict[str, float]:
    """Les ``last`` résultats les plus récents (en tête de chaîne). Chiffre =
    place (0 = 10e ou au-delà) ; lettre = faute (disqualifié, arrêté, tombé…)."""
    text = re.sub(r"\(\d+\)", "", musique or "")
    scores, faults = [], 0
    for code, _disc in _MUSIQUE_TOKEN.findall(text)[:last]:
        if code.isdigit():
            scores.append(10.0 if code == "0" else float(code))
        else:
            scores.append(12.0)
            faults += 1
    if not scores:
        return {"mus_moyenne": 0.0, "mus_victoires": 0.0, "mus_top3": 0.0, "mus_fautes": 0.0,
                "mus_derniere": 0.0, "mus_absente": 1.0}
    return {"mus_moyenne": sum(scores) / len(scores), "mus_victoires": float(sum(s == 1 for s in scores)),
            "mus_top3": float(sum(s <= 3 for s in scores)), "mus_fautes": float(faults),
            "mus_derniere": scores[0], "mus_absente": 0.0}


# ── Chargement et variables d'avant-course ───────────────────────────────
class Race:
    __slots__ = ("key", "day", "group", "runners", "features", "winner", "market")

    def __init__(self, key: Tuple[str, int, int], day: str, group: str):
        self.key, self.day, self.group = key, day, group
        self.runners: List[Dict[str, Any]] = []
        self.features: Optional[np.ndarray] = None
        self.winner: Optional[int] = None
        self.market: Optional[np.ndarray] = None


def _group_of(specialite: Any, discipline: Any) -> Optional[str]:
    return GROUPS.get(str(specialite or "")) or DISCIPLINE_GROUP.get(str(discipline or ""))


def load_races(db_path: str) -> Tuple[List[Race], Dict[str, int]]:
    """Courses courues (vue officielle) et leurs partants, dans l'ordre
    chronologique. Gagnant : ordre_arrivee = 1, sinon 1re place de arrivees ;
    dead-heat ou gagnant introuvable : course écartée (comptée)."""
    from turf_lab.history_export import HistoryDB
    HistoryDB(db_path).close()                 # crée la vue et les index si le miroir est ancien
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    arrivals: Dict[Tuple[str, int, int], Any] = {}
    for row in conn.execute("SELECT date_course, num_reunion, num_course, ordre_arrivee FROM arrivees"):
        try:
            arrivals[(row[0], int(row[1]), int(row[2]))] = json.loads(row[3]) if row[3] else None
        except (TypeError, ValueError):
            pass
    races: Dict[Tuple[str, int, int], Race] = {}
    for row in conn.execute(_LOAD_SQL):
        key = (row["date_course"], int(row["num_reunion"]), int(row["num_course"]))
        race = races.get(key)
        if race is None:
            group = _group_of(row["specialite"], row["discipline"])
            race = races[key] = Race(key, row["date_course"], group or "")
        race.runners.append(dict(row))
    conn.close()

    stats = {"courses_courues": len(races), "ecartee_groupe_inconnu": 0, "ecartee_dead_heat": 0,
             "ecartee_sans_gagnant": 0, "ecartee_un_partant": 0}
    kept = []
    for race in races.values():
        if not race.group:
            stats["ecartee_groupe_inconnu"] += 1
            continue
        if len(race.runners) < 2:
            stats["ecartee_un_partant"] += 1
            continue
        firsts = [i for i, r in enumerate(race.runners) if r["ordre_arrivee"] == 1]
        if len(firsts) > 1:
            stats["ecartee_dead_heat"] += 1
            continue
        if not firsts:
            first_group = (arrivals.get(race.key) or [None])[0]
            nums = first_group if isinstance(first_group, list) else [first_group]
            idx = [i for i, r in enumerate(race.runners) if len(nums) == 1 and r["num_pmu"] == nums[0]]
            if len(nums) > 1:
                stats["ecartee_dead_heat"] += 1
                continue
            if not idx:
                stats["ecartee_sans_gagnant"] += 1
                continue
            firsts = idx
        race.winner = firsts[0]
        odds = [_f(r["cote_reference"]) or _f(r["cote_direct"]) for r in race.runners]
        if all(o is not None and o > 1.0 for o in odds):
            inv = np.array([1.0 / o for o in odds])
            race.market = inv / inv.sum()
        kept.append(race)
    kept.sort(key=lambda r: (r.day, str(r.runners[0]["heure_depart"] or ""), r.key))
    stats["courses_retenues"] = len(kept)
    stats["courses_trot"] = sum(r.group == "TROT" for r in kept)
    stats["courses_galop"] = sum(r.group == "GALOP" for r in kept)
    stats["partants"] = sum(len(r.runners) for r in kept)
    stats["courses_avec_marche"] = sum(r.market is not None for r in kept)
    return kept, stats


def build_features(races: Sequence[Race]) -> Dict[str, Any]:
    """Variables d'avant-course. Les statistiques tirées du miroir (cheval,
    jockey, entraîneur) sont figées à la VEILLE AU SOIR : un pronostic du matin
    ne connaît pas les courses du jour. Renvoie aussi l'audit de fuite."""
    horse_last: Dict[Tuple[str, str, str], Tuple[str, Optional[int], Optional[float]]] = {}
    humans: Dict[str, List[float]] = defaultdict(lambda: [0.0, 0.0])      # [montes, victoires]
    audit = {"gagnants": [0, 0.0], "autres": [0, 0.0], "mus1_gagnants": [0, 0], "mus1_autres": [0, 0]}
    by_day: Dict[str, List[Race]] = defaultdict(list)
    for race in races:
        by_day[race.day].append(race)

    for day in sorted(by_day):
        day_races = by_day[day]
        d0 = date.fromisoformat(day)
        for race in day_races:
            n = len(race.runners)
            poids = [_f(r["handicap_poids"]) for r in race.runners]
            poids_known = [p for p in poids if p is not None]
            poids_mean = sum(poids_known) / len(poids_known) if poids_known else 0.0
            rows = []
            for i, r in enumerate(race.runners):
                courses = _f(r["nombre_courses"]) or 0.0
                victoires = _f(r["nombre_victoires"]) or 0.0
                places = _f(r["nombre_places"]) or 0.0
                gains = max(_f(r["gains_carriere"]) or 0.0, 0.0)
                age = _f(r["age"]) or 0.0
                oeil = str(r["oeilleres"] or "")
                corde = _f(r["place_corde"])
                horse = (_name(r["nom"]), _name(r["nom_pere"]), _name(r["nom_mere"]))
                last = horse_last.get(horse)
                jockey = humans["J:" + _name(r["driver"])] if r["driver"] else [0.0, 0.0]
                trainer = humans["E:" + _name(r["entraineur"])] if r["entraineur"] else [0.0, 0.0]
                f = {
                    "taux_victoires": (victoires + 1.0) / (courses + 10.0),
                    "taux_places": (places + 3.0) / (courses + 10.0),
                    "log_courses": math.log1p(courses),
                    "log_gains": math.log1p(gains),
                    "log_gains_annee": math.log1p(max(_f(r["gains_annee_en_cours"]) or 0.0, 0.0)),
                    "log_gain_par_course": math.log1p(gains / max(courses, 1.0)),
                    "age": age, "age2": age * age,
                    "femelle": float(r["sexe"] == "FEMELLES"), "male": float(r["sexe"] == "MALES"),
                    "oeilleres": float(bool(oeil) and oeil != "SANS_OEILLERES"),
                    "oeilleres_austr": float(oeil == "OEILLERES_AUSTRALIENNES"),
                    "driver_change": float(bool(r["driver_change"])),
                    "corde_rel": (corde / n) if corde else 0.5, "corde_absente": float(not corde),
                    "poids_rel": ((poids[i] - poids_mean) / 10.0) if poids[i] is not None else 0.0,
                    "poids_absent": float(poids[i] is None),
                    "log_jours_repos": math.log1p((d0 - date.fromisoformat(last[0])).days) if last else 0.0,
                    "premiere_vue": float(last is None),
                    "derniere_place": float(min(last[1], 12)) if last and last[1] else (12.0 if last else 0.0),
                    "jockey_taux": (jockey[1] + PRIOR_K * PRIOR_WIN) / (jockey[0] + PRIOR_K),
                    "jockey_log_montes": math.log1p(jockey[0]),
                    "entraineur_taux": (trainer[1] + PRIOR_K * PRIOR_WIN) / (trainer[0] + PRIOR_K),
                    "entraineur_log_courses": math.log1p(trainer[0]),
                }
                f.update(parse_musique(r["musique"]))
                rows.append([f[name] for name in FEATURES])
                # Audit de fuite : la victoire du jour est-elle déjà comptée dans le programme ?
                if last is not None and last[2] is not None:
                    bucket = "gagnants" if last[1] == 1 else "autres"
                    audit[bucket][0] += 1
                    audit[bucket][1] += victoires - last[2]
                mus = "mus1_gagnants" if i == race.winner else "mus1_autres"
                audit[mus][0] += 1
                audit[mus][1] += int(str(r["musique"] or "").startswith("1"))
            race.features = np.array(rows, dtype=float)
        # Mise à jour APRÈS la journée : le lendemain seulement en profite.
        for race in day_races:
            for i, r in enumerate(race.runners):
                horse = (_name(r["nom"]), _name(r["nom_pere"]), _name(r["nom_mere"]))
                pos = r["ordre_arrivee"] if isinstance(r["ordre_arrivee"], int) else None
                horse_last[horse] = (day, pos if pos else 12, _f(r["nombre_victoires"]))
                won = float(i == race.winner)
                for key in (("J:" + _name(r["driver"])) if r["driver"] else None,
                            ("E:" + _name(r["entraineur"])) if r["entraineur"] else None):
                    if key:
                        humans[key][0] += 1.0
                        humans[key][1] += won

    def mean(pair):
        return round(pair[1] / pair[0], 4) if pair[0] else None
    return {
        "delta_victoires_apres_victoire": mean(audit["gagnants"]),
        "delta_victoires_apres_autre": mean(audit["autres"]),
        "musique_commence_par_1_gagnants": mean(audit["mus1_gagnants"]),
        "musique_commence_par_1_autres": mean(audit["mus1_autres"]),
        # Programme figé avant la course : le gagnant d'hier a +1 victoire aujourd'hui.
        "fuite_suspectee": bool(audit["gagnants"][0]) and (mean(audit["gagnants"]) or 0.0) < 0.5,
    }


# ── Logit conditionnel (Newton, numpy) ───────────────────────────────────
class Packed:
    """Courses empilées : X (partants × variables), bornes de segments, gagnants."""

    def __init__(self, blocks: Sequence[np.ndarray], winners: Sequence[int]):
        self.X = np.vstack(blocks)
        sizes = np.array([len(b) for b in blocks])
        self.starts = np.concatenate(([0], np.cumsum(sizes)[:-1]))
        self.seg = np.repeat(np.arange(len(blocks)), sizes)
        self.win_rows = self.starts + np.asarray(winners)
        self.n_races = len(blocks)

    def probs(self, beta: np.ndarray) -> np.ndarray:
        s = self.X @ beta
        s = s - np.maximum.reduceat(s, self.starts)[self.seg]
        e = np.exp(s)
        return e / np.add.reduceat(e, self.starts)[self.seg]

    def loglik(self, beta: np.ndarray) -> float:
        return float(np.log(np.maximum(self.probs(beta)[self.win_rows], FLOOR)).sum())


def fit_clogit(packed: Packed, ridge: float = RIDGE, iters: int = 50, tol: float = 1e-8) -> np.ndarray:
    """Maximum de vraisemblance pénalisée (L2) par Newton amorti. Concave :
    une seule solution, atteinte en quelques itérations."""
    k = packed.X.shape[1]
    beta = np.zeros(k)

    def objective(b):
        return packed.loglik(b) - 0.5 * ridge * float(b @ b)

    current = objective(beta)
    for _ in range(iters):
        p = packed.probs(beta)
        px = packed.X * p[:, None]
        m = np.add.reduceat(px, packed.starts, axis=0)       # Σ p·x par course
        grad = packed.X[packed.win_rows].sum(axis=0) - m.sum(axis=0) - ridge * beta
        hess = -(packed.X.T @ px - m.T @ m) - ridge * np.eye(k)
        try:
            step = np.linalg.solve(hess, -grad)
        except np.linalg.LinAlgError:
            step = grad * 1e-3
        t, improved = 1.0, False
        while t > 1e-6:
            candidate = beta + t * step
            value = objective(candidate)
            if value >= current - 1e-12:
                improved = True
                break
            t *= 0.5
        if not improved:                     # jamais un pas qui dégrade la vraisemblance
            break
        done = abs(value - current) < tol * max(1.0, abs(current))
        beta, current = candidate, value
        if done:
            break
    return beta


# ── Walk-forward et combinaison ──────────────────────────────────────────
def _month(day: str) -> str:
    return day[:7]


def _standardize(train: np.ndarray, cols: Sequence[int]) -> Tuple[np.ndarray, np.ndarray]:
    mu = train[:, cols].mean(axis=0)
    sd = train[:, cols].std(axis=0)
    sd[sd < 1e-9] = 1.0
    return mu, sd


def fundamental_walk_forward(races: Sequence[Race], feature_names: Sequence[str],
                             first_test_month: str = FIRST_TEST_MONTH,
                             train_start: str = TRAIN_START) -> Dict[str, Any]:
    """Pour chaque mois de test : modèle par groupe (TROT/GALOP) entraîné sur
    les mois précédents, prédictions hors échantillon stockées sur la course."""
    cols = [FEATURES.index(n) for n in feature_names]
    months = sorted({_month(r.day) for r in races if _month(r.day) >= first_test_month})
    coefficients: Dict[str, Dict[str, float]] = {}
    oos: Dict[Tuple[str, int, int], np.ndarray] = {}
    for month in months:
        for group in ("TROT", "GALOP"):
            train = [r for r in races if r.group == group and train_start <= r.day and _month(r.day) < month]
            test = [r for r in races if r.group == group and _month(r.day) == month]
            if len(train) < 200 or not test:
                continue
            mu, sd = _standardize(np.vstack([r.features for r in train]), cols)
            packed = Packed([(r.features[:, cols] - mu) / sd for r in train], [r.winner for r in train])
            beta = fit_clogit(packed)
            for r in test:
                s = ((r.features[:, cols] - mu) / sd) @ beta
                e = np.exp(s - s.max())
                oos[r.key] = e / e.sum()
            if month == months[-1]:
                coefficients[group] = {n: round(float(b), 5) for n, b in zip(feature_names, beta)}
                coefficients[group + "_apprentissage_courses"] = len(train)
    return {"oos": oos, "months": months, "coefficients": coefficients}


def _combo_packed(races: Sequence[Race], oos: Dict, with_fund: bool) -> Packed:
    blocks = []
    for r in races:
        cols = [np.log(np.maximum(r.market, FLOOR))]
        if with_fund:
            cols.append(np.log(np.maximum(oos[r.key], FLOOR)))
        blocks.append(np.column_stack(cols))
    return Packed(blocks, [r.winner for r in races])


def _race_ll(packed: Packed, beta: np.ndarray) -> np.ndarray:
    return np.log(np.maximum(packed.probs(beta)[packed.win_rows], FLOOR))


def bootstrap_ci(values: np.ndarray, n: int = BOOTSTRAP, seed: int = 20260928) -> Tuple[float, float]:
    rng = np.random.default_rng(seed)
    means = np.concatenate([values[rng.integers(0, len(values), size=(100, len(values)))].mean(axis=1)
                            for _ in range(n // 100)])           # par lots : mémoire bornée
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def evaluate(races: Sequence[Race], wf: Dict[str, Any]) -> Dict[str, Any]:
    """Par mois : vraisemblances hors échantillon. Combinaison et marché
    recalibré appris sur les mois de test PRÉCÉDENTS uniquement."""
    oos = wf["oos"]
    folds, pooled_delta, pooled_delta_raw = [], [], []
    history: List[Race] = []
    for month in wf["months"]:
        test = [r for r in races if _month(r.day) == month and r.key in oos]
        if not test:
            continue
        n_run = np.array([len(r.runners) for r in test])
        ll_unif = -np.log(n_run)
        ll_fund = np.array([math.log(max(oos[r.key][r.winner], FLOOR)) for r in test])
        fund_top1 = np.mean([int(np.argmax(oos[r.key]) == r.winner) for r in test])
        fold = {"mois": month, "courses": len(test),
                "ll_uniforme": round(float(ll_unif.mean()), 4), "ll_fondamental": round(float(ll_fund.mean()), 4),
                "r2_fondamental": round(float(1 - ll_fund.sum() / ll_unif.sum()), 4),
                "top1_fondamental": round(float(fund_top1), 4)}
        with_mkt = [r for r in test if r.market is not None]
        fold["courses_avec_marche"] = len(with_mkt)
        if with_mkt:
            ll_mkt = np.array([math.log(max(r.market[r.winner], FLOOR)) for r in with_mkt])
            fold["ll_marche"] = round(float(ll_mkt.mean()), 4)
            fold["top1_marche"] = round(float(np.mean([int(np.argmax(r.market) == r.winner) for r in with_mkt])), 4)
            prior = [r for r in history if r.market is not None]
            if len(prior) >= 500:
                gamma = fit_clogit(_combo_packed(prior, oos, False), ridge=1e-3)
                ab = fit_clogit(_combo_packed(prior, oos, True), ridge=1e-3)
                ll_recal = _race_ll(_combo_packed(with_mkt, oos, False), gamma)
                ll_comb = _race_ll(_combo_packed(with_mkt, oos, True), ab)
                fold.update({"ll_marche_recalibre": round(float(ll_recal.mean()), 4),
                             "ll_combine": round(float(ll_comb.mean()), 4),
                             "gamma_marche": round(float(gamma[0]), 4),
                             "alpha_marche": round(float(ab[0]), 4), "beta_fondamental": round(float(ab[1]), 4),
                             "delta_vs_marche_recalibre": round(float((ll_comb - ll_recal).mean()), 5),
                             "delta_vs_marche_brut": round(float((ll_comb - ll_mkt).mean()), 5)})
                pooled_delta.extend((ll_comb - ll_recal).tolist())
                pooled_delta_raw.extend((ll_comb - ll_mkt).tolist())
        folds.append(fold)
        history.extend(test)

    result: Dict[str, Any] = {"plis": folds}
    if pooled_delta:
        delta = np.array(pooled_delta)
        lo, hi = bootstrap_ci(delta)
        raw = np.array(pooled_delta_raw)
        lo_raw, hi_raw = bootstrap_ci(raw)
        result["combinaison"] = {
            "courses_jugees": len(delta),
            "delta_ll_par_course_vs_marche_recalibre": round(float(delta.mean()), 5),
            "ic95_vs_marche_recalibre": [round(lo, 5), round(hi, 5)],
            "delta_ll_par_course_vs_marche_brut": round(float(raw.mean()), 5),
            "ic95_vs_marche_brut": [round(lo_raw, 5), round(hi_raw, 5)],
            "G1_courses_suffisantes": len(delta) >= G1_MIN_TEST_RACES,
            "avantage_demontre": lo > 0,
        }
    return result


# ── Rapport et envoi sur R2 privé ────────────────────────────────────────
def run(db_path: str) -> Dict[str, Any]:
    races, data_stats = load_races(db_path)
    _log("BENTER_DONNEES", data_stats)
    audit = build_features(races)
    _log("BENTER_AUDIT_FUITE", audit)
    names = [n for n in FEATURES if not (audit["fuite_suspectee"] and n in CAREER_FEATURES)]
    wf = fundamental_walk_forward(races, names)
    ev = evaluate(races, wf)
    for fold in ev["plis"]:
        _log("BENTER_PLI", fold)
    if "combinaison" in ev:
        _log("BENTER_RESULTAT", ev["combinaison"])
    return {
        "genere_le_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "methode": {"apprentissage_depuis": TRAIN_START, "premier_mois_test": FIRST_TEST_MONTH,
                    "ridge": RIDGE, "variables": names, "groupes": ["TROT", "GALOP"],
                    "marche": "cote_reference finale (clôture), normalisée"},
        "donnees": data_stats, "audit_fuite": audit,
        "coefficients_dernier_pli": wf["coefficients"], **ev,
    }


def upload(report: Dict[str, Any], client_factory=None) -> Optional[str]:
    from turf_lab.r2_store import make_client, r2_config
    cfg = r2_config()
    if cfg is None:
        _log("BENTER_ENVOI_IGNORE", {"raison": "secrets R2 absents"})
        return None
    client = (client_factory or make_client)(cfg)
    body = json.dumps(report, ensure_ascii=False, indent=1).encode("utf-8")
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    key = f"{REPORT_PREFIX}fondamental_{report['genere_le_utc'][:10]}_{run_id}.json"
    for k in (key, REPORT_PREFIX + "dernier.json"):
        client.put_object(Bucket=cfg["bucket"], Key=k, Body=body, ContentType="application/json")
    _log("BENTER_RAPPORT_ENVOYE", {"cle": key, "octets": len(body)})
    return key


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Labo Benter : fondamental + combinaison, walk-forward")
    parser.add_argument("--db", default="turf_history.db")
    parser.add_argument("--sortie")
    parser.add_argument("--sans-envoi", action="store_true")
    args = parser.parse_args(argv)
    if not os.path.exists(args.db):
        _log("BENTER_ERREUR", {"erreur": "miroir absent", "chemin": args.db})
        return 2
    report = run(args.db)
    if args.sortie:
        with open(args.sortie, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
    if not args.sans_envoi:
        upload(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
