"""Tests de la répétition générale de l'ombre : fenêtre du 01 au 06/10,
copie privée distincte de la base de production, horaires jugés, diagnostic
de la chaîne sans aucune comparaison ombre / publié. Aucun réseau."""
import io
import json
import os
import sqlite3
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone

import pytest

np = pytest.importorskip("numpy")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab import fondamental_nuit, r2_store, repetition
from tests.test_benter_lab import make_mirror
from tests.test_fondamental_nuit import LAST_DAY, FakePMU, _bench
from tests.test_ombre_lecture import make_bench

JOUR_MIROIR = "2026-02-17"               # lendemain du dernier jour du miroir synthétique


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, Metadata=None, ContentType=None):
        self.objects[Key] = {"body": Body.read(), "meta": dict(Metadata or {})}

    def get_object(self, Bucket, Key):
        obj = self.objects[Key]
        return {"Body": io.BytesIO(obj["body"]), "Metadata": obj["meta"]}


def _clock(*hms, day=JOUR_MIROIR):
    times = iter(datetime.fromisoformat(f"{day}T{t}+00:00") for t in hms)
    return lambda: next(times)


def _quiet(fn, *a, **k):
    with redirect_stdout(io.StringIO()) as out:
        res = fn(*a, **k)
    return res, out.getvalue()


@pytest.fixture(scope="module")
def mirror(tmp_path_factory):
    return make_mirror(str(tmp_path_factory.mktemp("m") / "h.db"))


@pytest.fixture
def fenetre_miroir(monkeypatch):
    monkeypatch.setattr(repetition, "FENETRE", (JOUR_MIROIR, "2026-02-22"))
    monkeypatch.setattr(fondamental_nuit, "PAUSE_S", 0.0)


def test_cle_privee_distincte_de_la_production():
    assert repetition.REPETITION_KEY != r2_store.DB_KEY and repetition.REPETITION_KEY.startswith("lab/repetition/")
    assert repetition.FENETRE == ("2026-10-01", "2026-10-06")


def test_nuit_refusee_hors_fenetre(tmp_path):
    s3 = FakeS3()
    after, _ = _quiet(repetition.nuit, "h.db", str(tmp_path / "c.db"), None, s3, "b",
                      horloge=_clock("05:05:00", day="2026-10-07"))
    before, _ = _quiet(repetition.nuit, "h.db", str(tmp_path / "c.db"), None, s3, "b",
                       horloge=_clock("05:05:00", day="2026-09-30"))
    late_ctrl, _ = _quiet(repetition.nuit, "h.db", str(tmp_path / "c.db"), None, s3, "b", controle=True,
                          horloge=_clock("05:05:00", day="2026-10-07"))
    assert after["refus"] == before["refus"] == late_ctrl["refus"] == "HORS_FENETRE" and s3.objects == {}


def test_nuit_ecrit_la_copie_sur_la_cle_de_repetition(mirror, tmp_path, fenetre_miroir):
    s3 = FakeS3()
    copie = _bench(str(tmp_path / "copie.db"))
    rep, logs = _quiet(repetition.nuit, mirror, copie, FakePMU(mirror, LAST_DAY, JOUR_MIROIR), s3, "b",
                       prevu="05:05", horloge=_clock("05:06:00", "05:07:30", "05:07:40"))
    assert rep["verdict"] == "OK" and rep["ecrit"] and rep["retard_declenchement_min"] == 1.0
    assert rep["calcul_avant_06h20"] and rep["envoi_avant_06h28"]
    assert list(s3.objects) == [repetition.REPETITION_KEY]                  # jamais la base de production
    meta = s3.objects[repetition.REPETITION_KEY]["meta"]
    assert meta["sha256"] == r2_store.sha256_file(copie) and meta["jour"] == JOUR_MIROIR
    assert "REPETITION_NUIT " in logs
    dest = str(tmp_path / "dev.db")
    info, _ = _quiet(repetition.fetch, s3, "b", dest)
    assert info["sha256"] == meta["sha256"] and r2_store.sha256_file(dest) == meta["sha256"]
    s3.objects[repetition.REPETITION_KEY]["meta"]["sha256"] = "0" * 64
    with pytest.raises(repetition.RepetitionRefus):
        repetition.fetch(s3, "b", str(tmp_path / "dev2.db"))


def test_nuit_hors_delai_a_expliquer(mirror, tmp_path, fenetre_miroir):
    """Lancement en retard (horaire GitHub) : calcul jugé, mais rien n'est
    envoyé ; la copie du matin reste en place (cas du 02/10 à 10h56)."""
    s3 = FakeS3()
    s3.objects[repetition.REPETITION_KEY] = {"body": b"copie de 05h05", "meta": {"sha256": "x"}}
    rep, _ = _quiet(repetition.nuit, mirror, _bench(str(tmp_path / "c.db")), FakePMU(mirror, LAST_DAY, JOUR_MIROIR),
                    s3, "b", prevu="05:05", horloge=_clock("10:56:00", "10:57:00"))
    assert rep["retard_declenchement_min"] == 351.0 and not rep["calcul_avant_06h20"]
    assert rep["envoi"] == "SANS_ENVOI_HORS_DELAI" and rep["verdict"] == "A_EXPLIQUER" and "sha256" not in rep
    assert s3.objects[repetition.REPETITION_KEY]["body"] == b"copie de 05h05"


