"""Répétition générale de l'ombre du fondamental, du 01 au 06/10/2026 (GO de
Steph du 28/09), sans aucun effet sur la production.

Chaque matin de la fenêtre, aux horaires réels de l'ombre (05h05 UTC, secours
05h50 ; workflow repetition_ombre.yml) :

  1. copie en LECTURE SEULE de la base de production (turf_bench.db sur R2) ;
  2. calcul de nuit du fondamental du jour dans cette copie, avec le code de
     la nuit de l'ombre (audit de fuite, retard de l'historique) ;
  3. envoi de la copie sur une clé PRIVÉE distincte (``lab/repetition/``),
     jamais sur la base de production.

Journal : horaires réels (retard de déclenchement, calcul fini avant 06h20,
envoi avant 06h28) et agrégats du calcul.

Les devs NVE et daily_sync récupèrent la copie (``fetch``), enchaînent chez
eux le verrou du matin et le moteur avec l'ombre, puis contrôlent la chaîne
(``diagnostic``) : probabilités fondamentales complètes et d'une seule
version, éditions du matin éligibles portant une ombre complète, clés de
l'archive. Aucune comparaison entre l'ombre et l'édition publiée. Après la
fenêtre, tout est refusé : l'outil ne peut pas servir de lecture
intermédiaire une fois l'ombre ouverte.

Usage :
    python -m turf_lab.repetition nuit --historique turf_history.db --copie repetition.db --prevu 05:05
    python -m turf_lab.repetition nuit --controle ...        # avant la fenêtre : programme du lendemain
    python -m turf_lab.repetition fetch --copie copie_repetition.db
    python -m turf_lab.repetition diagnostic --copie copie_repetition.db --jour 2026-10-01
"""

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from turf_lab import fondamental_nuit, ombre, ombre_lecture, r2_store

FENETRE = ("2026-10-01", "2026-10-06")          # répétition : du jeudi 01/10 au mardi 06/10
REPETITION_KEY = "lab/repetition/turf_bench.db"  # clé privée distincte de la base de production
LIMITE_ENVOI_UTC = (6, 28)                       # comme la nuit de l'ombre : aucun envoi après 06h28


class RepetitionRefus(RuntimeError):
    pass


