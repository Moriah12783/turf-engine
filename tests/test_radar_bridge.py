"""Tests du pont RADAR_V4 (4e moteur du banc). Aucun réseau : les lignes du
journal Radar sont injectées ; les instants sont contrôlés."""
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab.database import TurfDatabase
from turf_lab.daily_sync import DailySyncManager
from turf_lab.odds_quality import DEFAULT_ODDS
from turf_lab.radar_bridge import ENGINE_NAME, RadarBridgeClient, RadarV4Engine, bridge_config

RACE_DATE = "2026-09-07"
RACE_ID = "R3C8_07092026_FEURS"
NOW = datetime(2026, 9, 7, 12, 0)


class FakeClient(RadarBridgeClient):
    """Client de pont sans réseau : renvoie les lignes fournies."""

    def __init__(self, rows):
        super().__init__({"url": "https://x", "key": "k", "token": "t"})
        self.rows = rows
        self.calls = 0

    def fetch_radar_journal(self, date_iso):
        self.calls += 1
        return list(self.rows) if date_iso == RACE_DATE else []


def _race():
    return {
        "race_id": RACE_ID, "date": RACE_DATE, "meeting_number": 3, "race_number": 8,
        "name": "Prix Test", "hippodrome": "FEURS", "discipline": "TROT_ATTELE", "distance": 2700,
        "track_type": "SABLE", "track_condition": "BON", "rope": "GAUCHE", "autostart": False,
        "scheduled_start_time": "14:00 GMT (16:00 Paris)", "status": "SCHEDULED",
    }


def _runners(n=10, priced=10, np_nums=()):
    out = []
    for i in range(1, n + 1):
        odds = 3.0 + i if i <= priced else DEFAULT_ODDS
        out.append({
            "num": i, "horse_name": f"Cheval{i}", "sex": "M", "age": 5, "driver_jockey": "J", "trainer": "T",
            "weight": 60.0, "draw": i, "shoeing": "FERRE", "blinkers": "SANS",
            "morning_odds": odds, "odds_t15": odds, "final_odds": odds, "is_non_partant": i in np_nums,
            "press_citation_count": 0, "music": "1a2a3a", "earnings": 1000.0, "record_chrono": 0.0, "official_rating": 0.0,
        })
    return out


def _rows(nums, scelle_a="2026-09-07T07:35:00+00:00", r=3, c=8, edition="MATIN"):
    # p_win décroissante avec le numéro : le n° 1 est le favori Radar
    return [{
        "num_reunion": r, "num_course": c, "num_pmu": n, "nom": f"Cheval{n}",
        "p_win": round(0.30 - 0.02 * (n - 1), 4), "p_top2": 0.4, "p_top3": 0.5,
        "cote_au_scelle": 3.0 + n, "rang_note": n, "edition": edition, "modele": "v4-labels", "scelle_a": scelle_a,
    } for n in nums]


def _mgr(rows=None):
    db = TurfDatabase(os.path.join(tempfile.mkdtemp(), "radar.db"))
    mgr = DailySyncManager(db)
    if rows is not None:
        mgr.radar_engine = RadarV4Engine(client=FakeClient(rows))
    else:
        mgr.radar_engine = RadarV4Engine(client=RadarBridgeClient(None))  # pont désactivé
    return db, mgr


def test_config_absente_desactive_le_pont(monkeypatch=None):
    for v in ("RADAR_SUPABASE_URL", "RADAR_PUBLISHABLE_KEY", "RADAR_BRIDGE_TOKEN"):
        os.environ.pop(v, None)
    assert bridge_config() is None
    eng = RadarV4Engine()  # aucune exception, log RADAR_BRIDGE_DISABLED
    assert eng.enabled is False
    p = eng.predict(_race(), _runners(), as_of_utc=NOW)
    assert p["metadata"]["status"] == "ABSENT" and p["selection"] == []
    db, mgr = _mgr(rows=None)
    db.save_race(_race()); db.save_runners(RACE_ID, _runners())
    assert mgr._lock_radar(_race(), _runners(), "T_MATIN", NOW) == 0
    assert not db.has_prediction(RACE_ID, ENGINE_NAME, "T_MATIN")


