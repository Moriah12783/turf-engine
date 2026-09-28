"""Garde-fou de confidentialité de l'ombre : jusqu'à la lecture, seul le
lecteur scellé (et l'outil de répétition, qui ne lit qu'une copie antérieure
à l'ombre) lit l'archive ``ombre_fondamental`` ; le moteur l'écrit. Aucun
autre code du dépôt ne la cite, et le site public ne la contient jamais."""
import os

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLES = ("ombre_fondamental", "META_OMBRE")
CODE_AUTORISE = {os.path.join("turf_lab", f) for f in ("ombre_lecture.py", "repetition.py", "engine.py")}
IGNORES = {".git", "tests", "__pycache__", "node_modules", "venv", ".venv"}
TEXTE_PUBLIC = (".json", ".html", ".js", ".css", ".txt", ".csv", ".xml")


def _fichiers(base, extensions):
    for dossier, sous, noms in os.walk(base):
        sous[:] = [d for d in sous if d not in IGNORES]
        for nom in noms:
            if nom.endswith(extensions):
                yield os.path.join(dossier, nom)


def _cite(chemin):
    with open(chemin, encoding="utf-8", errors="ignore") as f:
        texte = f.read()
    return any(cle in texte for cle in CLES)


def test_seuls_le_lecteur_la_repetition_et_le_moteur_citent_l_archive():
    fautifs = sorted(os.path.relpath(p, RACINE) for p in _fichiers(RACINE, (".py",)) if _cite(p))
    assert [p for p in fautifs if p not in CODE_AUTORISE] == []


def test_le_site_public_ne_contient_jamais_l_archive():
    site = os.path.join(RACINE, "site")
    fautifs = [os.path.relpath(p, RACINE) for p in _fichiers(site, TEXTE_PUBLIC) if _cite(p)]
    assert fautifs == []
