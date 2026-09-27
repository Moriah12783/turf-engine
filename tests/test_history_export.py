"""Tests de l'export de l'historique Radar vers R2 (phase 1 du plan Benter).
Aucun réseau : une fausse source remplace le Postgres Radar, le faux client
S3 de test_r2_store remplace R2."""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab import history_export as hx
from turf_lab.history_export import (HISTORY_KEY, HISTORY_PREV_KEY, TABLES, HistoryDB, HistoryGuardError,
                                     plan_dates, pull_history, push_history, run_export, to_sqlite)
from tests.test_r2_store import BUCKET, R2_ENV, FakeS3

NUIT = datetime(2026, 9, 28, 1, 17, tzinfo=timezone.utc)
AUJOURDHUI = "2026-09-28"


def _row(table, day, i):
    """Ligne factice complète (toutes les colonnes explicites)."""
    values = []
    for col in TABLES[table]:
        if col == "date_course":
            values.append(day)
        elif col in ("id", "num_reunion", "num_course", "num_pmu"):
            values.append(i)
        else:
            values.append(f"{col}-{i}")
    return tuple(values)


class FakeSource:
    def __init__(self, data):
        self.data = data            # {table: {date: [rows]}}
        self.fetches = []
        self.closed = False

    def whoami(self):
        return {"current_user": "lecteur_benter", "courses": sum(len(r) for r in self.data.get("courses", {}).values())}

    def date_counts(self, table):
        return {d: len(rows) for d, rows in self.data.get(table, {}).items()}

    def fetch(self, table, day):
        self.fetches.append((table, day))
        return list(self.data.get(table, {}).get(day, []))

    def close(self):
        self.closed = True


def _data(days, per_day=2, tables=("courses", "participants")):
    return {t: {d: [_row(t, d, i) for i in range(per_day)] for d in days} for t in tables}