def test_appariement_et_non_partant_ignore():
    eng = RadarV4Engine(client=FakeClient(_rows(range(1, 11)) + _rows([1, 2], r=1, c=1)))  # lignes d'une autre course
    runners = _runners(10, np_nums=(10,))
    p = eng.predict(_race(), runners, as_of_utc=NOW)
    assert p["metadata"]["status"] == "OK"
    assert 10 not in p["selection"] and "10" not in p["probabilities"]
    assert p["selection"] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert p["bases"] == [1, 2] and p["outsider_num"] == 8
    assert abs(sum(p["probabilities"].values()) - 1.0) < 1e-3
    assert p["metadata"]["value_indices"]["1"] == round(p["probabilities"]["1"] * 4.0, 2)


def test_couverture_insuffisante_absent():
    eng = RadarV4Engine(client=FakeClient(_rows(range(1, 9))))  # 8 lignes sur 10 partants
    p = eng.predict(_race(), _runners(10), as_of_utc=NOW)
    assert p["metadata"]["status"] == "ABSENT" and p["metadata"]["coverage"] == 0.8
    assert p["selection"] == [] and p["probabilities"] == {}
    # 9/10 = 90 % : accepté (seuil inclusif)
    eng9 = RadarV4Engine(client=FakeClient(_rows(range(1, 10))))
    assert eng9.predict(_race(), _runners(10), as_of_utc=NOW)["metadata"]["status"] == "OK"


def test_garde_anti_fuite_scelle_a_posterieur():
    rows = _rows(range(1, 11), scelle_a="2026-09-07T16:40:00+00:00")  # scellé du soir, après l'instant du verrou
    eng = RadarV4Engine(client=FakeClient(rows))
    p = eng.predict(_race(), _runners(10), as_of_utc=NOW)
    assert p["metadata"]["status"] == "ABSENT" and p["metadata"]["coverage"] == 0.0
    # À une passe ultérieure (après le scellé), la même ligne devient utilisable
    p2 = eng.predict(_race(), _runners(10), as_of_utc=datetime(2026, 9, 7, 17, 0))
    assert p2["metadata"]["status"] == "OK" and p2["metadata"]["edition"] == "MATIN"


def test_lock_radar_une_seule_fois_et_persistance():
    db, mgr = _mgr(rows=_rows(range(1, 11)))
    db.save_race(_race()); db.save_runners(RACE_ID, _runners())
    assert mgr._lock_radar(_race(), _runners(), "T_MATIN", NOW) == 1
    assert mgr._lock_radar(_race(), _runners(), "T_MATIN", NOW) == 0  # jamais réécrit
    preds = [p for p in db.get_predictions(RACE_ID) if p["engine_name"] == ENGINE_NAME]
    assert len(preds) == 1 and preds[0]["horizon"] == "T_MATIN"
    assert preds[0]["odds_real"] == 1 and preds[0]["priced_ratio"] == 1.0 and preds[0]["lock_time_utc"] == NOW.isoformat()
    assert preds[0]["selection"][:2] == [1, 2] and preds[0]["metadata"]["status"] == "OK"
    # Un autre horizon reçoit son propre verrou
    assert mgr._lock_radar(_race(), _runners(), "T90", NOW + timedelta(hours=1)) == 1


def test_lock_radar_porte_de_fraicheur_et_absent():
    db, mgr = _mgr(rows=_rows(range(1, 9)))
    db.save_race(_race())
    # Cotes par défaut : refusé, rien enregistré
    assert mgr._lock_radar(_race(), _runners(10, priced=0), "T_MATIN", NOW) == 0
    assert mgr.gate_refused_radar == 1
    # Cotes réelles mais couverture 8/10 : ABSENT, rien enregistré
    assert mgr._lock_radar(_race(), _runners(10), "T_MATIN", NOW) == 0
    assert mgr.radar_absent == 1
    assert not db.has_prediction(RACE_ID, ENGINE_NAME, "T_MATIN")


def test_cache_une_requete_par_passe():
    client = FakeClient(_rows(range(1, 11)))
    eng = RadarV4Engine(client=client)
    for _ in range(5):
        eng.predict(_race(), _runners(), as_of_utc=NOW)
    # FakeClient court-circuite le cache : on vérifie le cache du vrai client
    real = RadarBridgeClient({"url": "https://x", "key": "k", "token": "t"})
    real._cache[RACE_DATE] = _rows(range(1, 11))
    eng2 = RadarV4Engine(client=real)
    assert eng2.predict(_race(), _runners(), as_of_utc=NOW)["metadata"]["status"] == "OK"
    real.clear_cache()
    assert real._cache == {}


def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for name, fn in tests:
        fn()
        print(f"  [OK] {name}")
    print(f"\n=== {len(tests)} TESTS PONT RADAR_V4 PASSENT ===")


if __name__ == "__main__":
    main()
