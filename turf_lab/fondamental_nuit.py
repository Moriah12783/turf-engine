"""Calcul de nuit du fondamental « état du matin » pour l'ombre NVE.

Règle arrêtée avec le dev NVE le 28/09/2026 (ombre après le 06/10, rien
avant). Chaque nuit, entre le pull et le push R2 de ``turf_bench.db``
(workflow fondamental_nuit.yml, 05h05 UTC, secours 05h50) :

  1. miroir historique Radar (R2 privé, lecture seule), arrêté à la veille ;
  2. programme PMU du jour, réunions du périmètre abonnés (celles que
     daily_sync ingère), avec le même ``race_id`` que turf_bench.db ;
  3. modèle « état du matin » (sans les 5 variables qui changent dans la
     journée), coefficients réestimés sur tout l'historique jusqu'à la veille,
     hyperparamètres et variables figés ;
  4. table ``fundamental_probs`` : p par partant (somme 1 par course),
     ``model_version`` (empreinte du code du modèle : tout changement de code
     remet le compteur de l'ombre à zéro), ``computed_at``, ``train_until``.

Garde-fous : aucune écriture après 06h20 UTC ni pour une course déjà
verrouillée ; audit de fuite négatif obligatoire ; historique en retard de
plus de 3 jours => rien n'est écrit (jour compté hors couverture). Journal :
agrégats seulement (dépôt public).

Mode ``parite`` (lecture seule) : pour une journée passée, compare le
programme PMU converti au format Radar avec les lignes du miroir, champ par
champ, variable par variable et en probabilités — le modèle est entraîné sur
le Radar et servi sur le PMU, les deux doivent coïncider.

Usage :
    python -m turf_lab.fondamental_nuit nuit   --historique turf_history.db --banc turf_bench.db
    python -m turf_lab.fondamental_nuit essai  --historique turf_history.db --banc copie.db
    python -m turf_lab.fondamental_nuit parite --historique turf_history.db [--jours 2]
"""

import argparse
import hashlib
import inspect
import json
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from turf_lab import benter_lab as lab