@pytest.fixture
def workdir(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.chdir(d)
        for key in R2_ENV + (hx.ENV_DSN,):
            monkeypatch.delenv(key, raising=False)
        yield d


@pytest.fixture
def env_ok(workdir, monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acc")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "k")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "s")
    monkeypatch.setenv("R2_BUCKET", BUCKET)
    monkeypatch.setenv(hx.ENV_DSN, "postgresql://lecteur_benter.x:secret@pooler:5432/postgres")
    return workdir


def _main(args, source, s3, now=NUIT):
    return hx.main(args, source_factory=lambda dsn: source, client_factory=lambda cfg: s3, now=now)


# ── Conversions ─────────────────────────────────────────────────────────
def test_conversions_postgres_vers_sqlite():
    assert to_sqlite(True) == 1 and type(to_sqlite(True)) is int
    assert to_sqlite(Decimal("4.5")) == 4.5
    assert to_sqlite(datetime(2026, 9, 1, 13, 50, tzinfo=timezone.utc)) == "2026-09-01T13:50:00+00:00"
    assert to_sqlite(datetime(2026, 9, 1).date()) == "2026-09-01"
    assert to_sqlite({"b": 1, "a": [2]}) == '{"a": [2], "b": 1}'
    assert to_sqlite(None) is None and to_sqlite(7) == 7 and to_sqlite("x") == "x"


# ── Planification ───────────────────────────────────────────────────────
def test_plan_nouvelles_dates_et_rafraichissement():
    source = {"2026-09-20": 5, "2026-09-24": 5, "2026-09-25": 5, "2026-09-27": 5, AUJOURDHUI: 5}
    exported = {"2026-09-20": 5, "2026-09-24": 5, "2026-09-25": 5}
    # 20 et 24 déjà là et inchangées ; 25 dans les 3 derniers jours ; 27 nouvelle ; aujourd'hui exclu.
    assert plan_dates(source, exported, AUJOURDHUI, 3) == ["2026-09-25", "2026-09-27"]


def test_plan_detecte_une_date_modifiee_cote_radar():
    # Reprise Radar : le 01/08 passe de 40 à 973 lignes -> réexporté sans intervention.
    assert plan_dates({"2026-08-01": 973}, {"2026-08-01": 40}, AUJOURDHUI, 3) == ["2026-08-01"]


def test_plan_plage_forcee_et_plancher():
    source = {"2025-07-17": 1, "2026-07-19": 1, "2026-07-20": 1, "2026-09-01": 1}
    exported = dict(source)
    # Plancher des rapports : rien avant le 20/07/2026 sans --start explicite.
    assert plan_dates(source, {}, AUJOURDHUI, 3, floor="2026-07-20") == ["2026-07-20", "2026-09-01"]
    # Relance après la ligne de fin de reprise : --start lève le plancher, tout est réexporté.
    assert plan_dates(source, exported, AUJOURDHUI, 3, start="2025-07-17", end="2026-07-19",
                      floor="2026-07-20") == ["2025-07-17", "2026-07-19"]


# ── Miroir local ────────────────────────────────────────────────────────
def test_replace_date_idempotent(workdir):
    db = HistoryDB("h.db")
    rows = [_row("courses", "2026-09-01", i) for i in range(3)]
    db.replace_date("courses", "2026-09-01", rows, "t1")
    db.replace_date("courses", "2026-09-01", rows[:2], "t2")
    assert db.counts()["courses"] == 2
    assert db.exported("courses") == {"2026-09-01": 2}
    assert db.coverage()["courses"] == {"premiere_date": "2026-09-01", "derniere_date": "2026-09-01", "dates": 1}
    db.close()


def test_run_export_nuit_suivante_incrementale(workdir):
    days = ["2026-09-01", "2026-09-02", "2026-09-26", "2026-09-27"]
    source = FakeSource(_data(days))
    db = HistoryDB("h.db")
    first = run_export(source, db, ["courses", "participants"], AUJOURDHUI)
    assert first["tables"]["courses"] == {"dates_prevues": 4, "dates_exportees": 4, "lignes": 8}
    source.fetches.clear()
    run_export(source, db, ["courses", "participants"], AUJOURDHUI)
    # Seuls les 3 derniers jours sont relus : 26 et 27.
    assert source.fetches == [("courses", "2026-09-26"), ("courses", "2026-09-27"),
                              ("participants", "2026-09-26"), ("participants", "2026-09-27")]
    assert db.counts()["participants"] == 8
    db.close()


def test_run_export_s_arrete_au_budget_et_reprend(workdir):
    days = [f"2026-08-{d:02d}" for d in range(1, 11)]
    source = FakeSource(_data(days, tables=("courses",)))
    db = HistoryDB("h.db")
    ticks = iter(range(100))
    report = run_export(source, db, ["courses"], AUJOURDHUI, max_seconds=3, clock=lambda: next(ticks))
    assert report["interrompu"] is True
    assert report["tables"]["courses"]["dates_exportees"] == 3
    report = run_export(source, db, ["courses"], AUJOURDHUI)
    assert report["tables"]["courses"]["dates_prevues"] == 7      # reprise là où la nuit s'est arrêtée
    assert db.counts()["courses"] == 20
    db.close()


# ── R2 ──────────────────────────────────────────────────────────────────
def test_aller_retour_r2_avec_empreinte(workdir):
    s3 = FakeS3()
    assert pull_history(s3, BUCKET, "h.db") == {"present": False, "counts": {}}
    db = HistoryDB("h.db")
    db.replace_date("courses", "2026-09-01", [_row("courses", "2026-09-01", 0)], "t")
    counts = db.counts()
    db.close()
    sent = push_history(s3, BUCKET, "h.db", {}, counts)
    assert HISTORY_PREV_KEY not in s3.objects
    push_history(s3, BUCKET, "h.db", counts, counts)
    assert HISTORY_PREV_KEY in s3.objects                               # version précédente conservée
    os.remove("h.db")
    back = pull_history(s3, BUCKET, "h.db")
    assert back["present"] and back["counts"] == counts
    assert hx.sha256_file("h.db") == sent["sha256"]


def test_empreinte_corrompue_refusee(workdir):
    s3 = FakeS3()
    HistoryDB("h.db").close()
    push_history(s3, BUCKET, "h.db", {}, {"courses": 0})
    data, meta, etag = s3.objects[HISTORY_KEY]
    s3.objects[HISTORY_KEY] = (data + b"x", meta, etag)
    with pytest.raises(HistoryGuardError, match="EMPREINTE_INVALIDE"):
        pull_history(s3, BUCKET, "h.db")


def test_chute_de_lignes_refusee(workdir):
    s3 = FakeS3()
    HistoryDB("h.db").close()
    with pytest.raises(HistoryGuardError, match="CHUTE_REFUSEE"):
        push_history(s3, BUCKET, "h.db", {"participants": 1000}, {"participants": 900})
    assert s3.puts == 0
    push_history(s3, BUCKET, "h.db", {"participants": 1000}, {"participants": 900}, allow_drop=True)
    assert s3.puts == 1


# ── CLI ─────────────────────────────────────────────────────────────────
def test_main_configuration(env_ok, monkeypatch):
    s3, source = FakeS3(), FakeSource({})
    assert _main(["run", "--tables", "courses,chevaux"], source, s3) == 2
    monkeypatch.delenv(hx.ENV_DSN)
    assert _main(["probe"], source, s3) == 2
    assert s3.puts == 0 and source.fetches == []


def test_main_probe_sans_r2(workdir, monkeypatch, capsys):
    monkeypatch.setenv(hx.ENV_DSN, "postgresql://x")
    source = FakeSource(_data(["2026-09-01"], tables=("courses",)))
    assert _main(["probe"], source, FakeS3()) == 0
    out = capsys.readouterr().out
    assert "HISTORY_PROBE_OK" in out and "lecteur_benter" in out and "postgresql://" not in out
    assert source.closed


def test_main_hors_creneau_ne_lit_rien(env_ok, capsys):
    s3, source = FakeS3(), FakeSource(_data(["2026-09-01"]))
    midi = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    assert _main(["run"], source, s3, now=midi) == 0
    assert "HISTORY_HORS_CRENEAU" in capsys.readouterr().out
    assert source.fetches == [] and s3.puts == 0


def test_main_budget_borne_par_fin_du_creneau(env_ok, monkeypatch):
    seen = {}
    real = hx.run_export

    def spy(*a, **kw):
        seen["max_seconds"] = kw["max_seconds"]
        return real(*a, **kw)

    monkeypatch.setattr(hx, "run_export", spy)
    tard = datetime(2026, 9, 28, 4, 50, tzinfo=timezone.utc)
    assert _main(["run"], FakeSource({}), FakeS3(), now=tard) == 0
    assert seen["max_seconds"] == 600                                   # 10 min restantes, pas 45


def test_main_run_complet_puis_status(env_ok, capsys):
    s3 = FakeS3()
    data = _data(["2026-09-25", "2026-09-26"], tables=tuple(TABLES))
    data["rapports_definitifs"]["2026-07-01"] = [_row("rapports_definitifs", "2026-07-01", 0)]
    source = FakeSource(data)
    assert _main(["run"], source, s3) == 0
    assert ("rapports_definitifs", "2026-07-01") not in source.fetches   # verrou reprise respecté
    assert "HISTORY_EXPORT_OK" in capsys.readouterr().out
    conn = sqlite3.connect("turf_history.db")
    assert conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0] == 4
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()
    assert _main(["status"], FakeSource({}), s3) == 0
    assert '"participants": 4' in capsys.readouterr().out


def test_main_refus_de_chute_code_3(env_ok, capsys):
    s3 = FakeS3()
    days = ["2026-09-25", "2026-09-26", "2026-09-27"]
    assert _main(["run", "--tables", "participants"], FakeSource(_data(days, per_day=100)), s3) == 0
    # Le lendemain la source ne renvoie presque plus rien (droit révoqué, table vidée…).
    assert _main(["run", "--tables", "participants"], FakeSource(_data(days, per_day=1)), s3) == 3
    assert "CHUTE_REFUSEE" in capsys.readouterr().out
    assert s3.puts == 1                                                  # miroir R2 intact


def test_main_fetch_lecture_seule(env_ok, monkeypatch, capsys):
    s3 = FakeS3()
    monkeypatch.delenv(hx.ENV_DSN)                                        # aucun accès Radar requis
    open("copie.db", "wb").write(b"copie locale du dev")
    assert _main(["fetch", "--db", "copie.db"], None, s3) == 1
    assert "HISTORY_ABSENT" in capsys.readouterr().out
    assert open("copie.db", "rb").read() == b"copie locale du dev"        # jamais effacée par fetch
    HistoryDB("h.db").close()
    push_history(s3, BUCKET, "h.db", {}, {"courses": 0})
    assert _main(["fetch", "--db", "copie.db"], None, s3) == 0
    assert "HISTORY_FETCH_OK" in capsys.readouterr().out
    assert hx.sha256_file("copie.db") == hx.sha256_file("h.db")
    assert s3.puts == 1                                                   # fetch n'écrit jamais sur R2
