"""Plancher de l'édition du matin : source unique (décision de Steph du 12/11/2026).

06h30 UTC jusqu'au 22/11/2026, 08h30 heure de Paris à partir du 23/11/2026.
Tests demandés par le dev Radar : hiver, été, changements d'heure, bascule,
course précoce, fuseau machine forcé, repli sans tzdata, concordance de tous
les consommateurs, aucune heure en dur, refus par défaut du workflow de nuit.
Tous les instants sont injectés : aucune dépendance à l'heure réelle.
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab import fondamental_nuit, plancher_matin as pm, publication_gate as pg
from turf_lab.daily_sync import DailySyncManager
from turf_lab.database import TurfDatabase

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Jour → plancher attendu (UTC sans fuseau, HH:MM).
ATTENDU = {
    "2026-11-12": "06:30",   # avant la bascule (hiver) : ancienne règle
    "2026-11-22": "06:30",   # veille de la bascule
    "2026-11-23": "07:30",   # bascule : 08h30 à Paris, hiver
    "2027-01-15": "07:30",   # hiver
    "2027-03-27": "07:30",   # veille du passage à l'heure d'été
    "2027-03-28": "06:30",   # passage à l'heure d'été (01h00 UTC) : 08h30 à Paris = 06h30 UTC
    "2027-07-15": "06:30",   # été
    "2027-10-30": "06:30",   # veille du passage à l'heure d'hiver
    "2027-10-31": "07:30",   # passage à l'heure d'hiver (01h00 UTC) : 08h30 à Paris = 07h30 UTC
}


def _hm(dt):
    return dt.strftime("%H:%M")


def _a(jour, hm):
    h, m = (int(x) for x in hm.split(":"))
    d = date.fromisoformat(jour)
    return datetime(d.year, d.month, d.day, h, m)


# ── Le plancher lui-même ─────────────────────────────────────────────────

@pytest.mark.parametrize("jour,hm", sorted(ATTENDU.items()))
def test_plancher_par_jour(jour, hm):
    p = pm.plancher_matin_utc(jour)
    assert p.tzinfo is None and _hm(p) == hm and p.date() == date.fromisoformat(jour)
    assert pm.limite_ecriture_nuit_utc(jour) == p - timedelta(minutes=10)
    assert pm.limite_envoi_utc(jour) == p - timedelta(minutes=2)
    assert pm.plancher_matin_utc(date.fromisoformat(jour)) == p == pm.plancher_matin_utc(_a(jour, "12:00"))


def test_heure_de_paris_et_libelle():
    assert pm.heure_paris_du_plancher("2026-11-22") == "07:30"      # ancienne règle : 06h30 UTC = 07h30 à Paris
    for jour in ("2026-11-23", "2027-03-28", "2027-07-15", "2027-10-31"):
        assert pm.heure_paris_du_plancher(jour) == "08:30"
    assert pm.libelle_plancher("2026-11-22") == "06h30 GMT"
    assert pm.libelle_plancher("2026-11-23") == "08h30 heure de Paris (07h30 GMT)"
    assert pm.libelle_plancher("2027-07-15") == "08h30 heure de Paris (06h30 GMT)"


def test_date_illisible():
    with pytest.raises(ValueError):
        pm.plancher_matin_utc("13/11/2026")
    assert pg._floor_datetime("") is None and pg._floor_datetime("pas une date") is None


def test_repli_regle_europeenne_identique_a_zoneinfo(monkeypatch):
    """Sans tzdata (Windows), la règle européenne donne le même plancher que
    zoneinfo, chaque jour de 2026 à 2040."""
    zone = pm._zone()
    if zone is None:
        pytest.skip("zoneinfo indisponible sur cette machine : seul le repli est testable")
    jours = [date(2026, 1, 1) + timedelta(days=k) for k in range((date(2040, 12, 31) - date(2026, 1, 1)).days + 1)]
    avec = {j: pm._plancher_paris_utc(j, zone) for j in jours}
    sans = {j: pm._plancher_paris_utc(j, None) for j in jours}
    assert avec == sans
    monkeypatch.setattr(pm, "_ZONE", None)
    assert pm.source_fuseau() == "regle_ue"
    for jour, hm in ATTENDU.items():
        assert _hm(pm.plancher_matin_utc(jour)) == hm
        assert pm.heure_paris_du_plancher(jour) == ("07:30" if jour < "2026-11-23" else "08:30")


def test_derniers_dimanches():
    assert pm._dernier_dimanche(2026, 3) == date(2026, 3, 29) and pm._dernier_dimanche(2026, 10) == date(2026, 10, 25)
    assert pm._dernier_dimanche(2027, 3) == date(2027, 3, 28) and pm._dernier_dimanche(2027, 10) == date(2027, 10, 31)
    assert pm._dernier_dimanche(2028, 12) == date(2028, 12, 31)


def test_fuseau_de_la_machine_sans_effet(monkeypatch):
    """Le fuseau local de la machine ne change rien (aucun astimezone() sur une
    heure sans fuseau)."""
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset indisponible (Windows)")
    try:
        for tz in ("America/New_York", "Asia/Tokyo", "Africa/Abidjan"):
            monkeypatch.setenv("TZ", tz)
            time.tzset()
            for jour, hm in ATTENDU.items():
                assert _hm(pm.plancher_matin_utc(jour)) == hm
                assert pm.en_utc_naif(_a(jour, hm)) == _a(jour, hm)
            ny = datetime(2026, 11, 23, 2, 30, tzinfo=timezone(timedelta(hours=-5)))
            assert pm.en_utc_naif(ny) == datetime(2026, 11, 23, 7, 30)
            sortie = subprocess.run([sys.executable, "-m", "turf_lab.plancher_matin", "envoi",
                                     "--maintenant", "2026-11-23T07:27:00"],
                                    cwd=RACINE, capture_output=True, text=True, env={**os.environ, "TZ": tz})
            assert sortie.returncode == 0 and sortie.stdout.startswith("ENVOI_AUTORISE ")
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


# ── Ligne de commande : refus par défaut ─────────────────────────────────

@pytest.mark.parametrize("action,instant,code,mot", [
    ("ecriture", "2026-11-22T06:19:00", 0, "ECRITURE_AUTORISEE"),
    ("ecriture", "2026-11-22T06:20:00", 1, "ECRITURE_REFUSEE"),
    ("ecriture", "2026-11-23T07:19:00", 0, "ECRITURE_AUTORISEE"),
    ("ecriture", "2026-11-23T07:20:00", 1, "ECRITURE_REFUSEE"),
    ("envoi", "2026-11-22T06:27:59", 0, "ENVOI_AUTORISE"),
    ("envoi", "2026-11-22T06:28:00", 1, "ENVOI_REFUSE"),
    ("envoi", "2026-11-23T07:27:59", 0, "ENVOI_AUTORISE"),
    ("envoi", "2026-11-23T07:28:00", 1, "ENVOI_REFUSE"),
    ("envoi", "2027-07-15T06:28:00", 1, "ENVOI_REFUSE"),
    ("envoi", "2026-11-23T08:27:00+01:00", 0, "ENVOI_AUTORISE"),   # 07h27 UTC, écrit avec fuseau
    ("envoi", "illisible", 2, "PLANCHER_ERREUR"),
])
def test_ligne_de_commande(action, instant, code, mot):
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert pm.main([action, "--maintenant", instant]) == code
    assert buf.getvalue().startswith(mot + " ")


def test_journal_plancher_matin():
    buf = io.StringIO()
    with redirect_stdout(buf):
        pm.journal(datetime(2026, 11, 23, 0, 5))
    ligne = buf.getvalue().strip()
    assert ligne.startswith("PLANCHER_MATIN {")
    for cle in ('"jour": "2026-11-23"', '"plancher_utc": "07:30"', '"plancher_paris": "08:30"',
                '"limite_ecriture_utc": "07:20"', '"limite_envoi_utc": "07:28"', '"regle": "08h30_PARIS"'):
        assert cle in ligne
    main_py = open(os.path.join(RACINE, "main.py"), encoding="utf-8").read()
    branche_sync = main_py[main_py.index('elif action == "sync":'):main_py.index('elif action == "verify":')]
    assert "plancher_matin.journal()" in branche_sync            # une ligne à chaque passage


# ── Concordance de tous les consommateurs ────────────────────────────────

def _race(jour, depart="14:00", rid=None):
    d = date.fromisoformat(jour)
    rid = rid or f"R1C1_{d:%d%m%Y}_TESTVILLE"
    return {
        "race_id": rid, "date": jour, "meeting_number": 1, "race_number": 1,
        "name": "Prix Test", "hippodrome": "TESTVILLE", "discipline": "TROT_ATTELE",
        "distance": 2700, "track_type": "SABLE", "track_condition": "BON", "rope": "GAUCHE",
        "autostart": False, "scheduled_start_time": f"{depart} GMT", "status": "SCHEDULED",
    }


def _runners(n=10):
    return [{
        "num": i, "horse_name": f"Cheval{i}", "sex": "M", "age": 5, "driver_jockey": "J.TEST",
        "trainer": "T.TEST", "weight": 60.0, "draw": i, "shoeing": "FERRE", "blinkers": "SANS",
        "morning_odds": 4.0 + i, "odds_t15": 4.0 + i, "final_odds": 4.0 + i, "is_non_partant": False,
        "press_citation_count": 0, "music": "1a2a3a", "earnings": 50000.0,
        "record_chrono": 0.0, "official_rating": 0.0,
    } for i in range(1, n + 1)]


def _setup(jour, depart="14:00"):
    db = TurfDatabase(os.path.join(tempfile.mkdtemp(), "plancher.db"))
    mgr = DailySyncManager(db)
    race, runners = _race(jour, depart), _runners()
    db.save_race(race)
    db.save_runners(race["race_id"], runners)
    return db, mgr, race, runners


@pytest.mark.parametrize("jour", sorted(ATTENDU))
def test_concordance_des_consommateurs(jour):
    """daily_sync, la règle de diffusion, le calcul de nuit et la ligne de
    commande du workflow basculent tous à la même minute."""
    p = pm.plancher_matin_utc(jour)
    une_min = timedelta(minutes=1)
    with redirect_stdout(io.StringIO()):
        db, mgr, race, runners = _setup(jour)
        # daily_sync : T_MATIN dû à partir du plancher, pas une minute avant
        assert "T_MATIN" not in mgr.due_horizons(race["scheduled_start_time"], jour, p - une_min)
        assert "T_MATIN" in mgr.due_horizons(race["scheduled_start_time"], jour, p)
        # Règle de diffusion : même plancher
        assert pg._floor_datetime(jour) == p
        assert pg.can_publish(db, race["race_id"], "T_MATIN", now_utc=p - une_min, log=False) == (
            False, "BEFORE_MORNING_FLOOR")
        assert mgr._lock_horizon(race, runners, "T_MATIN", now_utc=p) == 1
        assert pg.can_publish(db, race["race_id"], "T_MATIN", now_utc=p + une_min, log=False) == (True, "OK")
        # Calcul de nuit : plus rien après plancher − 10 min
        limite = p - timedelta(minutes=10)
        assert not fondamental_nuit._too_late(limite - une_min, jour)
        assert fondamental_nuit._too_late(limite, jour)
        assert fondamental_nuit._too_late(limite.replace(tzinfo=timezone.utc), jour)       # heure avec fuseau
        # Ligne de commande du workflow : envoi refusé à partir de plancher − 2 min
        assert pm.main(["envoi", "--maintenant", (p - timedelta(minutes=3)).isoformat()]) == 0
        assert pm.main(["envoi", "--maintenant", (p - timedelta(minutes=2)).isoformat()]) == 1


def test_heure_avec_fuseau_ne_casse_pas_la_passe():
    """Une heure avec fuseau donne le même résultat qu'une heure UTC sans fuseau."""
    jour = "2026-11-23"
    with redirect_stdout(io.StringIO()):
        db, mgr, race, runners = _setup(jour)
        for naif in (datetime(2026, 11, 23, 7, 29), datetime(2026, 11, 23, 7, 30)):
            aware = naif.replace(tzinfo=timezone.utc)
            assert mgr.due_horizons(race["scheduled_start_time"], jour, aware) == \
                mgr.due_horizons(race["scheduled_start_time"], jour, naif)
        assert pg.can_publish(db, race["race_id"], "T_MATIN",
                              now_utc=datetime(2026, 11, 23, 8, 29, tzinfo=timezone(timedelta(hours=1))),
                              log=False) == (False, "BEFORE_MORNING_FLOOR")


