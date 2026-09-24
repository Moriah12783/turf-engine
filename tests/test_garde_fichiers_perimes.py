"""Garde anti-régression de publier_vers_github.bat (incident du 23/09/2026 :
une copie périmée du workflow publiée depuis le PC a annulé la migration R2)."""
import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import garde_fichiers_perimes as garde


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def _write(repo, path, content):
    full = os.path.join(repo, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


def _read(repo, path):
    with open(os.path.join(repo, path), encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def repo():
    """Historique GitHub : workflow v1 (ancien), puis v2 (migration R2)."""
    with tempfile.TemporaryDirectory() as d:
        _git(d, "init", "-q")
        _git(d, "config", "user.email", "t@t")
        _git(d, "config", "user.name", "t")
        _write(d, ".github/workflows/daily_sync.yml", "v1 sans R2\n")
        _write(d, "turf_lab/engine.py", "moteur v1\n")
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "v1")
        _write(d, ".github/workflows/daily_sync.yml", "v2 avec R2\n")
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "migration R2")
        yield d


def test_copie_perimee_detectee_et_restauree(repo, monkeypatch):
    # Le PC a encore l'ancien workflow (jamais touché localement) + un vrai changement moteur.
    _write(repo, ".github/workflows/daily_sync.yml", "v1 sans R2\n")
    _write(repo, "turf_lab/engine.py", "moteur v2 du dev\n")
    _git(repo, "add", "-A")
    assert garde.find_stale("HEAD", cwd=repo) == [".github/workflows/daily_sync.yml"]

    monkeypatch.chdir(repo)
    assert garde.main(["garde", "HEAD"]) == 0
    assert _read(repo, ".github/workflows/daily_sync.yml") == "v2 avec R2\n"   # PC mis à jour
    staged = _git(repo, "diff", "--cached", "--name-only").split()
    assert staged == ["turf_lab/engine.py"]                                      # seul le vrai changement part


def test_vraie_modification_publiee(repo):
    _write(repo, ".github/workflows/daily_sync.yml", "v3 nouvelle version\n")
    _git(repo, "add", "-A")
    assert garde.find_stale("HEAD", cwd=repo) == []


def test_nouveau_fichier_non_concerne(repo):
    _write(repo, "docs/nouveau.md", "v1 sans R2\n")
    _git(repo, "add", "-A")
    assert garde.find_stale("HEAD", cwd=repo) == []


def test_retour_volontaire_force(repo, monkeypatch):
    _write(repo, ".github/workflows/daily_sync.yml", "v1 sans R2\n")
    _git(repo, "add", "-A")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PUBLIER_FORCER_ANCIEN", "1")
    assert garde.main(["garde", "HEAD"]) == 0
    assert _read(repo, ".github/workflows/daily_sync.yml") == "v1 sans R2\n"
