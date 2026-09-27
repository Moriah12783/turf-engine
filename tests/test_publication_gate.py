"""Tests du verrou de fraîcheur et de la porte de diffusion (Axe 3).

Exécutable en script (python tests/test_publication_gate.py) ou via pytest.
Tous les instants sont injectés : aucune dépendance à l'heure réelle.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab.database import TurfDatabase
from turf_lab.daily_sync import DailySyncManager
from turf_lab.odds_quality import DEFAULT_ODDS, MIN_LOCK_RATIO, MIN_PRICED_RATIO, neutralize_odds, priced_ratio
from turf_lab.publication_gate import GATE_FINGERPRINT, GATE_VERSION, REASON_LABELS, can_publish, gate_fingerprint

RACE_DATE = "2026-09-07"
RACE_ID = "R1C1_07092026_TESTVILLE"


def _db():
    tmp = tempfile.mkdtemp()
    return TurfDatabase(os.path.join(tmp, "gate.db"))


def _race(start="14:00"):
    return {
        "race_id": RACE_ID, "date": RACE_DATE, "meeting_number": 1, "race_number": 1,
        "name": "Prix Test", "hippodrome": "TESTVILLE", "discipline": "TROT_ATTELE",
        "distance": 2700, "track_type": "SABLE", "track_condition": "BON", "rope": "GAUCHE",
        "autostart": False, "scheduled_start_time": f"{start} GMT (16:00 Paris)", "status": "SCHEDULED",
    }


def _runners(n=10, priced=10):
    """n partants ; les `priced` premiers ont une cote réelle, les autres restent à DEFAULT_ODDS."""
    out = []
    for i in range(1, n + 1):
        real = i <= priced
        odds = 4.0 + i if real else DEFAULT_ODDS
        out.append({
            "num": i, "horse_name": f"Cheval{i}", "sex": "M", "age": 5, "driver_jockey": "J.TEST",
            "trainer": "T.TEST", "weight": 60.0, "draw": i, "shoeing": "FERRE", "blinkers": "SANS",
            "morning_odds": odds, "odds_t15": odds, "final_odds": odds, "is_non_partant": False,
            "press_citation_count": 0, "music": "1a2a3a", "earnings": 50000.0,
            "record_chrono": 0.0, "official_rating": 0.0,
        })
    return out


def _setup(priced=10, n=10, start="14:00"):
    db = _db()
    mgr = DailySyncManager(db)
    race = _race(start)
    runners = _runners(n, priced)
    db.save_race(race)
    db.save_runners(RACE_ID, runners)
    return db, mgr, race, runners


def _lock(mgr, race, runners, horizon="T_MATIN"):
    return mgr._lock_horizon(race, runners, horizon)


NOON = datetime(2026, 9, 7, 12, 0)


def test_priced_ratio():
    assert priced_ratio(_runners(10, 10)) == 1.0
    assert priced_ratio(_runners(10, 8)) == 0.8
    assert priced_ratio(_runners(10, 0)) == 0.0
    assert priced_ratio([]) == 0.0
    # Un non-partant n'entre pas dans le dénominateur
    rs = _runners(10, 9)
    rs[9]["is_non_partant"] = True
    assert priced_ratio(rs) == 1.0


def test_edition_100pct_defaut_refusee():
    """Édition 100 % à 15.0 → verrou refusé, can_publish = (False, 'ODDS_DEFAULT')."""
    db, mgr, race, runners = _setup(priced=0)
    assert _lock(mgr, race, runners) == 0
    assert mgr.gate_refused == 1
    assert db.get_locked_horizons(RACE_ID) == []
    assert can_publish(db, RACE_ID, "T_MATIN", now_utc=NOON, log=False) == (False, "ODDS_DEFAULT")


def test_edition_80pct_verrouillee_mais_non_diffusable():
    """15/09 : entre 50 et 90 % l'édition EXISTE (odds_real = false, ratio réel
    persisté) mais la porte de diffusion la refuse — rien ne part vers les abonnés."""
    db, mgr, race, runners = _setup(priced=8)
    assert _lock(mgr, race, runners) == 1
    assert mgr.gate_refused == 0
    assert db.get_locked_horizons(RACE_ID) == ["T_MATIN"]
    pred = [p for p in db.get_predictions(RACE_ID) if p["engine_name"] == "NEW_VALUE_ENGINE"][0]
    assert pred["odds_real"] == 0 and pred["priced_ratio"] == 0.8 and pred["lock_time_utc"]
    assert can_publish(db, RACE_ID, "T_MATIN", now_utc=NOON, log=False) == (False, "PRICED_RATIO_LOW")


def test_edition_40pct_refusee():
    """Sous le seuil de verrou (50 %) : aucun horizon posé, GATE_REFUSED conservé."""
    db, mgr, race, runners = _setup(priced=4)
    assert _lock(mgr, race, runners) == 0
    assert mgr.gate_refused == 1
    assert db.get_locked_horizons(RACE_ID) == []
    assert can_publish(db, RACE_ID, "T_MATIN", now_utc=NOON, log=False) == (False, "PRICED_RATIO_LOW")


def test_verrou_partiel_calcule_sans_marche():
    """Marché partiel (70 %) : les moteurs reçoivent des cotes neutralisées —
    modèle pur pour NEW_VALUE_ENGINE (aucune calibration sur un mélange vraies
    cotes / sentinelles), marché nominal pour MARKET_BASELINE — tandis que les
    partants d'origine, leurs vraies cotes et les snapshots du verrou sont intacts."""
    db, mgr, race, runners = _setup(priced=7)
    before = [(r["num"], r["morning_odds"], r["odds_t15"], r["final_odds"]) for r in runners]
    assert _lock(mgr, race, runners) == 1
    after = [(r["num"], r["morning_odds"], r["odds_t15"], r["final_odds"]) for r in runners]
    assert after == before                                     # cotes d'origine jamais modifiées
    preds = {p["engine_name"]: p for p in db.get_predictions(RACE_ID)}
    calib = preds["NEW_VALUE_ENGINE"]["metadata"]["market_calibration"]
    assert calib["applied"] is False and calib["coverage_pct"] == 0.0
    assert preds["NEW_VALUE_ENGINE"]["odds_real"] == 0 and preds["MARKET_BASELINE"]["odds_real"] == 0
    # Le marché nominal ne « sait » rien : probabilités uniformes
    probs = list(preds["MARKET_BASELINE"]["probabilities"].values())
    assert max(probs) - min(probs) < 1e-6
    # Les vraies cotes partielles restent archivées dans les snapshots du verrou
    snaps = db.get_odds_snapshots(RACE_ID)
    assert snaps and any(h.get("T_MATIN") not in (None, DEFAULT_ODDS) for h in snaps.values())
    assert can_publish(db, RACE_ID, "T_MATIN", now_utc=NOON, log=False) == (False, "PRICED_RATIO_LOW")


