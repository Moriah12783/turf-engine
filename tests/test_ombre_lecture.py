"""Tests de la lecture scellée de l'ombre : constantes identiques au document
daté, aucune lecture intermédiaire, lectures figées, décisions de la règle."""
import io
import json
import os
import re
import sqlite3
import sys
from contextlib import redirect_stdout
from datetime import date, timedelta

import pytest

np = pytest.importorskip("numpy")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab import fondamental_nuit, ombre, ombre_lecture as lecture

DOC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "OMBRE_FONDAMENTAL.md")
HORIZONS = ("T_MATIN", "T90", "T30", "T15")
DEBUT = "2026-10-07"


def test_constantes_identiques_au_document():
    rows = dict(re.findall(r"^\| ([A-Z_0-9]+) \| (.+?) \|$", open(DOC, encoding="utf-8").read(), re.M))
    assert float(rows["POIDS_MARCHE"]) == ombre.POIDS_MARCHE
    assert int(rows["LECTURE_1"]) == ombre.LECTURE_1 and int(rows["LECTURE_2"]) == ombre.LECTURE_2
    assert float(rows["NIVEAU_LECTURE_1"]) == ombre.NIVEAU_LECTURE_1
    assert float(rows["NIVEAU_LECTURE_2"]) == ombre.NIVEAU_LECTURE_2
    assert float(rows["NIVEAU_INUTILITE"]) == ombre.NIVEAU_INUTILITE
    assert float(rows["COUVERTURE_MIN"]) == ombre.COUVERTURE_MIN
    assert float(rows["SEUIL_NON_DEGRADATION"]) == ombre.SEUIL_NON_DEGRADATION
    assert tuple(rows["HORIZONS_SECONDAIRES"].split(", ")) == ombre.HORIZONS_SECONDAIRES
    assert float(rows["PUISSANCE_MIN"]) == ombre.PUISSANCE_MIN
    assert int(rows["BOOTSTRAP_TIRAGES"]) == ombre.BOOTSTRAP_TIRAGES and int(rows["GRAINE"]) == ombre.GRAINE
    assert tuple(int(x) for x in rows["HEURE_LIMITE_UTC"].split(":")) == fondamental_nuit.HEURE_LIMITE_UTC
    assert int(rows["RETARD_MAX_JOURS"]) == fondamental_nuit.RETARD_MAX_JOURS
    assert rows["META_OMBRE"] == lecture.META_OMBRE
    debut = rows["DEBUT_OMBRE"]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", debut):
        assert lecture.DEBUT_OMBRE == debut                     # règle gelée
    else:
        assert lecture.DEBUT_OMBRE is None                      # projet : aucune lecture possible


def _softmax(x):
    e = np.exp(x - x.max())
    return e / e.sum()


def make_bench(path, n, shadow="meilleure", missing_every=None, switch_at=None, horizons=HORIZONS, seed=3):
    """n courses de 10 partants (3 réunions de 8 courses par jour) ; l'édition
    publiée est bruitée, l'ombre plus proche de la vérité (ou inversée)."""
    rng = np.random.default_rng(seed)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE predictions (prediction_id TEXT, race_id TEXT, engine_name TEXT, horizon TEXT, "
                 "lock_time TEXT, selection_json TEXT, probabilities_json TEXT, metadata_json TEXT)")
    conn.execute("CREATE TABLE race_results (race_id TEXT, statut TEXT, ranking_json TEXT, arrival_order_json TEXT)")
    nums = list(range(1, 11))
    for i in range(n):
        day = date.fromisoformat(DEBUT) + timedelta(days=i // 24)
        race_id = f"R{1 + (i % 24) // 8}C{1 + i % 8}_{day.strftime('%d%m%Y')}_SYN"
        s = rng.normal(0, 1.2, 10)
        order = rng.choice(10, size=10, replace=False, p=_softmax(s))
        pub = _softmax(0.5 * s + rng.normal(0, 0.9, 10))
        sh = _softmax(0.95 * s + rng.normal(0, 0.3, 10)) if shadow == "meilleure" else _softmax(-s)
        version = "fond-v1" if switch_at is None or i < switch_at else "fond-v2"
        for k, h in enumerate(horizons):
            meta = {"market_calibration": {"applied": True, "market_weight": 0.9}}
            if not (missing_every and i % missing_every == 0):
                meta["ombre_fondamental"] = {"recette": "A_lineaire_0.90_0.10", "model_version": version,
                                             "nve_version": "nve-1", "train_until": "x",
                                             "probabilities": {str(a): float(b) for a, b in zip(nums, sh)}}
            conn.execute("INSERT INTO predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
                f"{race_id}_NEW_{h}", race_id, "NEW_VALUE_ENGINE", h,
                f"{day.isoformat()}T{6 + 3 * k:02d}:{30 + i % 24:02d}:00",
                json.dumps([nums[j] for j in np.argsort(-pub)]), json.dumps({str(a): float(b) for a, b in zip(nums, pub)}),
                json.dumps(meta)))
        conn.execute("INSERT INTO race_results VALUES (?, 'DEFINITIVE', ?, NULL)",
                     (race_id, json.dumps([{"rang": r + 1, "num": int(order[r]) + 1} for r in range(10)])))
    conn.commit()
    conn.close()
    return path


