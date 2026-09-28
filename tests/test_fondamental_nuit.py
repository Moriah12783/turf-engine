"""Tests du calcul de nuit du fondamental (ombre NVE). Aucun réseau : un
miroir synthétique et un flux PMU simulé construit à partir de ses lignes."""
import io
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone

import pytest

np = pytest.importorskip("numpy")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab import fondamental_nuit as nuit
from tests.test_benter_lab import make_mirror

LAST_DAY = "2026-02-16"                  # dernier jour du miroir synthétique (200 jours depuis le 01/08/2025)


def _pmu_participant(row):
    return {"numPmu": row["num_pmu"], "nom": row["nom"], "nomPere": row["nom_pere"], "nomMere": row["nom_mere"],
            "age": row["age"], "sexe": row["sexe"], "placeCorde": row["place_corde"], "oeilleres": row["oeilleres"],
            "entraineur": row["entraineur"], "driver": row["driver"], "driverChange": bool(row["driver_change"]),
            "musique": row["musique"], "nombreCourses": row["nombre_courses"],
            "nombreVictoires": row["nombre_victoires"], "nombrePlaces": row["nombre_places"],
            "gainsParticipant": {"gainsCarriere": row["gains_carriere"],
                                 "gainsAnneeEnCours": row["gains_annee_en_cours"]},
            "handicapPoids": row["handicap_poids"], "statut": row["statut"]}


class FakePMU:
    """Programme PMU d'une journée recopié du miroir (courses et partants de
    ``source_day``), servi pour ``served_day`` ; plus une réunion étrangère."""

    def __init__(self, mirror_path, source_day, served_day=None, tweak=None, non_partant=None):
        conn = sqlite3.connect(mirror_path)
        conn.row_factory = sqlite3.Row
        self.courses = [dict(r) for r in conn.execute(
            "SELECT * FROM courses WHERE date_course = ? ORDER BY num_reunion, num_course", (source_day,))]
        self.parts = {}
        for r in conn.execute("SELECT * FROM participants WHERE date_course = ? ORDER BY num_pmu", (source_day,)):
            p = _pmu_participant(dict(r))
            if tweak:
                tweak(p)
            if non_partant and (r["num_course"], r["num_pmu"]) == non_partant:
                p["statut"] = "NON_PARTANT"
            self.parts.setdefault((r["num_reunion"], r["num_course"]), []).append(p)
        conn.close()
        self.served = date.fromisoformat(served_day or source_day).strftime("%d%m%Y")
        self.calls = 0

    def fetch_programme(self, date_str):
        assert date_str == self.served
        self.calls += 1
        courses = [{"numOrdre": c["num_course"], "specialite": c["specialite"], "discipline": c["discipline"],
                    "libelle": "Prix", "distance": 2100} for c in self.courses]
        return {"programme": {"reunions": [
            {"numOfficiel": 1, "hippodrome": {"libelleCourt": "SYNTHVILLE"}, "pays": {"code": "FRA"},
             "courses": courses},
            {"numOfficiel": 7, "hippodrome": {"libelleCourt": "ASCOT"}, "pays": {"code": "GBR"},
             "courses": [{"numOrdre": 1, "specialite": "PLAT", "discipline": "PLAT"}]}]}}

    def fetch_participants(self, date_str, r_num, c_num):
        self.calls += 1
        parts = self.parts.get((r_num, c_num))
        return {"participants": [dict(p) for p in parts]} if parts else None


@pytest.fixture(scope="module")
def mirror():
    with tempfile.TemporaryDirectory() as d:
        yield make_mirror(os.path.join(d, "h.db"))


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(nuit, "PAUSE_S", 0.0)


@pytest.fixture
def ombre_ouverte(monkeypatch):
    from turf_lab import ombre_lecture
    monkeypatch.setattr(ombre_lecture, "DEBUT_OMBRE", "2025-01-01")


def _bench(path, locked=()):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE predictions (prediction_id TEXT, race_id TEXT, engine_name TEXT, horizon TEXT)")
    for race_id in locked:
        conn.execute("INSERT INTO predictions VALUES (?, ?, 'NEW_VALUE_ENGINE', 'T_MATIN')", (race_id + "_NEW", race_id))
    conn.commit()
    conn.close()
    return path


