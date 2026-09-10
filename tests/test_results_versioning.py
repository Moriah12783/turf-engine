"""Tests du contrôle d'arrivée définitive et du suivi des corrections
(points 2 et 3 du correctif partenaire), en base et à travers sync_date.

Exécutable en script (python tests/test_results_versioning.py) ou via pytest.
"""
import calendar
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab.database import TurfDatabase
from turf_lab.daily_sync import DailySyncManager
from turf_lab.results_reader import ArrivalReading, STATUT_ANNULEE, STATUT_DEFINITIVE, STATUT_PROVISOIRE, ranking_from_groups

NOW = datetime.utcnow()
DATE_API = NOW.strftime("%d%m%Y")
RID = f"R1C1_{DATE_API}_TESTVILLE"


def _db():
    return TurfDatabase(os.path.join(tempfile.mkdtemp(), "res.db"))


def _reading(groups, statut, incidents=None, pmu_statut=None, source="PMU_PROGRAMME"):
    r = ArrivalReading(statut=statut, definitive_flag=(statut == STATUT_DEFINITIVE), source=source,
                       pmu_statut=pmu_statut or ("ARRIVEE_DEFINITIVE_COMPLETE" if statut == STATUT_DEFINITIVE else "ARRIVEE_PROVISOIRE"))
    r.ranking = ranking_from_groups(groups)
    r.incidents = incidents or []
    return r


def _seed_race(db):
    db.save_race({"race_id": RID, "date": NOW.strftime("%Y-%m-%d"), "meeting_number": 1, "race_number": 1,
                  "name": "Prix Test", "hippodrome": "TESTVILLE", "discipline": "TROT_ATTELE", "distance": 2700,
                  "track_type": "SABLE", "track_condition": "BON", "rope": "GAUCHE", "autostart": False,
                  "scheduled_start_time": "14:00 GMT (16:00 Paris)", "status": "SCHEDULED"})
    db.save_runners(RID, [{"num": n, "horse_name": f"Cheval{n}", "sex": "M", "age": 5, "driver_jockey": "J", "trainer": "T",
                           "weight": 60.0, "draw": n, "shoeing": "FERRE", "blinkers": "SANS", "morning_odds": 5.0 + n,
                           "odds_t15": 5.0 + n, "final_odds": 5.0 + n, "is_non_partant": False, "press_citation_count": 0,
                           "music": "1a", "earnings": 1000.0, "record_chrono": 0.0, "official_rating": 0.0} for n in range(1, 9)])


# ─────────────────────────────────────────────────────────────────────
# Base : record_result
# ─────────────────────────────────────────────────────────────────────

def test_provisoire_puis_definitive_puis_correction():
    db = _db(); _seed_race(db)
    t0 = datetime(2026, 9, 9, 13, 20)
    # 1. Arrivée provisoire (top 3)
    res = db.record_result(RID, _reading([[4], [2], [7]], STATUT_PROVISOIRE), source_url="u1", now_utc=t0)
    assert res == {"action": "INITIAL", "version": 1, "reason": "INITIAL", "statut": "PROVISOIRE"}, res
    assert db.get_race(RID)["status"] == "ARRIVEE_PROVISOIRE"
    assert db.get_finished_races() == []                       # jamais jugée tant que provisoire
    assert db.get_race_evaluation_data(RID) is None
    # 2. Relecture identique : pas de version, seul last_checked_at avance
    res = db.record_result(RID, _reading([[4], [2], [7]], STATUT_PROVISOIRE), now_utc=t0 + timedelta(minutes=15))
    assert res["action"] == "UNCHANGED" and res["version"] == 1, res
    # 3. Définitive, classement complété (aucun rang modifié)
    res = db.record_result(RID, _reading([[4], [2], [7], [1], [8]], STATUT_DEFINITIVE), now_utc=t0 + timedelta(minutes=30))
    assert res == {"action": "VERSION", "version": 2, "reason": "PROVISOIRE_VERS_DEFINITIVE", "statut": "DEFINITIVE"}, res
    cur = db.get_result(RID)
    assert cur["statut"] == "DEFINITIVE" and cur["nb_corrections"] == 0 and cur["arrival_order"] == [4, 2, 7, 1, 8]
    assert cur["definitive_at"] == (t0 + timedelta(minutes=30)).isoformat() and cur["first_seen_at"] == t0.isoformat()
    assert db.get_race(RID)["status"] == "FINISHED" and db.get_finished_races() == [RID]
    # 4. Correction après réclamation : le 2 est déclassé derrière le 7
    res = db.record_result(RID, _reading([[4], [7], [2], [1], [8]], STATUT_DEFINITIVE), now_utc=t0 + timedelta(hours=1))
    assert res == {"action": "VERSION", "version": 3, "reason": "CORRECTION_CLASSEMENT", "statut": "DEFINITIVE"}, res
    cur = db.get_result(RID)
    assert cur["nb_corrections"] == 1 and cur["arrival_order"] == [4, 7, 2, 1, 8]
    # 5. Historique complet, append-only
    hist = db.get_result_history(RID)
    assert [(h["version"], h["statut"], h["reason"]) for h in hist] == [
        (1, "PROVISOIRE", "INITIAL"), (2, "DEFINITIVE", "PROVISOIRE_VERS_DEFINITIVE"), (3, "DEFINITIVE", "CORRECTION_CLASSEMENT")], hist
    assert [x["num"] for x in hist[0]["ranking"]] == [4, 2, 7]
    print("  [OK] test_provisoire_puis_definitive_puis_correction")