def _read(path, **kw):
    with redirect_stdout(io.StringIO()) as out:
        rep = lecture.read(path, debut=kw.get("debut", DEBUT))
    return rep, out.getvalue()


def test_refus_sans_date_de_debut(tmp_path):
    if lecture.DEBUT_OMBRE is not None:
        pytest.skip("règle gelée : la date de début est inscrite")
    rep, logs = _read(make_bench(str(tmp_path / "b.db"), 10), debut=None)
    assert "refus" in rep and logs.startswith("OMBRE_REFUS ")


def test_aucune_lecture_avant_1000(tmp_path):
    rep, logs = _read(make_bench(str(tmp_path / "b.db"), 999))
    assert rep["editions_avec_ombre"] == 999 and "lecture_1" not in rep
    assert "aucune lecture intermédiaire" in rep["prochaine_lecture"]
    assert "delta_ll" not in logs and "ecart" not in logs and logs.startswith("OMBRE_COMPTEUR ")


def test_lecture_1_figee_puis_passage(tmp_path):
    first, _ = _read(make_bench(str(tmp_path / "a.db"), 1000))
    later, _ = _read(make_bench(str(tmp_path / "b.db"), 1300))
    assert first["lecture_1"] == later["lecture_1"]                # relire plus tard ne change rien
    l1 = first["lecture_1"]
    assert l1["editions"] == 1000 and l1["couverture"] == 1.0 and "ic99" in l1
    assert l1["ic99"][0] > 0 and l1["echecs_secondaires"] == []
    assert len(l1["criteres_secondaires"]) == 8
    assert l1["decision"] == "PASSAGE_EN_PRODUCTION_SUR_DECISION_ECRITE_DE_STEPH"
    assert "lecture_2" not in later                                # décision prise : plus de lecture


def test_couverture_insuffisante_suspend_la_decision(tmp_path):
    rep, _ = _read(make_bench(str(tmp_path / "b.db"), 1250, missing_every=6))
    l1 = rep["lecture_1"]
    assert rep["exclusions"]["sans_ombre"] > 0 and l1["couverture"] < ombre.COUVERTURE_MIN
    assert l1["decision"] == "DECISION_SUSPENDUE_COUVERTURE" and "delta_ll" in l1   # lecture rendue quand même


def test_arret_pour_inutilite(tmp_path):
    rep, _ = _read(make_bench(str(tmp_path / "b.db"), 1000, shadow="inversee"))
    assert rep["lecture_1"]["ic95_inutilite"][1] < 0 and rep["lecture_1"]["decision"] == "ARRET_INUTILITE"


def test_changement_de_code_remet_le_compteur(tmp_path):
    rep, _ = _read(make_bench(str(tmp_path / "b.db"), 1100, switch_at=300))
    assert rep["model_version"] == "fond-v2" and rep["editions_avec_ombre"] == 800 and "lecture_1" not in rep


def test_critere_secondaire_non_mesurable_bloque(tmp_path):
    rep, _ = _read(make_bench(str(tmp_path / "b.db"), 1000, horizons=("T_MATIN",)))
    l1 = rep["lecture_1"]
    assert l1["ic99"][0] > 0 and "gagnant_dans_8_T90" in l1["echecs_secondaires"]
    assert l1["criteres_secondaires"]["tierce_dans_8_T15"]["verdict"] == "NON_MESURABLE"
    assert l1["decision"] == "PASSAGE_BLOQUE_CRITERE_SECONDAIRE"
