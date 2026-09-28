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
     Marché = cote de CLÔTURE ``cote_direct`` finale : vérifié le 28/09/2026,
     elle vaut le rapport simple gagnant payé (médiane 1,000). ``cote_reference``
     n'est PAS la clôture (rapport / cote_reference : médiane 0,905, 0,51 à
     1,51) : elle sert de second repère, moins informé.
  5. Étape 3 simulée : mises au Kelly fractionné sur les probabilités
     combinées, réglées au rapport officiel. Décision aux cotes finales :
     BORNE HAUTE (en pari mutuel, la cote finale n'est connue qu'au départ).
     Témoin : la même stratégie sur le marché seul ne doit presque rien miser.
  7. Apport au PRODUIT : sur les courses du banc (``turf_bench.db``), le
     fondamental améliore-t-il les probabilités publiées par chaque moteur à
     l'édition du matin et à T-30 ? Combinaison apprise semaine par semaine.
  6. Horizon réel de pari (T-30, T-15) : cotes de la dernière photo
     ``cotes_snapshots`` prise au moins H minutes avant le départ (au plus
     H+10) ; combinaison apprise semaine par semaine sur les semaines
     précédentes ; mises DÉCIDÉES aux cotes de T-x, RÉGLÉES au rapport final.
     C'est la simulation réaliste (données depuis le 15/07/2026).

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
from typing import Any, Dict, Hashable, List, Optional, Sequence, Tuple

import numpy as np

from turf_lab import ombre

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
KELLY_FRACTION = 0.25           # quart de Kelly
EV_MIN = 0.05                   # espérance minimale d'un pari (+5 %)
MAX_RACE_EXPOSURE = 0.05        # au plus 5 % de la bankroll par course
TX_HORIZONS = (30, 15, 5)       # minutes avant le départ
BENCH_ENGINES = ("MARKET_BASELINE", "NEW_VALUE_ENGINE", "RADAR_V4")   # ETPE : pas de probabilités publiées
BENCH_HORIZONS = ("T_MATIN", "T90", "T30")     # T90 : l'édition des joueurs au guichet
BENCH_MIN_PRIOR = 300
_RACE_ID = re.compile(r"^R(\d+)C(\d+)_(\d{2})(\d{2})(\d{4})")
TX_MAX_STALENESS = 10           # la photo doit dater d'au plus H+10 minutes
TX_MIN_PRIOR = 400              # courses minimales pour apprendre la combinaison à T-x
ODDS_BUCKETS = (("cote_<5", 1.0, 5.0), ("cote_5-15", 5.0, 15.0), ("cote_>15", 15.0, float("inf")))

FEATURES = (
    "taux_victoires", "taux_places", "log_courses", "log_gains", "log_gains_annee",
    "log_gain_par_course", "age", "age2", "femelle", "male", "oeilleres", "oeilleres_austr",
    "driver_change", "corde_rel", "corde_absente", "poids_rel", "poids_absent",
    "mus_moyenne", "mus_victoires", "mus_top3", "mus_fautes", "mus_derniere", "mus_absente",
    "log_jours_repos", "premiere_vue", "derniere_place",
    "jockey_taux", "jockey_log_montes", "entraineur_taux", "entraineur_log_courses",
)
# Variables susceptibles de changer dans la journée (driver remplacé, œillères
# modifiées) : exclues du test de robustesse « état connu au matin ».
INTRADAY_FEATURES = ("driver_change", "jockey_taux", "jockey_log_montes", "oeilleres", "oeilleres_austr")
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
    __slots__ = ("key", "day", "group", "runners", "features", "winner", "market", "market_ref",
                 "odds", "dividend", "market_tx", "odds_tx")

    def __init__(self, key: Tuple[str, int, int], day: str, group: str):
        self.key, self.day, self.group = key, day, group
        self.runners: List[Dict[str, Any]] = []
        self.features: Optional[np.ndarray] = None
        self.winner: Optional[int] = None
        self.market: Optional[np.ndarray] = None       # clôture (cote_direct), normalisé
        self.market_ref: Optional[np.ndarray] = None   # cote_reference, normalisé
        self.odds: Optional[np.ndarray] = None         # cotes de clôture brutes
        self.dividend: Optional[float] = None          # rapport officiel simple gagnant
        self.market_tx: Dict[int, np.ndarray] = {}     # horizon (min) -> marché normalisé à T-x
        self.odds_tx: Dict[int, np.ndarray] = {}       # horizon (min) -> cotes brutes à T-x


def _implied(odds: Sequence[Optional[float]]) -> Optional[np.ndarray]:
    if not all(o is not None and o > 1.0 for o in odds):
        return None
    inv = np.array([1.0 / o for o in odds])
    return inv / inv.sum()


