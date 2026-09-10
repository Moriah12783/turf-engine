"""Reproduction des cas remontés par le partenaire (validation du 10/09/2026)
et des exigences de traçabilité associées.

Exécutable en script (python tests/test_partenaire_disqualification.py) ou via
pytest. Véritable stockage SQLite, fixtures du dépôt, aucune donnée réelle.
"""
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab.database import TurfDatabase
from turf_lab.results_reader import (
    CMP_CORRECTION, CMP_REGRESSION, ArrivalReading, STATUT_DEFINITIVE, STATUT_PROVISOIRE,
    compare_rankings, ranking_from_groups, removed_unexplained,
)

RID = "R1C1_10092026_TESTVILLE"
DQ = "DISQUALIFIE_POUR_ALLURE_IRREGULIERE"


def _db():
    return TurfDatabase(os.path.join(tempfile.mkdtemp(), "partenaire.db"))


def _seed(db):
    db.save_race({"race_id": RID, "date": "2026-09-10", "meeting_number": 1, "race_number": 1, "name": "Prix Test",
                  "hippodrome": "TESTVILLE", "discipline": "TROT_ATTELE", "distance": 2700, "track_type": "SABLE",
                  "track_condition": "BON", "rope": "GAUCHE", "autostart": False,
                  "scheduled_start_time": "14:00 GMT (16:00 Paris)", "status": "SCHEDULED"})
    db.save_runners(RID, [{"num": n, "horse_name": f"Cheval{n}", "sex": "M", "age": 5, "driver_jockey": "J", "trainer": "T",
                           "weight": 60.0, "draw": n, "shoeing": "FERRE", "blinkers": "SANS", "morning_odds": 5.0,
                           "odds_t15": 5.0, "final_odds": 5.0, "is_non_partant": False, "press_citation_count": 0,
                           "music": "1a", "earnings": 0.0, "record_chrono": 0.0, "official_rating": 0.0} for n in range(1, 9)])


def _reading(groups, statut, incidents=None, pmu_statut=None):
    r = ArrivalReading(statut=statut, definitive_flag=(statut == STATUT_DEFINITIVE), source="PMU_PROGRAMME",
                       pmu_statut=pmu_statut or ("ARRIVEE_DEFINITIVE_COMPLETE" if statut == STATUT_DEFINITIVE else "ARRIVEE_PROVISOIRE"))
    r.ranking = ranking_from_groups(groups)
    r.incidents = incidents or []
    return r


# ── Cas 1 du partenaire : DEFINITIVE puis disqualification finale ────────

def test_disqualification_finale_apres_definitive():
    db = _db(); _seed(db)
    t0 = datetime(2026, 9, 10, 14, 5)
    assert db.record_result(RID, _reading([[1], [2], [3], [4], [5]], STATUT_DEFINITIVE), now_utc=t0)["action"] == "INITIAL"
    res = db.record_result(RID, _reading([[1], [2], [3], [4]], STATUT_DEFINITIVE, incidents=[{"num": 5, "type": DQ}]),
                           now_utc=t0 + timedelta(minutes=20))
    assert res == {"action": "VERSION", "version": 2, "reason": "CORRECTION_CLASSEMENT", "statut": "DEFINITIVE"}, res
    cur = db.get_result(RID)
    assert cur["arrival_order"] == [1, 2, 3, 4], cur["arrival_order"]            # le cheval est retiré
    assert cur["incidents"] == [{"num": 5, "type": DQ}] and cur["disqualified"] == [5]  # l'incident est conservé
    assert cur["nb_corrections"] == 1 and cur["statut"] == "DEFINITIVE"
    hist = db.get_result_history(RID)
    assert [(h["version"], h["reason"], [x["num"] for x in h["ranking"]]) for h in hist] == [
        (1, "INITIAL", [1, 2, 3, 4, 5]), (2, "CORRECTION_CLASSEMENT", [1, 2, 3, 4])]   # ancienne version préservée
    assert db.get_finished_races() == [RID]
    print("  [OK] test_disqualification_finale_apres_definitive")


# ── Cas 2 du partenaire : PROVISOIRE puis DEFINITIVE avec disqualification ──

