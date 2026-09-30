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
    assert {w for _, w, _ in taches} == {"history_export.yml", "repetition_ombre.yml", "fondamental_nuit.yml"}
    for _, workflow, cles in taches:
        assert set(cles) <= _entrees(workflow), (workflow, cles)


def test_horaires_du_plan_identiques_a_wrangler():
    toml = open(os.path.join(DOSSIER, "wrangler.toml"), encoding="utf-8").read()
    crons = set(re.findall(r'"([^"]+)"', toml[toml.index("crons"):]))
    assert {c for c, _, _ in _plan()} == crons == {"17 1 * * *", "5 5 * * *", "50 5 * * *"}
