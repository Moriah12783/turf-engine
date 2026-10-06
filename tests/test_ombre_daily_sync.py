"""Tests des deux changements daily_sync de la répétition de l'ombre (28/09/2026) :

1. la ligne du verrou T_MATIN qui fait porter aux partants leur probabilité
   fondamentale (turf_lab/fondamental_verrou.py, docs/OMBRE_FONDAMENTAL.md) ;
2. le correctif des valeurs mixtes du déferrage, avec la même règle que le
   labo (turf_lab/deferre_lab.code).

Exécutable en script (python tests/test_ombre_daily_sync.py) ou via pytest.
Tous les instants sont injectés : aucune dépendance à l'heure réelle.
"""
import contextlib
import io
import os
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab import fondamental_verrou as fv
from turf_lab.daily_sync import DailySyncManager, is_mixed_shoeing, shoeing_code
from turf_lab.database import TurfDatabase
from turf_lab.odds_quality import DEFAULT_ODDS

RACE_DATE = "2026-10-01"
RACE_ID = "R1C1_01102026_TESTVILLE"
NOON = datetime(2026, 10, 1, 12, 0)
VERSION = "fond-abc123"
TRAIN_UNTIL = "2026-09-30"


def _race():
    return {
        "race_id": RACE_ID, "date": RACE_DATE, "meeting_number": 1, "race_number": 1,
        "name": "Prix Test", "hippodrome": "TESTVILLE", "discipline": "TROT_ATTELE",
        "distance": 2700, "track_type": "SABLE", "track_condition": "BON", "rope": "GAUCHE",
        "autostart": False, "scheduled_start_time": "14:00 GMT (16:00 Paris)", "status": "SCHEDULED",
    }


def _runners(n=10, priced=10):
    out = []
    for i in range(1, n + 1):
        odds = 4.0 + i if i <= priced else DEFAULT_ODDS
        out.append({
            "num": i, "horse_name": f"Cheval{i}", "sex": "M", "age": 5, "driver_jockey": "J.TEST",
            "trainer": "T.TEST", "weight": 60.0, "draw": i, "shoeing": "FERRE", "blinkers": "SANS",
            "morning_odds": odds, "odds_t15": odds, "final_odds": odds, "is_non_partant": False,
            "press_citation_count": 0, "music": "1a2a3a", "earnings": 50000.0,
            "record_chrono": 0.0, "official_rating": 0.0,
        })
    return out


def _probs(n=10):
    return {i: round(1.0 / n, 6) for i in range(1, n + 1)}


def _setup(priced=10, n=10, rows=None, table=True):
    """Base neuve + gestionnaire dont le moteur NVE est espionné (partants reçus par horizon)."""
    db = TurfDatabase(os.path.join(tempfile.mkdtemp(), "ombre.db"))
    if table:
        conn = db.get_connection()
        conn.execute("CREATE TABLE fundamental_probs (race_id TEXT, num INTEGER, p REAL, model_version TEXT, "
                     "computed_at TEXT, train_until TEXT, PRIMARY KEY (race_id, num, model_version))")
        for row in (rows if rows is not None else
                    [(RACE_ID, k, v, VERSION, "2026-10-01T05:30:00Z", TRAIN_UNTIL) for k, v in _probs(n).items()]):
            conn.execute("INSERT INTO fundamental_probs VALUES (?, ?, ?, ?, ?, ?)", row)
        conn.commit()
        conn.close()
    mgr = DailySyncManager(db)
    seen = {}
    original = mgr.new_engine.predict

    def spy(race, runners):
        seen[len(seen)] = [dict(r) for r in runners]
        return original(race, runners)

    mgr.new_engine.predict = spy
    race = _race()
    runners = _runners(n, priced)
    db.save_race(race)
    db.save_runners(RACE_ID, runners)
    return db, mgr, race, runners, seen


def _porte(runner):
    return any(k in runner for k in fv.CLES)


# ── 1. Ligne du verrou T_MATIN ───────────────────────────────────────────
def test_table_absente_rien_ne_change():
    db, mgr, race, runners, seen = _setup(table=False)
    same, info = fv.porter_fondamental(db, RACE_ID, runners)
    assert same is runners and info["statut"] == "TABLE_ABSENTE"
    assert mgr._lock_horizon(race, runners, "T_MATIN", now_utc=NOON) == 1
    assert not any(_porte(r) for r in seen[0])


