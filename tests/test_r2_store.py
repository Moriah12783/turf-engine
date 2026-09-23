"""Tests de la persistance R2 (étape 1 de la sortie de GitHub). Aucun réseau :
un faux client S3 en mémoire remplace R2 ; un test optionnel rejoue les flux
avec boto3 + moto s'ils sont installés."""
import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab import r2_store
from turf_lab.r2_store import (BACKUP_PREFIX, DB_KEY, SEED_MARKER_KEY, R2ConfigError, R2GuardError,
                               fetch, pull, push, r2_config)

BUCKET = "turf-engine-data"
NOW = "2026-09-23T10:00:00Z"
R2_ENV = ("R2_ACCOUNT_ID", "CLOUDFLARE_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")


class _NotFound(Exception):
    def __init__(self):
        super().__init__("404")
        self.response = {"Error": {"Code": "404"}}


class FakeS3:
    """Sous-ensemble de l'API S3 utilisé par r2_store, en mémoire."""

    def __init__(self):
        self.objects = {}  # key -> (bytes, metadata, etag)
        self.puts = 0

    def _get(self, key):
        if key not in self.objects:
            raise _NotFound()
        return self.objects[key]

    def head_object(self, Bucket, Key):
        data, meta, etag = self._get(Key)
        return {"ETag": etag, "Metadata": dict(meta), "ContentLength": len(data)}

    def get_object(self, Bucket, Key):
        data, meta, etag = self._get(Key)
        return {"Body": io.BytesIO(data), "ETag": etag, "Metadata": dict(meta)}

    def put_object(self, Bucket, Key, Body, Metadata=None, ContentType=None):
        data = Body.read() if hasattr(Body, "read") else Body
        self.objects[Key] = (data, dict(Metadata or {}), '"%s"' % hashlib.md5(data).hexdigest())
        self.puts += 1

    def copy_object(self, Bucket, Key, CopySource):
        self.objects[Key] = self._get(CopySource["Key"])


