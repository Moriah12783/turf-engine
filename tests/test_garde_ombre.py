"""Tests de la garde de confidentialité de l'ombre : aucune trace -> OK ; une
trace dans les fichiers publics, dans l'historique récent ou sur le site
publié -> FUITE, sans jamais imprimer d'extrait ; site injoignable ->
INCOMPLET sans échec ; marqueurs du workflow = ceux de la règle gelée."""
import io
import json
import os
import re
import subprocess
import sys
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab import garde_ombre as garde

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARQUEURS = ["cle_secrete_ombre", "recette_X"]
SECRET = "0.4321"                                    # contenu voisin d'un marqueur : jamais imprimé


def _git(depot, *args):
    subprocess.run(["git", "-C", depot, *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def _ecrire(depot, chemin, texte):
    complet = os.path.join(depot, chemin)
    os.makedirs(os.path.dirname(complet), exist_ok=True)
    with open(complet, "w", encoding="utf-8") as f:
        f.write(texte)


@pytest.fixture
def depot(tmp_path):
    d = str(tmp_path / "depot")
    os.makedirs(d)
    _git(d, "init", "-q")
    _ecrire(d, "site/index.html", "<html>pronostics</html>")
    _ecrire(d, "site/archive/2026-10-08.json", json.dumps([{"race_id": "R1C1", "sel_moteur": [3, 5, 1]}]))
    _ecrire(d, "benchmark_report.json", "{}")
    _ecrire(d, "docs/regle.md", "cle_secrete_ombre recette_X")        # documentation : hors périmètre public
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "depart")
    return d


def _site_propre(url):
    return (404, "") if "resultats/2026" in url else (200, '{"ok": true}')


def _main(depot, fetch=_site_propre, site="https://exemple.test"):
    argv = ["--racine", depot, "--heures", "14"] + (["--site", site] if site else [])
    for m in MARQUEURS:
        argv += ["--marqueur", m]
    with redirect_stdout(io.StringIO()) as out:
        code = garde.main(argv, fetch=fetch)
    texte = out.getvalue()
    rapport = json.loads(texte.split("GARDE_OMBRE ", 1)[1].splitlines()[0])
    return code, rapport, texte


def test_aucune_trace(depot):
    code, rapport, texte = _main(depot)
    assert code == 0 and rapport["statut"] == "OK" and rapport["traces"] == 0
    assert rapport["depot"]["fichiers"] == 3                          # docs/ n'est pas public au sens de la garde
    assert rapport["site"]["pages_lues"] == 5                          # export daté absent (404) : seulement noté
    assert len(rapport["site"]["absentes"]) == 1 and rapport["site"]["absentes"][0].startswith("/resultats/")


def test_trace_dans_un_fichier_public(depot):
    _ecrire(depot, "site/archive/2026-10-08.json", json.dumps([{"meta": {"cle_secrete_ombre": {"p": SECRET}}}]))
    code, rapport, texte = _main(depot)
    assert code == 1 and rapport["statut"] == "FUITE"
    assert {"source": os.path.join("site", "archive", "2026-10-08.json"), "marqueur": "cle_secrete_ombre",
            "n": 1} in rapport["depot"]["traces"]
    assert SECRET not in texte and "::error::" in texte


def test_trace_effacee_reste_dans_l_historique(depot):
    _ecrire(depot, "site/index.html", f"<html>recette_X {SECRET}</html>")
    _git(depot, "commit", "-q", "-am", "fuite")
    _ecrire(depot, "site/index.html", "<html>pronostics</html>")
    _git(depot, "commit", "-q", "-am", "nettoyage")
    code, rapport, texte = _main(depot)
    assert rapport["depot"]["traces"] == []                           # l'état actuel est propre
    assert code == 1 and rapport["statut"] == "FUITE"
    sources = [t["source"] for t in rapport["historique"]["traces"]]
    assert len(sources) == 1 and sources[0].startswith("commit ")
    assert rapport["historique"]["commits"] == 3 and SECRET not in texte


def test_historique_hors_fenetre_ignore(depot):
    plus_tard = datetime.now(timezone.utc) + timedelta(days=2)
    rapport = garde.controler(depot, MARQUEURS, None, 14, maintenant=plus_tard)
    assert rapport["historique"]["commits"] == 0 and rapport["historique"]["couvert"] is True


def test_trace_sur_le_site_publie(depot):
    def fuite(url):
        if url.endswith("/"):
            return 200, f"<script>var x = {{'cle_secrete_ombre': {SECRET}}}</script>"
        return _site_propre(url)
    code, rapport, texte = _main(depot, fetch=fuite)
    assert code == 1 and rapport["site"]["traces"] == [{"source": "site /", "marqueur": "cle_secrete_ombre", "n": 1}]
    assert SECRET not in texte


def test_site_injoignable_avertit_sans_echouer(depot):
    code, rapport, texte = _main(depot, fetch=lambda url: (None, ""))
    assert code == 0 and rapport["statut"] == "INCOMPLET" and "::warning::" in texte
    assert rapport["site"]["pages_lues"] == 0 and "/ (réseau)" in rapport["site"]["injoignables"]


def test_une_fuite_l_emporte_sur_un_site_injoignable(depot):
    _ecrire(depot, "benchmark_report.json", '{"x": "recette_X"}')
    code, rapport, _ = _main(depot, fetch=lambda url: (None, ""))
    assert code == 1 and rapport["statut"] == "FUITE"


def test_marqueurs_du_workflow_sont_ceux_de_la_regle_gelee():
    pytest.importorskip("numpy")
    from turf_lab import fondamental_nuit, ombre, ombre_lecture
    texte = open(os.path.join(RACINE, ".github", "workflows", "garde_ombre.yml"), encoding="utf-8").read()
    marqueurs = re.findall(r"--marqueur (\S+)", texte)
    assert marqueurs[0] == ombre_lecture.META_OMBRE
    assert marqueurs[1] == ombre.RECETTE
    assert fondamental_nuit.model_version().startswith(marqueurs[2])
    assert len(marqueurs) == 3 and "--site https://prono.elite-turf.fr" in texte


def test_le_depot_reel_est_propre_aujourd_hui():
    """Les fichiers publics du dépôt ne contiennent aucun marqueur (l'historique
    et le site publié sont contrôlés par le workflow)."""
    pytest.importorskip("numpy")
    from turf_lab import ombre, ombre_lecture
    fichiers, traces = garde.scanner_depot(RACINE, [ombre_lecture.META_OMBRE, ombre.RECETTE, "fond-matin-"])
    assert fichiers > 0 and traces == []


def test_clone_partiel_trop_court_signale(depot, tmp_path):
    for i in range(3):
        _ecrire(depot, "site/index.html", f"<html>{i}</html>")
        _git(depot, "commit", "-q", "-am", f"c{i}")
    clone = str(tmp_path / "clone")
    subprocess.run(["git", "clone", "-q", "--depth", "2", f"file://{depot}", clone], check=True, capture_output=True)
    plus_tard = datetime.now(timezone.utc) + timedelta(hours=1)
    rapport = garde.controler(clone, MARQUEURS, None, 14, maintenant=plus_tard)
    assert rapport["historique"]["couvert"] is False and rapport["statut"] == "INCOMPLET"
    rapport = garde.controler(clone, MARQUEURS, None, 0.5, maintenant=plus_tard)
    assert rapport["historique"]["couvert"] is True and rapport["statut"] == "OK"