def test_probabilites_portees_au_matin():
    db, mgr, race, runners, seen = _setup()
    assert mgr._lock_horizon(race, runners, "T_MATIN", now_utc=NOON) == 1
    recus = seen[0]
    assert all(r[fv.CLE_P] == _probs()[r["num"]] for r in recus)
    assert all(r[fv.CLE_MODEL_VERSION] == VERSION and r[fv.CLE_TRAIN_UNTIL] == TRAIN_UNTIL for r in recus)
    assert not any(_porte(r) for r in runners)          # partants d'origine intacts


def test_jamais_hors_du_matin_meme_passe():
    """T_MATIN puis T90 dans la même passe, sur la même liste : T90 ne porte rien."""
    db, mgr, race, runners, seen = _setup()
    assert mgr._lock_horizon(race, runners, "T_MATIN", now_utc=NOON) == 1
    assert mgr._lock_horizon(race, runners, "T90", now_utc=NOON) == 1
    assert all(_porte(r) for r in seen[0])
    assert not any(_porte(r) for r in seen[1])
    assert not any(_porte(r) for r in runners)
    for h in ("T30", "T15"):
        seen.clear()
        assert mgr._lock_horizon(race, runners, h, now_utc=NOON) == 1
        assert not any(_porte(r) for r in seen[0])


def test_aucune_ligne_pour_la_course():
    rows = [("R9C9_01102026_AILLEURS", k, v, VERSION, "x", TRAIN_UNTIL) for k, v in _probs().items()]
    db, mgr, race, runners, seen = _setup(rows=rows)
    same, info = fv.porter_fondamental(db, RACE_ID, runners)
    assert same is runners and info["statut"] == "AUCUNE_LIGNE"


def test_plusieurs_versions_rien():
    rows = ([(RACE_ID, k, v, VERSION, "x", TRAIN_UNTIL) for k, v in _probs().items()]
            + [(RACE_ID, k, v, "fond-autre", "x", TRAIN_UNTIL) for k, v in _probs().items()])
    db, mgr, race, runners, seen = _setup(rows=rows)
    same, info = fv.porter_fondamental(db, RACE_ID, runners)
    assert same is runners and info["statut"] == "VERSIONS_MULTIPLES"
    assert info["versions"] == sorted([VERSION, "fond-autre"])
    assert mgr._lock_horizon(race, runners, "T_MATIN", now_utc=NOON) == 1
    assert not any(_porte(r) for r in seen[0])


def test_plusieurs_train_until_rien():
    rows = [(RACE_ID, k, v, VERSION, "x", TRAIN_UNTIL if k < 5 else "2026-09-29") for k, v in _probs().items()]
    db, mgr, race, runners, seen = _setup(rows=rows)
    same, info = fv.porter_fondamental(db, RACE_ID, runners)
    assert same is runners and info["statut"] == "TRAIN_UNTIL_MULTIPLES"


def test_partant_sans_p_laisse_au_moteur():
    """Un partant absent de la table ne porte rien ; le moteur décide (jamais d'ombre partielle)."""
    rows = [(RACE_ID, k, v, VERSION, "x", TRAIN_UNTIL) for k, v in _probs().items() if k != 7]
    db, mgr, race, runners, seen = _setup(rows=rows)
    out, info = fv.porter_fondamental(db, RACE_ID, runners)
    assert info["statut"] == "PORTE" and info["portes"] == 9 and info["sans_p"] == 1
    assert not _porte(next(r for r in out if r["num"] == 7))


def test_marche_partiel_porte_quand_meme():
    """Marché partiel : copie neutralisée ET probabilités portées ; le moteur n'en fait pas d'ombre."""
    db, mgr, race, runners, seen = _setup(priced=6)
    assert mgr._lock_horizon(race, runners, "T_MATIN", now_utc=NOON) == 1
    assert all(_porte(r) and r["odds_is_real"] is False for r in seen[0])
    assert runners[0]["morning_odds"] == 5.0              # cotes réelles d'origine intactes