def test_verrou_complet_calibre_sur_le_marche():
    """≥ 90 % : comportement inchangé — calibration marché appliquée, odds_real = true."""
    db, mgr, race, runners = _setup(priced=10)
    assert _lock(mgr, race, runners) == 1
    pred = [p for p in db.get_predictions(RACE_ID) if p["engine_name"] == "NEW_VALUE_ENGINE"][0]
    assert pred["metadata"]["market_calibration"]["applied"] is True
    assert pred["odds_real"] == 1


def test_edition_90pct_acceptee():
    """Exactement 90 % cotée → verrou posé (seuil inclusif)."""
    db, mgr, race, runners = _setup(priced=9)
    assert _lock(mgr, race, runners) == 1
    assert can_publish(db, RACE_ID, "T_MATIN", now_utc=NOON, log=False) == (True, "OK")


def test_edition_reelle_verrouillee_et_publiable():
    """Édition réelle après 06h30 → verrouillée, odds_real = true, publication acceptée."""
    db, mgr, race, runners = _setup(priced=10)
    assert _lock(mgr, race, runners) == 1
    assert db.get_locked_horizons(RACE_ID) == ["T_MATIN"]
    pred = [p for p in db.get_predictions(RACE_ID) if p["engine_name"] == "NEW_VALUE_ENGINE"][0]
    assert pred["odds_real"] == 1
    assert pred["priced_ratio"] == 1.0
    assert pred["lock_time_utc"]
    assert can_publish(db, RACE_ID, "T_MATIN", now_utc=NOON, log=False) == (True, "OK")
    # Horizon non verrouillé → refus explicite
    assert can_publish(db, RACE_ID, "T15", now_utc=NOON, log=False) == (False, "NOT_LOCKED")
    # Second verrou du même horizon : jamais réécrit
    assert _lock(mgr, race, runners) == 0