def test_bascule_veille_et_jour():
    """22/11 : T_MATIN à 06h30 UTC. 23/11 : T_MATIN à 07h30 UTC (08h30 à Paris)."""
    with redirect_stdout(io.StringIO()):
        _, mgr, race22, _ = _setup("2026-11-22")
        _, mgr23, race23, _ = _setup("2026-11-23")
    assert "T_MATIN" in mgr.due_horizons(race22["scheduled_start_time"], "2026-11-22", datetime(2026, 11, 22, 6, 30))
    assert "T_MATIN" not in mgr23.due_horizons(race23["scheduled_start_time"], "2026-11-23",
                                               datetime(2026, 11, 23, 6, 30))
    assert "T_MATIN" not in mgr23.due_horizons(race23["scheduled_start_time"], "2026-11-23",
                                               datetime(2026, 11, 23, 7, 29))
    assert "T_MATIN" in mgr23.due_horizons(race23["scheduled_start_time"], "2026-11-23",
                                           datetime(2026, 11, 23, 7, 30))


def test_course_precoce_en_hiver():
    """Hiver : une course qui part avant 10h10 à Paris (plancher + 100 min,
    fenêtre T90) reçoit T_MATIN avec T90 avant le plancher. Ce T_MATIN reste
    refusé à la diffusion, durablement, comme aujourd'hui avec 06h30."""
    jour = "2026-11-23"
    with redirect_stdout(io.StringIO()):
        db, mgr, race, runners = _setup(jour, depart="09:09")        # 10h09 à Paris
        avant = datetime(2026, 11, 23, 7, 29)                       # une minute avant le plancher
        assert mgr.due_horizons(race["scheduled_start_time"], jour, avant) == ["T_MATIN", "T90"]
        assert mgr._lock_horizon(race, runners, "T_MATIN", now_utc=avant) == 1
        for plus_tard in (datetime(2026, 11, 23, 7, 45), datetime(2026, 11, 23, 9, 0)):
            assert pg.can_publish(db, race["race_id"], "T_MATIN", now_utc=plus_tard, log=False) == (
                False, "BEFORE_MORNING_FLOOR")
        buf = io.StringIO()
        with redirect_stdout(buf):
            pg.can_publish(db, race["race_id"], "T_MATIN", now_utc=datetime(2026, 11, 23, 8, 0), log=True)
        assert "BEFORE_MORNING_FLOOR" in buf.getvalue() and '"plancher_utc": "2026-11-23T07:30:00"' in buf.getvalue()
        # 10h10 à Paris : hors de la fenêtre T90 une minute avant le plancher → rien avant le plancher
        _, mgr2, race2, _ = _setup(jour, depart="09:10")
        assert mgr2.due_horizons(race2["scheduled_start_time"], jour, avant) == []
        assert mgr2.due_horizons(race2["scheduled_start_time"], jour, datetime(2026, 11, 23, 7, 30)) == [
            "T_MATIN", "T90"]