def test_disqualification_finale_depuis_provisoire():
    db = _db(); _seed(db)
    t0 = datetime(2026, 9, 10, 14, 5)
    assert db.record_result(RID, _reading([[1], [2], [3], [4], [5]], STATUT_PROVISOIRE), now_utc=t0)["action"] == "INITIAL"
    res = db.record_result(RID, _reading([[1], [2], [3], [4]], STATUT_DEFINITIVE, incidents=[{"num": 5, "type": DQ}]),
                           now_utc=t0 + timedelta(minutes=12))
    assert res["action"] == "VERSION" and res["version"] == 2 and res["statut"] == "DEFINITIVE", res
    assert res["reason"] == "CORRECTION_CLASSEMENT", res
    cur = db.get_result(RID)
    assert cur["arrival_order"] == [1, 2, 3, 4] and cur["incidents"] == [{"num": 5, "type": DQ}], cur
    assert cur["finalite"] == "VERIFIEE_PMU" and cur["verified_at"] == (t0 + timedelta(minutes=12)).isoformat()
    assert cur["definitive_at"] == cur["verified_at"]
    hist = db.get_result_history(RID)
    assert [x["num"] for x in hist[0]["ranking"]] == [1, 2, 3, 4, 5] and hist[0]["statut"] == "PROVISOIRE"
    assert [x["num"] for x in hist[1]["ranking"]] == [1, 2, 3, 4] and hist[1]["statut"] == "DEFINITIVE"
    print("  [OK] test_disqualification_finale_depuis_provisoire")


# ── Le garde-fou de régression survit : retrait INEXPLIQUÉ ────────────────

def test_retrait_inexplique_reste_une_regression():
    db = _db(); _seed(db)
    db.record_result(RID, _reading([[1], [2], [3], [4], [5]], STATUT_DEFINITIVE))
    res = db.record_result(RID, _reading([[1], [2], [3], [4]], STATUT_DEFINITIVE))   # aucun incident, aucun NP
    assert res["action"] == "IGNORED_REGRESSION" and db.get_result(RID)["arrival_order"] == [1, 2, 3, 4, 5], res
    # Retrait expliqué par une NON-PARTANCE (cheval retiré du programme) = correction
    r = _reading([[1], [2], [3], [4]], STATUT_DEFINITIVE); r.non_partants = [5]
    res = db.record_result(RID, r)
    assert res["action"] == "VERSION" and res["reason"] == "CORRECTION_CLASSEMENT", res
    assert db.get_result(RID)["arrival_order"] == [1, 2, 3, 4] and db.get_result(RID)["non_partants"] == [5]
    # Unité : comparaison directe
    old = ranking_from_groups([[1], [2], [3], [4], [5]]); new = ranking_from_groups([[1], [2], [3], [4]])
    assert compare_rankings(old, new, [], []) == CMP_REGRESSION
    assert compare_rankings(old, new, [], [{"num": 5, "type": DQ}]) == CMP_CORRECTION
    assert compare_rankings(old, new, [], [], (), (5,)) == CMP_CORRECTION
    assert removed_unexplained(old, new, []) == [5] and removed_unexplained(old, new, [{"num": 5, "type": DQ}]) == []
    print("  [OK] test_retrait_inexplique_reste_une_regression")


# ── Traçabilité (point 3) : legacy non vérifié vs finalité vérifiée ─────