def pick_snapshot(minutes_available, horizon: int) -> Optional[int]:
    """Dernière photo prise AU MOINS ``horizon`` minutes avant le départ, et
    pas plus de TX_MAX_STALENESS minutes plus tôt ; sinon aucune."""
    eligible = [m for m in minutes_available if horizon <= m <= horizon + TX_MAX_STALENESS]
    return min(eligible) if eligible else None


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
    dividends: Dict[Tuple[str, int, int], Optional[Tuple[str, Optional[float]]]] = {}
    for row in conn.execute("SELECT date_course, num_reunion, num_course, combinaison, dividende_pour_1e "
                            "FROM rapports_definitifs WHERE type_pari = 'SIMPLE_GAGNANT' "
                            "AND libelle = 'Simple gagnant'"):
        key = (row[0], int(row[1]), int(row[2]))
        dividends[key] = None if key in dividends else (str(row[3]), _f(row[4]))   # doublon : ambigu
    # course -> partant -> minute -> cote (photo retenue cheval par cheval : les
    # partants d'une même capture ne tombent pas toujours sur la même minute)
    snaps: Dict[Tuple[str, int, int], Dict[int, Dict[int, float]]] = defaultdict(lambda: defaultdict(dict))
    for row in conn.execute("SELECT date_course, num_reunion, num_course, num_pmu, cote, minutes_avant_depart "
                            "FROM cotes_snapshots WHERE cote IS NOT NULL AND minutes_avant_depart IS NOT NULL"):
        snaps[(row[0], int(row[1]), int(row[2]))][int(row[3])][int(row[5])] = float(row[4])
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
        close = [_f(r["cote_direct"]) for r in race.runners]
        race.market = _implied(close)
        if race.market is not None:
            race.odds = np.array(close)
        race.market_ref = _implied([_f(r["cote_reference"]) for r in race.runners])
        div = dividends.get(race.key)
        if div and div[1] and div[0] == str(race.runners[race.winner]["num_pmu"]):
            race.dividend = div[1]
        race_snaps = snaps.get(race.key) or {}
        for horizon in TX_HORIZONS:
            tx = []
            for r in race.runners:
                runner_snaps = race_snaps.get(int(r["num_pmu"])) or {}
                minute = pick_snapshot(runner_snaps.keys(), horizon)
                tx.append(runner_snaps[minute] if minute is not None else None)
            implied = _implied(tx)
            if implied is not None:
                race.market_tx[horizon], race.odds_tx[horizon] = implied, np.array(tx)
        kept.append(race)
    kept.sort(key=lambda r: (r.day, str(r.runners[0]["heure_depart"] or ""), r.key))
    stats["courses_retenues"] = len(kept)
    stats["courses_trot"] = sum(r.group == "TROT" for r in kept)
    stats["courses_galop"] = sum(r.group == "GALOP" for r in kept)
    stats["partants"] = sum(len(r.runners) for r in kept)
    stats["courses_avec_marche"] = sum(r.market is not None for r in kept)
    stats["courses_avec_marche_reference"] = sum(r.market_ref is not None for r in kept)
    stats["courses_avec_rapport_officiel"] = sum(r.dividend is not None for r in kept)
    for horizon in TX_HORIZONS:
        stats[f"courses_avec_cotes_T{horizon}"] = sum(horizon in r.market_tx for r in kept)
    overround = [float((1.0 / r.odds).sum()) for r in kept if r.odds is not None]
    stats["surround_moyen_cloture"] = round(float(np.mean(overround)), 4) if overround else None
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


def _combo_packed(races: Sequence[Race], oos: Dict, with_fund: bool, attr="market") -> Packed:
    """``attr`` : nom d'attribut de marché, ou fonction course -> marché."""
    market_of = attr if callable(attr) else (lambda r: getattr(r, attr))
    blocks = []
    for r in races:
        cols = [np.log(np.maximum(market_of(r), FLOOR))]
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


def evaluate(races: Sequence[Race], wf: Dict[str, Any], attr: str = "market") -> Dict[str, Any]:
    """Par mois : vraisemblances hors échantillon face au marché ``attr``.
    Combinaison et marché recalibré appris sur les mois de test PRÉCÉDENTS
    uniquement. Garde aussi, par course, les probabilités combinées et de
    marché recalibré (pour la simulation de mises ; jamais publiées)."""
    oos = wf["oos"]
    folds, pooled_delta, pooled_delta_raw = [], [], []
    comb_probs: Dict[Tuple[str, int, int], np.ndarray] = {}
    recal_probs: Dict[Tuple[str, int, int], np.ndarray] = {}
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
        with_mkt = [r for r in test if getattr(r, attr) is not None]
        fold["courses_avec_marche"] = len(with_mkt)
        if with_mkt:
            ll_mkt = np.array([math.log(max(getattr(r, attr)[r.winner], FLOOR)) for r in with_mkt])
            fold["ll_marche"] = round(float(ll_mkt.mean()), 4)
            fold["top1_marche"] = round(float(np.mean([int(np.argmax(getattr(r, attr)) == r.winner)
                                                       for r in with_mkt])), 4)
            prior = [r for r in history if getattr(r, attr) is not None]
            if len(prior) >= 500:
                gamma = fit_clogit(_combo_packed(prior, oos, False, attr), ridge=1e-3)
                ab = fit_clogit(_combo_packed(prior, oos, True, attr), ridge=1e-3)
                recal_pack = _combo_packed(with_mkt, oos, False, attr)
                comb_pack = _combo_packed(with_mkt, oos, True, attr)
                ll_recal = _race_ll(recal_pack, gamma)
                ll_comb = _race_ll(comb_pack, ab)
                pc, pr = comb_pack.probs(ab), recal_pack.probs(gamma)
                for r, start in zip(with_mkt, comb_pack.starts):
                    comb_probs[r.key] = pc[start:start + len(r.runners)]
                    recal_probs[r.key] = pr[start:start + len(r.runners)]
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

    result: Dict[str, Any] = {"plis": folds, "_comb": comb_probs, "_recal": recal_probs}
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


