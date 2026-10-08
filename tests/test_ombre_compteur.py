"""Tests du compteur quotidien de l'ombre : comptes par jour et compteur
officiel ; jamais de lecture, même passé 1 000 éditions ou le 36e jour ;
jamais de probabilité ni d'écart imprimés ; alertes sur une édition éligible
récente sans ombre, une autre clé, une couverture sous 90 %."""
import io
import json
import os
import re
import sqlite3
import sys
from contextlib import redirect_stdout

import pytest

np = pytest.importorskip("numpy")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab import ombre, ombre_compteur as compteur, ombre_lecture as lecture
from tests.test_ombre_lecture import DEBUT, make_bench

CLE = ("fond-v1", "nve-1", ombre.RECETTE)                  # clé des bancs synthétiques
CLE_GELEE = compteur.cle_gelee                              # la vraie, avant remplacement par la fixture
RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _main(path, jour):
    with redirect_stdout(io.StringIO()) as out:
        code = compteur.main(["--banc", path, "--jour", jour])
    texte = out.getvalue()
    return code, json.loads(texte.split("OMBRE_COMPTEUR_JOUR ", 1)[1].splitlines()[0]), texte


@pytest.fixture(autouse=True)
def _regle(monkeypatch):
    monkeypatch.setattr(lecture, "DEBUT_OMBRE", DEBUT)
    monkeypatch.setattr(compteur, "cle_gelee", lambda: CLE)


def test_comptes_par_jour_et_compteur_officiel(tmp_path):
    path = make_bench(str(tmp_path / "b.db"), 90)            # 3 jours de 30 éditions, toutes avec ombre
    code, r, _ = _main(path, "2026-10-09")
    assert code == 0 and r["statut"] == "OK" and r["alertes"] == []
    assert list(r["jours"]) == ["2026-10-07", "2026-10-08", "2026-10-09"]
    assert all(j == {"editions_matin": 30, "eligibles": 30, "avec_ombre": 30, "sans_ombre": 0, "cle_differente": 0,
                     "ombre_hors_eligibles": 0} for j in r["jours"].values())
    assert r["officiel"]["editions_eligibles"] == 90 and r["officiel"]["editions_avec_ombre"] == 90
    assert r["couverture"] == 1.0 and r["officiel"]["lecture_au_plus_tard"] == "2026-11-11"


def test_jamais_de_lecture_meme_apres_1000_editions_et_le_36e_jour(tmp_path):
    path = make_bench(str(tmp_path / "b.db"), 1100)
    with redirect_stdout(io.StringIO()):
        assert "lecture" in lecture.read(path, debut=DEBUT, today="2027-06-01")   # le lecteur, lui, lirait
    code, r, texte = _main(path, "2027-06-01")
    assert code == 0 and "lecture" not in r["officiel"] and r["officiel"]["editions_avec_ombre"] == 1050      # 35 jours de 30
    for interdit in ("OMBRE_LECTURE", "delta", "ic95", "ecart", "decision", "probab", "selection", "fondamental"):
        assert interdit not in texte, interdit


def test_aucun_chiffre_de_l_ombre_ni_de_l_edition_publiee(tmp_path):
    path = make_bench(str(tmp_path / "b.db"), 30)
    _, _, texte = _main(path, "2026-10-07")
    conn = sqlite3.connect(path)
    valeurs = set()
    for (probs, meta) in conn.execute("SELECT probabilities_json, metadata_json FROM predictions"):
        valeurs |= {str(v) for v in json.loads(probs).values()}
        for v in json.loads(meta).get(lecture.META_OMBRE, {}).get("probabilities", {}).values():
            valeurs.add(str(v))
    conn.close()
    nombres = set(re.findall(r"\d+\.\d+", texte))
    assert not (nombres & {v for v in valeurs if "." in v and len(v) > 4})


def test_alerte_edition_recente_sans_ombre(tmp_path):
    path = make_bench(str(tmp_path / "b.db"), 60, missing_every=7)    # quelques éditions sans ombre
    code, r, texte = _main(path, "2026-10-08")
    assert code == 1 and r["statut"] == "ALERTE" and "::error::" in texte
    assert r["jours"]["2026-10-08"]["sans_ombre"] > 0
    assert any(a.startswith("2026-10-08 :") for a in r["alertes"])
    assert any(a.startswith("2026-10-07 :") for a in r["alertes"])                  # hier aussi


def test_un_trou_ancien_reste_compte_sans_alerter_chaque_jour(tmp_path):
    path = make_bench(str(tmp_path / "b.db"), 300, missing_first=1)   # 10 jours ; 1re édition sans ombre
    code, r, _ = _main(path, "2026-10-16")
    assert r["jours"]["2026-10-07"]["sans_ombre"] == 1
    assert code == 0 and r["statut"] == "OK"                          # couverture 299/300, au-dessus de 90 %
    assert r["officiel"]["exclusions"] == {"sans_ombre_complete": 1}


def test_alerte_autre_cle(tmp_path):
    path = make_bench(str(tmp_path / "b.db"), 60, switch_at=45)       # 15 éditions d'une autre version
    code, r, _ = _main(path, "2026-10-08")
    assert code == 1 and r["jours"]["2026-10-08"]["cle_differente"] == 15
    assert any("autre clé" in a for a in r["alertes"])


def test_alerte_couverture_sous_90(tmp_path):
    path = make_bench(str(tmp_path / "b.db"), 300, missing_every=5)   # 1 sur 5 sans ombre : 80 %
    code, r, _ = _main(path, "2026-10-30")
    assert code == 1 and r["couverture"] < ombre.COUVERTURE_MIN
    assert any(a.startswith("couverture") for a in r["alertes"])


def test_workflow_lecture_seule_et_jours_de_l_ombre():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(open(os.path.join(RACINE, ".github", "workflows", "ombre_compteur.yml"), encoding="utf-8"))
    declencheurs = doc.get("on", doc.get(True))
    assert {c["cron"] for c in declencheurs["schedule"]} == {"23 9 8-31 10 *", "23 9 1-25 11 *"}
    assert doc["permissions"] == {"contents": "read"}
    texte = " ".join(str(e.get("run", "")) for e in doc["jobs"]["compteur"]["steps"])
    assert "r2_store fetch" in texte and "r2_store pull" not in texte and "push" not in texte


def test_cle_gelee_est_celle_du_code():
    from turf_lab import engine, fondamental_nuit
    assert CLE_GELEE() == (fondamental_nuit.model_version(), engine.NVE_VERSION, ombre.RECETTE)
    assert CLE_GELEE()[:2] == ("fond-matin-e570d663793c", "nve-2026-10-07")
