"""Compteur quotidien de l'ombre du fondamental (décision de Steph du 08/10/2026).

Il lit une COPIE en lecture seule de la base (``r2_store fetch``, jamais
renvoyée sur R2) et ne publie que des comptes :

  - par jour depuis DEBUT_OMBRE : éditions du matin, éditions éligibles
    (marché réel), éditions portant une ombre complète de la clé gelée,
    éditions éligibles sans ombre ;
  - le compteur officiel du lecteur scellé : éditions éligibles avec arrivée
    définitive, éditions retenues, exclusions par motif, d'où la couverture.

Garde-fou : le lecteur scellé est appelé avec la date du jour 1. Ni la
lecture des 1 000 éditions (au plus tôt le lendemain de la 1 000e) ni celle
des 35 jours (le 36e jour) ne peuvent alors se déclencher. Sa sortie est
captée et seuls des champs choisis sont publiés : aucune probabilité, aucun
écart, aucune sélection, ni de l'ombre ni de l'édition publiée.

ALERTE (le job échoue, GitHub envoie l'alerte) :
  - une édition éligible d'hier ou d'aujourd'hui sans ombre complète ;
  - une ombre dont la clé (model_version, nve_version, recette) n'est pas
    celle de la règle gelée ;
  - une couverture officielle sous le seuil de la règle (90 %).
La remise à zéro n'est permise que dans les 14 premiers jours : ce compteur
sert à voir un défaut de couverture assez tôt pour qu'il reste réparable.

Usage : python -m turf_lab.ombre_compteur --banc copie_banc.db
"""

import argparse
import io
import json
import sys
from collections import defaultdict
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from turf_lab import ombre, ombre_lecture as lecture

CHAMPS_OFFICIELS = ("debut", "horloge_depart", "fin_des_35_jours", "lecture_au_plus_tard",
                    "editions_eligibles", "editions_avec_ombre", "exclusions",
                    "remises_a_zero", "motif", "decision", "refus")


def cle_gelee() -> Tuple[str, str, str]:
    """(model_version, nve_version, recette) du code en place sur main."""
    from turf_lab import engine, fondamental_nuit
    return fondamental_nuit.model_version(), engine.NVE_VERSION, ombre.RECETTE


def par_jour(bench_path: str, debut: str, cle: Sequence[str]) -> Dict[str, Dict[str, int]]:
    editions, _ = lecture.load(bench_path, debut)
    jours: Dict[str, Dict[str, int]] = defaultdict(lambda: {
        "editions_matin": 0, "eligibles": 0, "avec_ombre": 0, "sans_ombre": 0,
        "cle_differente": 0, "ombre_hors_eligibles": 0})
    for ed in (editions.get("T_MATIN") or {}).values():
        j = jours[ed["day"]]
        j["editions_matin"] += 1
        sh = lecture.shadow_of(ed)
        if not lecture.is_eligible(ed):
            j["ombre_hors_eligibles"] += int(sh is not None)
            continue
        j["eligibles"] += 1
        if sh is None:
            j["sans_ombre"] += 1
        elif tuple(sh["cle"]) != tuple(cle):
            j["cle_differente"] += 1
        else:
            j["avec_ombre"] += 1
    return dict(sorted(jours.items()))


def compteur_officiel(bench_path: str, debut: str) -> Dict[str, Any]:
    """Le lecteur scellé, appelé avec la date du jour 1 : jamais de lecture.
    Sa propre sortie est captée ; seuls des champs choisis sont rendus."""
    with redirect_stdout(io.StringIO()):
        out = lecture.read(bench_path, debut=debut, today=debut)
    if "lecture" in out:                       # impossible avec today = jour 1 ; rien n'est rendu
        return {"erreur": "LECTURE_REFUSEE_PAR_LE_COMPTEUR"}
    return {k: out[k] for k in CHAMPS_OFFICIELS if k in out}


def compter(bench_path: str, jour: Optional[str] = None, debut: Optional[str] = None,
            cle: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    debut = debut or lecture.DEBUT_OMBRE
    jour = jour or datetime.now(timezone.utc).date().isoformat()
    if not debut:
        return {"statut": "ALERTE", "alertes": ["REGLE_NON_DATEE"], "jour": jour}
    cle = tuple(cle or cle_gelee())
    jours = par_jour(bench_path, debut, cle)
    officiel = compteur_officiel(bench_path, debut)
    rapport: Dict[str, Any] = {"jour": jour, "debut": debut, "cle_gelee": list(cle), "jours": jours,
                               "officiel": officiel}
    eligibles, avec = officiel.get("editions_eligibles") or 0, officiel.get("editions_avec_ombre") or 0
    rapport["couverture"] = round(avec / eligibles, 4) if eligibles else None

    alertes: List[str] = []
    recents = {jour, (date.fromisoformat(jour) - timedelta(days=1)).isoformat()}
    for d, j in jours.items():
        if d in recents and j["sans_ombre"]:
            alertes.append(f"{d} : {j['sans_ombre']} édition(s) éligible(s) sans ombre complète")
        if j["cle_differente"]:
            alertes.append(f"{d} : {j['cle_differente']} ombre(s) d'une autre clé que la règle gelée")
    if rapport["couverture"] is not None and rapport["couverture"] < ombre.COUVERTURE_MIN:
        alertes.append(f"couverture {rapport['couverture']:.1%} sous le seuil de {ombre.COUVERTURE_MIN:.0%}")
    for cle_alerte in ("motif", "refus", "erreur"):
        if cle_alerte in officiel:
            alertes.append(f"lecteur scellé : {officiel[cle_alerte]}")
    rapport["alertes"] = alertes
    rapport["statut"] = "ALERTE" if alertes else "OK"
    return rapport


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Compteur quotidien de l'ombre (comptes seulement)")
    parser.add_argument("--banc", default="copie_banc.db")
    parser.add_argument("--jour", help="AAAA-MM-JJ (par défaut : aujourd'hui, UTC)")
    args = parser.parse_args(argv)
    rapport = compter(args.banc, jour=args.jour)
    print("OMBRE_COMPTEUR_JOUR " + json.dumps(rapport, ensure_ascii=False, sort_keys=True))
    if rapport["statut"] == "ALERTE":
        print("::error::Compteur de l'ombre : " + " ; ".join(rapport["alertes"]))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