MORNING_FEATURES = tuple(n for n in lab.FEATURES if n not in lab.INTRADAY_FEATURES)
HEURE_LIMITE_UTC = (6, 20)              # aucune écriture après 06h20 UTC (verrou du matin : 06h30)
RETARD_MAX_JOURS = 3                    # historique plus ancien : rien n'est écrit
MIN_TRAIN_RACES = 200
PAUSE_S = 0.2                           # entre deux requêtes PMU
NON_PARTANT = {"NON_PARTANT", "NP", "FORFAIT"}

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS fundamental_probs (
    race_id TEXT NOT NULL,
    num INTEGER NOT NULL,
    p REAL NOT NULL,
    model_version TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    train_until TEXT NOT NULL,
    PRIMARY KEY (race_id, num, model_version)
)"""

# Champs du programme lus par le modèle du matin (comparés en mode parité),
# puis ceux qui changent dans la journée (comparés pour information).
CHAMPS_MODELE = ("nom", "nom_pere", "nom_mere", "age", "sexe", "place_corde", "musique", "nombre_courses",
                 "nombre_victoires", "nombre_places", "gains_carriere", "gains_annee_en_cours",
                 "handicap_poids", "entraineur")
CHAMPS_JOURNEE = ("driver", "driver_change", "oeilleres")


def _log(tag: str, payload: Dict[str, Any]) -> None:
    print(tag + " " + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


# ── Programme PMU -> format Radar ────────────────────────────────────────
def bench_race_id(reunion: Dict[str, Any], course: Dict[str, Any], date_str_api: str) -> str:
    """Même construction que DailySyncManager.sync_date (test de concordance)."""
    r_num = reunion.get("numOfficiel", 1)
    hippo = reunion.get("hippodrome", {}).get("libelleCourt", "HIPPO")
    return f"R{r_num}C{course.get('numOrdre', 1)}_{date_str_api}_{hippo}"


def pmu_runner(p: Dict[str, Any]) -> Dict[str, Any]:
    """Partant du flux PMU ``/participants`` -> colonnes du miroir Radar."""
    gains = p.get("gainsParticipant") or {}
    return {
        "num_pmu": p.get("numPmu"), "nom": p.get("nom"), "nom_pere": p.get("nomPere"),
        "nom_mere": p.get("nomMere"), "age": p.get("age"), "sexe": p.get("sexe"),
        "place_corde": p.get("placeCorde"), "oeilleres": p.get("oeilleres"),
        "entraineur": p.get("entraineur"), "driver": p.get("driver"), "driver_change": p.get("driverChange"),
        "musique": p.get("musique"), "nombre_courses": p.get("nombreCourses"),
        "nombre_victoires": p.get("nombreVictoires"), "nombre_places": p.get("nombrePlaces"),
        "gains_carriere": gains.get("gainsCarriere"), "gains_annee_en_cours": gains.get("gainsAnneeEnCours"),
        "handicap_poids": p.get("handicapPoids"), "statut": p.get("statut"), "incident": p.get("incident"),
        "ordre_arrivee": None,
    }


def is_partant(p: Dict[str, Any]) -> bool:
    """Même règle que l'apprentissage (statut PARTANT, pas d'incident NON_PARTANT),
    plus les marqueurs de non-partant du flux du matin."""
    statut = str(p.get("statut") or "").upper()
    return not (statut in NON_PARTANT or bool(p.get("nonPartant")) or str(p.get("incident") or "") == "NON_PARTANT")


def fetch_programme(fetcher, day: str, countries: Optional[set] = None) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Courses du jour (réunions du périmètre abonnés) et leurs partants PMU bruts."""
    if countries is None:
        from turf_lab.daily_sync import DailySyncManager
        countries = DailySyncManager.SUBSCRIBER_COUNTRIES
    date_str_api = date.fromisoformat(day).strftime("%d%m%Y")
    stats = {"reunions": 0, "reunions_hors_perimetre": 0, "courses": 0, "participants_indisponibles": 0}
    programme = fetcher.fetch_programme(date_str_api)
    if not isinstance(programme, dict) or "programme" not in programme:
        raise RuntimeError("PROGRAMME_PMU_INDISPONIBLE")
    out = []
    for reunion in (programme.get("programme") or {}).get("reunions") or []:
        if str((reunion.get("pays") or {}).get("code", "FRA")).upper() not in countries:
            stats["reunions_hors_perimetre"] += 1
            continue
        stats["reunions"] += 1
        for course in reunion.get("courses") or []:
            stats["courses"] += 1
            time.sleep(PAUSE_S)
            parts = fetcher.fetch_participants(date_str_api, reunion.get("numOfficiel", 1), course.get("numOrdre", 1))
            if not isinstance(parts, dict) or not isinstance(parts.get("participants"), list):
                stats["participants_indisponibles"] += 1
                continue
            out.append({"race_id": bench_race_id(reunion, course, date_str_api),
                        "key": (day, int(reunion.get("numOfficiel", 1)), int(course.get("numOrdre", 1))),
                        "specialite": course.get("specialite"), "discipline": course.get("discipline"),
                        "participants": parts["participants"]})
    return out, stats


class TodayRace(lab.Race):
    """Course du jour : porte en plus son race_id de turf_bench.db."""
    __slots__ = ("race_id",)


def races_from_programme(courses: Sequence[Dict[str, Any]], day: str) -> Tuple[List[TodayRace], Dict[str, int]]:
    """Objets course du labo pour le jour, partants au moment du calcul seulement."""
    races, stats = [], {"non_partants_exclus": 0, "courses_groupe_inconnu": 0, "courses_un_partant": 0}
    for c in courses:
        group = lab._group_of(c.get("specialite"), c.get("discipline"))
        if not group:
            stats["courses_groupe_inconnu"] += 1
            continue
        race = TodayRace(c["key"], day, group)
        for p in sorted(c["participants"], key=lambda x: int(x.get("numPmu") or 0)):
            if is_partant(p):
                race.runners.append(pmu_runner(p))
            else:
                stats["non_partants_exclus"] += 1
        if len(race.runners) < 2:
            stats["courses_un_partant"] += 1
            continue
        race.race_id = c.get("race_id")
        races.append(race)
    return races, stats