# ── Aucune heure en dur chez les consommateurs ───────────────────────────

# repetition.py (répétition du 01 au 06/10/2026, inerte depuis) lit aussi ses
# limites dans plancher_matin.py, mais garde ses noms de clés d'archive
# (calcul_avant_06h20, envoi_avant_06h28) : il n'est pas balayé ici.
CONSOMMATEURS = ["turf_lab/daily_sync.py", "turf_lab/publication_gate.py", "turf_lab/fondamental_nuit.py",
                 "turf_lab/html_report.py", "main.py", ".github/workflows/fondamental_nuit.yml"]
EN_DUR = re.compile(r"(?<!\d)0?6[:h](30|20|28)\b|(?<!\w)06(30|20|28)\b|\(\s*6\s*,\s*(30|20|28)\s*\)"
                    r"|hour\s*[=<>]=?\s*6\b|>=\s*6(20|28)\b")


def test_aucune_heure_du_plancher_en_dur():
    """Hors commentaires et hors du motif historique BEFORE_0630 (règle PG1),
    aucun consommateur n'écrit 06h30, 06h20 ou 06h28 en dur."""
    fautes = []
    for rel in CONSOMMATEURS:
        for n, ligne in enumerate(open(os.path.join(RACINE, rel), encoding="utf-8"), 1):
            code = ligne.strip()
            if not code or code.startswith("#") or "BEFORE_0630" in code:
                continue
            if EN_DUR.search(code):
                fautes.append(f"{rel}:{n}: {code[:100]}")
    assert fautes == []


