"""Labo du déferrage (phase 1c, feu vert de Steph du 28/09/2026).

Le déferrage est une information publique du programme (D4 : déferré des
quatre pieds, DA : antérieurs, DP : postérieurs), réputée décisive au trot,
surtout « pour la première fois ». Le miroir Radar ne la contient pas ; le
flux PMU, si. Trois étapes :

  sonde       forme du champ ``deferre`` sur quelques journées (valeurs,
              part renseignée, trot / galop) : agrégats seulement ;
  rattrapage  historique incrémental, date par date, depuis le flux PMU
              public (une requête par course, pause entre deux requêtes),
              vers une base PRIVÉE sur R2 (``lab/deferre/deferre.db``) ;
  test        apport du déferrage au fondamental « état du matin »
              (walk-forward mensuel, séparément trot et galop), puis
              au-dessus du marché (référence et clôture), écarts appariés.

Aucune requête au Radar, aucun effet sur la production. Journal :
agrégats seulement (dépôt public) ; rapport complet sur R2 privé.
"""

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from turf_lab import benter_lab as lab
from turf_lab import ombre

DB_KEY = "lab/deferre/deferre.db"
REPORT_PREFIX = "lab/deferre/"
PAUSE_S = 0.3                           # entre deux requêtes PMU (rythme modéré)
RATTRAPAGE_GROUPES = ("TROT",)          # le déferrage concerne le trot (la sonde le vérifie au galop)
DEFERRE_FEATURES = ("d4", "da", "dp", "protege", "d4_premiere_fois", "deferre_premiere_fois")
MIN_COVERAGE = 0.95                     # part des partants d'une course avec déferrage connu
SCHEMA = """
CREATE TABLE IF NOT EXISTS deferre (date TEXT, reunion INTEGER, course INTEGER, num INTEGER, deferre TEXT,
                                    PRIMARY KEY (date, reunion, course, num));
CREATE TABLE IF NOT EXISTS journees (date TEXT PRIMARY KEY, courses INTEGER, partants INTEGER, fetched_at TEXT);
"""


