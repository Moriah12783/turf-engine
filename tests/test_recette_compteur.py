"""Compteur de la recette A en production (édition du matin, à partir du 30/11/2026).

Base de synthèse au format de turf_bench.db : seules les colonnes lues par le
lecteur scellé (``ombre_lecture.load``) sont créées. Le compteur ne publie que
des comptes, jamais une probabilité ni le témoin NVE.
"""
import io
import json
import os
import re
import sqlite3
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab import engine, fondamental_nuit, ombre, recette_compteur as rc

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOUR = "2026-11-30"


def _meta(recette=rc.RECETTE_A, motif=None, version=rc.MODELE_ATTENDU, nve=None, reel=True, sans_marqueur=False):
    meta = {"market_calibration": {"applied": reel, "market_weight": 0.9 if reel else 0.0, "coverage_pct": 100.0},
            "value_indices": {"1": 1.234567}}
    if sans_marqueur:
        return meta
    meta.update({"recette": recette, "nve_version": nve or (rc.NVE_RECETTE_A if recette == rc.RECETTE_A else rc.NVE_REPLI),
                 "fondamental_model_version": version})
    if motif:
        meta["motif_repli"] = motif
    if recette == rc.RECETTE_A:
        meta["temoin_nve"] = {"nve_version": rc.NVE_REPLI, "probabilities": {"1": 0.314159, "2": 0.271828},
                              "selection": [1, 2]}
    return meta