def test_avant_0630_refusee_puis_acceptee():
    """Édition réelle à 06h10 → refusée (BEFORE_0630) ; acceptée à la passe suivante."""
    db, mgr, race, runners = _setup(priced=10)
    t0610 = datetime(2026, 9, 7, 6, 10)
    t0631 = datetime(2026, 9, 7, 6, 31)
    # Plancher horaire : T_MATIN n'est pas dû à 06h10 (aucun verrou posé)
    assert "T_MATIN" not in mgr.due_horizons(race["scheduled_start_time"], RACE_DATE, t0610)
    assert can_publish(db, RACE_ID, "T_MATIN", now_utc=t0610, log=False) == (False, "BEFORE_0630")
    # Passe suivante (≥ 06h30) : dû, verrouillé, diffusable
    assert "T_MATIN" in mgr.due_horizons(race["scheduled_start_time"], RACE_DATE, t0631)
    assert _lock(mgr, race, runners) == 1
    assert can_publish(db, RACE_ID, "T_MATIN", now_utc=t0631, log=False) == (True, "OK")


def test_verrou_avant_0630_persiste_refuse():
    """Un verrou dont lock_time_utc est antérieur à 06h30 reste refusé même plus tard."""
    db, mgr, race, runners = _setup(priced=10)
    assert _lock(mgr, race, runners) == 1
    with db.transaction() as conn:
        conn.execute("UPDATE predictions SET lock_time_utc = ? WHERE race_id = ?",
                     ("2026-09-07T06:10:00", RACE_ID))
    assert can_publish(db, RACE_ID, "T_MATIN", now_utc=NOON, log=False) == (False, "BEFORE_0630")


def test_course_partie_refusee():
    """Course partie → refusée (RACE_STARTED), aucun verrou rétroactif."""
    db, mgr, race, runners = _setup(priced=10, start="11:00")
    after_start = datetime(2026, 9, 7, 11, 1)
    assert mgr.due_horizons(race["scheduled_start_time"], RACE_DATE, after_start) == []
    assert can_publish(db, RACE_ID, "T_MATIN", now_utc=after_start, log=False) == (False, "RACE_STARTED")
    # Même verrouillée avant le départ, une course partie n'est plus diffusable
    assert _lock(mgr, race, runners) == 1
    assert can_publish(db, RACE_ID, "T_MATIN", now_utc=after_start, log=False) == (False, "RACE_STARTED")
    # Course terminée : idem
    db.save_results(RACE_ID, [1, 2, 3, 4, 5])
    assert can_publish(db, RACE_ID, "T_MATIN", now_utc=NOON, log=False) == (False, "RACE_STARTED")


def test_flag_persiste_prime_sur_etat_courant():
    """odds_real = false persisté au verrou → refus, même si les cotes sont réelles maintenant."""
    db, mgr, race, runners = _setup(priced=10)
    assert _lock(mgr, race, runners) == 1
    with db.transaction() as conn:
        conn.execute("UPDATE predictions SET odds_real = 0, priced_ratio = 0.0 WHERE race_id = ?", (RACE_ID,))
    assert can_publish(db, RACE_ID, "T_MATIN", now_utc=NOON, log=False) == (False, "ODDS_DEFAULT")


def test_course_inconnue():
    db = _db()
    assert can_publish(db, "R9C9_01012000_NULLEPART", "T_MATIN", now_utc=NOON, log=False) == (False, "RACE_UNKNOWN")


def test_verrou_refuse_puis_pose_a_la_passe_suivante():
    """Cotes ouvertes entre deux passes : refus, puis verrou (le plancher 06h30 reste en place)."""
    db, mgr, race, runners = _setup(priced=0)
    assert _lock(mgr, race, runners) == 0
    assert db.get_locked_horizons(RACE_ID) == []
    runners = _runners(10, 10)
    db.save_runners(RACE_ID, runners)
    assert _lock(mgr, race, runners) == 1
    assert can_publish(db, RACE_ID, "T_MATIN", now_utc=NOON, log=False) == (True, "OK")


# ── Table publication_decisions (28/09/2026) ─────────────────────────────

def _decisions(db):
    return db.get_publication_decisions(RACE_ID)


def test_decision_enregistree_au_verrou_diffusable():
    """Verrou à 100 % : une ligne, décision OK, version de la porte, instant = lock_time_utc."""
    db, mgr, race, runners = _setup(priced=10)
    assert mgr._lock_horizon(race, runners, "T_MATIN", now_utc=NOON) == 1
    rows = _decisions(db)
    assert len(rows) == 1
    row = rows[0]
    assert (row["horizon"], row["publishable"], row["reason"]) == ("T_MATIN", 1, "OK")
    assert row["gate_version"] == GATE_VERSION
    pred = [p for p in db.get_predictions(RACE_ID) if p["engine_name"] == "NEW_VALUE_ENGINE"][0]
    assert row["decided_at_utc"] == pred["lock_time_utc"] == NOON.isoformat()