# ── Modèle « état du matin » ─────────────────────────────────────────────
def train_models(history: Sequence[lab.Race], train_until: str) -> Dict[str, Dict[str, Any]]:
    """Par groupe (TROT/GALOP) : logit conditionnel sur tout l'historique
    [TRAIN_START ; train_until], variables standardisées."""
    cols = [lab.FEATURES.index(n) for n in MORNING_FEATURES]
    models = {}
    for group in ("TROT", "GALOP"):
        train = [r for r in history if r.group == group and lab.TRAIN_START <= r.day <= train_until
                 and r.winner is not None and r.features is not None]
        if len(train) < MIN_TRAIN_RACES:
            continue
        mu, sd = lab._standardize(np.vstack([r.features for r in train]), cols)
        beta = lab.fit_clogit(lab.Packed([(r.features[:, cols] - mu) / sd for r in train], [r.winner for r in train]))
        models[group] = {"mu": mu, "sd": sd, "beta": beta, "courses": len(train)}
    return models


def score(race: lab.Race, models: Dict[str, Dict[str, Any]]) -> Optional[np.ndarray]:
    m = models.get(race.group)
    if m is None or race.features is None:
        return None
    cols = [lab.FEATURES.index(n) for n in MORNING_FEATURES]
    s = ((race.features[:, cols] - m["mu"]) / m["sd"]) @ m["beta"]
    e = np.exp(s - s.max())
    return e / e.sum()


def model_version() -> str:
    """Empreinte du SEUL code qui fait le modèle (chargement, variables,
    apprentissage, conversion du programme) et de ses réglages : un
    changement ailleurs dans le labo ne change pas la version."""
    from turf_lab.history_export import COURSES_COURUES_SQL
    parts = [inspect.getsource(obj) for obj in (
        lab.load_races, lab.parse_musique, lab.build_features, lab.Packed, lab.fit_clogit, lab._standardize,
        lab._f, lab._name, lab._group_of, lab._implied, TodayRace, pmu_runner, is_partant, races_from_programme,
        train_models, score)]
    parts += [lab._LOAD_SQL, COURSES_COURUES_SQL, json.dumps({
        "variables": MORNING_FEATURES, "ridge": lab.RIDGE, "debut": lab.TRAIN_START, "prior_k": lab.PRIOR_K,
        "prior_win": lab.PRIOR_WIN, "groupes": lab.GROUPS, "disciplines": lab.DISCIPLINE_GROUP,
        "musique": lab._MUSIQUE_TOKEN.pattern, "min_train": MIN_TRAIN_RACES}, sort_keys=True)]
    return "fond-matin-" + hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:12]


# ── Écriture dans turf_bench.db ──────────────────────────────────────────
def locked_race_ids(conn: sqlite3.Connection) -> set:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "predictions" not in tables:
        return set()
    return {r[0] for r in conn.execute("SELECT DISTINCT race_id FROM predictions WHERE horizon = 'T_MATIN'")}


def write_probs(bench_path: str, rows: Sequence[Tuple[str, Sequence[int], np.ndarray]], version: str,
                computed_at: str, train_until: str) -> Dict[str, int]:
    """Une ligne par partant ; jamais pour une course déjà verrouillée au matin."""
    conn = sqlite3.connect(bench_path)
    try:
        conn.execute(TABLE_SQL)
        locked = locked_race_ids(conn)
        stats = {"courses_ecrites": 0, "partants_ecrits": 0, "courses_deja_verrouillees": 0}
        for race_id, nums, probs in rows:
            if race_id in locked:
                stats["courses_deja_verrouillees"] += 1
                continue
            conn.executemany("INSERT OR REPLACE INTO fundamental_probs VALUES (?, ?, ?, ?, ?, ?)",
                             [(race_id, int(n), float(p), version, computed_at, train_until)
                              for n, p in zip(nums, probs)])
            stats["courses_ecrites"] += 1
            stats["partants_ecrits"] += len(nums)
        conn.commit()
    finally:
        conn.close()
    return stats


def _too_late(now_utc: datetime) -> bool:
    return (now_utc.hour, now_utc.minute) >= HEURE_LIMITE_UTC