def test_legacy_non_verifiee_puis_confirmation():
    path = os.path.join(tempfile.mkdtemp(), "legacy.db")
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
    # « Définitif selon l'ancien système » : jugé par le banc, mais finalité NON vérifiée, jamais antidatée
    assert cur["statut"] == "DEFINITIVE" and cur["finalite"] == "LEGACY_NON_VERIFIEE"
    assert cur["definitive_at"] is None and cur["verified_at"] is None
    assert cur["legacy_recorded_at"] == "2026-09-01T14:00:00"
    assert db.get_finished_races() == ["R1C1_01092026_X"]

    # Relecture IDENTIQUE par le nouveau lecteur avec le drapeau PMU : preuve de
    # revalidation conservée (version dédiée, source/URL/statut PMU enregistrés)
    t1 = datetime(2026, 9, 10, 12, 12, 42)
    res = db.record_result("R1C1_01092026_X", _reading([[5], [3], [9]], STATUT_DEFINITIVE),
                           source_url="https://online.turfinfo.api.pmu.fr/rest/client/7/programme/01092026", now_utc=t1)
    assert res == {"action": "VERSION", "version": 2, "reason": "CONFIRMATION_DEFINITIVE", "statut": "DEFINITIVE"}, res
    cur = db.get_result("R1C1_01092026_X")
    assert cur["finalite"] == "VERIFIEE_PMU" and cur["verified_at"] == t1.isoformat() and cur["definitive_at"] == t1.isoformat()
    assert cur["source"] == "PMU_PROGRAMME" and cur["source_url"].endswith("/01092026") and cur["pmu_statut"] == "ARRIVEE_DEFINITIVE_COMPLETE"
    assert cur["arrival_order"] == [5, 3, 9] and cur["nb_corrections"] == 0
    assert cur["legacy_recorded_at"] == "2026-09-01T14:00:00"
    hist = db.get_result_history("R1C1_01092026_X")
    assert [(h["version"], h["reason"], h["source"]) for h in hist] == [(1, "MIGRATION_LEGACY", "PMU_LEGACY"), (2, "CONFIRMATION_DEFINITIVE", "PMU_PROGRAMME")]
    # Une seconde relecture identique n'ajoute rien (preuve déjà acquise)
    res = db.record_result("R1C1_01092026_X", _reading([[5], [3], [9]], STATUT_DEFINITIVE), now_utc=t1 + timedelta(hours=1))
    assert res["action"] == "UNCHANGED" and db.get_result("R1C1_01092026_X")["version"] == 2
    # Une relecture PROVISOIRE identique ne vérifie rien
    db2 = TurfDatabase(os.path.join(tempfile.mkdtemp(), "l2.db"))
    conn = sqlite3.connect(db2.db_path)
    conn.execute("INSERT INTO races (race_id, date, status) VALUES ('R1C1_01092026_Y', '2026-09-01', 'FINISHED')")
    conn.execute("INSERT INTO race_results (race_id, arrival_order_json, disqualified_json, recorded_at) VALUES ('R1C1_01092026_Y', '[5, 3]', '[]', '2026-09-01T14:00:00')")
    conn.commit(); conn.close()
    db2 = TurfDatabase(db2.db_path)
    assert db2.get_result("R1C1_01092026_Y")["finalite"] == "LEGACY_NON_VERIFIEE"
    res = db2.record_result("R1C1_01092026_Y", _reading([[5], [3]], STATUT_PROVISOIRE))
    assert res["action"] == "UNCHANGED" and db2.get_result("R1C1_01092026_Y")["finalite"] == "LEGACY_NON_VERIFIEE"
    print("  [OK] test_legacy_non_verifiee_puis_confirmation")