def test_erreur_de_lecture_n_empeche_pas_le_verrou():
    """Table illisible (schéma inattendu) : aucune probabilité, erreur journalisée, verrou posé."""
    db, mgr, race, runners, seen = _setup(table=False)
    conn = db.get_connection()
    conn.execute("CREATE TABLE fundamental_probs (autre_chose TEXT)")
    conn.commit()
    conn.close()
    out, info = fv.porter_fondamental(db, RACE_ID, runners)
    assert out is runners and info["statut"] == "ERREUR"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert mgr._lock_horizon(race, runners, "T_MATIN", now_utc=NOON) == 1
    assert '"statut": "ERREUR"' in buf.getvalue()
    assert not any(_porte(r) for r in seen[0])


def test_journal_sans_aucune_probabilite():
    rows = [(RACE_ID, k, 0.1 + k / 1000.0, VERSION, "x", TRAIN_UNTIL) for k in range(1, 11)]
    db, mgr, race, runners, seen = _setup(rows=rows)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert mgr._lock_horizon(race, runners, "T_MATIN", now_utc=NOON) == 1
    log = [line for line in buf.getvalue().splitlines() if line.startswith("FONDAMENTAL_VERROU ")]
    assert len(log) == 1 and '"statut": "PORTE"' in log[0] and '"portes": 10' in log[0]
    assert not any(str(0.1 + k / 1000.0) in log[0] for k in range(1, 11))
    assert fv.CLE_P not in log[0]


def test_journal_uniquement_au_matin():
    db, mgr, race, runners, seen = _setup()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mgr._lock_horizon(race, runners, "T90", now_utc=NOON)
    assert "FONDAMENTAL_VERROU" not in buf.getvalue()


# ── 2. Déferrage : même règle que le labo ────────────────────────────────
VALEURS_FLUX = [
    None, "", "FERRE", "SANS", "DEFERRE_ANTERIEURS_POSTERIEURS", "DEFERRE_ANTERIEURS", "DEFERRE_POSTERIEURS",
    "PROTEGE_ANTERIEURS_DEFERRRE_POSTERIEURS", "PROTEGE_ANTERIEURS_DEFERRE_POSTERIEURS",
    "DEFERRE_ANTERIEURS_PROTEGE_POSTERIEURS", "PROTEGE_ANTERIEURS", "PROTEGE_POSTERIEURS",
    "PROTEGE_ANTERIEURS_POSTERIEURS", "deferre_anterieurs_posterieurs", "INCONNU",
]


def test_deferrage_meme_regle_que_le_labo():
    from turf_lab.deferre_lab import code
    for raw in VALEURS_FLUX:
        attendu = code(raw)
        attendu = "FERRE" if attendu == "PROTEGE" else attendu    # protection seule : inchangée en production
        assert shoeing_code(raw) == attendu, (raw, shoeing_code(raw), attendu)


def test_valeurs_mixtes_du_message():
    assert shoeing_code("PROTEGE_ANTERIEURS_DEFERRRE_POSTERIEURS") == "DP"
    assert shoeing_code("DEFERRE_ANTERIEURS_PROTEGE_POSTERIEURS") == "DA"
    assert shoeing_code("DEFERRE_ANTERIEURS_POSTERIEURS") == "D4"


def test_valeurs_non_mixtes_inchangees():
    """Seules les valeurs mixtes changent par rapport au code d'avant le 07/10."""
    def ancien(raw):
        return {"DEFERRE_ANTERIEURS_POSTERIEURS": "D4", "DEFERRE_POSTERIEURS": "DP",
                "DEFERRE_ANTERIEURS": "DA"}.get(raw, "FERRE")
    for raw in VALEURS_FLUX:
        if raw and raw == str(raw).upper() and not is_mixed_shoeing(raw):
            assert shoeing_code(raw) == ancien(raw), raw


def test_detection_des_valeurs_mixtes():
    assert is_mixed_shoeing("PROTEGE_ANTERIEURS_DEFERRRE_POSTERIEURS")
    assert is_mixed_shoeing("DEFERRE_ANTERIEURS_PROTEGE_POSTERIEURS")
    assert not is_mixed_shoeing("DEFERRE_ANTERIEURS_POSTERIEURS")
    assert not is_mixed_shoeing("PROTEGE_ANTERIEURS")
    assert not is_mixed_shoeing(None)


def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for name, fn in tests:
        with contextlib.redirect_stdout(io.StringIO()):
            fn()
        print(f"  [OK] {name}")
    print(f"\n=== {len(tests)} TESTS OMBRE DAILY_SYNC (FONDAMENTAL T_MATIN + DEFERRAGE) PASSENT ===")


if __name__ == "__main__":
    main()
