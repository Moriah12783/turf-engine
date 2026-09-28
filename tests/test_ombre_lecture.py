"""Tests de la lecture scellée de l'ombre : constantes identiques au document
daté, aucune lecture intermédiaire, lecture unique (1 000 éditions ou
35 jours), édition du matin seulement, décisions de la règle."""
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
DEBUT = "2026-10-07"
LOIN = "2027-06-01"                                   # date de lecture bien après la fin de l'ombre


def test_constantes_identiques_au_document():
    rows = dict(re.findall(r"^\| ([A-Z_0-9]+) \| (.+?) \|$", open(DOC, encoding="utf-8").read(), re.M))
    assert float(rows["POIDS_MARCHE"]) == ombre.POIDS_MARCHE
    assert rows["RECETTE"] == ombre.RECETTE
    assert int(rows["LECTURE"]) == ombre.LECTURE and int(rows["DUREE_MAX_JOURS"]) == ombre.DUREE_MAX_JOURS
    assert float(rows["NIVEAU_LECTURE"]) == ombre.NIVEAU_LECTURE
    assert float(rows["NIVEAU_INUTILITE"]) == ombre.NIVEAU_INUTILITE
    assert float(rows["COUVERTURE_MIN"]) == ombre.COUVERTURE_MIN
    assert float(rows["SEUIL_NON_DEGRADATION"]) == ombre.SEUIL_NON_DEGRADATION
    assert tuple(rows["HORIZONS_SECONDAIRES"].split(", ")) == ombre.HORIZONS_SECONDAIRES == ("T_MATIN",)
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


def make_bench(path, n, per_day=30, shadow="meilleure", missing_every=None, switch_at=None, bad_selection=False,
               no_selection=False, seed=3, small_every=None, missing_first=0, dead_heat_every=None):
    """n courses de 10 partants (7 une course sur ``small_every``), réunions
    de 10 courses ; édition publiée bruitée, ombre plus proche de la vérité
    (ou inversée). L'archive porte les clés convenues avec le dev NVE, dont
    la sélection de l'ombre. ``missing_first`` : premières éditions sans
    ombre (nuits manquées) ; ``dead_heat_every`` : deux premiers ex aequo."""
    rng = np.random.default_rng(seed)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE predictions (prediction_id TEXT, race_id TEXT, engine_name TEXT, horizon TEXT, "
                 "lock_time TEXT, selection_json TEXT, probabilities_json TEXT, metadata_json TEXT)")
    conn.execute("CREATE TABLE race_results (race_id TEXT, statut TEXT, ranking_json TEXT, arrival_order_json TEXT)")
    for i in range(n):
        day = date.fromisoformat(DEBUT) + timedelta(days=i // per_day)
        k = i % per_day
        race_id = f"R{1 + k // 10}C{1 + k % 10}_{day.strftime('%d%m%Y')}_SYN"
        m = 7 if small_every and i % small_every == 0 else 10
        nums = list(range(1, m + 1))
        s = rng.normal(0, 1.2, m)
        order = rng.choice(m, size=m, replace=False, p=_softmax(s))
        pub = _softmax(0.5 * s + rng.normal(0, 0.9, m))
        sh = _softmax(0.95 * s + rng.normal(0, 0.3, m)) if shadow == "meilleure" else _softmax(-s)
        version = "fond-v1" if switch_at is None or i < switch_at else "fond-v2"
        meta = {"market_calibration": {"applied": True, "market_weight": 0.9}}
        sel_ombre = [nums[j] for j in np.argsort(-sh)]
        if bad_selection:                                      # l'ombre écarte le gagnant de ses 8
            winner = int(order[0]) + 1
            sel_ombre = [x for x in sel_ombre if x != winner][:9] + [winner]
        if i >= missing_first and not (missing_every and i % missing_every == 0):
            arch = {"recette": ombre.RECETTE, "model_version": version, "nve_version": "nve-1", "train_until": "x",
                    "probabilities": {str(a): round(float(b), 4) for a, b in zip(nums, sh)},
                    "fondamental": {str(a): round(float(b), 4) for a, b in zip(nums, sh)}}
            if not no_selection:
                arch["selection"] = sel_ombre[:10]
            meta["ombre_fondamental"] = arch
        conn.execute("INSERT INTO predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
            f"{race_id}_NEW_T_MATIN", race_id, "NEW_VALUE_ENGINE", "T_MATIN",
            f"{day.isoformat()}T06:{30 + k:02d}:00", json.dumps([nums[j] for j in np.argsort(-pub)]),
            json.dumps({str(a): round(float(b), 4) for a, b in zip(nums, pub)}), json.dumps(meta)))
        ranks = [r + 1 for r in range(m)]
        if dead_heat_every and i % dead_heat_every == 0:
            ranks[1] = 1                                       # deux premiers ex aequo
        conn.execute("INSERT INTO race_results VALUES (?, 'DEFINITIVE', ?, NULL)",
                     (race_id, json.dumps([{"rang": ranks[r], "num": int(order[r]) + 1} for r in range(m)])))
    conn.commit()
    conn.close()
    return path


def _read(path, debut=DEBUT, today=LOIN):
    with redirect_stdout(io.StringIO()) as out:
        rep = lecture.read(path, debut=debut, today=today)
    return rep, out.getvalue()


def test_refus_sans_date_de_debut(tmp_path):
    if lecture.DEBUT_OMBRE is not None:
        pytest.skip("règle gelée : la date de début est inscrite")
    rep, logs = _read(make_bench(str(tmp_path / "b.db"), 10), debut=None)
    assert "refus" in rep and logs.startswith("OMBRE_REFUS ")


