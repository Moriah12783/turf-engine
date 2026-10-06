"""Ombre du fondamental dans NVE (règle pré-enregistrée docs/OMBRE_FONDAMENTAL.md).

Vérifie que l'ombre n'influence AUCUN champ publié, qu'elle n'existe que
complète (tous les partants, une seule version, marché réel), qu'elle suit la
recette A et le départage de la production, et que ses constantes sont celles
de la règle scellée.
"""
import copy
import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab import engine as engine_mod
from turf_lab import fondamental_verrou as fv
from turf_lab import ombre, ombre_lecture
from turf_lab.database import TurfDatabase
from turf_lab.engine import NVE_VERSION, OMBRE_META, OMBRE_RECETTE, NewValueEngine

RACE_ID = "R1C1_07102026_TESTVILLE"
RACE = {"race_id": RACE_ID, "date": "2026-10-07", "meeting_number": 1, "race_number": 1, "name": "Prix Test",
        "hippodrome": "TESTVILLE", "discipline": "TROT_ATTELE", "distance": 2700, "track_type": "SABLE",
        "track_condition": "BON", "rope": "GAUCHE", "autostart": False,
        "scheduled_start_time": "14:00 GMT (16:00 Paris)", "status": "SCHEDULED"}


def _runners(n=10, default_odds_from=None):
    out = []
    for i in range(1, n + 1):
        odds = 15.0 if default_odds_from and i >= default_odds_from else 2.0 + 1.5 * i
        out.append({"num": i, "horse_name": f"Cheval{i}", "sex": "M", "age": 5, "driver_jockey": f"J.D{i}",
                    "trainer": f"T.E{i}", "weight": 60.0, "draw": i, "shoeing": "FERRE", "blinkers": "SANS",
                    "morning_odds": odds, "odds_t15": odds, "final_odds": odds, "is_non_partant": False,
                    "press_citation_count": 0, "music": f"{1 + i % 5}a{2 + i % 4}a3a", "earnings": 50000.0 + i,
                    "record_chrono": 0.0, "official_rating": 0.0})
    return out


def _with_fundamental(runners, version="fond-matin-test", train_until="2026-10-06", skip=()):
    # Fondamental volontairement différent du marché : il préfère les numéros élevés.
    weights = {r["num"]: float(r["num"]) for r in runners}
    total = sum(weights.values())
    for r in runners:
        if r["num"] in skip:
            continue
        r[fv.CLE_P] = weights[r["num"]] / total
        r[fv.CLE_MODEL_VERSION] = version
        r[fv.CLE_TRAIN_UNTIL] = train_until
    return runners


def _published(pred):
    """Tous les champs publiés, métadonnées comprises, sauf l'archive de l'ombre."""
    p = copy.deepcopy(pred)
    p["metadata"].pop(OMBRE_META, None)
    return p


def test_constantes_identiques_a_la_regle():
    assert NewValueEngine.MARKET_WEIGHT == ombre.POIDS_MARCHE
    assert OMBRE_RECETTE == ombre.RECETTE
    assert OMBRE_META == ombre_lecture.META_OMBRE
    assert NVE_VERSION == engine_mod.NVE_VERSION and NVE_VERSION.startswith("nve-")
    # Contrat avec la ligne du verrou (dev daily_sync) : mêmes noms de clés.
    assert (engine_mod.CLE_P, engine_mod.CLE_MODEL_VERSION, engine_mod.CLE_TRAIN_UNTIL) == fv.CLES


def test_sans_fondamental_rien_ne_change():
    eng = NewValueEngine()
    base = eng.predict(RACE, _runners())
    assert OMBRE_META not in base["metadata"]
    again = eng.predict(RACE, _runners())
    assert base == again


def test_ombre_ninfluence_aucun_champ_publie():
    eng = NewValueEngine()
    base = eng.predict(RACE, _runners())
    avec = eng.predict(RACE, _with_fundamental(_runners()))
    assert _published(avec) == base
    sh = avec["metadata"][OMBRE_META]
    assert set(sh) == {"recette", "model_version", "nve_version", "train_until", "probabilities", "selection",
                       "fondamental"}
    assert sh["recette"] == OMBRE_RECETTE and sh["nve_version"] == NVE_VERSION
    assert sh["model_version"] == "fond-matin-test" and sh["train_until"] == "2026-10-06"
    assert set(sh["probabilities"]) == set(avec["probabilities"])
    assert abs(sum(sh["probabilities"].values()) - 1.0) < 2e-3
    assert abs(sum(sh["fondamental"].values()) - 1.0) < 1e-5
    assert len(sh["selection"]) == 10 and set(sh["selection"]) <= {int(k) for k in sh["probabilities"]}


