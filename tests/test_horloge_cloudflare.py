"""Cohérence de l'horloge Cloudflare (infra/cloudflare/horloge) avec les
workflows qu'elle lance : chaque workflow existe et déclare les entrées
envoyées (sinon GitHub refuse le lancement, 422), et les horaires du plan
sont exactement ceux configurés dans wrangler.toml. La logique du Worker est
testée par node : node --test infra/cloudflare/horloge/worker.test.mjs."""
import os
import re

import pytest

yaml = pytest.importorskip("yaml")

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOSSIER = os.path.join(RACINE, "infra", "cloudflare", "horloge")
_TACHE = re.compile(r'\{\s*cron:\s*"([^"]+)",\s*workflow:\s*"([^"]+)",\s*inputs:\s*\{([^}]*)\}')


def _plan():
    texte = open(os.path.join(DOSSIER, "worker.js"), encoding="utf-8").read()
    bloc = texte[texte.index("const PLAN"):texte.index("];", texte.index("const PLAN"))]
    taches = [(c, w, re.findall(r'(\w+):\s*"[^"]*"', i)) for c, w, i in _TACHE.findall(bloc)]
    assert len(taches) == bloc.count("cron:")                 # aucune tâche illisible pour ce test
    return taches


def _entrees(workflow):
    doc = yaml.safe_load(open(os.path.join(RACINE, ".github", "workflows", workflow), encoding="utf-8"))
    declencheurs = doc.get("on", doc.get(True)) or {}
    return set(((declencheurs.get("workflow_dispatch") or {}).get("inputs") or {}).keys())


def test_chaque_tache_lance_un_workflow_existant_avec_des_entrees_declarees():
    taches = _plan()
    assert {w for _, w, _ in taches} == {"history_export.yml", "repetition_ombre.yml", "fondamental_nuit.yml",
                                        "garde_ombre.yml", "ombre_compteur.yml"}
    for _, workflow, cles in taches:
        assert set(cles) <= _entrees(workflow), (workflow, cles)


def test_horaires_du_plan_identiques_a_wrangler():
    toml = open(os.path.join(DOSSIER, "wrangler.toml"), encoding="utf-8").read()
    crons = set(re.findall(r'"([^"]+)"', toml[toml.index("crons"):]))
    assert {c for c, _, _ in _plan()} == crons == {"17 1 * * *", "5 5 * * *", "50 5 * * *"}


def test_filet_github_du_calcul_de_nuit_sur_les_jours_de_l_horloge():
    """Gel du 07/10 : les horaires GitHub du calcul de nuit sont un filet, sur
    les mêmes jours que l'horloge (08/10-24/11), et un filet parti après
    06h20 UTC ne calcule rien et n'envoie rien."""
    texte = open(os.path.join(DOSSIER, "worker.js"), encoding="utf-8").read()
    jours = set(re.findall(r'workflow: "fondamental_nuit\.yml".*?du: "([\d-]+)", au: "([\d-]+)"', texte))
    assert jours == {("2026-10-08", "2026-11-24")}
    chemin = os.path.join(RACINE, ".github", "workflows", "fondamental_nuit.yml")
    doc = yaml.safe_load(open(chemin, encoding="utf-8"))
    declencheurs = doc.get("on", doc.get(True)) or {}
    assert {c["cron"] for c in declencheurs["schedule"]} == {
        "5 5 8-31 10 *", "50 5 8-31 10 *", "5 5 1-24 11 *", "50 5 1-24 11 *"}
    etapes = doc["jobs"]["nuit"]["steps"]
    garde = etapes[0]
    assert garde["if"] == "github.event_name == 'schedule'" and ">= 620" in garde["run"]
    assert "FILET_HORS_DELAI=1" in garde["run"]
    nuit = [e for e in etapes if "env.ACTION == 'nuit'" in str(e.get("if", ""))]
    assert len(nuit) == 3 and all("env.FILET_HORS_DELAI != '1'" in e["if"] for e in nuit)