# ── Nuit ─────────────────────────────────────────────────────────────────
def run_night(history_path: str, bench_path: str, fetcher, now_utc: Optional[datetime] = None,
              enforce_hour: bool = True, day: Optional[str] = None) -> Dict[str, Any]:
    """Calcule et écrit les probabilités du jour. ``enforce_hour`` et ``day``
    ne servent qu'au mode essai (copie locale de la base, jamais renvoyée sur
    R2 ; par exemple le programme du lendemain, pas encore verrouillé)."""
    clock = now_utc or datetime.now(timezone.utc)
    started = time.monotonic()
    day = day or clock.date().isoformat()
    version = model_version()
    report: Dict[str, Any] = {"jour": day, "model_version": version, "ecrit": False}
    if enforce_hour and _too_late(clock):
        report["refus"] = "HEURE_LIMITE_DEPASSEE"
        _log("NUIT_FONDAMENTAL_REFUS", report)
        return report

    history, data_stats = lab.load_races(history_path)
    history = [r for r in history if r.day < day]
    train_until = max((r.day for r in history), default=None)
    report["train_until"] = train_until
    report["courses_historique"] = len(history)
    retard = (date.fromisoformat(day) - date.fromisoformat(train_until)).days - 1 if train_until else None
    report["retard_historique_jours"] = retard
    if retard is None or retard > RETARD_MAX_JOURS:
        report["refus"] = "HISTORIQUE_EN_RETARD"
        _log("NUIT_FONDAMENTAL_REFUS", report)
        return report

    courses, prog_stats = fetch_programme(fetcher, day)
    today, race_stats = races_from_programme(courses, day)
    report.update(prog_stats)
    report.update(race_stats)
    audit = lab.build_features(list(history) + list(today))
    if audit["fuite_suspectee"]:
        report["refus"] = "AUDIT_DE_FUITE"
        _log("NUIT_FONDAMENTAL_REFUS", {**report, "audit": audit})
        return report
    models = train_models(history, train_until)
    report["courses_apprentissage"] = {g: m["courses"] for g, m in models.items()}
    rows = []
    for race in today:
        probs = score(race, models)
        if probs is None:
            report["courses_sans_modele"] = report.get("courses_sans_modele", 0) + 1
            continue
        rows.append((race.race_id, [int(r["num_pmu"]) for r in race.runners], probs))

    computed = datetime.now(timezone.utc) if now_utc is None else clock
    if enforce_hour and _too_late(computed):
        report["refus"] = "HEURE_LIMITE_DEPASSEE"
        _log("NUIT_FONDAMENTAL_REFUS", report)
        return report
    report.update(write_probs(bench_path, rows, version, computed.strftime("%Y-%m-%dT%H:%M:%SZ"), train_until))
    report["ecrit"] = report["courses_ecrites"] > 0
    report["duree_s"] = round(time.monotonic() - started, 1)
    _log("NUIT_FONDAMENTAL", report)
    return report


# ── Parité programme PMU <-> miroir Radar (lecture seule) ────────────────
def _norm_field(field: str, value: Any) -> Any:
    if field in ("nom", "nom_pere", "nom_mere", "entraineur", "driver"):
        return lab._name(value) if value else ""
    if field in ("sexe", "oeilleres", "musique"):
        return str(value or "").strip()
    if field == "driver_change":
        return bool(value)
    return lab._f(value)


def _pct(num: int, den: int) -> Optional[float]:
    return round(100.0 * num / den, 2) if den else None