def test_regression_source_perimee_ignoree():
    db = _db(); _seed_race(db)
    db.record_result(RID, _reading([[4], [2], [7], [1], [8]], STATUT_DEFINITIVE))
    # Une source périmée (cache) ne montre que le top 3 : ignorée, version inchangée
    res = db.record_result(RID, _reading([[4], [2], [7]], STATUT_DEFINITIVE))
    assert res["action"] == "IGNORED_REGRESSION" and res["version"] == 1, res
    assert db.get_result(RID)["arrival_order"] == [4, 2, 7, 1, 8]
    # Une arrivée DEFINITIVE ne redevient jamais PROVISOIRE
    res = db.record_result(RID, _reading([[4], [2], [7], [1], [8]], STATUT_PROVISOIRE))
    assert res["action"] == "UNCHANGED" and db.get_result(RID)["statut"] == "DEFINITIVE", res
    res = db.record_result(RID, _reading([[2], [4], [7]], STATUT_PROVISOIRE))
    assert res["action"] == "DIVERGENCE_PROVISOIRE" and db.get_result(RID)["version"] == 1, res
    print("  [OK] test_regression_source_perimee_ignoree")


def test_lecture_invalide_jamais_ecrite():
    db = _db(); _seed_race(db)
    bad = _reading([[4], [4]], STATUT_DEFINITIVE); bad.errors = ["NUMERO_EN_DOUBLE"]
    res = db.record_result(RID, bad)
    assert res["action"] == "REJECTED" and db.get_result(RID) is None, res
    empty = _reading([], STATUT_DEFINITIVE)
    assert db.record_result(RID, empty)["action"] == "REJECTED"
    assert db.get_race(RID)["status"] == "SCHEDULED"
    print("  [OK] test_lecture_invalide_jamais_ecrite")


def test_annulation_versionnee():
    db = _db(); _seed_race(db)
    db.record_result(RID, _reading([[4], [2], [7]], STATUT_PROVISOIRE))
    res = db.record_result(RID, ArrivalReading(statut=STATUT_ANNULEE, pmu_statut="COURSE_ANNULEE"))
    assert res["action"] == "ANNULATION" and res["version"] == 2, res
    assert db.get_race(RID)["status"] == "ANNULEE" and db.get_result(RID)["statut"] == "ANNULEE"
    assert db.get_finished_races() == []
    assert db.get_result_history(RID)[-1]["reason"] == "ANNULATION"
    print("  [OK] test_annulation_versionnee")


