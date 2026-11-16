"""Compteur quotidien de la recette A en production sur l'édition du matin.

Décision écrite de Steph du 13/11/2026 : recette A (0,90 marché + 0,10
fondamental) en production sur l'édition du matin à compter du 30/11/2026.
Contrat des marqueurs avec le moteur (dev NVE) : docs/RECETTE_A_PRODUCTION.md.

Il lit une COPIE en lecture seule de la base (``r2_store fetch``, jamais
renvoyée sur R2) et ne publie que des comptes, par jour de course :

  - éditions du matin, dont celles à marché réel (les seules diffusables) ;
  - éditions en recette A, éditions en repli (par motif, avec la liste des
    courses en repli : identifiants seulement), éditions sans marqueur ;
  - versions portées (fondamental et NVE).

Il n'utilise que les marqueurs ``recette``, ``motif_repli``, ``nve_version``
et ``fondamental_model_version``, et l'éligibilité du lecteur scellé (marché
réel). Il n'utilise jamais le témoin NVE ni aucune probabilité, et n'en publie
aucune : aucune lecture intermédiaire de la règle d'arrêt n'est possible.

ALERTE (le job échoue, GitHub prévient) :
  - MATINEE_EN_REPLI : des éditions du matin à marché réel, aucune en
    recette A (calcul de nuit manqué) : ligne datée au dev Bases le jour même ;
  - PART_RECETTE_A_BASSE : moins de 90 % des éditions du matin à marché réel
    en recette A ;
  - MARQUEUR_ABSENT : une édition du matin sans ``recette`` (repli « non
    porté ») ;
  - RECETTE_INCONNUE : une valeur de ``recette`` hors du contrat ;
  - VERSION_INATTENDUE : une version du fondamental ou de NVE différente de
    celles de la fenêtre de la règle d'arrêt (gel du 30/11 au 29/12).

Modes : ``matin`` compte le jour UTC courant, après le verrou du matin
(horloge Cloudflare : 07h50 UTC) ; ``jour`` compte la veille complète
(01h17 UTC). ``--jour AAAA-MM-JJ`` choisit un jour. ``--essai`` compte sans
alerte, y compris avant le 30/11 (contrôle sur PR).

Usage : python -m turf_lab.recette_compteur --banc copie_banc.db --mode jour
"""

import argparse
import io
import json
import sys
from collections import Counter
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from turf_lab import ombre_lecture as lecture

# Contrat du 13/11 avec le moteur (docs/RECETTE_A_PRODUCTION.md).
DEBUT_PRODUCTION = "2026-11-30"              # premier jour de course en recette A (RECETTE_A_DEBUT)
RECETTE_A = "A_lineaire_0.90_0.10"           # metadata.recette en recette A
RECETTE_REPLI = "REPLI_NVE"                  # metadata.recette en repli
NVE_RECETTE_A = "nve-2026-11-30"             # metadata.nve_version en recette A
NVE_REPLI = "nve-2026-10-07"                 # metadata.nve_version en repli
MODELE_ATTENDU = "fond-matin-e570d663793c"   # fondamental figé pendant la fenêtre de la règle d'arrêt
SEUIL_PART = 0.90                            # part minimale des éditions à marché réel en recette A
MARQUEURS = ("recette", "motif_repli", "nve_version", "fondamental_model_version")


def _log(tag: str, payload: Dict[str, Any]) -> None:
    print(tag + " " + json.dumps(payload, ensure_ascii=False, sort_keys=True))