# ── Horizon réel de pari : T-30, T-15 ────────────────────────────────────
def evaluate_tx(races: Sequence[Race], oos: Dict, horizon: int) -> Dict[str, Any]:
    """Combinaison à T-x apprise semaine par semaine sur les semaines
    précédentes (données depuis le 15/07/2026). Mesure le gain face au
    marché de T-x recalibré, l'information arrivée ensuite (clôture), puis
    simule les mises décidées à T-x et réglées au rapport final."""
    pool = [r for r in races if horizon in r.market_tx and r.key in oos]
    by_week: Dict[Tuple[int, int], List[Race]] = defaultdict(list)
    for r in pool:
        by_week[date.fromisoformat(r.day).isocalendar()[:2]].append(r)
    market_of = lambda r: r.market_tx[horizon]                                   # noqa: E731
    comb_probs, recal_probs = {}, {}
    deltas, late, betas = [], [], []
    history: List[Race] = []
    for week in sorted(by_week):
        test = by_week[week]
        if len(history) >= TX_MIN_PRIOR:
            gamma = fit_clogit(_combo_packed(history, oos, False, market_of), ridge=1e-3)
            ab = fit_clogit(_combo_packed(history, oos, True, market_of), ridge=1e-3)
            betas.append(float(ab[1]))
            recal_pack = _combo_packed(test, oos, False, market_of)
            comb_pack = _combo_packed(test, oos, True, market_of)
            ll_recal, ll_comb = _race_ll(recal_pack, gamma), _race_ll(comb_pack, ab)
            deltas.extend((ll_comb - ll_recal).tolist())
            pc, pr = comb_pack.probs(ab), recal_pack.probs(gamma)
            for r, start in zip(test, comb_pack.starts):
                comb_probs[r.key] = pc[start:start + len(r.runners)]
                recal_probs[r.key] = pr[start:start + len(r.runners)]
                if r.market is not None:                 # information arrivée entre T-x et la clôture
                    late.append(math.log(max(r.market[r.winner], FLOOR))
                                - math.log(max(r.market_tx[horizon][r.winner], FLOOR)))
        history.extend(test)
    out: Dict[str, Any] = {"horizon_min": horizon, "courses_avec_cotes": len(pool), "courses_jugees": len(deltas)}
    if deltas:
        arr = np.array(deltas)
        lo, hi = bootstrap_ci(arr)
        out.update({"delta_ll_vs_marche_T_recalibre": round(float(arr.mean()), 5),
                    "ic95": [round(lo, 5), round(hi, 5)], "avantage_demontre": lo > 0,
                    "beta_fondamental_moyen": round(float(np.mean(betas)), 4),
                    "gain_ll_cloture_vs_T": round(float(np.mean(late)), 5) if late else None})
        decide = lambda r: r.odds_tx.get(horizon)                                 # noqa: E731
        out["kelly"] = simulate_kelly(races, comb_probs, decide)
        temoin = simulate_kelly(races, recal_probs, decide)
        out["kelly_temoin_marche_seul"] = {k: temoin.get(k) for k in ("paris", "courses_jouees", "roi_mise_fixe",
                                                                      "ic95_roi_mise_fixe")}
    return out


# ── Apport au produit : moteurs du banc + fondamental ────────────────────
def load_bench(path: str, horizons: Sequence[str] = BENCH_HORIZONS
               ) -> Dict[Tuple[str, str], Dict[Tuple[str, int, int], Dict[str, Any]]]:
    """(moteur, horizon) -> course -> édition publiée : probabilités, métadonnées,
    sélection et drapeau de cotes réelles (pour les découpages du dev NVE)."""
    out: Dict[Tuple[str, str], Dict[Tuple[str, int, int], Dict[str, Any]]] = defaultdict(dict)
    conn = sqlite3.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(predictions)")}
    extra = [c for c in ("metadata_json", "selection_json", "odds_real") if c in cols]
    marks = ",".join("?" * len(BENCH_ENGINES)), ",".join("?" * len(horizons))
    query = (f"SELECT race_id, engine_name, horizon, probabilities_json{''.join(', ' + c for c in extra)} "
             f"FROM predictions WHERE engine_name IN ({marks[0]}) AND horizon IN ({marks[1]})")
    for row in conn.execute(query, (*BENCH_ENGINES, *horizons)):
        race_id, engine, horizon, probs = row[:4]
        rest = dict(zip(extra, row[4:]))
        m = _RACE_ID.match(str(race_id or ""))
        if not m or not probs:
            continue
        try:
            parsed = {int(k): float(v) for k, v in json.loads(probs).items()}
            meta = json.loads(rest.get("metadata_json") or "{}")
            selection = json.loads(rest.get("selection_json") or "[]")
        except (TypeError, ValueError):
            continue
        key = (f"{m.group(5)}-{m.group(4)}-{m.group(3)}", int(m.group(1)), int(m.group(2)))
        out[(engine, horizon)][key] = {"probs": parsed, "meta": meta if isinstance(meta, dict) else {},
                                       "selection": selection, "odds_real": rest.get("odds_real")}
    conn.close()
    return out


def _engine_probs(race: Race, published: Dict[int, float]) -> Optional[np.ndarray]:
    """Probabilités d'un moteur alignées sur les partants au départ (il en faut
    pour tous), renormalisées sur ces partants comme le fondamental."""
    vals = [published.get(int(r["num_pmu"])) for r in race.runners]
    if any(v is None or v <= 0 for v in vals):
        return None
    arr = np.array(vals, dtype=float)
    return arr / arr.sum()


def _is_nve_production(edition: Dict[str, Any]) -> bool:
    """Découpage (1) du dev NVE : éditions de production actuelles."""
    cal = (edition.get("meta") or {}).get("market_calibration") or {}
    return cal.get("applied") is True and abs(float(cal.get("market_weight") or 0.0) - 0.9) < 1e-9


def _is_market_informative(edition: Dict[str, Any]) -> bool:
    """Découpage (2) : règle officielle du banc (baselines.market_edition_informative)."""
    from turf_lab.baselines import market_edition_informative
    return market_edition_informative({"selection": edition.get("selection"), "odds_real": edition.get("odds_real"),
                                       "probabilities": {str(k): v for k, v in edition["probs"].items()}})