def _log(tag: str, payload: Dict[str, Any]) -> None:
    print(tag + " " + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def dans_la_fenetre(jour: str) -> bool:
    return FENETRE[0] <= jour <= FENETRE[1]


# ── Copie privée sur R2 ──────────────────────────────────────────────────
def upload(client, bucket: str, path: str, meta: Dict[str, Any]) -> str:
    if REPETITION_KEY == r2_store.DB_KEY or not REPETITION_KEY.startswith("lab/repetition/"):
        raise RepetitionRefus("CLE_DE_PRODUCTION")
    digest = r2_store.sha256_file(path)
    with open(path, "rb") as f:
        client.put_object(Bucket=bucket, Key=REPETITION_KEY, Body=f, ContentType="application/vnd.sqlite3",
                          Metadata={"sha256": digest, **{k: str(v) for k, v in meta.items()}})
    return digest


def fetch(client, bucket: str, dest: str) -> Dict[str, Any]:
    """Copie de répétition -> ``dest``, empreinte vérifiée (lecture seule)."""
    tmp = dest + ".r2download"
    obj = r2_store._download(client, bucket, REPETITION_KEY, tmp)
    digest = r2_store.sha256_file(tmp)
    meta = obj.get("Metadata") or {}
    if meta.get("sha256") != digest:
        os.remove(tmp)
        raise RepetitionRefus(f"EMPREINTE_INVALIDE : attendu {meta.get('sha256')}, obtenu {digest}")
    os.replace(tmp, dest)
    return {"sha256": digest, "jour": meta.get("jour"), "model_version": meta.get("model_version")}


# ── Nuit ─────────────────────────────────────────────────────────────────
def nuit(history_path: str, copie_path: str, fetcher, client, bucket: str, prevu: str = "05:05",
         controle: bool = False, horloge: Callable[[], datetime] = _now) -> Dict[str, Any]:
    """Calcul de nuit du jour dans la copie, puis envoi sur la clé de
    répétition. ``controle`` (avant la fenêtre) : programme du lendemain,
    pour vérifier le circuit ; les horaires n'y sont pas jugés."""
    debut = horloge()
    today = debut.date()
    jour = (today + timedelta(days=1)).isoformat() if controle else today.isoformat()
    rep: Dict[str, Any] = {"jour": jour, "controle": controle, "debut_utc": debut.strftime("%H:%M:%S")}
    if today.isoformat() > FENETRE[1] or not (controle or dans_la_fenetre(jour)):
        rep["refus"] = "HORS_FENETRE"
        _log("REPETITION_REFUS", rep)
        return rep
    if not controle:
        h, m = (int(x) for x in prevu.split(":"))
        rep["prevu_utc"] = prevu
        rep["retard_declenchement_min"] = round(
            (debut - debut.replace(hour=h, minute=m, second=0, microsecond=0)).total_seconds() / 60.0, 1)
    calc = fondamental_nuit.run_night(history_path, copie_path, fetcher, enforce_hour=False, day=jour)
    fin = horloge()
    rep.update({"model_version": calc.get("model_version"), "ecrit": bool(calc.get("ecrit")),
                "fin_calcul_utc": fin.strftime("%H:%M:%S")})
    if calc.get("refus"):
        rep["refus_calcul"] = calc["refus"]                 # en production : pas d'ombre ce jour-là
    if not controle:
        rep["calcul_avant_06h20"] = (fin.hour, fin.minute) < fondamental_nuit.HEURE_LIMITE_UTC
    if rep["ecrit"]:
        rep["sha256"] = upload(client, bucket, copie_path, {"jour": jour, "model_version": rep["model_version"]})
        envoi = horloge()
        rep["envoi_utc"] = envoi.strftime("%H:%M:%S")
        if not controle:
            rep["envoi_avant_06h28"] = (envoi.hour, envoi.minute) < LIMITE_ENVOI_UTC
    ok = rep["ecrit"] and (controle or (rep["calcul_avant_06h20"] and rep["envoi_avant_06h28"]))
    rep["verdict"] = "OK" if ok else "A_EXPLIQUER"
    _log("REPETITION_NUIT", rep)
    return rep


# ── Diagnostic de la chaîne (chez les devs, sur leur copie) ──────────────
def _fundamental(conn: sqlite3.Connection, jour: str) -> Dict[str, Dict[str, Dict[int, float]]]:
    """race_id -> model_version -> {num: p} pour le jour."""
    out: Dict[str, Dict[str, Dict[int, float]]] = defaultdict(lambda: defaultdict(dict))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "fundamental_probs" not in tables:
        return out
    for race_id, num, p, version in conn.execute("SELECT race_id, num, p, model_version FROM fundamental_probs"):
        parsed = ombre_lecture._race_day_meeting(race_id)
        if parsed and parsed[0] == jour:
            out[race_id][version][int(num)] = float(p)
    return out


def diagnostic(copie_path: str, jour: str) -> Dict[str, Any]:
    """Contrôle de plomberie d'une journée de répétition : aucun chiffre de
    performance, aucune comparaison entre l'ombre et l'édition publiée."""
    if not dans_la_fenetre(jour) or _now().date().isoformat() > FENETRE[1]:
        out = {"jour": jour, "refus": "HORS_FENETRE"}
        _log("REPETITION_REFUS", out)
        return out
    conn = sqlite3.connect(f"file:{copie_path}?mode=ro", uri=True)
    try:
        fond = _fundamental(conn, jour)
    finally:
        conn.close()
    versions = sorted({v for per in fond.values() for v in per})
    ecart = max((abs(sum(ps.values()) - 1.0) for per in fond.values() for ps in per.values()), default=None)
    editions, _ = ombre_lecture.load(copie_path, jour)
    matin = [ed for ed in (editions.get("T_MATIN") or {}).values() if ed["day"] == jour]
    eligibles = [ed for ed in matin if ombre_lecture.is_eligible(ed)]
    compte: Dict[str, int] = defaultdict(int)
    cles = set()
    for ed in eligibles:
        sh = ombre_lecture.shadow_of(ed)
        if sh is not None:
            compte["avec_ombre_complete"] += 1
            cles.add(sh["cle"])
            continue
        if ed["race_id"] not in fond:
            compte["sans_fondamental"] += 1                   # course non calculée la nuit
        elif any(set(ed["probs"]) - set(ps) for ps in fond[ed["race_id"]].values()):
            compte["partant_sans_p"] += 1                     # jamais d'ombre partielle
        elif not isinstance(ed["meta"].get(ombre_lecture.META_OMBRE), dict):
            compte["archive_absente"] += 1                    # à expliquer côté daily_sync / moteur
        else:
            compte["archive_incomplete"] += 1
    cles_list = [list(c) for c in sorted(cles, key=str)]
    ok = (bool(eligibles) and compte["avec_ombre_complete"] == len(eligibles) and len(cles) == 1
          and all(c[0] in versions and c[2] == ombre.RECETTE for c in cles))
    out = {"jour": jour, "courses_fondamental": len(fond), "versions_fondamental": versions,
           "ecart_somme_max": None if ecart is None else round(ecart, 6),
           "editions_matin": len(matin), "editions_eligibles": len(eligibles), **dict(compte),
           "cles_archive": cles_list, "verdict": "OK" if ok else "A_EXPLIQUER"}
    _log("REPETITION_DIAGNOSTIC", out)
    return out


def _client():
    cfg = r2_store.r2_config()
    if cfg is None:
        raise RepetitionRefus("R2_NON_CONFIGURE")
    return r2_store.make_client(cfg), cfg["bucket"]


def main(argv: Optional[List[str]] = None, fetcher=None) -> int:
    parser = argparse.ArgumentParser(description="Répétition générale de l'ombre du fondamental (01-06/10)")
    parser.add_argument("action", choices=["nuit", "fetch", "diagnostic"])
    parser.add_argument("--historique", default="turf_history.db")
    parser.add_argument("--copie", default="repetition.db")
    parser.add_argument("--prevu", default="05:05", help="nuit : horaire prévu (05:05 ou 05:50)")
    parser.add_argument("--controle", action="store_true", help="nuit : programme du lendemain, avant la fenêtre")
    parser.add_argument("--jour", help="diagnostic : AAAA-MM-JJ")
    args = parser.parse_args(argv)
    try:
        if args.action == "diagnostic":
            out = diagnostic(args.copie, args.jour or _now().date().isoformat())
            return 0 if out.get("verdict") == "OK" else 1
        client, bucket = _client()
        if args.action == "fetch":
            _log("REPETITION_FETCH_OK", {"copie": args.copie, **fetch(client, bucket, args.copie)})
            return 0
        if fetcher is None:
            from turf_lab.daily_sync import PMUDataFetcher
            fetcher = PMUDataFetcher()
        out = nuit(args.historique, args.copie, fetcher, client, bucket, prevu=args.prevu, controle=args.controle)
        return 0 if out.get("verdict") == "OK" else 1
    except (RepetitionRefus, r2_store.R2ConfigError, RuntimeError) as exc:
        _log("REPETITION_ERREUR", {"action": args.action, "erreur": str(exc)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