def test_motif_en_dur_detecte():
    for ligne in ("if (( 10#$(date -u +%H%M) >= 620 )); then", "HEURE = (6, 20)", "posée dès 06h30 GMT",
                  "now_utc.hour == 6 and now_utc.minute >= 30", "avant 06:28"):
        assert EN_DUR.search(ligne), ligne
    for ligne in ("R1C6_30", "timeout-minutes: 25", "06h05", "plancher_matin_utc(d)", 'content="630"', "2630"):
        assert not EN_DUR.search(ligne), ligne


# ── Workflow de nuit : refus par défaut ──────────────────────────────────

def _etape(nom):
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(open(os.path.join(RACINE, ".github", "workflows", "fondamental_nuit.yml"), encoding="utf-8"))
    etapes = [e for e in doc["jobs"]["nuit"]["steps"] if str(e.get("name", "")).startswith(nom)]
    assert len(etapes) == 1
    return etapes[0]["run"]


FAUX_PYTHON = {
    "absent": None,
    "erreur": "#!/bin/sh\necho 'Traceback: ModuleNotFoundError' >&2\nexit 1\n",
    "illisible": "#!/bin/sh\necho '???'\nexit 0\n",
    "plante_apres": "#!/bin/sh\necho \"${OUI} maintenant=05:10 UTC\"\nexit 3\n",
    "refus": "#!/bin/sh\necho \"${NON} maintenant=07:40 UTC\"\nexit 1\n",
    "oui": "#!/bin/sh\nif [ \"$2\" = turf_lab.r2_store ]; then echo ENVOI_FAIT; exit 0; fi\n"
           "echo \"${OUI} maintenant=05:10 UTC\"\nexit 0\n",
}