# (moteur, découpage, filtre, portée du filtre). « juger » : la combinaison est
# apprise sur TOUTES les éditions passées du moteur (les éditions de
# production sont trop récentes pour 300 courses d'apprentissage) mais jugée
# sur les seules éditions filtrées ; « tout » : filtrées partout.
BENCH_LINES = (
    ("MARKET_BASELINE", "toutes", None, "tout"),
    ("MARKET_BASELINE", "informatives", _is_market_informative, "tout"),
    ("NEW_VALUE_ENGINE", "toutes", None, "tout"),
    ("NEW_VALUE_ENGINE", "production_poids_0.9", _is_nve_production, "juger"),
    ("RADAR_V4", "toutes", None, "tout"),
)


def _paired_ci(values: Sequence[float]) -> List[float]:
    lo, hi = bootstrap_ci(np.asarray(values, dtype=float))
    return [round(lo, 5), round(hi, 5)]


def _bench_line(races: Sequence[Race], oos: Dict, probs: Dict[Tuple[str, int, int], np.ndarray],
                judge: Optional[set] = None) -> Dict[str, Any]:
    """Combinaison moteur + fondamental apprise semaine par semaine sur le passé ;
    gain de log-vraisemblance face au moteur seul recalibré et gain APPARIÉ de
    gagnants en tête (même course, combiné − moteur), chacun avec IC 95 %."""
    pool = [r for r in races if r.key in probs]
    by_week: Dict[Tuple[int, int], List[Race]] = defaultdict(list)
    for r in pool:
        by_week[date.fromisoformat(r.day).isocalendar()[:2]].append(r)
    market_of = lambda r: probs[r.key]                                           # noqa: E731
    deltas, betas, ll_eng, top_diff, top_eng = [], [], [], [], []
    history: List[Race] = []
    for week in sorted(by_week):
        test = by_week[week]
        if len(history) >= BENCH_MIN_PRIOR:
            gamma = fit_clogit(_combo_packed(history, oos, False, market_of), ridge=1e-3)
            ab = fit_clogit(_combo_packed(history, oos, True, market_of), ridge=1e-3)
            betas.append(float(ab[1]))
            recal_pack = _combo_packed(test, oos, False, market_of)
            comb_pack = _combo_packed(test, oos, True, market_of)
            race_delta = _race_ll(comb_pack, ab) - _race_ll(recal_pack, gamma)
            pc = comb_pack.probs(ab)
            for r, start, delta in zip(test, comb_pack.starts, race_delta):
                if judge is not None and r.key not in judge:
                    continue                           # apprise ici, jugée ailleurs
                deltas.append(float(delta))
                ll_eng.append(math.log(max(probs[r.key][r.winner], FLOOR)))
                t_eng = int(np.argmax(probs[r.key]) == r.winner)
                t_comb = int(np.argmax(pc[start:start + len(r.runners)]) == r.winner)
                top_eng.append(t_eng)
                top_diff.append(t_comb - t_eng)
        history.extend(test)
    entry: Dict[str, Any] = {"courses": len(pool) if judge is None else len(judge & set(probs)),
                             "courses_jugees": len(deltas)}
    if deltas:
        arr = np.array(deltas)
        lo, hi = bootstrap_ci(arr)
        entry.update({"ll_moteur": round(float(np.mean(ll_eng)), 4),
                      "delta_ll_vs_moteur_recalibre": round(float(arr.mean()), 5),
                      "ic95": [round(lo, 5), round(hi, 5)], "apport_demontre": lo > 0,
                      "beta_fondamental_moyen": round(float(np.mean(betas)), 4),
                      "top1_moteur": round(float(np.mean(top_eng)), 4),
                      "gain_top1": round(float(np.mean(top_diff)), 4), "ic95_gain_top1": _paired_ci(top_diff)})
    return entry


def _fund_vs_pure(races: Sequence[Race], oos: Dict, editions: Dict[Tuple[str, int, int], Dict[str, Any]]) -> Dict[str, Any]:
    """Fondamental SEUL face au modèle NVE pur (``model_probs``, avant tout
    mélange avec le marché), sur les mêmes courses : écarts appariés."""
    d_ll, d_top, ll_f, ll_n, top_f, top_n = [], [], [], [], [], []
    for r in races:
        ed = editions.get(r.key)
        if ed is None or r.key not in oos:
            continue
        pure = _engine_probs(r, {int(k): float(v) for k, v in ((ed.get("meta") or {}).get("model_probs") or {}).items()})
        if pure is None:
            continue
        f = oos[r.key]
        lf, ln = math.log(max(f[r.winner], FLOOR)), math.log(max(pure[r.winner], FLOOR))
        tf, tn = int(np.argmax(f) == r.winner), int(np.argmax(pure) == r.winner)
        ll_f.append(lf), ll_n.append(ln), top_f.append(tf), top_n.append(tn)
        d_ll.append(lf - ln), d_top.append(tf - tn)
    if not d_ll:
        return {"courses": 0}
    return {"courses": len(d_ll), "ll_fondamental": round(float(np.mean(ll_f)), 4),
            "ll_nve_pur": round(float(np.mean(ll_n)), 4),
            "delta_ll_fondamental_moins_nve_pur": round(float(np.mean(d_ll)), 5), "ic95": _paired_ci(d_ll),
            "top1_fondamental": round(float(np.mean(top_f)), 4), "top1_nve_pur": round(float(np.mean(top_n)), 4),
            "delta_top1": round(float(np.mean(d_top)), 4), "ic95_delta_top1": _paired_ci(d_top)}


def _nve_composition(editions: Dict[Tuple[str, int, int], Dict[str, Any]], keys) -> Dict[str, int]:
    """Composition des éditions NVE jugées : sans marché, poids 0,70, 0,90…"""
    counts: Dict[str, int] = defaultdict(int)
    for key in keys:
        cal = (editions[key].get("meta") or {}).get("market_calibration") or {}
        label = "sans_marche" if cal.get("applied") is not True else f"poids_{float(cal.get('market_weight') or 0):.2f}"
        counts[label] += 1
    return dict(counts)


