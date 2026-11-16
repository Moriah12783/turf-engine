"""Garde du test automatique à chaque PR (décision de Steph du 16/11/2026).

Conditions du dev NVE, dépôt public : déclencheur pull_request (jamais
pull_request_target), permissions en lecture seule, aucun secret, groupe de
concurrence distinct de turf-sync, Python 3.11 comme en production, et
test_verrouillage_t15.py (dépendant de l'heure réelle) non lancé.
"""
import os

import pytest

yaml = pytest.importorskip("yaml")

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHEMIN = os.path.join(RACINE, ".github", "workflows", "tests.yml")


def _doc():
    return yaml.safe_load(open(CHEMIN, encoding="utf-8"))


def test_declencheurs_sans_pull_request_target():
    doc = _doc()
    declencheurs = doc.get("on", doc.get(True)) or {}
    assert "pull_request" in declencheurs
    assert "pull_request_target" not in declencheurs
    assert set(declencheurs) <= {"pull_request", "workflow_dispatch"}


def test_lecture_seule_et_aucun_secret():
    doc = _doc()
    assert doc["permissions"] == {"contents": "read"}
    texte = open(CHEMIN, encoding="utf-8").read()
    assert "secrets." not in texte and "R2_" not in texte and "CLOUDFLARE" not in texte
    for job in doc["jobs"].values():
        assert "permissions" not in job                      # aucun droit ajouté au niveau du job
        checkout = [e for e in job["steps"] if str(e.get("uses", "")).startswith("actions/checkout")]
        assert checkout and all(e.get("with", {}).get("persist-credentials") is False for e in checkout)


def test_concurrence_distincte_de_la_production():
    groupe = _doc()["concurrency"]["group"]
    assert "turf-sync" not in groupe and groupe.startswith("tests-")
    for nom in os.listdir(os.path.join(RACINE, ".github", "workflows")):
        if nom == "tests.yml" or not nom.endswith(".yml"):
            continue
        autre = yaml.safe_load(open(os.path.join(RACINE, ".github", "workflows", nom), encoding="utf-8"))
        assert (autre.get("concurrency") or {}).get("group") != groupe, nom


def test_python_311_et_suite_sans_le_test_horaire():
    doc = _doc()
    etapes = [e for job in doc["jobs"].values() for e in job["steps"]]
    versions = [e["with"]["python-version"] for e in etapes if str(e.get("uses", "")).startswith("actions/setup-python")]
    assert versions == ["3.11"]
    commandes = " ".join(str(e.get("run", "")) for e in etapes)
    assert "pytest" in commandes and "tests turf_lab/tests" in commandes
    assert "test_verrouillage_t15" not in commandes