def _jouer(script, cas, oui, non):
    """Joue le script de l'étape comme GitHub (bash -e) avec un faux python."""
    if shutil.which("bash") is None:
        pytest.skip("bash indisponible")
    with tempfile.TemporaryDirectory() as d:
        bindir = os.path.join(d, "bin")
        os.mkdir(bindir)
        date_bin = shutil.which("date")
        os.symlink(date_bin, os.path.join(bindir, "date"))
        if FAUX_PYTHON[cas] is not None:
            chemin = os.path.join(bindir, "python")
            with open(chemin, "w") as f:
                f.write(FAUX_PYTHON[cas])
            os.chmod(chemin, 0o755)
        env_file = os.path.join(d, "github_env")
        open(env_file, "w").close()
        sortie = subprocess.run([shutil.which("bash"), "-e", "-c", script], capture_output=True, text=True,
                                env={"PATH": bindir, "GITHUB_ENV": env_file, "OUI": oui, "NON": non})
        return sortie.returncode, sortie.stdout, open(env_file).read()


@pytest.mark.parametrize("cas", ["absent", "erreur", "illisible", "plante_apres", "refus"])
def test_envoi_refuse_par_defaut(cas):
    code, sortie, _ = _jouer(_etape("Push the bench to R2"), cas, "ENVOI_AUTORISE", "ENVOI_REFUSE")
    assert code != 0 and "PUSH_REFUSE_HEURE" in sortie and "ENVOI_FAIT" not in sortie


