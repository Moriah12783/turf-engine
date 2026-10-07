"""Garde de confidentialité de l'ombre du fondamental, indépendante de la
chaîne de production (décision de Steph du 07/10/2026).

Deux fois par jour (workflow garde_ombre.yml), elle cherche toute trace de
l'ombre dans ce qui est public :
  - le site publié (prono.elite-turf.fr) : page d'accueil, archives et
    résultats du jour et de la veille ;
  - les fichiers publics du dépôt (site/, benchmark_report.json,
    benchmark_dashboard.html) ;
  - les lignes ajoutées à ces fichiers par les commits des dernières heures :
    le dépôt est public, une trace effacée ensuite resterait dans l'historique.

Les marqueurs cherchés sont donnés par le workflow (--marqueur) ; un test
vérifie qu'ils sont ceux de la règle gelée. La garde ne lit jamais la base,
n'utilise aucun secret, n'écrit rien et n'imprime jamais d'extrait : seulement
des comptes (source, marqueur, nombre).

Statuts : OK ; FUITE (le job échoue, ce qui déclenche l'alerte GitHub) ;
INCOMPLET (une source n'a pas pu être lue : avertissement, sans échec).

Usage : python -m turf_lab.garde_ombre --site https://prono.elite-turf.fr \\
            --marqueur M1 --marqueur M2 [--heures 14] [--racine .]
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

CHEMINS_PUBLICS = ("site", "benchmark_report.json", "benchmark_dashboard.html")
EXTENSIONS_TEXTE = (".html", ".json", ".js", ".css", ".txt", ".csv", ".xml", ".svg")


def compter(texte: str, marqueurs: Sequence[str]) -> Dict[str, int]:
    return {m: texte.count(m) for m in marqueurs if m in texte}


def _traces(source: str, comptes: Dict[str, int]) -> List[Dict[str, object]]:
    return [{"source": source, "marqueur": m, "n": n} for m, n in sorted(comptes.items())]


# ── Fichiers publics du dépôt ────────────────────────────────────────────
def _fichiers_publics(racine: str):
    for chemin in CHEMINS_PUBLICS:
        complet = os.path.join(racine, chemin)
        if os.path.isfile(complet):
            yield complet
        elif os.path.isdir(complet):
            for dossier, _, noms in os.walk(complet):
                for nom in sorted(noms):
                    if nom.endswith(EXTENSIONS_TEXTE):
                        yield os.path.join(dossier, nom)


def scanner_depot(racine: str, marqueurs: Sequence[str]) -> Tuple[int, List[Dict[str, object]]]:
    fichiers, traces = 0, []
    for chemin in _fichiers_publics(racine):
        fichiers += 1
        with open(chemin, encoding="utf-8", errors="ignore") as f:
            traces += _traces(os.path.relpath(chemin, racine), compter(f.read(), marqueurs))
    return fichiers, traces


# ── Historique récent du dépôt ───────────────────────────────────────────
def scanner_historique(racine: str, marqueurs: Sequence[str], heures: float,
                       maintenant: Optional[datetime] = None) -> Dict[str, object]:
    """Lignes ajoutées aux fichiers publics par les commits des ``heures``
    dernières heures. ``couvert`` est faux si l'historique local (clone
    partiel) commence après le début de la fenêtre."""
    maintenant = maintenant or datetime.now(timezone.utc)
    debut = maintenant - timedelta(hours=heures)
    depuis = debut.strftime("%Y-%m-%dT%H:%M:%SZ")
    base = ["git", "-C", racine]
    sortie = subprocess.run(base + ["log", f"--since={depuis}", "--format=%x00%H", "-p", "--unified=0",
                                    "--no-color", "--", *CHEMINS_PUBLICS],
                            capture_output=True, text=True, errors="ignore", check=True).stdout
    commits, traces, courant, comptes = 0, [], None, {}

    def clore():
        if courant is not None:
            traces.extend(_traces(f"commit {courant[:12]}", comptes))

    for ligne in sortie.splitlines():
        if ligne.startswith("\x00"):
            clore()
            courant, comptes = ligne[1:].strip(), {}
            commits += 1
        elif ligne.startswith("+") and not ligne.startswith("+++"):
            for m, n in compter(ligne, marqueurs).items():
                comptes[m] = comptes.get(m, 0) + n
    clore()
    partiel = subprocess.run(base + ["rev-parse", "--is-shallow-repository"], capture_output=True, text=True,
                             check=True).stdout.strip() == "true"
    couvert = True
    if partiel:                          # clone partiel : son plus vieux commit doit précéder la fenêtre
        premier = subprocess.run(base + ["log", "--reverse", "--format=%cI"], capture_output=True, text=True,
                                 check=True).stdout.splitlines()
        couvert = bool(premier) and datetime.fromisoformat(premier[0]) <= debut
    return {"commits": commits, "depuis": depuis, "couvert": couvert, "traces": traces}


# ── Site publié ──────────────────────────────────────────────────────────
def pages_du_site(jour: str) -> List[Tuple[str, bool]]:
    """(chemin, obligatoire) : une page obligatoire absente rend le contrôle
    incomplet ; un export daté absent (404) est seulement noté."""
    veille = (datetime.fromisoformat(jour) - timedelta(days=1)).date().isoformat()
    return [("/", True), (f"/archive/{jour}.json", False), (f"/archive/{veille}.json", False),
            ("/resultats/index.json", False), ("/resultats/corrections.json", False),
            (f"/resultats/{veille}.json", False)]


def telecharger(url: str, essais: int = 3, attente: float = 5.0) -> Tuple[Optional[int], str]:
    """(statut HTTP, texte). Statut None : site injoignable après les essais."""
    for essai in range(1, essais + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "turf-engine-garde-ombre",
                                                       "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 404, ""
            if e.code < 500:
                return e.code, ""
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        if essai < essais:
            time.sleep(attente)
    return None, ""


def scanner_site(base: str, jour: str, marqueurs: Sequence[str],
                 fetch: Callable[[str], Tuple[Optional[int], str]] = telecharger) -> Dict[str, object]:
    lues, absentes, injoignables, traces = 0, [], [], []
    for chemin, obligatoire in pages_du_site(jour):
        statut, texte = fetch(base.rstrip("/") + chemin)
        if statut == 200:
            lues += 1
            traces += _traces(f"site {chemin}", compter(texte, marqueurs))
        elif statut == 404 and not obligatoire:
            absentes.append(chemin)
        else:
            injoignables.append(f"{chemin} ({statut or 'réseau'})")
    return {"pages_lues": lues, "absentes": absentes, "injoignables": injoignables, "traces": traces}


# ── Synthèse ─────────────────────────────────────────────────────────────
def controler(racine: str, marqueurs: Sequence[str], site: Optional[str], heures: float,
              maintenant: Optional[datetime] = None,
              fetch: Callable[[str], Tuple[Optional[int], str]] = telecharger) -> Dict[str, object]:
    maintenant = maintenant or datetime.now(timezone.utc)
    fichiers, traces_depot = scanner_depot(racine, marqueurs)
    historique = scanner_historique(racine, marqueurs, heures, maintenant)
    rapport: Dict[str, object] = {
        "controle_utc": maintenant.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "marqueurs": len(marqueurs),
        "depot": {"fichiers": fichiers, "traces": traces_depot},
        "historique": historique,
    }
    incomplet = []
    if site:
        rapport["site"] = scanner_site(site, maintenant.date().isoformat(), marqueurs, fetch)
        if rapport["site"]["injoignables"]:
            incomplet.append("site injoignable : " + ", ".join(rapport["site"]["injoignables"]))
    if not historique["couvert"]:
        incomplet.append(f"historique local plus court que {heures:g} h")
    traces = traces_depot + historique["traces"] + (rapport.get("site") or {}).get("traces", [])
    rapport["traces"] = len(traces)
    rapport["statut"] = "FUITE" if traces else ("INCOMPLET" if incomplet else "OK")
    if incomplet:
        rapport["incomplet"] = incomplet
    return rapport


def main(argv: Optional[List[str]] = None,
         fetch: Callable[[str], Tuple[Optional[int], str]] = telecharger) -> int:
    parser = argparse.ArgumentParser(description="Garde de confidentialité de l'ombre (lecture seule)")
    parser.add_argument("--racine", default=".")
    parser.add_argument("--site", help="URL du site publié, par exemple https://prono.elite-turf.fr")
    parser.add_argument("--heures", type=float, default=14.0, help="fenêtre de l'historique des commits")
    parser.add_argument("--marqueur", action="append", required=True)
    args = parser.parse_args(argv)
    rapport = controler(args.racine, args.marqueur, args.site, args.heures, fetch=fetch)
    print("GARDE_OMBRE " + json.dumps(rapport, ensure_ascii=False, sort_keys=True))
    if rapport["statut"] == "FUITE":
        print(f"::error::GARDE_OMBRE : {rapport['traces']} trace(s) de l'ombre dans ce qui est public. "
              "Sources dans la ligne GARDE_OMBRE ci-dessus (aucun extrait n'est imprimé).")
        return 1
    if rapport["statut"] == "INCOMPLET":
        print("::warning::GARDE_OMBRE incomplet : " + " ; ".join(rapport["incomplet"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