def parity_day(history: Sequence[lab.Race], day: str, fetcher) -> Dict[str, Any]:
    """Une journée passée : partants du miroir vs programme PMU converti."""
    mirror_day = {r.key: r for r in history if r.day == day}
    past = [r for r in history if r.day < day]
    courses, prog_stats = fetch_programme(fetcher, day)
    pmu_races, race_stats = races_from_programme(courses, day)
    pmu_by_key = {r.key: r for r in pmu_races}
    out: Dict[str, Any] = {"jour": day, "courses_miroir": len(mirror_day), "courses_pmu": len(pmu_races),
                           **prog_stats, **race_stats}
    field_ok = {f: [0, 0] for f in CHAMPS_MODELE + CHAMPS_JOURNEE}
    pairs: List[Tuple[lab.Race, lab.Race]] = []
    cles_absentes_pmu = partants_differents = 0
    for key, mr in mirror_day.items():
        pr = pmu_by_key.get(key)
        if pr is None:
            cles_absentes_pmu += 1
            continue
        m_nums = [int(x["num_pmu"]) for x in mr.runners]
        p_by_num = {int(x["num_pmu"]): x for x in pr.runners}
        if set(m_nums) != set(p_by_num):
            partants_differents += 1
            continue
        pr.runners = [p_by_num[n] for n in m_nums]            # même ordre que le miroir
        for mx, px in zip(mr.runners, pr.runners):
            for f in field_ok:
                field_ok[f][1] += 1
                field_ok[f][0] += int(_norm_field(f, mx.get(f)) == _norm_field(f, px.get(f)))
        pairs.append((mr, pr))
    out.update({"courses_absentes_du_pmu": cles_absentes_pmu, "courses_pmu_absentes_du_miroir":
                sum(1 for k in pmu_by_key if k not in mirror_day), "courses_partants_differents": partants_differents,
                "courses_comparees": len(pairs), "partants_compares": sum(len(m.runners) for m, _ in pairs),
                "champs_identiques_pct": {f: _pct(*v) for f, v in field_ok.items()}})
    if not pairs:
        return out
    # Variables et probabilités : mêmes statistiques figées à la veille des deux côtés.
    lab.build_features(list(past) + [m for m, _ in pairs] + [p for _, p in pairs])
    cols = [lab.FEATURES.index(n) for n in MORNING_FEATURES]
    same = np.zeros(len(cols))
    n_runners = 0
    for m, p in pairs:
        same += (np.abs(m.features[:, cols] - p.features[:, cols]) < 1e-9).sum(axis=0)
        n_runners += len(m.runners)
    out["variables_identiques_pct"] = {n: round(100.0 * s / n_runners, 2) for n, s in zip(MORNING_FEATURES, same)}
    models = train_models(past, max((r.day for r in past), default=day))
    dp, top_diff, compared = [], 0, 0
    for m, p in pairs:
        pm, pp = score(m, models), score(p, models)
        if pm is None or pp is None:
            continue
        compared += 1
        dp.extend(np.abs(pm - pp).tolist())
        top_diff += int(np.argmax(pm) != np.argmax(pp))
    if dp:
        out.update({"courses_notees": compared, "delta_p_moyen": round(float(np.mean(dp)), 5),
                    "delta_p_max": round(float(np.max(dp)), 5), "favori_different_pct": _pct(top_diff, compared)})
    return out


def run_parity(history_path: str, fetcher, days: int = 2) -> List[Dict[str, Any]]:
    history, _ = lab.load_races(history_path)
    last_days = sorted({r.day for r in history})[-days:]
    reports = []
    for day in last_days:
        rep = parity_day(history, day, fetcher)
        _log("NUIT_PARITE", rep)
        reports.append(rep)
        history, _ = lab.load_races(history_path)           # objets neufs pour la journée suivante
    return reports


def main(argv: Optional[List[str]] = None, fetcher=None) -> int:
    parser = argparse.ArgumentParser(description="Fondamental de nuit pour l'ombre NVE")
    parser.add_argument("action", choices=["nuit", "essai", "parite"])
    parser.add_argument("--historique", default="turf_history.db")
    parser.add_argument("--banc", default="turf_bench.db")
    parser.add_argument("--jours", type=int, default=2)
    parser.add_argument("--jour", help="essai seulement : AAAA-MM-JJ ou « demain »")
    args = parser.parse_args(argv)
    if fetcher is None:
        from turf_lab.daily_sync import PMUDataFetcher
        fetcher = PMUDataFetcher()
    try:
        if args.action == "parite":
            run_parity(args.historique, fetcher, args.jours)
        elif args.action == "essai":
            day = args.jour
            if day == "demain":
                day = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
            run_night(args.historique, args.banc, fetcher, enforce_hour=False, day=day)
        else:
            run_night(args.historique, args.banc, fetcher)             # nuit : toujours le jour même
    except RuntimeError as exc:
        _log("NUIT_FONDAMENTAL_ERREUR", {"erreur": str(exc)})
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