def test_envoi_autorise_seulement_sur_reponse_explicite():
    code, sortie, _ = _jouer(_etape("Push the bench to R2"), "oui", "ENVOI_AUTORISE", "ENVOI_REFUSE")
    assert code == 0 and "ENVOI_FAIT" in sortie and "PUSH_REFUSE_HEURE" not in sortie


@pytest.mark.parametrize("cas", ["absent", "erreur", "illisible", "plante_apres", "refus"])
def test_filet_github_refuse_par_defaut(cas):
    code, sortie, env = _jouer(_etape("Late GitHub safety net"), cas, "ECRITURE_AUTORISEE", "ECRITURE_REFUSEE")
    assert code == 0 and "FILET_HORS_DELAI :" in sortie and "FILET_HORS_DELAI=1" in env


def test_filet_github_calcule_sur_reponse_explicite():
    code, sortie, env = _jouer(_etape("Late GitHub safety net"), "oui", "ECRITURE_AUTORISEE", "ECRITURE_REFUSEE")
    assert code == 0 and "FILET_HORS_DELAI" not in sortie and env == ""


# ── Libellés du site (remarque du dev NVE du 13/11) ──────────────────────

@pytest.mark.parametrize("maintenant,entete,par_jour", [
    # 22/11 au soir : l'en-tête annonce déjà l'édition du 23/11 ; chaque course garde le plancher de sa date
    (datetime(2026, 11, 22, 20, 0), "verrouillée dès 08h30 heure de Paris (07h30 GMT)",
     {"2026-11-22": "06h30 GMT", "2026-11-23": "08h30 heure de Paris (07h30 GMT)"}),
    # 22/11 à 05h00 UTC : l'édition du jour n'est pas encore posée, ancienne règle
    (datetime(2026, 11, 22, 5, 0), "verrouillée dès 06h30 GMT",
     {"2026-11-22": "06h30 GMT", "2026-11-23": "08h30 heure de Paris (07h30 GMT)"}),
    # 27/03/2027 au soir : le lendemain passe à l'heure d'été
    (datetime(2027, 3, 27, 20, 0), "verrouillée dès 08h30 heure de Paris (06h30 GMT)",
     {"2027-03-27": "08h30 heure de Paris (07h30 GMT)", "2027-03-28": "08h30 heure de Paris (06h30 GMT)"}),
])
def test_libelles_du_site(monkeypatch, tmp_path, maintenant, entete, par_jour):
    import json as _json
    from turf_lab import html_report
    monkeypatch.setattr(html_report, "maintenant_utc", lambda: maintenant)
    out = tmp_path / "index.html"
    with redirect_stdout(io.StringIO()):
        html_report.generate_html_dashboard({"evaluations": {}, "total_finished_races": 0, "historical_logs": []},
                                            output_path=str(out))
    page = out.read_text(encoding="utf-8")
    assert entete in page
    m = re.search(r"const PLANCHER_PAR_JOUR = (\{.*?\});", page)
    assert m, "table des planchers par date absente du site"
    table = _json.loads(m.group(1))
    for jour, libelle in par_jour.items():
        assert table[jour] == libelle
    assert 'PLANCHER_PAR_JOUR[item.date]' in page