def _banc(path, editions, jour=JOUR):
    """editions : liste de dicts de métadonnées, une édition T_MATIN par course."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE predictions (race_id TEXT, engine_name TEXT, horizon TEXT, lock_time TEXT, "
                 "selection_json TEXT, probabilities_json TEXT, metadata_json TEXT)")
    conn.execute("CREATE TABLE race_results (race_id TEXT, statut TEXT, ranking_json TEXT, arrival_order_json TEXT)")
    a, m, j = jour[:4], jour[5:7], jour[8:10]
    for i, meta in enumerate(editions, 1):
        rid = f"R{(i - 1) // 8 + 1}C{(i - 1) % 8 + 1}_{j}{m}{a}_TESTVILLE"
        for horizon in ("T_MATIN", "T90"):
            conn.execute("INSERT INTO predictions VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (rid, "NEW_VALUE_ENGINE", horizon, f"{jour}T07:30:41", "[1, 2, 3]",
                          json.dumps({"1": 0.4321, "2": 0.3456, "3": 0.2223}),
                          json.dumps(meta if horizon == "T_MATIN" else {"market_calibration": {"applied": True}})))
    conn.commit()
    conn.close()
    return path


def _main(path, *args, maintenant=None):
    out = io.StringIO()
    with redirect_stdout(out):
        code = rc.main(["--banc", path, *args], maintenant=maintenant)
    texte = out.getvalue()
    return code, json.loads(texte.split("RECETTE_COMPTEUR ", 1)[1].splitlines()[0]), texte


def test_avant_la_production_rien_n_est_compte(tmp_path):
    path = _banc(str(tmp_path / "b.db"), [_meta(sans_marqueur=True)] * 10, jour="2026-11-29")
    code, r, _ = _main(path, "--jour", "2026-11-29")
    assert code == 0 and r["statut"] == "AVANT_PRODUCTION" and "editions_matin" not in r


def test_matinee_complete_en_recette_a(tmp_path):
    path = _banc(str(tmp_path / "b.db"), [_meta()] * 30)
    code, r, _ = _main(path, "--jour", JOUR)
    assert code == 0 and r["statut"] == "OK" and r["alertes"] == []
    assert (r["editions_matin"], r["marche_reel"], r["recette_a"], r["repli"], r["marqueur_absent"]) == (30, 30, 30, 0, 0)
    assert r["part_recette_a"] == 1.0 and r["courses_en_repli"] == []
    assert r["versions_fondamental"] == {rc.MODELE_ATTENDU: 30} and r["versions_nve"] == {rc.NVE_RECETTE_A: 30}


def test_quelques_replis_au_seuil(tmp_path):
    eds = [_meta()] * 27 + [_meta(rc.RECETTE_REPLI, "TABLE_ABSENTE", version=None)] * 3
    code, r, _ = _main(_banc(str(tmp_path / "b.db"), eds), "--jour", JOUR)
    assert code == 0 and r["part_recette_a"] == 0.9 and r["repli"] == 3
    assert r["motifs_repli"] == {"TABLE_ABSENTE": 3}
    assert [c["motif"] for c in r["courses_en_repli"]] == ["TABLE_ABSENTE"] * 3
    assert all(re.fullmatch(r"R\d+C\d+_30112026_TESTVILLE", c["race_id"]) for c in r["courses_en_repli"])


def test_part_basse_alerte(tmp_path):
    eds = [_meta()] * 26 + [_meta(rc.RECETTE_REPLI, "PARTANT_SANS_FONDAMENTAL")] * 4
    code, r, texte = _main(_banc(str(tmp_path / "b.db"), eds), "--jour", JOUR)
    assert code == 1 and r["statut"] == "ALERTE" and "::error::" in texte
    assert any(a.startswith("PART_RECETTE_A_BASSE") for a in r["alertes"])


def test_matinee_entiere_en_repli_calcul_de_nuit_manque(tmp_path):
    eds = [_meta(rc.RECETTE_REPLI, "TABLE_ABSENTE", version=None)] * 25
    code, r, _ = _main(_banc(str(tmp_path / "b.db"), eds), "--jour", JOUR)
    assert code == 1 and any(a.startswith("MATINEE_EN_REPLI") for a in r["alertes"])
    assert not any(a.startswith("PART_RECETTE_A_BASSE") for a in r["alertes"])       # une seule alerte de part


def test_marche_partiel_hors_du_denominateur(tmp_path):
    eds = [_meta()] * 20 + [_meta(rc.RECETTE_REPLI, "MARCHE_ABSENT", reel=False)] * 10
    code, r, _ = _main(_banc(str(tmp_path / "b.db"), eds), "--jour", JOUR)
    assert code == 0 and r["marche_reel"] == 20 and r["part_recette_a"] == 1.0 and r["repli"] == 10
    assert all(c["marche_reel"] is False for c in r["courses_en_repli"])


def test_marqueur_absent_et_recette_inconnue(tmp_path):
    eds = [_meta()] * 28 + [_meta(sans_marqueur=True), _meta(recette="B_inconnue")]
    code, r, _ = _main(_banc(str(tmp_path / "b.db"), eds), "--jour", JOUR)
    assert code == 1 and r["marqueur_absent"] == 1 and r["recette_inconnue"] == 1
    assert {a.split(" :")[0] for a in r["alertes"]} == {"MARQUEUR_ABSENT", "RECETTE_INCONNUE"}


def test_versions_inattendues(tmp_path):
    eds = [_meta()] * 28 + [_meta(version="fond-matin-autre")] + [_meta(nve="nve-2099-01-01")]
    code, r, _ = _main(_banc(str(tmp_path / "b.db"), eds), "--jour", JOUR)
    assert code == 1
    assert "VERSION_INATTENDUE : fondamental fond-matin-autre" in r["alertes"]
    assert "VERSION_INATTENDUE : NVE nve-2099-01-01" in r["alertes"]


def test_essai_avant_la_production_sans_alerte(tmp_path):
    path = _banc(str(tmp_path / "b.db"), [_meta(sans_marqueur=True)] * 12, jour="2026-11-20")
    code, r, texte = _main(path, "--jour", "2026-11-20", "--essai")
    assert code == 0 and r["statut"] == "ESSAI" and r["alertes"] == [] and "::error::" not in texte
    assert r["editions_matin"] == 12 and r["marqueur_absent"] == 12


def test_jour_selon_le_mode():
    assert rc.jour_du_mode("matin", datetime(2026, 11, 30, 7, 50, tzinfo=timezone.utc)) == "2026-11-30"
    assert rc.jour_du_mode("jour", datetime(2026, 12, 1, 1, 17, tzinfo=timezone.utc)) == "2026-11-30"


def test_aucune_probabilite_ni_temoin_publies(tmp_path):
    eds = [_meta()] * 25 + [_meta(rc.RECETTE_REPLI, "AUCUNE_LIGNE")] * 5
    _, _, texte = _main(_banc(str(tmp_path / "b.db"), eds), "--jour", JOUR)
    for valeur in ("0.4321", "0.3456", "0.2223", "0.314159", "0.271828", "1.234567"):
        assert valeur not in texte, valeur
    for interdit in ("temoin", "probab", "selection", "delta", "ic95"):
        assert interdit not in texte.lower(), interdit


def test_le_compteur_ne_cite_jamais_le_temoin():
    source = open(os.path.join(RACINE, "turf_lab", "recette_compteur.py"), encoding="utf-8").read()
    assert "temoin" not in source.lower()


def test_contrat_avec_le_moteur_et_le_gel():
    assert rc.MODELE_ATTENDU == fondamental_nuit.model_version()      # modèle figé pendant la fenêtre
    assert rc.RECETTE_A == ombre.RECETTE == engine.OMBRE_RECETTE
    assert rc.NVE_REPLI == engine.NVE_VERSION
    # Une fois le paquet moteur fusionné, ses constantes doivent concorder.
    for nom, attendu in (("RECETTE_A_DEBUT", rc.DEBUT_PRODUCTION), ("RECETTE_REPLI", rc.RECETTE_REPLI),
                         ("NVE_VERSION_RECETTE_A", rc.NVE_RECETTE_A)):
        if hasattr(engine, nom):
            assert getattr(engine, nom) == attendu, nom
