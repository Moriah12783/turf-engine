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
                                        "garde_ombre.yml", "ombre_compteur.yml", "recette_compteur.yml"}
    for _, workflow, cles in taches:
        assert set(cles) <= _entrees(workflow), (workflow, cles)


def test_horaires_du_plan_identiques_a_wrangler():
    toml = open(os.path.join(DOSSIER, "wrangler.toml"), encoding="utf-8").read()
    crons = set(re.findall(r'"([^"]+)"', toml[toml.index("crons"):]))
    assert {c for c, _, _ in _plan()} == crons == {"17 1 * * *", "5 5 * * *", "50 5 * * *", "50 7 * * *"}
    assert len(crons) <= 5                                   # limite du forfait gratuit : 5 déclencheurs par compte


def _lignes(workflow):
    texte = open(os.path.join(DOSSIER, "worker.js"), encoding="utf-8").read()
    return [l for l in texte.splitlines() if f'workflow: "{workflow}"' in l and l.strip().startswith("{")]


def test_filet_github_du_calcul_de_nuit_sur_les_jours_de_l_horloge():
    """Décision de Steph du 13/11 : calcul de nuit maintenu après le 24/11,
    sans date de fin (recette A en production au 30/11). Les horaires GitHub
    restent un filet, chaque jour, et un filet parti après la limite
    d'écriture du jour (turf_lab/plancher_matin.py) ne calcule rien et
    n'envoie rien."""
    lignes = _lignes("fondamental_nuit.yml")
    assert len(lignes) == 2
    assert all('du: "2026-10-08"' in l and "au:" not in l for l in lignes)
    chemin = os.path.join(RACINE, ".github", "workflows", "fondamental_nuit.yml")
    doc = yaml.safe_load(open(chemin, encoding="utf-8"))
    declencheurs = doc.get("on", doc.get(True)) or {}
    assert {c["cron"] for c in declencheurs["schedule"]} == {"5 5 * * *", "50 5 * * *"}
    etapes = doc["jobs"]["nuit"]["steps"]
    garde = [e for e in etapes if str(e.get("name", "")).startswith("Late GitHub safety net")]
    assert len(garde) == 1
    garde = garde[0]
    assert garde["if"] == "github.event_name == 'schedule'"
    assert "python -m turf_lab.plancher_matin ecriture" in garde["run"]
    assert "FILET_HORS_DELAI=1" in garde["run"]
    # La garde passe avant tout calcul : seules l'extraction du dépôt et
    # l'installation de Python la précèdent.
    assert [e.get("name") for e in etapes[:etapes.index(garde)]] == ["Checkout Repository", "Set up Python"]
    nuit = [e for e in etapes if "env.ACTION == 'nuit'" in str(e.get("if", ""))]
    assert len(nuit) == 3 and all("env.FILET_HORS_DELAI != '1'" in e["if"] for e in nuit)


def test_compteur_de_la_recette_a_dans_le_deploiement_du_22_11():
    """Le compteur recette A / repli est dans le plan dès le redéploiement du
    22/11, avec ses dates de début : aucun second redéploiement."""
    lignes = _lignes("recette_compteur.yml")
    assert len(lignes) == 2 and all("au:" not in l for l in lignes)
    matin = [l for l in lignes if 'cron: "50 7 * * *"' in l]
    nuit = [l for l in lignes if 'cron: "17 1 * * *"' in l]
    assert len(matin) == 1 and 'mode: "matin"' in matin[0] and 'du: "2026-11-30"' in matin[0]
    assert len(nuit) == 1 and 'mode: "jour"' in nuit[0] and 'du: "2026-12-01"' in nuit[0]
    from turf_lab import recette_compteur
    assert recette_compteur.DEBUT_PRODUCTION == "2026-11-30"
