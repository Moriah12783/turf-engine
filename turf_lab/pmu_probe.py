"""Sonde ponctuelle de l'API publique PMU (turfinfo) : où sont publiés les
RAPPORTS PROBABLES des paris combinés (couplé, trio, 2sur4, multi…) avant le
départ ? Préalable à leur capture quotidienne (docs/LABO_BENTER.md : « la
valeur est peut-être dans les paris combinés »).

Pour une course du jour pas encore partie, la sonde essaie une liste de
points d'accès candidats et n'écrit dans le journal que la FORME des
réponses (statut HTTP, type, clés, tailles) — jamais le contenu. Données PMU
publiques ; aucune requête au Radar ; aucun effet sur la production.
TLS strict (contexte de ``secure_http``), une requête à la fois, pause
entre deux appels.

Usage : python -m turf_lab.pmu_probe [--date JJMMAAAA] [--reunion N --course M]
"""

import argparse
import gzip
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from turf_lab.secure_http import DEFAULT_HEADERS, build_ssl_context

HOST = "https://online.turfinfo.api.pmu.fr/rest/client"
BET_TYPES = ("SIMPLE_GAGNANT", "COUPLE_GAGNANT", "COUPLE_ORDRE", "TRIO", "DEUX_SUR_QUATRE", "MULTI")
PAUSE_S = 0.3


def _log(tag: str, payload: Dict[str, Any]) -> None:
    print(tag + " " + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def describe(value: Any, depth: int = 0) -> Any:
    """Forme d'un JSON (types, clés, tailles), sans les valeurs. Au-delà de
    deux niveaux, seulement les NOMS des champs (le schéma, pas les données)."""
    if isinstance(value, dict):
        if depth >= 2:
            return {"champs": sorted(value)[:25]}
        return {k: describe(v, depth + 1) for k, v in list(value.items())[:25]}
    if isinstance(value, list):
        return {"list": len(value), "elem": describe(value[0], depth + 1) if value else None}
    return type(value).__name__


def fetch(url: str, timeout: int = 15) -> Tuple[int, Optional[Any], int]:
    req = urllib.request.Request(url, headers={**DEFAULT_HEADERS, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=build_ssl_context()) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            try:
                return resp.status, json.loads(raw.decode("utf-8")), len(raw)
            except ValueError:
                return resp.status, None, len(raw)
    except urllib.error.HTTPError as exc:
        return exc.code, None, 0
    except Exception as exc:                       # réseau, TLS : journalisé, jamais contourné
        _log("PMU_PROBE_ERREUR", {"url": url, "erreur": type(exc).__name__})
        return -1, None, 0


def candidates() -> List[Tuple[str, str]]:
    """(client, suffixe) à essayer après /programme/{date}/R{r}/C{c}."""
    out = [(c, s) for c in ("7", "1") for s in ("", "/combinaisons", "/combinaisons?specialisation=INTERNET",
                                              "/combinaisons?specialisation=OFFLINE", "/rapports-probables",
                                              "/rapports")]
    for bet in BET_TYPES:
        for prefix in ("", "E_"):
            out += [("7", f"/rapports/{prefix}{bet}"), ("1", f"/rapports/{prefix}{bet}"),
                    ("1", f"/rapports/{prefix}{bet}?specialisation=INTERNET"),
                    ("7", f"/rapports-probables/{prefix}{bet}")]
    out += [("61", ""), ("61", "/combinaisons?specialisation=INTERNET")]
    return out


def offered_bets(course: Dict[str, Any]) -> List[str]:
    return sorted({str(p.get("typePari")) for p in course.get("paris") or [] if isinstance(p, dict)
                   and p.get("typePari")})


def pick_race(programme: Dict[str, Any], now_ms: int) -> Optional[Tuple[int, int, int]]:
    """Course pas encore partie (au moins 20 min avant le départ) qui propose
    le PLUS de paris (Quinté+, multi… de préférence) ; à égalité, la plus tôt."""
    best, best_rank = None, None
    for reunion in (programme.get("programme") or {}).get("reunions") or []:
        for course in reunion.get("courses") or []:
            start = course.get("heureDepart")
            if isinstance(start, (int, float)) and start - now_ms > 20 * 60 * 1000:
                key = (start, int(reunion.get("numOfficiel") or reunion.get("numReunion") or 0),
                       int(course.get("numOrdre") or course.get("numCourse") or 0))
                rank = (-len(offered_bets(course)), start)
                if best_rank is None or rank < best_rank:
                    best, best_rank = key, rank
    return best


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Sonde des rapports probables PMU")
    parser.add_argument("--date")
    parser.add_argument("--reunion", type=int)
    parser.add_argument("--course", type=int)
    args = parser.parse_args(argv)
    paris_now = datetime.now(timezone.utc) + timedelta(hours=2)
    day = args.date or paris_now.strftime("%d%m%Y")

    status, programme, _ = fetch(f"{HOST}/7/programme/{day}")
    _log("PMU_PROBE_PROGRAMME", {"date": day, "statut": status})
    if not programme:
        return 1
    if args.reunion and args.course:
        target = (None, args.reunion, args.course)
    else:
        target = pick_race(programme, int(datetime.now(timezone.utc).timestamp() * 1000))
    if not target:
        _log("PMU_PROBE_AUCUNE_COURSE", {"date": day})
        return 1
    _, r_num, c_num = target
    _log("PMU_PROBE_COURSE", {"reunion": r_num, "course": c_num,
                              "depart_utc": datetime.fromtimestamp(target[0] / 1000, timezone.utc).strftime("%H:%M")
                              if target[0] else None})

    status, course, _ = fetch(f"{HOST}/7/programme/{day}/R{r_num}/C{c_num}")
    offered = offered_bets(course) if isinstance(course, dict) else []
    _log("PMU_PROBE_PARIS_PROPOSES", {"types": offered})
    found = []
    tries = [("7", "/combinaisons?specialisation=INTERNET")] + [("7", f"/rapports/{t}") for t in offered]
    for client, suffix in tries:
        url = f"{HOST}/{client}/programme/{day}/R{r_num}/C{c_num}{suffix}"
        status, body, size = fetch(url)
        entry = {"client": client, "chemin": suffix or "(course)", "statut": status, "octets": size}
        if status == 200 and body is not None:
            entry["forme"] = describe(body)
            found.append(entry)
            _log("PMU_PROBE_OK", entry)
        else:
            _log("PMU_PROBE_KO", entry)
        time.sleep(PAUSE_S)
    _log("PMU_PROBE_FIN", {"reponses_ok": len(found), "essais": len(tries)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
