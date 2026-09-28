"""Tests du labo du déferrage : lecture du flux, rattrapage incrémental,
variables « première fois » figées à la veille, test de bout en bout sur un
miroir synthétique où le déferrage porte de l'information."""
import io
import os
import random
import sqlite3
import sys
from contextlib import redirect_stdout
from datetime import date

import pytest

np = pytest.importorskip("numpy")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab import deferre_lab as dl
from tests.test_benter_lab import make_mirror
from tests.test_r2_store import BUCKET, FakeS3


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(dl, "PAUSE_S", 0.0)


class FakePMU:
    """Deux réunions (une étrangère) ; C1 trot, C2 galop ; D4 au n°1 du trot."""

    def __init__(self, missing_days=()):
        self.calls, self.missing_days = 0, set(missing_days)

    def fetch_programme(self, api):
        self.calls += 1
        if api in self.missing_days:
            return None
        return {"programme": {"reunions": [
            {"numOfficiel": 1, "pays": {"code": "FRA"}, "courses": [
                {"numOrdre": 1, "specialite": "TROT_ATTELE", "discipline": "ATTELE"},
                {"numOrdre": 2, "specialite": "PLAT", "discipline": "PLAT"}]},
            {"numOfficiel": 5, "pays": {"code": "GBR"}, "courses": [{"numOrdre": 1, "specialite": "PLAT"}]}]}}

    def fetch_participants(self, api, r, c):
        self.calls += 1
        if c == 1:
            return {"participants": [{"numPmu": 1, "deferre": "DEFERRE_ANTERIEURS_POSTERIEURS"},
                                     {"numPmu": 2, "deferre": "DEFERRE_POSTERIEURS"}, {"numPmu": 3}]}
        return {"participants": [{"numPmu": 1}, {"numPmu": 2}]}


def test_codes_et_lecture_du_flux():
    assert dl.code("DEFERRE_ANTERIEURS_POSTERIEURS") == "D4" and dl.code("DEFERRE_ANTERIEURS") == "DA"
    assert dl.code("DEFERRE_POSTERIEURS") == "DP" and dl.code("PROTEGE_ANTERIEURS") == "PROTEGE"
    assert dl.code(None) == "FERRE" and dl.code("") == "FERRE"
    rows, stats = dl.fetch_day(FakePMU(), "2026-03-01")
    assert stats["courses"] == 2 and len(rows) == 5                      # réunion étrangère ignorée
    assert stats["valeurs"]["TROT:DEFERRE_ANTERIEURS_POSTERIEURS"] == 1 and stats["valeurs"]["GALOP:ABSENT"] == 2
    fake = FakePMU()
    trot_rows, trot_stats = dl.fetch_day(fake, "2026-03-01", groups=("TROT",))
    assert trot_stats["courses"] == 1 and len(trot_rows) == 3 and fake.calls == 2   # pas de requête au galop


def test_rattrapage_incremental_et_budget(tmp_path):
    db = str(tmp_path / "d.db")
    days = ["2026-03-01", "2026-03-02", "2026-03-03", "2026-03-04"]
    ticks = iter([0, 0, 1, 2, 999, 999])                                  # budget épuisé à la 4e date
    with redirect_stdout(io.StringIO()):
        first = dl.backfill(db, FakePMU(missing_days={"02032026"}), days, budget_s=10, clock=lambda: next(ticks))
    assert first["interrompu"] and first["dates_faites"] == 2                # le 02/03 sans programme : pas marqué
    again = dl.backfill(db, FakePMU(), days, budget_s=1e9)
    assert again["dates_a_faire"] == 2 and again["dates_restantes"] == 0      # le 02/03 et le 04/03 seulement
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM journees").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM deferre").fetchone()[0] == 12   # 3 partants trot × 4 dates
    conn.close()


def test_envoi_et_relecture_sur_r2_prive(tmp_path):
    db = str(tmp_path / "d.db")
    dl.backfill(db, FakePMU(), ["2026-03-01"], budget_s=1e9)
    s3 = FakeS3()
    dl.r2_upload(s3, BUCKET, db)
    back = str(tmp_path / "back.db")
    assert dl.r2_download(s3, BUCKET, back) and dl._sha(back) == dl._sha(db)
    data, meta, etag = s3.objects[dl.DB_KEY]
    s3.objects[dl.DB_KEY] = (data + b"x", meta, etag)                        # base altérée : refus
    with pytest.raises(RuntimeError):
        dl.r2_download(s3, BUCKET, back)
    assert not dl.r2_download(FakeS3(), BUCKET, str(tmp_path / "none.db"))


@pytest.fixture(scope="module")
def mirror_with_deferre(tmp_path_factory):
    """Miroir synthétique ; au trot, le gagnant est déferré des 4 pieds une fois
    sur deux, les autres une fois sur dix : le déferrage porte de l'information."""
    d = tmp_path_factory.mktemp("def")
    path = make_mirror(str(d / "h.db"))
    rng = random.Random(5)
    conn = sqlite3.connect(path)
    parts = conn.execute("SELECT p.date_course, p.num_reunion, p.num_course, p.num_pmu, p.ordre_arrivee, c.specialite "
                         "FROM participants p JOIN courses c ON c.date_course = p.date_course AND "
                         "c.num_reunion = p.num_reunion AND c.num_course = p.num_course").fetchall()
    conn.close()
    db = str(d / "d.db")
    dconn = dl.open_db(db)
    rows = []
    for day, r, c, num, pos, spec in parts:
        if spec != "TROT_ATTELE":
            continue
        d4 = rng.random() < (0.5 if pos == 1 else 0.1)
        rows.append((day, r, c, num, "DEFERRE_ANTERIEURS_POSTERIEURS" if d4 else ""))
    dconn.executemany("INSERT INTO deferre VALUES (?, ?, ?, ?, ?)", rows)
    dconn.commit()
    dconn.close()
    return path, db


def test_premiere_fois_figee_a_la_veille():
    from turf_lab import benter_lab as lab
    races = []
    for i, day in enumerate(("2026-01-01", "2026-01-02", "2026-01-02")):
        race = lab.Race((day, 1, i + 1), day, "TROT")
        race.runners = [{"num_pmu": 1, "nom": "A", "nom_pere": "P", "nom_mere": "M"}]
        races.append(race)
    data = {("2026-01-01", 1, 1, 1): "D4", ("2026-01-02", 1, 2, 1): "D4", ("2026-01-02", 1, 3, 1): "DA"}
    cols = dl.deferre_features(races, data)["colonnes"]
    names = list(dl.DEFERRE_FEATURES)
    first, second, third = (cols[r.key][0] for r in races)
    assert first[names.index("d4_premiere_fois")] == 1.0                 # jamais vu avant
    assert second[names.index("d4_premiere_fois")] == 0.0                # déjà D4 la veille
    assert third[names.index("deferre_premiere_fois")] == 0.0            # déferré la veille (D4)


def test_bout_en_bout_le_deferrage_apporte(mirror_with_deferre):
    path, db = mirror_with_deferre
    out = io.StringIO()
    with redirect_stdout(out):
        report = dl.run_test(path, db)
    trot = report["TROT"]
    gain = trot["fondamental_plus_deferre_vs_fondamental"]
    assert trot["courses_couvertes"] > 0 and gain["delta_ll"] > 0 and gain["ic95"][0] > 0
    assert report["GALOP"]["courses_couvertes"] == 0                      # pas de déferrage au galop ici
    assert "combinaison_marche_cloture_avec_vs_sans_deferre" in trot
    logs = out.getvalue()
    assert "CHEVAL" not in logs and "JOC" not in logs