def compter(bench_path: str, jour: str) -> Dict[str, Any]:
    """Comptes des éditions du matin du jour de course ``jour`` (AAAA-MM-JJ)."""
    with redirect_stdout(io.StringIO()):
        editions, _ = lecture.load(bench_path, jour)
    matin = [ed for ed in (editions.get("T_MATIN") or {}).values() if ed["day"] == jour]
    out: Dict[str, Any] = {"jour": jour, "editions_matin": len(matin), "marche_reel": 0, "recette_a": 0,
                           "repli": 0, "marqueur_absent": 0}
    motifs: Counter = Counter()
    versions_fond: Counter = Counter()
    versions_nve: Counter = Counter()
    en_repli: List[Dict[str, str]] = []
    for ed in sorted(matin, key=lambda e: e["race_id"]):
        meta = {k: ed["meta"].get(k) for k in MARQUEURS}
        reel = lecture.is_eligible(ed)
        out["marche_reel"] += int(reel)
        if meta["recette"] is None:
            out["marqueur_absent"] += 1
            continue
        versions_fond[str(meta["fondamental_model_version"])] += 1
        versions_nve[str(meta["nve_version"])] += 1
        if meta["recette"] not in (RECETTE_A, RECETTE_REPLI):
            out["recette_inconnue"] = out.get("recette_inconnue", 0) + 1
            continue
        if meta["recette"] == RECETTE_A and reel:
            out["recette_a"] += 1
        elif meta["recette"] == RECETTE_A:
            out["recette_a_hors_marche_reel"] = out.get("recette_a_hors_marche_reel", 0) + 1
        else:
            out["repli"] += 1
            motif = str(meta["motif_repli"] or "SANS_MOTIF")
            motifs[motif] += 1
            en_repli.append({"race_id": ed["race_id"], "motif": motif, "marche_reel": reel})
    out["part_recette_a"] = round(out["recette_a"] / out["marche_reel"], 4) if out["marche_reel"] else None
    out["motifs_repli"] = dict(sorted(motifs.items()))
    out["courses_en_repli"] = en_repli
    out["versions_fondamental"] = dict(sorted(versions_fond.items()))
    out["versions_nve"] = dict(sorted(versions_nve.items()))
    return out


def alertes(c: Dict[str, Any]) -> List[str]:
    a: List[str] = []
    if c["marche_reel"] and c["recette_a"] == 0:
        a.append(f"MATINEE_EN_REPLI : {c['marche_reel']} éditions du matin à marché réel, aucune en recette A")
    elif c["part_recette_a"] is not None and c["part_recette_a"] < SEUIL_PART:
        a.append(f"PART_RECETTE_A_BASSE : {c['part_recette_a']:.2%} des éditions à marché réel en recette A")
    if c["marqueur_absent"]:
        a.append(f"MARQUEUR_ABSENT : {c['marqueur_absent']} éditions du matin sans marqueur de recette")
    if c.get("recette_inconnue"):
        a.append(f"RECETTE_INCONNUE : {c['recette_inconnue']} éditions du matin hors du contrat")
    inattendues = sorted(set(c["versions_fondamental"]) - {MODELE_ATTENDU, "None"})
    if inattendues:
        a.append("VERSION_INATTENDUE : fondamental " + ", ".join(inattendues))
    nve = sorted(set(c["versions_nve"]) - {NVE_RECETTE_A, NVE_REPLI})
    if nve:
        a.append("VERSION_INATTENDUE : NVE " + ", ".join(nve))
    return a


def jour_du_mode(mode: str, maintenant: Optional[datetime] = None) -> str:
    now = maintenant or datetime.now(timezone.utc)
    d = now.date() if mode == "matin" else now.date() - timedelta(days=1)
    return d.isoformat()


def main(argv: Optional[List[str]] = None, maintenant: Optional[datetime] = None) -> int:
    parser = argparse.ArgumentParser(description="Compteur de la recette A en production (édition du matin)")
    parser.add_argument("--banc", default="turf_bench.db")
    parser.add_argument("--mode", choices=["matin", "jour"], default="jour")
    parser.add_argument("--jour", default=None, help="Jour de course AAAA-MM-JJ (sinon : selon le mode)")
    parser.add_argument("--essai", action="store_true", help="Comptes sans alerte, même avant la production")
    args = parser.parse_args(argv)
    jour = args.jour or jour_du_mode(args.mode, maintenant)
    date.fromisoformat(jour)
    entete = {"mode": args.mode, "debut_production": DEBUT_PRODUCTION}
    if jour < DEBUT_PRODUCTION and not args.essai:
        _log("RECETTE_COMPTEUR", {**entete, "jour": jour, "statut": "AVANT_PRODUCTION"})
        return 0
    c = {**entete, **compter(args.banc, jour)}
    a = [] if args.essai else alertes(c)
    c["alertes"] = a
    c["statut"] = "ESSAI" if args.essai else ("ALERTE" if a else "OK")
    _log("RECETTE_COMPTEUR", c)
    for msg in a:
        print(f"::error::{jour} : {msg}")
    return 1 if a else 0


if __name__ == "__main__":
    sys.exit(main())