def evaluate_bench(races: Sequence[Race], oos: Dict, bench: Dict,
                   horizons: Sequence[str] = BENCH_HORIZONS) -> Dict[str, Any]:
    """Découpages demandés par le dev NVE (28/09) : pour chaque moteur, horizon
    et découpage, apport du fondamental ; au matin, fondamental seul face au
    NVE pur ; composition des éditions NVE jugées."""
    results: Dict[str, Any] = {}
    for horizon in horizons:
        for engine, cut, keep, scope in BENCH_LINES:
            editions = bench.get((engine, horizon)) or {}
            probs: Dict[Tuple[str, int, int], np.ndarray] = {}
            judge: Optional[set] = set() if scope == "juger" else None
            excluded = 0
            for r in races:
                ed = editions.get(r.key)
                if ed is None or r.key not in oos:
                    continue
                kept = keep is None or keep(ed)
                if not kept:
                    excluded += 1
                    if scope == "tout":
                        continue
                arr = _engine_probs(r, ed["probs"])
                if arr is not None:
                    probs[r.key] = arr
                    if judge is not None and kept:
                        judge.add(r.key)
            entry = {"moteur": engine, "horizon": horizon, "decoupage": cut, "editions_exclues": excluded,
                     **_bench_line(races, oos, probs, judge)}
            if engine == "NEW_VALUE_ENGINE":
                entry["composition"] = _nve_composition(editions, judge if judge is not None else probs.keys())
            results[f"{engine}_{horizon}_{cut}"] = entry
        nve = bench.get(("NEW_VALUE_ENGINE", horizon)) or {}
        if horizon == "T_MATIN" and nve:
            results["FONDAMENTAL_vs_NVE_PUR_T_MATIN_toutes"] = {
                "horizon": horizon, "decoupage": "toutes", **_fund_vs_pure(races, oos, nve)}
            prod = {k: v for k, v in nve.items() if _is_nve_production(v)}
            results["FONDAMENTAL_vs_NVE_PUR_T_MATIN_production_poids_0.9"] = {
                "horizon": horizon, "decoupage": "production_poids_0.9", **_fund_vs_pure(races, oos, prod)}
    return results