def test_migration_legacy_idempotente():
    """Une base d'avant le versionnage : classement plat => structuré, statut
    DEFINITIVE, source PMU_LEGACY, version 1, historique MIGRATION_LEGACY ;
    une seconde ouverture ne change rien."""
    path = os.path.join(tempfile.mkdtemp(), "legacy.db")
    import sqlite3, json
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE races (race_id TEXT PRIMARY KEY, date TEXT NOT NULL, meeting_number INTEGER, race_number INTEGER,
            name TEXT, hippodrome TEXT, discipline TEXT, distance INTEGER, track_type TEXT, track_condition TEXT,
            rope TEXT, autostart BOOLEAN, scheduled_start_time TEXT, status TEXT DEFAULT 'SCHEDULED');
        CREATE TABLE race_results (race_id TEXT PRIMARY KEY, arrival_order_json TEXT NOT NULL,
            disqualified_json TEXT, recorded_at TEXT NOT NULL);
        INSERT INTO races (race_id, date, status) VALUES ('R1C1_01092026_X', '2026-09-01', 'FINISHED');
        INSERT INTO race_results VALUES ('R1C1_01092026_X', '[5, 3, 9]', '[]', '2026-09-01T14:00:00');
    """)
    conn.commit(); conn.close()
    db = TurfDatabase(path)
    cur = db.get_result("R1C1_01092026_X")
    assert cur["statut"] == "DEFINITIVE" and cur["source"] == "PMU_LEGACY" and cur["version"] == 1, cur
    assert cur["ranking"] == [{"rang": 1, "num": 5, "dead_heat": False}, {"rang": 2, "num": 3, "dead_heat": False},
                              {"rang": 3, "num": 9, "dead_heat": False}]
    assert cur["arrival_order"] == [5, 3, 9] and cur["first_seen_at"] == "2026-09-01T14:00:00"
    # Finalité NON vérifiée : jamais antidatée (definitive_at NULL), horodatage ancien conservé à part
    assert cur["finalite"] == "LEGACY_NON_VERIFIEE" and cur["definitive_at"] is None and cur["legacy_recorded_at"] == "2026-09-01T14:00:00"
    assert db.get_finished_races() == ["R1C1_01092026_X"]
    hist = db.get_result_history("R1C1_01092026_X")
    assert len(hist) == 1 and hist[0]["reason"] == "MIGRATION_LEGACY"
    db2 = TurfDatabase(path)  # ré-ouverture : idempotent
    assert len(db2.get_result_history("R1C1_01092026_X")) == 1 and db2.get_result("R1C1_01092026_X")["version"] == 1
    print("  [OK] test_migration_legacy_idempotente")


# ─────────────────────────────────────────────────────────────────────
# Intégration : sync_date avec un flux PMU simulé (forme réelle)
# ─────────────────────────────────────────────────────────────────────

class FakePMU:
    """Course 1 partie il y a 10 min ; l'état de l'arrivée est piloté par le test."""

    def __init__(self):
        self.course_state = {"statut": "PROGRAMMEE", "categorieStatut": "A_PARTIR", "arriveeDefinitive": False}
        self.participants_order = {}     # num -> ordreArrivee (repli)
        self.course_info = None          # réponse de l'endpoint course (None = indisponible)
        self.calls = {"programme": 0, "course": 0, "participants": 0, "rapports": 0}

    def _course(self):
        start = NOW - timedelta(minutes=10)
        c = {"numOrdre": 1, "discipline": "TROT_ATTELE", "distance": 2700, "libelle": "Prix Test", "corde": "CORDE_A_GAUCHE",
             "specialite": "", "heureDepart": calendar.timegm(start.timetuple()) * 1000, "nombreDeclaresPartants": 8}
        c.update(self.course_state)
        return c

    def fetch_programme(self, date_str):
        self.calls["programme"] += 1
        return {"programme": {"reunions": [{"numOfficiel": 1, "hippodrome": {"libelleCourt": "TESTVILLE"},
                                            "pays": {"code": "FRA"}, "courses": [self._course()]}]}}

    def fetch_participants(self, date_str, r_num, c_num):
        self.calls["participants"] += 1
        parts = []
        for num in range(1, 9):
            p = {"numPmu": num, "nom": f"Cheval{num}", "musique": "1a2a", "driver": "J", "entraineur": "T", "statut": "PARTANT",
                 "age": 5, "sexe": "M", "gainsCarriere": 100000, "deferre": "FERRE", "oeilleres": "SANS",
                 "dernierRapportDirect": {"rapport": 4.0 + num}, "dernierRapportReference": {"rapport": 5.0 + num}}
            if num in self.participants_order:
                p["ordreArrivee"] = self.participants_order[num]
            parts.append(p)
        return {"participants": parts}

    def fetch_course_info(self, date_str, r_num, c_num):
        self.calls["course"] += 1
        return self.course_info

    def fetch_rapports(self, date_str, r_num, c_num):
        self.calls["rapports"] += 1
        return [{"typePari": "SIMPLE_GAGNANT", "rapports": [{"dividendePourUnEuro": 720, "combinaison": "4"}]}]


def test_sync_cycle_complet():
    db = _db()
    mgr = DailySyncManager(db)
    mgr.fetcher = FakePMU()

    # Passe A : course partie, aucune arrivée encore => EN_ATTENTE, rien d'écrit
    s = mgr.sync_date(NOW)
    assert s["results_resolved"] == 0 and db.get_result(RID) is None, s
    assert db.get_race(RID)["status"] == "SCHEDULED"
    assert db.get_race(RID)["start_time_utc"].endswith("Z") and db.get_race(RID)["declared_runners"] == 8

    # Passe B : arrivée PROVISOIRE (top 3) dans le programme => version 1, course NON gelée
    mgr.fetcher.course_state = {"statut": "ARRIVEE_PROVISOIRE", "categorieStatut": "ARRIVEE", "arriveeDefinitive": False,
                                "ordreArrivee": [[4], [2], [7]]}
    s = mgr.sync_date(NOW)
    assert s["results_provisoires"] == 1 and s["results_definitifs"] == 0, s
    cur = db.get_result(RID)
    assert cur["statut"] == "PROVISOIRE" and cur["version"] == 1 and cur["arrival_order"] == [4, 2, 7]
    assert cur["source"] == "PMU_PROGRAMME" and cur["source_url"].endswith(f"/programme/{DATE_API}")
    assert db.get_race(RID)["status"] == "ARRIVEE_PROVISOIRE" and s["races_frozen"] == 0
    assert db.get_finished_races() == []

    # Passe C : DÉFINITIVE complète avec ex æquo et DAI => version 2, course gelée, dividendes
    mgr.fetcher.course_state = {"statut": "ARRIVEE_DEFINITIVE_COMPLETE", "categorieStatut": "ARRIVEE", "arriveeDefinitive": True,
                                "isArriveeDefinitive": True, "ordreArrivee": [[4], [2], [7], [1, 8], [3]],
                                "incidents": [{"type": "DISQUALIFIE_POUR_ALLURE_IRREGULIERE", "numeroParticipants": [6]}]}
    s = mgr.sync_date(NOW)
    assert s["results_definitifs"] == 0 and s["results_updated"] == 1, s
    cur = db.get_result(RID)
    assert cur["statut"] == "DEFINITIVE" and cur["version"] == 2 and cur["nb_corrections"] == 0
    assert cur["arrival_order"] == [4, 2, 7, 1, 8, 3] and cur["disqualified"] == [6]
    assert [(r["rang"], r["num"]) for r in cur["ranking"]][3:] == [(4, 1), (4, 8), (6, 3)]
    assert db.get_race(RID)["status"] == "FINISHED" and db.get_finished_races() == [RID]
    assert db.has_rapports(RID)

    # Passe D : course gelée, programme identique => aucune version, aucune requête partants
    before_calls = dict(mgr.fetcher.calls)
    s = mgr.sync_date(NOW)
    assert s["races_frozen"] == 1 and s["results_resolved"] == 0, s
    assert mgr.fetcher.calls["participants"] == before_calls["participants"]
    assert db.get_result(RID)["version"] == 2

    # Passe D bis : identité horaire d'une course gelée SANS heure UTC (héritée) complétée depuis heureDepart
    with db.transaction() as conn:
        conn.execute("UPDATE races SET start_time_utc = NULL, declared_runners = NULL WHERE race_id = ?", (RID,))
    assert db.get_race(RID)["start_time_utc"] is None
    mgr.sync_date(NOW)
    race = db.get_race(RID)
    assert race["start_time_utc"] and race["start_time_utc"].endswith("Z") and race["declared_runners"] == 8, race
    assert db.get_result(RID)["version"] == 2  # aucune version parasite

    # Passe E : CORRECTION publiée sur une course gelée (réclamation) => version 3, sans dégeler les pronostics
    mgr.fetcher.course_state["ordreArrivee"] = [[2], [4], [7], [1, 8], [3]]
    s = mgr.sync_date(NOW)
    assert s["results_corrections"] == 1, s
    cur = db.get_result(RID)
    assert cur["version"] == 3 and cur["nb_corrections"] == 1 and cur["arrival_order"] == [2, 4, 7, 1, 8, 3]
    assert [h["reason"] for h in db.get_result_history(RID)] == ["INITIAL", "PROVISOIRE_VERS_DEFINITIVE", "CORRECTION_CLASSEMENT"]

    # Passe F : programme périmé (cache) montrant moins de rangs => ignoré
    mgr.fetcher.course_state["ordreArrivee"] = [[2], [4]]
    s = mgr.sync_date(NOW)
    assert s["results_resolved"] == 0 and db.get_result(RID)["version"] == 3, s
    print("  [OK] test_sync_cycle_complet")


def test_sync_repli_endpoint_course_et_participants():
    db = _db()
    mgr = DailySyncManager(db)
    mgr.fetcher = FakePMU()
    # Programme dit ARRIVEE mais sans ordreArrivee (incomplet) => l'endpoint course est interrogé
    mgr.fetcher.course_state = {"statut": "FIN_COURSE", "categorieStatut": "ARRIVEE", "arriveeDefinitive": False}
    mgr.fetcher.course_info = {"statut": "ARRIVEE_DEFINITIVE", "categorieStatut": "ARRIVEE", "arriveeDefinitive": True,
                               "ordreArrivee": [[5], [1], [2]], "incidents": []}
    s = mgr.sync_date(NOW)
    cur = db.get_result(RID)
    assert cur and cur["statut"] == "DEFINITIVE" and cur["source"] == "PMU_COURSE" and cur["source_url"].endswith("/R1/C1"), cur
    assert mgr.fetcher.calls["course"] == 1

    # Nouvelle base : ni programme ni endpoint course, seulement l'ordreArrivee des partants => PROVISOIRE
    db2 = _db(); mgr2 = DailySyncManager(db2); mgr2.fetcher = FakePMU()
    mgr2.fetcher.participants_order = {5: 1, 1: 2, 2: 3}
    s = mgr2.sync_date(NOW)
    cur = db2.get_result(RID)
    assert cur and cur["statut"] == "PROVISOIRE" and cur["source"] == "PMU_PARTICIPANTS" and cur["arrival_order"] == [5, 1, 2], cur
    assert db2.get_race(RID)["status"] == "ARRIVEE_PROVISOIRE" and db2.get_finished_races() == []
    print("  [OK] test_sync_repli_endpoint_course_et_participants")


def test_sync_annulation_et_verify():
    db = _db()
    mgr = DailySyncManager(db)
    mgr.fetcher = FakePMU()
    mgr.fetcher.course_state = {"statut": "ARRIVEE_PROVISOIRE", "categorieStatut": "ARRIVEE", "arriveeDefinitive": False,
                                "ordreArrivee": [[4], [2]]}
    mgr.sync_date(NOW)
    mgr.fetcher.course_state = {"statut": "COURSE_ANNULEE", "categorieStatut": "ANNULEE"}
    s = mgr.sync_date(NOW)
    assert s.get("results_annulations") == 1 and db.get_race(RID)["status"] == "ANNULEE", s
    # verify_results : une seule requête (programme), relit et versionne
    mgr.fetcher.course_state = {"statut": "ARRIVEE_DEFINITIVE_COMPLETE", "categorieStatut": "ARRIVEE", "arriveeDefinitive": True,
                                "ordreArrivee": [[4], [2], [7]]}
    calls = dict(mgr.fetcher.calls)
    v = mgr.verify_results(NOW)
    assert v["results_checked"] == 1 and mgr.fetcher.calls["programme"] == calls["programme"] + 1
    assert mgr.fetcher.calls["participants"] == calls["participants"]
    assert db.get_result(RID)["statut"] == "DEFINITIVE" and db.get_result(RID)["version"] == 3
    print("  [OK] test_sync_annulation_et_verify")


def main():
    test_provisoire_puis_definitive_puis_correction()
    test_regression_source_perimee_ignoree()
    test_lecture_invalide_jamais_ecrite()
    test_annulation_versionnee()
    test_migration_legacy_idempotente()
    test_sync_cycle_complet()
    test_sync_repli_endpoint_course_et_participants()
    test_sync_annulation_et_verify()
    print("\n=== 8 TESTS ARRIVÉE DÉFINITIVE / SUIVI DES CORRECTIONS PASSENT ===")


if __name__ == "__main__":
    main()