def _log(tag: str, payload: Dict[str, Any]) -> None:
    print(tag + " " + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def code(raw: Any) -> str:
    """Valeur brute du flux -> code court (même lecture que daily_sync, plus « protégé »)."""
    s = str(raw or "").upper()
    if s == "DEFERRE_ANTERIEURS_POSTERIEURS":
        return "D4"
    if s == "DEFERRE_ANTERIEURS":
        return "DA"
    if s == "DEFERRE_POSTERIEURS":
        return "DP"
    if s.startswith("PROTEGE"):
        return "PROTEGE"
    return "FERRE"


# ── Flux PMU ─────────────────────────────────────────────────────────────
def fetch_day(fetcher, day: str, countries: Optional[set] = None,
              groups: Optional[Sequence[str]] = None) -> Tuple[List[Tuple], Dict[str, Any]]:
    """Déferrage des partants des réunions du périmètre, pour une date (courses
    des ``groups`` seulement si précisé : pas de requête pour les autres)."""
    if countries is None:
        from turf_lab.daily_sync import DailySyncManager
        countries = DailySyncManager.SUBSCRIBER_COUNTRIES
    api = date.fromisoformat(day).strftime("%d%m%Y")
    programme = fetcher.fetch_programme(api)
    stats: Dict[str, Any] = {"courses": 0, "indisponibles": 0, "valeurs": defaultdict(int)}
    rows: List[Tuple] = []
    if not isinstance(programme, dict) or "programme" not in programme:
        stats["programme_indisponible"] = True
        return rows, stats
    for reunion in (programme.get("programme") or {}).get("reunions") or []:
        if str((reunion.get("pays") or {}).get("code", "FRA")).upper() not in countries:
            continue
        r_num = int(reunion.get("numOfficiel", 1))
        for course in reunion.get("courses") or []:
            c_num = int(course.get("numOrdre", 1))
            group = lab._group_of(course.get("specialite"), course.get("discipline")) or "?"
            if groups is not None and group not in groups:
                continue
            time.sleep(PAUSE_S)
            parts = fetcher.fetch_participants(api, r_num, c_num)
            if not isinstance(parts, dict) or not isinstance(parts.get("participants"), list):
                stats["indisponibles"] += 1
                continue
            stats["courses"] += 1
            for p in parts["participants"]:
                raw = p.get("deferre")
                stats["valeurs"][f"{group}:{raw if raw is not None else 'ABSENT'}"] += 1
                rows.append((day, r_num, c_num, int(p.get("numPmu") or 0), str(raw or "")))
    stats["valeurs"] = dict(stats["valeurs"])
    return rows, stats


def run_probe(fetcher, days: Sequence[str]) -> List[Dict[str, Any]]:
    out = []
    for day in days:
        _, stats = fetch_day(fetcher, day)
        _log("DEFERRE_SONDE", {"jour": day, **stats})
        out.append(stats)
    return out


# ── Base privée ──────────────────────────────────────────────────────────
def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def backfill(db_path: str, fetcher, days: Sequence[str], budget_s: float, clock=time.monotonic) -> Dict[str, Any]:
    """Rattrapage date par date (les plus anciennes d'abord), dans le budget
    de temps ; une date n'est marquée faite qu'une fois entièrement écrite."""
    started = clock()
    conn = open_db(db_path)
    done = {r[0] for r in conn.execute("SELECT date FROM journees")}
    todo = [d for d in sorted(days) if d not in done]
    report = {"dates_a_faire": len(todo), "dates_faites": 0, "partants": 0, "interrompu": False}
    for day in todo:
        if clock() - started > budget_s:
            report["interrompu"] = True
            break
        rows, stats = fetch_day(fetcher, day, groups=RATTRAPAGE_GROUPES)
        if stats.get("programme_indisponible"):
            continue
        with conn:
            conn.executemany("INSERT OR REPLACE INTO deferre VALUES (?, ?, ?, ?, ?)", rows)
            conn.execute("INSERT OR REPLACE INTO journees VALUES (?, ?, ?, ?)",
                         (day, stats["courses"], len(rows), datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
        report["dates_faites"] += 1
        report["partants"] += len(rows)
    report["dates_restantes"] = len(todo) - report["dates_faites"]
    conn.close()
    return report


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def r2_download(client, bucket: str, path: str) -> bool:
    try:
        obj = client.get_object(Bucket=bucket, Key=DB_KEY)
    except Exception:                                    # absente : premier rattrapage
        return False
    with open(path, "wb") as f:
        f.write(obj["Body"].read())
    expected = (obj.get("Metadata") or {}).get("sha256")
    if expected and expected != _sha(path):
        raise RuntimeError("EMPREINTE_DEFERRE_INVALIDE")
    return True


def r2_upload(client, bucket: str, path: str) -> None:
    with open(path, "rb") as f:
        client.put_object(Bucket=bucket, Key=DB_KEY, Body=f, Metadata={"sha256": _sha(path)},
                          ContentType="application/vnd.sqlite3")


# ── Variables ────────────────────────────────────────────────────────────
def load_deferre(db_path: str) -> Dict[Tuple[str, int, int, int], str]:
    conn = sqlite3.connect(db_path)
    out = {(d, int(r), int(c), int(n)): code(v) for d, r, c, n, v in conn.execute("SELECT * FROM deferre")}
    conn.close()
    return out


def deferre_features(races: Sequence[lab.Race], data: Dict[Tuple[str, int, int, int], str]) -> Dict[str, Any]:
    """Colonnes de déferrage par course (dans l'ordre chronologique des courses).
    « Première fois » : déferré aujourd'hui, et pas lors de la dernière course
    connue du cheval (ou jamais vu). Couverture : part des partants connus."""
    last: Dict[Tuple[str, str, str], str] = {}
    cols: Dict[Tuple[str, int, int], np.ndarray] = {}
    coverage: Dict[Tuple[str, int, int], float] = {}
    by_day: Dict[str, List[lab.Race]] = defaultdict(list)
    for r in races:
        by_day[r.day].append(r)
    for day in sorted(by_day):
        seen_today = []
        for race in by_day[day]:
            rows, known = [], 0
            for x in race.runners:
                c = data.get((race.day, race.key[1], race.key[2], int(x["num_pmu"])))
                known += c is not None
                c = c or "FERRE"
                horse = (lab._name(x["nom"]), lab._name(x["nom_pere"]), lab._name(x["nom_mere"]))
                prev = last.get(horse)
                deferred = c in ("D4", "DA", "DP")
                rows.append([float(c == "D4"), float(c == "DA"), float(c == "DP"), float(c == "PROTEGE"),
                             float(c == "D4" and prev != "D4"),
                             float(deferred and prev not in ("D4", "DA", "DP"))])
                seen_today.append((horse, c))
            cols[race.key] = np.array(rows)
            coverage[race.key] = known / max(len(race.runners), 1)
        for horse, c in seen_today:                          # la veille au soir seulement
            last[horse] = c
    return {"colonnes": cols, "couverture": coverage}


# ── Test ─────────────────────────────────────────────────────────────────
def walk_forward(races: Sequence[lab.Race], extra: Optional[Dict[Tuple[str, int, int], np.ndarray]],
                 group: str, base_names: Sequence[str]) -> Dict[Tuple[str, int, int], np.ndarray]:
    """Fondamental « état du matin » (+ déferrage si ``extra``), un groupe,
    réappris chaque mois sur les mois précédents : probabilités hors échantillon."""
    cols = [lab.FEATURES.index(n) for n in base_names]
    pool = [r for r in races if r.group == group and (extra is None or r.key in extra)]

    def x_of(r):
        base = r.features[:, cols]
        return np.hstack([base, extra[r.key]]) if extra is not None else base
    months = sorted({lab._month(r.day) for r in pool if lab._month(r.day) >= lab.FIRST_TEST_MONTH})
    oos = {}
    for month in months:
        train = [r for r in pool if lab.TRAIN_START <= r.day and lab._month(r.day) < month]
        test = [r for r in pool if lab._month(r.day) == month]
        if len(train) < 200 or not test:
            continue
        xs = [x_of(r) for r in train]
        stacked = np.vstack(xs)
        mu, sd = stacked.mean(axis=0), stacked.std(axis=0)
        sd[sd < 1e-9] = 1.0
        beta = lab.fit_clogit(lab.Packed([(x - mu) / sd for x in xs], [r.winner for r in train]))
        for r in test:
            s = ((x_of(r) - mu) / sd) @ beta
            e = np.exp(s - s.max())
            oos[r.key] = e / e.sum()
    return oos


def _paired(races_by_key, a: Dict, b: Dict) -> Dict[str, Any]:
    keys = [k for k in a if k in b]
    if len(keys) < 30:
        return {"courses": len(keys)}
    d = [math.log(max(a[k][races_by_key[k].winner], lab.FLOOR)) - math.log(max(b[k][races_by_key[k].winner], lab.FLOOR))
         for k in keys]
    top = [int(np.argmax(a[k]) == races_by_key[k].winner) - int(np.argmax(b[k]) == races_by_key[k].winner) for k in keys]
    clusters = [(k[0], k[1]) for k in keys]
    return {"courses": len(keys), "delta_ll": round(float(np.mean(d)), 5),
            "ic95": [round(v, 5) for v in ombre.intervalle(d, clusters, 0.95)],
            "delta_top1": round(float(np.mean(top)), 4),
            "ic95_top1": [round(v, 4) for v in ombre.intervalle(top, clusters, 0.95)]}


def run_test(history_path: str, deferre_path: str) -> Dict[str, Any]:
    from turf_lab.fondamental_nuit import MORNING_FEATURES
    races, _ = lab.load_races(history_path)
    lab.build_features(races)
    feats = deferre_features(races, load_deferre(deferre_path))
    covered = {k for k, v in feats["couverture"].items() if v >= MIN_COVERAGE}
    extra = {k: v for k, v in feats["colonnes"].items() if k in covered}
    by_key = {r.key: r for r in races}
    report: Dict[str, Any] = {"courses": len(races), "courses_couvertes": len(covered)}
    for group in ("TROT", "GALOP"):
        subset = [r for r in races if r.group == group and r.key in covered]
        if not subset:
            report[group] = {"groupe": group, "courses_couvertes": 0}
            _log("DEFERRE_TEST", report[group])
            continue
        shares = {n: round(float(np.mean(np.concatenate([extra[r.key][:, j] for r in subset]))), 4)
                  for j, n in enumerate(DEFERRE_FEATURES)} if subset else {}
        base = walk_forward(races, {k: np.zeros((len(by_key[k].runners), 0)) for k in covered}, group,
                            MORNING_FEATURES)
        with_def = walk_forward(races, extra, group, MORNING_FEATURES)
        entry: Dict[str, Any] = {"groupe": group, "courses_couvertes": len(subset), "part_des_partants": shares,
                                 "fondamental_plus_deferre_vs_fondamental": _paired(by_key, with_def, base)}
        # Au-dessus du marché : combinaison apprise sur les mois précédents (labo).
        for attr, label in (("market_ref", "marche_reference"), ("market", "marche_cloture")):
            months = sorted({lab._month(by_key[k].day) for k in base})
            comb_base = lab.evaluate([by_key[k] for k in base], {"oos": base, "months": months}, attr)["_comb"]
            comb_def = lab.evaluate([by_key[k] for k in with_def], {"oos": with_def, "months": months}, attr)["_comb"]
            entry[f"combinaison_{label}_avec_vs_sans_deferre"] = _paired(by_key, comb_def, comb_base)
        _log("DEFERRE_TEST", entry)
        report[group] = entry
    return report


def main(argv: Optional[List[str]] = None, fetcher=None, client_factory=None) -> int:
    parser = argparse.ArgumentParser(description="Labo du déferrage (phase 1c)")
    parser.add_argument("action", choices=["sonde", "rattrapage", "test"])
    parser.add_argument("--historique", default="turf_history.db")
    parser.add_argument("--deferre", default="deferre.db")
    parser.add_argument("--jours", default="", help="sonde : dates AAAA-MM-JJ séparées par des virgules")
    parser.add_argument("--budget-min", type=float, default=50.0)
    args = parser.parse_args(argv)
    if args.action in ("sonde", "rattrapage") and fetcher is None:
        from turf_lab.daily_sync import PMUDataFetcher
        fetcher = PMUDataFetcher()
    if args.action == "sonde":
        run_probe(fetcher, [d for d in args.jours.split(",") if d])
        return 0
    from turf_lab.r2_store import make_client, r2_config
    cfg = r2_config()
    client = (client_factory or make_client)(cfg) if cfg else None
    if client is not None:
        _log("DEFERRE_BASE", {"presente_sur_r2": r2_download(client, cfg["bucket"], args.deferre)})
    if args.action == "rattrapage":
        conn = sqlite3.connect(args.historique)
        days = [r[0] for r in conn.execute("SELECT DISTINCT date_course FROM courses WHERE date_course >= ?",
                                           (lab.TRAIN_START,))]
        conn.close()
        report = backfill(args.deferre, fetcher, days, args.budget_min * 60)
        if client is not None and report["dates_faites"]:
            r2_upload(client, cfg["bucket"], args.deferre)
        _log("DEFERRE_RATTRAPAGE", report)
        return 0
    report = run_test(args.historique, args.deferre)
    report["genere_le_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if client is not None:
        key = f"{REPORT_PREFIX}test_{report['genere_le_utc'][:10]}_{os.environ.get('GITHUB_RUN_ID', 'local')}.json"
        client.put_object(Bucket=cfg["bucket"], Key=key, ContentType="application/json",
                          Body=json.dumps(report, ensure_ascii=False, indent=1, default=str).encode("utf-8"))
        _log("DEFERRE_RAPPORT_ENVOYE", {"cle": key})
    return 0


if __name__ == "__main__":
    sys.exit(main())