def test_reparation_des_lignes_versionnees_avant_finalite():
    """Base telle que produite par le commit 26b1b53 (colonnes de version, pas
    de finalité) : la réparation recalcule la finalité depuis l'historique."""
    path = os.path.join(tempfile.mkdtemp(), "v1.db")
    db = TurfDatabase(path)  # crée le schéma complet
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO races (race_id, date, status) VALUES ('A', '2026-09-05', 'FINISHED')")
    conn.execute("INSERT INTO races (race_id, date, status) VALUES ('B', '2026-09-05', 'FINISHED')")
    # A : legacy complété par le nouveau lecteur le 10/09 (definitive_at antidaté par l'ancien code)
    conn.execute("""INSERT INTO race_results (race_id, arrival_order_json, disqualified_json, recorded_at, statut, version,
                    nb_corrections, source, ranking_json, incidents_json, non_partants_json, first_seen_at, definitive_at, updated_at, last_checked_at)
                    VALUES ('A', '[1,2,3]', '[]', '2026-09-05T14:00:00', 'DEFINITIVE', 2, 0, 'PMU_PROGRAMME',
                    '[{"rang":1,"num":1,"dead_heat":false},{"rang":2,"num":2,"dead_heat":false},{"rang":3,"num":3,"dead_heat":false}]', '[]', '[]',
                    '2026-09-05T14:00:00', '2026-09-05T14:00:00', '2026-09-10T12:12:42', '2026-09-10T12:12:42')""")
    conn.execute("""INSERT INTO race_results_history (race_id, version, statut, ranking_json, incidents_json, non_partants_json, source, reason, recorded_at)
                    VALUES ('A', 1, 'DEFINITIVE', '[{"rang":1,"num":1,"dead_heat":false},{"rang":2,"num":2,"dead_heat":false}]', '[]', '[]', 'PMU_LEGACY', 'MIGRATION_LEGACY', '2026-09-05T14:00:00')""")
    conn.execute("""INSERT INTO race_results_history (race_id, version, statut, ranking_json, incidents_json, non_partants_json, source, reason, recorded_at)
                    VALUES ('A', 2, 'DEFINITIVE', '[{"rang":1,"num":1,"dead_heat":false},{"rang":2,"num":2,"dead_heat":false},{"rang":3,"num":3,"dead_heat":false}]', '[]', '[]', 'PMU_PROGRAMME', 'COMPLETION', '2026-09-10T12:12:42')""")
    # B : legacy jamais relu
    conn.execute("""INSERT INTO race_results (race_id, arrival_order_json, disqualified_json, recorded_at, statut, version,
                    nb_corrections, source, ranking_json, incidents_json, non_partants_json, first_seen_at, definitive_at, updated_at, last_checked_at)
                    VALUES ('B', '[4,5]', '[]', '2026-09-05T15:00:00', 'DEFINITIVE', 1, 0, 'PMU_LEGACY',
                    '[{"rang":1,"num":4,"dead_heat":false},{"rang":2,"num":5,"dead_heat":false}]', '[]', '[]',
                    '2026-09-05T15:00:00', '2026-09-05T15:00:00', '2026-09-05T15:00:00', '2026-09-05T15:00:00')""")
    conn.execute("""INSERT INTO race_results_history (race_id, version, statut, ranking_json, incidents_json, non_partants_json, source, reason, recorded_at)
                    VALUES ('B', 1, 'DEFINITIVE', '[{"rang":1,"num":4,"dead_heat":false},{"rang":2,"num":5,"dead_heat":false}]', '[]', '[]', 'PMU_LEGACY', 'MIGRATION_LEGACY', '2026-09-05T15:00:00')""")
    conn.execute("UPDATE race_results SET finalite = NULL, verified_at = NULL, legacy_recorded_at = NULL")
    conn.commit(); conn.close()
    db = TurfDatabase(path)  # réparation à l'ouverture
    a, b = db.get_result("A"), db.get_result("B")
    assert a["finalite"] == "VERIFIEE_PMU" and a["verified_at"] == "2026-09-10T12:12:42" and a["definitive_at"] == "2026-09-10T12:12:42"
    assert a["legacy_recorded_at"] == "2026-09-05T14:00:00" and a["version"] == 2
    assert b["finalite"] == "LEGACY_NON_VERIFIEE" and b["verified_at"] is None and b["definitive_at"] is None
    assert b["legacy_recorded_at"] == "2026-09-05T15:00:00"
    assert sorted(db.get_finished_races()) == ["A", "B"]  # le banc juge toujours les deux
    db3 = TurfDatabase(path)  # idempotent
    assert db3.get_result("A")["verified_at"] == "2026-09-10T12:12:42" and db3.get_result("B")["definitive_at"] is None
    print("  [OK] test_reparation_des_lignes_versionnees_avant_finalite")


# ── Oscillation d'incidents (défaut constaté en production le 10/09) ─────

def test_variante_sans_incidents_ne_cree_aucune_version():
    """Le programme PMU servi depuis un cache peut arriver SANS le bloc incidents
    (classement identique). Cette perte d'information ne doit ni retirer les
    incidents connus, ni créer de version, ni compter une correction."""
    db = _db(); _seed(db)
    t0 = datetime(2026, 9, 10, 12, 12)
    full = _reading([[2], [8], [13], [6]], STATUT_DEFINITIVE, incidents=[{"num": 1, "type": DQ}, {"num": 3, "type": DQ}])
    full.non_partants = [7]
    assert db.record_result(RID, full, now_utc=t0)["action"] == "INITIAL"
    for k in range(1, 9):   # 8 passes alternées : sans incidents / avec incidents
        bare = _reading([[2], [8], [13], [6]], STATUT_DEFINITIVE)
        res = db.record_result(RID, bare, now_utc=t0 + timedelta(minutes=6 * k))
        assert res["action"] == "UNCHANGED", (k, res)
        res = db.record_result(RID, _reading([[2], [8], [13], [6]], STATUT_DEFINITIVE,
                                             incidents=[{"num": 1, "type": DQ}, {"num": 3, "type": DQ}]), now_utc=t0 + timedelta(minutes=6 * k + 3))
        assert res["action"] == "UNCHANGED", (k, res)
    cur = db.get_result(RID)
    assert cur["version"] == 1 and cur["nb_corrections"] == 0
    assert cur["incidents"] == [{"num": 1, "type": DQ}, {"num": 3, "type": DQ}] and cur["non_partants"] == [7]
    # Un incident ne disparaît que si le cheval RÉAPPARAÎT au classement (disqualification annulée)
    back = _reading([[2], [8], [1], [13], [6]], STATUT_DEFINITIVE, incidents=[{"num": 3, "type": DQ}])
    res = db.record_result(RID, back, now_utc=t0 + timedelta(hours=2))
    assert res["action"] == "VERSION" and res["reason"] == "CORRECTION_CLASSEMENT", res
    cur = db.get_result(RID)
    assert cur["arrival_order"] == [2, 8, 1, 13, 6] and cur["incidents"] == [{"num": 3, "type": DQ}] and cur["non_partants"] == [7]
    print("  [OK] test_variante_sans_incidents_ne_cree_aucune_version")