def test_envoi_apres_06h28_a_expliquer(mirror, tmp_path, fenetre_miroir):
    s3 = FakeS3()
    rep, _ = _quiet(repetition.nuit, mirror, _bench(str(tmp_path / "c.db")), FakePMU(mirror, LAST_DAY, JOUR_MIROIR),
                    s3, "b", prevu="05:50", horloge=_clock("06:05:00", "06:19:00", "06:29:00"))
    assert rep["calcul_avant_06h20"] and not rep["envoi_avant_06h28"] and rep["verdict"] == "A_EXPLIQUER"
    assert list(s3.objects) == [repetition.REPETITION_KEY]


def test_controle_pendant_la_fenetre_n_envoie_rien(mirror, tmp_path, monkeypatch):
    """Une PR pendant la répétition ne remplace jamais la copie du matin."""
    monkeypatch.setattr(repetition, "FENETRE", (LAST_DAY, "2026-02-22"))
    monkeypatch.setattr(fondamental_nuit, "PAUSE_S", 0.0)
    s3 = FakeS3()
    rep, _ = _quiet(repetition.nuit, mirror, _bench(str(tmp_path / "c.db")), FakePMU(mirror, LAST_DAY, JOUR_MIROIR),
                    s3, "b", controle=True, horloge=_clock("11:00:00", "11:01:00", day=LAST_DAY))
    assert rep["jour"] == JOUR_MIROIR and rep["ecrit"] and rep["envoi"] == "SANS_ENVOI_CONTROLE"
    assert rep["verdict"] == "OK" and s3.objects == {}


def _add_fundamental(path, version="fond-v1", create=True):
    """Table fundamental_probs tirée de la clé ``fondamental`` des archives."""
    conn = sqlite3.connect(path)
    if create:
        conn.execute("CREATE TABLE fundamental_probs (race_id TEXT, num INTEGER, p REAL, model_version TEXT, "
                     "computed_at TEXT, train_until TEXT)")
    for race_id, meta in conn.execute("SELECT race_id, metadata_json FROM predictions").fetchall():
        arch = json.loads(meta).get("ombre_fondamental") or {}
        fond = arch.get("fondamental") or {str(n): 0.1 for n in range(1, 11)}
        total = sum(fond.values())
        for num, p in fond.items():
            conn.execute("INSERT INTO fundamental_probs VALUES (?, ?, ?, ?, 'x', 'x')",
                         (race_id, int(num), p / total, version))
    conn.commit()
    conn.close()


def test_diagnostic_de_la_chaine(tmp_path, monkeypatch):
    monkeypatch.setattr(repetition, "FENETRE", ("2026-10-07", "2026-10-08"))
    monkeypatch.setattr(repetition, "_now", lambda: datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc))
    ok_path = make_bench(str(tmp_path / "ok.db"), 30)
    _add_fundamental(ok_path)
    ok, logs = _quiet(repetition.diagnostic, ok_path, "2026-10-07")
    assert ok["verdict"] == "OK" and ok["editions_eligibles"] == 30 and ok["avec_ombre_complete"] == 30
    assert ok["versions_fondamental"] == ["fond-v1"] and ok["ecart_somme_max"] < 1e-9
    assert "delta" not in logs and "ecart\"" not in logs                    # aucune comparaison ombre / publié
    trou = make_bench(str(tmp_path / "trou.db"), 30, missing_every=5)
    _add_fundamental(trou)
    ko, _ = _quiet(repetition.diagnostic, trou, "2026-10-07")
    assert ko["archive_absente"] == 6 and ko["verdict"] == "A_EXPLIQUER"
    hors, _ = _quiet(repetition.diagnostic, ok_path, "2026-10-09")
    assert hors["refus"] == "HORS_FENETRE"
    # Cas relevé par le dev NVE : une course qui somme à 1,2 et deux versions.
    conn = sqlite3.connect(ok_path)
    race = conn.execute("SELECT race_id FROM fundamental_probs LIMIT 1").fetchone()[0]
    conn.execute("UPDATE fundamental_probs SET p = p * 1.2 WHERE race_id = ?", (race,))
    conn.commit()
    conn.close()
    somme, _ = _quiet(repetition.diagnostic, ok_path, "2026-10-07")
    assert somme["ecart_somme_max"] > 0.19 and somme["verdict"] == "A_EXPLIQUER"
    deux = make_bench(str(tmp_path / "deux.db"), 30)
    _add_fundamental(deux)
    _add_fundamental(deux, version="fond-v0", create=False)
    v2, _ = _quiet(repetition.diagnostic, deux, "2026-10-07")
    assert v2["versions_fondamental"] == ["fond-v0", "fond-v1"] and v2["verdict"] == "A_EXPLIQUER"

