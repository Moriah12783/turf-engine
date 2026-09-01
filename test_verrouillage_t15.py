"""Tests fonctionnels du verrouillage multi-horizon (T-15 réel) et de la persistance de l'historique."""
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from turf_lab.database import TurfDatabase
from turf_lab.daily_sync import DailySyncManager

NOW = datetime.utcnow()


def ms(dt):
    return int(dt.timestamp() * 1000)


class FakeFetcher:
    """Simule l'API PMU avec des courses à différentes distances temporelles du départ."""

    def __init__(self):
        # course_num -> minutes avant départ (5 : course déjà partie)
        self.races = {1: 200, 2: 80, 3: 25, 4: 10, 5: -5}
        self.live_odds = {1: 5.0, 2: 6.0, 3: 7.0, 4: 8.0, 5: 9.0}
        self.finished = set()

    def fetch_programme(self, date_str):
        import calendar
        courses = []
        for c_num, mins in self.races.items():
            start = NOW + timedelta(minutes=mins)
            # timestamp UTC en ms (utcfromtimestamp doit redonner l'heure voulue)
            epoch = calendar.timegm(start.timetuple())
            courses.append({
                "numOrdre": c_num,
                "discipline": "TROT_ATTELE",
                "distance": 2700,
                "libelle": f"Prix Test C{c_num}",
                "corde": "CORDE_A_GAUCHE",
                "specialite": "",
                "heureDepart": epoch * 1000,
            })
        return {"programme": {"reunions": [{
            "numOfficiel": 1,
            "hippodrome": {"libelleCourt": "TESTVILLE"},
            "courses": courses,
        }]}}

    def fetch_participants(self, date_str, r_num, c_num):
        parts = []
        for num in range(1, 9):
            p = {
                "numPmu": num,
                "nom": f"Cheval{num}",
                "musique": "1a2a3a",
                "driver": "J.TEST",
                "entraineur": "T.TEST",
                "statut": "PARTANT",
                "age": 5,
                "sexe": "M",
                "gainsCarriere": 5000000,
                "deferre": "FERRE",
                "oeilleres": "SANS",
                "dernierRapportDirect": {"rapport": self.live_odds[c_num] + num},
                "rapportReference": {"rapport": 10.0 + num},
            }
            if c_num in self.finished:
                p["ordreArrivee"] = num  # arrivée 1..8
            parts.append(p)
        return {"participants": parts}

    def fetch_course_info(self, date_str, r_num, c_num):
        return {}

    def fetch_rapports(self, date_str, r_num, c_num):
        if c_num in self.finished:
            return [{"typePari": "SIMPLE_GAGNANT", "dividende": 850, "combinaison": "1"}]
        return None


def get_horizons(db, race_id):
    return sorted(db.get_locked_horizons(race_id))


def main():
    tmp = tempfile.mkdtemp()
    db = TurfDatabase(os.path.join(tmp, "test.db"))
    mgr = DailySyncManager(db)
    mgr.fetcher = FakeFetcher()

    date_api = NOW.strftime("%d%m%Y")
    rid = lambda c: f"R1C{c}_{date_api}_TESTVILLE"

    # --- PASSE 1 : verrouillage selon la fenêtre temporelle ---
    stats1 = mgr.sync_date(NOW)
    print("PASSE 1:", stats1)
    a1 = get_horizons(db, rid(1)); a2 = get_horizons(db, rid(2)); a3 = get_horizons(db, rid(3)); a4 = get_horizons(db, rid(4)); a5 = get_horizons(db, rid(5))
    print("  C1 (H-200):", a1, "| attendu [T_MATIN]")
    print("  C2 (H-80): ", a2, "| attendu [T90, T_MATIN]")
    print("  C3 (H-25): ", a3, "| attendu [T30, T90, T_MATIN]")
    print("  C4 (H-10): ", a4, "| attendu [T15, T30, T90, T_MATIN]")
    print("  C5 (partie):", a5, "| attendu [] (JAMAIS de verrou après le départ)")
    assert a1 == ["T_MATIN"], a1
    assert a2 == ["T90", "T_MATIN"], a2
    assert a3 == ["T30", "T90", "T_MATIN"], a3
    assert a4 == ["T15", "T30", "T90", "T_MATIN"], a4
    assert a5 == [], a5

    # cotes archivées passe 1
    r4 = {r["num"]: r for r in db.get_runners(rid(4))}
    morning_before = r4[1]["morning_odds"]
    t15_before = r4[1]["odds_t15"]
    snaps4 = db.get_odds_snapshots(rid(4))
    assert snaps4[1].get("T15") == t15_before, (snaps4[1], t15_before)
    lock_time_before = [p for p in db.get_predictions(rid(4)) if p["horizon"] == "T15" and p["engine_name"] == "NEW_VALUE_ENGINE"][0]["lock_time"]

    # --- PASSE 2 : les cotes bougent, rien de verrouillé ne doit changer ---
    mgr.fetcher.live_odds = {1: 2.0, 2: 3.0, 3: 3.5, 4: 4.0, 5: 9.5}
    stats2 = mgr.sync_date(NOW)
    print("PASSE 2:", stats2)
    assert stats2["predictions_locked"] == 0, stats2  # rien de nouveau à verrouiller

    r4b = {r["num"]: r for r in db.get_runners(rid(4))}
    assert r4b[1]["morning_odds"] == morning_before, "morning_odds écrasée !"
    assert r4b[1]["odds_t15"] == t15_before, "cote T-15 écrasée !"
    assert r4b[1]["final_odds"] == 4.0 + 1, "final_odds devrait suivre le direct"
    lock_time_after = [p for p in db.get_predictions(rid(4)) if p["horizon"] == "T15" and p["engine_name"] == "NEW_VALUE_ENGINE"][0]["lock_time"]
    assert lock_time_after == lock_time_before, "pronostic T-15 re-verrouillé !"
    snaps4b = db.get_odds_snapshots(rid(4))
    assert snaps4b[1].get("T15") == t15_before, "snapshot T-15 écrasé !"
    print("  [OK] morning/T-15/pronostics figés, final_odds suit le direct")

    # --- PASSE 3 : la course 4 se termine ---
    mgr.fetcher.finished.add(4)
    stats3 = mgr.sync_date(NOW)
    print("PASSE 3:", stats3)
    assert stats3["results_resolved"] >= 1
    race4 = db.get_race(rid(4))
    assert race4["status"] == "FINISHED", race4["status"]

    # --- PASSE 4 : course terminée => archive gelée ---
    mgr.fetcher.live_odds = {1: 9.9, 2: 9.9, 3: 9.9, 4: 99.0, 5: 9.9}
    stats4 = mgr.sync_date(NOW)
    print("PASSE 4:", stats4)
    assert stats4["races_frozen"] >= 1, stats4
    r4c = {r["num"]: r for r in db.get_runners(rid(4))}
    assert r4c[1]["final_odds"] == 4.0 + 1, "cote finale modifiée après clôture !"
    print("  [OK] course terminée gelée définitivement")

    # --- PASSE 5 : date passée => AUCUN verrou rétroactif (intégrité éditoriale) ---
    yesterday = NOW - timedelta(days=1)
    stats5 = mgr.sync_date(yesterday)
    rid_y = f"R1C1_{yesterday.strftime('%d%m%Y')}_TESTVILLE"
    ay = get_horizons(db, rid_y)
    print("PASSE 5 (date passée):", ay, "| attendu [] (aucun pronostic rétroactif)")
    assert ay == [], ay

    print("\n=== TOUS LES TESTS T-15 / PERSISTANCE PASSENT ===")


if __name__ == "__main__":
    main()