def test_recette_a_et_departage_de_la_production():
    eng = NewValueEngine()
    runners = _with_fundamental(_runners())
    pred = eng.predict(RACE, runners)
    sh = pred["metadata"][OMBRE_META]
    implied = {r["num"]: 1.0 / max(1.05, r["odds_t15"]) for r in runners}
    tot = sum(implied.values())
    for r in runners:
        attendu = round(0.90 * implied[r["num"]] / tot + 0.10 * sh["fondamental"][str(r["num"])], 4)
        assert abs(sh["probabilities"][str(r["num"])] - attendu) <= 1e-4
    # Même clé de classement que la production : probabilité, puis indice de value.
    odds = {r["num"]: r["odds_t15"] for r in runners}
    rows = [(sh["probabilities"][str(n)], round(sh["probabilities"][str(n)] * odds[n], 2), n) for n in odds]
    attendu_sel = [n for _, _, n in sorted(rows, key=lambda x: (x[0], x[1]), reverse=True)][:10]
    assert sh["selection"] == attendu_sel
    # Le fondamental préfère les numéros élevés : l'ombre doit s'écarter de la production.
    assert sh["probabilities"] != pred["probabilities"]


def test_jamais_dombre_partielle():
    eng = NewValueEngine()
    assert OMBRE_META not in eng.predict(RACE, _with_fundamental(_runners(), skip=(4,)))["metadata"]
    mixed = _with_fundamental(_runners())
    mixed[2][fv.CLE_MODEL_VERSION] = "fond-matin-autre"
    assert OMBRE_META not in eng.predict(RACE, mixed)["metadata"]
    zero = _with_fundamental(_runners())
    zero[0][fv.CLE_P] = 0.0
    assert OMBRE_META not in eng.predict(RACE, zero)["metadata"]


def test_pas_dombre_sans_marche_reel():
    eng = NewValueEngine()
    sans_marche = _with_fundamental(_runners(default_odds_from=1))       # toutes les cotes à 15.0
    pred = eng.predict(RACE, sans_marche)
    assert pred["metadata"]["market_calibration"]["applied"] is False
    assert OMBRE_META not in pred["metadata"]


def test_non_partant_tardif_renormalise():
    eng = NewValueEngine()
    runners = _with_fundamental(_runners())
    runners[9]["is_non_partant"] = True                                  # le n° 10 est retiré au verrou
    sh = eng.predict(RACE, runners)["metadata"][OMBRE_META]
    assert "10" not in sh["fondamental"] and "10" not in sh["probabilities"]
    assert abs(sum(sh["fondamental"].values()) - 1.0) < 1e-5


def test_petit_peloton_selection_de_la_taille_du_champ():
    eng = NewValueEngine()
    sh = eng.predict(RACE, _with_fundamental(_runners(n=6)))["metadata"][OMBRE_META]
    assert len(sh["selection"]) == 6                                     # < 8 partants : pas de 8 possible


def test_contrat_avec_le_lecteur_scelle():
    """La sortie du moteur, relue comme la relit ombre_lecture.load, est
    acceptée telle quelle par shadow_of (couture entre les deux sessions),
    petits champs compris (moins de 8 partants : tous les partants)."""
    eng = NewValueEngine()
    for n in (10, 8, 7, 6):
        pred = eng.predict(RACE, _with_fundamental(_runners(n=n)))
        ed = {"probs": {int(k): float(v) for k, v in pred["probabilities"].items()},
              "meta": json.loads(json.dumps(pred["metadata"])), "selection": pred["selection"]}
        sh = ombre_lecture.shadow_of(ed)
        assert sh is not None and sh["cle"] == ("fond-matin-test", NVE_VERSION, OMBRE_RECETTE)
        assert len(sh["selection"]) == min(10, n)


def test_chaine_table_du_labo_ligne_du_verrou_moteur():
    """Bout en bout : table du labo (fondamental_nuit) -> ligne du verrou
    (fondamental_verrou, dev daily_sync) -> ombre du moteur ; les partants
    d'origine restent intacts et les champs publiés ne changent pas."""
    from turf_lab.fondamental_nuit import TABLE_SQL                     # contrat de la table du labo
    path = os.path.join(tempfile.mkdtemp(), "ombre.db")
    db = TurfDatabase(path)
    conn = sqlite3.connect(path)
    conn.execute(TABLE_SQL)
    conn.executemany("INSERT INTO fundamental_probs VALUES (?, ?, ?, ?, ?, ?)",
                     [(RACE_ID, n, n / 55.0, "fond-matin-v1", "2026-10-07T05:06:00Z", "2026-10-06") for n in range(1, 11)])
    conn.commit()
    conn.close()
    runners = _runners()
    portes, info = fv.porter_fondamental(db, RACE_ID, runners)
    assert info["statut"] == "PORTE" and info["portes"] == 10
    assert not any(fv.CLE_P in r for r in runners)                        # copie, jamais l'original
    eng = NewValueEngine()
    avec = eng.predict(RACE, portes)
    sh = avec["metadata"][OMBRE_META]
    assert sh["model_version"] == "fond-matin-v1" and sh["train_until"] == "2026-10-06"
    assert _published(avec) == eng.predict(RACE, runners)
    assert ombre_lecture.shadow_of({"probs": {int(k): v for k, v in avec["probabilities"].items()},
                                    "meta": avec["metadata"], "selection": avec["selection"]}) is not None