def _rows(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT race_id, num, p, model_version, computed_at, train_until "
                            "FROM fundamental_probs ORDER BY race_id, num").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def test_nuit_ecrit_les_probabilites_du_jour(mirror, ombre_ouverte):
    today = (date.fromisoformat(LAST_DAY) + timedelta(days=1)).isoformat()
    fake = FakePMU(mirror, LAST_DAY, today, non_partant=(2, 3))
    api = date.fromisoformat(today).strftime("%d%m%Y")
    with tempfile.TemporaryDirectory() as d:
        bench = _bench(os.path.join(d, "turf_bench.db"), locked=[f"R1C4_{api}_SYNTHVILLE"])
        out = io.StringIO()
        with redirect_stdout(out):
            rep = nuit.run_night(mirror, bench, fake, now_utc=datetime(2026, 2, 17, 5, 5, tzinfo=timezone.utc))
        rows = _rows(bench)
    assert rep["ecrit"] is True and rep["train_until"] == LAST_DAY and rep["retard_historique_jours"] == 0
    assert rep["reunions_hors_perimetre"] == 1 and rep["non_partants_exclus"] == 1
    assert rep["courses_deja_verrouillees"] == 1                       # jamais sur une course verrouillée
    by_race = {}
    for race_id, num, p, version, computed_at, train_until in rows:
        by_race.setdefault(race_id, []).append((num, p))
        assert version == nuit.model_version() and train_until == LAST_DAY
        assert computed_at == "2026-02-17T05:05:00Z"
    assert len(by_race) == rep["courses_ecrites"] == 17 and f"R1C4_{api}_SYNTHVILLE" not in by_race
    assert all(abs(sum(p for _, p in v) - 1.0) < 1e-9 for v in by_race.values())       # somme 1 par course
    assert 3 not in [n for n, _ in by_race[f"R1C2_{api}_SYNTHVILLE"]]                    # non-partant exclu
    assert all(rid.startswith("R1C") and rid.endswith(f"_{api}_SYNTHVILLE") for rid in by_race)
    logs = out.getvalue()
    assert "CHEVAL" not in logs and "JOC" not in logs and "ENT " not in logs              # dépôt public


def test_nuit_refuse_apres_06h20_et_historique_en_retard(mirror, ombre_ouverte):
    today = (date.fromisoformat(LAST_DAY) + timedelta(days=1)).isoformat()
    with tempfile.TemporaryDirectory() as d:
        bench = _bench(os.path.join(d, "turf_bench.db"))
        with redirect_stdout(io.StringIO()):
            late = nuit.run_night(mirror, bench, FakePMU(mirror, LAST_DAY, today),
                                  now_utc=datetime(2026, 2, 17, 6, 20, tzinfo=timezone.utc))
            stale = nuit.run_night(mirror, bench, FakePMU(mirror, LAST_DAY, "2026-02-21"),
                                   now_utc=datetime(2026, 2, 21, 5, 5, tzinfo=timezone.utc))
            trial = nuit.run_night(mirror, bench, FakePMU(mirror, LAST_DAY, today),
                                   now_utc=datetime(2026, 2, 17, 12, 0, tzinfo=timezone.utc), enforce_hour=False)
        assert late["refus"] == "HEURE_LIMITE_DEPASSEE" and not late["ecrit"]
        assert stale["refus"] == "HISTORIQUE_EN_RETARD" and stale["retard_historique_jours"] == 4
        assert trial["ecrit"] is True                       # essai : copie locale, jamais renvoyée sur R2
        assert len({r[0] for r in _rows(bench)}) == trial["courses_ecrites"]


def test_nuit_refuse_si_fuite(tmp_path, ombre_ouverte):
    leaky = make_mirror(str(tmp_path / "leak.db"), days=120, leak=True)
    day = (date(2025, 8, 1) + timedelta(days=119)).isoformat()
    today = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    bench = _bench(str(tmp_path / "turf_bench.db"))
    with redirect_stdout(io.StringIO()):
        rep = nuit.run_night(leaky, bench, FakePMU(leaky, day, today),
                             now_utc=datetime.fromisoformat(today + "T05:05:00+00:00"))
    assert rep["refus"] == "AUDIT_DE_FUITE" and _rows(bench) == []