def test_decision_marche_partiel_non_diffusable():
    """Verrou à 80 % : la ligne dit PRICED_RATIO_LOW, avec le vocabulaire de can_publish."""
    db, mgr, race, runners = _setup(priced=8)
    assert mgr._lock_horizon(race, runners, "T_MATIN", now_utc=NOON) == 1
    rows = _decisions(db)
    assert [(r["publishable"], r["reason"]) for r in rows] == [(0, "PRICED_RATIO_LOW")]
    assert rows[0]["reason"] in REASON_LABELS


def test_pas_de_decision_sans_verrou():
    """Verrou refusé (40 %) : aucun horizon posé, donc aucune ligne."""
    db, mgr, race, runners = _setup(priced=4)
    assert mgr._lock_horizon(race, runners, "T_MATIN", now_utc=NOON) == 0
    assert _decisions(db) == []


def test_une_ligne_par_course_et_par_horizon():
    """Une passe suivante ne réécrit rien ; chaque nouvel horizon ajoute sa ligne."""
    db, mgr, race, runners = _setup(priced=10)
    assert mgr._lock_horizon(race, runners, "T_MATIN", now_utc=NOON) == 1
    assert mgr._lock_horizon(race, runners, "T_MATIN", now_utc=NOON + timedelta(minutes=15)) == 0
    assert mgr._lock_horizon(race, runners, "T90", now_utc=NOON + timedelta(minutes=30)) == 1
    rows = _decisions(db)
    assert [(r["horizon"], r["decided_at_utc"]) for r in rows] == [
        ("T_MATIN", NOON.isoformat()), ("T90", (NOON + timedelta(minutes=30)).isoformat())]
    # Doublon direct refusé sans erreur (INSERT OR IGNORE) : la première décision est définitive
    assert db.save_publication_decision(RACE_ID, "T_MATIN", False, "STALE_ODDS", "x", GATE_VERSION) is False
    assert len(_decisions(db)) == 2


def test_table_en_ajout_seul():
    """UPDATE et DELETE sont refusés par la base elle-même."""
    import sqlite3
    db, mgr, race, runners = _setup(priced=10)
    mgr._lock_horizon(race, runners, "T_MATIN", now_utc=NOON)
    for sql in ("UPDATE publication_decisions SET publishable = 0",
                "DELETE FROM publication_decisions"):
        try:
            with db.transaction() as conn:
                conn.execute(sql)
            raise AssertionError("modification acceptée : " + sql)
        except sqlite3.DatabaseError as exc:
            assert "ajout seul" in str(exc)
    assert len(_decisions(db)) == 1


def test_erreur_d_enregistrement_n_annule_pas_le_verrou():
    """Si l'écriture de la décision échoue, le verrou reste posé et l'erreur est journalisée."""
    import contextlib
    import io
    db, mgr, race, runners = _setup(priced=10)

    def boom(*args, **kwargs):
        raise RuntimeError("disque plein")

    db.save_publication_decision = boom
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert mgr._lock_horizon(race, runners, "T_MATIN", now_utc=NOON) == 1
    assert db.get_locked_horizons(RACE_ID) == ["T_MATIN"]
    assert "PUBLICATION_DECISION_ERROR" in buf.getvalue()


def test_empreinte_de_la_porte():
    """Toute modification des règles de la porte doit changer GATE_VERSION."""
    fp = gate_fingerprint()
    assert fp == GATE_FINGERPRINT, (
        "Règles de la porte modifiées (empreinte " + fp + ") : incrémenter GATE_VERSION, "
        "remplacer GATE_FINGERPRINT par cette empreinte et annoncer le changement par ligne datée "
        "AVANT publication (engagement jusqu'au 20/10/2026).")


def _f_a(x):
    """Docstring A."""
    # commentaire
    return x + 1


def _f_b(x):
    """Autre docstring, plus longue."""

    return x + 1  # autre commentaire


def test_empreinte_ignore_commentaires_mais_pas_les_seuils():
    from turf_lab import odds_quality
    from turf_lab.publication_gate import _normalized_source
    assert _normalized_source(_f_a).replace("_f_a", "_f_b") == _normalized_source(_f_b)
    before = gate_fingerprint()
    saved = odds_quality.MIN_PRICED_RATIO
    try:
        odds_quality.MIN_PRICED_RATIO = 0.85
        assert gate_fingerprint() != before
    finally:
        odds_quality.MIN_PRICED_RATIO = saved
    assert gate_fingerprint() == before


def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for name, fn in tests:
        fn()
        print(f"  [OK] {name}")
    print(f"\n=== {len(tests)} TESTS PORTE DE DIFFUSION / VERROU DE FRAÎCHEUR PASSENT ===")


if __name__ == "__main__":
    main()