def test_reparation_des_oscillations_en_base():
    """Base polluée par l'ancien comportement (paires perte/retour) : les
    versions techniques sont retirées, la réparation est tracée, les vraies
    corrections restent."""
    db = _db(); _seed(db)
    t0 = datetime(2026, 9, 10, 12, 12)
    full = _reading([[2], [8], [13], [6]], STATUT_DEFINITIVE, incidents=[{"num": 1, "type": DQ}]); full.non_partants = [7]
    db.record_result(RID, full, now_utc=t0)
    # Injection directe de 6 versions techniques (3 pertes + 3 retours) puis d'une vraie correction
    conn = sqlite3.connect(db.db_path)
    rk = json.dumps(ranking_from_groups([[2], [8], [13], [6]]))
    inc_full, inc_none = json.dumps([{"num": 1, "type": DQ}]), "[]"
    v = 1
    for k in range(3):
        v += 1; conn.execute("INSERT INTO race_results_history (race_id, version, statut, ranking_json, incidents_json, non_partants_json, source, reason, recorded_at) VALUES (?,?,?,?,?,?,?,?,?)",
                             (RID, v, "DEFINITIVE", rk, inc_none, "[]", "PMU_PROGRAMME", "CORRECTION_CLASSEMENT", (t0 + timedelta(minutes=10 * v)).isoformat()))
        v += 1; conn.execute("INSERT INTO race_results_history (race_id, version, statut, ranking_json, incidents_json, non_partants_json, source, reason, recorded_at) VALUES (?,?,?,?,?,?,?,?,?)",
                             (RID, v, "DEFINITIVE", rk, inc_full, "[7]", "PMU_PROGRAMME", "COMPLETION", (t0 + timedelta(minutes=10 * v)).isoformat()))
    v += 1
    rk_corr = json.dumps(ranking_from_groups([[8], [2], [13], [6]]))
    conn.execute("INSERT INTO race_results_history (race_id, version, statut, ranking_json, incidents_json, non_partants_json, source, reason, recorded_at) VALUES (?,?,?,?,?,?,?,?,?)",
                 (RID, v, "DEFINITIVE", rk_corr, inc_full, "[7]", "PMU_PROGRAMME", "CORRECTION_CLASSEMENT", (t0 + timedelta(minutes=10 * v)).isoformat()))
    conn.execute("UPDATE race_results SET version = ?, nb_corrections = 4, ranking_json = ?, arrival_order_json = '[8,2,13,6]' WHERE race_id = ?", (v, rk_corr, RID))
    conn.commit(); conn.close()
    db = TurfDatabase(db.db_path)  # réparation à l'ouverture
    hist = db.get_result_history(RID)
    assert [(h["version"], h["reason"]) for h in hist] == [(1, "INITIAL"), (2, "CORRECTION_CLASSEMENT"), (3, "REPARATION_OSCILLATION_INCIDENTS")], hist
    cur = db.get_result(RID)
    assert cur["version"] == 3 and cur["nb_corrections"] == 1 and cur["arrival_order"] == [8, 2, 13, 6]
    assert cur["incidents"] == [{"num": 1, "type": DQ}] and cur["non_partants"] == [7]
    db = TurfDatabase(db.db_path)  # idempotent
    assert [h["version"] for h in db.get_result_history(RID)] == [1, 2, 3]
    print("  [OK] test_reparation_des_oscillations_en_base")


def main():
    test_disqualification_finale_apres_definitive()
    test_disqualification_finale_depuis_provisoire()
    test_retrait_inexplique_reste_une_regression()
    test_legacy_non_verifiee_puis_confirmation()
    test_reparation_des_lignes_versionnees_avant_finalite()
    test_variante_sans_incidents_ne_cree_aucune_version()
    test_reparation_des_oscillations_en_base()
    print("\n=== 7 TESTS RETOUR PARTENAIRE (DISQUALIFICATION FINALE / TRAÇABILITÉ / OSCILLATION) PASSENT ===")


if __name__ == "__main__":
    main()