# ── Ombre du fondamental dans NVE : calcul de puissance avant le gel ─────
def _nve_rows(races: Sequence[Race], oos: Dict, editions: Dict[Tuple[str, int, int], Dict[str, Any]]
              ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Éditions NVE avec marché, alignées sur les partants au départ : part
    marché reconstruite (publié = poids × marché + (1 − poids) × modèle),
    fondamental « état du matin », sélection publiée, gagnant et tiercé."""
    rows: List[Dict[str, Any]] = []
    excl: Dict[str, int] = defaultdict(int)
    for r in races:
        ed = editions.get(r.key)
        if ed is None or r.key not in oos:
            continue
        meta = ed.get("meta") or {}
        cal = meta.get("market_calibration") or {}
        weight = float(cal.get("market_weight") or 0.0)
        if cal.get("applied") is not True or not 0.0 < weight < 1.0:
            excl["sans_marche"] += 1
            continue
        nums = [int(x["num_pmu"]) for x in r.runners]
        model = {int(k): float(v) for k, v in (meta.get("model_probs") or {}).items()}
        if set(ed["probs"]) != set(nums):
            excl["partants_differents_au_verrou"] += 1            # non-partant tardif : hors calcul
            continue
        if any(n not in model for n in nums):
            excl["sans_modele_pur"] += 1
            continue
        pub = np.array([ed["probs"][n] for n in nums], dtype=float)
        mod = np.array([model[n] for n in nums], dtype=float)
        placed = sorted((int(x["ordre_arrivee"]), int(x["num_pmu"])) for x in r.runners
                        if isinstance(x.get("ordre_arrivee"), (int, float)) and 1 <= int(x["ordre_arrivee"]) <= 3)
        rows.append({"race": r, "reunion": (r.day, r.key[1]), "weight": weight, "production": abs(weight - 0.9) < 1e-9,
                     "nums": nums, "pub": pub, "market": ombre.marche_de_edition(pub, mod, weight),
                     "fond": oos[r.key], "selection": [int(x) for x in ed.get("selection") or []],
                     "tierce": [n for _, n in placed] if len(placed) >= 3 else None})
    return rows, dict(excl)


def _secondary(pairs: List[Tuple[int, int, Hashable]], n0: int) -> Dict[str, Any]:
    """Écart apparié ombre − publié d'un taux « dans les 8 », IC95 par réunion
    et borne basse projetée aux deux lectures (si l'écart reste le même)."""
    if not pairs:
        return {"editions": 0}
    diff = [o - p for p, o, _ in pairs]
    clusters = [c for _, _, c in pairs]
    mean = float(np.mean(diff))
    lo, hi = ombre.intervalle(diff, clusters, 0.95)
    se = ombre.erreur_type(diff, clusters)
    return {"editions": len(pairs), "publie": round(float(np.mean([p for p, _, _ in pairs])), 4),
            "ombre": round(float(np.mean([o for _, o, _ in pairs])), 4), "ecart": round(mean, 4),
            "ic95": [round(lo, 4), round(hi, 4)], "erreur_type": round(se, 5),
            "borne_basse_projetee_1000": round(ombre.borne_basse_projetee(mean, se, n0, ombre.LECTURE_1), 4),
            "borne_basse_projetee_2300": round(ombre.borne_basse_projetee(mean, se, n0, ombre.LECTURE_2), 4),
            # Risque d'échec du test (borne basse IC95 < −2 points) si l'écart
            # vrai est celui observé : un blocage « à tort » quand l'écart est nul.
            "risque_echec_1000": round(ombre.risque_echec_non_degradation(mean, se, n0, ombre.LECTURE_1), 4),
            "risque_echec_2300": round(ombre.risque_echec_non_degradation(mean, se, n0, ombre.LECTURE_2), 4),
            "risque_echec_1000_si_ecart_nul": round(ombre.risque_echec_non_degradation(0.0, se, n0, ombre.LECTURE_1), 4)}


def _primary(deltas: List[float], clusters: List[Hashable]) -> Dict[str, Any]:
    """Δll ombre − publié : intervalles par réunion et puissance aux deux lectures."""
    if len(deltas) < 2:
        return {"editions": len(deltas)}
    mean = float(np.mean(deltas))
    se = ombre.erreur_type(deltas, clusters)
    n0 = len(deltas)
    p1 = ombre.puissance(mean, se, n0, ombre.LECTURE_1, ombre.NIVEAU_LECTURE_1)
    p2 = ombre.puissance(mean, se, n0, ombre.LECTURE_2, ombre.NIVEAU_LECTURE_2)
    return {"editions": n0, "reunions": len(set(clusters)), "delta_ll": round(mean, 5),
            "ic95": [round(v, 5) for v in ombre.intervalle(deltas, clusters, 0.95)],
            "ic99": [round(v, 5) for v in ombre.intervalle(deltas, clusters, 0.99)],
            "erreur_type": round(se, 5), "puissance_1000_ic99": round(p1, 3), "puissance_2300_ic95": round(p2, 3),
            "detectable_2300": bool(p2 >= ombre.PUISSANCE_MIN)}


def evaluate_shadow_power(races: Sequence[Race], oos: Dict, bench: Dict,
                          horizons: Sequence[str] = ombre.HORIZONS_SECONDAIRES) -> Dict[str, Any]:
    """Point 4 de la règle (avant le gel) : sur les éditions NVE de production
    passées et le fondamental « état du matin » hors échantillon, valeur
    rétrospective des deux recettes face à l'édition publiée.
    A : 0,90 marché + 0,10 fondamental (linéaire, sans apprentissage).
    B : marché^a × fondamental^b, (a, b) réappris chaque lundi sur les seules
    éditions passées (toutes éditions NVE avec marché), jugée en production.
    B0 : marché^a seul (part de B due au seul recalibrage du marché).
    Critère principal au matin ; « dans les 8 » (gagnant, tiercé) aux quatre
    horizons. Aucune donnée d'ombre : tout est rétrospectif."""
    out: Dict[str, Any] = {"definition_detectable": (
        f"puissance >= {ombre.PUISSANCE_MIN:.0%} d'une borne basse IC95 positive à {ombre.LECTURE_2} éditions, "
        "effet estimé et erreur-type par réunion (fixé avant le calcul)")}
    for horizon in horizons:
        rows, excl = _nve_rows(races, oos, bench.get(("NEW_VALUE_ENGINE", horizon)) or {})
        by_week: Dict[Tuple[int, int], List[Dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_week[date.fromisoformat(row["race"].day).isocalendar()[:2]].append(row)
        acc = {k: {"d": [], "c": [], "gagnant": [], "tierce": []} for k in ("A", "B", "B0")}
        weights_b: List[Tuple[float, float]] = []
        history: List[Dict[str, Any]] = []
        for week in sorted(by_week):
            test = by_week[week]
            ab = a0 = None
            if len(history) >= BENCH_MIN_PRIOR:
                blocks = [np.column_stack([np.log(np.maximum(h["market"], FLOOR)), np.log(np.maximum(h["fond"], FLOOR))])
                          for h in history]
                winners = [h["race"].winner for h in history]
                ab = fit_clogit(Packed(blocks, winners), ridge=1e-3)
                a0 = fit_clogit(Packed([b[:, :1] for b in blocks], winners), ridge=1e-3)
                weights_b.append((float(ab[0]), float(ab[1])))
            for row in test:
                if not row["production"]:
                    continue
                w, nums, pub = row["race"].winner, row["nums"], row["pub"]
                shadows = {"A": ombre.ombre_lineaire(row["market"], row["fond"])}
                if ab is not None:
                    shadows["B"] = ombre.ombre_loglineaire(row["market"], row["fond"], float(ab[0]), float(ab[1]))
                    shadows["B0"] = ombre.ombre_loglineaire(row["market"], None, float(a0[0]))
                pub_sel = row["selection"] or ombre.top8(nums, pub)
                for key, sh in shadows.items():
                    a = acc[key]
                    a["d"].append(math.log(max(sh[w], FLOOR)) - math.log(max(pub[w], FLOOR)))
                    a["c"].append(row["reunion"])
                    sel = ombre.top8(nums, sh)
                    a["gagnant"].append((int(ombre.dans_les_8(pub_sel, [nums[w]])),
                                         int(ombre.dans_les_8(sel, [nums[w]])), row["reunion"]))
                    if row["tierce"]:
                        a["tierce"].append((int(ombre.dans_les_8(pub_sel, row["tierce"])),
                                            int(ombre.dans_les_8(sel, row["tierce"])), row["reunion"]))
            history.extend(test)
        entry: Dict[str, Any] = {"editions_avec_marche": len(rows),
                                 "editions_production": sum(r["production"] for r in rows), "exclusions": excl}
        for key, a in acc.items():
            recipe: Dict[str, Any] = _primary(a["d"], a["c"])
            if key != "B0":
                n0 = max(1, len(a["d"]))
                recipe["gagnant_dans_8"] = _secondary(a["gagnant"], n0)
                recipe["tierce_dans_8"] = _secondary(a["tierce"], n0)
            if key == "B" and weights_b:
                recipe["poids_moyens"] = {"marche": round(float(np.mean([x for x, _ in weights_b])), 4),
                                          "fondamental": round(float(np.mean([y for _, y in weights_b])), 4)}
            entry[key] = recipe
        out[horizon] = entry
    # Risque que l'un au moins des huit tests secondaires échoue (tests
    # supposés indépendants : borne prudente, les tests sont corrélés).
    for key in ("A", "B"):
        risks = {n: [] for n in ("risque_echec_1000", "risque_echec_2300", "risque_echec_1000_si_ecart_nul")}
        for horizon in horizons:
            for crit in ("gagnant_dans_8", "tierce_dans_8"):
                test = ((out.get(horizon) or {}).get(key) or {}).get(crit) or {}
                for n in risks:
                    if n in test:
                        risks[n].append(test[n])
        out[f"secondaires_{key}"] = {"tests_mesures": len(risks["risque_echec_1000"]),
                                     **{"au_moins_un_" + n: round(1.0 - float(np.prod([1.0 - r for r in v])), 4)
                                        for n, v in risks.items() if v}}
    # Rythme des éditions de production au matin (14 derniers jours du banc) :
    # durée probable de l'ombre, donc du gel symétrique de NVE.
    days = sorted(r["race"].day for r in _nve_rows(races, oos, bench.get(("NEW_VALUE_ENGINE", "T_MATIN")) or {})[0]
                  if r["production"])
    if days:
        last = date.fromisoformat(days[-1])
        recent = [d for d in days if (last - date.fromisoformat(d)).days < 14]
        span = (last - date.fromisoformat(min(recent))).days + 1
        rate = len(recent) / span
        out["rythme_production_matin"] = {"editions_par_jour_14j": round(rate, 1), "jours_couverts": span,
                                          "jours_pour_1000": math.ceil(ombre.LECTURE_1 / rate) if rate else None,
                                          "jours_pour_2300": math.ceil(ombre.LECTURE_2 / rate) if rate else None}
    matin = (out.get("T_MATIN") or {}).get("A") or {}
    out["recette_principale_selon_regle"] = "A_lineaire_0.90_0.10" if matin.get("detectable_2300") else "B_loglineaire"
    return out


# ── Étape 3 simulée : Kelly fractionné ───────────────────────────────────
def _roi(staked: float, returned: float) -> Optional[float]:
    return round(returned / staked - 1.0, 4) if staked else None


def simulate_kelly(races: Sequence[Race], probs: Dict[Tuple[str, int, int], np.ndarray],
                   decision_odds=None) -> Dict[str, Any]:
    """Mises sur chaque cheval dont l'espérance p·cote − 1 dépasse EV_MIN :
    quart de Kelly, au plus MAX_RACE_EXPOSURE de la bankroll par course. La
    décision se prend aux cotes ``decision_odds(course)`` (par défaut la
    clôture) ; le gain est TOUJOURS réglé au rapport final (officiel, à défaut
    cote de clôture). Deux lectures : mise fixe (1 par pari, ROI robuste) et
    Kelly (bankroll, drawdown)."""
    decision_odds = decision_odds or (lambda race: race.odds)
    bankroll, peak, max_dd = 1.0, 1.0, 0.0
    flat_s = flat_r = kelly_s = kelly_r = 0.0
    n_bets = 0
    per_race: List[Tuple[float, float]] = []
    months: Dict[str, List[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    buckets: Dict[str, List[float]] = {name: [0.0, 0.0, 0.0] for name, _, _ in ODDS_BUCKETS}
    settled = {"rapport_officiel": 0, "cote_finale": 0}
    for r in races:
        o = decision_odds(r) if r.key in probs else None
        if o is None:
            continue
        p = probs[r.key]
        ev = p * o - 1.0
        idx = [int(i) for i in np.where(ev > EV_MIN)[0]]
        if not idx:
            continue
        final = r.dividend if r.dividend else (float(r.odds[r.winner]) if r.odds is not None else None)
        if final is None:
            continue                                  # rapport final inconnu : course non réglable
        payout = final
        won = r.winner in idx
        if won:
            settled["rapport_officiel" if r.dividend else "cote_finale"] += 1
        fs, fr = float(len(idx)), (payout if won else 0.0)
        flat_s, flat_r, n_bets = flat_s + fs, flat_r + fr, n_bets + len(idx)
        per_race.append((fs, fr))
        month = months[_month(r.day)]
        month[0], month[1], month[2] = month[0] + fs, month[1] + fr, month[2] + len(idx)
        for i in idx:
            for name, lo, hi in ODDS_BUCKETS:
                if lo <= o[i] < hi:
                    b = buckets[name]
                    b[0], b[1], b[2] = b[0] + 1.0, b[1] + (payout if i == r.winner else 0.0), b[2] + 1
        f = KELLY_FRACTION * ev[idx] / (o[idx] - 1.0)
        if f.sum() > MAX_RACE_EXPOSURE:
            f *= MAX_RACE_EXPOSURE / f.sum()
        stakes = f * bankroll
        ret = float(stakes[idx.index(r.winner)] * payout) if won else 0.0
        kelly_s, kelly_r = kelly_s + float(stakes.sum()), kelly_r + ret
        bankroll += ret - float(stakes.sum())
        peak = max(peak, bankroll)
        max_dd = max(max_dd, 1.0 - bankroll / peak)

    out: Dict[str, Any] = {"paris": n_bets, "courses_jouees": len(per_race),
                           "roi_mise_fixe": _roi(flat_s, flat_r), "reglement": settled}
    if per_race:
        arr = np.array(per_race)
        rng = np.random.default_rng(20260928)
        rois = []
        for _ in range(BOOTSTRAP // 100):
            idx = rng.integers(0, len(arr), size=(100, len(arr)))
            s, rr = arr[idx, 0].sum(axis=1), arr[idx, 1].sum(axis=1)
            rois.append(rr / s - 1.0)
        rois = np.concatenate(rois)
        out["ic95_roi_mise_fixe"] = [round(float(np.percentile(rois, 2.5)), 4),
                                     round(float(np.percentile(rois, 97.5)), 4)]
        out.update({"kelly_bankroll_finale": round(bankroll, 4), "kelly_roi": _roi(kelly_s, kelly_r),
                    "kelly_drawdown_max": round(max_dd, 4),
                    "roi_par_mois": {m: _roi(v[0], v[1]) for m, v in sorted(months.items())},
                    "paris_par_mois": {m: int(v[2]) for m, v in sorted(months.items())},
                    "roi_par_cote": {n: {"paris": int(v[2]), "roi": _roi(v[0], v[1])} for n, v in buckets.items()}})
    return out


# ── Rapport et envoi sur R2 privé ────────────────────────────────────────
def run(db_path: str, bench_path: Optional[str] = None) -> Dict[str, Any]:
    races, data_stats = load_races(db_path)
    _log("BENTER_DONNEES", data_stats)
    audit = build_features(races)
    _log("BENTER_AUDIT_FUITE", audit)
    names = [n for n in FEATURES if not (audit["fuite_suspectee"] and n in CAREER_FEATURES)]
    wf = fundamental_walk_forward(races, names)
    ev = evaluate(races, wf, "market")
    comb, recal = ev.pop("_comb"), ev.pop("_recal")
    for fold in ev["plis"]:
        _log("BENTER_PLI", fold)
    if "combinaison" in ev:
        _log("BENTER_RESULTAT", {"marche": "cloture", **ev["combinaison"]})
    ev_ref = evaluate(races, wf, "market_ref")
    ev_ref.pop("_comb"), ev_ref.pop("_recal")
    if "combinaison" in ev_ref:
        _log("BENTER_RESULTAT_REFERENCE", {"marche": "cote_reference", **ev_ref["combinaison"]})
    kelly = simulate_kelly(races, comb)
    _log("BENTER_KELLY", kelly)
    temoin = simulate_kelly(races, recal)
    _log("BENTER_KELLY_TEMOIN", {k: temoin.get(k) for k in ("paris", "courses_jouees", "roi_mise_fixe")})
    horizons = {}
    for horizon in TX_HORIZONS:
        tx = evaluate_tx(races, wf["oos"], horizon)
        horizons[f"T{horizon}"] = tx
        kelly_tx = tx.get("kelly") or {}
        _log("BENTER_TX", {**{k: v for k, v in tx.items() if not k.startswith("kelly")},
                           "kelly": {k: kelly_tx.get(k) for k in ("paris", "courses_jouees", "roi_mise_fixe",
                                                                  "ic95_roi_mise_fixe", "kelly_bankroll_finale",
                                                                  "kelly_drawdown_max")},
                           "temoin": tx.get("kelly_temoin_marche_seul")})
    bench_report = bench_fige = shadow = None
    if bench_path and os.path.exists(bench_path):
        bench = load_bench(bench_path, tuple(dict.fromkeys(BENCH_HORIZONS + ombre.HORIZONS_SECONDAIRES)))
        bench_report = evaluate_bench(races, wf["oos"], bench)
        for entry in bench_report.values():
            _log("BENTER_BANC", entry)
        # Robustesse « état connu au matin » : sans les variables qui peuvent
        # changer dans la journée (driver, œillères).
        wf_fige = fundamental_walk_forward(races, [n for n in names if n not in INTRADAY_FEATURES])
        bench_fige = evaluate_bench(races, wf_fige["oos"], bench, horizons=("T_MATIN",))
        for entry in bench_fige.values():
            _log("BENTER_BANC_VARIABLES_FIGEES", entry)
        # Point 4 de la règle d'ombre : puissance des deux recettes, avec le
        # modèle « état du matin » (celui qui tournera réellement).
        shadow = evaluate_shadow_power(races, wf_fige["oos"], bench)
        for horizon in ombre.HORIZONS_SECONDAIRES:
            _log("BENTER_OMBRE_PUISSANCE", {"horizon": horizon, **(shadow.get(horizon) or {})})
        _log("BENTER_OMBRE_DECISION", {k: shadow.get(k) for k in (
            "definition_detectable", "recette_principale_selon_regle", "secondaires_A", "secondaires_B",
            "rythme_production_matin")})
    return {
        "genere_le_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "methode": {"apprentissage_depuis": TRAIN_START, "premier_mois_test": FIRST_TEST_MONTH,
                    "ridge": RIDGE, "variables": names, "groupes": ["TROT", "GALOP"],
                    "marche": "cote_direct finale (clôture = rapport payé), normalisée",
                    "kelly": {"fraction": KELLY_FRACTION, "esperance_min": EV_MIN,
                              "exposition_max_par_course": MAX_RACE_EXPOSURE,
                              "avertissement": "décision aux cotes finales : borne haute"}},
        "donnees": data_stats, "audit_fuite": audit,
        "coefficients_dernier_pli": wf["coefficients"], **ev,
        "combinaison_marche_reference": ev_ref.get("combinaison"),
        "plis_marche_reference": ev_ref["plis"],
        "kelly": kelly, "kelly_temoin_marche_seul": temoin,
        "horizons_de_pari": horizons,
        "apport_aux_moteurs_du_banc": bench_report,
        "apport_aux_moteurs_du_banc_variables_figees": bench_fige,
        "ombre_puissance_avant_gel": shadow,
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
    parser.add_argument("--banc", help="turf_bench.db : mesure l'apport du fondamental aux moteurs publiés")
    args = parser.parse_args(argv)
    if not os.path.exists(args.db):
        _log("BENTER_ERREUR", {"erreur": "miroir absent", "chemin": args.db})
        return 2
    report = run(args.db, args.banc)
    if args.sortie:
        with open(args.sortie, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
    if not args.sans_envoi:
        upload(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