def _make_db(path, races=3, predictions=6, results=2, snapshots=10):
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE races (race_id TEXT PRIMARY KEY);
        CREATE TABLE predictions (prediction_id TEXT PRIMARY KEY);
        CREATE TABLE race_results (race_id TEXT PRIMARY KEY);
        CREATE TABLE odds_snapshots (race_id TEXT, num INTEGER);
        CREATE TABLE runners (runner_id TEXT PRIMARY KEY);
        CREATE TABLE rapports (rapport_id TEXT PRIMARY KEY);
    """)
    conn.executemany("INSERT INTO races VALUES (?)", [(f"R{i}",) for i in range(races)])
    conn.executemany("INSERT INTO predictions VALUES (?)", [(f"P{i}",) for i in range(predictions)])
    conn.executemany("INSERT INTO race_results VALUES (?)", [(f"R{i}",) for i in range(results)])
    conn.executemany("INSERT INTO odds_snapshots VALUES (?, ?)", [("R0", i) for i in range(snapshots)])
    conn.commit()
    conn.close()
    return path


def _add_prediction(path, pid):
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO predictions VALUES (?)", (pid,))
    conn.commit()
    conn.close()


@pytest.fixture
def workdir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def _seeded(workdir):
    """Bucket amorcé depuis une base locale de 3 courses / 6 éditions."""
    s3 = FakeS3()
    db = _make_db(os.path.join(workdir, "turf_bench.db"))
    pull(s3, BUCKET, db, now=NOW)
    push(s3, BUCKET, db, now=NOW, env={})
    return s3, db


# ── Configuration ───────────────────────────────────────────────────────
def test_aucun_secret_mode_legacy():
    assert r2_config({}) is None


def test_configuration_partielle_refusee():
    with pytest.raises(R2ConfigError):
        r2_config({"R2_BUCKET": BUCKET})


def test_compte_cloudflare_existant_reutilise():
    cfg = r2_config({"CLOUDFLARE_ACCOUNT_ID": "acc", "R2_ACCESS_KEY_ID": "k",
                     "R2_SECRET_ACCESS_KEY": "s", "R2_BUCKET": BUCKET})
    assert cfg == {"account_id": "acc", "key_id": "k", "secret": "s", "bucket": BUCKET}


# ── Amorçage unique ─────────────────────────────────────────────────────
def test_amorcage_depuis_copie_git(workdir):
    s3, db = _seeded(workdir)
    assert set(s3.objects) == {DB_KEY, SEED_MARKER_KEY, f"{BACKUP_PREFIX}2026-09-23.db"}
    data, meta, _ = s3.objects[DB_KEY]
    assert meta["sha256"] == hashlib.sha256(data).hexdigest() == r2_store.sha256_file(db)
    assert json.loads(meta["counts"])["predictions"] == 6
    marker = json.loads(s3.objects[SEED_MARKER_KEY][0])
    assert marker["sha256"] == meta["sha256"]


def test_base_absente_apres_amorcage_refusee(workdir):
    s3, db = _seeded(workdir)
    del s3.objects[DB_KEY]
    before = r2_store.sha256_file(db)
    with pytest.raises(R2GuardError, match="BASE_ABSENTE_APRES_AMORCAGE"):
        pull(s3, BUCKET, db, now=NOW)
    assert r2_store.sha256_file(db) == before


def test_amorcage_sans_copie_locale_refuse(workdir):
    with pytest.raises(R2GuardError, match="AMORCAGE_IMPOSSIBLE"):
        pull(FakeS3(), BUCKET, os.path.join(workdir, "absente.db"), now=NOW)


# ── Passes normales ─────────────────────────────────────────────────────
def test_pull_remplace_la_copie_git_perimee(workdir):
    s3, db = _seeded(workdir)
    _add_prediction(db, "P_R2")                       # la passe ajoute une édition
    pull_state = json.load(open(db + ".r2state.json"))
    assert pull_state["mode"] == "r2"
    push(s3, BUCKET, db, now=NOW, env={})
    _make_db(db, predictions=2)                        # checkout suivant : copie Git périmée
    state = pull(s3, BUCKET, db, now=NOW)
    assert state["mode"] == "r2"
    assert r2_store.table_counts(db)["predictions"] == 7


def test_base_inchangee_pas_de_reecriture(workdir):
    s3, db = _seeded(workdir)
    pull(s3, BUCKET, db, now=NOW)
    puts = s3.puts
    del s3.objects[f"{BACKUP_PREFIX}2026-09-23.db"]
    result = push(s3, BUCKET, db, now=NOW, env={})
    assert result["uploaded"] is False and s3.puts == puts
    assert f"{BACKUP_PREFIX}2026-09-23.db" in s3.objects


def test_sauvegarde_datee_par_jour(workdir):
    s3, db = _seeded(workdir)
    pull(s3, BUCKET, db, now="2026-09-24T09:00:00Z")
    _add_prediction(db, "P_J2")
    push(s3, BUCKET, db, now="2026-09-24T09:05:00Z", env={"GITHUB_RUN_ID": "42"})
    assert f"{BACKUP_PREFIX}2026-09-23.db" in s3.objects
    assert f"{BACKUP_PREFIX}2026-09-24.db" in s3.objects
    assert s3.objects[DB_KEY][1]["run-id"] == "42"


# ── Garde-fous ──────────────────────────────────────────────────────────
def test_retrecissement_refuse_puis_autorise(workdir):
    s3, db = _seeded(workdir)
    pull(s3, BUCKET, db, now=NOW)
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM predictions WHERE prediction_id = 'P0'")
    conn.commit()
    conn.close()
    with pytest.raises(R2GuardError, match="RETRECISSEMENT_REFUSE"):
        push(s3, BUCKET, db, now=NOW, env={})
    assert json.loads(s3.objects[DB_KEY][1]["counts"])["predictions"] == 6
    push(s3, BUCKET, db, now=NOW, env={}, allow_shrink=True)
    assert json.loads(s3.objects[DB_KEY][1]["counts"])["predictions"] == 5


def test_ecriture_concurrente_refusee(workdir):
    s3, db = _seeded(workdir)
    pull(s3, BUCKET, db, now=NOW)
    other = _make_db(os.path.join(workdir, "autre.db"), predictions=9)
    with open(other, "rb") as f:
        s3.put_object(Bucket=BUCKET, Key=DB_KEY, Body=f, Metadata={})
    _add_prediction(db, "P_X")
    with pytest.raises(R2GuardError, match="BASE_DISTANTE_MODIFIEE"):
        push(s3, BUCKET, db, now=NOW, env={})


def test_telechargement_corrompu_refuse_copie_locale_intacte(workdir):
    s3, db = _seeded(workdir)
    data, meta, etag = s3.objects[DB_KEY]
    s3.objects[DB_KEY] = (data[:-10] + b"x" * 10, meta, etag)
    before = r2_store.sha256_file(db)
    with pytest.raises(R2GuardError, match="EMPREINTE_INVALIDE"):
        pull(s3, BUCKET, db, now=NOW)
    assert r2_store.sha256_file(db) == before
    assert not os.path.exists(db + ".r2download")


def test_push_sans_pull_refuse(workdir):
    db = _make_db(os.path.join(workdir, "turf_bench.db"))
    with pytest.raises(R2GuardError, match="ETAT_PULL_ABSENT"):
        push(FakeS3(), BUCKET, db, now=NOW, env={})


def test_base_vide_refusee(workdir):
    db = _make_db(os.path.join(workdir, "turf_bench.db"), races=0)
    with pytest.raises(R2GuardError, match="BASE_VIDE"):
        pull(FakeS3(), BUCKET, db, now=NOW)


def test_fetch_lecture_seule(workdir):
    s3, _ = _seeded(workdir)
    puts = s3.puts
    copie = os.path.join(workdir, "copie.db")
    info = fetch(s3, BUCKET, copie)
    assert info["sha256_verifie"] and r2_store.table_counts(copie)["predictions"] == 6
    assert s3.puts == puts


# ── CLI (workflow GitHub) ───────────────────────────────────────────────
def _clean_env(monkeypatch, workdir):
    for name in R2_ENV:
        monkeypatch.delenv(name, raising=False)
    github_env = os.path.join(workdir, "github_env")
    monkeypatch.setenv("GITHUB_ENV", github_env)
    return github_env


def test_cli_sans_secret_mode_legacy(monkeypatch, workdir):
    github_env = _clean_env(monkeypatch, workdir)
    assert r2_store.main(["pull", "--db", os.path.join(workdir, "x.db")]) == 0
    assert open(github_env).read() == "R2_MODE=legacy\n"


def test_cli_configuration_partielle_echec(monkeypatch, workdir):
    _clean_env(monkeypatch, workdir)
    monkeypatch.setenv("R2_BUCKET", BUCKET)
    assert r2_store.main(["pull"]) == 2


def test_cli_amorcage_puis_mode_r2(monkeypatch, workdir):
    github_env = _clean_env(monkeypatch, workdir)
    for name, value in (("CLOUDFLARE_ACCOUNT_ID", "acc"), ("R2_ACCESS_KEY_ID", "k"),
                        ("R2_SECRET_ACCESS_KEY", "s"), ("R2_BUCKET", BUCKET)):
        monkeypatch.setenv(name, value)
    s3 = FakeS3()
    db = _make_db(os.path.join(workdir, "turf_bench.db"))
    assert r2_store.main(["pull", "--db", db], client_factory=lambda cfg: s3) == 0
    assert r2_store.main(["push", "--db", db], client_factory=lambda cfg: s3) == 0
    assert open(github_env).read() == "R2_MODE=r2\n"
    assert SEED_MARKER_KEY in s3.objects
    del s3.objects[DB_KEY]
    assert r2_store.main(["pull", "--db", db], client_factory=lambda cfg: s3) == 3


# ── Validation optionnelle avec le vrai boto3 (moto) ───────────────────
def test_flux_complet_boto3_moto(workdir):
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    cfg = {"account_id": "acc", "key_id": "k", "secret": "s", "bucket": BUCKET}
    client = r2_store.make_client(cfg)
    assert client.meta.endpoint_url == "https://acc.r2.cloudflarestorage.com"
    with moto.mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1", aws_access_key_id="k",
                          aws_secret_access_key="s", config=r2_store.client_config())
        s3.create_bucket(Bucket=BUCKET)
        db = _make_db(os.path.join(workdir, "turf_bench.db"))
        assert pull(s3, BUCKET, db, now=NOW)["mode"] == "seed"
        push(s3, BUCKET, db, now=NOW, env={})
        _add_prediction(db, "P_MOTO")
        assert pull(s3, BUCKET, db, now=NOW)["mode"] == "r2"
        assert r2_store.table_counts(db)["predictions"] == 6   # la base R2 fait foi
        _add_prediction(db, "P_MOTO")
        assert push(s3, BUCKET, db, now=NOW, env={})["uploaded"] is True
        assert r2_store.status(s3, BUCKET)["counts"]["predictions"] == 7
        keys = {o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"]}
        assert keys == {DB_KEY, SEED_MARKER_KEY, f"{BACKUP_PREFIX}2026-09-23.db"}