def test_race_id_identique_a_daily_sync(tmp_path):
    """Le race_id écrit la nuit est celui que daily_sync donne à la course."""
    from turf_lab.daily_sync import DailySyncManager
    from turf_lab.database import TurfDatabase

    class Programme:
        def fetch_programme(self, date_str):
            return {"programme": {"reunions": [
                {"numOfficiel": 1, "hippodrome": {"libelleCourt": "VINCENNES"}, "pays": {"code": "FRA"},
                 "courses": [{"numOrdre": 1, "discipline": "ATTELE", "specialite": "TROT_ATTELE"},
                             {"numOrdre": 2, "discipline": "PLAT", "specialite": "PLAT"}]},
                {"numOfficiel": 4, "hippodrome": {"libelleCourt": "PAU"}, "pays": {"code": "FRA"},
                 "courses": [{"numOrdre": 3, "discipline": "HAIE", "specialite": "OBSTACLE"}]},
                {"numOfficiel": 6, "hippodrome": {"libelleCourt": "ASCOT"}, "pays": {"code": "GBR"},
                 "courses": [{"numOrdre": 1, "discipline": "PLAT", "specialite": "PLAT"}]}]}}

        def fetch_participants(self, date_str, r_num, c_num):
            return {"participants": [{"numPmu": n, "nom": f"H{r_num}{c_num}{n}", "statut": "PARTANT", "age": 5,
                                      "sexe": "HONGRES", "musique": "1a"} for n in range(1, 6)]}

        def fetch_course_info(self, *a):
            return None

        def fetch_rapports(self, *a):
            return []

    db = TurfDatabase(str(tmp_path / "bench.db"))
    mgr = DailySyncManager(db)
    mgr.fetcher = Programme()
    with redirect_stdout(io.StringIO()):
        mgr.sync_date(datetime(2026, 2, 17))
    conn = sqlite3.connect(str(tmp_path / "bench.db"))
    sync_ids = {r[0] for r in conn.execute("SELECT race_id FROM races")}
    conn.close()
    courses, stats = nuit.fetch_programme(Programme(), "2026-02-17")
    assert {c["race_id"] for c in courses} == sync_ids == {
        "R1C1_17022026_VINCENNES", "R1C2_17022026_VINCENNES", "R4C3_17022026_PAU"}
    assert stats["reunions_hors_perimetre"] == 1


def test_parite_programme_pmu_et_miroir(mirror):
    with redirect_stdout(io.StringIO()):
        same = nuit.parity_day(nuit.lab.load_races(mirror)[0], LAST_DAY, FakePMU(mirror, LAST_DAY))
    assert same["courses_comparees"] == same["courses_miroir"] > 0
    assert set(same["champs_identiques_pct"].values()) == {100.0}
    assert set(same["variables_identiques_pct"].values()) == {100.0}
    assert same["delta_p_max"] == 0.0 and same["favori_different_pct"] == 0.0

    def plus_une(p):                                    # programme décalé d'une course
        p["nombreCourses"] = (p["nombreCourses"] or 0) + 1
    with redirect_stdout(io.StringIO()):
        off = nuit.parity_day(nuit.lab.load_races(mirror)[0], LAST_DAY, FakePMU(mirror, LAST_DAY, tweak=plus_une))
    assert off["champs_identiques_pct"]["nombre_courses"] == 0.0
    assert off["champs_identiques_pct"]["nom"] == 100.0
    assert off["variables_identiques_pct"]["log_courses"] == 0.0 and off["delta_p_moyen"] > 0


def test_version_du_modele_stable_et_sensible_au_code(monkeypatch):
    v1 = nuit.model_version()
    assert v1 == nuit.model_version() and v1.startswith("fond-matin-")
    monkeypatch.setattr(nuit.lab, "RIDGE", 2.0)
    assert nuit.model_version() != v1                   # réglage changé => nouvelle version


def test_essai_en_ligne_de_commande(mirror, tmp_path):
    bench = _bench(str(tmp_path / "copie_banc.db"))
    with redirect_stdout(io.StringIO()) as out:
        code = nuit.main(["essai", "--historique", mirror, "--banc", bench, "--jour", "2026-02-17"],
                         fetcher=FakePMU(mirror, LAST_DAY, "2026-02-17"))
    assert code == 0 and len({r[0] for r in _rows(bench)}) == 18
    line = [l for l in out.getvalue().splitlines() if l.startswith("NUIT_FONDAMENTAL ")][0]
    assert json.loads(line.split(" ", 1)[1])["courses_ecrites"] == 18


def test_rien_n_est_ecrit_avant_le_gel_de_la_regle(mirror, tmp_path, monkeypatch):
    """Tant que DEBUT_OMBRE n'est pas inscrit (gel après le 06/10), la nuit
    refuse d'écrire, même lancée à la main ; avant DEBUT_OMBRE aussi."""
    from turf_lab import ombre_lecture
    bench = _bench(str(tmp_path / "turf_bench.db"))
    fake = FakePMU(mirror, LAST_DAY, "2026-02-17")
    at = datetime(2026, 2, 17, 5, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(ombre_lecture, "DEBUT_OMBRE", None)
    with redirect_stdout(io.StringIO()):
        closed = nuit.run_night(mirror, bench, fake, now_utc=at)
    monkeypatch.setattr(ombre_lecture, "DEBUT_OMBRE", "2026-02-18")
    with redirect_stdout(io.StringIO()):
        early = nuit.run_night(mirror, bench, fake, now_utc=at)
    assert closed["refus"] == early["refus"] == "OMBRE_PAS_OUVERTE" and _rows(bench) == []
    assert fake.calls == 0                                   # pas même une requête PMU