def test_aucune_lecture_avant_1000_ni_avant_la_fin_des_35_jours(tmp_path):
    path = make_bench(str(tmp_path / "b.db"), 600)             # 20 jours à 30 éditions
    rep, logs = _read(path, today="2026-10-30")                # 23e jour : ni 1 000, ni 35 jours
    assert rep["editions_avec_ombre"] == 600 and "lecture" not in rep
    assert "aucune lecture intermédiaire" in rep["prochaine_lecture"]
    assert rep["fin_des_35_jours"] == "2026-11-10" and rep["lecture_au_plus_tard"] == "2026-11-11"
    assert "delta_ll" not in logs and "ecart" not in logs and logs.startswith("OMBRE_COMPTEUR ")


def test_lecture_a_1000_editions_figee_puis_passage(tmp_path):
    first, _ = _read(make_bench(str(tmp_path / "a.db"), 1000))
    later, _ = _read(make_bench(str(tmp_path / "b.db"), 1040))
    assert first["lecture"] == later["lecture"]                # relire plus tard ne change rien
    lec = first["lecture"]
    assert lec["mode"] == "1000_editions" and lec["editions"] == 1000 and lec["couverture"] == 1.0
    assert lec["ic95"][0] > 0 and lec["echecs_secondaires"] == [] and len(lec["empreinte"]) == 64
    assert set(lec["criteres_secondaires"]) == {"gagnant_dans_8_T_MATIN", "tierce_dans_8_T_MATIN"}
    assert lec["decision"] == "PASSAGE_EN_PRODUCTION_DU_MATIN_SUR_DECISION_ECRITE_DE_STEPH"
    # Au plus tôt le lendemain de la 1 000e édition.
    same_day, _ = _read(make_bench(str(tmp_path / "c.db"), 1000), today=lec["dernier_jour"])
    assert "lecture" not in same_day


def test_lecture_au_35e_jour_si_1000_non_atteintes(tmp_path):
    path = make_bench(str(tmp_path / "b.db"), 900, per_day=20)  # 45 jours à 20 éditions
    too_early, _ = _read(path, today="2026-11-10")              # 35e jour : pas encore
    assert "lecture" not in too_early
    rep, _ = _read(path, today="2026-11-11")                    # 36e jour : lecture
    lec = rep["lecture"]
    assert lec["mode"] == "35_jours" and lec["editions"] == 700 and lec["dernier_jour"] == "2026-11-10"


def test_couverture_insuffisante_suspend_la_decision(tmp_path):
    rep, _ = _read(make_bench(str(tmp_path / "b.db"), 1100, missing_every=6))
    lec = rep["lecture"]
    assert rep["exclusions"]["sans_ombre_complete"] > 0 and lec["couverture"] < ombre.COUVERTURE_MIN
    assert lec["decision"] == "DECISION_SUSPENDUE_COUVERTURE" and "delta_ll" in lec


def test_archive_sans_selection_n_est_pas_une_ombre(tmp_path):
    rep, _ = _read(make_bench(str(tmp_path / "b.db"), 300, no_selection=True))
    assert rep["editions_avec_ombre"] == 0


def test_arret_pour_inutilite(tmp_path):
    rep, _ = _read(make_bench(str(tmp_path / "b.db"), 1000, shadow="inversee"))
    assert rep["lecture"]["ic95_inutilite"][1] < 0 and rep["lecture"]["decision"] == "ARRET_INUTILITE"


def test_bug_bloquant_remet_compteur_et_horloge_a_zero(tmp_path):
    rep, _ = _read(make_bench(str(tmp_path / "b.db"), 1100, switch_at=300))
    assert rep["model_version"] == "fond-v2" and rep["horloge_depart"] == "2026-10-17"
    assert rep["fin_des_35_jours"] == "2026-11-20" and rep["lecture"]["editions"] == 800
    assert rep["lecture"]["mode"] == "35_jours"                # 800 éditions en 27 jours, horloge relancée


def test_selection_de_l_ombre_degradee_bloque_le_passage(tmp_path):
    rep, _ = _read(make_bench(str(tmp_path / "b.db"), 1000, bad_selection=True))
    lec = rep["lecture"]
    assert lec["ic95"][0] > 0 and "gagnant_dans_8_T_MATIN" in lec["echecs_secondaires"]
    assert lec["decision"] == "PASSAGE_BLOQUE_CRITERE_SECONDAIRE"


def test_courses_a_moins_de_8_partants_comptees(tmp_path):
    rep, _ = _read(make_bench(str(tmp_path / "b.db"), 1000, small_every=5))
    lec = rep["lecture"]
    assert rep["exclusions"] == {} and lec["editions"] == 1000 and lec["couverture"] == 1.0


def test_horloge_et_couverture_partent_de_debut_ombre(tmp_path):
    # Cinq premières nuits manquées : elles comptent dans la couverture et
    # n'allongent pas le gel.
    rep, _ = _read(make_bench(str(tmp_path / "b.db"), 1150, per_day=40, missing_first=200))
    assert rep["horloge_depart"] == DEBUT and rep["fin_des_35_jours"] == "2026-11-10"
    assert rep["exclusions"]["sans_ombre_complete"] == 200
    lec = rep["lecture"]
    assert lec["mode"] == "35_jours" and lec["editions"] == 950 and lec["couverture"] == round(950 / 1150, 4)
    assert lec["decision"] == "DECISION_SUSPENDUE_COUVERTURE"


def test_dead_heat_a_sa_propre_etiquette(tmp_path):
    rep, _ = _read(make_bench(str(tmp_path / "b.db"), 300, dead_heat_every=50), today="2026-10-10")
    assert rep["exclusions"] == {"dead_heat_premiere_place": 6} and rep["editions_eligibles"] == 294
